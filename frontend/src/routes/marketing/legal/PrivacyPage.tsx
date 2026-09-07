import { LegalPage } from './LegalPage';

export default function PrivacyPage() {
  return (
    <LegalPage
      uz={{
        kicker: 'YURIDIK',
        title: 'Maxfiylik siyosati',
        intro: "Muddato qanday ma'lumotlarni yig'adi, ularni qanday saqlaydi va ulashadi.",
        sections: [
          { heading: "1. Yig'adigan ma'lumotlar", body: "Telegram ID, ism, foydalanuvchi nomi, foto URL (kirish uchun). Biznesning mijozlari va to'lovlari (biznes tomonidan kiritilganlari)." },
          { heading: '2. Maqsad', body: "Hisob faolligini ushlab turish, eslatmalar yuborish, hisobotlar tayyorlash. Reklama va marketing uchun uchinchi tomonga sotmaymiz." },
          { heading: '3. Xavfsizlik va saqlash', body: "Ma'lumotlaringiz shifrlash va zamonaviy xavfsizlik choralari bilan himoyalangan serverlarda saqlanadi. Ularga faqat vakolatli xodimlar cheklangan tarzda kira oladi. Hisobni o'chirsangiz, ma'lumotlar 30 kun ichida butunlay yo'q qilinadi." },
          { heading: '4. Sizning huquqlaringiz', body: "Ma'lumotlarni eksport qilish, tuzatish yoki o'chirish so'rovini @MuddatoSupport'ga yuboring — 7 ish kuni ichida amalga oshiramiz." },
          { heading: '5. Cookie va lokal saqlash', body: "Kuzatuv cookie'laridan foydalanmaymiz. Brauzerda sessiyangiz xavfsiz HttpOnly cookie'da saqlanadi (JavaScript o'qiy olmaydi); localStorage'da til, kirish belgisi va (Telegram ilovasida cookie ishlamagani uchun) sessiya tokeni saqlanadi. Batafsil — Cookie siyosati sahifasida." },
          { heading: '6. Aloqalar', body: "Maxfiylik bo'yicha savol bo'lsa: privacy@muddato.uz yoki @MuddatoSupport." },
        ],
      }}
      ru={{
        kicker: 'ЮРИДИКА',
        title: 'Политика конфиденциальности',
        intro: 'Какие данные Muddato собирает, как хранит и с кем делится.',
        sections: [
          { heading: '1. Какие данные собираем', body: 'Telegram ID, имя, username, фото (для входа). Клиенты и платежи бизнеса (вводимые бизнесом).' },
          { heading: '2. Цели', body: 'Поддержание работы аккаунта, напоминания, отчёты. Не продаём третьим лицам.' },
          { heading: '3. Безопасность и хранение', body: 'Ваши данные хранятся на защищённых серверах с шифрованием и современными мерами безопасности. Доступ ограничен и предоставляется только уполномоченным сотрудникам. При удалении аккаунта данные полностью удаляются в течение 30 дней.' },
          { heading: '4. Ваши права', body: 'Экспорт, исправление или удаление — заявка в @MuddatoSupport, выполняем в течение 7 рабочих дней.' },
          { heading: '5. Cookies и локальное хранилище', body: 'Отслеживающие cookies не используем. В браузере ваша сессия хранится в защищённой HttpOnly cookie (недоступна для JavaScript); в localStorage хранятся язык, признак входа и (в приложении Telegram, где cookie не работает) токен сессии. Подробнее — на странице «Политика Cookies».' },
          { heading: '6. Контакты', body: 'privacy@muddato.uz или @MuddatoSupport.' },
        ],
      }}
    />
  );
}
