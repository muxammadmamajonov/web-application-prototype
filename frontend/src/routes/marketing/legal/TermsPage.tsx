import { LegalPage } from './LegalPage';

export default function TermsPage() {
  return (
    <LegalPage
      uz={{
        kicker: 'YURIDIK',
        title: 'Foydalanish shartlari',
        intro: 'Muddato platformasidan foydalanish shartlari. Hisob ochib platformaga kirgan har bir foydalanuvchi ushbu shartlarga rozi.',
        sections: [
          {
            heading: '1. Qabul',
            body: "Muddato akkauntini yoki API'sini ishlatish bilan siz ushbu shartlarni o'qib chiqib qabul qilganingizni tasdiqlaysiz. Rozi bo'lmasangiz, xizmatdan foydalanishni to'xtating.",
          },
          {
            heading: '2. Hisob',
            body: "Hisob ma'lumotlari xavfsizligi siz uchun mas'uliyat. Telegram orqali kirgan akkaunt orqali bajarilgan har bir amal egasi uchun yozib boriladi.",
          },
          {
            heading: "3. To'lov va bekor qilish",
            body: "Demo davri Muddato bilan kelishiladi va u uchun to'lov olinmaydi. Demo tugagach tarifga avtomatik o'tilmaydi — yangi tarifni siz tasdiqlaysiz. Istalgan vaqtda obunani bekor qilishingiz mumkin.",
          },
          {
            heading: "4. Foydalanish chegaralari",
            body: "Spam, qonunsiz tovar/xizmatlar yoki uchinchi tomonning huquqlarini buzadigan amallar uchun akkaunt to'xtatiladi. Mavjud cheklar tarif sahifasida e'lon qilingan.",
          },
          {
            heading: "5. Ma'lumotlar egaligi",
            body: "Siz yuklagan barcha mijoz va to'lov ma'lumotlari sizning egaligingizda qoladi. Eksport istalgan vaqtda CSV / PDF formatda olinadi.",
          },
          {
            heading: "6. Mas'uliyat chegarasi",
            body: "Xizmat 'as is' tarzda taqdim etiladi. Muddato to'g'ridan-to'g'ri yoki bilvosita zararlar uchun javobgar emas; mas'uliyatimizning yuqori chegarasi so'nggi 12 oy uchun to'lagan summangizdan oshmaydi.",
          },
          {
            heading: '7. O\'zgartirishlar',
            body: "Shartlarga sezilarli o'zgarish kiritilganda kamida 30 kun oldin xabar beramiz. O'zgartirishdan keyin xizmatdan foydalanish — yangi shartlarga rozilik hisoblanadi.",
          },
          {
            heading: "8. Amaldagi qonun va nizolar",
            body: "Ushbu shartlarga O'zbekiston Respublikasi qonunchiligi qo'llaniladi. Muzokaralar yo'li bilan hal etilmagan nizolar Toshkent shahridagi vakolatli sudda ko'rib chiqiladi.",
          },
        ],
      }}
      ru={{
        kicker: 'ЮРИДИКА',
        title: 'Условия использования',
        intro: 'Условия использования платформы Muddato. Каждый пользователь, открывающий аккаунт, соглашается с этими условиями.',
        sections: [
          { heading: '1. Принятие', body: 'Используя аккаунт или API Muddato, вы подтверждаете, что прочли и принимаете эти условия. Если не согласны — прекратите использование сервиса.' },
          { heading: '2. Аккаунт', body: 'Безопасность учётных данных — ваша ответственность. Все действия, выполненные через ваш аккаунт Telegram, фиксируются на вас.' },
          { heading: '3. Оплата и отмена', body: 'Срок демо согласуется с Muddato, плата за него не взимается. После демо тариф не активируется автоматически. Отменить подписку можно в любой момент.' },
          { heading: '4. Лимиты использования', body: 'Спам, незаконные товары/услуги или нарушения прав третьих лиц приводят к блокировке. Лимиты указаны на странице тарифов.' },
          { heading: '5. Право собственности на данные', body: 'Все загруженные вами данные клиентов и платежей остаются вашей собственностью. Экспорт CSV/PDF доступен в любой момент.' },
          { heading: '6. Ограничение ответственности', body: 'Сервис предоставляется «как есть». Muddato не несёт ответственности за прямой или косвенный ущерб; максимальный предел нашей ответственности не превышает суммы, уплаченной вами за последние 12 месяцев.' },
          { heading: '7. Изменения', body: 'О существенных изменениях уведомим минимум за 30 дней. Продолжение использования после изменений = согласие.' },
          { heading: '8. Применимое право и споры', body: 'К настоящим условиям применяется законодательство Республики Узбекистан. Споры, не урегулированные путём переговоров, рассматриваются в компетентном суде г. Ташкента.' },
        ],
      }}
    />
  );
}
