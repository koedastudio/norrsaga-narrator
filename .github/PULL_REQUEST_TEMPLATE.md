<!-- Small, focused PRs are easiest to review. -->

## What & why

<!-- What does this change and what problem does it solve? Link the issue if there is one. -->

## Checklist

- [ ] `uv run pytest` and `uv run ruff check . && uv run ruff format --check .` pass
- [ ] New behaviour has tests
- [ ] No new dependencies (or agreed on in the issue)
- [ ] `EXTRACTOR_VERSION` bumped if extracted text or segment boundaries changed
- [ ] HTTP API stays backwards compatible
