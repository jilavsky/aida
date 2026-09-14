# The USAXS Instrument Software Ecosystem

**APS 12-ID-E — USAXS / SAXS / WAXS**
Draft for review — September 2026 (rev. 2)

## 1. What this document is

This describes the full software stack that now supports the USAXS/SAXS/WAXS instrument at APS 12-ID-E, from beam-time control through data reduction, analysis, and plotting. The point worth documenting is not any single tool but the shape of the whole system: every stage of the experiment — controlling the instrument, surveying samples, reducing raw data, analyzing it, and plotting it — can be driven through more than one interface, and a user picks whichever interface suits them (or mixes them) at each step. Command line, desktop GUI, and natural-language AI agent are, for the first time, all first-class ways to do the same work end to end.

This draft is meant to be checked and corrected before it becomes the basis for diagrams, a web page, and eventually a paper.

## 2. The five stages, and the three ways to drive each one

| Stage | Command line | Desktop GUI | AI agent (natural language) |
|---|---|---|---|
| **Instrument control** | Interactive Bluesky/IPython session (`usaxs-bits`) | `usaxs-qmonitor` — client of the queue server | Aida + bait_mcp + epics-mcp — also a client of the queue server |
| **Sample survey** | `run_command_file` plan replays an exported `.mac` file, from any of the three columns | Igor Pro *Setup Sample Plates* (mac export, or direct-to-queue) or `matilda-sample-plates` (mac export today) | Same queue, via bait_mcp |
| **Data reduction/calibration** | `matilda` daemon (automatic, headless) | `matilda-gui` (manual override) | — (automatic; not a per-scan judgment call) |
| **Data analysis** | pyIrena batch/JSON scripting API | pyIrena GUI (`pyirena-gui` and tool-specific GUIs) | pyIrena MCP server, via Aida or any MCP client |
| **Plotting** | — | Igor Pro (current production tool for publication-quality plots); Bernardyn (early alpha, growing) | — |

No single row is complete without the others, and no single column has to be used exclusively — a user can survey samples in Igor Pro, run the actual scan from the command line, and ask an agent to summarize the reduced results, all in the same session.

## 3. Instrument control

**Repository:** `usaxs-bits` (package `bits_usaxs`, importable as `usaxs`), built on the BCDA-APS `apstools`/BITS framework. Replaces the pre-2023 `usaxs-bluesky-ended-2023` codebase.

### 3.1 Command line — the interactive Bluesky session

The Bluesky RunEngine, device definitions, and all beamline plans (`Flyscan`, `USAXSscan`, `mode_USAXS`, tune plans, etc.) live in this one package, loaded by a single `startup.py`. Run interactively (`./scripts/start-usaxs-bits.sh`), this gives a genuine IPython console: the user types commands directly into the live session and sees results immediately. This has been the operational way to run USAXS for some time and remains fully available.

### 3.2 GUI and agent both drive the *same* queue server session — neither lets you type into it

This is worth being precise about, because it's the real distinction from 3.1, not just "GUI vs. text": the queue server's RE Manager/RE Worker is **headless**. There is no interactive prompt to type free-form commands into, whether you're using the GUI or the agent — both are clients that submit *structured* requests (add a plan to the queue, run a plan now, read or set one named device) to that one running worker session.

- **`usaxs-qmonitor`** exposes this as point-and-click: queue control (open/close environment, start/stop/pause), a plan editor and queue table, a running-plan/history panel, `New User…`/`New Sample…`/`Load plan file…` actions, and a live-plot tab (tune_ar, tune_mr, tune_a2rp, tune_dx, tune_dy — last 5 scans overlaid), drawn from a separate 0MQ document-stream subscription. It talks to the RE Manager over ZMQ (control port 60615, console/status port 60625). Tested and working well since July 2026.
- **bait_mcp** exposes the same primitives — list/add/run plans, read/set a device, queue status — as MCP tools, over the same 0MQ control channel to the same RE Manager. It holds no ophyd devices and no separate Bluesky session of its own; device reads/writes are executed inside the queue server's own worker (by injecting small helper functions into its live namespace) rather than through a second connection to the hardware.
- **Aida** is the agent workbench a user actually talks to. It launches bait_mcp and epics-mcp (and the pyIrena MCP server) as managed subprocesses, groups their tools, renders returned plots as images, and layers its own client-side controls (confirmable/disabled tools) on top.
- **epics-mcp** is a separate, generic, policy-gated MCP server for direct EPICS Channel Access — for reading or writing individual PVs that fall outside the plans/devices known to the queue server. A YAML policy file enforced inside the server process is the hard boundary. It is currently alpha: the read path is tested against the live instrument; the write path has only been exercised against a fake backend.

