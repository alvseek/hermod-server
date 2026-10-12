"""FastMCP adapter — the agent-facing MCP face (streamable-HTTP transport).

Hermod serves the coding overlay's procedures as MCP **Prompts** and its templates as
**Resources** — and both again as tools (``read_procedure`` / ``read_resource``), because a
Prompt is user-invoked and a Resource client-attached, while a tool is the one primitive the
protocol lets the agent itself call. A served procedure that says "execute ``/map-orientation``"
is addressed to an agent with no picker to run it from, and ``read_procedure("map-orientation")``
is what that instruction resolves to here.

**The data tools are the store's ops.** The project store arrived in Phase 4, so this face also
registers one tool per ``db.md`` op — the context entries and the orientation map a served
procedure reads and writes. There is still no auth, and it is still structural: the server binds
loopback and owns only the project memory the overlay's ``db`` backend has been declaring since
ADR-023.

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

from hermod.business_services.context_service import ContextService
from hermod.content.loader import ContentLoader
from hermod.data_entities.project_context import ContextEntry, ContextScope

#: What a client is told at ``initialize``. claude.ai and Claude Code inject it into the system
#: prompt, so it is re-sent on every call of every session — which is why it is four sentences and
#: not a manual: enough to act on with no other reading, and nothing the tool list already says.
#: ``help`` returns the same text for clients that ignore it.
INSTRUCTIONS = (
    "Hermod serves the coding overlay: the procedures for working in a repository — the "
    "coding awakening, project context, the orientation map, the session wrap-up, and "
    "push/pull.\n"
    'Start with read_procedure("awaken-coder") and follow it; help() lists everything served.\n'
    "Project context and the orientation map live in this server's own store; the store tools "
    "read and write them.\n"
    "Agent memory is Munnin's, not this server's: a coding client connects both.\n"
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


def build_mcp(content: ContentLoader, service: ContextService) -> FastMCP:
    """The MCP face: the overlay's content, and the project store behind it."""
    mcp: FastMCP = FastMCP("hermod", instructions=INSTRUCTIONS)

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
    return mcp


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


def _scope_arg(value: str | None) -> ContextScope | None:
    """The scope argument as an enum, or ``None`` for both scopes. A bad value is named rather
    than swallowed, because a caller that typed a scope meant a specific one."""
    if value is None:
        return None
    try:
        return ContextScope(value)
    except ValueError:
        raise ValueError(f"unknown scope {value!r}; expected 'shared' or 'private'") from None


