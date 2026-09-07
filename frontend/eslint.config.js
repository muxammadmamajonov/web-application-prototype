// ESLint flat config for the Muddato frontend (Vite + React 19 + TypeScript).
//
// Migrated from the classic `.eslintrc.cjs` (ESLint 8) to ESLint 9's flat
// config. Rule-for-rule equivalent to the previous config — this migration
// is tooling only, not a rule-set change. If a rule newly flags existing code
// under ESLint 9, it is turned `off` here with a comment explaining why,
// rather than mass-fixing `src/**` in the same change.
//
// Type-aware rules are intentionally NOT enabled (no `parserOptions.project`):
// `tsc -b` already provides full type checking in CI/build; ESLint here covers
// the lint layer (unused vars, hook rules, obvious foot-guns) and stays fast.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

// Base env: `browser: true, es2021: true` in the old .eslintrc.cjs.
const baseGlobals = { ...globals.browser, ...globals.es2021 };

export default tseslint.config(
  // Build output, generated declaration dirs, and tool dotfolders are not source.
  { ignores: ['dist', 'public', '.tsbuild-node', '.wrangler'] },
  {
    // ESLint 9 flat config defaults `reportUnusedDisableDirectives` to "warn"
    // (ESLint 8's eslintrc format did not check this at all). At least one
    // existing `eslint-disable-next-line` comment in `src/` is now stale
    // under that new default. This migration is tooling-only — fixing/removing
    // stale disable comments in `src/**` is a separate, deliberate change —
    // so restore the ESLint 8 behavior here rather than touch source files.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
    ],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: baseGlobals,
    },
    plugins: {
      'react-refresh': reactRefresh,
    },
    rules: {
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // `_` / `_foo` are intentional throwaways (ignored destructure slots, unused
      // args) — the same convention the Python backend uses. Don't flag them.
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
        },
      ],
    },
  },
  {
    // Vite build config runs in Node (uses __dirname / process / node:path).
    files: ['vite.config.ts'],
    languageOptions: {
      globals: { ...baseGlobals, ...globals.node },
    },
  },
  {
    // Cloudflare Pages Functions run in the Workers runtime: browser-like
    // globals (fetch/Response/URL/AbortController) plus the ambient
    // PagesFunction type. They are not part of the `src` tsconfig project.
    files: ['functions/**/*.ts'],
    languageOptions: {
      globals: { ...baseGlobals, ...globals.worker },
    },
  },
);
