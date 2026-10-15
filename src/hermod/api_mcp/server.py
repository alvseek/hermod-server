"""FastMCP adapter — the agent-facing MCP face (streamable-HTTP transport).

Hermod serves the coding overlay's procedures as MCP **Prompts** and its templates as
**Resources** — and both again as tools (``read_procedure`` / ``read_resource``), because a
Prompt is user-invoked and a Resource client-attached, while a tool is the one primitive the
protocol lets the agent itself call. A served procedure that says "execute ``/map-orientation``"
is addressed to an agent with no picker to run it from, and ``read_procedure("map-orientation")``
is what that instruction resolves to here.

**The data tools are the store's ops.** The project store arrived in Phase 4, so this face also
registers one tool per ``db.md`` op — the context entries and the orientation map a served
procedure reads and writes. Those tools write, which is why an issuer-configured server carries a
token in front of this face: the guard is applied by the transport (``auth`` reaches ``FastMCP``),
so it covers every tool and every prompt at once rather than each one opting in.

A stranger who connects meets this surface cold, so the server says what it is: ``INSTRUCTIONS``
goes out in the ``initialize`` result (clients inject it into the system prompt), ``help`` returns
the same text for clients that show no instructions, and every tool carries a title and the
read-only hint a client shows before it lets a tool run.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider

from hermod.business_services.awakening_service import AwakeningService
from hermod.business_services.context_service import ContextService
from hermod.content.loader import ContentLoader
from hermod.data_entities.project_context import ContextEntry

#: What a client is told at ``initialize``. claude.ai and Claude Code inject it into the system
#: prompt, so it is re-sent on every call of every session — which is why it is four sentences and
#: not a manual: enough to act on with no other reading, and nothing the tool list already says.
#: ``help`` returns the same text for clients that ignore it.
INSTRUCTIONS = (
    "Hermod serves the coding overlay: the procedures for working in a repository — the "
    "coding awakening, project context, the orientation map, the session wrap-up, and "
    "push/pull.\n"
    'Start with read_procedure("awaken-coder") and follow it; help() lists everything served.\n'
    "Project context and the orientation map are the memory store's records, scoped by project; "
    "the store tools create them and list them.\n"
    "A context entry is one of Munnin's records, so reading or editing one goes through Munnin's "
    "own ops, by the uuid a listing returned; agent memory is Munnin's too: a coding client "
    "connects both.\n"
    "Procedures come from read_procedure(name), never from slash commands."
)

# Clients read these before letting a tool run. Every tool on this face is read-only, so none of
# them needs a confirmation prompt, and ``openWorldHint`` is false throughout: nothing here
# reaches past the overlay checkout.
_READ = {"readOnlyHint": True, "openWorldHint": False}
# The store tools write project memory, so they say so. Still no open world: the store is one local
# file and nothing here reaches past it.
_WRITE = {"readOnlyHint": False, "openWorldHint": False}


def _procedure_rows(content: ContentLoader) -> list[dict[str, str]]:
    """Name, title and one-line purpose of every served procedure — the menu ``help`` and
    ``list_procedures`` both show, empty when no overlay content is available."""
    if not content.available():
        return []
    return [
        {
            "name": name,
            "title": content.title_prompt(name),
            "description": content.describe_prompt(name),
        }
        for name in content.list_prompts()
    ]


def build_mcp(
    content: ContentLoader,
    service: ContextService,
    awakening: AwakeningService,
    auth: AuthProvider | None = None,
) -> FastMCP:
    """The MCP face: the overlay's content, and the project store behind it.

    ``auth=None`` leaves the face open, which is the laptop shape: it is reached exactly when no
    issuer is configured, and then only under a loopback bind. Passing a provider hands it to the
    transport, so the guard covers the whole face — prompts, resources and tools — with no
    per-handler wiring.
    """
    mcp: FastMCP = FastMCP("hermod", instructions=INSTRUCTIONS, auth=auth)

    @mcp.tool(title="Liveness check", annotations=_READ)
    def ping() -> str:
        """Liveness check — returns 'pong'."""
        return "pong"

    # Registered whether or not content is available: it is the fallback for a client that never
    # shows ``instructions``, so it has to exist in every configuration.
    @mcp.tool(name="help", title="What Hermod is and where to start", annotations=_READ)
    def help_() -> dict[str, Any]:
        """What this server is and what to call first — the same text a client receives at
        initialize as ``instructions`` — plus the served procedures. Read-only; call it when you
        are unsure what Hermod serves or which procedure to read next."""
        return {"instructions": INSTRUCTIONS, "procedures": _procedure_rows(content)}

    _register_content(mcp, content)
    _register_store(mcp, service)
    _register_awakening(mcp, awakening)
    return mcp


def _register_awakening(mcp: FastMCP, awakening: AwakeningService) -> None:
    """The coding awakening's one call (ADR-029).

    Registered beside the store tools rather than inside ``_register_store``, because it is not
    one: every tool there reads or writes a single record, and this one assembles a payload for
    one procedure. The docstring says so where a reader meets the exception.
    """

    @mcp.tool(
        name="awaken_coder",
        title="Assemble the coding half of an awakening",
        annotations=_READ,
    )
    def awaken_coder(domain: str, projects: list[str]) -> dict[str, Any]:
        """One call for a coding awakening: the coding lane, the project set's episode index and
        its newest episode per project, that set's project context index, and its orientation maps.

        The only tool on this face that assembles a payload rather than reading one record. The
        overlay's ``/awaken-coder`` procedure is its one caller, and it issues this call together
        with Munnin's ``awaken``; together the two are the whole awakening.

        ``projects`` is the session's own repository **first**, then the repositories related to
        it. That first element is the session's home, not a filter: every part of the payload spans
        the whole set, because the related set widens the recall rather than moving the session. A
        reordered list makes a different repository the session's home.
        """
        return awakening.awaken(domain=domain, projects=projects)


def _register_content(mcp: FastMCP, content: ContentLoader) -> None:
    """Register the served procedures as Prompts, the templates as Resources, and both as tools.

    All read live from the overlay checkout, so editing a procedure changes what is served with
    no re-import. The tools are the same content through a door the agent can open itself.
    """
    if not content.available():
        return

    def _make_prompt(procedure: str, argument: tuple[str, str] | None) -> Callable[..., str]:
        # FastMCP derives a prompt's arguments from the function signature, so a procedure that
        # takes one needs a real parameter carrying its name. The parameter is built dynamically
        # because each procedure names its argument differently, and **both** ``__signature__``
        # and ``__annotations__`` must be set: pydantic reads the second and raises on the first
        # alone. A plain ``str`` annotation is deliberate — an ``Annotated[str, Field(...)]``
        # makes FastMCP append a JSON-schema instruction to the description, which reads as noise
        # in a command menu.
        def fn(**kwargs: str) -> str:
            return content.get_prompt(procedure, kwargs.get(argument[0]) if argument else None)

        fn.__name__ = procedure.replace("-", "_")
        if argument is None:
            # A bare signature, or FastMCP sees the raw ``**kwargs`` and refuses the prompt.
            fn.__annotations__ = {"return": str}
            fn.__signature__ = inspect.Signature([], return_annotation=str)  # type: ignore[attr-defined]
            return fn
        arg_name, arg_help = argument
        fn.__doc__ = f"{procedure}\n\nArgs:\n    {arg_name}: {arg_help}\n"
        fn.__annotations__ = {arg_name: str, "return": str}
        parameter = inspect.Parameter(
            arg_name, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str
        )
        fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [parameter], return_annotation=str
        )
        return fn

    for name in content.list_prompts():
        mcp.prompt(
            name=name,
            title=content.title_prompt(name),
            description=content.describe_prompt(name),
        )(_make_prompt(name, content.argument_prompt(name)))

    def _make_resource(resource: str) -> Callable[[], str]:
        # Zero-arg, so FastMCP registers a static resource rather than a URI template.
        def fn() -> str:
            return content.get_resource(resource)

        fn.__name__ = f"resource_{resource.replace('-', '_')}"
        return fn

    for name in content.list_resources():
        mcp.resource(
            f"resource://templates/{name}",
            name=name,
            title=content.title_resource(name),
            description=content.describe_resource(name),
            mime_type="text/markdown",
            # A fill-in template is written by an agent and read by nobody else, so the audience
            # is the assistant. The priority sits near the floor because `priority` scores whether
            # a client should pull something into context unasked, and a template earns its place
            # only when deliberately fetched. Not 0.0: that is the spec's "entirely optional",
            # which a client could read as "hide" — and these belong in a picker.
            annotations={"audience": ["assistant"], "priority": 0.1},
        )(_make_resource(name))

    # --- the same content as tools: the one primitive the agent may invoke itself ---

    def _not_served(kind: str, name: str, listing: str) -> dict[str, Any]:
        # A tool's caller is an agent typing whatever a procedure told it to type, so an unknown
        # name is an answer to give rather than an error to raise. The raise is right for the
        # Prompt surface, where a client can only ask for a registered name.
        return {
            "served": False,
            "name": name,
            "note": f"no served {kind} named {name!r}; call {listing} to see what is",
        }

    @mcp.tool(title="List served procedures", annotations=_READ)
    def list_procedures() -> list[dict[str, str]]:
        """List the overlay procedures this server serves — name, title, one-line purpose.

        A procedure is the how-to an agent follows before acting. Read one with
        read_procedure(name).
        """
        return _procedure_rows(content)

    @mcp.tool(title="Read a served procedure", annotations=_READ)
    def read_procedure(name: str, argument: str | None = None) -> dict[str, Any]:
        """Read a served overlay procedure, composed for this server's storage backend.

        Use this whenever a procedure or instruction tells you to execute, invoke or run a slash
        command such as `/map-orientation`: call read_procedure("map-orientation") instead of the
        slash command and follow what it returns. `argument` fills the procedure's `$ARGUMENTS`
        slot (a domain for awaken-coder, a mode for map-orientation). A name that is not served
        returns served=false rather than an error.
        """
        try:
            text = content.get_prompt(name, argument)
        except KeyError:
            return _not_served("procedure", name, "list_procedures()")
        return {"served": True, "name": name, "content": text}

    @mcp.tool(title="List served resources", annotations=_READ)
    def list_resources() -> list[dict[str, str]]:
        """List the templates this server serves — name, title, one-line purpose.

        A resource is a template a procedure copies into the repository by path. Read one with
        read_resource(name).
        """
        return [
            {
                "name": name,
                "title": content.title_resource(name),
                "description": content.describe_resource(name),
            }
            for name in content.list_resources()
        ]

    @mcp.tool(title="Read a served resource", annotations=_READ)
    def read_resource(name: str) -> dict[str, Any]:
        """Read a served template verbatim.

        Use this when a served instruction points at a template by path: a reference to
        `templates/project-context-template.md` is read_resource("project-context-template"). A
        name that is not served returns served=false rather than an error.
        """
        try:
            text = content.get_resource(name)
        except KeyError:
            return _not_served("resource", name, "list_resources()")
        return {"served": True, "name": name, "content": text}


def _register_store(mcp: FastMCP, service: ContextService) -> None:
    """Register the project store's ops as tools.

    Discovery and creation only: a listing per layer, a create per layer, the two direction ops,
    and the map's three. Everything addressed by a uuid that already exists is a tool on the
    memory layer (``get``, the ``edit`` family, ``edit_metadata``, ``soft_delete``), because those
    already act on these rows, so nothing here duplicates one. The three ops with no code behind
    them (``§ ensure-context-dir``, ``§ update-context-index``, ``§ check-entry-size``) get no
    tool: the backend document declares them no-ops, so there is nothing to call.

    Registered independently of ``content``: the store reaches the memory database by DSN, so its
    tools are served whether or not an overlay checkout is present.
    """

    def _entry(entry: ContextEntry) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "uuid": entry.uuid,
            "project": entry.project,
            "scope": entry.scope.value,
            "theme": entry.theme,
            "content_summary": entry.content_summary,
            "tags": entry.tags,
            "created_date": entry.created_date,
            "modified_date": entry.modified_date,
        }
        return payload

    @mcp.tool(title="List the shared project context", annotations=_READ)
    def list_shared_context(project: str) -> list[dict[str, Any]]:
        """List a project's shared context entries, the cross-agent half of its context index.

        Each row carries its `uuid`, and that uuid is how the entry is addressed from then on: a
        body comes from Munnin's `get(uuid)`, is changed with its `edit` family, has its summary
        and tags set with its `edit_metadata`, and is retired with its `soft_delete`. A row with
        either lifecycle flag set is retired, so it is absent here.
        """
        return [_entry(entry) for entry in service.list_shared_context(project=project)]

    @mcp.tool(title="List one agent's project context", annotations=_READ)
    def list_agent_context(project: str, agent: str) -> list[dict[str, Any]]:
        """List one agent's own context entries for a project.

        `agent` is required rather than optional, because those rows are owned: answering without
        it could only be every agent's rows at once. Each row carries its `uuid`, which is what
        Munnin's own ops address it by.
        """
        return [
            _entry(entry)
            for entry in service.list_agent_context(project=project, agent=agent)
        ]

    @mcp.tool(title="Create a shared context entry", annotations=_WRITE)
    def create_shared_context_entry(
        project: str,
        theme: str,
        content: str,
        template_version: str,
        content_summary: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one shared entry from a filled-in project-context template.

        Read `project-context-template` first and pass the `template_version` stamp it carries: it
        is a gate, not content, and no stored entry carries it. `theme` names the entry for a
        reader, while reading and writing are by `uuid` and never by theme. `content` is the whole
        document, frontmatter included, minus that stamp. `content_summary` is required, since it
        is the line a listing shows beside the theme. No theme is checked here, so list first and
        create only when nothing already covers it. Every later read or change of the entry goes
        through Munnin, by the uuid this returns.
        """
        entry = service.create_shared_context_entry(
            project=project,
            theme=theme,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
            tags=tags,
        )
        return _entry(entry)

    @mcp.tool(title="Create an agent's context entry", annotations=_WRITE)
    def create_agent_context_entry(
        project: str,
        agent: str,
        theme: str,
        content: str,
        template_version: str,
        content_summary: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one entry owned by an agent, from the same template and behind the same gates.

        `agent` must be a kebab domain the memory store already holds, because the row carries its
        owner. Everything else matches the shared create, including that later reads and changes go
        through Munnin by the uuid it returns.
        """
        entry = service.create_agent_context_entry(
            project=project,
            agent=agent,
            theme=theme,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
            tags=tags,
        )
        return _entry(entry)

    @mcp.tool(title="Promote an entry into the shared layer", annotations=_WRITE)
    def promote_context_entry(uuid: str) -> dict[str, Any]:
        """Move one entry from an agent's layer into the shared one, body untouched.

        The uuid is kept, so a handle a caller already holds still resolves afterwards, and the
        source row is tombstoned rather than deleted, the same copy-then-delete shape the markdown
        backend has. Refused when the entry already lives in the shared layer. No theme is checked,
        so a move onto a theme the shared layer already holds leaves two rows on it, exactly as a
        create would; list the target layer first when that matters. Reading or editing its body
        afterwards goes through Munnin, by the uuid it keeps.
        """
        return _entry(service.promote_context_entry(uuid=uuid))

    @mcp.tool(title="Demote an entry into an agent's layer", annotations=_WRITE)
    def demote_context_entry(uuid: str, agent: str) -> dict[str, Any]:
        """Move one shared entry into one agent's layer, body untouched and uuid kept.

        The owner is required, because the target row carries `agent_id` and a shared row has none
        to inherit. Reading or editing its body afterwards goes through Munnin, by the uuid it
        keeps.
        """
        return _entry(service.demote_context_entry(uuid=uuid, agent=agent))

    @mcp.tool(title="Read the orientation map", annotations=_READ)
    def read_map(project: str) -> dict[str, Any]:
        """Read the project's orientation map whole, or `found: False`.

        One call answers existence as well, since an absent map comes back as `found: False`, so
        nothing checks for one first.
        """
        orientation_map = service.read_map(project=project)
        if orientation_map is None:
            return {"found": False}
        return {
            "found": True,
            "project": orientation_map.project,
            "content_summary": orientation_map.content_summary,
            "content": orientation_map.full_content,
            "created_date": orientation_map.created_date,
            "modified_date": orientation_map.modified_date,
        }

    @mcp.tool(title="Create the orientation map", annotations=_WRITE)
    def create_map(
        project: str, content: str, template_version: str, content_summary: str | None = None
    ) -> dict[str, Any]:
        """Create the project's orientation map, from the orientation-map template.

        Read `orientation-map-template` first and pass the `template_version` stamp it carries; the
        map never carries it. Refuses a second map, since a project has one and it is updated
        thereafter, and a missing or stale stamp.
        """
        orientation_map = service.create_map(
            project=project,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
        )
        return {"project": orientation_map.project, "created_date": orientation_map.created_date}

    @mcp.tool(title="Update the orientation map", annotations=_WRITE)
    def update_map(
        project: str, content: str, template_version: str, content_summary: str | None = None
    ) -> dict[str, Any]:
        """Rewrite the project's map wholesale.

        It must exist first, which is what keeps an update from quietly becoming a create, and it
        carries the same template stamp the create declares.
        """
        orientation_map = service.update_map(
            project=project,
            content=content,
            template_version=template_version,
            content_summary=content_summary,
        )
        return {"project": orientation_map.project, "modified_date": orientation_map.modified_date}
