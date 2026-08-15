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
