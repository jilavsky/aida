# Tool-call display modes — GUI implementation guide

**Written 2026-09-12.** For an agent with a working Qt environment. The
session that wrote this could not run `tests/ui` (no PySide6 in its
container), and a transcript layout change written blind is exactly the
kind of change that should not be — so the code below is complete but
unverified. Read the whole file before editing.

---

## 0. Before you start

**Environment check.** Both must pass, or stop and say so:

```bash
python -m pytest tests/ -q --ignore=tests/ui
python -m pytest tests/ui -q
```

If `tests/ui` cannot run, do not implement this. Say so and stop.

Ground rules are unchanged from `planning/gui_implementation_guide.md` §0
— no assertions on Qt's realized state, `ruff check src tests` must pass,
core never imports Qt, all Qt imports go through `aida/ui/qt/_qt.py`,
every comment must be true, nothing gets a `[x]` in `PLAN.md`.

**Four commits**, in the order below.

---

## 1. The problem

Bug report: *"Some of my chats are lots of tool calls. Basically before I
get real answer, there are so many tool calls, that I do not see the other
prior parts of the chat. […] when I am trying to read the chat to see what
and how we did, these tool calls are distracting and making finding the
chat parts difficult."*

This is **not** the earlier "rows taking pages of space" report, which was
already fixed in `tool_call_widget.py` — rows start collapsed,
`_MAX_SUMMARY_ARGS_CHARS` is down to 120, and the layout margins are
tightened. Per-row height is near its floor.

What is left is **row count**. One `ToolCallRow` per call, ~30 px each; a
Playwright or pyIrena-heavy turn makes thirty of them, ~900 px of
transcript, and the reply that prompted them has scrolled away. The
transcript stops reading as a conversation.

The constraint that shapes the fix, from the same report: *"it is important
to be able to inspect the tool calls when things go wrong — so I think
making sure I can get to them when checking what went wrong and how to
improve instructions is critical — but my users at the beamline may not be
that interested."* So: **nothing may be discarded, and inspection must stay
available retroactively** — after a failure, without re-running anything.
That rules out not building the rows; it only permits not *showing* them.

---

## 2. The fix, in one sentence

A run of consecutive tool calls is one logical episode — "the agent went
and did things" — so render it as **one collapsible group** whose header
summarizes the run, with the existing `ToolCallRow`s inside it, unchanged.

```
▸ 12 tool calls · 11 ✓ 1 ✗ · 8.4 s
```

and while the turn is running:

```
⏳ 12 tool calls · running pyirena_plot_iq…
```

Three display modes, on top of the grouping, persisted in `AppConfig`:

| mode | what the group looks like | who it is for |
|---|---|---|
| `hidden` | one dim line, `· 12 tool calls`, no tally or timing | beamline users |
| `grouped` (default) | the header above, tally + timing + live call | everyone, day to day |
| `expanded` | group opens itself; every row visible, as today | Jan, debugging |

The rows are **always built**, in every mode. A mode only sets visibility,
so switching is instant and retroactive: flip to `expanded` *after*
something goes wrong and the whole failed turn is there to inspect. That
property is the whole point — do not "optimize" it away by skipping row
construction in `hidden` mode.

`hidden` deliberately leaves a one-line stub rather than removing the
widget: a user who sees nothing at all cannot know there is anything to
open, and the stub is what makes the retroactive path discoverable. If Jan
later wants it genuinely invisible, that is `self.setVisible(False)` in
`_apply_mode` — one line — but ship the stub first.

Nothing outside `aida/ui/qt/` changes except one `AppConfig` field. No
event, agent-loop or persistence change.

---

## 3. Commit 1 — `ToolCallGroup`

New file `src/aida/ui/qt/tool_call_group.py`:

