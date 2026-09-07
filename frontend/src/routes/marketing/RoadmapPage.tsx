import { useParams } from 'react-router';
import { Check, Circle, Sparkles } from 'lucide-react';
import { Container } from '@/components/ui/Container';
import { PublicShell } from '@/components/marketing/PublicLayout';
import { cn } from '@/lib/cn';

type Milestone = { quarter: string; title: string; items: string[]; state: 'done' | 'in-progress' | 'planned' };

const ROADMAP: Record<'uz' | 'ru', Milestone[]> = {
  uz: [
    {
      quarter: '2026 · 1-chorak',
      title: 'Platforma poydevori',
      state: 'done',
      items: [
        "Telegram orqali xavfsiz kirish — bot va WebApp",
        "Brendlangan landing, demo va bog'lanish sahifalari",
        "Ishonchli bulutli infratuzilma — 24/7 ishlaydi",
        "Biznes sozlamalari, xodimlar boshqaruvi va bildirishnomalar",
        "Tarif imkoniyatlari + mijoz va xodim taklif havolalari",
        "Mahsulot katalogi: kategoriya → brend → model",
      ],
    },
    {
      quarter: '2026 · 2-chorak',
      title: "Avtomatlashtirish va shartnoma",
      state: 'in-progress',
      items: [
        "Muddati o'tgan to'lovlarni avtomatik belgilash + jarima",
        "Avto-eslatma: 3 / 1 / 0 kun oldin",
        "O'zbekiston bayramlari kalendari (eslatmalarni moslashtirish)",
        "Shartnoma PDF — O'zbekiston huquqiy shabloni",
        "Pro tarif: o'z shartnoma shablonini yuklash",
        "Chek PDF — A4 va termal 80mm chop etish",
      ],
    },
    {
      quarter: '2026 · 3-chorak',
      title: "Mijoz ilovasi + Click/Payme",
      state: 'planned',
      items: [
        "Click va Payme to'liq integratsiyasi",
        "Mijoz ilovasi: \"Mening to'lovlarim\" + muddat sanog'i",
        "Bir tugmada to'lash: Click / Payme / Humo / Uzcard",
        "Chek rasmini yuborish (naqd to'lov uchun)",
        "Muddatdan oldin yopish kalkulyatori",
        "Shartnomani mijoz o'zi yuklab olishi",
        "Telefon do'koni paketi: IMEI, kafil, ish joyi / oylik",
      ],
    },
    {
      quarter: '2026 · 4-chorak',
      title: 'Risk tarmog\'i + Hisobotlar',
      state: 'planned',
      items: [
        "Do'konlararo qora ro'yxat — ixtiyoriy va maxfiy tarmoq",
        "Mijoz risk bahosi: ichki tarix + tashqi signallar",
        "Hisobotlar: muddat, pul oqimi, sotuvchi, margin",
        "Sotuvchi komissiyasi + smena boshqaruvi",
        "Har bir hisobotda PDF / CSV / Excel eksport",
        "SMS eslatma — Telegramsiz mijozlar uchun",
      ],
    },
    {
      quarter: '2027 · 1-chorak',
      title: "O'sish va tariflar",
      state: 'planned',
      items: [
        "O'zini-o'zi ro'yxatdan o'tkazish + 14 kunlik trial",
        "Moslashuvchan tarif sozlamalari",
        "Har bir bo'lib to'lash uchun individual tarif",
        "Avtomatik billing: hisob-faktura, eslatma, to'xtatish",
        "Ombor harakati + Excel/CSV import",
        "Kam qoldiq haqida ogohlantirish",
      ],
    },
    {
      quarter: '2027 · 2-chorak+',
      title: 'Integratsiyalar va kengayish',
      state: 'planned',
      items: [
        "1C / SmartUp eksport + integratsiya imkoniyatlari",
        "Soliq qo'mitasi fiskalizatsiyasi (onlayn chek)",
        "Qaytarish / qayta tuzish / qisman to'lov",
        "Murabaha (islomiy moliya) hisob-kitobi",
        "Ko'p filial bitta biznes ostida",
        "Telegram Stars to'lovi va mobil ilova",
      ],
    },
  ],
  ru: [
    {
      quarter: '2026 · Q1',
      title: 'Фундамент платформы',
      state: 'done',
      items: [
        'Безопасный вход через Telegram — бот и WebApp',
        'Брендированные лендинг, демо и страница контактов',
        'Надёжная облачная инфраструктура — работает 24/7',
        'Настройки бизнеса, управление сотрудниками и уведомления',
        'Возможности тарифов + приглашения клиентов и сотрудников',
        'Каталог товаров: категория → бренд → модель',
      ],
    },
    {
      quarter: '2026 · Q2',
      title: 'Автоматизация и договор',
      state: 'in-progress',
      items: [
        'Автоматическая пометка просрочки + начисление пени',
        'Авто-напоминания: за 3 / 1 / 0 дней',
        'Календарь праздников Узбекистана (адаптация напоминаний)',
        'PDF договора — юридический шаблон Узбекистана',
        'Pro: загрузка собственного шаблона договора',
        'PDF чека — A4 и термопечать 80мм',
      ],
    },
    {
      quarter: '2026 · Q3',
      title: 'Клиентское приложение + Click/Payme',
      state: 'planned',
      items: [
        'Полная интеграция Click и Payme',
        'Приложение клиента: «Мои рассрочки» + обратный отсчёт',
        'Оплата одной кнопкой: Click / Payme / Humo / Uzcard',
        'Отправка фото чека (для оплаты наличными)',
        'Калькулятор досрочного погашения',
        'Скачивание договора клиентом',
        'Пакет для телефонных магазинов: IMEI, поручитель, место работы',
      ],
    },
    {
      quarter: '2026 · Q4',
      title: 'Сеть рисков + Отчёты',
      state: 'planned',
      items: [
        'Межмагазинный чёрный список — добровольная и конфиденциальная сеть',
        'Оценка риска клиента: внутренняя история + внешние сигналы',
        'Отчёты: просрочка, денежный поток, продавцы, маржа',
        'Расчёт комиссий продавцов + управление сменой',
        'Экспорт PDF / CSV / Excel в каждом отчёте',
        'SMS-напоминания — для клиентов без Telegram',
      ],
    },
    {
      quarter: '2027 · Q1',
      title: 'Рост и тарифы',
      state: 'planned',
      items: [
        'Самостоятельная регистрация + 14-дневный триал',
        'Гибкая настройка тарифов',
        'Индивидуальный тариф для каждой рассрочки',
        'Автоматизация биллинга: счета, напоминания, приостановка',
        'Движения склада + импорт из Excel/CSV',
        'Оповещение о низком остатке',
      ],
    },
    {
      quarter: '2027 · Q2+',
      title: 'Интеграции и расширение',
      state: 'planned',
      items: [
        '1С / SmartUp экспорт + возможности интеграции',
        'Фискализация Налогового комитета (онлайн-чек)',
        'Возврат / реструктуризация / частичные платежи',
        'Расчёт «Мурабаха» (исламский финансинг)',
        'Несколько филиалов в одном бизнесе',
        'Оплата Telegram Stars и мобильное приложение',
      ],
    },
  ],
};

