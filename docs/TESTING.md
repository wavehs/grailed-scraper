# Testing

Parser acceptance is live-only. No generated listing, local source substitute, cassette, snapshot, or test double proves compatibility with Grailed.

## Source-independent checks

`pytest` may verify exact money handling, secret masking, migrations, database constraints, and idempotent upsert behavior. Transport and UI units may use narrow test doubles, but they are not parser acceptance evidence.

## Required live gate

After compliance acknowledgement and discovery:

```powershell
cd backend
python -m app.cli canary --brand "Rick Owens" --limit 50
```

The report must show live T1, real listing identifiers, required schema fields, valid/rejected counts, and no secrets or seller PII. Every parser milestone also runs the smallest bounded live collection that exercises its changed path and reports coverage, duplicates, truncation, and resource cleanup.

Stop with `HOLD` on automation prohibition, CAPTCHA, repeated 429, missing credentials, or an incomplete result that is not explicitly marked partial/truncated.

## AI grouping gate

Source-independent tests cover type boundaries, deduplication, prompt privacy/injection,
malformed JSON, candidate allowlists, Decimal budget stops, persisted Batch resume,
atomic apply and rollback. A real Gemini check is a 100-item canary; only a complete
structured response permits the remaining historical rollout. It requires an explicit
UI budget confirmation, caps the canary at `$0.50`, and caps canary plus the historical
rollout at `$5.00`. It never replaces the bounded live Grailed gate above.