```python
"""``ToolCallGroup``: one collapsible row standing for a *run* of
consecutive tool calls.

Bug report: "there are so many tool calls, that I do not see the other
prior parts of the chat... when I am trying to read the chat to see what
and how we did, these tool calls are distracting". ``ToolCallRow`` was
already collapsed and tightened as far as it usefully goes (see that
module's comments) — what was left was sheer *count*: one row per call,
thirty calls in a browser-automation or plotting turn, and the reply that
started them is off-screen. Grouping attacks the count instead of the
height: a whole run collapses to a single line, and the transcript reads
as user/assistant turns again.

The rows themselves are unchanged and always constructed, whatever the
display mode — from the same report: "it is important to be able to
inspect the tool calls when things go wrong... but my users at the
beamline may not be that interested". So a mode here only ever changes
*visibility*, never what exists: switching to "expanded" after a failure
shows that turn's calls in full, with no re-run and nothing lost. Do not
make ``hidden`` skip building rows — that trades the one property this
feature exists to preserve for a saving nobody asked for.
"""

from __future__ import annotations

from aida.ui.qt._qt import (
    QFrame,
    QSizePolicy,
    Qt,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from aida.ui.qt.tool_call_widget import ToolCallRow

#: The three values ``AppConfig.tool_call_display`` may take. Kept here
#: rather than in config so the widget that implements them owns them;
#: ``main_window`` imports this to build the View menu and to fall back
#: when config holds something unrecognized (a hand-edited config.yaml
#: must not be able to blank the transcript).
TOOL_CALL_DISPLAY_MODES = ("hidden", "grouped", "expanded")
DEFAULT_TOOL_CALL_DISPLAY = "grouped"


class ToolCallGroup(QFrame):
    """A header line plus a hidden column of ``ToolCallRow``s.

    Built empty and grown one row at a time by ``ChatPanel`` as
    ``ToolCallStarted`` events arrive; the header re-summarizes itself on
    every add and on every ``row_finished()``.
    """

    def __init__(
        self, parent: QWidget | None = None, *, mode: str = DEFAULT_TOOL_CALL_DISPLAY
    ) -> None:
        super().__init__(parent)
        self._rows: list[ToolCallRow] = []
        self._mode = mode if mode in TOOL_CALL_DISPLAY_MODES else DEFAULT_TOOL_CALL_DISPLAY
        self.setFrameShape(QFrame.Shape.NoFrame)

        outer = QVBoxLayout(self)
        # Same tight margins as ToolCallRow, for the same reason: this
        # widget exists to save vertical space, so it must not spend a
        # default 9-11px layout margin per side reclaiming it.
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(2)

        self._header = QToolButton(self)
        self._header.setCheckable(True)
        self._header.setChecked(False)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.RightArrow)
        # Expanding/Fixed with a word-wrapping-equivalent minimum: a
        # QToolButton's size hint is its full text width, and a header
        # naming a long tool would otherwise push the whole window wider,
        # exactly as the un-wrapped QLabel did in ToolCallRow (see that
        # module's first comment). The text here is bounded by
        # _MAX_HEADER_TOOL_NAME_CHARS instead, since a tool button cannot
        # word-wrap.
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self._on_header_clicked)
        outer.addWidget(self._header)

        self._body = QWidget(self)
        body_layout = QVBoxLayout(self._body)
        # Indented left, so an expanded group visibly nests under its
        # header rather than reading as more top-level transcript.
        body_layout.setContentsMargins(14, 0, 0, 0)
        body_layout.setSpacing(2)
        self._body.setVisible(False)
        outer.addWidget(self._body)

        self._apply_mode()

    # --- building ----------------------------------------------------------

    def add_row(self, row: ToolCallRow) -> None:
        row.setParent(self._body)
        self._body.layout().addWidget(row)
        self._rows.append(row)
        self._refresh_header()

    def row_finished(self) -> None:
        """Call after any contained row's ``mark_finished`` — the header's
        tally and elapsed total are derived from the rows, so they are
        stale until something asks for a recount."""
        self._refresh_header()

    @property
    def rows(self) -> list[ToolCallRow]:
        return list(self._rows)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    # --- display mode -------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in TOOL_CALL_DISPLAY_MODES:
            mode = DEFAULT_TOOL_CALL_DISPLAY
        self._mode = mode
        self._apply_mode()
        self._refresh_header()

    @property
    def mode(self) -> str:
        return self._mode

    def _apply_mode(self) -> None:
        """A mode change wins over a hand-toggled group.

        Deliberate: the modes are how the user says "I am reading" versus
        "I am debugging", and a switch that left half the transcript in
        the old state would not answer either question. Expanding one
        group by hand is a per-group action; changing the mode is a
        statement about the whole transcript.
        """
        expanded = self._mode == "expanded"
        self._set_expanded(expanded)
        # In "hidden" the header keeps its arrow and stays clickable — the
        # stub is the only signal that anything is there to open, and
        # retroactive inspection is the requirement this feature is built
        # around. It is only styled down, not disabled.
        if self._mode == "hidden":
            self._header.setStyleSheet(
                "QToolButton { border: none; background: transparent; "
                "color: palette(mid); font-size: 10px; padding: 0px 2px; }"
            )
        else:
            self._header.setStyleSheet(
                "QToolButton { border: none; background: transparent; "
                "color: gray; font-size: 11px; padding: 1px 2px; }"
                "QToolButton:hover { color: palette(text); }"
            )

    def _on_header_clicked(self) -> None:
        self._set_expanded(self._header.isChecked())

    def _set_expanded(self, expanded: bool) -> None:
        self._header.setChecked(expanded)
        self._header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._body.setVisible(expanded)

    @property
    def is_expanded(self) -> bool:
        return self._header.isChecked()

    # --- header text --------------------------------------------------------

    def _refresh_header(self) -> None:
        self._header.setText(self.header_text())

    def header_text(self) -> str:
        """Exposed (rather than only set on the button) so tests can assert
        on the summary without reaching into Qt's realized state."""
        total = len(self._rows)
        noun = "tool call" if total == 1 else "tool calls"
        if self._mode == "hidden":
            # Deliberately bare: no tally, no timing, no running tool. The
            # beamline preset — enough to know something happened and that
            # it can be opened, nothing that invites reading.
            return f"{total} {noun}"

        pending = [row for row in self._rows if row.is_error is None and not row.is_historic]
        if pending:
            # Live feedback in one line, replacing what the individual
            # in-flight rows used to provide. Without naming the current
            # tool a slow call is indistinguishable from a hang, which is
            # the failure mode the "⏳ ... …" per-row marker existed to
            # prevent — so the name stays, just bounded.
            name = _abbreviate(pending[-1].tool_name)
            return f"⏳ {total} {noun} · running {name}…"

        ok = sum(1 for row in self._rows if row.is_error is False)
        failed = sum(1 for row in self._rows if row.is_error is True)
        parts = [f"{total} {noun}"]
        tally = []
        if ok:
            tally.append(f"{ok} ✓")
        if failed:
            tally.append(f"{failed} ✗")
        if tally:
            parts.append(" ".join(tally))
        elapsed = [row.elapsed for row in self._rows if row.elapsed is not None]
        if elapsed:
            parts.append(f"{sum(elapsed):.1f} s")
        return " · ".join(parts)


#: A tool name long enough to widen the window is truncated rather than
#: wrapped: QToolButton has no word-wrap, and an un-wrapped label's size
#: hint propagates up as a hard width floor (the bug ToolCallRow's first
#: comment documents at length).
_MAX_HEADER_TOOL_NAME_CHARS = 40


def _abbreviate(name: str) -> str:
    if len(name) <= _MAX_HEADER_TOOL_NAME_CHARS:
        return name
    return name[: _MAX_HEADER_TOOL_NAME_CHARS - 1] + "…"


__all__ = ["ToolCallGroup", "TOOL_CALL_DISPLAY_MODES", "DEFAULT_TOOL_CALL_DISPLAY"]
```

