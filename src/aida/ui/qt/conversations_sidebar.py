"""``ConversationsSidebar`` (PLAN.md Phase 5): "Conversations sidebar: list
(title, date, workspace), open/resume, delete with confirmation, cleanup
dialog (older-than picker)".

Deliberately dumb about persistence: it's fed plain
``aida.persistence.store.ConversationSummary`` objects
(``set_conversations``) and only emits *requests* (``resume_requested``,
``delete_requested``) — ``aida.ui.qt.main_window`` is the one place that
actually calls into ``aida.persistence``/``aida.cli.conversations``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from aida.persistence.store import ConversationSummary
from aida.ui.qt._qt import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFont,
    QFontMetrics,
    QFormLayout,
    QGridLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPainter,
    QPalette,
    QPushButton,
    QRect,
    QSize,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)

ALL_USERS_LABEL = "All users"

#: Item data the row delegate paints from. The item's own ``text()`` stays
#: the one-line ``_row_label`` (accessibility, tooltip); these are the two
#: lines actually drawn, plus the group-header marker.
TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_HEADER_ROLE = Qt.ItemDataRole.UserRole + 3

#: Shown for a conversation whose title is NULL — only reachable now for a
#: row written before titles existed, since the recorder derives one from
#: the first user message.
UNTITLED_LABEL = "(untitled)"

#: The group a conversation with an unparseable ``updated_at`` falls into.
#: Should never appear in practice; it exists so a hand-edited or foreign
#: DB row lands somewhere visible instead of crashing the grouping.
UNDATED_GROUP = "Undated"

#: How narrow the user is allowed to drag this column. Bug report: "Left
#: one is fixed width or hidden ... I cannot fit this on smaller screens."
#: It was never *declared* fixed — the four action buttons sat in one row,
#: and a QSplitter cannot shrink a pane below its layout's minimum, so that
#: row's combined width was the floor and the only way past it was to
#: collapse the pane entirely. The buttons now wrap 2x2 and ignore their
#: text width when shrinking (see ``__init__``), which leaves this as the
#: real floor: small enough to fit a laptop screen, large enough that the
#: column still shows a usable slice of a conversation title rather than
#: becoming an unreadable sliver the user has to drag back out again.
MIN_SIDEBAR_WIDTH = 140

#: The conversations that carry no user label at all — everything created
#: before the feature existed, and anything a user cleared. Reachable on
#: its own rather than only via "All users", because otherwise the only way
#: to see unlabelled work is to see *everyone's*.
NO_USER_LABEL = "(no user)"


def _matches(summary: ConversationSummary, query: str) -> bool:
    """Match anything the conversation row visibly identifies.

    The workspace is shown beside the title, so a search that ignored it
    looked broken rather than deliberately narrow.  User labels are useful
    search terms for the same reason.
    """
    haystacks = (summary.title, summary.workspace_name, summary.user)
    return any(query in (value or "").lower() for value in haystacks)


def _format_timestamp(iso_str: str) -> str:
    """Local short date/time for a sidebar row — bug report: "these
    date/times are not very convenient to use" (row labels used to show the
    raw UTC ISO-8601 string, e.g. "2026-08-22T14:03:22.123456+00:00...").
    ``updated_at`` is always written by ``aida.persistence.recorder._now_iso``
    (``datetime.now(UTC).isoformat()``); shown here converted to the
    viewer's own local timezone as e.g. "Aug 22 09:03". Falls back to the
    raw string on anything unparseable — a hand-edited or foreign DB row
    must not crash the sidebar."""
    try:
        parsed = datetime.fromisoformat(iso_str)
    except (TypeError, ValueError):
        return iso_str
    return parsed.astimezone().strftime("%b %d %H:%M")


def _row_label(summary: ConversationSummary) -> str:
    """The whole row on one line.

    No longer what the list *paints* — ``_ConversationRowDelegate`` draws
    the title and the subtitle separately — but still what each item
    carries as its ``text()``: it is the accessible name a screen reader
    announces, and the string the tooltip shows.
    """
    title = summary.title or UNTITLED_LABEL
    workspace = summary.workspace_name or "-"
    when = _format_timestamp(summary.updated_at)
    return f"{when}  [{workspace}]  {title}"


def _local_datetime(iso_str: str) -> datetime | None:
    """``updated_at`` in the viewer's own timezone, or ``None`` if the
    string isn't a timestamp at all — a hand-edited or foreign DB row must
    never crash the sidebar (same rule ``_format_timestamp`` follows)."""
    try:
        return datetime.fromisoformat(iso_str).astimezone()
    except (TypeError, ValueError):
        return None


def _relative_when(iso_str: str, *, now: datetime | None = None) -> str:
    """How recent this conversation is, at the precision that is actually
    useful at that distance.

    An absolute "Aug 22 09:03" is the same eleven characters whether the
    conversation was an hour ago or last spring, and reading it costs the
    user a subtraction every time. Today's work is identified by its time
    of day, this week's by its weekday, and anything older by its date —
    with the year only once it stops being obvious.

    ``now`` is injectable so the tests are not written against the clock.
    """
    local = _local_datetime(iso_str)
    if local is None:
        return iso_str
    now = (now or datetime.now()).astimezone()
    days = (now.date() - local.date()).days
    if days <= 0:
        return local.strftime("%H:%M")
    if days == 1:
        return f"Yesterday {local:%H:%M}"
    if days < 7:
        return local.strftime("%a %H:%M")
    if local.year == now.year:
        return local.strftime("%b %d")
    return local.strftime("%b %d, %Y")


def _date_group(iso_str: str, *, now: datetime | None = None) -> str:
    """Which band of the list this conversation belongs under.

    Coarser than ``_relative_when`` on purpose: the headers exist to break
    a long list into a handful of scannable blocks, so anything older than
    a week collapses into its month rather than producing a header per
    day.
    """
    local = _local_datetime(iso_str)
    if local is None:
        return UNDATED_GROUP
    now = (now or datetime.now()).astimezone()
    days = (now.date() - local.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return "Previous 7 days"
    return local.strftime("%B %Y")


def _row_subtitle(
    summary: ConversationSummary, *, group: str | None = None, now: datetime | None = None
) -> str:
    """The dim second line: when, where, and whose. Empty parts are
    dropped rather than shown as "-", so an install that uses no user
    labels doesn't carry a column of placeholders down the whole list.

    ``group`` is the header this row sits under, so the two don't stutter:
    "Yesterday 17:44" directly beneath a "YESTERDAY" heading says the word
    twice, and the time alone is the only part carrying information there.
    Passing nothing gives the standalone form, which is what a row needs
    when it is read outside the list.
    """
    when = _relative_when(summary.updated_at, now=now)
    if group == "Yesterday":
        local = _local_datetime(summary.updated_at)
        if local is not None:
            when = local.strftime("%H:%M")
    parts = [when, summary.workspace_name or "", summary.user or ""]
    return " · ".join(part for part in parts if part)


class _ConversationRowDelegate(QStyledItemDelegate):
    """Paints a conversation as two lines — the title, then everything
    that identifies *which* one it is.

    Bug report: "the display in the Conversation column is not very
    helpful. Could we make it into two lines — title first and
    date-time/workspace second?" The single line it replaces
    (``_row_label``) led with the timestamp and the workspace, so on a
    narrow column — which this one is designed to be draggable down to,
    see ``MIN_SIDEBAR_WIDTH`` — the elide ate the title, the only part
    worth reading.

    A delegate rather than ``setItemWidget``: a per-row widget costs a
    layout and a paint tree per conversation (this list grows into the
    hundreds) and has to re-derive the selection colours by hand. Painting
    straight from ``option.palette`` also means light and dark system
    themes both come out right, which matters because AIDA does no
    theming of its own and relies entirely on Qt's native styling.
    """

    _PADDING_X = 6
    _PADDING_Y = 4
    _HEADER_PADDING_TOP = 8

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        title_font = QFont(option.font)
        title_font.setBold(True)
        subtitle_font = QFont(option.font)
        subtitle_font.setPointSizeF(max(6.0, option.font.pointSizeF() * 0.85))
        return title_font, subtitle_font

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802 - Qt override
        self.initStyleOption(option, index)
        title_font, subtitle_font = self._fonts(option)
        if index.data(GROUP_HEADER_ROLE):
            height = QFontMetrics(subtitle_font).height() + self._HEADER_PADDING_TOP
            return QSize(0, height)
        height = (
            QFontMetrics(title_font).height()
            + QFontMetrics(subtitle_font).height()
            + 2 * self._PADDING_Y
        )
        # Width 0, deliberately: a QListWidget sizes itself to the widest
        # sizeHint it is given, so reporting the text's natural width here
        # would re-impose exactly the column-width floor MIN_SIDEBAR_WIDTH
        # exists to remove. The painting below elides to whatever width
        # the view actually hands it.
        return QSize(0, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        self.initStyleOption(option, index)
        title_font, subtitle_font = self._fonts(option)
        painter.save()

        group = index.data(GROUP_HEADER_ROLE)
        if group:
            # No selection background and no hover: a header is a label,
            # and it is already unselectable (Qt.ItemFlag.NoItemFlags).
            painter.setFont(subtitle_font)
            painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
            rect = option.rect.adjusted(self._PADDING_X, self._HEADER_PADDING_TOP, -2, 0)
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                QFontMetrics(subtitle_font).elidedText(
                    group.upper(), Qt.TextElideMode.ElideRight, rect.width()
                ),
            )
            painter.restore()
            return

        # Selection/hover background, drawn by the active style so it
        # matches every other list in the app (and the platform).
        style = option.widget.style() if option.widget is not None else None
        if style is not None:
            style.drawPrimitive(
                QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget
            )

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        title_color = option.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        # Dim, but still legible on the highlight — the highlighted text
        # colour is the only one guaranteed to contrast with a selected
        # row's background, so a selected subtitle keeps it rather than
        # dropping to Mid and disappearing into the highlight.
        subtitle_color = (
            option.palette.color(QPalette.ColorRole.HighlightedText)
            if selected
            else option.palette.color(QPalette.ColorRole.Mid)
        )

        width = option.rect.width() - 2 * self._PADDING_X
        x = option.rect.left() + self._PADDING_X
        y = option.rect.top() + self._PADDING_Y

        title_metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(title_color)
        painter.drawText(
            QRect(x, y, width, title_metrics.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title_metrics.elidedText(
                str(index.data(TITLE_ROLE) or UNTITLED_LABEL),
                Qt.TextElideMode.ElideRight,
                width,
            ),
        )

        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        if subtitle:
            subtitle_metrics = QFontMetrics(subtitle_font)
            painter.setFont(subtitle_font)
            painter.setPen(subtitle_color)
            painter.drawText(
                QRect(x, y + title_metrics.height(), width, subtitle_metrics.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                subtitle_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, width),
            )

        painter.restore()


class CleanupDialog(QDialog):
    """ "Older than N days" picker — a static-method convenience
    (``get_cutoff_days``) mirrors Qt's own ``QInputDialog.getInt`` pattern:
    construct, ask, tear down, all in one call for the common case, while
    the class itself stays directly testable (no ``exec()`` needed) for
    anything more specific."""

    def __init__(self, parent: QWidget | None = None, *, default_days: int = 30) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clean Up Old Conversations")
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._days_spin = QSpinBox(self)
        self._days_spin.setRange(1, 3650)
        self._days_spin.setValue(default_days)
        form.addRow("Delete conversations older than (days):", self._days_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def days(self) -> int:
        return self._days_spin.value()

    @staticmethod
    def get_cutoff_days(parent: QWidget | None = None, *, default_days: int = 30) -> int | None:
        dialog = CleanupDialog(parent, default_days=default_days)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.days()
        return None


class ConversationsSidebar(QWidget):
    resume_requested = Signal(str)  # conversation_id
    delete_requested = Signal(str)  # conversation_id, already confirmed
    cleanup_requested = Signal(int)  # cutoff in days, already confirmed
    # Bug report: "Can we have the chat list in the history column have
    # some kind of names? ... these date/times are not very convenient to
    # use." set_title already exists on ConversationStore (used once, by
    # auto-titling) — this is the missing "rename it again" entry point.
    rename_requested = Signal(str, str)  # conversation_id, new_title
    #: A snapshot export was requested for one past conversation, distinct
    #: from resume_requested — this never opens the conversation, just
    #: writes its transcript somewhere the user picks. MainWindow owns the
    #: destination/options dialog; this widget only knows which id.
    export_requested = Signal(str)  # conversation_id
    # Bug report: "Enable multiple file selection ... useful for deleting
    # multiple chats." A separate signal from delete_requested (rather than
    # a list there too) keeps every existing single-delete connection/test
    # unchanged — MainWindow just adds one more connection, mirroring
    # _on_cleanup_requested's own "loop then refresh once" shape.
    delete_many_requested = Signal(list)  # list[str] of conversation_ids, already confirmed
    #: (conversation_ids, user) — move chats to a label, "" to unlabel.
    #: The repair for the mistake a free-text label makes easy: having the
    #: wrong name selected when a conversation was started. Nothing else
    #: could fix it — rename_user moves *everything* a name owns.
    move_to_user_requested = Signal(list, str)
    #: The search box's live text, re-emitted verbatim. This widget stays
    #: "dumb about persistence" (see class docstring) — it cannot itself
    #: query ``messages.content`` (``aida.persistence.store.
    #: ConversationStore.search_conversations``), so ``MainWindow`` does
    #: that and hands the matching ids back via ``set_content_matches``.
    search_query_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ids_by_row: list[str] = []
        self._titles_by_row: list[str] = []
        # U5: the full, unfiltered set from the last set_conversations() —
        # _apply_filter() re-derives the visible rows from this, so a
        # refresh (resume/delete/rename/cleanup all call set_conversations
        # again) re-applies whatever the user has typed instead of
        # silently clearing it.
        self._all_summaries: list[ConversationSummary] = []
        #: The active name at the last refresh, so the filter follows a
        #: real switch without stamping on a choice made here.
        self._last_active_user: str | None = None
        self._known_users: list[str] = []
        #: Ids ``MainWindow`` reported as having a matching message,
        #: for the search text currently in the box — ``None`` means "no
        #: content search has run for this text" (too short, or none typed
        #: yet), which must not be confused with "ran and found nothing" (an
        #: empty set, still merged in below and changing nothing).
        self._content_match_ids: set[str] | None = None

        layout = QVBoxLayout(self)
        # Everything below is built to *shrink*: the column's width is the
        # user's to choose (MIN_SIDEBAR_WIDTH), so no child may quietly
        # impose a wider floor on the splitter than that.
        self.setMinimumWidth(MIN_SIDEBAR_WIDTH)

        # User labels organize shared work; they are not access control.
        # Keep "All users" one click away so selecting a label can never
        # make another person's conversations look lost.
        self._user_filter = QComboBox(self)
        self._user_filter.addItem(ALL_USERS_LABEL)
        self._user_filter.currentTextChanged.connect(
            lambda _text: self._apply_filter(self._search_edit.text())
        )
        # A combo box sizes itself to its longest entry by default, so one
        # long user label would have set the whole column's minimum width.
        self._user_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._user_filter.setMinimumContentsLength(6)
        layout.addWidget(self._user_filter)

        # U5 bug report follow-up: "the list grows fast in real use" — a
        # substring filter over the title, applied live as the user types.
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Search conversations…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.setMinimumWidth(0)
        layout.addWidget(self._search_edit)

        self._list = QListWidget(self)
        # Bug report: "Enable multiple file selection (usual shift click to
        # select range and ctrl/cmd click to select specific ones) useful
        # for deleting multiple chats." ExtendedSelection is exactly that
        # standard shift-range / ctrl-toggle behavior, built into Qt.
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        # Bug report: "Add meaningful ... button functions to the right
        # click (rename, resume, delete)."
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu_requested)
        # Row labels are long and a QListWidget would otherwise ask for the
        # widest of them; the text elides instead (the delegate does the
        # eliding now, per line), and the full label stays available as a
        # tooltip for whatever the narrow column cuts off.
        self._list.setMinimumWidth(0)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        # Two-line rows under date-group headers — see
        # _ConversationRowDelegate. Kept as an attribute because a delegate
        # is not parented to the view it is set on: dropping the only
        # Python reference lets it be garbage-collected and leaves the view
        # painting with a destroyed C++ object.
        self._row_delegate = _ConversationRowDelegate(self._list)
        self._list.setItemDelegate(self._row_delegate)
        layout.addWidget(self._list)

        # 2x2 rather than one row of four: half the width for the same four
        # actions. Each button additionally ignores its own text width when
        # the layout shrinks (QSizePolicy.Ignored contributes 0 to the
        # layout's minimum), which is what actually lets the column reach
        # MIN_SIDEBAR_WIDTH — the labels clip there, so every button also
        # carries a tooltip, and each action already has a right-click menu
        # entry that never clips at all.
        buttons = QGridLayout()
        self._resume_button = QPushButton("Resume", self)
        self._resume_button.clicked.connect(self._on_resume_clicked)
        buttons.addWidget(self._resume_button, 0, 0)

        self._delete_button = QPushButton("Delete…", self)
        self._delete_button.clicked.connect(self._on_delete_clicked)
        buttons.addWidget(self._delete_button, 0, 1)

        self._rename_button = QPushButton("Rename…", self)
        self._rename_button.clicked.connect(self._on_rename_clicked)
        buttons.addWidget(self._rename_button, 1, 0)

        self._cleanup_button = QPushButton("Clean Up…", self)
        self._cleanup_button.clicked.connect(self._on_cleanup_clicked)
        buttons.addWidget(self._cleanup_button, 1, 1)
        for button in (
            self._resume_button,
            self._delete_button,
            self._rename_button,
            self._cleanup_button,
        ):
            button.setToolTip(button.text())
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addLayout(buttons)

    def set_conversations(
        self, summaries: Iterable[ConversationSummary], *, active_user: str | None = None
    ) -> None:
        """Refresh the list.

        ``active_user`` makes the filter *follow* the toolbar: switching to
        a name in the toolbar and then not having the list change was the
        first thing everybody tried. It only re-selects when the active
        name actually changed since the last refresh, so an explicit
        "All users" (or another name) picked here survives an ordinary
        refresh and is only overridden by a real switch.
        """
        # Bug report: "Let's not add in this list ... conversations which
        # have no messages in them. Currently there are conversations which
        # are empty (were created on start or workspace change and never
        # used)." A ChatSession's recorder creates its conversation row up
        # front, before any message exists (see MainWindow's
        # _delete_conversation_if_empty for the matching auto-delete side
        # of this) — filtering here also retroactively hides any already-
        # accumulated empty rows from *before* that existed, with no
        # migration needed.
        summaries = list(summaries)
        self._all_summaries = [s for s in summaries if s.message_count > 0]
        selected = self._user_filter.currentText()
        names = sorted({summary.user for summary in summaries if summary.user})
        if active_user and active_user not in names:
            # A freshly created name has no conversations yet, but the
            # filter still has to be able to show it — as an empty list,
            # which is the honest answer.
            names = sorted([*names, active_user])
        has_unlabelled = any(summary.user is None for summary in self._all_summaries)

        if active_user is not None and active_user != self._last_active_user:
            selected = active_user or ALL_USERS_LABEL
            self._last_active_user = active_user

        self._user_filter.blockSignals(True)
        self._user_filter.clear()
        self._user_filter.addItem(ALL_USERS_LABEL)
        self._user_filter.addItems(names)
        if has_unlabelled:
            self._user_filter.addItem(NO_USER_LABEL)
        index = self._user_filter.findText(selected)
        self._user_filter.setCurrentIndex(index if index >= 0 else 0)
        self._user_filter.setVisible(bool(names))
        self._user_filter.blockSignals(False)
        self._apply_filter(self._search_edit.text())

    def _on_search_text_changed(self, text: str) -> None:
        """Every keystroke in the search box.

        Clears any previous ``set_content_matches`` result *before*
        re-applying the filter and telling ``MainWindow`` about the new
        text — those matches were computed for the query that just
        changed, not this one, and would otherwise keep showing a stale
        conversation until ``MainWindow``'s (synchronous, but still a
        separate call) response for the new text arrives.
        """
        self._content_match_ids = None
        self._apply_filter(text)
        self.search_query_changed.emit(text)

    def set_content_matches(self, ids: set[str] | None) -> None:
        """Ids ``MainWindow`` found with a matching message for the current
        search text (``None`` to clear — see ``_content_match_ids``'s
        docstring for the ``None`` vs. empty-set distinction). Re-applies
        the filter immediately so results appear as soon as the query comes
        back, not only on the next keystroke."""
        self._content_match_ids = ids
        self._apply_filter(self._search_edit.text())

    def _apply_filter(self, query: str) -> None:
        query = query.strip().lower()
        if not query:
            visible = self._all_summaries
        else:
            match_ids = self._content_match_ids or set()
            visible = [s for s in self._all_summaries if _matches(s, query) or s.id in match_ids]
        selected = self._user_filter.currentText()
        if selected == NO_USER_LABEL:
            visible = [summary for summary in visible if summary.user is None]
        elif selected != ALL_USERS_LABEL:
            # Only this user's conversations — not theirs plus every
            # unlabelled one. Including unlabelled rows here was meant to
            # protect a pre-existing history from vanishing, but since all
            # of that history is unlabelled it made picking a name look
            # like it did nothing at all. "All users" is the safety net,
            # it is one click away, and "(no user)" reaches the unlabelled
            # ones on their own.
            visible = [summary for summary in visible if summary.user == selected]
        self._list.clear()
        self._ids_by_row = []
        self._titles_by_row = []
        # The summaries arrive newest-first (ConversationStore
        # .list_conversations' own ORDER BY), so walking them in order and
        # emitting a header whenever the band changes produces each group
        # exactly once, with no sorting or bucketing pass of its own.
        current_group: str | None = None
        for summary in visible:
            group = _date_group(summary.updated_at)
            if group != current_group:
                current_group = group
                self._list.addItem(self._make_group_header(group))
                # Headers occupy a row, so both row-indexed lists need a
                # placeholder to stay aligned with the view. Every reader
                # of these (selected_conversation_id(s), _on_double_click,
                # _on_rename_clicked) treats None as "not a conversation".
                self._ids_by_row.append(None)
                self._titles_by_row.append(None)

            label = _row_label(summary)
            item = QListWidgetItem(label)
            # The column is user-resizable and each line elides, so the
            # untruncated row has to stay reachable somewhere.
            item.setToolTip(label)
            item.setData(TITLE_ROLE, summary.title or UNTITLED_LABEL)
            item.setData(SUBTITLE_ROLE, _row_subtitle(summary, group=group))
            self._list.addItem(item)
            self._ids_by_row.append(summary.id)
            self._titles_by_row.append(summary.title or "")

    @staticmethod
    def _make_group_header(group: str) -> QListWidgetItem:
        """A "Today" / "September 2026" divider.

        ``NoItemFlags`` is what keeps the rest of the widget honest: a
        header cannot be clicked, shift-range-selected, tabbed onto or
        double-clicked, so no action can ever be aimed at one and none of
        the selection paths need a special case for it beyond skipping its
        ``None`` id.
        """
        item = QListWidgetItem(group)
        item.setData(GROUP_HEADER_ROLE, group)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        return item

    @property
    def count(self) -> int:
        """How many *conversations* are listed.

        Not ``self._list.count()``: the view also holds date-group header
        rows (``_make_group_header``), and no caller has ever wanted those
        counted — "how many conversations match the current filter" is the
        question this has always answered.
        """
        return sum(1 for conv_id in self._ids_by_row if conv_id is not None)

    def listed_conversation_ids(self) -> list[str]:
        """Every conversation currently shown, newest first, with the
        date-group headers filtered out — "what the user can see right
        now", as opposed to ``_all_summaries`` ("what there is")."""
        return [conv_id for conv_id in self._ids_by_row if conv_id is not None]

    def _conversation_id_at(self, row: int) -> str | None:
        """The conversation at view row ``row``, or ``None`` for an
        out-of-range row *or* a date-group header (which occupies a row
        but is not a conversation — see ``_make_group_header``)."""
        if row < 0 or row >= len(self._ids_by_row):
            return None
        return self._ids_by_row[row]

    def selected_conversation_id(self) -> str | None:
        return self._conversation_id_at(self._list.currentRow())

    def selected_conversation_ids(self) -> list[str]:
        """Every currently-selected row's conversation id, in list order
        (not selection/click order) — the multi-select counterpart of
        ``selected_conversation_id`` above, used by bulk Delete."""
        rows = sorted({index.row() for index in self._list.selectedIndexes()})
        ids = (self._conversation_id_at(row) for row in rows)
        return [conv_id for conv_id in ids if conv_id is not None]

    def _view_row_for(self, index: int) -> int | None:
        """The view row holding the ``index``-th conversation, skipping
        date-group headers. ``None`` if there is no such conversation."""
        seen = -1
        for row, conv_id in enumerate(self._ids_by_row):
            if conv_id is None:
                continue
            seen += 1
            if seen == index:
                return row
        return None

    def select_row(self, index: int) -> None:
        """Select the ``index``-th *conversation* — header rows are not
        counted and cannot be selected, so this stays stable as groups
        appear and disappear with the filter."""
        row = self._view_row_for(index)
        if row is not None:
            self._list.setCurrentRow(row)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        conv_id = self._conversation_id_at(self._list.row(item))
        if conv_id is not None:
            self.resume_requested.emit(conv_id)

    def _on_resume_clicked(self) -> None:
        conv_id = self.selected_conversation_id()
        if conv_id:
            self.resume_requested.emit(conv_id)

    def _on_delete_clicked(self) -> None:
        """Deletes whatever is currently selected — one conversation
        (``delete_requested``, unchanged from before multi-select existed)
        or several at once (``delete_many_requested``). Shared by the
        Delete… button and the right-click menu's Delete action."""
        conv_ids = self.selected_conversation_ids()
        if not conv_ids:
            return
        if len(conv_ids) == 1:
            title = "Delete Conversation"
            message = "Delete this conversation? This removes its record, artifacts, and history permanently."
        else:
            title = "Delete Conversations"
            message = (
                f"Delete these {len(conv_ids)} conversations? "
                "This removes their records, artifacts, and history permanently."
            )
        answer = QMessageBox.question(
            self,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if len(conv_ids) == 1:
            self.delete_requested.emit(conv_ids[0])
        else:
            self.delete_many_requested.emit(conv_ids)

    def _on_rename_clicked(self) -> None:
        row = self._list.currentRow()
        conv_id = self.selected_conversation_id()
        if not conv_id:
            return
        # `or ""`: a header row's slot holds None, and QInputDialog.getText
        # will not take that for `text`. Unreachable in practice —
        # selected_conversation_id() already returned None above for a
        # header — but this is the only place the list is indexed for
        # anything other than an id.
        current_title = (
            self._titles_by_row[row] if 0 <= row < len(self._titles_by_row) else ""
        ) or ""
        new_title, ok = QInputDialog.getText(
            self, "Rename Conversation", "Title:", text=current_title
        )
        new_title = new_title.strip()
        if ok and new_title:
            self.rename_requested.emit(conv_id, new_title)

    def _on_export_clicked(self) -> None:
        conv_id = self.selected_conversation_id()
        if conv_id:
            self.export_requested.emit(conv_id)

    def _on_cleanup_clicked(self) -> None:
        days = CleanupDialog.get_cutoff_days(self)
        if days is not None:
            self.cleanup_requested.emit(days)

    def _on_context_menu_requested(self, pos) -> None:
        """Right-click menu — bug report: "Add meaningful (one conversation
        action) button functions to the right click (rename, resume,
        delete)." Right-clicking a row that's already part of the current
        multi-selection acts on that whole selection (standard file-
        manager behavior); right-clicking anywhere else selects just that
        row first, same as a plain left click would."""
        item = self._list.itemAt(pos)
        if item is None:
            return
        if item not in self._list.selectedItems():
            self._list.clearSelection()
            item.setSelected(True)
            self._list.setCurrentItem(item)

        self._popup_context_menu(self._build_context_menu(), self._list.viewport().mapToGlobal(pos))

    def _popup_context_menu(self, menu: QMenu, global_pos) -> None:
        """Split out from ``_on_context_menu_requested`` purely so tests
        can monkeypatch this one call: ``QMenu.exec()`` itself is a
        compiled Qt slot, not overridable via a plain Python monkeypatch,
        and would otherwise pop up a real modal menu that blocks waiting
        for mouse input no automated test can provide."""
        menu.exec(global_pos)

    def set_known_users(self, names: list[str]) -> None:
        """The names offered by the context menu's "Move to User" submenu.
        Supplied by the window rather than read here, so the menu offers
        exactly what the toolbar offers — including a name declared but not
        yet used by any conversation."""
        self._known_users = list(names)

    def _on_move_to_user(self, user: str) -> None:
        ids = self.selected_conversation_ids()
        if ids:
            self.move_to_user_requested.emit(ids, user)

    def _on_move_to_new_user(self) -> None:
        ids = self.selected_conversation_ids()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "Move to User", "Name:")
        if ok and name.strip():
            self.move_to_user_requested.emit(ids, name.strip())

    def _add_move_to_user_menu(self, menu: QMenu) -> QMenu:
        # Constructed with ``menu`` as parent rather than via
        # ``menu.addMenu("Move to User")``: PySide6 6.9.x lets Python
        # garbage-collect the QMenu that overload returns, destroying the
        # C++ submenu as soon as this function returns and leaving an
        # action whose menu() raises "Internal C++ object already
        # deleted". Parenting at construction keeps it C++-owned.
        submenu = QMenu("Move to User", menu)
        menu.addMenu(submenu)
        for name in self._known_users:
            submenu.addAction(name, lambda checked=False, n=name: self._on_move_to_user(n))
        if self._known_users:
            submenu.addSeparator()
        submenu.addAction(NO_USER_LABEL, lambda checked=False: self._on_move_to_user(""))
        submenu.addAction("New user…", lambda checked=False: self._on_move_to_new_user())
        return submenu

    def _build_context_menu(self) -> QMenu:
        """Split out from ``_on_context_menu_requested`` so tests can
        inspect the built menu's actions without popping up a real, modal
        native menu (``exec()`` blocks for real mouse/keyboard input).
        Resume/Rename only make sense for exactly one conversation; a
        multi-selection gets Delete only."""
        menu = QMenu(self)
        if len(self.selected_conversation_ids()) == 1:
            menu.addAction("Resume", self._on_resume_clicked)
            menu.addAction("Rename…", self._on_rename_clicked)
            menu.addAction("Export…", self._on_export_clicked)
            menu.addSeparator()
        # Offered for a multi-selection too: putting a run of chats under
        # the right name is exactly when several are wrong at once.
        self._add_move_to_user_menu(menu)
        menu.addSeparator()
        menu.addAction("Delete…", self._on_delete_clicked)
        return menu


__all__ = ["CleanupDialog", "ConversationsSidebar"]
