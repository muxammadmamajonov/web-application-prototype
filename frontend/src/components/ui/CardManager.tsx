import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, ChevronRight, Copy, CreditCard, Loader2, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Modal, Field, inputCls } from '@/components/ui/Modal';
import { EmptyState } from '@/components/ui/EmptyState';
import { Select } from '@/components/ui/Select';
import { cn } from '@/lib/cn';
import { luhn, mask, groups, onlyDigits } from '@/lib/card';
import { CARD_NETWORKS, NETWORK_CURRENCY, SCHEME_STYLES, type CardNetwork } from '@/lib/cardNetworks';

export interface PayoutCard {
  id: string;
  number: string;          // digits only, full PAN (receiving card, shown to payers)
  holder?: string;
  scheme?: string;         // uzcard | humo | visa | mastercard | unionpay | ...
  bank?: string;           // issuing bank name (Aloqabank, TBC, …)
  /**
   * The DECLARED payment network — the only field a settlement currency may be
   * derived from. `scheme` above is BIN-detected and cannot decide it: Uzcard's
   * 5614 range sits inside Mastercard's 51-55. A card with no `network` is
   * withheld from every top-up screen.
   */
  network?: CardNetwork;
  /** Server-derived from `network`; never chosen by hand. */
  settlement_currency?: string;
}

const rid = () => Math.random().toString(36).slice(2, 10);

