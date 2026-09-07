// @vitest-environment jsdom
/**
 * H17 — `DataTable` itself was never wrapped in `React.memo` ("useMemo faqat
 * `sorted` qatorlar uchun" — the audit's own words), so even a caller that
 * memoizes its `columns`/`rows` props still re-rendered every row on any
 * unrelated parent state change (a debounced search keystroke, a filter
 * drawer toggling open).
 *
 * A render-count assertion across a real update is not used here: this
 * component also calls `useTranslation()`, whose i18next subscription can
 * itself schedule renders independent of props/memo, which would make a
 * call-count assertion flaky for reasons unrelated to what this test is
 * meant to pin. Instead this asserts the concrete, deterministic fact H17
 * asked for — `export const DataTable = memo(...)` — by checking the
 * component's own `$$typeof`, the same marker `React.memo` itself sets and
 * that `react-test-renderer`/React DevTools use to identify memoized
 * components.
 */
import { describe, expect, it } from 'vitest';
import { DataTable } from './DataTable';

describe('DataTable is wrapped in React.memo (H17)', () => {
  it('carries the react.memo marker', () => {
    expect((DataTable as unknown as { $$typeof: symbol }).$$typeof).toBe(Symbol.for('react.memo'));
  });
});
