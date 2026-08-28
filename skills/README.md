# Sample skills

Example/starter skills. AIDA reads skills from `~/.aida/skills/`, not from
this folder directly.

**These ship inside the installed package too** (`pyproject.toml`
force-includes this folder as `aida/resources/skills`), so a
`pip install aida-workbench` user has them without a repo checkout — see
`aida.config.paths.bundled_skills_dir`. To get them into place:

- `aida mcp add-pyirena` installs `saxs-basics` and `pyirena-usage`
  automatically, since it attaches both to the server it configures.
- Anything already in `~/.aida/skills/` is **never overwritten** — once a
  skill is there it's yours, tailored to your beamline, and an AIDA upgrade
  must not replace it.

Or copy/symlink by hand:

```bash
cp skills/saxs-basics.md skills/pyirena-usage.md skills/review-checklist.md ~/.aida/skills/
# or, to keep them tracked in this repo and edited in place:
ln -s "$(pwd)/skills/saxs-basics.md" ~/.aida/skills/saxs-basics.md
ln -s "$(pwd)/skills/pyirena-usage.md" ~/.aida/skills/pyirena-usage.md
ln -s "$(pwd)/skills/review-checklist.md" ~/.aida/skills/review-checklist.md
```

## What's here

- **`saxs-basics.md`** — general SAXS/USAXS domain conventions (Q units,
  the five analysis approaches, Irena/Igor terminology). Reusable across
  any workspace, not tied to a specific MCP server.
- **`pyirena-usage.md`** — bridges an AIDA workspace's configured
  source/target folders with pyIrena's own MCP tools. Deliberately does
  *not* duplicate pyIrena's fitting-workflow guidance — the pyirena-mcp
  server already provides that itself via its MCP `initialize` handshake
  `instructions` field, which AIDA now surfaces directly into the system
  context automatically whenever that server is connected (see
  `planning/phase07_mcp_management.md`'s post-delivery notes) — no skill
  file needed for that part.
- **`review-checklist.md`** — a **draft, not a finished skill**: a generic
  scientific-document review checklist for the `perform-reviews` workspace.
  The workspace has no MCP tools attached, so what makes a good review
  depends entirely on what's actually being reviewed (manuscript, beamtime
  proposal, internal report, ...) — read the file's own header before
  relying on it and tailor the checklist to your real review type.

These three match the skill names already referenced in a `workspaces.yaml`
using `use-pyirena` (`saxs-basics`, `pyirena-usage`) and `perform-reviews`
(`review-checklist`) — until they exist under `~/.aida/skills/`,
`aida workspace show`/the GUI's workspace validation warns
`skill file(s) not found (will be skipped)` and the model gets none of
their guidance.
