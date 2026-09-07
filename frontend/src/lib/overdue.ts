/**
 * Overdue classification for contract rows.
 *
 * The server owns this decision. `mark_overdue` is a nightly cron, so between a
 * due date passing and that job running a genuinely overdue contract still
 * carries `status: 'active'`. The collections worklist therefore derives
 * `is_overdue` from the due date against the TENANT's today and sends it with
 * every row; reclassifying in the browser would reintroduce both the cron lag
 * and the timezone bug (shops run at UTC+5, the browser may be anywhere).
 *
 * `status` is used only as a fallback for rows that did not come from the
 * worklist — the ordinary list endpoint does not compute this field.
 */

interface OverdueLike {
  status?: string | null;
  /** Server-derived; present on worklist rows only. */
  is_overdue?: boolean;
  next_due_date?: string | null;
}

/** True when the contract's payment is late. Prefers the server's verdict. */
export function isRowOverdue(row: OverdueLike): boolean {
  return row.is_overdue ?? row.status === 'overdue';
}

/**
 * Days since the epoch for a `YYYY-MM-DD` calendar date, or `null` if it is not
 * one.
 *
 * Built with `Date.UTC`, which has no daylight-saving transitions, so the
 * difference between two ordinals is always an exact whole number of calendar
 * days. Parsing `"…T00:00:00"` instead yields LOCAL midnight, and subtracting
 * two local midnights across a DST boundary gives 23 or 25 hours — which
 * `Math.floor(ms / 86_400_000)` then turns into an off-by-one day.
 *
 * The round-trip check rejects a date that is well-formed but not real
 * (`2026-02-31`), which `Date.UTC` would silently roll forward into March.
 */
function dayOrdinal(ymd: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const ms = Date.UTC(y, mo - 1, d);
  const back = new Date(ms);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) {
    return null;
  }
  return Math.round(ms / 86_400_000);
}

/** Today on the viewer's own calendar, as the same ordinal. */
function todayOrdinal(): number {
  const now = new Date();
  return Math.round(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / 86_400_000);
}

/**
 * Whole days a contract is past its due date, 0 when it is not overdue.
 *
 * `asOfDate` is the worklist's tenant-local `as_of_date`. Passing it keeps the
 * count on the tenant's calendar; without it the viewer's own calendar date is
 * the only reference available, which is why worklist-backed screens should
 * always pass it through.
 *
 * An unparseable or impossible date yields 0 rather than a wrong number — a
 * wrong day count on a collections screen is worse than no day count.
 */
export function overdueDays(row: OverdueLike, asOfDate?: string | null): number {
  if (!isRowOverdue(row) || !row.next_due_date) return 0;
  const due = dayOrdinal(row.next_due_date);
  if (due === null) return 0;
  const ref = asOfDate ? dayOrdinal(asOfDate) : todayOrdinal();
  if (ref === null) return 0;
  return Math.max(0, ref - due);
}
