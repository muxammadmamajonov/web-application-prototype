"""Retention for archived Telegram documents.

THE FINDING (Batch 4 §20, closed here). "Archived documents are never swept. A
revoked or superseded contract's archived copy stays in the tenant topic.
Harmless (the topic is private and the row is a cache) but unbounded." Unbounded
is the part that matters: every regeneration of a contract adds a permanent
message to the shop's forum topic and a permanent row to
``document_deliveries``, and nothing ever read the old one again.

WHAT AN ARCHIVE ENTRY IS, AND WHY THIS NEEDS NO LEGAL INPUT. It is a **cache of a
derived document**, not primary data. ``docs/RETENTION.md`` is a draft precisely
because the durations for KYC media and receipts are a legal question — and this
is deliberately not that. A contract PDF regenerates from ``installments`` on
demand; the archive only saves an upload. So the retention rule follows from how
the cache is read rather than from a retention period somebody has to decide:

    keep the entry that can still be a cache hit; sweep the ones that cannot.

``find_archived`` looks a document up by ``(kind, object_id, document_version)``
where the version is a hash of the bytes. Three classes of row can therefore
never be returned to anyone again:

* **superseded** — an older archived version of an object that has since been
  regenerated with different bytes;
* **orphaned** — an archive of an object that no longer exists;
* **failed** — a claim row whose upload never completed, which the cache lookup
  already ignores.

Everything else is left alone. In particular the newest archived version of a
live object is never swept, which is what keeps "deletion does not break live
records" true: the only rows removed are ones no code path can reach.

THE GRACE PERIOD is not a retention duration. It is slack so that a copy
superseded seconds ago is not yanked out from under an in-flight delivery, and
so a person who was just sent a document can still find it in the topic. It is
short and it is configurable.

ORDER OF OPERATIONS. The Telegram message is deleted FIRST and the row only
after that succeeds. The reverse order would drop the only record that a message
still needs deleting, leaving a file in the topic that nothing knows about — the
exact "metadata and physical storage drift apart" failure the batch asks to
avoid. A failed delete leaves the row, stamps ``error``, and the next sweep
retries it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentDelivery

log = logging.getLogger("muddato.document_retention")

#: How long a row that has become unreachable is kept before it is swept.
GRACE = timedelta(days=7)

#: Ceiling on one sweep, so a large backlog is drained over several runs instead
#: of holding a transaction and a Telegram rate-limit budget open indefinitely.
BATCH_LIMIT = 200

#: Which table holds the object each archive kind points at. An archive whose
#: object has been deleted is orphaned; a kind that is not listed is never
#: treated as orphaned, because "I do not know where to look" must not be
#: allowed to read as "the object is gone".
_OBJECT_TABLES: dict[str, str] = {
    "contract": "installments",
    "payment_receipt": "payments",
    "invoice": "invoices",
}


@dataclass
class SweepResult:
    """What one sweep did. Returned rather than logged only, so the scheduler
    job and the tests can both assert on it."""

    examined: int = 0
    deleted: int = 0
    retained_failures: int = 0

    def __bool__(self) -> bool:
        return bool(self.deleted or self.retained_failures)


async def find_sweepable(
    db: AsyncSession, *, now: datetime | None = None, grace: timedelta | None = None,
    limit: int = BATCH_LIMIT,
) -> list[DocumentDelivery]:
    """Archive rows that no cache lookup can return, older than the grace period.

    Deliberately a separate, testable query: "which rows are unreachable" is the
    part of this that has to be exactly right, and it is much easier to be sure
    of when it can be asserted on without deleting anything.
    """
    now = now or datetime.now(UTC)
    cutoff = now - (GRACE if grace is None else grace)

    # The newest SENT archive per (kind, object_id) is the one a lookup can still
    # hit. Everything older in the same group is superseded.
    newest = (
        select(
            DocumentDelivery.kind,
            DocumentDelivery.object_id,
            func.max(DocumentDelivery.created_at).label("newest_at"),
        )
        .where(
            DocumentDelivery.purpose == "archive",
            DocumentDelivery.status == "sent",
        )
        .group_by(DocumentDelivery.kind, DocumentDelivery.object_id)
        .subquery()
    )

    superseded = and_(
        DocumentDelivery.status == "sent",
        DocumentDelivery.created_at < newest.c.newest_at,
    )
    never_completed = DocumentDelivery.status != "sent"

    rows = (await db.execute(
        select(DocumentDelivery)
        .join(
            newest,
            and_(
                newest.c.kind == DocumentDelivery.kind,
                newest.c.object_id == DocumentDelivery.object_id,
            ),
            isouter=True,
        )
        .where(
            DocumentDelivery.purpose == "archive",
            DocumentDelivery.created_at < cutoff,
            or_(superseded, never_completed),
        )
        .order_by(DocumentDelivery.created_at)
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def find_orphaned(
    db: AsyncSession, *, now: datetime | None = None, grace: timedelta | None = None,
    limit: int = BATCH_LIMIT,
) -> list[DocumentDelivery]:
    """Archives whose underlying object no longer exists.

    Kept apart from :func:`find_sweepable` because it asks a different question of
    a different table, and because an unmapped kind must fall through to "not
    orphaned" rather than to "delete it".
    """
    now = now or datetime.now(UTC)
    cutoff = now - (GRACE if grace is None else grace)

    from sqlalchemy import text as _text

    out: list[DocumentDelivery] = []
    for kind, table in _OBJECT_TABLES.items():
        rows = (await db.execute(
            select(DocumentDelivery)
            .where(
                DocumentDelivery.purpose == "archive",
                DocumentDelivery.kind == kind,
                DocumentDelivery.created_at < cutoff,
            )
            .limit(limit)
        )).scalars().all()
        if not rows:
            continue
        ids = {r.object_id for r in rows}
        # One IN over the ids we actually hold — bounded by `limit`, so this does
        # not grow with the table. The table name comes from _OBJECT_TABLES, a
        # constant in this module, never from a row.
        present = {
            UUID(str(found)) for found in (await db.execute(
                _text(f"SELECT id FROM {table} WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [str(i) for i in ids]},
            )).scalars().all()
        }
        out.extend(r for r in rows if r.object_id not in present)
        if len(out) >= limit:
            break
    return out[:limit]


async def sweep_archived_documents(
    db: AsyncSession, *, now: datetime | None = None,
    grace: timedelta | None = None, limit: int = BATCH_LIMIT,
) -> SweepResult:
    """Delete unreachable archived documents from Telegram, then from the table.

    Idempotent: a row is only removed once its message is gone, and a message
    that is already gone counts as gone — so re-running finds nothing to do, and
    a sweep interrupted half-way resumes exactly where it stopped.
    """
    from app.services.telegram_media import delete_document_from_topic

    result = SweepResult()
    candidates = await find_sweepable(db, now=now, grace=grace, limit=limit)
    seen = {r.id for r in candidates}
    for row in await find_orphaned(db, now=now, grace=grace, limit=limit):
        if row.id not in seen:
            candidates.append(row)
            seen.add(row.id)

    for row in candidates:
        result.examined += 1
        # A row that never completed has nothing in the topic to remove.
        if row.status == "sent" and row.chat_id and row.message_id:
            ok = await delete_document_from_topic(
                chat_id=row.chat_id, message_id=row.message_id,
            )
            if not ok:
                # Leave the row. It is the only record that this message still
                # needs deleting, and the next sweep will try again.
                row.error = "retention_delete_failed"
                result.retained_failures += 1
                continue
        await db.delete(row)
        result.deleted += 1

    await db.flush()
    if result.examined:
        log.info(
            "document_retention.sweep examined=%s deleted=%s retained_failures=%s",
            result.examined, result.deleted, result.retained_failures,
        )
    return result


async def sweep_archived_documents_job() -> None:
    """Scheduler entry point. Cross-tenant by nature, so it runs under the
    explicit WORKER authority like every other job."""
    from app.db import worker_session

    try:
        async with worker_session() as db:
            result = await sweep_archived_documents(db)
            await db.commit()
        if result.retained_failures:
            log.warning(
                "document_retention.sweep_incomplete retained_failures=%s",
                result.retained_failures,
            )
    except Exception:
        log.exception("document_retention.sweep_failed")


__all__ = (
    "BATCH_LIMIT",
    "GRACE",
    "SweepResult",
    "find_orphaned",
    "find_sweepable",
    "sweep_archived_documents",
    "sweep_archived_documents_job",
)
