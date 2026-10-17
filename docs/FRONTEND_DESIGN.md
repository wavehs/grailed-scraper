# Дизайн-система фронтенда

Интерфейс повторяет стиль Linear (тёмная тема, плотные списки, волосяные границы, один акцентный цвет). Тема только тёмная. Этот документ — правила для новых экранов; код в `frontend/src` — источник истины.

## Токены

Все цвета и размеры — CSS-переменные в `src/app/globals.css`; экраны читают только их, никаких сырых hex.

| Группа | Переменные | Назначение |
|---|---|---|
| Поверхности | `--bg-base`, `--bg-sidebar`, `--bg-surface`, `--bg-surface-raised`, `--bg-surface-hover`, `--bg-control(-hover)`, `--bg-selected` | от фона страницы до контролов |
| Границы | `--border-subtle`, `--border-default`, `--border-strong` | волосяные линии 1px |
| Текст | `--text-primary`, `--text-body`, `--text-secondary`, `--text-muted` | четыре уровня акцента |
| Акцент | `--accent`, `--accent-hover`, `--accent-soft`, `--accent-contrast` | кнопка Primary, выбранные чипы, фокус |
| Статусы | `--success/warning/danger/info` и `-bg`, `-border` | цвет живёт в точке бейджа или тексте |
| Размеры | `--sidebar-width` 220px, `--topbar-height` 44px, `--row-height` 40px | |

Tailwind-алиасы (`bg-surface`, `text-fg-muted`, `border-line` и т.д.) объявлены в `tailwind.config.ts` и указывают на те же переменные.

Типографика: Inter, базовый размер 13px; заголовки секций — `text-[13px] font-medium text-primary`; подписи и метаданные — `text-xs text-muted`. Без uppercase-заголовков и без жирного крупного текста. Радиусы: 5px контролы, 6px (`rounded-md`) панели, 8px (`rounded-lg`) группы настроек и всплывающие меню.

## Каркас

- `AppSidebar` — 220px, разделы «Аналитика» и «Управление», «Настройки» прижаты книзу; на телефоне — выезжающая панель с верхней полосой 44px.
- `main` имеет отступы `px-4 md:px-6 pt-4 pb-10`. `PageHeader` (липкий, 44px) компенсирует их через `-mt-4 -mx-4 md:-mx-6`; полноширинные полосы — `-mx-4 md:-mx-6 px-4 md:px-6 border-b`.

## Примитивы (`src/components/ui`)

| Компонент | Когда использовать |
|---|---|
| `PageHeader` | заголовок экрана, описание, действия справа, хлебные крошки |
| `Toolbar` | полоса фильтров под заголовком |
| `SearchInput`, `.control-sm` | поиск и селекты высотой 28px в тулбаре |
| `.chip` (`aria-pressed`) | фильтр-переключатель, выбор брендов |
| `Segmented` | взаимоисключающие режимы (уровень, окно, вердикт) |
| `Popover`, `PopoverSection`, `PopoverRow`, `MenuCheckbox` | меню «Фильтр», «Вид», сканы брендов |
| `DataTable` (`bleed`), `TableRow`, `TableCell` | списки на всю ширину, строки 40px |
| `Pager` | «Назад/Далее» с диапазоном строк |
| `ContentSection`, `PropertyList`, `Property` | блоки детальной страницы и панель свойств справа |
| `InlineStat` | метрика в строку (подпись + значение) |
| `Badge`, `StatusDot`, `statusVariant` | статус: цветная точка + нейтральный текст |
| `Button` (`primary/secondary/ghost/success/danger`, `size="sm"`) | действия |
| `Switch` | булевы настройки (роль `switch`, `aria-label`) |
| `Modal`, `HelpTip`, `ProgressBar`, `Card` | диалоги, подсказки, прогресс сбора, простая панель |
| `SettingsGroup`, `SettingRow` (`components/settings`) | экран настроек: строка «название слева — контрол справа» |

## Шаблоны экранов

- **Список** (Каталог, Отслеживаемые, Находки, Тренды): `PageHeader` → `Toolbar` → `DataTable bleed` → `Pager`.
- **Деталь** (Группа): основная колонка из `ContentSection` + правая панель 300px с `PropertyList`.
- **Мастер-деталь** (Бренды): список 340px слева, деталь справа.
- **Настройки**: колонка 640px из `SettingsGroup`.

## Правила

- Тексты только через `t(key)`; ключ обязан быть в `en` и `ru` (`src/lib/i18n.test.tsx` проверяет литеральные ключи; для динамических — `t(\`prefix_${value}\`)` — добавляйте все значения вручную).
- Новые контролы должны иметь доступное имя (`aria-label` или `<label htmlFor>`): тесты ищут их через `getByLabelText`.
- Проверка: `make frontend-check` (eslint, tsc, vitest, `next build`). `pnpm build` перезаписывает `.next` запущенного `pnpm dev` — после сборки перезапустите dev-сервер.
