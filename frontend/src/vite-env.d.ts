/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_API_PROXY?: string;
  readonly VITE_TELEGRAM_BOT_USERNAME?: string;
  /** Canonical public origin the *.pages.dev preview host redirects to. */
  readonly VITE_CANONICAL_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