def _register_store(mcp: FastMCP, service: ContextService) -> None:
    """Register the project store's ops as tools.

    One tool per op the served ``db`` mechanics name, so an instruction like "call
    § create-context-entry" resolves to something the agent can actually call. The two ops with no
    code behind them (``§ ensure-context-dir``, ``§ update-context-index``) get no tool: the backend
    document declares them no-ops, so there is nothing to call.

    Registered independently of ``content``: the store is a local file, so it is served whether or
    not an overlay checkout is present.
    """

    def _entry(entry: ContextEntry, *, with_content: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project": entry.project,
            "scope": entry.scope.value,
            "theme": entry.theme,
            "description": entry.description,
            "tags": entry.tags,
            "created_date": entry.created_date,
            "modified_date": entry.modified_date,
        }
        if with_content:
            payload["content"] = entry.full_content
        return payload

    @mcp.tool(title="List project context entries", annotations=_READ)
    def list_context_entries(project: str, scope: str | None = None) -> list[dict[str, Any]]:
        """List a project's context entries — the project's context index.

        `scope` narrows to 'shared' or 'private'; omit it for both. An entry is addressed by
        (project, scope, theme): read one with read_context_entry.
        """
        return [
            _entry(entry, with_content=False)
            for entry in service.list_context_entries(project=project, scope=_scope_arg(scope))
        ]

    @mcp.tool(title="Read a project context entry", annotations=_READ)
    def read_context_entry(project: str, scope: str, theme: str) -> dict[str, Any]:
        """Read one context entry whole, by (project, scope, theme).

        Returns `found: False` when no live entry holds that identity — an answer to give, not an
        error to raise.
        """
        entry = service.read_context_entry(project=project, scope=_scope_arg(scope), theme=theme)
        if entry is None:
            return {"found": False}
        return {"found": True, **_entry(entry, with_content=True)}

    @mcp.tool(title="Create a project context entry", annotations=_WRITE)
    def create_context_entry(
        project: str,
        scope: str,
        theme: str,
        content: str,
        template_version: str,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one context entry from a filled-in project-context template.

        Read `project-context-template` first and pass the `template_version` stamp it carries: it
        is a gate, not content, and no stored entry carries it. `content` is the whole document,
        frontmatter included, minus that stamp. `description` and `tags` are the frontmatter the
        context index shows, so pass them and the listing can tell entries apart. Refuses an entry
        over the line cap, a missing or stale stamp, and an identity that is already taken.
        """
        entry = service.create_context_entry(
            project=project,
            scope=_scope_arg(scope),
            theme=theme,
            content=content,
            template_version=template_version,
            description=description,
            tags=tags,
        )
        return _entry(entry, with_content=False)

    @mcp.tool(title="Update a project context entry", annotations=_WRITE)
    def update_context_entry(
        project: str,
        scope: str,
        theme: str,
        content: str,
        template_version: str,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Replace a live context entry: its content, description and tags.

        Read `project-context-template` first and pass its `template_version` stamp, exactly as the
        create does: an update restates the whole document. A **wholesale replace**, exactly as the
        markdown backend's write-back is: `description` and `tags` default to empty, so a caller
        that passes only `content` clears them. The entry must already exist, which is what keeps
        an update from quietly becoming a create.
        """
        entry = service.update_context_entry(
            project=project,
            scope=_scope_arg(scope),
            theme=theme,
            content=content,
            template_version=template_version,
            description=description,
            tags=tags,
        )
        return _entry(entry, with_content=False)

    @mcp.tool(title="Move a context entry between scopes", annotations=_WRITE)
    def move_context_entry(
        project: str, theme: str, from_scope: str, to_scope: str
    ) -> dict[str, Any]:
        """Promote a private context entry to shared (or the reverse). The body is untouched.

        Refuses when the target scope already holds that theme, which is the collision the move
        sub-flow stops on.
        """
        entry = service.move_context_entry(
            project=project,
            theme=theme,
            from_scope=_scope_arg(from_scope),
            to_scope=_scope_arg(to_scope),
        )
        return _entry(entry, with_content=False)

    @mcp.tool(title="Delete a context entry", annotations=_WRITE)
    def delete_context_entry(project: str, scope: str, theme: str) -> dict[str, Any]:
        """Tombstone one context entry. It disappears from every read, and the same theme can be
        created again afterwards."""
        service.delete_context_entry(project=project, scope=_scope_arg(scope), theme=theme)
        return {"deleted": True, "project": project, "scope": scope, "theme": theme}

    @mcp.tool(title="Check a context entry's size", annotations=_READ)
    def check_entry_size(content: str) -> dict[str, Any]:
        """Measure an entry against the line cap.

        Create and update refuse an oversized entry; this is the measurement they refuse on, for a
        caller that wants to see it before writing.
        """
        verdict = service.check_entry_size(content=content)
        return {
            "lines": verdict.lines,
            "max_lines": verdict.max_lines,
            "oversized": verdict.oversized,
        }

    @mcp.tool(title="Does a project have an orientation map?", annotations=_READ)
    def map_exists(project: str) -> dict[str, Any]:
        """Whether the project has an orientation map. The served § map-exists."""
        return {"project": project, "exists": service.map_exists(project=project)}

    @mcp.tool(title="Read the orientation map", annotations=_READ)
    def read_map(project: str) -> dict[str, Any]:
        """Read the project's orientation map whole, or `found: False`."""
        orientation_map = service.read_map(project=project)
        if orientation_map is None:
            return {"found": False}
        return {
            "found": True,
            "project": orientation_map.project,
            "description": orientation_map.description,
            "content": orientation_map.full_content,
            "created_date": orientation_map.created_date,
            "modified_date": orientation_map.modified_date,
        }

    @mcp.tool(title="Create the orientation map", annotations=_WRITE)
    def create_map(
        project: str, content: str, template_version: str, description: str | None = None
    ) -> dict[str, Any]:
        """Create the project's orientation map, from the orientation-map template.

        Read `orientation-map-template` first and pass the `template_version` stamp it carries; the
        map never carries it. Refuses a second map (a project has one, and it is edited
        thereafter), and a missing or stale stamp.
        """
        orientation_map = service.create_map(
            project=project,
            content=content,
            template_version=template_version,
            description=description,
        )
        return {"project": orientation_map.project, "created_date": orientation_map.created_date}

    @mcp.tool(title="Write the orientation map", annotations=_WRITE)
    def write_map(
        project: str, content: str, template_version: str, description: str | None = None
    ) -> dict[str, Any]:
        """Rewrite the project's orientation map wholesale.

        It must exist first, and it carries the same template stamp the create declares.
        """
        orientation_map = service.write_map(
            project=project,
            content=content,
            template_version=template_version,
            description=description,
        )
        return {"project": orientation_map.project, "modified_date": orientation_map.modified_date}
