import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useSupportUsername } from '@/hooks/useSupportUsername';
import { ArrowUpRight, FileText, Sparkles, Wallet } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { MobileHeader } from '@/components/mobile/primitives';
import { Button } from '@/components/ui/Button';
import { buttonClasses } from '@/components/ui/buttonClasses';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';
import { ErrorCard } from '@/components/ui/ErrorCard';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonList } from '@/components/ui/Skeleton';
import { money, shortDate } from '@/lib/format';
import { DEFAULT_CURRENCY } from '@/lib/currency';
import {
  changeTenantPlan, downloadInvoicePdf, usePublicPlans,
  useTenantBilling, useTenantInvoices, useTenantTopupInfo, useTenantTopups,
  type PublicPlan,
} from '@/lib/queries';
import { queryKeys } from '@/lib/queryKeys';
import { TopUpModal } from './_modals/TopUpModal';
import { V2_SURFACES_ENABLED } from '@/routes/_scope';

/* The plan name comes from the live catalogue (`usePublicPlans` below), never a
   literal map. This used to be `{ trial, lite, pro, custom }` — the fixed enum
   plan codes carried before R36 made them owner-chosen slugs. The catalogue has
   since moved to starter/business/pro/enterprise/flex, so for a shop on
   `business` the lookup missed and the page printed the raw lowercase code back
   at the owner as if it were the plan's name. */

/** The server caps `GET /tenant/billing/topups` at 50 rows and returns no total,
 *  so the page can only say "there may be older ones" by recognising a full page.
 *  Kept in sync by name with the backend's hard `.limit(50)`. */
const TOPUP_PAGE_SIZE = 50;

/** `shortDate` renders day–month with no year, which is ambiguous in a top-up
 *  history that carries no date bound and can span years. Append the year only
 *  when it is not the current one, so the ordinary case stays as compact as the
 *  invoice rows below it. */
function topupDate(iso: string): string {
  const base = shortDate(iso);
  const d = new Date(iso);
  if (base === '—' || Number.isNaN(d.getTime())) return base;
  const year = d.getFullYear();
  return year === new Date().getFullYear() ? base : `${base} ${year}`;
}