### 1b. Two accessors on `ToolCallRow`

`ToolCallGroup._refresh_header` reads state `ToolCallRow` currently keeps
private. In `src/aida/ui/qt/tool_call_widget.py`, next to the existing
`is_expanded` property:

```python
    @property
    def elapsed(self) -> float | None:
        """Seconds the call took, or ``None`` while in flight and for a
        row rebuilt from history (see ``mark_historic``) — read by
        ``ToolCallGroup`` to total a run without reaching into privates."""
        return self._elapsed

    @property
    def is_historic(self) -> bool:
        """True for a row rebuilt from persisted history rather than a
        live event pair. Distinguishes "finished, outcome unknown" from
        "still running", which both have ``is_error is None``."""
        return self._historic
```

That `is_historic` distinction matters: without it a resumed conversation's
groups all report `⏳ … running …` forever, because every historic row has
`is_error is None`.

### 1c. Tests

New `tests/ui/test_tool_call_group.py`. Cover at least:

- a group with two finished rows (one ok, one error) reports
  `"2 tool calls · 1 ✓ 1 ✗ · …"` from `header_text()`
- one unfinished row → `header_text()` starts `"⏳"` and names that tool
- all-historic rows → no `"⏳"` (this is the `is_historic` regression)
- `mode="hidden"` → `header_text() == "3 tool calls"`, nothing else
- `set_mode("expanded")` → `is_expanded` is True; `set_mode("grouped")` →
  False; `_body.isVisibleTo(group)` follows (immediate parent only — §0)
