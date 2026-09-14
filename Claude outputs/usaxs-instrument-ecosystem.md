# The USAXS Instrument Software Ecosystem

**APS 12-ID-E — USAXS / SAXS / WAXS**
Draft for review — September 2026

## 1. What this document is

This describes the full software stack that now supports the USAXS/SAXS/WAXS instrument at APS 12-ID-E, from beam-time control through data reduction, visualization, and analysis. The point worth documenting is not any single tool but the shape of the whole system: every stage of the experiment — controlling the instrument, surveying samples, reducing raw data, plotting it, and analyzing it — can be driven through more than one interface, and a user picks whichever interface suits them (or mixes them) at each step. Command line, desktop GUI, and natural-language AI agent are, for the first time, all first-class ways to do the same work end to end.

This draft is meant to be checked and corrected before it becomes the basis for diagrams, a web page, and eventually a paper.

## 2. The five stages, and the three ways to drive each one

| Stage | Command line | Desktop GUI | AI agent (natural language) |
|---|---|---|---|
| **Instrument control** | Bluesky/IPython console (`usaxs-bits`) | `usaxs-qmonitor` (queue server GUI) | Aida + bait_mcp + epics-mcp |
| **Sample survey** | Exported `.mac` command file | Igor Pro *Setup Sample Plates*, or Matilda's `matilda-sample-plates` | Agent-loaded plan file via queue server |
| **Data reduction/calibration** | `matilda` daemon (automatic, headless) | `matilda-gui` (manual override) | — (automatic; not typically agent-driven) |
| **Data analysis** | pyIrena batch/JSON scripting API | pyIrena GUI (`pyirena-gui` and tool-specific GUIs) | pyIrena MCP server, via Aida or any MCP client |
| **Plotting** | — | Bernardyn (early alpha), Igor Pro | — |

No single row is complete without the others, and no single column has to be used exclusively — a user can survey samples in the GUI, run the actual scan from the command line, and ask an agent to summarize the reduced results, all in the same session.

## 3. Instrument control

**Repository:** `usaxs-bits` (package `bits_usaxs`, importable as `usaxs`), built on the BCDA-APS `apstools`/BITS framework. Replaces the pre-2023 `usaxs-bluesky-ended-2023` codebase.

### 3.1 Command line (the foundation)

The Bluesky RunEngine, device definitions, and all beamline plans (`Flyscan`, `USAXSscan`, `mode_USAXS`, tune plans, etc.) live in this one package, loaded by a single `startup.py` that is the shared entry point for both an interactive IPython console and the headless queue server. This has been the operational way to run USAXS for some time and remains fully available (`./scripts/start-usaxs-bits.sh` for an interactive session).

### 3.2 GUI (queue server + `usaxs-qmonitor`)

Since around July 2026, USAXS has also been driven from a dedicated GUI, `usaxs-qmonitor`, layered on top of `bluesky-widgets` (not a fork of it). At runtime there are three cooperating processes:

- the **RE Manager** (queue server) and its headless **RE Worker**, which hold the live device/plan namespace;
- the **`usaxs-qmonitor` GUI**, which controls the queue over ZMQ (ports 60615 control / 60625 console-status) and separately subscribes to a copy of the live Bluesky document stream (via a 0MQ proxy) to draw tune/alignment plots itself.

The GUI exposes queue control (open/close environment, start/stop/pause), a plan editor and queue table, a running-plan/history panel, `New User…`/`New Sample…`/`Load plan file…` actions, and a live-plot tab (tune_ar, tune_mr, tune_a2rp, tune_dx, tune_dy, last 5 scans overlaid). It has been tested and is working well as of July 2026.

### 3.3 Natural-language agent control

Added in September 2026: an AI agent can now drive the same instrument through natural language, via two purpose-built MCP servers plus the Aida agent workbench.