export default function BillingPage() {
  const { t, i18n } = useTranslation();
  const support = useSupportUsername();
  const { locale } = useParams();
  const lng = (locale ?? i18n.language ?? 'uz') as 'uz' | 'ru';
  const planName = (p: { name_uz: string; name_ru?: string | null }) => (lng === 'ru' && p.name_ru) ? p.name_ru : p.name_uz;
  /** Display name for a stored plan CODE, resolved against the live catalogue.
   *  Falls back to the code itself only when the catalogue genuinely has no such
   *  plan — which is information the owner should see, not something to hide. */
  const planNameForCode = (code: string | null | undefined) => {
    const p = (plans.data ?? []).find((x) => x.code === code);
    return p ? planName(p) : (code || '—');
  };
  const qc = useQueryClient();
  const billing = useTenantBilling();
  const invoices = useTenantInvoices();
  const plans = usePublicPlans();
  const topupInfo = useTenantTopupInfo();
  // Every figure on this page is PLATFORM money — wallet balance, top-ups,
  // plan prices, overage, invoices — so it takes the platform currency the
  // server states, never `useCurrency()` (the shop's).
  const currency = topupInfo.data?.currency ?? DEFAULT_CURRENCY;
  // (#9) The platform's receiving cards. Empty while loading too, which is the
  // safe direction: the action stays unavailable until we know there is
  // somewhere to send the money, rather than flickering enabled and opening a
  // modal with an empty card list.
  const topupCards = topupInfo.data?.cards ?? [];
  // (#9) Authority for "may I top up" is the SERVER, not the client's own
  // session store. `activeRole()` returns null whenever the active tenant id
  // does not match a membership, which silently hid this control from real
  // owners — the reported bug. `can_topup` is decided by the same code that
  // enforces it on POST, so the two cannot disagree.
  const canTopUp = topupInfo.data?.can_topup ?? false;
  // Read-only status of the manual top-up lifecycle. Server-authoritative: this
  // query is the ONLY source of a row, and the balance above is never adjusted
  // client-side by a pending one.
  const topups = useTenantTopups();
  const topupRows = topups.data ?? [];

  // Same mapping the top-up modal uses: `card` is translated; Payme and Click are
  // brand names and stay literal in both bundles.
  const methodLabel = (m: string) =>
    m === 'card' ? t('topup.card', 'Karta o‘tkazmasi') : m === 'payme' ? 'Payme' : m === 'click' ? 'Click' : m;
  const methodText = (r: { method: string; dest_card_label?: string | null }) =>
    r.dest_card_label ? `${methodLabel(r.method)} · ${r.dest_card_label}` : methodLabel(r.method);

  const [picking, setPicking] = useState<PublicPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [dlId, setDlId] = useState<string | null>(null);
  const [planErr, setPlanErr] = useState<string | null>(null);
  const [topupOpen, setTopupOpen] = useState(false);

  const b = billing.data;
  const selectable = (plans.data ?? []).filter((p) => p.code !== 'trial');

  const confirmChange = async () => {
    if (!picking || picking.code === 'trial') return;
    setBusy(true); setPlanErr(null);
    try {
      await changeTenantPlan(picking.code);
      await qc.invalidateQueries({ queryKey: queryKeys.tenant.billing() });
      await qc.invalidateQueries({ queryKey: queryKeys.me.root() });
      setPicking(null);
    } catch (ex) {
      const e = ex as { response?: { status?: number; data?: { detail?: { error?: string; balance?: number; required?: number } | string } } };
      const detail = e.response?.data?.detail;
      if (e.response?.status === 402 && typeof detail === 'object' && detail?.error === 'insufficient_balance') {
        setPlanErr(t('billing.insufficient', {
          balance: money(detail.balance ?? 0), required: money(detail.required ?? 0),
          defaultValue: `Balans yetarli emas. Hozir: ${money(detail.balance ?? 0)} so'm, kerak: ${money(detail.required ?? 0)} so'm. Balansni to'ldirish uchun @MuddatoSupport bilan bog'laning.`,
        }));
      } else {
        setPlanErr(typeof detail === 'string' ? detail : t('common.errorLoading'));
      }
    } finally {
      setBusy(false);
    }
  };

  const download = async (id: string) => {
    setDlId(id);
    try { await downloadInvoicePdf(id); }
    finally { setDlId(null); }
  };

  return (
    <div>
      <div className="hidden md:block">
        <PageHeader
          title={t('billing.title', 'Tarif va to’lovlar')}
          subtitle={t('billing.subtitle', 'Joriy tarifingiz, foydalanish va hisob-fakturalar tarixi.')}
        />
      </div>
      <div className="md:hidden">
        <MobileHeader title={t('billing.title', 'Tarif va to’lovlar')} />
      </div>

      {/* (#403) Gate the plan/usage + balance summary behind billing/topup
          load+error so it never renders "Trial / 0 so'm" from an errored or
          in-flight query. */}
      {billing.isError || topupInfo.isError ? (
        <ErrorCard error={billing.error ?? topupInfo.error}
                   onRetry={() => { void billing.refetch(); void topupInfo.refetch(); }} />
      ) : billing.isLoading || topupInfo.isLoading ? (
        <section className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => <div key={i} className="card h-28 animate-pulse bg-line/30" />)}
        </section>
      ) : (<>
      {/* current plan + usage */}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="card p-5">
          <div className="kicker mb-2">{t('billing.currentPlan', 'Joriy tarif')}</div>
          <div className="flex items-center gap-2">
            <span className="text-title font-semibold tracking-tight">
              {planNameForCode(b?.plan_code)}
            </span>
            {b && <Badge tone={b.status === 'active' ? 'success' : 'warn'}>{t(`status.${b.status}`, b.status)}</Badge>}
          </div>
          {b?.trial_ends_at && (
            <p className="mt-2 text-caption text-muted">
              {t('billing.trialEnds', 'Sinov tugaydi')}: {shortDate(b.trial_ends_at)}
            </p>
          )}
        </div>

        <div className="card p-5">
          <div className="kicker mb-2">{t('billing.activeCredits', 'Faol bo’lib-to’lovlar')}</div>
          <div className="text-title font-semibold tracking-tight">{b?.active_installments ?? '—'}</div>
          {/* (#464) Encouraging zero-state hint for new tenants — distinguishes a
              real "0 credits" from the "—" reserved for a truly-absent value. */}
          {b?.active_installments === 0 && (
            <p className="mt-1 text-caption text-muted">{t('billing.noActiveCredits', 'Hali faol bo’lib-to’lov yo’q')}</p>
          )}
        </div>

        <div className="card p-5">
          <div className="kicker mb-2">{t('billing.projected', 'Joriy oy uchun')}</div>
          <div className="text-title font-semibold tracking-tight">
            {b?.projected ? money(b.projected.amount) : '—'}
            {b?.projected && <span className="ml-1 text-body text-muted">{t('common.sum', 'so’m')}</span>}
          </div>
        </div>
      </section>

      {/* prepaid balance + top-up */}
      <section className="card mt-4 flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <Wallet className="text-accent" size={20} />
          <div>
            <div className="kicker">{t('billing.balance', 'Balans')}</div>
            <div className="text-title font-semibold tabular-nums tracking-tight">
              {money(topupInfo.data?.balance ?? 0)} <span className="text-body text-muted">{currency}</span>
            </div>
          </div>
        </div>
        {/* (#9) Gated on a destination existing, not on explaining a role.
            The "only the owner can top up" sentence is gone: it told a manager
            about a restriction they cannot act on, on a screen where the
            balance beside it is already read-only for them. A non-owner now
            simply has no top-up control — the backend still enforces the rule.

            For the owner, no receiving card means no top-up: the flow's second
            step is "transfer to this card", so offering the action would open
            a modal with nothing to transfer to. Disabled with the reason
            stated, rather than hidden, so it is clear the action exists and
            what unblocks it. */}
        {canTopUp && (
          topupCards.length > 0 ? (
            <Button onClick={() => setTopupOpen(true)}>
              <Wallet size={16} /> {t('topup.title', 'Balansni to‘ldirish')}
            </Button>
          ) : (
            <div className="text-right">
              <Button disabled title={t('billing.topupNoCards', 'Hozircha qabul qiluvchi karta yo‘q')}>
                <Wallet size={16} /> {t('topup.title', 'Balansni to‘ldirish')}
              </Button>
              <p className="mt-1 text-caption text-muted">
                {t('billing.topupNoCards', 'Hozircha qabul qiluvchi karta yo‘q')}
              </p>
            </div>
          )
        )}
      </section>
      </>)}

      {/* Top-up requests — read-only status of the manual top-up lifecycle, so a
          shop that transferred money can see it is awaiting review rather than
          lost. Deliberately OUTSIDE the billing/topup-info gate above: a history
          failure must not blank the balance, and a balance failure must not hide
          a pending request. Read for owner AND manager —
          the backend read has no role guard, and a manager already sees balance
          and invoices read-only; only the top-up ACTION stays owner-only. */}
      <section className="mt-4">
        <h3 className="text-subsection font-semibold">{t('billing.topupHistory', 'To‘ldirish so‘rovlari')}</h3>
        {/* States what happens next: without it "Kutilmoqda" reads as stuck. */}
        <p className="mb-3 mt-1 text-caption text-muted">
          {t('billing.topupHistoryHint', 'So‘rov operator tasdiqlagach balansga qo‘shiladi.')}
        </p>
        {/* Error before loading, so an in-flight query never renders as "empty". */}
        {topups.isError ? (
          <ErrorCard error={topups.error} onRetry={() => { void topups.refetch(); }} />
        ) : topups.isLoading ? (
          <SkeletonList rows={3} />
        ) : topupRows.length === 0 ? (
          <EmptyState
            size="compact"
            tone="muted"
            icon={<Wallet size={20} />}
            title={t('billing.noTopups', 'Hali to‘ldirish so‘rovi yo‘q')}
            body={t('billing.noTopupsBody', 'Balansni to‘ldirganingizda so‘rov va uning holati shu yerda ko‘rinadi.')}
          />
        ) : (
          <>
            {/* Desktop (md+): full table. */}
            <div className="card hidden overflow-x-auto p-0 md:block">
              <table className="w-full min-w-[480px] text-body">
                <thead>
                  <tr className="border-b border-line text-left text-micro uppercase tracking-wide text-muted">
                    <th scope="col" className="px-5 py-3">{t('billing.topupDate', 'Sana')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.amount', 'Summa')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.status', 'Holat')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.topupMethod', 'Usul')}</th>
                  </tr>
                </thead>
                <tbody>
                  {topupRows.map((r) => (
                    <tr key={r.id} className="border-b border-line/60 last:border-0">
                      <td className="px-5 py-3">{topupDate(r.created_at)}</td>
                      <td className="px-5 py-3 font-medium tabular-nums">{money(r.amount)} {currency}</td>
                      <td className="px-5 py-3"><StatusBadge status={r.status} /></td>
                      <td className="max-w-[220px] truncate px-5 py-3 text-muted">{methodText(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Mobile (<md): one stacked card per request — amount leads, because
                the owner is matching a transfer they made. */}
            <ul className="space-y-2.5 md:hidden">
              {topupRows.map((r) => (
                <li key={r.id} className="rounded-card border border-line bg-surface px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="tabular-nums text-body font-medium">
                      {money(r.amount)} {currency}
                    </div>
                    <StatusBadge status={r.status} />
                  </div>
                  <div className="mt-0.5 truncate text-caption text-muted">
                    {topupDate(r.created_at)} · {methodText(r)}
                  </div>
                </li>
              ))}
            </ul>
            {/* The server hard-caps at 50 and returns no total, so a full page is
                the only signal that older rows exist. Say so — never truncate silently. */}
            {topupRows.length === TOPUP_PAGE_SIZE && (
              <p className="mt-2 text-caption text-muted">
                {t('billing.topupsTruncated', 'Oxirgi 50 ta so‘rov ko‘rsatilmoqda.')}
              </p>
            )}
          </>
        )}
      </section>

      {/* Overage / Flex estimate breakdown — shown ONLY when there is something
          to break down. The old condition let it render with both middle terms
          resolving to null, so the card read "199 000 UZS = 199 000 UZS": a
          sum, an equals sign, and the same sum again. A breakdown that restates
          its input is noise at best and reads as a broken calculation at worst.
          It also covered the 0-contract case, where the extra line would have
          been "+ 0 × price". */}
      {b?.projected && b.projected.contract_overage_price != null
        && b.projected.active_count > (b.projected.included_contracts ?? 0) && (
        <section className="card mt-4 p-5">
          <div className="kicker mb-2">{t('billing.estimate', 'Joriy oy taxminiy hisobi')}</div>
          <div className="flex flex-wrap items-baseline gap-x-2 text-body text-ink-soft">
            <span className="font-mono">{money(b.base_price)} {currency}</span>
            {b.projected.contract_overage_price != null && (b.projected.included_contracts ?? 0) === 0 ? (
              <span>
                + {b.projected.active_count} × {money(b.projected.contract_overage_price)} {currency}
              </span>
            ) : b.projected.contract_overage_price != null && b.projected.active_count > (b.projected.included_contracts ?? 0) ? (
              <span>
                + {b.projected.active_count - (b.projected.included_contracts ?? 0)} {t('billing.overContracts', 'qoʻshimcha shartnoma')} × {money(b.projected.contract_overage_price)}
              </span>
            ) : null}
            <span className="font-semibold text-ink">= {money(b.projected.amount)} {currency}</span>
          </div>
        </section>
      )}

      {/* Upgrade recommendation — drives the deferred self-serve plan change. */}
      {V2_SURFACES_ENABLED && b?.suggestion && (
        <section className="card mt-4 flex flex-wrap items-center justify-between gap-3 border-accent/30 bg-[color:var(--c-accent-soft)] p-5">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 text-accent" size={18} />
            <div>
              <p className="font-semibold">{b.suggestion.message}</p>
              <p className="mt-0.5 text-small text-ink-soft">
                {t('billing.suggestCompare', 'Hozirgi taxmin: {{cur}} → {{plan}}: {{price}}', {
                  cur: `${money(b.suggestion.current_projected)} ${currency}`,
                  plan: b.suggestion.to_plan_name,
                  price: `${money(b.suggestion.plan_price)} ${currency}`,
                })}
              </p>
            </div>
          </div>
          <Button size="sm" onClick={() => setPicking(selectable.find((p) => p.code === b.suggestion!.to_plan) ?? null)}>
            {b.suggestion.to_plan_name} <ArrowUpRight size={14} />
          </Button>
        </section>
      )}

      {/* Self-serve plan change is deferred; a plan change is settled by
          contact, and the screen says so rather than offering a control that
          does not exist. */}
      {!V2_SURFACES_ENABLED && (
        <section className="card mt-6 p-5">
          <h3 className="text-subsection font-semibold">{t('billing.choosePlan', 'Tarifni o’zgartirish')}</h3>
          <p className="mt-1 text-small text-ink-soft">
            {t('billing.planChangeContact', 'Tarifni o‘zgartirish uchun @MuddatoSupport bilan bog‘laning — biz siz uchun sozlab beramiz.')}
          </p>
          {/* A link styled as a button — never a button inside a link. */}
          <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer"
             className={buttonClasses({ variant: 'outline', size: 'sm', className: 'mt-3' })}>
            {t('billing.contactSupport', 'Qo‘llab-quvvatlash bilan bog‘lanish')}
          </a>
        </section>
      )}
      {V2_SURFACES_ENABLED && (
      <section className="mt-6">
        <h3 className="mb-3 text-subsection font-semibold">{t('billing.choosePlan', 'Tarifni o’zgartirish')}</h3>
        {plans.isError ? (
          <ErrorCard error={plans.error} onRetry={() => { void plans.refetch(); }} />
        ) : plans.isLoading ? (
          <div className="grid gap-4 md:grid-cols-3">
            {[0, 1, 2].map((i) => <div key={i} className="card h-48 animate-pulse bg-line/30" />)}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-3">
            {selectable.map((p) => {
              const isCurrent = p.code === b?.plan_code;
              return (
                <div key={p.code} className="card flex flex-col p-5">
                  <div className="flex items-center justify-between">
                    <span className="text-section font-semibold">{planName(p)}</span>
                    {p.badge === 'most_popular' && <Badge tone="accent">{t('billing.popular', 'Mashhur')}</Badge>}
                  </div>
                  <div className="mt-2 text-title font-semibold tracking-tight">
                    {p.is_contact_sales
                      ? (lng === 'ru' ? 'Договорная' : 'Kelishiladi')
                      : p.code === 'custom' && p.price_per_installment != null
                        ? <>{money(p.price_per_installment)}<span className="text-body text-muted">/{t('billing.perCredit', 'bo’lib-to’lov')}</span></>
                        : <>{money(p.price_monthly)}<span className="text-body text-muted">/{t('billing.perMonth', 'oy')}</span></>}
                  </div>
                  {(() => { const d = lng === 'ru' ? (p.description_ru ?? p.description_uz) : p.description_uz; return d ? <p className="mt-2 text-small text-ink-soft">{d}</p> : null; })()}
                  <div className="mt-4 pt-2">
                    {p.is_contact_sales ? (
                      <a href={`https://t.me/${support}`} target="_blank" rel="noreferrer"
                         className={buttonClasses({ variant: 'primary', block: true })}>
                        {t('billing.contactSalesCta', 'Bog‘lanish')}
                      </a>
                    ) : (
                      <Button
                        block
                        variant={isCurrent ? 'soft' : 'primary'}
                        loading={busy}
                        onClick={() => setPicking(p)}
                      >
                        {isCurrent ? t('billing.current', 'Joriy tarif') : t('billing.select', 'Tanlash')}
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
      )}

      {/* invoice history */}
      <section className="mt-8">
        <h3 className="mb-3 text-subsection font-semibold">{t('billing.invoices', 'Hisob-fakturalar')}</h3>
        {invoices.isError ? (
          <div className="card p-0"><div className="px-5 py-10">
            <ErrorCard error={invoices.error} onRetry={() => { void invoices.refetch(); }} />
          </div></div>
        ) : invoices.data && invoices.data.length > 0 ? (
          <>
            {/* Desktop (md+): full table. */}
            <div className="card hidden overflow-x-auto p-0 md:block">
              <table className="w-full min-w-[480px] text-body">
                <thead>
                  <tr className="border-b border-line text-left text-micro uppercase tracking-wide text-muted">
                    <th scope="col" className="px-5 py-3">{t('billing.period', 'Davr')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.amount', 'Summa')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.status', 'Holat')}</th>
                    <th scope="col" className="px-5 py-3">{t('billing.due', 'Muddat')}</th>
                    <th scope="col" className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {invoices.data.map((inv) => (
                    <tr key={inv.id} className="border-b border-line/60 last:border-0">
                      <td className="px-5 py-3">{shortDate(inv.period_start)} – {shortDate(inv.period_end)}</td>
                      <td className="px-5 py-3 font-medium">{money(inv.amount)}</td>
                      <td className="px-5 py-3"><StatusBadge status={inv.status} /></td>
                      <td className="px-5 py-3 text-muted">{inv.due_at ? shortDate(inv.due_at) : '—'}</td>
                      <td className="px-5 py-3 text-right">
                        <Button variant="ghost" size="sm" loading={dlId === inv.id} onClick={() => download(inv.id)}>
                          <FileText size={14} /> PDF
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Mobile (<md): one stacked card per invoice. */}
            <ul className="space-y-2.5 md:hidden">
              {invoices.data.map((inv) => (
                <li key={inv.id} className="rounded-card border border-line bg-surface px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-body font-medium">{shortDate(inv.period_start)} – {shortDate(inv.period_end)}</div>
                      <div className="mt-0.5 tabular-nums text-small text-ink-soft">{money(inv.amount)} {currency}</div>
                    </div>
                    <StatusBadge status={inv.status} />
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-3">
                    <span className="text-caption text-muted">
                      {t('billing.due', 'Muddat')}: <span className="tabular-nums text-ink-soft">{inv.due_at ? shortDate(inv.due_at) : '—'}</span>
                    </span>
                    <Button variant="ghost" size="sm" loading={dlId === inv.id} onClick={() => download(inv.id)}>
                      <FileText size={14} /> PDF
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <EmptyState icon={<FileText size={20} />}
                      title={t('billing.noInvoices', 'Hozircha hisob-faktura yo’q.')} />
        )}
      </section>

      {/* confirm modal */}
      <Modal
        open={picking !== null}
        title={t('billing.confirmTitle', 'Tarifni o’zgartirish')}
        subtitle={picking ? `${planNameForCode(b?.plan_code)} → ${planName(picking)}` : undefined}
        onClose={() => { if (!busy) { setPicking(null); setPlanErr(null); } }}
        footer={
          <div className="flex justify-end gap-2">
            <Button variant="ghost" loading={busy} onClick={() => setPicking(null)}>
              {t('common.cancel', 'Bekor qilish')}
            </Button>
            <Button loading={busy} onClick={confirmChange}>
              {busy ? t('common.saving', 'Saqlanmoqda…') : t('billing.confirm', 'Tasdiqlash')}
            </Button>
          </div>
        }
      >
        <p className="text-body text-ink-soft">
          {t('billing.confirmBody', 'Tarif darhol o’zgaradi. Keyingi hisob-faktura yangi tarif bo’yicha shakllantiriladi.')}
        </p>
        {picking && picking.price_monthly ? (
          <div className="mt-3 flex items-center justify-between rounded-control border border-line bg-cream px-3 py-2 text-body">
            <span className="text-muted">{t('billing.willCharge', 'Balansdan yechiladi')}</span>
            <span className="font-semibold tabular-nums">{money(picking.price_monthly)} {currency}</span>
          </div>
        ) : null}
        {planErr && (
          <div className="mt-3 rounded-control border border-danger/30 bg-[color:var(--c-danger-soft)] px-3 py-2 text-body text-danger">
            {planErr}
            <Button size="sm" variant="outline" className="mt-2"
                    onClick={() => { setPicking(null); setPlanErr(null); setTopupOpen(true); }}>
              <Wallet size={14} /> {t('topup.title', 'Balansni to‘ldirish')}
            </Button>
          </div>
        )}
      </Modal>

      <TopUpModal open={topupOpen} onClose={() => { setTopupOpen(false); qc.invalidateQueries({ queryKey: queryKeys.tenant.topupInfo() }); }} />
    </div>
  );
}
