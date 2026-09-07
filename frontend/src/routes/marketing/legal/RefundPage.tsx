import { LegalPage } from './LegalPage';

export default function RefundPage() {
  return (
    <LegalPage
      uz={{
        kicker: 'YURIDIK',
        title: "Pul qaytarish siyosati",
        intro: "Muddato obuna to'lovlari uchun pul qaytarish shartlari.",
        sections: [
          { heading: "1. Demo davri", body: "Demo davri Muddato bilan kelishiladi. Demo davrida hech qanday to'lov olinmaydi. Demo tugagandan keyin obuna bo'lmasangiz akkaunt o'qishga (read-only) o'tadi." },
          { heading: "2. Birinchi 7 kun", body: "Tarif sotib olganingizdan keyin 7 kun ichida sababini ko'rsatmasdan to'liq pul qaytarib olishingiz mumkin." },
          { heading: "3. Nisbiy (pro-rata) qaytarish", body: "Yillik obuna olib, muddat tugashidan oldin to'xtatsangiz, ishlatilmagan to'liq oylar uchun pulni qaytarib beramiz." },
          { heading: "4. Jarima yo'q", body: "Bekor qilish uchun jarima qo'ymaymiz. Faqat avval olingan to'lov qaytariladi." },
          { heading: "5. Qanday so'raysiz", body: "@MuddatoSupport'ga yozing yoki refund@muddato.uz manzilini ishlating — 5 ish kuni ichida hisobingizga qaytariladi." },
        ],
      }}
      ru={{
        kicker: 'ЮРИДИКА',
        title: 'Политика возврата средств',
        intro: 'Условия возврата за подписку Muddato.',
        sections: [
          { heading: '1. Демо-период', body: 'Срок демо согласуется с Muddato. Во время демо платежи не списываем. После окончания без оплаты аккаунт переходит в режим чтения.' },
          { heading: '2. Первые 7 дней', body: 'В течение 7 дней после оплаты — полный возврат без объяснения причин.' },
          { heading: '3. Пропорциональный возврат', body: 'При досрочном прекращении годовой подписки возвращаем стоимость неиспользованных полных месяцев.' },
          { heading: '4. Без штрафов', body: 'Штрафов за отмену нет. Возвращается только ранее уплаченная сумма.' },
          { heading: '5. Как запросить', body: 'Напишите @MuddatoSupport или refund@muddato.uz — возврат в течение 5 рабочих дней.' },
        ],
      }}
    />
  );
}
