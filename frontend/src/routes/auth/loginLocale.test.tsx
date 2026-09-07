// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

// (MUDD-71 H3) These components read the support handle through
// `useSupportUsername`, which resolves it from platform settings via TanStack
// Query — the handle is operator-configurable, so a build-time constant could
// not be the source. That makes a QueryClientProvider part of the environment
// they need, exactly as it is in the real tree (`main.tsx`). `retry: false` so a
// test never waits on a retry schedule for a request nothing here asserts on.
function withQuery(ui: ReactNode): ReactNode {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>
  );
}

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import i18n from '@/i18n';
import LoginPage from './LoginPage';

afterEach(() => cleanup());

describe.each(['uz', 'ru'] as const)('browser login navigation in %s', (locale) => {
  it('returns every home link to the active locale', async () => {
    await i18n.changeLanguage(locale);
    render(withQuery(<MemoryRouter initialEntries={['/auth/login']}><LoginPage /></MemoryRouter>));

    const homes = [...document.querySelectorAll<HTMLAnchorElement>(`a[href="/${locale}"]`)];
    expect(homes).toHaveLength(3);
    const other = locale === 'uz' ? 'ru' : 'uz';
    expect(document.querySelector(`a[href="/${other}"]`)).toBeNull();
  });
});
