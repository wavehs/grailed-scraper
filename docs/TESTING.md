# Testing

Parser acceptance is live-only. No generated listing, local source substitute, cassette, snapshot, or test double proves compatibility with Grailed.

## Source-independent checks

`pytest` may verify exact money handling, secret masking, migrations, database constraints, and idempotent upsert behavior. Transport and UI units may use narrow test doubles, but they are not parser acceptance evidence.

CI runs `pytest --cov`. Line coverage of `backend/app` must stay at or above the
`fail_under` floor in `backend/pyproject.toml` (73%; measured 74% on 2026-10-06). Raise the
floor when coverage grows and never lower it to land a change. Coverage counts
source-independent checks only and never replaces the live gate below.

## Required live gate

```powershell
cd backend
python -m app.cli discover
python -m app.cli canary --brand "Rick Owens" --limit 50
python -m app.cli taxonomy-check
```

`discover` reads the public search key from `window.PUBLIC_CONFIG` and probes indices
and facets; `taxonomy-check` lists real `category_path` values missing from
`config/taxonomy.yaml` (one request).

The report must show real listing identifiers, required schema fields, valid/rejected counts, and no secrets or seller PII. Every parser milestone also runs the smallest bounded live collection that exercises its changed path and reports coverage, duplicates, truncation, and resource cleanup.

Stop with `HOLD` on automation prohibition, CAPTCHA, repeated 429, missing credentials, or an incomplete result that is not explicitly marked partial/truncated.

## Grouping quality

After a live collection, `python -m app.cli grouping-report [--brand NAME]` prints per
brand the share of listings with a model, "No model" and review counts, the top groups,
and `mixed_type_assignments`, which must be 0. Check a random sample of 100 assignments
per brand by hand before trusting a new dictionary. Grouping and relist rules have
source-independent unit tests (`tests/test_grouping*.py`).

## Grouping evaluation (`grouping-eval`)

Plan grouping-v7 measures grouping against hand labels in `eval/<brand>/` (format:
`eval/README.md`; definitions: `docs/GROUPING.md`, section 0). The commands only read the
database (`mode=ro`): the working one or a copy selected with `APP_DATABASE_URL`.

```powershell
cd backend
python -m app.cli grouping-eval --brand balenciaga                       # dev split
python -m app.cli grouping-eval --brand balenciaga --strict --baseline-db ..\data\backups\<before>.sqlite3
python -m app.cli grouping-eval --brand balenciaga --split holdout --checkpoint v7a --strict --baseline-db <before>
python -m app.cli grouping-eval-sample --brand balenciaga --out ..\data\cache\eval\<round>
```

**Splits.** `sha256(grailed_id) % 5 ∈ {0, 1}` is the holdout (about 40%). `--split holdout`
requires `--checkpoint` from `eval.yaml` (`0`, `v7a`, `v7b`); every holdout run appends a
row (date, commit, label sha256, aggregates) to `eval/<brand>/holdout_runs.csv`, which is
committed, so the number of holdout runs is visible in review. (`holdout_runs.log` would be
ignored by the repository's `*.log` rule.)

**Listing metrics.** The unit is a labeled listing; sold rows have weight 1, so the uniform
sold sample is sales-weighted. A listing is *right* when the system's root line has the
gold line's name or alias (spelling-insensitive: case, accents, spaces, hyphens, plurals);
`Mini City` and `City` are one line. The strict variant compares versions and is printed,
not targeted. Rows with `borderline = 1` are excluded and listed apart.

| Metric | Definition |
|---|---|
| `model_precision` | rows the system put into a `model` group (minus collab-only rows): share in the right line |
| `recall_line` | rows whose gold is a model: share in the right line; target "not below the baseline" |
| `recall_regression` | rows the baseline placed right (`--baseline-db`): share still right |
| `none_precision` | rows in `none` or `descriptor` groups: share whose gold is `NONE`/`DESCRIPTOR` without a collab |
| `descriptor_precision` | rows in `descriptor` groups: share whose gold is `DESCRIPTOR` |
| `collab_precision`, `collab_recall` | rows with a collab partner and no model |

Every share carries a 95% Wilson interval. A share target passes when the point meets it
**and** the interval bound is within 5 p.p. of it. These targets come from the prototype
with a margin, so they check feasibility; the product bar is the customer's decision.

**Phrase metrics.** Model lines (versions included; `descriptor`, `collab`, `none` and
`review` groups are not models) are ranked by sales. For the top 50/100/200,
`phrases.csv` verdicts give the garbage (`not_model`) and garbage+duplicate shares by
lines and by sales. The tail estimate draws 50 lines past rank 200 with replacement in
proportion to sales (seed in `eval.yaml`), so the garbage share among draws estimates the
garbage share of tail sales.

**Checkpoint artifacts.** With `--baseline-db` the report lists *lost groups*: former model
groups with sales that are gone and whose sales got no model. Every row with ≥ 10 lost
sales must be labeled in `phrases.csv`; real-model losses must stay ≤ 60 sales.
`collab_watch` counts sold Yeezy Gap listings without a model.

**Warnings and `--strict`.** Unlabeled lines in the top 100, a label sha256 that differs
from `MANIFEST`, missing labeled listings, a missing baseline and unlabeled lost groups
are warnings; `--strict` (every checkpoint) turns them into a failure. In CI the
evaluation runs on a test fixture (`tests/test_grouping_eval.py`).