type OsStatus = 'live' | 'soon';
type OsModule = { name: string; desc: string; status: OsStatus };

const OS_MODULES: Record<'uz' | 'ru', OsModule[]> = {
  uz: [
    { name: 'HisobchiQarz', desc: "Qarz va bo'lib to'lashni boshqarish", status: 'live' },
    { name: 'EslatBot', desc: 'Avtomatik eslatma — Telegram va SMS', status: 'live' },
    { name: 'ClubPay', desc: "A'zolik / abonement — fitnes, kurs, coworking", status: 'soon' },
    { name: 'IjaraBoshqar', desc: "Ijara to'lovlari — kvartira, ofis, do'kon", status: 'soon' },
    { name: 'ObunaKanal', desc: 'Telegram premium kanal obunalari', status: 'soon' },
    { name: 'Hisob-faktura', desc: 'Hisob-faktura va takroriy billing', status: 'soon' },
    { name: 'CRM Lite', desc: 'Lidlar, eslatmalar, vazifalar', status: 'soon' },
    { name: 'Analitika', desc: 'MRR, churn, LTV, cohort — bitta joyda', status: 'soon' },
  ],
  ru: [
    { name: 'HisobchiQarz', desc: 'Управление долгами и рассрочкой', status: 'live' },
    { name: 'EslatBot', desc: 'Автонапоминания — Telegram и SMS', status: 'live' },
    { name: 'ClubPay', desc: 'Абонементы — фитнес, курсы, коворкинг', status: 'soon' },
    { name: 'IjaraBoshqar', desc: 'Арендные платежи — квартира, офис, магазин', status: 'soon' },
    { name: 'ObunaKanal', desc: 'Подписки на премиум Telegram-каналы', status: 'soon' },
    { name: 'Счета', desc: 'Счета и повторяющийся биллинг', status: 'soon' },
    { name: 'CRM Lite', desc: 'Лиды, заметки, задачи', status: 'soon' },
    { name: 'Аналитика', desc: 'MRR, churn, LTV, cohort — в одном месте', status: 'soon' },
  ],
};

