# Intro slide — "What is AIDA?"

Opening slide for the AIDA talk. The figures in
[`figures/`](figures/) follow it; see [`concepts.md`](concepts.md) for the
full explanation of every term used here.

---

## Slide

### AIDA — an AI assistant that works on *your* data, on *your* machine

- **Not another chat window.** A desktop app that connects an AI model to
  your files, your analysis code, and your instruments.

- **You pick the brain.** A local model where nothing leaves the machine, or
  a cloud one — Claude, GPT, ANL Argo. Switch mid-conversation.

- **It reads and writes real files.** Point it at a data folder: it opens
  what is there and writes reports, tables and figures back where you say.

- **It runs the analysis — not a description of it.** pyIrena fits, plots,
  NXcanSAS files. And it can *look* at the plot it just made and tell you
  whether the fit is any good.

- **It writes and runs Python in your own conda environment.** Your
  packages, your versions, your data.

- **It searches your own documents.** Notes, SOPs, past reports, manuscripts
  — instead of guessing from whatever it was trained on.

- **You decide what it may touch.** Folders and permissions are set per
  project. It asks before every write until you tell it to stop asking.

> **Beyond the beamline:** reading and reviewing manuscripts and proposals ·
> drafting reports · mining ten years of your own notes · routine file
> wrangling · scheduled jobs that run without you.

---

## Speaker notes

- The room already believes an AI can talk. The claim worth making is the
  second half of the title: *your data, your machine.* Everything else on
  the slide is evidence for that.
- Bullet 2 is the one that disarms the "I can't put our data in ChatGPT"
  objection — say it early, and say that a local model means the data never
  leaves the laptop.
- Bullet 4 is the differentiator against every commercial chat app. If one
  demo is shown all day, make it this one: ask for a fit, get a plot back in
  the conversation, ask "is that fit any good?"
- The "beyond the beamline" line matters for anyone in the room who does not
  run USAXS. Reviewing a manuscript needs no MCP server and no instrument —
  it is the cheapest way for a sceptic to try it.
- Do not explain workspaces, profiles or MCP here. That is the next slide
  (Figure A). This slide only has to earn the next five minutes.

## If time is short — cut to five bullets

Drop **"writes and runs Python"** (folds into "runs the analysis") and
**"searches your own documents"** (the least universal). Keep the closing
line either way; it is what makes the non-beamline half of the room lean in.
