"""(#845) Running an Alembic migration must NOT disable the app's loggers.

`alembic/env.py` calls `logging.config.fileConfig`. Its default
`disable_existing_loggers=True` flips `disabled=True` on every already-created logger
(`muddato.*`), so the app goes silent after a migration runs — in tests this kills the
`request.completed` audit line for any test that runs after a `migrated_db` fixture
(an order-dependent failure of `test_request_audit_log`), and in-process at deploy it
would mute all application logging. `env.py` must pass `disable_existing_loggers=False`.
"""
from __future__ import annotations

import logging
from logging.config import fileConfig
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]


def test_env_py_passes_disable_existing_loggers_false() -> None:
    env_src = (_BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "disable_existing_loggers=False" in env_src, (
        "alembic/env.py must call fileConfig(disable_existing_loggers=False) so a "
        "migration run doesn't disable the app's loggers"
    )


def test_migration_logging_config_leaves_app_loggers_enabled() -> None:
    """Behavioural: configuring logging the way (fixed) env.py does must leave an
    already-created app logger ENABLED. Save/restore the global logging state so this
    test doesn't itself leak a reconfiguration onto the rest of the suite."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    probe = logging.getLogger("muddato.observability")
    saved_disabled = probe.disabled
    try:
        probe.disabled = False
        fileConfig(str(_BACKEND / "alembic.ini"), disable_existing_loggers=False)
        assert probe.disabled is False, "a migration's logging config must not disable app loggers"
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        probe.disabled = saved_disabled
