"""Composition root — build the ASGI app and wire the graph.

One process serves both surfaces:

- FastMCP's streamable-HTTP face, mounted at ``/mcp``
- ``/health``, which a deploy gate or a human reads

The mounted MCP app carries a lifespan — its session manager — that **must** be handed to the
parent app, or the MCP session manager never starts and the face answers nothing. That wiring is
the reason this file exists at all.

There is no HTTP API face. What there is, once an issuer is configured, is token verification in
front of the MCP face: the project store is readable *and* writable, so a server anyone can reach
must not be one anyone can write. ``/health`` stays open on purpose — the deploy's own health gate
calls it without a credential, so guarding it would fail the cutover. The verification is Munnin's,
ported, minus its tenancy: a Logto JWKS verifier bound to the resource identifier this server
advertises, plus the RFC 9728 document a client reads to discover who mints its tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from fastmcp.server.auth import AuthProvider, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from hermod import __version__
from hermod.api_mcp.server import build_mcp
from hermod.business_services.context_service import ContextService
from hermod.configuration.config import Config, load_config
from hermod.content.loader import ContentLoader, ContentSource
from hermod.data_repositories.postgres_project_repository import PostgresProjectRepository
from hermod.data_repositories.project_repository import ProjectRepository
from hermod.data_repositories.sqlite_project_repository import SqliteProjectRepository

#: Where the MCP face is mounted, and therefore the address a client connects to.
MCP_MOUNT_PATH = "/mcp"

#: The document a client fetches to learn who issues tokens for this server, at the path
#: RFC 9728 derives from the resource identifier: the well-known prefix followed by the
#: identifier's own path. Spelled out here only so the normaliser below can name it without
#: reconstructing the derivation.
PROTECTED_RESOURCE_METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_MOUNT_PATH}"


def mcp_resource_url(public_base_url: str) -> str:
    """The one identifier that names the MCP face — deliberately without a trailing slash.

    This string is what a client sends as ``resource`` on both the authorization and the refresh
    request, and the issuer matches it character for character, so the slash question is not
    cosmetic. The MCP specification settles it: implementations *should consistently use the form
    without the trailing slash*, and clients act on that by normalising away any slash they are
    configured with. The identifier therefore has to be the no-slash form, because a client can be
    made to send nothing else.
    """
    return f"{public_base_url.rstrip('/')}{MCP_MOUNT_PATH}"


class _NormalisePath:
    """Serve the MCP face and its metadata document under both slash forms, no redirect.

    Starlette's ``Mount`` only matches paths *below* the mount, so a request to the bare ``/mcp``
    can be answered with a slash-appending 307 instead of reaching the sub-app. Behind a
    TLS-terminating proxy that redirect is worse than a detour: it is built from the scheme uvicorn
    saw, so it points at ``http://``, and a client that dropped the slash (which the specification
    tells it to do) is bounced to plaintext before it ever sees a 401. The same happens in the
    other direction for the metadata document, which the SDK registers at the no-slash path.

    Rewriting the path before routing is the smallest change that removes the redirect entirely:
    both forms land on the one handler, and nothing on ``/mcp*`` can answer 3xx. It rewrites only
    the two exact paths it names, so every other route keeps Starlette's default behaviour.
    ``raw_path`` is kept in step because middleware further down may read either.
    """

    _REWRITES = {
        MCP_MOUNT_PATH: f"{MCP_MOUNT_PATH}/",
        f"{PROTECTED_RESOURCE_METADATA_PATH}/": PROTECTED_RESOURCE_METADATA_PATH,
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            target = self._REWRITES.get(scope["path"])
            if target is not None:
                scope = dict(scope)
                scope["path"] = target
                scope["raw_path"] = target.encode("ascii")
        await self.app(scope, receive, send)


class LocalModeNotLoopbackError(RuntimeError):
    """Raised when no issuer is configured and the server would still be reachable from elsewhere.

    Local mode serves the project store to whoever can reach the port, which is safe on a laptop
    and a disclosure anywhere else. Two settings say where the server is reachable from, and both
    are checked: the public base URL, which states where it believes it is addressed, and the bind
    address, which decides which interfaces actually answer. A container has to bind ``0.0.0.0`` to
    be reachable even from its own host, so the bind check can be waived — but only by
    ``HERMOD_LOCAL_BIND_ALL=1``, an explicit statement that something in front of the process (a
    port published on the host's loopback) keeps it local. The server cannot detect that; it asks
    to be told.
    """


#: Hosts a URL may name and still be "this machine only". ``urlsplit`` strips the brackets from an
#: IPv6 literal, so ``::1`` is compared bare.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str | None) -> bool:
    """Whether a bare host — a bind address, or the host part of a URL — is this machine."""
    return host is not None and host.strip("[]").lower() in _LOOPBACK_HOSTS


def is_loopback_url(url: str) -> bool:
    """Whether ``url`` names this machine and nothing beyond it."""
    return is_loopback_host(urlsplit(url).hostname)


class LogtoAuthProvider(RemoteAuthProvider):
    """Logto as the authorization server; Hermod stays a pure resource server.

    Logto ships no FastMCP provider class and needs none — the shape is a JWKS verifier plus the
    address of whoever mints the tokens, which is what FastMCP's own ``KeycloakAuthProvider``
    already is. Both OIDC paths are derived from the tenant endpoint because Logto fixes them and
    allows neither to be customised.

    ``set_mcp_path`` is overridden for one reason, and omitting it would fail quietly: the base
    class records the resource URL without passing it to the verifier, so ``audience`` would stay
    ``None`` and every token would clear the audience check by never being subjected to it.
    ``AuthKitProvider`` overrides it for exactly this reason; ``KeycloakAuthProvider`` does not,
    which is the trap in copying that file as-is.

    ``scopes_supported`` is passed for a symmetrical reason, and omitting it also failed quietly. A
    client builds its authorization request from what this server advertises in its
    protected-resource metadata, so an empty list tells the client to ask for nothing — and an
    authorization request carrying a resource but no scopes leaves the issuer with nothing to grant,
    which Logto answers with ``access_denied`` *after* the person has already signed in. Nothing in
    the server logs an error; the failure looks like a refused login.
    """

    #: Advertised so clients know what to ask for. ``openid`` is what makes it an OIDC request at
    #: all; ``offline_access`` buys a refresh token, without which a connector silently stops
    #: working whenever the access token expires.
    DEFAULT_SCOPES = ("openid", "profile", "email", "offline_access")

    def __init__(
        self,
        *,
        endpoint: str,
        base_url: str,
        audience: Sequence[str] = (),
        resource_base_url: str | None = None,
    ) -> None:
        oidc = f"{endpoint.rstrip('/')}/oidc"
        self._pinned_audience = tuple(audience)
        super().__init__(
            token_verifier=JWTVerifier(
                jwks_uri=f"{oidc}/jwks",
                issuer=oidc,
                algorithm="RS256",
                # A list even for a single entry, because the plural form is what lets two
                # registered identifiers be trusted at the same time.
                audience=list(self._pinned_audience) or None,
            ),
            authorization_servers=[AnyHttpUrl(oidc)],
            base_url=AnyHttpUrl(base_url.rstrip("/")),
            # What goes in the protected-resource metadata, and so what a client asks the issuer to
            # stamp the token for. It is separate from ``base_url`` because this server is
            # re-hosted: the metadata is served from the root while the resource it describes lives
            # under the mount, and only the caller knows that.
            resource_base_url=resource_base_url,
            # Advertised, deliberately not required. Enforcing ``openid`` on an incoming token adds
            # a rejection path while proving almost nothing — every OIDC token carries it — and the
            # access control that matters here is the audience check.
            scopes_supported=list(self.DEFAULT_SCOPES),
        )

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        """The advertised identifier, fixed rather than assembled from the mount path.

        The base class appends whatever path FastMCP believes it is serving at, which is a fact the
        sub-app gets wrong by construction — it is built for ``/`` and then re-hosted under
        ``/mcp``. Since ``resource_base_url`` already names the face in full, ignoring the path is
        what stops the two disagreeing, in either direction: a missing segment, or the doubled
        ``/mcp/mcp`` a plausible mount would produce.
        """
        if self.resource_base_url is not None:
            return self.resource_base_url
        return super()._get_resource_url(path)

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Bind the verifier's audience to the resource URL this server advertises."""
        super().set_mcp_path(mcp_path)
        if self._pinned_audience:
            return
        if self._resource_url is not None and isinstance(self.token_verifier, JWTVerifier):
            self.token_verifier.audience = str(self._resource_url)


def build_auth(config: Config) -> AuthProvider | None:
    """The verifier in front of the MCP face — or ``None``, in local mode.

    ``None`` is returned in exactly one case: **no issuer is configured**, which is the laptop
    shape. Tokens are verified exactly when there is something to verify them against, so there is
    no mode to set and therefore no mode to set wrong. The guard lives here rather than in the
    config loader so the same refusal fires whether the config came from the environment or was
    constructed directly.

    With no issuer the server must be unreachable from anywhere else, and two settings say whether
    it is: the public base URL, which states where it believes it is addressed, and the bind
    address, which decides which interfaces actually answer. A container has to bind ``0.0.0.0`` to
    be reachable even from its own host, so that half can be waived by ``HERMOD_LOCAL_BIND_ALL``.
    """
    if not config.logto_endpoint:
        if not is_loopback_url(config.public_base_url):
            raise LocalModeNotLoopbackError(
                f"no issuer is configured and HERMOD_PUBLIC_BASE_URL names "
                f"{config.public_base_url!r}, which is not this machine. Refusing to start: a "
                "server that checks no token serves the project store to anyone, and lets anyone "
                "rewrite it. Set HERMOD_LOGTO_ENDPOINT, or point HERMOD_PUBLIC_BASE_URL at "
                "loopback for a local run."
            )
        if not is_loopback_host(config.host) and not config.local_bind_all:
            raise LocalModeNotLoopbackError(
                f"no issuer is configured and HERMOD_HOST={config.host!r} would answer on every "
                "interface of this machine with no authentication. Bind to 127.0.0.1, or — only "
                "when something in front of this process publishes the port on the host's "
                "loopback, as compose.yaml does — set HERMOD_LOCAL_BIND_ALL=1 to say so."
            )
        return None

    # Every path past here verifies locally against Logto's public JWKS. No provider here calls the
    # vendor on each request, and none proxies OAuth: proxying would put a client secret on the box
    # and make this server the issuer of its own tokens.
    return LogtoAuthProvider(
        endpoint=config.logto_endpoint,
        base_url=config.public_base_url,
        audience=config.logto_audience,
        resource_base_url=mcp_resource_url(config.public_base_url),
    )


class StoreBackendError(RuntimeError):
    """Raised when the configured store backend is unknown or incomplete."""


def _validate_backend(config: Config) -> None:
    """Refuse an unknown or incomplete backend rather than defaulting silently.

    A wrong value must fail loud: silently reading an unknown backend as SQLite would point a
    deployment at a local file it never meant to use, and a missing DSN would leave a Postgres
    deployment with no store at all. Mirrors Munnin's own check.
    """
    if config.db_backend not in {"sqlite", "postgres"}:
        raise StoreBackendError(
            f"HERMOD_DB_BACKEND={config.db_backend!r} is not 'sqlite' or 'postgres'."
        )
    if config.db_backend == "postgres" and not config.db_url:
        raise StoreBackendError(
            "HERMOD_DB_BACKEND=postgres requires HERMOD_DB_URL (a PostgreSQL DSN)."
        )


def _build_project_repository(config: Config) -> ProjectRepository:
    """The store for the configured backend (both satisfy the Protocol).

    SQLite is the local and test path; Postgres is the deployed one, a second database on the
    instance the memory store lives on. Nothing above this line knows which is in play, which is
    the whole point of the seam.
    """
    if config.db_backend == "postgres":
        return PostgresProjectRepository(config.db_url)
    return SqliteProjectRepository(config.db_path)


async def health(request: Request) -> JSONResponse:
    """Liveness: the service name and its version, and nothing read from anywhere.

    Open by construction. It sits on the parent app while the guard sits on the mounted MCP face
    (authentication is enforced at the transport there), so this route never passes through it,
    and it reads nothing from the store — so it is also honest about what it proves.
    """
    return JSONResponse({"status": "ok", "service": "hermod", "version": __version__})


def build_app(config: Config | None = None, auth: AuthProvider | None = None) -> Starlette:
    """Wire the graph: one loader, one MCP face, one app carrying both.

    ``auth`` exists so tests can present a chosen token through a doubled verifier. It replaces
    *which* issuer is trusted and never whether verification happens — no value of this parameter
    switches auth off. The one way to run without verification is to configure no issuer at all,
    which ``build_auth`` grants only for a loopback public URL and bind.
    """
    config = config or load_config()
    auth = auth if auth is not None else build_auth(config)
    _validate_backend(config)
    content = ContentLoader(
        [ContentSource(name="overlay", root=config.content_root)],
        backend_doc=config.backend_doc,
    )
    service = ContextService(
        _build_project_repository(config),
        # The stamps come from the served templates themselves, so the gate can never drift from
        # what a client was told to read.
        {name: content.template_version(name) for name in content.list_resources()},
    )
    mcp_app = build_mcp(content, service, auth=auth).http_app(path="/")

    app = Starlette(
        routes=[
            Route("/health", health),
            Mount(MCP_MOUNT_PATH, app=mcp_app),
        ],
        # Without this the MCP session manager never starts: the sub-app's lifespan is its own.
        lifespan=mcp_app.lifespan,
    )

    # OAuth discovery, served from the **root**. FastMCP builds its app believing it sits at "/", so
    # it advertises root-level metadata URLs and puts its own copies of these routes inside the
    # sub-app — which Starlette then mounts under /mcp, leaving the advertised URLs 404. Measured on
    # Munnin: a 401 answers `WWW-Authenticate: Bearer resource_metadata="<base>/.well-known/..."`
    # while the document only existed at `/mcp/.well-known/...`, so a client following the standard
    # flow failed discovery and never reached a login screen.
    #
    # These two are deliberately unauthenticated, and must be: a client reads them to find out how
    # to obtain a token, so requiring one would be circular (RFC 9728). In local mode there is
    # nothing to discover — no issuer exists — so advertising one would only send a client to a
    # login that cannot happen.
    if auth is not None:
        app.router.routes.extend(auth.get_well_known_routes())

    # Outermost, so the rewrite happens before any router looks at the path — see the class for why
    # a redirect here is a broken login rather than a detour.
    app.add_middleware(_NormalisePath)
    return app