- **bait_mcp** — an MCP server that is a thin 0MQ client of the *same* queue server the GUI talks to. It never runs its own Bluesky/ophyd session; it lists, enqueues, and runs plans through the RE Manager, and reads/writes individual devices by injecting small helper functions into the running RE Worker's namespace (`script_upload` + `function_execute`) rather than by holding its own device connection. Reads run in the background (safe mid-plan); writes are foreground and are refused by the queue server while a plan is running — that queue-server-level serialization is the safety interlock. bait_mcp itself performs no human-in-the-loop gating; anything that calls it (Aida) is responsible for that.
- **epics-mcp** — a separate, generic, policy-gated MCP server for direct EPICS Channel Access, for the cases that fall outside plans/devices known to the queue server (e.g. arbitrary PV reads such as "has the Linkam reached temperature?"). A YAML policy file is the hard boundary enforced inside the server process: no policy file, no PVs; writes additionally require `mode: read-write`, a matching bounded rule, a rate-limit slot, and for some rules a single-use confirm token. Currently alpha: the read path is tested against the live instrument; the write path has only been exercised against a fake backend and has not yet touched a real IOC.
- **Aida** — the local agent workbench (GUI + CLI) that a user actually talks to. It launches bait_mcp, epics-mcp, and the pyIrena MCP server as managed subprocesses, groups their tools so a model isn't overwhelmed, renders returned plots as images rather than flattened text, and adds its own client-side safety layer (confirmable/disabled tools) on top of bait_mcp's and epics-mcp's own gates.

This has been tested and works well: the same instrument can now be operated by typed command, by clicking through `usaxs-qmonitor`, or by describing what's wanted to Aida — and all three routes ultimately go through the same queue server and the same device namespace, so they stay consistent with one another.

## 4. Sample survey

Before scanning, samples are laid out on a plate and their positions, names, thicknesses, and per-sample technique selections (USAXS/SAXS/WAXS) are recorded, then exported as a Bluesky `.mac` command file (or loaded straight into the queue).

- **Igor Pro** (`IN3_SamplePlate.ipf`, part of the long-standing Irena package) has done this job historically, exporting the sample list as a text/`.mac` script that can be run from the command line or loaded into the queue server.
- **Matilda** now provides a modern replacement, `matilda-sample-plates`: a standalone desktop GUI (works without beamline access) with a clickable plate image, a sample table, timing/runtime estimation, and export to the same `.mac` format, in any of several scan orderings. It also has an optional **Beamline Survey** mode that drives the sample stage live over EPICS when run on a machine with beamline connectivity.

Either path produces the same kind of command file that feeds the queue server, so this stage already has both a legacy/CLI-flavored route and a modern GUI route; only the natural-language/agent route (an agent loading a generated plan file into the running worker, which `usaxs-qmonitor` already supports via "Load plan file…") is not yet a normal part of this specific step.

