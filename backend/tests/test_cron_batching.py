"""H4 — keyset batching for cron jobs terminates and covers every row once."""
from __future__ import annotations

import asyncio

from app.services.scheduler import _CRON_BATCH, _iter_keyset


class _Row:
    def __init__(self, i):
        self.id = i


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _PagingDB:
    """Returns successive id-ordered pages regardless of the statement,
    emulating a table of ``total`` rows fetched in ``batch`` chunks."""

    def __init__(self, total, batch):
        self._all = [_Row(i) for i in range(total)]
        self._batch = batch
        self._cursor = 0

    async def execute(self, _stmt):
        page = self._all[self._cursor:self._cursor + self._batch]
        self._cursor += len(page)
        return _ScalarResult(page)


class _FakeStmt:
    # The fake DB ignores the statement, so these chainable no-ops are enough.
    def where(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self


class _FakeCol:
    def __gt__(self, other):
        return None

    def asc(self):
        return None


def _collect(total, batch):
    async def run():
        db = _PagingDB(total, batch)
        out = []
        async for r in _iter_keyset(db, base_stmt=_FakeStmt(), pk_col=_FakeCol(), batch=batch):
            out.append(r.id)
        return out
    return asyncio.run(run())


def test_keyset_covers_all_rows_once_exact_multiple() -> None:
    ids = _collect(total=1000, batch=500)  # 500, 500, then empty
    assert ids == list(range(1000))


def test_keyset_terminates_on_short_final_page() -> None:
    ids = _collect(total=750, batch=500)  # 500, then 250 (<batch → stop)
    assert ids == list(range(750))


def test_keyset_empty_set() -> None:
    assert _collect(total=0, batch=500) == []


def test_cron_batch_is_bounded() -> None:
    assert 0 < _CRON_BATCH <= 1000
