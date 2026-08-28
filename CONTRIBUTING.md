# Contributing to AIDA

AIDA is early-stage (beta) and currently developed against a phased plan
in `PLAN.md` and `planning/`. Before opening a PR:

1. Check `PLAN.md` §10 (phase map) and the relevant `planning/phaseNN_*.md`
   checklist file — work should map to an unchecked task there.
2. Keep the layering rules in `PLAN.md` §3 intact: `aida.core`, `aida.providers`,
   `aida.mcp`, `aida.workspace`, `aida.knowledge`, and `aida.persistence` must
   never import Qt. Only `aida.ui.qt` may, and only through `_qt.py`.
3. Add or update tests under `tests/` for any behavior change.
4. Run locally before pushing:

   ```bash
   pip install -e ".[dev]"
   ruff check .
   pytest
   ```

5. Update the corresponding phase checklist file in the same commit as the
   work it tracks (see `PLAN.md` §11, "Working agreements").

Questions or design discussion: open a GitHub issue on
[jilavsky/aida](https://github.com/jilavsky/aida).
