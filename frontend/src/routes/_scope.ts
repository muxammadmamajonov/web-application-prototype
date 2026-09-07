/**
 * MVP route scope.
 *
 * The pages behind deferred surfaces still exist in `src/routes/**` on purpose:
 * re-enabling one is a flip of this flag rather than resurrecting deleted code.
 *
 * What this flag governs is *reachability*: while it is false, a deferred
 * surface has no route registered, so it cannot be reached by navigation, by a
 * typed URL, or by a stale deep link — a wildcard redirect sends those to the
 * persona's retained landing screen instead.
 *
 * The explicit `: boolean` annotation is deliberate. It stops TypeScript from
 * narrowing the value to the literal `false`, which would make every guarded
 * JSX branch statically dead and trip `noUnusedLocals` on the lazy page imports
 * those branches reference.
 */
export const V2_SURFACES_ENABLED: boolean = false;

/**
 * (#15) The tenant Reports section.
 *
 * Deferred for a different reason than the V2 surfaces above, so it gets its own
 * switch rather than borrowing theirs: Reports is not unfinished, it is
 * REDUNDANT — the Overview dashboard already carries the figures a shop reads
 * day to day, and a second place to read them splits attention without adding an
 * answer.
 *
 * Nothing is deleted. `ReportsPage` and every endpoint behind it (aging,
 * cashflow, staff, products, LTV, margin) stay live, and `DateRangePicker`'s
 * shared `reports.*` copy is used well outside this page. Flip this to true and
 * the section returns intact.
 */
export const REPORTS_ENABLED: boolean = false;
