# Полный Редизайн UI — Grailed Liquidity Analyzer

## Проблемы текущего интерфейса

Текущий UI — это минималистичный «скелет»: белый фон, базовые `slate` цвета, `border` вокруг карточек, голые `<table>` без визуальной иерархии. Нет иконок на кнопках, нет цветовых акцентов по состояниям, нет визуального разделения зон ответственности. Для пользователя непонятно что важно, а что второстепенно — всё выглядит одинаково.

## Концепция редизайна

**Тёмная тема** с акцентными градиентами, стеклянными карточками (glassmorphism), иконками Lucide на каждой кнопке и навигации, цветовыми бейджами статусов, микро-анимациями. Интерфейс должен ощущаться как премиальный SaaS-продукт.

### Дизайн-система

| Элемент | Реализация |
|---|---|
| **Фон** | Тёмный радиальный градиент `#0a0a12` → `#111827` |
| **Карточки** | Glass: `rgba(255,255,255,0.04)` backdrop-blur, мягкий border `rgba(255,255,255,0.08)` |
| **Акценты** | Градиент от `#6366f1` (indigo) до `#8b5cf6` (violet) для CTA |
| **Позитивные метрики** | Emerald `#10b981` → `#34d399` |
| **Негативные/ошибки** | Rose `#f43f5e` → `#fb7185` |
| **Предупреждения** | Amber `#f59e0b` → `#fbbf24` |
| **Текст** | Основной `#e2e8f0`, вторичный `#94a3b8`, приглушённый `#64748b` |
| **Шрифт** | Inter (Google Fonts) |
| **Анимации** | `transition-all duration-200`, hover-glow на карточках |

---

## Предлагаемые изменения

### Design System

#### [MODIFY] [globals.css](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/globals.css)
Полная переделка: тёмная тема, CSS-переменные дизайн-системы, кастомные скроллбары, анимации, шрифт Inter через Google Fonts import.

#### [MODIFY] [tailwind.config.ts](file:///c:/Users/Alex/Documents/IDE/parser/frontend/tailwind.config.ts)
Расширение палитры: добавить кастомные цвета `glass`, `surface`, `accent`, анимации `fade-in`, `slide-up`.

---

### UI Components

#### [MODIFY] [card.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/card.tsx)
Glassmorphism-стиль: полупрозрачный фон, blur, мягкая тень, hover-эффект подъёма.

#### [MODIFY] [button.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/button.tsx)
Вариантная система: `primary` (градиент indigo→violet), `secondary` (glass), `danger` (rose), `ghost`. Все с иконками, плавными hover-переходами.

#### [NEW] [badge.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/badge.tsx)
Цветные бейджи для статусов: ready/running/completed/failed/degraded etc. Каждый статус — свой цвет + dot-индикатор.

#### [NEW] [stat-card.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/stat-card.tsx)
Карточка метрики с иконкой, заголовком, значением и опциональным трендом. Заменяет текущие безликие `<Card className="p-4">`.

#### [NEW] [page-header.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/page-header.tsx)
Заголовок страницы с градиентным текстом, описанием, опциональными actions справа.

#### [NEW] [data-table.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/data-table.tsx)
Стилизованная обёртка для таблиц: sticky header, zebra-row hover, красивые заголовки.

#### [NEW] [progress-bar.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/progress-bar.tsx)
Градиентный progress bar с анимацией и процентом.

#### [NEW] [modal.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/modal.tsx)
Стилизованный модальный диалог с backdrop-blur, анимацией входа/выхода.

#### [NEW] [input.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/input.tsx)
Стилизованные Input, Select, Checkbox — единый стиль, тёмный фон, focus-ring.

#### [NEW] [tooltip.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/ui/tooltip.tsx)
Тултипы для объяснения малопонятных терминов (Tier, Coverage, Opportunity score и т.д.).

---

### Layout

#### [MODIFY] [layout.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/layout.tsx)
Подключение Google Font Inter, dark-themed body, структура с новым sidebar.

#### [MODIFY] [app-sidebar.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/app-sidebar.tsx)
Полная переделка: glass-боковая панель, иконки Lucide для каждого пункта, активный пункт с градиентной подсветкой, логотип сверху, переключатель языка снизу. Группировка пунктов по смыслу (Analytics / Management / System).

#### [MODIFY] [health-banner.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/health-banner.tsx)
Переделка в sticky-топбар с иконками AlertTriangle/XCircle, цветами по статусу.

#### [MODIFY] [states.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/states.tsx)
Скелетон-лоадер вместо текста «Loading…», красивые empty/error состояния с иконками.

---

### Страницы

#### [MODIFY] [dashboard.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/components/dashboard.tsx)
- Stat-карточки с иконками вместо безликих `<Card>`
- Таблица моделей — через DataTable с hover-эффектами
- Бейджи для Opportunity Score (цвет по диапазону)
- Фильтры и поиск — стилизованные Input/Select
- Секция Recent Runs — с бейджами статусов

#### [MODIFY] [brands/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/brands/page.tsx)
- Карточки брендов с цветными бейджами (verified/review/unresolved)
- Таблицы маппингов — через DataTable
- Кнопки confirm/reject — стилизованные Button с иконками Check/X
- PageHeader

#### [MODIFY] [parser-runs/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/parser-runs/page.tsx)
- Workflow-шаги в виде визуального stepper с иконками и линиями-коннекторами
- Таблица запусков — DataTable с бейджами статусов/фаз
- Модальное окно прогресса — через Modal компонент, с ProgressBar
- Budget preview — стилизованный с цветными индикаторами

#### [MODIFY] [settings/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/settings/page.tsx)
- Группы настроек с иконками заголовков
- Поля ввода — через стилизованные Input/Select/Checkbox
- Секции прокси и discovery — карточки с иконками

#### [MODIFY] [model-rules/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/model-rules/page.tsx)
- Форма создания — стилизованная
- Карточки правил с бейджами active/inactive, кнопки с иконками

#### [MODIFY] [identity-review/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/identity-review/page.tsx)
- Карточки сравнения — боковая компоновка с visual diff
- Кнопки решения — контрастные с иконками

#### [MODIFY] [model-groups/[id]/page.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/model-groups/%5Bid%5D/page.tsx)
- Графики Recharts в тёмной теме
- Score breakdown с визуальными прогресс-барами

#### [MODIFY] [error.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/error.tsx), [loading.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/loading.tsx), [not-found.tsx](file:///c:/Users/Alex/Documents/IDE/parser/frontend/src/app/not-found.tsx)
- Стилизованные экраны ошибок / загрузки / 404 в тёмной теме

---

## Не затрагиваем

- `lib/api.ts`, `lib/queries.ts`, `lib/types.ts`, `lib/utils.ts`, `lib/i18n.tsx` — логика и данные остаются
- `providers.tsx` — не трогаем
- Backend — без изменений

## План верификации

### Сборка
```bash
cd frontend && pnpm build
```
Проект должен собираться без ошибок.

### Визуальная проверка
Запуск `pnpm dev` и ручная проверка всех страниц в браузере.
