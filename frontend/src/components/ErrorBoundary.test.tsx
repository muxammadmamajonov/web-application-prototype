// @vitest-environment jsdom
/** (#174) The top-level error layer: a render throw below the boundary shows the
 *  fallback instead of white-screening the SPA. */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import { ErrorBoundary } from './ErrorBoundary';

afterEach(cleanup);

function Boom(): never {
  throw new Error('kaboom');
}

describe('ErrorBoundary (#174)', () => {
  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <div>safe content</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('safe content')).toBeInTheDocument();
  });

  it('renders the fallback (not a white screen) when a child throws', () => {
    // React logs caught render errors to console.error — silence it for a clean run.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/Nimadir.*ketdi/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Qayta yuklash/i })).toBeInTheDocument();
    spy.mockRestore();
  });
});
