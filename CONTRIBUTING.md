# Contributing to AIDA

AIDA is early-stage (beta) and currently developed against a phased plan
in `PLAN.md` and `planning/`. Before opening a PR:

1. Check [`PLAN.md`](PLAN.md) §1 ("Planned") and the relevant
   `planning/phaseNN_*.md` checklist file — work should map to an unchecked
   task there. What has already shipped, and why, is in
   [`planning/COMPLETED.md`](planning/COMPLETED.md), not `PLAN.md` — that
   file holds only what is not done.
2. Keep the layering rules in [`planning/DESIGN.md`](planning/DESIGN.md) §3
   intact: `aida.core`, `aida.providers`, `aida.mcp`, `aida.workspace`,
   `aida.knowledge`, and `aida.persistence` must never import Qt. Only
   `aida.ui.qt` may, and only through `_qt.py`.
3. Add or update tests under `tests/` for any behavior change.
4. Run locally before pushing:

   ```bash
   pip install -e ".[dev]"
   ruff check .
   pytest
   ```

5. Update the corresponding phase checklist file in the same commit as the
   work it tracks (see `PLAN.md`'s "Working agreements" paragraph near the
   top).
6. Add a bullet under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) for
   any user-visible change (new capability, behavior change, bug fix) — this
   is what turns into that release's notes; it's cheap to add now and
   tedious to reconstruct later from commit messages.

Questions or design discussion: open a GitHub issue on
[jilavsky/aida](https://github.com/jilavsky/aida).

## Cutting a release

1. Move `## [Unreleased]`'s bullets in `CHANGELOG.md` under a new
   `## [x.y.zbN] - YYYY-MM-DD` heading (keep an empty `Unreleased` section
   above it), and add its compare-link at the bottom of the file.
2. Bump the version in both `pyproject.toml` (`version =`) and
   `src/aida/__init__.py` (`__version__ =`) — they must match.
3. Update the `**Status: ...**` line at the top of `README.md`,
   `PLAN.md`, and every file under `docs/` to the new version (and date, in
   `PLAN.md`).
4. Commit, tag `vX.Y.ZbN`, push the tag.
