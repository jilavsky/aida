# Contributing to AIDA

AIDA is in daily use at APS beamlines 12-ID/9-ID. The ten phases it was
built in are all delivered; work now is driven by what users hit. Before
opening a PR:

1. Check [`TODO.md`](TODO.md) — the working list of what is open — and
   [`PLAN.md`](PLAN.md) §1, which holds the reasoning behind each item.
   `PLAN.md` §2 is the list of things considered and deliberately *not*
   committed; if your change is there, say in the PR what concrete need
   has appeared. What has already shipped, and why, is in
   [`planning/COMPLETED.md`](planning/COMPLETED.md); the archived
   per-phase checklists are in
   [`planning/completed/`](planning/completed/README.md).
2. Keep the layering rules in [`planning/DESIGN.md`](planning/DESIGN.md) §3
   intact: `aida.core`, `aida.providers`, `aida.mcp`, `aida.workspace`,
   `aida.knowledge`, and `aida.persistence` must never import Qt. Only
   `aida.ui.qt` may, and only through `_qt.py`.
3. Add or update tests under `tests/` for any behavior change.
4. Run locally before pushing:

   ```bash
   pip install -e ".[dev]"
   ruff check .
   ruff format --check .
   pytest
   ```

5. Update `TODO.md` in the same commit as the work it tracks, and add a
   new item to `PLAN.md` rather than growing an existing one's scope
   silently (see its "Working agreements" paragraph near the top). A
   change to a decision recorded in `planning/DESIGN.md` §2 gets a dated
   note appended to that section's changelog.
6. Add a bullet under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) for
   any user-visible change (new capability, behavior change, bug fix) — this
   is what turns into that release's notes; it's cheap to add now and
   tedious to reconstruct later from commit messages.

## Documentation screenshots

The screenshots in `docs/` are made by a script, on a throw-away `AIDA_HOME`
with AIDA's mock provider (no model, no network, no key; your `~/.aida`,
records folder and keychain are not touched). With the `gui` extra installed:

```bash
python scripts/make_doc_screenshots.py                          # every shot
python scripts/make_doc_screenshots.py --only gui-overview      # one page
python scripts/make_doc_screenshots.py --only gui-overview-01-window
python scripts/make_doc_screenshots.py --list
```

Each image is written next to its page (`<page dir>/img/<page>/<id>.png`).
[`planning/docs-screenshots/SHOTLIST.md`](planning/docs-screenshots/SHOTLIST.md)
lists every shot, including the ones still taken by hand (an invisible
`% SCREENSHOT <id>: …` comment in the page until the image exists);
`tests/test_doc_screenshots.py` keeps pages, list, images and the
`scripts/doc_shots/<page>.py` modules in step. Fonts come from the operating
system, so regenerate a page's shots on one machine.

Questions or design discussion: open a GitHub issue on
[jilavsky/aida](https://github.com/jilavsky/aida).

## Cutting a release

1. Move `## [Unreleased]`'s bullets in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading (keep an empty `Unreleased` section
   above it), and add its compare-link at the bottom of the file.
2. Bump the version in both `pyproject.toml` (`version =`) and
   `src/aida/__init__.py` (`__version__ =`) — they must match.
   `tests/test_version_metadata.py` fails if they do not, and
   `publish.yml` re-checks both against the tag before building, because
   PyPI will not let a version be re-uploaded once it is wrong.
3. Update the `**Status: ...**` line at the top of `README.md`, `PLAN.md`,
   and every page under `docs/` that carries one (including `docs/index.md`).
4. Commit, tag `vX.Y.Z`, push the tag, and publish a GitHub release from
   it — `.github/workflows/publish.yml` fires on release publication and
   uploads to PyPI through trusted publishing. Do not run `twine` by hand.
