"""FastMCP adapter — the agent-facing MCP face (streamable-HTTP transport).

Hermod serves the coding overlay's procedures as MCP **Prompts** and its templates as
**Resources** — and both again as tools (``read_procedure`` / ``read_resource``), because a
Prompt is user-invoked and a Resource client-attached, while a tool is the one primitive the
protocol lets the agent itself call. A served procedure that says "execute ``/map-orientation``"
is addressed to an agent with no picker to run it from, and ``read_procedure("map-orientation")``
is what that instruction resolves to here.

**There are no data tools yet.** The project store arrives in Phase 4, and until it does this
face serves content and liveness only — which is also why there is no auth: nothing here reads
or writes anything a caller should not reach.

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

from hermod.content.loader import ContentLoader

#: What a client is told at ``initialize``. claude.ai and Claude Code inject it into the system
#: prompt, so it is re-sent on every call of every session — which is why it is four sentences and
#: not a manual: enough to act on with no other reading, and nothing the tool list already says.
#: ``help`` returns the same text for clients that ignore it.
INSTRUCTIONS = (
    "Hermod serves the coding overlay: the procedures for working in a repository — the "
    "coding awakening, project context, the orientation map, the session wrap-up, and "
    "push/pull.\n"
    'Start with read_procedure("awaken-coder") and follow it; help() lists everything served.\n'
    "Agent memory is Munnin's, not this server's: a coding client connects both.\n"
    "Procedures come from read_procedure(name), never from slash commands."
)

# Clients read these before letting a tool run. Every tool on this face is read-only, so none of
# them needs a confirmation prompt, and ``openWorldHint`` is false throughout: nothing here
# reaches past the overlay checkout.
_READ = {"readOnlyHint": True, "openWorldHint": False}


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


def build_mcp(content: ContentLoader) -> FastMCP:
    """The MCP face, serving whatever the loader can read from the overlay checkout."""
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
