# Product requirements

Grailed Liquidity Analyzer collects real active and sold Grailed listings, normalizes them, maintains lifecycle state, groups them into brand → product type → model lines and versions ([GROUPING.md](GROUPING.md)), and shows which models are trending: sales growth, time to sell, price and supply ([METRICS.md](METRICS.md)).

The product has one source mode: live. A run requires a one-time compliance acknowledgement, discovered credentials/schema (refreshed automatically), verified brand mappings, explicit coverage reporting, and resumable persistence; speed is bounded by the 90 requests/minute and 3 concurrent request limits. Incomplete collection is always visible as partial/truncated.

Data comes from direct Algolia search over plain HTTP, using the public search key from Grailed's page config. There are no browser or DOM fallbacks. CAPTCHA, prohibited automation, or repeated throttling stops the run.

Credentials are masked. Seller identity is not stored in plaintext unless explicitly enabled. Money uses `Decimal`; listings are unique by `grailed_id`; disappearance never implies sale.
