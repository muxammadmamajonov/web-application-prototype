import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Smile } from 'lucide-react';
import { cn } from '@/lib/cn';
import { inputCls } from './Modal';

// Broad emoji set grouped by theme, with light keyword tags for search.
// "Show all" means the panel scrolls through the whole set; search narrows it.
const GROUPS: { key: string; emojis: [string, string][] }[] = [
  { key: 'tech', emojis: [
    ['📱','phone smartphone'],['📲','phone'],['💻','laptop computer'],['🖥️','desktop'],['⌨️','keyboard'],
    ['🖱️','mouse'],['🖨️','printer'],['📷','camera'],['📸','camera'],['📹','video'],['🎥','camera film'],
    ['📺','tv television'],['📻','radio'],['🎧','headphones audio'],['🔋','battery'],['🔌','plug power'],
    ['💡','bulb light'],['🔦','flashlight'],['⌚','watch'],['📟','pager'],['☎️','telephone'],['🎮','game'],
    ['🕹️','joystick'],['💽','disk'],['💾','floppy save'],['💿','cd'],['📀','dvd'],['🧮','abacus'],
  ]},
  { key: 'home', emojis: [
    ['🛋️','sofa couch furniture'],['🪑','chair'],['🛏️','bed'],['🚪','door'],['🪟','window'],['🧺','basket laundry'],
    ['🧹','broom'],['🧽','sponge'],['🚿','shower'],['🛁','bath'],['🚽','toilet'],['🪞','mirror'],['🕯️','candle'],
    ['🖼️','picture frame'],['🪆','doll'],['🧸','teddy toy'],['🛒','cart shopping'],['🏠','house home'],
    ['🏡','house'],['🏢','office building'],['🏬','store mall'],['🏪','shop store'],
  ]},
  { key: 'appliance', emojis: [
    ['🧊','ice fridge'],['❄️','snow cold ac'],['🌡️','temperature'],['🔥','fire heat'],['💨','wind fan'],
    ['🫧','bubbles wash'],['⚡','electric power'],['🪫','battery low'],['🧯','extinguisher'],
  ]},
  { key: 'transport', emojis: [
    ['🚗','car'],['🚙','suv car'],['🏎️','race car'],['🚕','taxi'],['🚓','police car'],['🚐','van'],['🚚','truck'],
    ['🚛','truck lorry'],['🏍️','motorcycle'],['🛵','scooter'],['🚲','bicycle bike'],['🛴','scooter'],['🛹','skateboard'],
    ['🚜','tractor'],['✈️','plane'],['🚁','helicopter'],['🚢','ship'],['⛵','boat sail'],['🛞','tire wheel'],['⛽','fuel gas'],
  ]},
  { key: 'fashion', emojis: [
    ['👕','tshirt shirt clothes'],['👔','shirt tie'],['👖','jeans pants'],['👗','dress'],['👚','blouse'],['🧥','coat jacket'],
    ['🧦','socks'],['🧤','gloves'],['🧣','scarf'],['🧢','cap hat'],['👒','hat'],['🎩','top hat'],['👜','bag handbag'],
    ['👛','purse'],['👝','pouch bag'],['🎒','backpack'],['👞','shoe'],['👟','sneaker shoe'],['👠','heels shoe'],
    ['👢','boot'],['🥾','boot hiking'],['🩴','sandal'],['💍','ring jewelry'],['💎','diamond gem'],['⌚','watch'],['🕶️','sunglasses'],['👓','glasses'],
  ]},
  { key: 'beauty', emojis: [
    ['💄','lipstick makeup'],['💅','nails'],['🧴','lotion bottle'],['🧼','soap'],['🪥','toothbrush'],['🧷','pin'],
    ['🪒','razor'],['🧬','dna'],['💊','pill medicine'],['💉','syringe'],['🩹','bandage'],['🌸','flower'],['🌹','rose'],
  ]},
  { key: 'food', emojis: [
    ['🍎','apple fruit'],['🍌','banana'],['🍊','orange'],['🍉','watermelon'],['🍇','grapes'],['🍓','strawberry'],
    ['🥦','broccoli vegetable'],['🥕','carrot'],['🍞','bread'],['🥖','baguette bread'],['🧀','cheese'],['🥩','meat'],
    ['🍗','chicken'],['🍔','burger'],['🍕','pizza'],['🌮','taco'],['🍜','noodles'],['🍚','rice'],['🍰','cake'],
    ['🧁','cupcake'],['🍫','chocolate'],['🍬','candy'],['☕','coffee'],['🍵','tea'],['🥤','drink'],['🍼','bottle'],
    ['🍯','honey'],['🧂','salt'],['🥛','milk'],['🍶','sake'],
  ]},
  { key: 'tools', emojis: [
    ['🔧','wrench tool'],['🔨','hammer'],['🪛','screwdriver'],['🔩','bolt nut'],['⚙️','gear'],['🪚','saw'],['🪓','axe'],
    ['🧰','toolbox'],['🪝','hook'],['🧲','magnet'],['⛏️','pick'],['🛠️','tools'],['🔗','link chain'],['📏','ruler'],
    ['📐','ruler triangle'],['✂️','scissors'],['🧪','test tube'],['🔬','microscope'],['🔭','telescope'],
  ]},
  { key: 'sport', emojis: [
    ['⚽','football soccer'],['🏀','basketball'],['🏈','football'],['⚾','baseball'],['🎾','tennis'],['🏐','volleyball'],
    ['🏉','rugby'],['🎱','billiard'],['🏓','pingpong'],['🏸','badminton'],['🥊','boxing'],['🥋','martial'],['⛳','golf'],
    ['🏹','archery bow'],['🎣','fishing'],['🛼','skate'],['⛸️','ice skate'],['🎿','ski'],['🏋️','weights gym'],['🚴','cycling'],
  ]},
  { key: 'music', emojis: [
    ['🎸','guitar'],['🎹','piano keyboard'],['🥁','drum'],['🎺','trumpet'],['🎷','sax'],['🎻','violin'],['🎤','mic'],
    ['🎼','music notes'],['🎵','note'],['🎶','notes'],['📯','horn'],['🪕','banjo'],
  ]},
  { key: 'baby', emojis: [
    ['🍼','baby bottle'],['🧸','teddy'],['👶','baby'],['🚼','baby symbol'],['🪀','yoyo toy'],['🎈','balloon'],
    ['🎁','gift'],['🎀','ribbon'],['🪁','kite'],['🧩','puzzle'],['🎲','dice'],
  ]},
  { key: 'misc', emojis: [
    ['📦','box package'],['🏷️','tag label'],['🛍️','bags shopping'],['💰','money bag'],['💵','cash money'],['💳','card'],
    ['🧾','receipt'],['🪙','coin'],['📚','books'],['📖','book'],['✏️','pencil'],['🖊️','pen'],['📝','memo note'],
    ['📅','calendar'],['📌','pin'],['📎','clip'],['🔑','key'],['🗝️','key'],['🔒','lock'],['⭐','star'],['❤️','heart'],
    ['✅','check'],['🎯','target'],['🏆','trophy'],['🎉','party'],['🌟','star'],['💼','briefcase'],['🧳','luggage'],
  ]},
];

