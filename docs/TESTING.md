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

## Local grouping gate

Source-independent checks cover type conflicts, model qualifiers, strict JSON,
truncation, persisted per-input resume, zero external Batch calls and atomic rollback.
Hardware probes with user-provided example strings measure local inference only;
they are not real-listing acceptance or accuracy measurements. Local canary uses at
most 100 existing real listings and no new Grailed requests. Before broad rollout,
review its assignments against human labels and report false merges, missed merges,
and abstentions separately. Successful JSON alone is not quality acceptance.

2026-09-07: live parser gate HOLD. Current Grailed Terms §12 explicitly prohibit
automated extraction; repository instructions require stopping this gate.
No fresh listing collection was performed. This does not block local work on
user-provided data. Source: https://www.grailed.com/about/terms .