*(Flag for review: please confirm whether sample-plate survey today is still primarily done in Igor Pro operationally, with Matilda's tool as the newer/parallel option, or whether Matilda has already taken over as primary.)*

## 5. Data reduction and calibration — Matilda

Matilda (developed over the last year) turns raw detector/collection HDF5 files into calibrated, absolute-intensity I(Q) — automatically, with a GUI available for the cases that need a human.

- **`matilda` daemon** — runs as a systemd service on `usaxscontrol.xray.aps.anl.gov`, polling the Tiled server every ~5–15 seconds and reducing new USAXS Flyscan/StepScan and SAXS/WAXS area-detector data as it arrives (normalization, blank subtraction, absolute calibration, Lake/Strobl desmearing for USAXS; pyFAI azimuthal integration for SAXS/WAXS), writing NXcanSAS back into the same HDF5 files.
- **`matilda-gui`** — the manual-override desktop tool: open an HDF5 file, see the calibrated I(Q) immediately, override blank selection, thickness, transmission, Qmin, calibration model, or desmearing parameters case by case, and batch-reprocess a folder.
- Two further, config-file-triggered integrations run inside the daemon without any code changes: automatic detector-geometry recalibration via **pynika** whenever an `AgBehenateLaB6` calibration scan appears, and automatic model fitting / USAXS+SAXS merging via **pyIrena**'s batch API when a `pyirena_config.json` / `merge_config.json` is dropped into the relevant data folder.

This stage is effectively "automatic by default, GUI when you need to intervene" rather than command-line/GUI/agent in the same sense as instrument control — there isn't a natural-language interface to reduction itself, since it isn't really a judgment call a user makes turn by turn.

## 6. Plotting — Bernardyn

Bernardyn is a dedicated SAS/diffraction plotting workbench (PySide6/PyQtGraph), a visual companion to pyIrena, currently in **early alpha**. It reads NXcanSAS/HDF5 and simple text data, offers the standard SAS transformation views (Guinier, Kratky, Porod, Zimm, Debye–Bueche, etc.), 3D waterfall/surface rendering, and saves self-contained, portable `*.bernardyn.h5` graph packages (as well as PNG/SVG/CSV/Igor ITX export). It is GUI-only at this stage — there is no CLI or agent surface for it yet, and model fitting, reduction, and 2D-detector workflows are explicitly out of scope for its 1.0.

## 7. Data analysis — pyIrena

pyIrena is the Python port of the 25-year-old Igor Pro Irena package, and — like instrument control — is available through three deliberate "surfaces" built on the same underlying analysis code:

- **GUI** — `pyirena-gui` (Data Selector) as the entry point, plus standalone tools for each method: Modeling (up to 10 combined populations of size distributions, Unified Fit levels, or diffraction peaks), Unified Fit (Beaucage hierarchical model), Size Distribution (MaxEnt/Regularization/TNNLS/Monte Carlo inversion), Simple Fits (13 analytical models), WAXS Peak Fit, Data Merge, Data Explorer, and an Igor `.pxp`/`.h5xp` importer for bringing legacy data in.
- **Scripting (JSON-configured batch API)** — the same fitting/merging functions (`fit_unified`, `fit_sizes`, `fit_simple`, `fit_waxs`, `fit_modeling`, `merge_data`) are callable headlessly from Python or via JSON config files; this is exactly the mechanism Matilda's daemon uses for its automatic analysis and merging.
- **MCP (agent) server** — `pyirena-mcp` exposes HDF5 readers, parameter aggregation/tabulation, and headless plotting as MCP tools (`pyirena_summarize_folder`, `pyirena_tabulate_parameter`, `pyirena_plot_iq`, `pyirena_read_modeling`, etc.), so an agent (Aida, Claude Desktop, Claude Code, or any other MCP client) can answer plain-language questions about analysis results directly from the data files.

All three surfaces read and write the same NXcanSAS/HDF5 files, so results are shareable and self-contained regardless of which interface produced them.

## 8. Putting it together

The common thread across sections 3 and 7 in particular — and increasingly the others — is that **the underlying engine is interface-agnostic**: one queue server, one set of Bluesky plans, one set of pyIrena fitting functions, one NXcanSAS file format. What's new is that a thin, purpose-built layer (a GUI, a JSON/batch API, or an MCP server plus Aida) has been added on top of each engine so that command-line users, GUI users, and now natural-language/agent users are all working with the same underlying system rather than three different tools that happen to solve the same problem.

That gives a user at the beamline — staff or visiting scientist — real freedom to work the way they're comfortable with at each step: an expert can script a whole experiment from the command line, a first-time user can point-and-click through `usaxs-qmonitor` and `matilda-gui`, and either one can ask an agent in plain language to check instrument status, load a sample plan, or summarize what a batch of scans found — and switch between these at any point, because they all ultimately touch the same instrument, the same queue, and the same data.

## 9. Open items to confirm before drawing diagrams

- Confirm current operational status of the sample-survey step (Igor Pro vs. Matilda `matilda-sample-plates`) as noted in §4.
- Confirm whether "natural language control" should be shown as covering all of instrument control, or whether there are stages within it (e.g. safety-critical writes) that are agent-*assisted* but always require explicit human confirmation — this matters for how the diagrams represent the human-in-the-loop boundary (bait_mcp/epics-mcp explicitly push HITL responsibility up to Aida/the consumer).
- Confirm the intended audiences and formats for the "few drawings": e.g. (a) an expert/architecture diagram showing the actual processes, MCP servers, and protocols (ZMQ, EPICS CA, MCP/HTTP) for a technical paper, vs. (b) a simplified three-lane diagram (CLI / GUI / Agent) for beamline users and the web site.
- Confirm whether Bernardyn and the sample-survey step should appear as full peers in the "3 interfaces" framing, or be called out as GUI-only/partial for now (as drafted in §5–§6).
