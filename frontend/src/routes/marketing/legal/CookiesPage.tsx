import { LegalPage } from './LegalPage';

export default function CookiesPage() {
  return (
    <LegalPage
      uz={{
        kicker: 'YURIDIK',
        title: "Cookie va lokal saqlash siyosati",
        intro: "Muddato kuzatuv (tracking) cookie'laridan foydalanmaydi. Kirish uchun faqat zaruriy, xavfsiz cookie'lardan foydalanamiz va brauzeringizning lokal xotirasida (localStorage) bir nechta texnik yozuv saqlaymiz.",
        sections: [
          { heading: "1. Faqat zaruriy cookie'lar", body: "Brauzerda sessiyangiz xavfsiz, HttpOnly cookie'da saqlanadi (muddato.session) — uni JavaScript o'qiy olmaydi. CSRF himoyasi uchun muddato.csrf cookie'si ham ishlatiladi. Bu cookie'lar reklama yoki saytlararo kuzatuv uchun emas." },
          { heading: "2. Lokal xotirada saqlanadigan yozuvlar", body: "muddato.authed — tizimga kirgansiz, degan oddiy belgi. i18nextLng — tanlangan til, uz yoki ru. muddato.session — sessiya tokeni: brauzerda u HttpOnly cookie'da bo'ladi, Telegram ilovasida esa (u yerda cookie ishonchli ishlamaydi) localStorage'da ham saqlanadi." },
          { heading: "3. Tahliliy va marketing", body: "Tahliliy yoki marketing cookie'lari, uchinchi tomon piksellari yoki reklama kuzatuvchilari ishlatilmaydi. Kelajakda tahlil qo'shilsa, oldindan banner orqali roziligingizni so'raymiz." },
          { heading: "4. Boshqarish", body: "Brauzeringiz sozlamalaridan saqlangan ma'lumotlarni (cookie va localStorage) istalgan vaqtda tozalashingiz mumkin. Buning natijasida tizimdan chiqib ketasiz va tilni qayta tanlashingiz kerak bo'ladi." },
        ],
      }}
      ru={{
        kicker: 'ЮРИДИКА',
        title: 'Политика Cookies и локального хранилища',
        intro: 'Muddato не использует отслеживающие (tracking) cookies. Для входа применяются только необходимые защищённые cookies, а в локальном хранилище (localStorage) браузера хранятся несколько технических записей.',
        sections: [
          { heading: '1. Только необходимые cookies', body: 'В браузере ваша сессия хранится в защищённой HttpOnly cookie (muddato.session) — её нельзя прочитать из JavaScript. Также используется cookie muddato.csrf для защиты от CSRF. Эти cookies не используются для рекламы или межсайтового отслеживания.' },
          { heading: '2. Записи в локальном хранилище', body: 'muddato.authed — простой признак того, что вы вошли. i18nextLng — выбранный язык, uz или ru. muddato.session — токен сессии: в браузере он в HttpOnly cookie, а в приложении Telegram (где cookie работает ненадёжно) хранится также в localStorage.' },
          { heading: '3. Аналитика и маркетинг', body: 'Аналитические и маркетинговые cookies, сторонние пиксели и рекламные трекеры не используются. Если в будущем добавим аналитику — заранее запросим согласие через баннер.' },
          { heading: '4. Управление', body: 'Сохранённые данные (cookies и localStorage) можно очистить в настройках браузера в любой момент. После этого вы выйдете из системы и потребуется заново выбрать язык.' },
        ],
      }}
    />
  );
}
