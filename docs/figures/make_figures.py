#!/usr/bin/env python3
"""Generate the AIDA concept figures (docs/concepts.md sketches A-D) as SVG.

Edit the DATA blocks near the bottom and re-run; nothing else needs touching.

    python make_figures.py            # writes fig_*.svg next to this file
    python make_figures.py --png      # also renders PNG (needs cairosvg)
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

# ---------------------------------------------------------------- palette ---
INK = "#1c2333"          # primary text
INK_SOFT = "#4a5568"     # secondary text
INK_FAINT = "#8a94a6"    # tertiary / empty slots
PAPER = "#ffffff"
RULE = "#dfe3ea"

BANDS = {
    "brain":      {"line": "#3f5fbf", "fill": "#eef2fd", "box": "#ffffff", "edge": "#c2cdf0"},
    "behavior":   {"line": "#1f8a70", "fill": "#eaf6f2", "box": "#ffffff", "edge": "#b7ddd2"},
    "reach":      {"line": "#c07818", "fill": "#fdf4e7", "box": "#ffffff", "edge": "#efd6ae"},
    "guardrails": {"line": "#b5455e", "fill": "#fdeef1", "box": "#ffffff", "edge": "#f0c6d0"},
    "neutral":    {"line": "#5a6478", "fill": "#f4f6f9", "box": "#ffffff", "edge": "#d5dae3"},
}

SANS = "Helvetica Neue, Helvetica, Arial, Carlito, DejaVu Sans, sans-serif"
MONO = "SF Mono, Menlo, Consolas, DejaVu Sans Mono, monospace"


# ------------------------------------------------------------- primitives ---
def esc(s: str) -> str:
    return html.escape(s, quote=False)


def text(x, y, s, *, size=15, fill=INK, weight="normal", family=SANS,
         anchor="start", style="normal", opacity=None):
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" font-style="{style}" fill="{fill}" '
        f'text-anchor="{anchor}"{op}>{esc(s)}</text>'
    )


def rect(x, y, w, h, *, fill, stroke=None, rx=10, sw=1.5, dash=None, opacity=None):
    st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    da = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{st}{da}{op}/>'


def line(x1, y1, x2, y2, *, stroke=RULE, sw=1.5, dash=None):
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"{da}/>'


def arrow(x1, y1, x2, y2, *, stroke=INK_FAINT, sw=2):
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{sw}" marker-end="url(#arrowhead)"/>')


def svg_doc(w, h, body, *, bg=PAPER):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">\n'
        '<defs>'
        '<marker id="arrowhead" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">'
        f'<polygon points="0 0, 9 3.5, 0 7" fill="{INK_FAINT}"/></marker>'
        '</defs>\n'
        f'<rect width="{w}" height="{h}" fill="{bg}"/>\n' + body + "\n</svg>\n"
    )


def figure_title(x, y, title, subtitle=None):
    out = [text(x, y, title, size=30, weight="bold")]
    if subtitle:
        out.append(text(x, y + 28, subtitle, size=16, fill=INK_SOFT))
    return out


# ------------------------------------------------------------- FIGURE A -----
def figure_a() -> str:
    W, H = 1600, 982
    o = []
    o += figure_title(56, 62, "A workspace is one configured assistant",
                      "Everything below is set once, per workspace. Picking the workspace in the toolbar applies all of it.")

    # workspace frame — height derived from the bands so there is no dead space
    fx, fy, fw = 40, 108, W - 80
    fh = 34 + (4 * 182 + 3 * 14) + 22
    o.append(rect(fx, fy, fw, fh, fill="#fbfcfe", stroke="#c8cfdb", rx=16, sw=2))
    o.append(rect(fx + 26, fy - 16, 330, 34, fill="#1c2333", rx=8))
    o.append(text(fx + 44, fy + 7, 'WORKSPACE   "usaxs-analysis"', size=15,
                  fill="#ffffff", weight="bold"))

    bands = [
        ("brain", "BRAIN", "which AI, and how much it can hold", [
            ("Profile (provider)", ["which model, which endpoint,", "which key. The brain."],
             "openRouter  ·  argo-claude"),
            ("Context window", ["how much fits in one turn", "(set on the profile)"],
             "200 000 tokens"),
        ]),
        ("behavior", "BEHAVIOR", "who it is, and what it knows", [
            ("System prompt", ["the ROLE it is playing —", "identity and priorities"],
             '"You are a careful reviewer…"'),
            ("Skills", ["the HOW-TO — procedures and", "conventions it must follow"],
             "saxs-basics.md"),
            ("Knowledge base (RAG)", ["your own library, indexed so", "it can be searched by meaning"],
             "10 years of beamline notes"),
        ]),
        ("reach", "REACH", "what it can touch", [
            ("Source folder(s)", ["where the stuff IS —", "files it may open by name"],
             "/share1/USAXS_data/…"),
            ("Target folder", ["where results GO —", "reports, figures, scripts"],
             "~/Documents/Aida/usaxs"),
            ("MCP servers (group)", ["extra tools: pyIrena,", "instrument control, web…"],
             "group: pyirena-analysis"),
            ("Python interpreter", ["WHICH conda env the code", "it writes actually runs in"],
             "…/envs/pyirena/bin/python"),
        ]),
        ("guardrails", "GUARDRAILS", "how much rope it gets", [
            ("Safety mode", ["confirm = ask before every write;", "relaxed = don't, inside my folders"],
             "confirm  |  relaxed"),
            ("Command allowlist", ["shell commands that may run", "without asking. Keep it short."],
             "git status,  git log *"),
            ("Scripting on / off", ["master switch, plus a hard", "timeout on any one run"],
             "on  ·  30 s ceiling"),
        ]),
    ]

    y = fy + 34
    band_gap = 14
    inner_pad = 20
    for key, name, tagline, boxes in bands:
        c = BANDS[key]
        bh = 38 + 128 + 16
        o.append(rect(fx + inner_pad, y, fw - 2 * inner_pad, bh, fill=c["fill"], rx=12))
        o.append(rect(fx + inner_pad, y, 6, bh, fill=c["line"], rx=3))
        o.append(text(fx + inner_pad + 22, y + 26, name, size=16, weight="bold", fill=c["line"]))
        o.append(text(fx + inner_pad + 22 + 11 * len(name) + 16, y + 26, "— " + tagline,
                      size=14, fill=INK_SOFT))

        n = len(boxes)
        avail = fw - 2 * inner_pad - 32
        gap = 14
        bw = (avail - gap * (n - 1)) / n
        bx = fx + inner_pad + 16
        by = y + 38
        for title, lines, example in boxes:
            o.append(rect(bx, by, bw, 128, fill=c["box"], stroke=c["edge"], rx=10, sw=1.5))
            o.append(text(bx + 16, by + 28, title, size=17, weight="bold"))
            for i, ln in enumerate(lines):
                o.append(text(bx + 16, by + 54 + i * 20, ln, size=14, fill=INK_SOFT))
            o.append(line(bx + 16, by + 100, bx + bw - 16, by + 100, stroke=c["edge"]))
            o.append(text(bx + 16, by + 118, example, size=13, family=MONO, fill=c["line"]))
            bx += bw + gap
        y += bh + band_gap

    o.append(text(fx + 26, H - 26,
                  "Source folder = files it opens by name.   Knowledge base = text it searches by meaning.   "
                  "System prompt = who it is.   Skill = how it works.",
                  size=14, fill=INK_SOFT))
    return svg_doc(W, H, "\n".join(o))


# ------------------------------------------------------------- FIGURE B -----
def figure_b() -> str:
    W, H = 1600, 985
    o = []
    o += figure_title(56, 62, "What the model actually sees on every turn",
                      "Nothing persists on the model's side. The whole stack is rebuilt and re-sent with every message you type.")

    srcx = 250          # right edge of the source labels
    bx0 = 300           # left edge of the layer boxes
    bw = 1080           # box width
    rowh, gap = 62, 8

    groups = [
        ("#3f5fbf", "THE SYSTEM MESSAGE  —  assembled from your configuration, in this order", [
            ("1", "Identity", "\"Your name is Aida\" + the personal context you wrote once", "Settings"),
            ("2", "System prompt", "the workspace's role — who it is being", "Workspace"),
            ("3", "Operating facts", "your folders, the safety mode, each MCP server's own briefing", "Workspace + MCP"),
            ("4", "Skills", "the how-to files this workspace loads", "~/.aida/skills/"),
        ]),
        ("#c07818", "THEN THE REST OF THE REQUEST", [
            ("5", "Tool schemas", "~10 000 tokens for pyIrena — sent every turn, called or not", "MCP group"),
            ("6", "Retrieved passages", "this turn only, if a knowledge base matched your question", "Knowledge base"),
            ("7", "Conversation history", "your messages, its replies, and every tool result so far", "This chat"),
            ("8", "Your new question", "usually the smallest part of the whole request", "You"),
            ("9", "Room reserved for the reply", "max_tokens — set aside before a word is generated", "Profile"),
        ]),
    ]

    y = 138
    ytop = None
    for accent, heading, rows in groups:
        o.append(text(bx0, y + 16, heading, size=16, weight="bold", fill=accent))
        y += 30
        if ytop is None:
            ytop = y
        for num, title, sub, src in rows:
            o.append(rect(bx0, y, bw, rowh, fill="#ffffff", stroke=RULE, rx=9, sw=1.5))
            o.append(rect(bx0, y, 5, rowh, fill=accent, rx=2.5))
            o.append(text(bx0 + 24, y + 38, num, size=17, weight="bold", fill=accent))
            o.append(text(bx0 + 56, y + 38, title, size=17, weight="bold"))
            o.append(text(bx0 + 380, y + 38, sub, size=14.5, fill=INK_SOFT))
            o.append(text(srcx, y + 38, src, size=13.5, fill=INK_FAINT, anchor="end"))
            o.append(arrow(srcx + 10, y + rowh / 2, bx0 - 8, y + rowh / 2))
            y += rowh + gap
        y += 12
    yend = y - gap - 12

    # context window bracket down the right-hand side
    kx = bx0 + bw + 34
    o.append(line(kx, ytop, kx + 18, ytop, stroke="#b5455e", sw=2.5))
    o.append(line(kx + 18, ytop, kx + 18, yend, stroke="#b5455e", sw=2.5))
    o.append(line(kx, yend, kx + 18, yend, stroke="#b5455e", sw=2.5))
    mid = (ytop + yend) / 2
    o.append(f'<text transform="translate({kx + 52},{mid}) rotate(-90)" text-anchor="middle" '
             f'font-family="{SANS}" font-size="17" font-weight="bold" fill="#b5455e">'
             'all of it must fit the context window</text>')

    # takeaways
    ty = H - 112
    o.append(rect(56, ty, W - 112, 82, fill="#f4f6f9", rx=10))
    notes = [
        ("Tool schemas are rent, not usage.", "Every enabled server costs tokens each turn even if nothing calls it. That is what MCP groups are for."),
        ("Tool results stay in the history.", "Twenty fits, each returning a table, is what fills a window — not your prose."),
        ("Prompts and skills are cheap.", "A few hundred tokens, and they are what makes the agent behave. Be generous."),
    ]
    cw = (W - 112 - 40) / 3
    for i, (b1, b2) in enumerate(notes):
        x = 76 + i * (cw + 10)
        o.append(text(x, ty + 32, b1, size=15, weight="bold"))
        words, ln, lines = b2.split(), "", []
        for wd in words:
            if len(ln) + len(wd) > 52:
                lines.append(ln); ln = wd
            else:
                ln = (ln + " " + wd).strip()
        lines.append(ln)
        for j, l in enumerate(lines[:2]):
            o.append(text(x, ty + 54 + j * 18, l, size=13, fill=INK_SOFT))
    return svg_doc(W, H, "\n".join(o))


# ------------------------------------------------------------- FIGURE C -----
def figure_c() -> str:
    W, H = 1600, 900
    o = []
    o += figure_title(56, 62, "Same ten slots — three workspaces",
                      "What changes between a toy and a working setup is only which slots you bother to fill.")

    cols = [
        ("A.  \"chat\"", "baseline — proves the key works", "#5a6478", "#f4f6f9"),
        ("B.  \"paper-review\"", "discuss a manuscript", "#1f8a70", "#eaf6f2"),
        ("C.  \"usaxs-analysis\"", "fit real data", "#c07818", "#fdf4e7"),
    ]
    rows = [
        ("Profile", ["openRouter (free)", "openRouter (free)", "a TOOL-CAPABLE model,\nvision on"]),
        ("System prompt", [None, '"You are a careful reviewer\nof SAS manuscripts…"', '"You are a USAXS analysis\nassistant at APS 9-ID…"']),
        ("Skills", [None, "review-checklist", "saxs-basics,\npyirena-usage"]),
        ("Knowledge base", [None, "optional: journal guidelines", "optional: your method notes"]),
        ("MCP group", ["none", "none", "pyirena-analysis"]),
        ("Source folder", [None, "~/reviews/incoming", "/share1/USAXS_data/…"]),
        ("Target folder", ["~/Documents/Aida/chat", "~/reviews/out", "~/Documents/Aida/usaxs"]),
        ("Python interpreter", [None, None, "…/envs/pyirena/bin/python"]),
        ("Safety mode", ["confirm", "confirm", "confirm → relaxed\nonce you trust it"]),
        ("Scripting", ["off", "off", "on"]),
    ]
    outcome = [
        "Talks. That is all.\nNo files, no tools.\nUseful only to prove\nthe key works.",
        "Reads the PDF you attach,\nlooks at its figures, writes\nthe review into ~/out.",
        "Opens your HDF5 files, runs\nUnified Fits, shows you the plot,\nwrites a report with figures.",
    ]

    labw = 210
    x0 = 56
    gap = 16
    cw = (W - 112 - labw - 2 * gap) / 3
    top = 118
    hdr = 62
    rowh = 54

    # column headers
    for i, (name, sub, accent, tint) in enumerate(cols):
        x = x0 + labw + i * (cw + gap)
        o.append(rect(x, top, cw, hdr, fill=tint, rx=10))
        o.append(rect(x, top, cw, 5, fill=accent, rx=2.5))
        o.append(text(x + 18, top + 30, name, size=18, weight="bold", fill=INK))
        o.append(text(x + 18, top + 50, sub, size=13.5, fill=INK_SOFT))

    y = top + hdr + 10
    for ri, (label, cells) in enumerate(rows):
        if ri % 2 == 0:
            o.append(rect(x0, y - 2, W - 112, rowh, fill="#f8f9fb", rx=6))
        o.append(text(x0 + 8, y + 30, label, size=15, weight="bold", fill=INK_SOFT))
        for i, cell in enumerate(cells):
            x = x0 + labw + i * (cw + gap)
            accent = cols[i][2]
            if cell is None:
                o.append(rect(x, y, cw, rowh - 4, fill="#f1f3f7", rx=8))
                o.append(text(x + cw / 2, y + 32, "— not set —", size=13.5,
                              fill=INK_FAINT, anchor="middle"))
            else:
                o.append(rect(x, y, cw, rowh - 4, fill="#ffffff", stroke=RULE, rx=8, sw=1.2))
                parts = cell.split("\n")
                fam = MONO if ("/" in cell or "~" in cell) and not cell.startswith('"') else SANS
                sz = 13.5 if fam == MONO else 14.5
                if len(parts) == 1:
                    o.append(text(x + 14, y + 32, parts[0], size=sz, family=fam))
                else:
                    for j, p in enumerate(parts[:2]):
                        o.append(text(x + 14, y + 22 + j * 18, p, size=sz - 0.5, family=fam))
        y += rowh

    # outcome row
    y += 8
    oh = 108
    o.append(text(x0 + 8, y + 30, "What it can", size=15, weight="bold", fill=INK))
    o.append(text(x0 + 8, y + 50, "actually do", size=15, weight="bold", fill=INK))
    for i, txt in enumerate(outcome):
        x = x0 + labw + i * (cw + gap)
        accent, tint = cols[i][2], cols[i][3]
        o.append(rect(x, y, cw, oh, fill=tint, stroke=accent, rx=10, sw=1.5))
        for j, p in enumerate(txt.split("\n")):
            o.append(text(x + 16, y + 28 + j * 21, p, size=15, fill=INK))
    return svg_doc(W, H, "\n".join(o))


# ------------------------------------------------------------- FIGURE D -----
def figure_d() -> str:
    W, H = 1600, 840
    o = []
    o += figure_title(56, 62, "If you are coming from Claude Desktop or ChatGPT",
                      "Most of AIDA is a word you already know. Two rows have no equivalent at all — and those two are the point.")

    rows = [
        ("The app is the model", "Profile", "You choose the model and the endpoint; several profiles can share one endpoint.", False),
        ("Custom instructions", "System prompt", "Per workspace, not one global setting.", False),
        ("A \"GPT\" or a Project", "Workspace", "Also carries folders, safety rules, and an interpreter.", False),
        ("Uploaded project files", "Knowledge base  or  source folder", "Two different things: searched by meaning vs. opened by name.", False),
        ("Connectors / extensions", "MCP servers", "Same protocol — a claude_desktop_config.json imports directly.", False),
        ("\"Allow this action?\" popups", "Safety mode", "You decide how often it asks, per workspace.", False),
        ("— nothing like it —", "Source / target folders", "The agent reads and writes real files on your disk.", True),
        ("— nothing like it —", "Python interpreter", "The agent writes and runs code in your own conda environment.", True),
    ]

    x0, y = 56, 128
    w = W - 112
    c1, c2 = 380, 400
    hdr = 44
    o.append(rect(x0, y, w, hdr, fill="#1c2333", rx=10))
    o.append(text(x0 + 24, y + 29, "YOU KNOW IT AS", size=14, weight="bold", fill="#ffffff"))
    o.append(text(x0 + 24 + c1, y + 29, "IN AIDA IT IS", size=14, weight="bold", fill="#ffffff"))
    o.append(text(x0 + 24 + c1 + c2, y + 29, "THE DIFFERENCE THAT MATTERS", size=14, weight="bold", fill="#ffffff"))
    y += hdr + 10

    rh = 74
    for known, aida, diff, highlight in rows:
        if highlight:
            o.append(rect(x0, y, w, rh - 8, fill="#fdf4e7", stroke="#c07818", rx=10, sw=1.8))
        else:
            o.append(rect(x0, y, w, rh - 8, fill="#ffffff", stroke=RULE, rx=10, sw=1.2))
        o.append(text(x0 + 24, y + 40, known, size=16,
                      fill=INK_FAINT if highlight else INK_SOFT,
                      style="italic" if highlight else "normal"))
        o.append(text(x0 + 24 + c1, y + 40, aida, size=17, weight="bold",
                      fill="#c07818" if highlight else INK))
        o.append(text(x0 + 24 + c1 + c2, y + 40, diff, size=14.5, fill=INK_SOFT))
        y += rh

    o.append(rect(x0, y + 6, w, 56, fill="#f4f6f9", rx=10))
    o.append(text(x0 + 24, y + 40,
                  "Everything above the highlighted rows is convenience. The last two rows are why AIDA exists.",
                  size=16, fill=INK))
    return svg_doc(W, H, "\n".join(o))


# ------------------------------------------------------------------ main ----
FIGURES = {
    "fig-a-workspace-anatomy": figure_a,
    "fig-b-what-the-model-sees": figure_b,
    "fig-c-three-workspaces": figure_c,
    "fig-d-coming-from-chatgpt": figure_d,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true", help="also render PNG (needs cairosvg)")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--out", default=str(Path(__file__).parent))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in FIGURES.items():
        svg = fn()
        (out / f"{name}.svg").write_text(svg, encoding="utf-8")
        print("wrote", out / f"{name}.svg")
        if args.png:
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out / f"{name}.png"),
                             scale=args.scale)
            print("wrote", out / f"{name}.png")


if __name__ == "__main__":
    main()
