# AIDA — AI Data Assistant

> **Status: pre-alpha.** AIDA is under active, phased development (see
> `PLAN.md`). Nothing here is stable yet; APIs, config formats, and CLI
> commands will change without notice until Phase 5 (first GUI release).

AIDA is a local scientific agent workbench: a simple, reliable GUI (and CLI)
for using AI agents in scientific work — conversation with local or cloud
LLMs, correct use of domain MCP servers (pyIrena, bait_mcp, ...), correct
display of rich tool results (especially PNG plots), reading and producing
documents, and controlled access to the user's data folders.

It is built for pyIrena and USAXS-instrument users and is deliberately **not**
a general-purpose AI platform — see `PLAN.md` §1 for the full rationale.

## Install (development)

```bash
git clone https://github.com/jilavsky/aida.git
cd aida
conda env create -f environment.yml   # or: pip install -e ".[dev]"
conda activate aida
aida doctor
```

## Status

Following the phased plan in [`PLAN.md`](PLAN.md); per-phase checklists live
under [`planning/`](planning/). Phase 1 (this scaffold) delivers packaging,
configuration, logging, and CI — no agent functionality yet.

## License

MIT — see [`LICENSE`](LICENSE).
