"""The tenant comes from the token, resolved through Munnin's own mapping.

These run against a real PostgreSQL, because the mechanism *is* SQL: the `(iss, sub)` primary key,
the foreign key into `account`, and the create-then-re-read that makes two simultaneous first
logins settle on one tenant. A double would prove none of it.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from hermod.business_services.tenant_resolver import MissingTokenError, TenantResolver
from tests.valaskjalf_schema import make_store

ISSUER = "https://auth.lok.quest/oidc"
OTHER_ISSUER = "https://elsewhere.invalid/oidc"

pytestmark = pytest.mark.postgres


@pytest.fixture
def resolver() -> TenantResolver:
    url = os.getenv("HERMOD_PG_TEST_URL")
    if not url:
        pytest.skip("HERMOD_PG_TEST_URL not set")
    make_store(url, user_id="alvi", agents=())
    return TenantResolver(db_url=url)


def _account(resolver: TenantResolver, user_id: str) -> dict:
    with psycopg.connect(resolver._db_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT display_name, email FROM account WHERE user_id = %s", (user_id,)
        )
        row = cur.fetchone()
    return {"display_name": row[0], "email": row[1]}


def test_a_new_pair_gets_a_tenant_and_keeps_it(resolver: TenantResolver) -> None:
    first = resolver.resolve(ISSUER, "sub-a")
    assert first
    assert resolver.resolve(ISSUER, "sub-a") == first


def test_the_same_subject_under_another_issuer_is_another_tenant(resolver: TenantResolver) -> None:
    """The reason the key is the pair: a subject is unique only within its issuer."""
    assert resolver.resolve(ISSUER, "sub-a") != resolver.resolve(OTHER_ISSUER, "sub-a")


def test_the_tenant_is_minted_here_not_taken_from_the_subject(resolver: TenantResolver) -> None:
    user_id = resolver.resolve(ISSUER, "sub-a")
    assert user_id != "sub-a"
    assert len(user_id) == 32 and all(c in "0123456789abcdef" for c in user_id)


def test_labels_are_stored_for_a_new_tenant_only_and_never_looked_up(
    resolver: TenantResolver,
) -> None:
    """The leak this prevents: matching a person by an address their issuer may reassign."""
    first = resolver.resolve(ISSUER, "sub-a", email="shared@example.com", display_name="Sub A")
    second = resolver.resolve(ISSUER, "sub-b", email="shared@example.com", display_name="Sub B")

    assert first != second
    assert _account(resolver, first) == {"display_name": "Sub A", "email": "shared@example.com"}
    assert _account(resolver, second) == {"display_name": "Sub B", "email": "shared@example.com"}


def test_a_second_resolve_leaves_the_stored_labels_alone(resolver: TenantResolver) -> None:
    first = resolver.resolve(ISSUER, "sub-a", email="one@example.com", display_name="First")
    again = resolver.resolve(ISSUER, "sub-a", email="two@example.com", display_name="Second")

    assert again == first
    assert _account(resolver, first) == {"display_name": "First", "email": "one@example.com"}


def test_a_call_with_no_token_names_no_tenant(resolver: TenantResolver) -> None:
    with pytest.raises(MissingTokenError, match="no verified token"):
        resolver.current_user_id()
