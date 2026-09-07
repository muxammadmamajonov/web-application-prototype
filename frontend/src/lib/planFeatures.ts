/** Canonical plan feature + limit metadata (mirror of the backend R39 set).
 *  Drives the admin plan editor and the landing comparison matrix so the UI
 *  stays data-driven and free of plan-name hardcoding. */

export type LimitKey = 'contract' | 'staff' | 'branch' | 'custom_field' | 'broadcast';

export const LIMIT_KEYS: LimitKey[] = ['contract', 'staff', 'branch', 'custom_field', 'broadcast'];

export const LIMIT_LABEL: Record<LimitKey, { uz: string; ru: string }> = {
  contract:     { uz: 'Faol shartnomalar', ru: 'Активные договоры' },
  staff:        { uz: 'Xodimlar', ru: 'Сотрудники' },
  branch:       { uz: 'Filiallar', ru: 'Филиалы' },
  custom_field: { uz: 'Maxsus maydonlar', ru: 'Кастомные поля' },
  broadcast:    { uz: 'Eʼlon / oy', ru: 'Рассылки / мес' },
};

interface FeatureDef { key: string; uz: string; ru: string; group: string }

export const FEATURES: FeatureDef[] = [
  // baseline
  { key: 'telegram_bot', uz: 'Telegram bot', ru: 'Telegram-бот', group: 'core' },
  { key: 'telegram_webapp', uz: 'Telegram WebApp', ru: 'Telegram WebApp', group: 'core' },
  { key: 'customer_management', uz: 'Mijozlar boshqaruvi', ru: 'Управление клиентами', group: 'core' },
  { key: 'installments', uz: 'Shartnomalar boshqaruvi', ru: 'Управление договорами', group: 'core' },
  { key: 'payment_schedule', uz: "To'lov jadvali", ru: 'График платежей', group: 'core' },
  { key: 'reminders', uz: 'Avtomatik eslatmalar', ru: 'Автонапоминания', group: 'core' },
  { key: 'click', uz: 'Click to‘lovlar', ru: 'Click оплата', group: 'core' },
  { key: 'payme', uz: 'Payme to‘lovlar', ru: 'Payme оплата', group: 'core' },
  { key: 'cash', uz: 'Naqd to‘lov', ru: 'Наличная оплата', group: 'core' },
  { key: 'basic_dashboard', uz: 'Asosiy panel', ru: 'Базовая панель', group: 'core' },
  { key: 'basic_reports', uz: 'Asosiy hisobotlar', ru: 'Базовые отчёты', group: 'core' },
  { key: 'excel_export', uz: 'Excel eksport', ru: 'Экспорт в Excel', group: 'core' },
  { key: 'card_detection', uz: 'Karta turini aniqlash', ru: 'Определение типа карты', group: 'core' },
  { key: 'payment_history', uz: "To'lovlar tarixi", ru: 'История платежей', group: 'core' },
  // business
  { key: 'audit_logs', uz: 'Audit jurnali', ru: 'Журнал аудита', group: 'business' },
  { key: 'pdf_export', uz: 'PDF eksport', ru: 'Экспорт PDF', group: 'business' },
  { key: 'better_reports', uz: 'Yaxshilangan hisobotlar', ru: 'Улучшенные отчёты', group: 'business' },
  { key: 'employee_roles', uz: 'Xodim rollari', ru: 'Роли сотрудников', group: 'business' },
  { key: 'staff_activity', uz: 'Xodim faoliyati', ru: 'Активность сотрудников', group: 'business' },
  { key: 'basic_permissions', uz: 'Asosiy ruxsatlar', ru: 'Базовые права', group: 'business' },
  { key: 'support_standard', uz: 'Standart qo‘llab-quvvatlash', ru: 'Стандартная поддержка', group: 'business' },
  // pro
  { key: 'multi_branch', uz: 'Ko‘p filial boshqaruvi', ru: 'Мультифилиальность', group: 'pro' },
  { key: 'advanced_reports', uz: 'Kengaytirilgan hisobotlar', ru: 'Расширенные отчёты', group: 'pro' },
  { key: 'commission', uz: 'Komissiya hisoblash', ru: 'Расчёт комиссии', group: 'pro' },
  { key: 'pdf_template_upload', uz: 'PDF shablon yuklash', ru: 'Загрузка PDF-шаблонов', group: 'pro' },
  { key: 'risk_monitoring', uz: 'Risk monitoringi', ru: 'Мониторинг рисков', group: 'pro' },
  { key: 'advanced_permissions', uz: 'Kengaytirilgan ruxsatlar', ru: 'Расширенные права', group: 'pro' },
  { key: 'branch_analytics', uz: 'Filial analitikasi', ru: 'Аналитика по филиалам', group: 'pro' },
  { key: 'manager_dashboard', uz: 'Menejer paneli', ru: 'Панель менеджера', group: 'pro' },
  { key: 'support_high', uz: 'Yuqori prioritet qo‘llab-quvvatlash', ru: 'Приоритетная поддержка', group: 'pro' },
  // enterprise
  { key: 'api_keys', uz: 'API kalitlar', ru: 'API-ключи', group: 'enterprise' },
  { key: 'webhooks', uz: 'Webhooklar', ru: 'Вебхуки', group: 'enterprise' },
  { key: 'custom_domain', uz: 'Maxsus domen', ru: 'Свой домен', group: 'enterprise' },
  { key: 'white_label', uz: 'White label', ru: 'White label', group: 'enterprise' },
  { key: 'custom_integrations', uz: 'Maxsus integratsiyalar', ru: 'Кастомные интеграции', group: 'enterprise' },
  { key: 'account_manager', uz: 'Shaxsiy menejer', ru: 'Персональный менеджер', group: 'enterprise' },
  { key: 'sla', uz: 'SLA qo‘llab-quvvatlash', ru: 'SLA-поддержка', group: 'enterprise' },
  { key: 'custom_onboarding', uz: 'Maxsus onboarding', ru: 'Индивидуальный онбординг', group: 'enterprise' },
];