**On safety:** every plan exposed through the queue server is written and vetted to be safe to run — that is the whole point of exposing it there rather than as raw EPICS access. So any combination of queue-server plans reachable through `usaxs-qmonitor` or through the agent is safe from an instrument point of view, by construction, regardless of the order or combination in which they're invoked. Genuinely unsafe, low-level operations (direct PV manipulation during setup/alignment) are done manually by beamline staff via EPICS, outside this controlled path — that is also the scope epics-mcp's write side is meant for, not routine user operation.

This has been tested and works well: the same instrument can now be operated by typing into a live IPython session, by clicking through `usaxs-qmonitor`, or by describing what's wanted to Aida — and the latter two are, structurally, two different front ends on the very same queue server session.

## 4. Sample survey

Before scanning, samples are laid out on a plate — positions, names, thicknesses, and per-sample technique selection (USAXS/SAXS/WAXS) — and turned into instructions the queue server can run. There are two distinct mechanisms, and both matter:

1. **The `.mac` command file, run as one block.** A sample layout is exported as a Bluesky `.mac` file, and the plan `run_command_file` replays the whole file as a single queued command. Because `run_command_file` is just another plan, it can be launched identically from the command line, from `usaxs-qmonitor` (added to the queue), or from an agent via bait_mcp — the mac file itself doesn't care which interface triggers it.
2. **Direct-to-queue population.** Igor Pro's *Setup Sample Plates* tool (`IN3_SamplePlate.ipf`) can additionally export straight into the queue server's queue as a list of individual scan entries (one per sample/technique/position), rather than one opaque block. Because these land as ordinary queue items, a user can pause the queue, and add, remove, or modify any entry that hasn't started executing yet — much finer-grained control than replaying a mac file. This is the current workhorse for sample survey and is in everyday use.

Matilda's `matilda-sample-plates` GUI (built to eventually replace the Igor Pro tool) currently supports only mac-file export (mechanism 1); direct-to-queue population (mechanism 2) is expected to be a small, near-term extension.

## 5. Data reduction and calibration — Matilda

Matilda (developed over the last year) turns raw detector/collection HDF5 files into calibrated, absolute-intensity I(Q) — automatically, with a GUI available for the cases that need a human.

- **`matilda` daemon** — runs as a systemd service on `usaxscontrol.xray.aps.anl.gov`, polling the Tiled server every few seconds and reducing new USAXS Flyscan/StepScan and SAXS/WAXS area-detector data as it arrives (normalization, blank subtraction, absolute calibration, Lake/Strobl desmearing for USAXS; pyFAI azimuthal integration for SAXS/WAXS), writing NXcanSAS back into the same HDF5 files.
- **`matilda-gui`** — the manual-override desktop tool: open an HDF5 file, see the calibrated I(Q) immediately, override blank selection, thickness, transmission, Qmin, calibration model, or desmearing parameters case by case, and batch-reprocess a folder.
- Two further, config-file-triggered integrations run inside the daemon without any code changes: automatic detector-geometry recalibration via **pynika** whenever an `AgBehenateLaB6` calibration scan appears, and automatic model fitting / USAXS+SAXS merging via **pyIrena**'s batch API when a `pyirena_config.json` / `merge_config.json` is dropped into the relevant data folder.

This stage is effectively "automatic by default, GUI when you need to intervene" rather than command-line/GUI/agent in the same sense as instrument control — there isn't a natural-language interface to reduction itself, since it isn't really a judgment call a user makes turn by turn.

## 6. Data analysis — pyIrena

pyIrena is the Python port of the 25-year-old Igor Pro Irena package, and — like instrument control — is available through three deliberate "surfaces" built on the same underlying analysis code:

