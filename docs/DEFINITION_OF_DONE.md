# Definition of Done

The parser is done only when the bounded live workflow processes the selected brands sequentially with measured coverage, explicit truncation, no duplicate `grailed_id`, correct lifecycle transitions, resumable tasks, and no leaked credentials or seller PII. A 21-brand run is optional and must still obey the per-brand item limit.

T1 is the default. T2/T3 are accepted only when a live failure requires escalation and the run reports degraded mode. Source-independent tests, lint, type checking, migrations, backup/restore, and Windows startup must be green, but none replaces live acceptance.
