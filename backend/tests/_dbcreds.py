"""The two database identities, for tests that must tell them apart.

``TEST_DATABASE_URL`` is the MIGRATION credential: the schema owner. It is what
migrates and seeds a test world, because nothing else can.

The RUNTIME credential is the restricted login the application actually uses —
``muddato_api``: NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owner of nothing. It is
derived from the migration URL by swapping the username, so a developer only has
to set one variable, and can be overridden with
``TEST_RUNTIME_DATABASE_URL`` where the two live on different hosts.

The point of having both in the test suite at all: a security property proved
only against the owner connection is not proved. Every assertion about what the
application *cannot* do has to be made through the credential the application
actually holds.
"""
from __future__ import annotations

import os

from app.core.rls_manifest import ROLE_API_LOGIN


def migration_url() -> str | None:
    """The schema-owner URL (async), or None when the DB tier is not configured."""
    return os.environ.get("TEST_DATABASE_URL")


def runtime_url() -> str | None:
    """The restricted application-login URL (async), or None."""
    explicit = os.environ.get("TEST_RUNTIME_DATABASE_URL")
    if explicit:
        return explicit
    base = migration_url()
    return with_user(base, ROLE_API_LOGIN) if base else None


def sync(url: str) -> str:
    """The psycopg form of an asyncpg URL."""
    return url.replace("+asyncpg", "+psycopg")


def render(url: object) -> str:
    """A SQLAlchemy ``URL`` as a CONNECTABLE string — password intact.

    Never ``str(url)``. SQLAlchemy renders the password as the literal ``***``
    there, on purpose, so an engine built from that string tries to authenticate
    as ``***`` and is refused. On a ``trust``-authenticated developer cluster
    there is no password to hide and the bug is invisible; against CI's
    scram cluster it fails every connection — which is exactly how a whole
    class of "works locally" test failures got here.
    """
    return url.render_as_string(hide_password=False)  # type: ignore[attr-defined]


def with_user(url: str, username: str) -> str:
    """``url`` re-pointed at ``username``, dropping any password.

    The restricted login is provisioned by migration ``rg0credsplit`` with no
    password, which is unusable over scram and perfectly usable on a
    ``trust``-configured local cluster — exactly the split we want: a test
    machine can authenticate as it, a production cluster cannot until an
    operator sets one out of band.
    """
    from sqlalchemy.engine import make_url

    return str(make_url(url).set(username=username, password=None))