- **GUI** — `pyirena-gui` (Data Selector) as the entry point, plus standalone tools for each method: Modeling (up to 10 combined populations of size distributions, Unified Fit levels, or diffraction peaks), Unified Fit (Beaucage hierarchical model), Size Distribution (MaxEnt/Regularization/TNNLS/Monte Carlo inversion), Simple Fits (13 analytical models), WAXS Peak Fit, Data Merge, Data Explorer, and an Igor `.pxp`/`.h5xp` importer for bringing legacy data in.
- **Scripting (JSON-configured batch API)** — the same fitting/merging functions (`fit_unified`, `fit_sizes`, `fit_simple`, `fit_waxs`, `fit_modeling`, `merge_data`) are callable headlessly from Python or via JSON config files; this is exactly the mechanism Matilda's daemon uses for its automatic analysis and merging.
- **MCP (agent) server** — `pyirena-mcp` exposes HDF5 readers, parameter aggregation/tabulation, and headless plotting as MCP tools (`pyirena_summarize_folder`, `pyirena_tabulate_parameter`, `pyirena_plot_iq`, `pyirena_read_modeling`, etc.), so an agent (Aida, Claude Desktop, Claude Code, or any other MCP client) can answer plain-language questions about analysis results directly from the data files.

All three surfaces read and write the same NXcanSAS/HDF5 files, so results are shareable and self-contained regardless of which interface produced them.

## 7. Plotting

Publication-quality plotting of reduced and analyzed SAS data today is done mainly in **Igor Pro** — still the production tool for figures that go into papers. **Bernardyn**, a dedicated PySide6/PyQtGraph plotting workbench for SAS/diffraction curves (Guinier, Kratky, Porod, Zimm, Debye–Bueche views, 3D waterfalls/surfaces, portable `*.bernardyn.h5` graph packages, PNG/SVG/CSV/Igor-ITX export), is the intended successor, but is currently **early alpha**: it already plots reduced SAS data, and adding the ability to plot pyIrena's analysis/fit results directly is planned next. Treat Bernardyn as a future capability worth a small mention in the diagrams, not a peer of the other production tools yet.

## 8. Putting it together

The common thread across sections 3 and 6 in particular — and increasingly the others — is that **the underlying engine is interface-agnostic**: one queue server, one set of Bluesky plans, one set of pyIrena fitting functions, one NXcanSAS file format. What's new is that a thin, purpose-built layer (a GUI, a JSON/batch API, or an MCP server plus Aida) has been added on top of each engine so that command-line users, GUI users, and now natural-language/agent users are all working with the same underlying system rather than three different tools that happen to solve the same problem.

That gives a user at the beamline — staff or visiting scientist — real freedom to work the way they're comfortable with at each step: an expert can script a whole experiment from the command line, a first-time user can point-and-click through `usaxs-qmonitor` and `matilda-gui`, and either one can ask an agent in plain language to check instrument status, load a sample plan, or summarize what a batch of scans found — and switch between these at any point, because they all ultimately touch the same instrument, the same queue, and the same data.

## 9. Notes for the diagrams

- **Two diagrams, both needed now:** (a) a detailed technical diagram for expert/staff audiences — showing the real processes, protocols (ZMQ control/console, EPICS CA, MCP/HTTP), and the queue-server-centered control architecture; (b) a simplified, usability-focused schematic for training users — light on plumbing, clear on "here are your three ways to do X."
- **Sample survey is prominent, not a footnote:** it's a critical, ready-to-use step for every user (mac-file replay and, in Igor Pro, direct-to-queue population), and both diagrams should show it clearly, ahead of instrument control in the natural workflow order.
- **Bernardyn gets a small, clearly-future-labeled mention**, not equal billing with Igor Pro plotting, pyIrena, or Matilda.
- **Safety framing:** don't depict a human-in-the-loop gate between the agent and "normal" instrument operation — queue-server plans are safe by design, so GUI and agent control are drawn as equally safe, structurally-parallel clients of the same queue. Only raw EPICS access (epics-mcp writes, staff-only, alpha) sits outside that guarantee and should be shown as a distinct, staff-scoped path.
