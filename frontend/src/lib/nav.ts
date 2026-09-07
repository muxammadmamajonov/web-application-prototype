/** Mobile-nav helper (#33). Is `pathname` a drill-down subpage — i.e. strictly
 *  deeper than one of the top-level tab `roots`? Used to hide the bottom tab bar
 *  on subpages so drill-downs (e.g. /me/installments/:id) rely on their own Back.
 *
 *  Fail-safe: ONLY paths clearly nested under a root count as subpages, so the
 *  persona home and any route we don't recognise keep the bar (never trapped).
 *  Nested roots are handled — /uz/me/installments stays a root even though it
 *  nests under the /uz/me home.
 */
export function isSubpagePath(pathname: string, roots: string[]): boolean {
  const norm = (p: string) => p.replace(/\/+$/, '');
  const path = norm(pathname);
  const normRoots = roots.map(norm);
  return !normRoots.includes(path) && normRoots.some((r) => path.startsWith(`${r}/`));
}

/**
 * Is `pathname` a drill-down below the persona's top-level destinations — i.e.
 * something that should get Telegram's native Back button (vs. the top-level
 * tabs, which keep "Close")?
 *
 * Unlike isSubpagePath (which only flags paths nested UNDER a specific root),
 * this flags anything that shares a root's persona BASE without being a root
 * itself — so sibling drill-downs that don't nest under any one tab still count
 * (e.g. /uz/me/installments/:id sits beside the home/profile tabs, not under
 * them). The base is a root with its last segment stripped (/uz/me/home → /uz/me).
 */
export function isDrillDownPath(pathname: string, navRoots: string[]): boolean {
  const norm = (p: string) => p.replace(/\/+$/, '');
  const path = norm(pathname);
  const roots = navRoots.map(norm);
  if (roots.includes(path)) return false;
  const bases = new Set(roots.map((r) => r.replace(/\/[^/]+$/, '')));
  return [...bases].some((b) => b !== '' && path.startsWith(`${b}/`));
}
