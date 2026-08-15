# Product requirements

Grailed Liquidity Analyzer collects real active and sold Grailed listings, normalizes them, maintains lifecycle state, and ranks model-level liquidity.

The product has one source mode: live. A run requires compliance acknowledgement, current discovered credentials/schema, verified brand mappings, a bounded request budget, explicit coverage reporting, and resumable persistence. Incomplete collection is always visible as partial/truncated.

The default path is direct Algolia over Scrapling HTTP. Browser-mediated Algolia and allowed DOM parsing are fallbacks driven by observed live failures. CAPTCHA, prohibited automation, or repeated throttling stops the run.

Credentials and proxy secrets are masked. Seller identity is not stored in plaintext unless explicitly enabled. Money uses `Decimal`; listings are unique by `grailed_id`; disappearance never implies sale.
