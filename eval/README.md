# Эталон группировки

Одна папка на бренд (`eval/<brand-slug>/`). Всё здесь ревьюится в PR, как код. Метрики и
порядок запуска — `docs/TESTING.md`, определения модели — `docs/GROUPING.md`, раздел 0.

| Файл | Что хранит |
|---|---|
| `eval.yaml` | протокол: контрольные точки, топ-N, хвост, цели |
| `labels.csv` | метки объявлений |
| `phrases.csv` | вердикты по линейкам (тип + slug) |
| `canon.csv` | принятые написания канонических моделей (правило 6) |
| `MANIFEST` | sha256 меток, дата, кто размечал, внешняя проверка |
| `holdout_runs.csv` | журнал запусков на холдауте (пишет `grouping-eval`) |

Заголовков, категорий и продавцов в файлах нет: `grouping-eval` берёт их из базы по
`grailed_id`. Объявление, которого нет в базе, считается `missing` и видно в отчёте.

## `labels.csv`

`grailed_id,gold_type,gold_model,gold_collab,borderline,note`

- `gold_model`:
  - модель — каноническое имя, как в seed бренда (`Triple S`, `Le Cagole`);
  - версия — `Линейка > Версия` (`City > Mini City`, `Track > Track 2`); основная метрика
    сравнивает линейку, строгая — версию;
  - `NONE` — ни модели, ни описания;
  - `DESCRIPTOR:<класс>` — описание без модели; класс из таблицы в `docs/GROUPING.md`
    (`fit`, `size`, `material`, `print`, `detail`, `optics`, `subtype`, `style`,
    `brandmark`). Если описаний несколько — самое заметное, при равенстве первое в заголовке.
- `gold_collab` — партнёр коллаборации (`Yeezy Gap`, `Adidas`, `Gucci`), иначе пусто. С
  моделью («Yeezy Gap Dove Hoodie»): `gold_model = Dove`, `gold_collab = Yeezy Gap`.
- `borderline = 1` — разумны два ответа (несколько моделей в заголовке, лот, неясное
  слово). Такие строки не входят в целевые метрики и печатаются отдельно.
- `gold_type` — тип из `config/taxonomy.yaml`, необязателен; заполняется, только если
  разметчик проверял тип.
- `note` — причина для пограничных случаев и переразметки; вещи чужого бренда — `foreign`.

## `phrases.csv`

`product_type,slug,name,verdict,note`

- `model` — модель;
- `not_model:<класс>` — не модель (класс описания или `fragment`, `template`, `foreign`);
- `duplicate:<канон>` — дубль или обрывок существующей модели;
- `borderline` — пограничное.

## `canon.csv`

`canon,aliases` — каноническое имя и его написания через `|`: опечатки, слитно, раздельно,
через дефис, сокращения (`Le Cagole,Cagole`, `World Food Programme,WFP`). Линейка с таким
именем считается верной для метки канона. Обрывки сюда не входят (правило 7): `Campaign` —
не написание `Political Campaign`, а отдельная, неверная линейка.

## Как размечать

Листы для разметки слепые: только заголовок, категория и дизайнеры объявления, либо фраза и
8 случайных проданных заголовков. Текущая группа, статус, ранг и счётчики не показываются.

```
python -m app.cli grouping-eval-sample --brand balenciaga --out ../data/cache/eval/round1
python -m app.cli grouping-eval-sample --brand balenciaga --import <заполненный лист>
python -m app.cli grouping-eval --brand balenciaga --seal --labeler "<кто>" --external-check "<кто проверял | none>"
```

Импорт добавляет только новые строки и никогда не заменяет существующие: переразметка — это
правка файла отдельным коммитом с причиной в `note`.
