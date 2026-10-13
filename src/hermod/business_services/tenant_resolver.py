"""Who the current caller is acting as.

Hermod's store is Valaskjalf's records, and every row in them belongs to a tenant. So the tenant
is not a setting: it comes from the verified token of the call in progress, resolved through the
same `user_identity` mapping Munnin writes — the same Google account has to land on the same rows
through either server, and a configured value could drift from the login it is meant to mirror.

The mechanism is Munnin's, deliberately, including the parts that look optional:

- **An unmapped pair creates a tenant rather than refusing one.** Admission is the authorization
  server's job; a second gate here would have to agree with a dashboard forever, with nothing
  reporting divergence. Creation is logged loudly instead, because a door you already control is
  not worth a flag, while an unexpected opening is worth a line at WARNING.
- **The tenant id is ours, never the issuer's subject.** `user_id` is minted here, so a subject
  string is never load-bearing across an issuer change.
- **The write is re-read afterwards**, because two simultaneous first logins for one pair both
  reach the create and only one row survives the idempotent insert; the stored answer is the true
  one. The loser leaves its `account` row behind — nothing points at it, no memory reaches it, and
  no read can see it — which is the price of settling that race by re-reading rather than by
  locking the pair.
- **Email and display name are labels for a new tenant only, never a lookup key.** An issuer may
  reassign an address to somebody else, so matching on it could hand over a stranger's memory.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import uuid4

import psycopg
from fastmcp.server.dependencies import get_access_token
from psycopg.rows import tuple_row

_log = logging.getLogger("hermod.identity")

#: One row per (issuer, subject) pair, which is the pair OpenID Connect guarantees to be stable.
_LINK = (
    "INSERT INTO user_identity (iss, sub, user_id, linked_date) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (iss, sub) DO NOTHING"
)


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
        with psycopg.connect(self._db_url, row_factory=tuple_row) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            found = cur.fetchone()
        if found is None:
            raise LookupError(
                f"no tenant is mapped to iss={iss!r} sub={sub!r}, and an import refuses to invent "
                "one: it would write a second tenant's worth of rows that no login would ever "
                "reach. Sign in once with that account, or check the pair."
            )
        return found[0]

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
        with psycopg.connect(self._db_url) as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            found = cur.fetchone()
            if found is not None:
                return found[0]

            user_id = uuid4().hex
            cur.execute(
                "INSERT INTO account (user_id, display_name, email, created_date) "
                "VALUES (%s, %s, %s, now()::date::text)",
                (user_id, display_name, email),
            )
            cur.execute(_LINK, (iss, sub, user_id, _today()))
            # Re-read rather than returning what we minted: a concurrent first login for the same
            # pair reaches the same branch, and only one of the two rows survives.
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            settled = cur.fetchone()
            conn.commit()

        resolved = settled[0] if settled is not None else user_id
        # WARNING, not INFO: a tenant appearing is the event worth seeing even on a server whose
        # log level has been raised, because it should only happen when somebody was invited.
        _log.warning("new tenant created: user_id=%s iss=%s sub=%s", resolved, iss, sub)
        return resolved


def _today() -> str:
    return date.today().isoformat()
