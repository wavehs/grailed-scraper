# Testing

Parser acceptance is live-only. No generated listing, local source substitute, cassette, snapshot, or test double proves compatibility with Grailed.

## Source-independent checks

`pytest` may verify exact money handling, secret masking, migrations, database constraints, and idempotent upsert behavior. Transport and UI units may use narrow test doubles, but they are not parser acceptance evidence.

## Required live gate

After compliance acknowledgement:

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
