// @vitest-environment jsdom
/**
 * FB-023 — the platform top-up destination list is a list, not a wall of cards.
 *
 * REPRODUCED ON A REAL DEVICE (founder beta, 2026-08-09). "Balansni to'ldirish"
 * rendered each receiving card as a full-bleed ID-1 bank-card mockup — a
 * gradient tile at 1.586:1, roughly 220 px tall on a 360 px phone. Two of them
 * filled the entire sheet and the second was cut off behind the sticky footer,
 * so the owner scrolled past decoration to reach a number they were going to
 * copy anyway.
 *
 * The structural assertions below are deliberately about the *treatment*, not
 * just the output: the giant-card classes are named, so re-introducing
 * `BankCard` on this screen fails here rather than in somebody's screenshot.
 */
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, within, act } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from '@/i18n';
import { DestinationCardRow } from '@/components/ui/DestinationCardRow';
import type { PayoutCard } from '@/components/ui/CardManager';

const UZCARD: PayoutCard = {
  id: 'c-uz', number: '5614000000002802', bank: 'Kapital Bank',
  scheme: 'uzcard', network: 'UZCARD',
};

const writeText = vi.fn(async () => undefined);

beforeEach(async () => {
  await i18n.changeLanguage('uz');
  writeText.mockClear();
  Object.assign(navigator, { clipboard: { writeText } });
});
afterEach(cleanup);

describe('the row itself', () => {
  it('shows the grouped full number — a masked one cannot be typed into a bank', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    expect(screen.getByText('5614 6822 0493 2802')).toBeTruthy();
  });

  it('names the network and the settlement currency', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    const row = screen.getByTestId('topup-card-row');
    expect(within(row).getByText(/UZCARD/)).toBeTruthy();
    expect(within(row).getByText(/UZS/)).toBeTruthy();
  });

  it('copies the digits only, and says so in the active language', async () => {
    render(<DestinationCardRow card={{ ...UZCARD, number: '5614 6822 0493 2802' }} currency="UZS" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /nusxalash/i })); });
    expect(writeText).toHaveBeenCalledWith('5614000000002802');
    expect(await screen.findAllByText(/Nusxalandi/)).not.toHaveLength(0);
  });

  it('announces the copy to a screen reader, not only to the eye', async () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /nusxalash/i })); });
    expect(screen.getByRole('status').textContent).toMatch(/Nusxalandi/);
  });

  it('keeps a 44 px copy target — this is a coarse-pointer screen', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    const copy = screen.getByRole('button', { name: /nusxalash/i });
    expect(copy.className).toMatch(/\bh-11\b/);
    expect(copy.className).toMatch(/\bmin-w-11\b/);
  });

  it('is a radio when selectable, so the choice is keyboard-reachable', () => {
    const onSelect = vi.fn();
    render(<DestinationCardRow card={UZCARD} currency="UZS" selected onSelect={onSelect} />);
    const radio = screen.getByRole('radio');
    expect(radio.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(radio);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('never nests the copy control inside the selection control', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" selected onSelect={vi.fn()} />);
    const radio = screen.getByRole('radio');
    // Nested interactive elements are invalid AND make copy unreachable on the
    // one screen where the number has to be copied.
    expect(radio.querySelector('button')).toBeNull();
  });

  it('renders in Russian too', async () => {
    await i18n.changeLanguage('ru');
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    expect(screen.getByRole('button', { name: /скопировать|копировать/i })).toBeTruthy();
    await i18n.changeLanguage('uz');
  });
});

describe('the giant decorative card cannot come back', () => {
  const src = (p: string) => readFileSync(resolve(__dirname, p), 'utf8');

  it('the row carries no ID-1 aspect ratio and no gradient card face', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    const row = screen.getByTestId('topup-card-row');
    const classes = [row, ...Array.from(row.querySelectorAll('*'))]
      .map((el) => (el as HTMLElement).className || '').join(' ');
    expect(classes).not.toMatch(/aspect-\[1\.586/);
    expect(classes).not.toMatch(/text-card-number/);
    expect(classes).not.toMatch(/shadow-card/);
  });

  it('the only coloured surface is a fixed-size scheme chip', () => {
    render(<DestinationCardRow card={UZCARD} currency="UZS" />);
    const chip = screen.getByTestId('topup-card-row').querySelector('[style*="linear-gradient"]');
    expect(chip).not.toBeNull();
    // Bounded, not proportional: a gradient that scales with the row is a card.
    expect((chip as HTMLElement).className).toMatch(/\bh-9\b/);
    expect((chip as HTMLElement).className).toMatch(/\bw-11\b/);
  });

  it('the top-up modal renders rows, and does not reach for BankCard', () => {
    const modal = src('../../routes/tenant/_modals/TopUpModal.tsx');
    expect(modal).not.toMatch(/BankCard/);
    expect(modal).toMatch(/DestinationCardRow/);
  });

  it('the rows sit in a radiogroup, so one destination is chosen not many', () => {
    const modal = src('../../routes/tenant/_modals/TopUpModal.tsx');
    expect(modal).toMatch(/role="radiogroup"/);
  });

  it('nothing on this path can overflow horizontally at 320 px', () => {
    // Every text node in the row is `truncate`d and the flex children are
    // `min-w-0` / `shrink-0`, which is what keeps a 19-character PAN from
    // widening the sheet on the narrowest phone we support.
    render(<DestinationCardRow card={UZCARD} currency="UZS" selected onSelect={vi.fn()} />);
    const row = screen.getByTestId('topup-card-row');
    const number = screen.getByText('5614 6822 0493 2802');
    expect(number.className).toMatch(/\btruncate\b/);
    expect((number.parentElement as HTMLElement).className).toMatch(/\bmin-w-0\b/);
    expect(row.querySelector('.shrink-0')).not.toBeNull();
  });
});