/** Drop cards with a duplicate full PAN (keep the first). (#45) */
const dedupeCards = (list: PayoutCard[]): PayoutCard[] => {
  const seen = new Set<string>();
  return list.filter((c) => {
    const key = onlyDigits(c.number);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

// Local prefix detection — used only as an offline fallback for the scheme.
function localScheme(digits: string): string | null {
  if (digits.startsWith('8600') || digits.startsWith('5614')) return 'uzcard';
  if (digits.startsWith('9860')) return 'humo';
  if (digits.startsWith('62') || digits.startsWith('81')) return 'unionpay';
  if (/^220[0-4]/.test(digits)) return 'mir';
  if (/^(5018|5020|5038|5893|6304|6759|676[0-3])/.test(digits)) return 'maestro';
  if (digits.startsWith('4')) return 'visa';
  if (/^5[1-5]/.test(digits)) return 'mastercard';
  return null;
}

// binlist returns scheme names like "humocard"/"uzcard"/"unionpay" — normalise
// to the keys our card designs use.
function normScheme(s?: string): string | undefined {
  if (!s) return undefined;
  const v = s.toLowerCase();
  if (v.startsWith('humo')) return 'humo';
  if (v.startsWith('uzcard')) return 'uzcard';
  if (v.includes('union')) return 'unionpay';
  if (v.includes('maestro')) return 'maestro';
  if (v.startsWith('mir')) return 'mir';
  if (v.includes('visa')) return 'visa';
  if (v.includes('master')) return 'mastercard';
  return v;
}

// Cache BIN lookups by 8-digit prefix so retyping a card doesn't re-hit binlist.
const binCache = new Map<string, { scheme?: string; bank?: string }>();

/** Scheme + issuing-bank lookup via binlist. binlist DOES cover the Uzbek
 *  schemes (UZCARD → e.g. "Nbu", HUMO → e.g. "Ipoteka Bank"), so we always
 *  query it; local prefix detection is only an offline scheme fallback. We
 *  never invent a bank name — if binlist has none, bank stays empty. */
async function lookupBin(digits: string): Promise<{ scheme?: string; bank?: string }> {
  const bin = digits.slice(0, 8);
  const cached = binCache.get(bin);
  if (cached) return cached;
  const local = localScheme(digits);
  try {
    const res = await fetch(`https://lookup.binlist.net/${bin}`, {
      headers: { 'Accept-Version': '3' },
    });
    if (!res.ok) return { scheme: local ?? undefined };
    const j = (await res.json()) as { scheme?: string; bank?: { name?: string } };
    const out = { scheme: normScheme(j.scheme) || local || undefined, bank: j.bank?.name };
    binCache.set(bin, out);
    return out;
  } catch {
    return { scheme: local ?? undefined };
  }
}

// (EMV chip graphic removed per design — the receiving-card preview shows
//  bank · number · holder · scheme logo, nothing else.)

/** A realistic bank-card tile at true ID-1 proportions (85.6×54mm ≈ 1.586:1).
 *  Tap anywhere to copy the card number. */
/**
 * A receiving card rendered as a real bank-card tile.
 *
 * When `onSelect` is supplied the tile itself becomes the selection control and
 * copy moves to its own named button. The tile must never be a `<button>` that a
 * caller then wraps in another one: nested interactive elements are invalid and
 * make copy unreachable exactly where an owner needs it — while transferring
 * money to that card.
 */
const last4 = (n: string) => (n || '').replace(/\D/g, '').slice(-4);

export function BankCard({ card, onDelete, onSelect, selected }: {
  card: PayoutCard; onDelete?: () => void; onSelect?: () => void; selected?: boolean;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const s = SCHEME_STYLES[card.scheme ?? ''] ?? SCHEME_STYLES.default;
  const dark = !s.light;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(card.number);
      setCopied(true); setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked — no-op */ }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onSelect ?? copy}
        aria-pressed={onSelect ? !!selected : undefined}
        aria-label={onSelect
          ? t('cards.selectCard', { bank: card.bank || '', last4: last4(card.number), defaultValue: 'Kartani tanlash: {{bank}} ••{{last4}}' })
          : t('cards.copyCard', { bank: card.bank || '', last4: last4(card.number), defaultValue: 'Karta raqamini nusxalash: {{bank}} ••{{last4}}' })}
        style={{ background: s.bg }}
        className={cn(
          'block w-full overflow-hidden rounded-card text-left shadow-card transition active:scale-[0.99]',
          'aspect-[1.586/1] p-[5.5%]',
          dark ? 'text-white' : 'text-ink',
          onSelect && selected && 'ring-2 ring-ink',
        )}
      >
        <div className="flex h-full flex-col justify-between">
          {/* Top: bank name (left) + scheme logo (right) */}
          <div className="flex items-start justify-between gap-2">
            <span className={cn('truncate text-small font-semibold uppercase tracking-wide sm:text-body',
                                 dark ? 'text-white/90' : 'text-ink/80')}>
              {card.bank || ' '}
            </span>
            {s.logo
              ? <img src={s.logo} alt={card.scheme ?? ''} style={{ maxHeight: Math.min(s.logoH, 26) }}
                     className="h-auto w-auto max-w-[64px] shrink-0 object-contain drop-shadow-sm" draggable={false} />
              : null}
          </div>

          {/* Middle: card number — whitespace-nowrap + a tighter clamp so the
              last group never wraps to a 2nd line on narrow phones (#204). */}
          <div className="font-mono text-card-number tabular-nums tracking-[0.08em] whitespace-nowrap">
            {groups(card.number) || '•••• •••• •••• ••••'}
          </div>

          {/* Bottom: holder name */}
          <span className={cn('block min-w-0 truncate text-micro uppercase tracking-wide sm:text-caption',
                               dark ? 'text-white/75' : 'text-ink/65')}>
            {card.holder || (copied
              ? t('common.copied', 'Nusxalandi')
              : onSelect
                ? t('cards.tapSelect', 'Tanlash uchun bosing')
                : t('cards.tapCopy', 'Nusxalash uchun bosing'))}
          </span>
        </div>
      </button>

      {copied && (
        <span className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-pill bg-black/55 px-2.5 py-1 text-micro font-medium text-white backdrop-blur-sm">
          <Check size={12} className="mr-1 inline" />{t('common.copied', 'Nusxalandi')}
        </span>
      )}
      {onSelect && (
        // A real, localized accessible name — never a `title` tooltip, which
        // never appears on the phone this runs on — and a coarse-pointer target.
        <button type="button" onClick={() => void copy()}
                aria-label={t('cards.copyCard', { bank: card.bank || '', last4: last4(card.number), defaultValue: 'Karta raqamini nusxalash: {{bank}} ••{{last4}}' })}
                className="tap-target absolute bottom-2.5 right-2.5 inline-flex h-11 items-center gap-1.5 rounded-icon bg-black/30 px-3 text-caption font-medium text-white backdrop-blur-sm hover:bg-black/50">
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? t('common.copied', 'Nusxalandi') : t('common.copy', 'Nusxa olish')}
        </button>
      )}
      {onDelete && (
        <button type="button" onClick={onDelete}
                aria-label={t('cards.deleteCard', { last4: last4(card.number), defaultValue: 'Kartani o‘chirish: ••{{last4}}' })}
                className="tap-target absolute right-2.5 top-2.5 grid h-11 w-11 place-items-center rounded-icon bg-black/25 text-white backdrop-blur-sm hover:bg-black/45">
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

/** Per-card resolution state, computed by the server from the same
 *  matrix that filters the top-up screen. Present only where the caller has it
 *  (platform receiving-card settings). */
export interface CardValidation {
  id: string; last4: string; resolved: boolean;
  network: string | null; settlement_currency: string | null;
  source: string | null; reason: string | null;
}

interface Props {
  cards: PayoutCard[];
  onChange: (cards: PayoutCard[]) => void;
  title?: string;
  validation?: CardValidation[];
}

/**
 * Receiving-card manager: the saved-card list, and one Add Card action (#8).
 *
 * The add form used to sit inline under the list, permanently open — a live
 * card preview, two inputs and a full-width button that every reader scrolled
 * past to reach the cards they came for, on a screen where adding a card is a
 * rare act and reading one is the common one. It now lives behind a modal, so
 * the resting state of this component is exactly the list plus one compact
 * action, and the same component satisfies Business Settings (#14), which asked
 * for the identical treatment. One component, so the two cannot diverge.
 *
 * Scheme is auto-detected; bank is auto-filled when binlist knows it. No
 * expiry — these are payout cards, and we never ask for what we cannot use.
 */
export function CardManager({ cards, onChange, title, validation }: Props) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [num, setNum] = useState('');
  const [holder, setHolder] = useState('');
  const [bank, setBank] = useState<string | undefined>();   // auto from binlist only
  const [scheme, setScheme] = useState<string | undefined>();
  // Declared, never detected, and required: a card whose network is a guess
  // is withheld from every top-up screen.
  const [network, setNetwork] = useState<CardNetwork | ''>('');
  const [looking, setLooking] = useState(false);
  const [detail, setDetail] = useState<PayoutCard | null>(null);

  const digits = onlyDigits(num);
  // Reject a card whose full PAN is already saved (no duplicate cards).
  const isDup = digits.length >= 16 && cards.some((c) => onlyDigits(c.number) === digits);
  const panOk = digits.length >= 16 && digits.length <= 19;
  const luhnOk = luhn(digits);                 // (#204) reject mistyped/invalid PANs
  const holderOk = !!holder.trim();
  // (#204) Ask for ONLY the number + holder; bank + scheme are auto-detected.
  // Reject the card if it fails the Luhn check.
  const canAdd = panOk && luhnOk && !isDup && holderOk && !!network;

  const onNum = async (v: string) => {
    const d = onlyDigits(v).slice(0, 19);
    setNum(groups(d));
    if (d.length >= 8) {
      setLooking(true);
      const info = await lookupBin(d);          // scheme + issuing bank, both auto
      setScheme(info.scheme);
      setBank(info.bank);
      setLooking(false);
    } else if (d.length >= 1) {
      setScheme(localScheme(d) ?? undefined);   // instant scheme from prefix; bank waits for BIN
      setBank(undefined);
    } else {
      setScheme(undefined); setBank(undefined);
    }
  };

  const reset = () => {
    setNum(''); setHolder(''); setBank(undefined); setScheme(undefined); setNetwork('');
  };

  const closeAdd = () => { setAdding(false); reset(); };

  const add = () => {
    if (!canAdd) return;
    onChange([...cards, {
      id: rid(), number: digits,
      holder: holder.trim() || undefined,
      bank: bank || undefined,
      scheme,
      // The settlement currency is shown from NETWORK_CURRENCY but not sent:
      // the server derives and stores it, so there is one authority.
      network: network as CardNetwork,
    }]);
    closeAdd();
  };

  const remove = (id: string) => onChange(cards.filter((c) => c.id !== id));

  // #45: clean out any duplicate cards (same PAN) saved before dedup existed, so
  // the list never shows a card twice. Converges (no-op once deduped).
  useEffect(() => {
    const d = dedupeCards(cards);
    if (d.length !== cards.length) onChange(d);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cards]);
  const shown = dedupeCards(cards);

  const addAction = (
    // (#7) Compact icon+label, never a full-width block: adding a card is the
    // rare act on this screen, so it sits beside the title rather than under
    // the list taking a row of its own.
    <Button variant="outline" size="sm" type="button" onClick={() => setAdding(true)}>
      <Plus size={14} /> {t('cards.add', "Karta qo'shish")}
    </Button>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        {title
          ? <div className="text-micro uppercase tracking-wider text-muted">{title}</div>
          : <span />}
        {shown.length > 0 && addAction}
      </div>

      {/* Empty is a real state, not an absent list: without it the section would
          render as a bare heading and the only way to add a card would be a
          button that isn't there yet. */}
      {shown.length === 0 && (
        <EmptyState
          icon={<CreditCard size={20} />}
          title={t('cards.emptyTitle', 'Saqlangan karta yo‘q')}
          body={t('cards.emptyBody', 'To‘lovlarni qabul qilish uchun karta qo‘shing')}
          action={addAction}
          size="compact"
        />
      )}

      {/* (#204) Compact masked list (9860****4455 + holder) — tap a row for the
          full detail. Replaces the wall of full-size card tiles. */}
      {shown.length > 0 && (
        <ul className="space-y-2">
          {shown.map((c) => {
            const s = SCHEME_STYLES[c.scheme ?? ''] ?? SCHEME_STYLES.default;
            const v = validation?.find((x) => x.id === c.id);
            return (
              <li key={c.id} className="flex items-center gap-3 rounded-control border border-line bg-surface p-2.5">
                <button type="button" onClick={() => setDetail(c)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                  <span className="grid h-9 w-12 shrink-0 place-items-center overflow-hidden rounded-icon" style={{ background: s.bg }}>
                    {s.logo
                      ? <img src={s.logo} alt={c.scheme ?? ''} className="max-h-4 max-w-[40px] object-contain" draggable={false} />
                      : <CreditCard size={16} className="text-white/80" />}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-body tabular-nums tracking-wide">{mask(onlyDigits(c.number))}</span>
                    <span className="block truncate text-caption text-muted">
                      {[v?.network ?? c.network, v?.settlement_currency, c.holder || c.bank].filter(Boolean).join(' · ')
                        || t('cards.tapDetail', 'Tafsilotlar')}
                    </span>
                    {/* A card the server cannot resolve is withheld from
                        every top-up screen. Said here, on the row, rather than
                        left to be discovered as an empty screen elsewhere. */}
                    {v && !v.resolved && (
                      <span role="alert" className="mt-0.5 flex items-center gap-1 text-micro font-medium text-danger">
                        <AlertTriangle size={12} className="shrink-0" />
                        {t('cards.unresolved', "Tarmoq aniqlanmagan — bu karta to'ldirish ekranida ko'rinmaydi")}
                      </span>
                    )}
                  </span>
                  <ChevronRight size={16} className="ml-auto shrink-0 text-muted" />
                </button>
                <button type="button" onClick={() => remove(c.id)}
                        aria-label={t('cards.deleteCard', { last4: last4(c.number), defaultValue: 'Kartani o‘chirish: ••{{last4}}' })}
                        className="tap-target grid h-11 w-11 shrink-0 place-items-center rounded-icon text-danger hover:bg-[color:var(--c-danger-soft)]">
                  <Trash2 size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {/* Card detail — the full card tile (copyable) + the data we collect.
          No expiry / linked phone (never collected). */}
      {detail && (
        <Modal open onClose={() => setDetail(null)} size="sm" title={t('cards.detailTitle', 'Karta tafsilotlari')}>
          <div className="space-y-4">
            <BankCard card={detail} />
            <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-body">
              <dt className="text-muted">{t('cards.holder', 'Karta egasi')}</dt>
              <dd className="col-span-2 font-medium">{detail.holder || '—'}</dd>
              <dt className="text-muted">{t('cards.bank', 'Bank')}</dt>
              <dd className="col-span-2 font-medium">{detail.bank || '—'}</dd>
              <dt className="text-muted">{t('cards.network', 'Tarmoq')}</dt>
              <dd className="col-span-2 font-medium uppercase">{detail.scheme || '—'}</dd>
            </dl>
          </div>
        </Modal>
      )}

      {/* Add a card — the same live preview and fields as before, now behind an
          explicit action instead of always open (#8, #14). */}
      <Modal
        open={adding}
        onClose={closeAdd}
        size="sm"
        title={t('cards.addTitle', "Karta qo'shish")}
        footer={
          <>
            <Button variant="ghost" size="md" type="button" onClick={closeAdd}>
              {t('common.cancel', 'Bekor qilish')}
            </Button>
            <Button variant="primary" size="md" type="button" disabled={!canAdd} onClick={add}>
              <Plus size={14} /> {t('cards.add', "Karta qo'shish")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="mx-auto max-w-[340px]">
            <BankCard card={{ id: 'preview', number: digits, holder, bank, scheme }} />
          </div>
          <Field label={t('cards.number', 'Karta raqami')} required>
            <div className="relative">
              <input value={num} onChange={(e) => onNum(e.target.value)}
                     className={inputCls} inputMode="numeric" autoComplete="off"
                     placeholder="0000 0000 0000 0000" autoFocus />
              {looking && <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-muted" />}
            </div>
          </Field>
          <Field label={t('cards.holder', 'Karta egasi (F.I.Sh)')} required>
            <input value={holder} onChange={(e) => setHolder(e.target.value.toUpperCase())}
                   className={inputCls} placeholder="ALISHER VALIYEV" autoComplete="off" />
          </Field>
          {/* The one field that cannot be detected. The prefix says
              nothing usable — 5614 is Uzcard AND inside Mastercard's range — so
              the person adding the card states it, and the settlement currency
              follows from it. */}
          <Field label={t('cards.networkLabel', "To'lov tarmog'i")} required
                 hint={network ? t('cards.settlesIn', { currency: NETWORK_CURRENCY[network], defaultValue: '{{currency}} qabul qiladi' }) : undefined}>
            <Select
              value={network}
              onChange={(v) => setNetwork(v as CardNetwork)}
              placeholder={t('cards.networkPick', 'Tarmoqni tanlang')}
              options={CARD_NETWORKS.map((n) => ({
                value: n, label: n,
                hint: t('cards.settlesIn', { currency: NETWORK_CURRENCY[n], defaultValue: '{{currency}} qabul qiladi' }),
              }))}
            />
          </Field>
          {/* Bank + network are auto-detected (binlist / local prefix) — shown
              read-only while typing, never asked for (#204). */}
          {(bank || scheme) && (
            <p className="text-caption text-muted">
              {t('cards.detected', 'Aniqlandi')}: <span className="font-medium text-ink">{[bank, scheme?.toUpperCase()].filter(Boolean).join(' · ')}</span>
            </p>
          )}
          {/* Every rejection is named inline, before submit — a disabled button
              with no stated reason is the failure mode this replaces. */}
          {isDup && (
            <p className="text-caption font-medium text-danger">
              {t('cards.duplicate', 'Bu karta allaqachon qo‘shilgan')}
            </p>
          )}
          {panOk && !luhnOk && (
            <p className="text-caption font-medium text-danger">{t('cards.invalid', "Karta raqami noto'g'ri")}</p>
          )}
          {panOk && luhnOk && !isDup && !holderOk && (
            <p className="text-caption text-muted">{t('cards.fillHolder', "Karta egasining F.I.Sh ni kiriting")}</p>
          )}
          {panOk && luhnOk && !isDup && holderOk && !network && (
            <p className="text-caption text-muted">
              {t('cards.fillNetwork', "To'lov tarmog'ini tanlang — usiz karta to'ldirish ekranida ko'rinmaydi")}
            </p>
          )}
        </div>
      </Modal>
    </div>
  );
}