- `set_mode("wobble")` falls back to `grouped` rather than raising
- a tool name of 200 chars is truncated in `header_text()`

---

## 4. Commit 2 — wire it into `ChatPanel`

`src/aida/ui/qt/chat_panel.py`.

**Import** `from aida.ui.qt.tool_call_group import DEFAULT_TOOL_CALL_DISPLAY, ToolCallGroup`.

**In `__init__`**, beside `self._tool_rows`:

```python
        # A run of consecutive tool calls shares one collapsible group —
        # see ToolCallGroup's docstring for the bug report. None means "no
        # run is open": _append_widget clears it for any non-group widget,
        # so an assistant reply, an image or an error between two calls
        # ends the run and the next call starts a fresh group, which is
        # what makes a group correspond to one episode rather than to the
        # whole conversation.
        self._current_tool_group: ToolCallGroup | None = None
        # call_id -> the group holding that row, so ToolCallFinished can
        # tell the right header to recount without walking the layout.
        self._tool_groups: dict[str, ToolCallGroup] = {}
        self._tool_display_mode = DEFAULT_TOOL_CALL_DISPLAY
```

**In `_append_widget`**, first line:

```python
    def _append_widget(self, widget: QWidget) -> None:
        if not isinstance(widget, ToolCallGroup):
            # Anything that is not the group itself ends the current run —
            # see _current_tool_group's comment in __init__.
            self._current_tool_group = None
        self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
        self._scroll_to_bottom()
```

**New helper**, next to `_append_widget`:

```python
    def _ensure_tool_group(self) -> ToolCallGroup:
        if self._current_tool_group is None:
            group = ToolCallGroup(parent=self._content, mode=self._tool_display_mode)
            self._append_widget(group)  # clears _current_tool_group; set it after
            self._current_tool_group = group
        return self._current_tool_group
```

The ordering comment is load-bearing — `_append_widget` sets the field to
`None` for non-group widgets only, but assigning before the call would
still read oddly. Assign after.

**`ToolCallStarted` branch** — replace the `self._append_widget(row)` line:

```python
        elif name == "ToolCallStarted":
            row = ToolCallRow(
                call_id=event.call_id,
                tool_name=event.tool_name,
                arguments=event.arguments,
                parent=self._content,
            )
            self._tool_rows[event.call_id] = row
            group = self._ensure_tool_group()
            group.add_row(row)
            self._tool_groups[event.call_id] = group
            self._scroll_to_bottom()
```

**`ToolCallFinished` branch**:

```python
        elif name == "ToolCallFinished":
            row = self._tool_rows.get(event.call_id)
            if row is not None:
                row.mark_finished(result=event.result, is_error=event.is_error)
            group = self._tool_groups.get(event.call_id)
            if group is not None:
                group.row_finished()
```

**`load_history`**, the `message.role == "tool"` branch — same change:

```python
            if message.role == "tool":
                row = ToolCallRow(
                    call_id=message.tool_call_id or "",
                    tool_name=message.name or "tool",
                    arguments=call_arguments.get(message.tool_call_id, {}),
                    parent=self._content,
                )
                row.mark_historic(result=message.content)
                self._ensure_tool_group().add_row(row)
```

Consecutive tool messages land in one group for free, because everything
else in the loop goes through `_append_widget`, which ends the run.

**`clear()`** — add to the three lines at the end:

```python
        self._current_tool_group = None
        self._tool_groups.clear()
```

**New public method**, in the `--- public API ---` section:

```python
    def set_tool_display_mode(self, mode: str) -> None:
        """Apply one of ``TOOL_CALL_DISPLAY_MODES`` to the whole transcript,
        including groups already on screen.

        Retroactive on purpose: the rows exist in every mode, so switching
        to "expanded" after a turn has already gone wrong shows that turn's
        calls in full — the inspection path the bug report calls critical.
        New groups created after this call inherit the mode too.
        """
        self._tool_display_mode = mode
        for i in range(self._content_layout.count() - 1):
            widget = self._content_layout.itemAt(i).widget()
            if isinstance(widget, ToolCallGroup):
                widget.set_mode(mode)

    @property
    def tool_display_mode(self) -> str:
        return self._tool_display_mode
```

**`__all__`** gains `"ToolCallGroup"`? No — leave it; the group is imported
from its own module. Do not re-export.

### Existing tests this breaks

Only tests that put a tool call in the transcript: a group now sits at the
index the row used to occupy. In `tests/ui/test_chat_panel.py` that is the
`ToolCallRow`-related cases around lines 177–186, 215, 367–376, 422–430,
441, 472, 594–595, 755; `tests/ui/test_main_window.py` has a handful more.
Tests with only bubbles and artifacts are untouched — no group is created
when there are no tool calls.

Migrate them by reaching through the group rather than by asserting the
old flat layout:

```python
    group = panel.widget_at(1)
    assert isinstance(group, ToolCallGroup)
    row = group.rows[0]
```

Add a `ChatPanel` test that a run of three calls produces **one** widget,
that a bubble between two calls produces **two** groups, and that
`set_tool_display_mode("expanded")` opens a group created before the call.

---

## 5. Commit 3 — persist the choice

`src/aida/config/settings.py`, in `AppConfig` beside `collapsed_panels`:

```python
    # How the chat transcript renders tool calls: "hidden" (one dim stub
    # line per run), "grouped" (the default — one collapsible line with
    # the run's tally and timing) or "expanded" (every row visible, the
    # pre-2026-09 behavior). Bug report: a turn with thirty tool calls
    # pushed the actual conversation off-screen; beamline users reading a
    # session do not want them, and Jan debugging one does. The rows are
    # built in every mode, so this only ever changes what is *shown* and
    # can be flipped after the fact — see aida.ui.qt.tool_call_group.
    # An unrecognized value falls back to "grouped" at the UI boundary
    # rather than here, so a hand-edited config.yaml cannot blank the
    # transcript.
    tool_call_display: str = DEFAULT_TOOL_CALL_DISPLAY
```

Config must not import Qt (`test_qt_contract.py`), and
`DEFAULT_TOOL_CALL_DISPLAY` lives in a Qt module — so **do not import it
here**. Write the literal `"grouped"` and note in the comment that
`aida.ui.qt.tool_call_group.DEFAULT_TOOL_CALL_DISPLAY` must match. Then add
to `to_dict`:

```python
            "tool_call_display": self.tool_call_display,
```

and to `_APP_FIELD_KINDS`:

```python
    "tool_call_display": "str",
```

`tests/test_settings.py` already asserts those two stay in sync, so a
missed entry fails there rather than silently dropping the setting.

---

## 6. Commit 4 — the View menu

`src/aida/ui/qt/main_window.py`.

`QActionGroup` is **not** currently exported from `aida/ui/qt/_qt.py`
(verified 2026-09-12) — add it to both the `PySide6.QtGui` import list and
`__all__` there, alongside `QAction`. `QAction`, `QMenu`, `QToolButton`,
`QSizePolicy` and `QFrame` are already exported; import all of them from
`_qt.py`, never from `PySide6` directly (`tests/ui/test_qt_contract.py`).

