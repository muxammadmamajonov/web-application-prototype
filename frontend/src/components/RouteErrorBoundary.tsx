import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useLocation } from 'react-router';
import { reportError } from '../lib/sentry';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  errorKey: string;
}

/**
 * Per-route error boundary that catches render errors inside a shell's
 * <Routes> without blanking the sidebar. A throw in any lazy page is caught
 * here, shows an in-content error card with a retry button, and the shell
 * remains navigable.
 *
 * Keyed by pathname so navigating away and back resets the error state — the
 * new route re-renders fresh and the error is cleared.
 */
class RouteErrorBoundaryInner extends Component<Props & { location: ReturnType<typeof useLocation> }, State> {
  state: State = { hasError: false, errorKey: this.props.location.pathname };

  static getDerivedStateFromError(): Partial<State> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportError(error, info.componentStack);
    console.error('Route render error:', error, info.componentStack);
  }

  componentDidUpdate(prevProps: Props & { location: ReturnType<typeof useLocation> }) {
    // Clear error when pathname changes (user navigated away and back, or
    // the route re-mounted). The new component tree renders fresh.
    if (prevProps.location.pathname !== this.props.location.pathname) {
      this.setState({ hasError: false, errorKey: this.props.location.pathname });
    }
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="grid min-h-[60vh] place-items-center p-6">
        <div className="w-full max-w-narrow rounded-card border border-line bg-white p-8 text-center shadow-card">
          <h2 className="text-section font-semibold tracking-tight text-ink">
            Bu sahifa yuklanmadi
          </h2>
          <p className="mt-2 text-body text-ink/60">
            Kutilmagan xatolik yuz berdi. Sahifani qayta yuklash uchun tugmani bosing.
          </p>
          <button
            type="button"
            onClick={() => this.setState({ hasError: false })}
            className="mt-6 inline-flex h-10 items-center justify-center rounded-control bg-ink px-5 text-body font-medium text-white transition hover:bg-ink/90"
          >
            Qayta yuklash
          </button>
        </div>
      </div>
    );
  }
}

/**
 * Wrapper that provides location context to the error boundary class component.
 * Functional components can't be error boundaries, so we hook the location
 * from react-router and pass it as a prop.
 */
export function RouteErrorBoundary({ children }: Props) {
  const location = useLocation();
  return (
    <RouteErrorBoundaryInner location={location}>
      {children}
    </RouteErrorBoundaryInner>
  );
}
