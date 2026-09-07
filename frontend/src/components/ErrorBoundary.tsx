import { Component, type ErrorInfo, type ReactNode } from 'react';

import { reportError } from '../lib/sentry';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

/**
 * Top-level error boundary. A render throw anywhere below it is caught here
 * instead of white-screening the whole SPA. Deliberately dependency-light —
 * the fallback uses plain Tailwind classes and no app providers (i18n, router,
 * design-system components), since any of those could be what crashed.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportError(error, info.componentStack);   // (#159) → Sentry when a DSN is set
    console.error('Unhandled render error:', error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="min-h-screen flex items-center justify-center bg-cream p-6">
        <div className="w-full max-w-narrow rounded-card border border-line bg-white p-8 text-center shadow-card">
          <h1 className="text-section font-semibold tracking-tight text-ink">
            Nimadir noto&apos;g&apos;ri ketdi
          </h1>
          <p className="mt-2 text-body text-ink/60">
            Kutilmagan xatolik yuz berdi. Sahifani qayta yuklab ko&apos;ring.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-6 inline-flex h-10 items-center justify-center rounded-control bg-ink px-5 text-body font-medium text-white transition hover:bg-ink/90"
          >
            Qayta yuklash
          </button>
        </div>
      </div>
    );
  }
}
