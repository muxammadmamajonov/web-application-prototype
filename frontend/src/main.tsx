import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { queryClient } from './lib/queryClient';
import { initSentry, reportError } from './lib/sentry';
import { warmApi } from './lib/api';
import './i18n';
import './styles/globals.css';

initSentry(); // (#159) DSN-gated; inert without VITE_SENTRY_DSN

// (#F-30) Global async-error handlers. React error boundaries only catch errors
// thrown during render — rejected promises from event handlers/effects and
// errors raised outside React never reach a boundary. Forward both to Sentry
// (reportError is a no-op until a DSN is configured) so async failures stop
// vanishing silently. Listeners are passive: we never preventDefault, so the
// browser's own console reporting is preserved.
if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (event) => {
    reportError(event.reason ?? new Error('Unhandled promise rejection'));
  });
  window.addEventListener('error', (event) => {
    reportError(event.error ?? new Error(event.message || 'Uncaught error'));
  });
}

// Canonical-domain redirect: the Cloudflare Pages preview host (*.pages.dev)
// forwards visitors to the real domain, preserving the path.
// (MUDD-71 L1) The domain is build-time config, not a source literal: a fork, a
// staging Pages project or a rebrand would otherwise have to patch main.tsx —
// and would fail loudly only after shipping. The default keeps prod unchanged.
const CANONICAL_ORIGIN = import.meta.env.VITE_CANONICAL_ORIGIN ?? 'https://muddato.uz';

if (typeof window !== 'undefined' && window.location.hostname.endsWith('.pages.dev')) {
  window.location.replace(`${CANONICAL_ORIGIN}${window.location.pathname}${window.location.search}${window.location.hash}`);
}

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root not found');

function render() {
  createRoot(rootEl!).render(
    <StrictMode>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </ErrorBoundary>
    </StrictMode>,
  );
}

// DEV-ONLY operator-console mock (see lib/devMock). Enabled via VITE_DEV_MOCK
// env, a `?mock=1` URL param, or localStorage('devMock'). The runtime flag is the
// SECOND `&&` operand on purpose: `import.meta.env.DEV` is statically `false` in
// prod builds, so `false && …` makes the whole branch — including the dynamic
// import — dead code that the bundler drops. Nothing mock ships to prod.
function devMockFlag(): boolean {
  try {
    if (import.meta.env.VITE_DEV_MOCK === '1') return true;
    if (new URLSearchParams(window.location.search).has('mock')) { localStorage.setItem('devMock', '1'); return true; }
    return localStorage.getItem('devMock') === '1';
  } catch { return false; }
}

// (#4) Wake the API before the user asks it for anything. See `warmApi`.
warmApi();

if (import.meta.env.DEV && devMockFlag()) {
  import('./lib/devMock').then((m) => { m.installDevMock(); render(); });
} else {
  render();
}