export default function RoadmapPage() {
  const { locale } = useParams<{ locale?: string }>();
  const lng = (locale ?? 'uz') as 'uz' | 'ru';
  const t = COPY[lng];

  return (
    <PublicShell>
      <section className="border-b border-line py-16">
        <Container>
          <div className="kicker text-accent">{t.kicker}</div>
          <h1 className="mt-2 h-hero-2 font-semibold tracking-tight leading-[1.05]">{t.title}</h1>
          <p className="mt-4 max-w-2xl text-ink-soft text-subsection">{t.sub}</p>
        </Container>
      </section>

      <section className="border-b border-line bg-cream py-12">
        <Container>
          <div className="kicker text-accent">{t.osKicker}</div>
          <h2 className="mt-2 h-display font-semibold tracking-tight leading-tight">{t.osTitle}</h2>
          <p className="mt-3 max-w-2xl text-ink-soft text-body">{t.osSub}</p>
          <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {OS_MODULES[lng].map((m) => (
              <div key={m.name} className="rounded-2xl border border-line bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold tracking-tight">{m.name}</h3>
                  <span className={cn(
                    'inline-flex shrink-0 items-center rounded-pill px-2 py-0.5 text-micro font-medium',
                    m.status === 'live' ? 'bg-[color:var(--c-success-soft)] text-success' : 'bg-[color:var(--c-accent-soft)] text-accent',
                  )}>
                    {m.status === 'live' ? t.live : t.soon}
                  </span>
                </div>
                <p className="mt-1.5 text-small text-ink-soft">{m.desc}</p>
              </div>
            ))}
          </div>
        </Container>
      </section>

      <section className="py-12">
        <Container className="max-w-3xl">
          <ol className="relative space-y-10 before:absolute before:left-[9px] before:top-2 before:bottom-2 before:w-px before:bg-line">
            {ROADMAP[lng].map((m) => <Step key={m.quarter} m={m} lng={lng} />)}
          </ol>
        </Container>
      </section>
    </PublicShell>
  );
}

function Step({ m, lng }: { m: Milestone; lng: 'uz' | 'ru' }) {
  return (
    <li className="relative flex gap-4">
      <span className={cn(
        'relative z-10 mt-1 grid h-5 w-5 shrink-0 place-items-center rounded-full ring-4 ring-cream',
        m.state === 'done' && 'bg-success text-white',
        m.state === 'in-progress' && 'bg-accent text-white',
        m.state === 'planned' && 'bg-line text-muted',
      )}>
        {m.state === 'done' && <Check size={12} />}
        {m.state === 'in-progress' && <Sparkles size={12} />}
        {m.state === 'planned' && <Circle size={12} />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="font-mono text-caption uppercase tracking-wider text-muted">{m.quarter}</span>
          <h2 className="text-xl font-semibold tracking-tight">{m.title}</h2>
          <StateBadge state={m.state} lng={lng} />
        </div>
        <ul className="mt-4 space-y-2 text-sm text-ink-soft">
          {m.items.map((it) => (
            <li key={it} className="flex items-start gap-2">
              <span className={cn(
                'mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full',
                m.state === 'done' ? 'bg-success' : m.state === 'in-progress' ? 'bg-accent' : 'bg-ink/30',
              )} />
              <span>{it}</span>
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}

function StateBadge({ state, lng }: { state: Milestone['state']; lng: 'uz' | 'ru' }) {
  const label =
    state === 'done' ? (lng === 'ru' ? 'Сделано' : 'Bajarildi') :
    state === 'in-progress' ? (lng === 'ru' ? 'В работе' : 'Jarayonda') :
    (lng === 'ru' ? 'Запланировано' : 'Rejada');
  const tone =
    state === 'done' ? 'bg-[color:var(--c-success-soft)] text-success' :
    state === 'in-progress' ? 'bg-[color:var(--c-accent-soft)] text-accent' :
    'bg-line text-muted';
  return (
    <span className={cn('inline-flex items-center rounded-pill px-2.5 py-0.5 text-micro font-medium', tone)}>
      {label}
    </span>
  );
}

const COPY = {
  uz: {
    kicker: "YO'L XARITASI", title: "Hozir va keyingi qadamlar.",
    sub: "Muddato ochiq jadvalda ishlaydi — nima qilingani, nima ustida va keyin nima keladi.",
    osKicker: 'MUDDATO OS', osTitle: "Bitta platforma, ko'p yo'nalish.",
    osSub: "Muddato OS — qarz, a'zolik, ijara va Telegram obunalari uchun yagona to'lov-yig'ish tizimi. Bitta dvijok, bitta dashboard.",
    live: 'Tayyor', soon: 'Tez orada',
  },
  ru: {
    kicker: 'ROADMAP', title: 'Сейчас и следующие шаги.',
    sub: 'Muddato работает по открытой дорожной карте — что сделано, что в работе и что дальше.',
    osKicker: 'MUDDATO OS', osTitle: 'Одна платформа, много направлений.',
    osSub: 'Muddato OS — единая система сбора платежей для долгов, абонементов, аренды и Telegram-подписок. Один движок, один дашборд.',
    live: 'Готово', soon: 'Скоро',
  },
} as const;
