"""One-off update of the two USAXS diagrams for the October 2026 releases.

Bernardyn 1.0.0 (plots saved pyIrena results, ships bernardyn-mcp), pyIrena 1.2.0
(MCP can fit and manipulate data). Also fixes label/line overlaps in the expert
diagram and makes room for its legend. Every replacement must match exactly once.
"""

from pathlib import Path

HERE = Path(__file__).parent
FONT = 'font-family="Helvetica, Arial, sans-serif"'


def apply(path: Path, edits: list[tuple[str, str]]) -> None:
    s = path.read_text(encoding="utf-8")
    for old, new in edits:
        n = s.count(old)
        if n != 1:
            raise SystemExit(f"{path.name}: expected 1 match, found {n}: {old[:80]!r}")
        s = s.replace(old, new)
    path.write_text(s, encoding="utf-8")
    print(f"{path.name}: {len(edits)} edits")


def txt(x, y, s, size=11, fill="#475569", style="normal", anchor="start"):
    return (f'<text x="{x}" y="{y}" {FONT} font-size="{size}" font-weight="normal" '
            f'font-style="{style}" fill="{fill}" text-anchor="{anchor}">{s}</text>')


# ---------------------------------------------------------------- user diagram
apply(HERE / "usaxs-user-diagram.svg", [
    (">Ask Aida to summarize, tabulate,<", ">Ask Aida to fit, summarize, tabulate,<"),
    (">Igor Pro is today's tool for publication-quality figures.<",
     ">Bernardyn 1.0 plots your reduced data and saved pyIrena fits — or ask Aida to plot them.<"),
    (">+ Bernardyn 1.0 — a newer, dedicated plotting app, worth trying<",
     ">+ Igor Pro, the long-standing tool, works just as well<"),
])

# ---------------------------------------------------------------- expert diagram
NEW_H = 1440
LEG_OLD, LEG_NEW = 'y="1374"', 'y="1420"'
expert = HERE / "usaxs-expert-diagram.svg"
s = expert.read_text(encoding="utf-8")
n_leg = s.count(LEG_OLD)
assert n_leg == 10, n_leg  # 10 legend text elements, nothing else at that y
expert.write_text(s.replace(LEG_OLD, LEG_NEW), encoding="utf-8")

apply(expert, [
    # canvas taller so the legend clears the Plotting box
    ('width="1480" height="1400" viewBox="0 0 1480 1400"',
     f'width="1480" height="{NEW_H}" viewBox="0 0 1480 {NEW_H}"'),
    ('<rect x="0" y="0" width="1480" height="1400"', f'<rect x="0" y="0" width="1480" height="{NEW_H}"'),
    ("·  September 2026<", "·  October 2026<"),
    ('driving one queue server, the data-reduction, analysis, and plotting pipeline that follows."',
     'driving one queue server, the data-reduction, analysis, and plotting pipeline that follows; '
     'Aida reaches pyIrena and Bernardyn through their MCP servers."'),
    # Igor direct-to-queue line no longer cuts through the run_command_file box
    ('points="620,216 620,308 440,308 440,560"', 'points="410,216 410,308 440,308 440,560"'),
    # command-line label moved out of its box, split so it clears the green line
    (txt("258.0", "485.0", "typed commands — device I/O + data"),
     txt("258", "515", "typed commands —") + txt("258", "529", "device I/O + data")),
    # "polls Tiled server" no longer sits on its arrow
    (txt("783.0", "890.0", "polls Tiled server", fill="#b45309"),
     txt("820", "900", "polls Tiled server", fill="#b45309")),
    # auto-fit line starts below the daemon box instead of crossing its text; drop duplicate
    ('<polyline points="450,968 450,1148" fill="none" stroke="#047857" stroke-width="1.6" '
     'stroke-dasharray="3,3" marker-end="url(#arrow-green)"/>', ""),
    ('<line x1="450" y1="968" x2="450" y2="1148"', '<line x1="450" y1="1020" x2="450" y2="1148"'),
    (txt("200", "1058", "auto (pyirena_config.json)", size=10, fill="#047857"),
     txt("458", "1074", "auto (pyirena_config.json)", size=10, fill="#047857")),
    # pyIrena MCP now fits and manipulates data too
    (">pyirena-mcp — summarize,<", ">pyirena-mcp — read, fit,<"),
    (">tabulate, plot for an agent<", ">manipulate data for an agent<"),
    # Aida also reaches Bernardyn (arrow appended at the end below, so it draws over the Plotting box)
    ('<polyline points="1400,420 1460,420 1460,1174 1160,1174" fill="none" stroke="#6d28d9" '
     'stroke-width="1.6" stroke-dasharray="3,3" marker-end="url(#arrow-violet)"/>',
     '<polyline points="1400,420 1460,420 1460,1174 1160,1174" fill="none" stroke="#6d28d9" '
     'stroke-width="1.6" stroke-dasharray="3,3" marker-end="url(#arrow-violet)"/>'
    ),
    ("</svg>", '<polyline points="1460,1174 1460,1343 1130,1343" fill="none" stroke="#6d28d9" '
     'stroke-width="1.6" stroke-dasharray="3,3" marker-end="url(#arrow-violet)"/></svg>'),
    (">pyirena-mcp for analysis<", ">pyirena-mcp and<"),
    (">Q&amp;A — one agent,<", ">bernardyn-mcp — one agent,<"),
    (">two domains<", ">three domains<"),
    # Plotting box: both tools are current releases
    (">current production tool for<", ">long-standing tool for<"),
    (">plots SAS data now; will also plot<", ">reduced data + saved pyIrena results;<"),
    (">pyIrena fit results — planned next<", ">GUI, CLI, and bernardyn-mcp<"),
])
