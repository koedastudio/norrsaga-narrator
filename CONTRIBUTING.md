# Contributing

- `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` are
  the merge gate. New behaviour needs tests; ABS and Kokoro are mocked at the
  HTTP transport, see `tests/test_api.py`.
- No new dependencies without discussion in an issue first.
- Anything that changes extracted text or segment boundaries must bump
  `EXTRACTOR_VERSION` in `narrator/epub.py`, or cached books narrate the wrong text.
- Keep the HTTP API backwards compatible; the Norrsaga apps depend on it.
- Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`).
- Comments explain *why*, briefly. The code explains *what*.
