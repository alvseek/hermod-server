"""Resolve verified caller identities to home accounts through Workspace Access."""

from __future__ import annotations

import logging

from fastmcp.server.dependencies import get_access_token
from valaskjalf_workspace_access import PostgresWorkspaceAccess

_log = logging.getLogger("hermod.identity")

class MissingTokenError(RuntimeError):
    """Raised when a tenant is asked for on a call that carries no verified token.

    Unreachable in the deployed shape: the guard refuses an unauthenticated request before any
    handler runs. It exists so that a route added outside the guard fails loudly on its first
    call rather than quietly serving somebody a tenant.
    """


class TenantResolver:
    """Resolves the call's token to a tenant, creating it on a miss."""

    def __init__(self, *, db_url: str) -> None:
        self._db_url = db_url
        self._access = PostgresWorkspaceAccess(db_url)

    # --- the seam every handler asks ----------------------------------------------

    def current_user_id(self) -> str:
        """The tenant named by the verified token of the call in progress."""
        token = get_access_token()
        if token is None:
            raise MissingTokenError(
                "no verified token on this call — a handler is running outside the "
                "authentication guard"
            )
        claims = token.claims
        iss, sub = claims.get("iss"), claims.get("sub")
        if not iss or not sub:
            # A token that verified but names no subject names no tenant, and guessing one is the
            # exact failure the pair exists to prevent.
            raise MissingTokenError(
                "verified token carries no issuer/subject pair, so it names no tenant"
            )
        return self.resolve(
            str(iss),
            str(sub),
            email=claims.get("email"),
            display_name=claims.get("name"),
        )

    def resolve_existing(self, iss: str, sub: str) -> str:
        """The tenant for a pair that must already be mapped, refusing when it is not.

        For a caller with no token. The importer runs as an offline CLI, so it cannot use
        ``current_user_id``, and it must not use ``resolve``: importing into a tenant that did not
        exist a moment ago is almost certainly a typo, and it is the kind of mistake that takes
        longest to notice, because the rows land somewhere valid and nothing errors.
        """
        found = self._access.lookup_home(iss, sub)
        if found is None:
            raise LookupError(
                f"no tenant is mapped to iss={iss!r} sub={sub!r}, and an import refuses to invent "
                "one: it would write a second tenant's worth of rows that no login would ever "
                "reach. Sign in once with that account, or check the pair."
            )
        return found

    # --- the write ------------------------------------------------------------------

    def resolve(
        self,
        iss: str,
        sub: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> str:
        """The tenant for one issuer-and-subject pair, created if it has never been seen."""
        home = self._access.get_or_create_home(
            iss, sub, email=email, display_name=display_name
        )
        if home.created:
            _log.warning("new tenant created: user_id=%s iss=%s sub=%s", home.user_id, iss, sub)
        return home.user_id
