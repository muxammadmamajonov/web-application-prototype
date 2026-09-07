"""Alembic environment — reads the sync DB URL from app settings and the metadata from app.models."""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (ensures all models are imported)
from alembic import context

# Import the project settings + metadata. This also wires every ORM model
# into the metadata so autogenerate sees them.
from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False (NOT the fileConfig default): otherwise running a
    # migration disables EVERY already-created app logger (muddato.*), so the app goes
    # silent — in tests it kills the request.completed audit line for any test after a
    # migrated_db run (#845), and in-process at deploy it would mute all logging.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Inject the MIGRATION credential — never the runtime one.
#
# (Batch 5 correction) Alembic runs DDL, so it must connect as the schema owner.
# ``settings.migration_url_sync`` resolves MIGRATION_DATABASE_URL_SYNC, then
# MIGRATION_DATABASE_URL, and in production RAISES rather than falling back to
# DATABASE_URL: the ordinary runtime login is deliberately unable to CREATE,
# ALTER or DROP anything, so a silent fallback would either fail mid-DDL with a
# confusing privilege error or succeed only on a cluster where the split had
# been undone. Outside production it falls back with a CRITICAL log so a
# one-URL developer database still works.
config.set_main_option("sqlalchemy.url", settings.migration_url_sync)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # Each migration runs in its own transaction (not one giant
            # transaction for the whole chain), so a late failure doesn't roll
            # back everything and a migration can drop into an autocommit_block
            # for CONCURRENTLY DDL (#76).
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
