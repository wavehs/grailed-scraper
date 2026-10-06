---
paths:
  - "backend/tests/**/*.py"
---

# Backend test rules

- pytest runs with `--strict-markers`, and `addopts` deselects `browser` and `integration`. Any test that touches an external service must carry the `integration` marker.
- Never add Grailed or Algolia fixtures, replays, or fake servers. Source-independent unit tests are fine.
- `tests/conftest.py` points `APP_DATA_DIRECTORY`, `APP_LOG_DIRECTORY` and `APP_DATABASE_URL` at a temp dir before the app is imported, so tests never touch the developer's real DB or lock.
- mypy strict covers the tests too. Annotate helpers and fakes; an untyped helper triggers `no-untyped-call`. Pass secrets as `SecretStr`.
- Temp files go in `--basetemp=.test-tmp`. Close SQLite connections and engines explicitly; Windows keeps file handles open, and the autouse `gc.collect()` fixture is only a backstop.