const ALL: [string, string][] = GROUPS.flatMap((g) => g.emojis);

interface Props {
  value?: string | null;
  onChange: (emoji: string) => void;
  /** Render the full grid INLINE (no dropdown) — for wizard steps where there's
   *  room to show every emoji, grouped + searchable, instead of a cramped popup. */
  inline?: boolean;
}

/** Emoji picker for category icons — a searchable set of ~300 emojis. Renders
 *  either as a compact dropdown trigger (default) or as a full inline grid. */
export function EmojiPicker({ value, onChange, inline }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };  // (#164) Esc closes
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const term = q.trim().toLowerCase();
  const shown = term ? ALL.filter(([e, kw]) => kw.includes(term) || e === term) : ALL;

  // ── Inline mode: the whole grid, grouped, flowing in the parent's scroll ──
  if (inline) {
    const cell = (e: string, kw: string) => (
      <button key={e} type="button" aria-label={kw} onClick={() => onChange(e)}
              className={cn('grid h-10 place-items-center rounded-lg text-2xl leading-none transition',
                value === e ? 'bg-[color:var(--c-accent-soft)] ring-2 ring-[color:var(--c-accent-fill)]' : 'hover:bg-ink/5')}>
        {e}
      </button>
    );
    return (
      <div>
        <div className="mb-3 flex items-center gap-3">
          <span className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl bg-[color:var(--c-accent-fill)] text-3xl leading-none">
            {value || '🙂'}
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">{value ? t('emoji.picked', 'Tanlangan belgi') : t('emoji.pickPrompt', 'Belgi tanlang')}</div>
            {value && (
              <button type="button" onClick={() => onChange('')} className="mt-0.5 text-caption text-muted underline hover:text-ink">
                {t('common.clear', 'Tozalash')}
              </button>
            )}
          </div>
        </div>
        <div className="relative mb-3">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder={t('emoji.search', 'Qidirish (masalan: phone, car…)') ?? ''}
                 className="h-10 w-full rounded-xl border border-line bg-surface pl-9 pr-3 text-base outline-none placeholder:text-muted focus:border-ink/25 focus:bg-white" />
        </div>
        {term ? (
          <div className="grid grid-cols-7 gap-1">
            {shown.map(([e, kw]) => cell(e, kw))}
            {shown.length === 0 && (
              <div className="col-span-7 py-6 text-center text-sm text-muted">{t('emoji.none', 'Topilmadi')}</div>
            )}
          </div>
        ) : (
          GROUPS.map((g) => (
            <div key={g.key} className="mb-3">
              <div className="mb-1.5 text-micro uppercase tracking-wider text-muted">{t(`emojiGroup.${g.key}`, g.key)}</div>
              <div className="grid grid-cols-7 gap-1">{g.emojis.map(([e, kw]) => cell(e, kw))}</div>
            </div>
          ))
        )}
      </div>
    );
  }

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)}
              className={cn(inputCls, 'flex items-center gap-2 text-left')}>
        {value
          ? <span className="text-2xl leading-none">{value}</span>
          : <Smile size={18} className="text-muted" />}
        <span className={cn('text-sm', !value && 'text-muted')}>
          {value ? t('emoji.change', 'Belgini almashtirish') : t('emoji.pick', 'Belgi tanlash')}
        </span>
      </button>

      {open && (
        <div className="absolute left-0 z-50 mt-2 w-[min(20rem,90vw)] rounded-card border border-line bg-surface p-2 shadow-pop">
          <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus
                 placeholder={t('emoji.search', 'Qidirish (masalan: phone, car…)') ?? ''}
                 className={cn(inputCls, 'mb-2 h-9')} />
          <div className="grid max-h-56 grid-cols-8 gap-1 overflow-y-auto">
            {shown.map(([e, kw]) => (
              <button key={e} type="button" aria-label={kw}
                      onMouseDown={(ev) => { ev.preventDefault(); onChange(e); setOpen(false); setQ(''); }}
                      className="grid h-9 place-items-center rounded-lg text-xl hover:bg-ink/5">
                {e}
              </button>
            ))}
            {shown.length === 0 && (
              <div className="col-span-8 py-6 text-center text-sm text-muted">{t('emoji.none', 'Topilmadi')}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
