# Product requirements

Grailed Liquidity Analyzer collects real active and sold Grailed listings, normalizes them, maintains lifecycle state, and ranks model-level liquidity.

The product has one source mode: live. A run requires compliance acknowledgement, current discovered credentials/schema, verified brand mappings, a bounded request budget, explicit coverage reporting, and resumable persistence. Incomplete collection is always visible as partial/truncated.

Data comes from direct Algolia search over plain HTTP, using the public search key from Grailed's page config. There are no browser or DOM fallbacks. CAPTCHA, prohibited automation, or repeated throttling stops the run.

Credentials are masked. Seller identity is not stored in plaintext unless explicitly enabled. Money uses `Decimal`; listings are unique by `grailed_id`; disappearance never implies sale.
