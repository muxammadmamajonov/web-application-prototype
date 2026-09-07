// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { EmptyState } from './EmptyState';

afterEach(cleanup);

describe('EmptyState', () => {
  it('renders the title as a heading', () => {
    render(<EmptyState title="Hech narsa yoʻq" />);
    expect(screen.getByRole('heading', { name: /Hech narsa/ })).toBeInTheDocument();
  });

  it('renders body + action when provided', () => {
    render(<EmptyState title="Boʻsh" body="Hali maʼlumot yoʻq" action={<button>Qoʻshish</button>} />);
    expect(screen.getByText('Hali maʼlumot yoʻq')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Qoʻshish/ })).toBeInTheDocument();
  });

  it('omits body + action when not provided', () => {
    const { container } = render(<EmptyState title="Faqat sarlavha" />);
    expect(container.querySelector('p')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
  });

  it('tints the icon wrapper with the accent tone', () => {
    const { container } = render(<EmptyState title="X" tone="accent" icon={<svg data-testid="ic" />} />);
    expect(container.querySelector('.text-accent')).toBeTruthy();
  });
});