In `_build_menu_bar`, after `reset_widths_action` and before the Help menu:

```python
        view_menu.addSeparator()
        # Bug report: "there are so many tool calls, that I do not see the
        # other prior parts of the chat... my users at the beamline may not
        # be that interested and want relatively short version". Three
        # modes rather than a single on/off, because "readable" and
        # "invisible" are different asks — see
        # aida.ui.qt.tool_call_group. Exclusive and checkable, so the menu
        # also reports the current mode, same pattern as the column
        # toggles above.
        tool_calls_menu = QMenu("Tool Calls", view_menu)
        view_menu.addMenu(tool_calls_menu)
        self._tool_display_group = QActionGroup(self)
        self._tool_display_group.setExclusive(True)
        self._tool_display_actions: dict[str, QAction] = {}
        for mode, label in (
            ("hidden", "Hidden"),
            ("grouped", "Grouped"),
            ("expanded", "Expanded"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(mode)
            self._tool_display_group.addAction(action)
            tool_calls_menu.addAction(action)
            self._tool_display_actions[mode] = action
        # Bound method, not a lambda capturing self — see the column
        # toggles' comment for why that matters here.
        self._tool_display_group.triggered.connect(self._on_tool_display_mode_chosen)
```

New methods, next to `_on_section_toggled`:

```python
    # --- tool-call display ---------------------------------------------------

    def _apply_tool_display_mode(self) -> None:
        """Push the saved mode into the transcript and tick the menu.

        Called once at startup and after every change. An unrecognized
        value — a hand-edited config.yaml, or a setting written by a newer
        AIDA — falls back to the default here rather than reaching the
        widget, so config can never leave the user with a transcript whose
        state no menu item claims.
        """
        mode = self.settings.app.tool_call_display
        if mode not in TOOL_CALL_DISPLAY_MODES:
            mode = DEFAULT_TOOL_CALL_DISPLAY
            self.settings.app.tool_call_display = mode
        self.chat_panel.set_tool_display_mode(mode)
        self._tool_display_actions[mode].setChecked(True)

    def _on_tool_display_mode_chosen(self, action: QAction) -> None:
        mode = action.data()
        if mode == self.settings.app.tool_call_display:
            return
        self.settings.app.tool_call_display = mode
        self.chat_panel.set_tool_display_mode(mode)
        # Saved immediately rather than on close, same reason
        # _on_section_toggled is: a setting the user changed by hand should
        # survive a crash, not only a clean exit.
        save_app_config(self.settings.app)
```

Import `DEFAULT_TOOL_CALL_DISPLAY` and `TOOL_CALL_DISPLAY_MODES` from
`aida.ui.qt.tool_call_group`.

Call `self._apply_tool_display_mode()` once during construction, **after**
both `self.chat_panel` is built (line ~320) and `_build_menu_bar` has run —
it touches both. Put it with the other restore-from-config calls.

`clear()` and `load_history` need no extra handling: new groups read
`ChatPanel._tool_display_mode`, which `set_tool_display_mode` keeps
current.

### Tests

In `tests/ui/test_main_window.py`:

- the three actions exist, are checkable, and exactly one is checked
- the one checked matches `settings.app.tool_call_display` at startup
- triggering "Hidden" sets the config field, calls through to the panel
  (`window.chat_panel.tool_display_mode == "hidden"`) and saves
- a config carrying `tool_call_display: "nonsense"` starts up on
  `"grouped"` with the Grouped action checked and the field corrected

---

## 7. Docs

- `CHANGELOG.md` — one entry under the next beta.
- `docs/` — wherever the GUI tour lives, one paragraph: what the grouped
  line means, that clicking it opens the calls, and that
  **View → Tool Calls → Expanded** is how you inspect a turn that went
  wrong. That last sentence is the one a beamline user will need when they
  send Jan a bug report.
- Move the finished item out of `PLAN.md` into `planning/COMPLETED.md`
  if you added one there; do not tick a box in `PLAN.md`.
