/** Email helpers — ONE source of truth for domain suggestions, typo correction
 *  and validation. Used by both the form `EmailInput` (components/ui/FormFields)
 *  and the public `SmartContact` field, which previously each carried their own
 *  (divergent) copy of this logic. */

/** Popular email domains for Uzbek users, roughly by prevalence. Drives the
 *  autocomplete dropdown and the typo-correction candidate set. */
export const EMAIL_DOMAINS = [
  'gmail.com', 'mail.ru', 'icloud.com', 'yandex.ru', 'outlook.com',
  'yahoo.com', 'inbox.ru', 'umail.uz', 'proton.me',
];

/** Pragmatic email check — `local@domain.tld` with no spaces and a ≥2-char TLD.
 *  Not the full RFC grammar (deliberately): it accepts every address a normal
 *  user types and rejects the obvious mistakes, which is what a form needs. */
export function isValidEmail(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test((v || '').trim());
}

/** Levenshtein edit distance — inputs are short domains, so the simple DP is fine. */
function editDistance(a: string, b: string): number {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, (_, i) => [i, ...new Array(n).fill(0)]);
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return dp[m][n];
}

/** Nearest known domain within edit-distance 2 — catches `gmial.com`→`gmail.com`,
 *  `gmail.con`→`gmail.com`. Returns null when the domain is already known or too
 *  far from any candidate to be a confident correction. */
export function nearestEmailDomain(domain: string): string | null {
  const d = (domain || '').toLowerCase();
  if (!d || EMAIL_DOMAINS.includes(d)) return null;
  let best: string | null = null;
  let bestDist = 3;
  for (const cand of EMAIL_DOMAINS) {
    const dist = editDistance(d, cand);
    if (dist < bestDist) { bestDist = dist; best = cand; }
  }
  return bestDist <= 2 ? best : null;
}

/** Autocomplete/typo suggestions for the current value:
 *  - no `@`, or empty local part → none
 *  - `@` with empty domain → every known domain
 *  - partial domain (no dot yet) → prefix matches
 *  - full but unknown domain → a single typo correction (if close enough)
 *  - already-known full domain → none */
export function emailSuggestions(value: string): string[] {
  const at = value.indexOf('@');
  if (at < 0) return [];
  const local = value.slice(0, at);
  if (!local) return [];
  const domain = value.slice(at + 1).toLowerCase();
  if (!domain) return EMAIL_DOMAINS.map((d) => `${local}@${d}`);
  if (EMAIL_DOMAINS.includes(domain)) return [];
  if (domain.includes('.')) {
    const near = nearestEmailDomain(domain);
    return near ? [`${local}@${near}`] : [];
  }
  return EMAIL_DOMAINS.filter((d) => d.startsWith(domain)).map((d) => `${local}@${d}`);
}
