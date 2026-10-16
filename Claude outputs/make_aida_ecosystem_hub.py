"""Generate the Aida ecosystem hub diagram (SVG, 16:9) in the style of the other Claude outputs."""

import math
import sys
from xml.sax.saxutils import escape

W, H = 1920, 1080
FONT = "Helvetica, Arial, sans-serif"
INK, MUTED, LINE = "#1e293b", "#64748b", "#94a3b8"

out = []


def t(x, y, s, size=17, weight="normal", fill=INK, anchor="start", style="normal"):
    out.append(
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
        f'font-style="{style}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def rect(x, y, w, h, fill, stroke, sw=2.5, rx=16, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{sw}"{d}/>'
    )


def pill(x, y, s, fill, color, size=13):
    w = 9 + len(s) * size * 0.62
    rect(x, y - size - 3, w, size + 9, fill, color, sw=1.2, rx=(size + 9) / 2)
    t(x + w / 2, y + 1, s, size=size, weight="bold", fill=color, anchor="middle")
    return w


def line(x1, y1, x2, y2, color, sw=2, marker=None, dash=None):
    m = f' marker-end="url(#{marker})"' if marker else ""
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
        f'stroke-width="{sw}" stroke-linecap="round"{d}{m}/>'
    )


def marker(mid, color):
    out.append(
        f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
    )


def card(x, y, w, h, color, tint, num, title, server, lines, tag=None):
    """A capability card: colored left bar, title, MCP server name, description lines."""
    rect(x, y, w, h, tint, color)
    out.append(f'<rect x="{x}" y="{y + 14}" width="7" height="{h - 28}" rx="3.5" fill="{color}"/>')
    tx = x + 30
    if num:
        out.append(f'<circle cx="{tx + 15}" cy="{y + 36}" r="16" fill="{color}"/>')
        t(tx + 15, y + 42, num, size=17, weight="bold", fill="#ffffff", anchor="middle")
        tx2 = tx + 42
    else:
        tx2 = tx
    t(tx2, y + 43, title, size=24, weight="bold")
    if tag:
        pill(x + w - 20 - (9 + len(tag) * 13 * 0.62), y + 40, tag, "#ffffff", color)
    t(tx, y + 76, server, size=17, weight="bold", fill=color, style="italic")
    for i, s in enumerate(lines):
        t(tx, y + 104 + i * 25, s, size=17, fill="#334155")


# ---------------------------------------------------------------- defs, background
out.append("<defs>")
marker("a-ink", INK)
marker("a-violet", "#6d28d9")
marker("a-green", "#047857")
marker("a-gray", LINE)
out.append(
    '<radialGradient id="hubfill" cx="50%" cy="40%" r="65%">'
    '<stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#ede9fe"/></radialGradient>'
)
out.append("</defs>")
out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')

# ---------------------------------------------------------------- title
t(W / 2, 58, "Aida at the Center of the Beamline AI Ecosystem", size=40, weight="bold", anchor="middle")
t(
    W / 2, 96,
    "One AI workbench — any model you choose — connected through MCP servers to the instrument, "
    "the analysis, and everything else",
    size=20, fill=MUTED, anchor="middle",
)

# ---------------------------------------------------------------- geometry
HX, HY, HR = 960, 590, 165
LX, CW = 60, 560            # left column x, card width
RX = W - 60 - CW            # right column x

# ---------------------------------------------------------------- brains (top)
bw, bh, by = 400, 110, 158
b1x, b2x = HX - 20 - bw, HX + 20
for bx, color, tint, head, models, note in [
    (b1x, "#0369a1", "#f0f9ff", "Cloud — via ANL Argo", "Claude  ·  ChatGPT", "strongest reasoning, for hard problems"),
    (b2x, "#047857", "#ecfdf5", "Local — via Ollama", "Muse-Glimmer-30B  ·  others", "data never leaves the machine"),
]:
    rect(bx, by, bw, bh, tint, color)
    t(bx + bw / 2, by + 32, head, size=18, weight="bold", fill=color, anchor="middle")
    t(bx + bw / 2, by + 64, models, size=23, weight="bold", anchor="middle")
    t(bx + bw / 2, by + 92, note, size=15, fill=MUTED, anchor="middle", style="italic")
    sx = bx + bw / 2
    # arrow from brain box to hub rim
    ang = math.atan2(HY - (by + bh), HX - sx)
    ex, ey = HX - (HR + 8) * math.cos(ang), HY - (HR + 8) * math.sin(ang)
    line(sx, by + bh + 4, ex, ey, color, sw=3, marker="a-ink")
t(HX, by - 12, "THE BRAIN — your choice, switchable mid-conversation", size=14, weight="bold",
  fill=MUTED, anchor="middle")

# ---------------------------------------------------------------- column headers
t(LX, 300, "AT THE BEAMLINE", size=15, weight="bold", fill="#047857")
t(LX, 324, "users flow data: instrument → pyIrena → Bernardyn", size=17, fill=MUTED, style="italic")
t(RX, 300, "EVERYTHING ELSE", size=15, weight="bold", fill="#4338ca")
t(RX, 324, "the rest of a scientist's working day", size=17, fill=MUTED, style="italic")

