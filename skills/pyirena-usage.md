# Using pyIrena MCP in this AIDA workspace

pyIrena's own MCP server already tells you, at connection time, exactly how
its tools work — its `pyirena-mcp` server instructions (visible earlier in
this system context, right below the "Workspace folders" section if the
server started successfully) describe every tool, the five fitting
workflows (Unified Fit, Sizes, Simple Fits, Modeling, WAXS Peak Fit), and
the step-by-step call sequence for each. **Follow those directly for the
mechanics of driving a fit** — this skill only adds what that description
can't know: how pyIrena's tools fit into *this* AIDA workspace.

## Your folders, and pyIrena's tools

The "Workspace folders" block earlier in this context names your actual
configured source folder(s) and target folder for this session — use those
paths, don't guess or ask the user to repeat them.

- To find and understand SAS data, use pyIrena's own discovery tools —
  `pyirena_summarize_folder(folder=...)` (cheap orientation: file count,
  samples, which analyses are present) and `pyirena_list_files(folder=...)`
  — pointed at one of your **source folders**, not AIDA's generic
  `list_directory`/`find_files`. pyIrena's tools understand the HDF5
  structure (which analyses a file contains, sample names, scan numbers);
  the generic file tools only see raw filenames.
- Use `pyirena_inspect_file(path=...)` before assuming a file has the
  analysis you need — not every reduced file has every fit type run on it.
- When asked to survey "recent data" or "the latest scans," prefer
  `pyirena_list_files(..., sort="mtime_desc")` or
  `pyirena_tabulate_parameter(...)` over reading files one at a time.

## Writing results back

- Save the analysis as a report with `write_markdown_report(path=..., title=..., body=..., image_artifact_ids=[...])`,
  with `path` under your **target folder**. This is the default output
  format for this workspace — don't write a plain `.txt` summary when a
  Markdown report with embedded plots is expected.
- `pyirena_plot_iq(...)` and `pyirena_plot_parameter_trend(...)` return an
  image inline — AIDA turns that into an artifact with its own id
  (reported back to you as part of the tool result). Pass that id in
  `write_markdown_report`'s `image_artifact_ids` to embed the actual plot in
  the report; don't describe a plot in words when you can embed it.
- Numeric results worth reporting as text: Rg, fit quality (chi-squared),
  the specific tool/model used, and the Q range the fit covers — a bare
  number with no context about which model produced it or over what range
  isn't a complete answer.

## Conventions (see the `saxs-basics` skill for the full picture)

Q in Å⁻¹, intensity in cm⁻¹ where calibrated, NXcanSAS HDF5 files, and
Irena/Igor terminology (Unified Fit, Size Distribution, Simple Fits,
Modeling, WAXS Peak Fit — not invented alternative names). Results from
pyIrena tools are the same numbers Igor Irena would produce for the same
file and settings — if something looks inconsistent with that, flag it
rather than smoothing it over.

## A note on interactive fitting (`pyirena_ctrl_*` tools)

The control tools are stateful — `pyirena_ctrl_open_dataset()` returns a
`session_id` that every subsequent call in that fit needs. Sessions live
only for the lifetime of the pyIrena MCP server process (this session of
AIDA), and are not saved automatically — call the relevant `..._save_fit()`
tool when a fit is good, or the result only exists in memory. If you're
unsure which of the five control workflows to use, the server's own
instructions already give the decision rule (feature-over-a-range → Simple
Fits; whole multi-level curve → Unified Fit; dilute single population →
Sizes; several components/specific form factor → Modeling; wide-angle
peaks → WAXS Peak Fit) — don't re-derive it here, use that.