# ---------------------------------------------------------------- left: data pipeline cards
ch, gap = 165, 62
ly = [345, 345 + ch + gap, 345 + 2 * (ch + gap)]
left = [
    ("#1d4ed8", "#eff6ff", "1", "Run the Instrument", "bait_mcp → queue server → Bluesky (usaxs-bits)",
     ["Same safe plans the GUI and command line use.",
      "Inspect: epics-mcp (PVs) · aievaluator (beam, flux, tunes)"], None),
    ("#047857", "#ecfdf5", "2", "Analyze the Data", "pyirena-mcp",
     ["Find, summarize, average, subtract, merge — then fit:",
      "Unified Fit, size distribution, modeling → NXcanSAS"], None),
    ("#c2410c", "#fff7ed", "3", "Plot the Data", "bernardyn-mcp",
     ["Guinier, Kratky, Porod views, waterfalls,",
      "publication figures (PNG / SVG / Igor ITX)"], None),
]
for (color, tint, num, title, server, lines, tag), y in zip(left, ly, strict=True):
    card(LX, y, CW, ch, color, tint, num, title, server, lines, tag)

# data-flow arrows between pipeline cards
for y0, label in [(ly[0] + ch, "Matilda reduces every scan automatically"),
                  (ly[1] + ch, "calibrated data · fit results in NXcanSAS")]:
    x = LX + 70
    line(x, y0 + 6, x, y0 + gap - 8, "#047857", sw=3.5, marker="a-green")
    t(x + 22, y0 + gap / 2 + 6, label, size=16, fill="#047857", style="italic")

# ---------------------------------------------------------------- right: general-purpose cards
rh, rgap = 132, 30
ry = [345 + i * (rh + rgap) for i in range(4)]
right = [
    ("#4338ca", "#eef2ff", "Search", "brave · web-search · scientific-papers",
     ["literature and facts — looked up, not remembered"]),
    ("#0f766e", "#f0fdfa", "Documents & Notes", "Obsidian · MS Office (Word, Excel, PowerPoint)",
     ["read papers & proposals, write reports and slides"]),
    ("#334155", "#f8fafc", "Web Browser", "Playwright",
     ["drive real web pages: portals, forms, databases"]),
    ("#a21caf", "#fdf4ff", "Mac Automation", "applescript_execute · macos_automator",
     ["Mail, Calendar, Finder — any app on the desktop"]),
]
for (color, tint, title, server, lines), y in zip(right, ry, strict=True):
    card(RX, y, CW, rh, color, tint, None, title, server, lines)
# total right height = 4*132+3*21 = 591 ; left = 3*165+2*62 = 619 -> close enough, align bottoms
# ---------------------------------------------------------------- spokes (MCP connections)
spokes = [(LX + CW, y + ch / 2) for y in ly] + [(RX, y + rh / 2) for y in ry]
for ex, ey in spokes:
    ang = math.atan2(ey - HY, ex - HX)
    sx, sy = HX + (HR + 6) * math.cos(ang), HY + (HR + 6) * math.sin(ang)
    endx = ex + (10 if ex < HX else -10)
    line(sx, sy, endx, ey, "#6d28d9", sw=2.5, marker="a-violet")
    out.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#6d28d9"/>')

# ---------------------------------------------------------------- hub
out.append(f'<circle cx="{HX}" cy="{HY}" r="{HR + 14}" fill="none" stroke="#c4b5fd" stroke-width="2" stroke-dasharray="4 6"/>')
out.append(f'<circle cx="{HX}" cy="{HY}" r="{HR}" fill="url(#hubfill)" stroke="#6d28d9" stroke-width="5"/>')
t(HX, HY - 62, "Aida", size=64, weight="bold", fill="#5b21b6", anchor="middle")
t(HX, HY - 30, "AI Data Assistant", size=18, weight="bold", fill="#6d28d9", anchor="middle")
for i, s in enumerate(["workspaces on your folders", "search of your own docs",
                       "writes & runs Python", "shows plots, reads figures",
                       "asks before it writes"]):
    t(HX, HY + 4 + i * 23, s, size=16, fill="#334155", anchor="middle")

# ---------------------------------------------------------------- footer
fy = 985
rect(LX, fy, W - 2 * LX, 62, "#f8fafc", "#cbd5e1", sw=1.5, rx=12)
out.append(f'<line x1="{LX + 30}" y1="{fy + 31}" x2="{LX + 90}" y2="{fy + 31}" stroke="#6d28d9" stroke-width="2.5"/>')
out.append(f'<circle cx="{LX + 30}" cy="{fy + 31}" r="5" fill="#6d28d9"/>')
t(LX + 104, fy + 37, "= an MCP server.", size=18, weight="bold")
t(LX + 262, fy + 37,
  "Add a server and Aida gains a skill — same conversation, same permissions, "
  "any model. Users and I share one tool; we just use different spokes.",
  size=18, fill="#334155")

svg = (
    f'<?xml version="1.0" encoding="UTF-8"?>\n<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Aida ecosystem hub: Aida in the '
    f'center, cloud (Argo: Claude, ChatGPT) or local (Ollama: Muse-Glimmer) models above, beamline '
    f'data pipeline (instrument control, pyIrena analysis, Bernardyn plotting) on the left, and '
    f'general-purpose MCP servers (search, documents, browser, Mac automation) on the right.">'
    + "".join(out) + "</svg>\n"
)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(svg)
