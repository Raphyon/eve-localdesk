"""Shared Qt helpers."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..model import display_security
from .theme import ACCENT, icon

USER_ROLE = Qt.ItemDataRole.UserRole
BADGE_ROLE = Qt.ItemDataRole.UserRole + 1
SUB_ROLE = Qt.ItemDataRole.UserRole + 2   # optional second, smaller line in a cell

# Where tables remember hidden columns; the main window connects this to the database.
PREFS = {"get": lambda key, default=None: default, "set": lambda key, value: None}


def set_prefs_store(get, set_):
    PREFS["get"], PREFS["set"] = get, set_
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

SEC_COLORS = {1.0: "#2fefef", 0.9: "#48f0c0", 0.8: "#00ef47", 0.7: "#00f000", 0.6: "#8fef2f", 0.5: "#efef00",
              0.4: "#d77700", 0.3: "#f06000", 0.2: "#f04800", 0.1: "#d73000"}


def sec_color(sec):
    sec = display_security(sec)
    if sec is None:
        return None
    return SEC_COLORS.get(sec, "#f00000")


def sec_text(sec):
    sec = display_security(sec)
    return "" if sec is None else f"{sec:.1f}"


def standing_color(standing):
    if standing > 5:
        return ACCENT["primary"]
    if standing > 0:
        return ACCENT["info"]
    if standing < -5:
        return ACCENT["danger"]
    if standing < 0:
        return ACCENT["orange"]
    return ACCENT["secondary"]


def isk_color(amount):
    return ACCENT["success"] if amount > 0 else ACCENT["danger"] if amount < 0 else None


STATUS_COLORS = {
    "outstanding": "primary", "in_progress": "orange", "active": "primary", "ready": "success",
    "finished": "success", "delivered": "success", "completed": "success", "finished_issuer": "success",
    "finished_contractor": "success", "expired": "secondary", "cancelled": "secondary", "deleted": "secondary",
    "paused": "warning", "reverted": "secondary", "rejected": "danger", "failed": "danger",
    "buy": "success", "sell": "danger", "kill": "success", "loss": "danger",
    "shield_vulnerable": "success", "armor_reinforce": "warning", "hull_reinforce": "danger",
    "armor_vulnerable": "orange", "hull_vulnerable": "danger", "anchoring": "info", "onlining_vulnerable": "info",
    "unanchored": "secondary", "online": "success", "offline": "secondary",
    "critical": "danger", "warning": "orange", "info": "info", "add": "success", "update": "primary",
    "remove": "danger", "yes": "success",
}


def status_color(text):
    return ACCENT.get(STATUS_COLORS.get(str(text).lower(), ""), None)


def _text_on(color):
    c = QColor(color)
    return "#1f2d3d" if (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) > 160 else "#ffffff"


class BadgeDelegate(QStyledItemDelegate):
    """Draws cells with a BADGE_ROLE colour as AdminLTE-style pills, and cells with a SUB_ROLE text as
    two lines (main text, smaller muted text underneath). Long text is cut with '…'."""

    def paint(self, painter, option, index):
        color, sub = index.data(BADGE_ROLE), index.data(SUB_ROLE)
        if not color and not sub:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = option.widget
        (widget.style() if widget else QStyle).drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        if color:
            return self._badge(painter, opt, option, text, color)
        painter.save()
        rect = option.rect.adjusted(8, 3, -8, -3)
        main_font = QFont(opt.font)
        sub_font = QFont(opt.font)
        sub_font.setPointSizeF(max(sub_font.pointSizeF() * 0.86, 7))
        half = rect.height() // 2
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        painter.setPen(fg.color() if fg is not None else opt.palette.text().color())
        painter.setFont(main_font)
        align = (opt.displayAlignment & Qt.AlignmentFlag.AlignHorizontal_Mask) | Qt.AlignmentFlag.AlignBottom
        top = QRect(rect.left(), rect.top(), rect.width(), half)
        painter.drawText(top, align, painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width()))
        painter.setFont(sub_font)
        painter.setPen(opt.palette.placeholderText().color())
        bottom = QRect(rect.left(), rect.top() + half + 1, rect.width(), rect.height() - half)
        align = (opt.displayAlignment & Qt.AlignmentFlag.AlignHorizontal_Mask) | Qt.AlignmentFlag.AlignTop
        painter.drawText(bottom, align, painter.fontMetrics().elidedText(str(sub), Qt.TextElideMode.ElideRight,
                                                                         rect.width()))
        painter.restore()

    def _badge(self, painter, opt, option, text, color):
        if not text:
            return
        font = QFont(opt.font)
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF() * 0.92, 7))
        painter.save()
        painter.setFont(font)
        fm = painter.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 16, fm.height() + 6
        x = option.rect.right() - w - 8 if opt.displayAlignment & Qt.AlignmentFlag.AlignRight else option.rect.x() + 8
        rect = QRect(x, option.rect.center().y() - h // 2 + 1, w, h)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(_text_on(color)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(BADGE_ROLE):
            return QSize(size.width() + 20, size.height())
        sub = index.data(SUB_ROLE)
        if sub:
            font = QFont(option.font)
            font.setPointSizeF(max(font.pointSizeF() * 0.86, 7))
            from PySide6.QtGui import QFontMetrics
            return QSize(max(size.width(), QFontMetrics(font).horizontalAdvance(str(sub)) + 20), size.height() * 2)
        return size


def button(text="", icon_name=None, kind=None, tip=None, icon_color=None):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, icon_color or ("#ffffff" if kind in ("primary", "success", "danger")
                                                 else "#6c757d")))
    if kind:
        b.setProperty("kind", kind)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def muted(text="", wrap=True):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(wrap)
    return label


class Card(QFrame):
    """AdminLTE card: optional header (title + tools on the right) and a body."""

    def __init__(self, title=None, accent=None, margins=14):
        super().__init__()
        self.setObjectName("card")
        if accent:
            self.setProperty("accent", accent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tools = None
        if title is not None:
            head = QWidget()
            head.setObjectName("cardHeader")
            self.tools = QHBoxLayout(head)
            self.tools.setContentsMargins(14, 8, 10, 8)
            self.tools.setSpacing(8)
            self.title_label = QLabel(title)
            self.title_label.setObjectName("cardTitle")
            self.tools.addWidget(self.title_label)
            self.tools.addStretch(1)
            lay.addWidget(head)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(margins, margins, margins, margins)
        self.body.setSpacing(10)
        lay.addWidget(body, 1)

    def set_title(self, text):
        self.title_label.setText(text)

    def add_tool(self, widget):
        self.tools.addWidget(widget)
        return widget

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget


class InfoBox(QFrame):
    """Stat tile: coloured icon square, small caption, big value."""

    def __init__(self, icon_name, color, label):
        super().__init__()
        self.setObjectName("infobox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 12, 8)
        lay.setSpacing(12)
        square = QLabel()
        square.setFixedSize(64, 64)
        square.setAlignment(Qt.AlignmentFlag.AlignCenter)
        square.setStyleSheet(f"background: {color}; border-radius: 4px;")
        square.setPixmap(icon(icon_name, _text_on(color)).pixmap(30, 30))
        lay.addWidget(square)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.label = QLabel(label.upper())
        self.label.setObjectName("infoLabel")
        self.value = QLabel("—")
        self.value.setObjectName("infoValue")
        col.addStretch(1)
        col.addWidget(self.label)
        col.addWidget(self.value)
        col.addStretch(1)
        lay.addLayout(col, 1)

    def set(self, value):
        self.value.setText(str(value))


def round_pixmap(pix, size):
    pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


class SortItem(QTableWidgetItem):
    """Table cell that sorts by an explicit key (numbers sort numerically)."""

    def __init__(self, text, sort=None, *, color=None, right=False, data=None, bold=False, tip=None, badge=None,
                 sub=None):
        super().__init__("" if text is None else str(text))
        if badge:
            self.setData(BADGE_ROLE, badge)
        if sub:
            self.setData(SUB_ROLE, str(sub))
        self.sort_key = sort if sort is not None else self.text().lower()
        if color:
            self.setForeground(QBrush(QColor(color)))
        if right:
            self.setTextAlignment(RIGHT)
        if data is not None:
            self.setData(USER_ROLE, data)
        if bold:
            f = self.font()
            f.setBold(True)
            self.setFont(f)
        if tip:
            self.setToolTip(tip)

    def __lt__(self, other):
        b = getattr(other, "sort_key", other.text().lower())
        try:
            return self.sort_key < b
        except TypeError:
            return str(self.sort_key) < str(b)


class TreeItem(QTreeWidgetItem):
    """Tree row with optional per-column sort keys: TreeItem(texts, {column: key})."""

    def __init__(self, texts, sort_keys=None, data=None):
        super().__init__(["" if t is None else str(t) for t in texts])
        self.sort_keys = sort_keys or {}
        if data is not None:
            self.setData(0, USER_ROLE, data)

    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a = self.sort_keys.get(col, self.text(col).lower())
        b = other.sort_keys.get(col, other.text(col).lower()) if isinstance(other, TreeItem) else other.text(col)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


def fit_widths(natural, available, minimum=48):
    """Shrink the widest columns first ("water filling") until they fit in `available` pixels.
    `minimum` is a number or a per-column list: no column goes below it (or below its natural width
    if that's smaller). If even the minimums don't fit, the minimums are returned (the table scrolls)."""
    if not natural:
        return []
    mins = [min(n, m) for n, m in zip(natural, minimum if isinstance(minimum, (list, tuple))
                                      else [minimum] * len(natural))]
    if sum(natural) <= available:
        return list(natural)
    if sum(mins) >= available:
        return mins
    lo, hi = 0, max(natural)
    while hi - lo > 1:
        cap = (lo + hi) // 2
        if sum(max(min(n, cap), m) for n, m in zip(natural, mins)) <= available:
            lo = cap
        else:
            hi = cap
    return [max(min(n, lo), m) for n, m in zip(natural, mins)]


class FitTable(QTableWidget):
    """A table whose columns always fit the visible width: long text is cut with '…' (full text on
    hover) instead of making you scroll sideways. Right-click the header to show or hide columns; the
    choice is remembered per table (prefs_key)."""

    def __init__(self, headers, stretch=None, selectable=True, prefs_key=None, optional=()):
        super().__init__(0, len(headers))
        self.stretch = stretch
        self.prefs_key = prefs_key
        self.optional = set(optional)  # columns hidden until the user turns them on
        self._natural = {}
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection if selectable
                              else QAbstractItemView.SelectionMode.NoSelection)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().setDefaultSectionSize(34)
        h = self.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setHighlightSections(False)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(False)
        h.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)
        self.setItemDelegate(BadgeDelegate(self))
        self.set_headers(headers, prefs_key, optional)

    # --- columns ----------------------------------------------------------
    def headers(self):
        return [self.horizontalHeaderItem(c).text() if self.horizontalHeaderItem(c) else ""
                for c in range(self.columnCount())]

    def set_headers(self, headers, prefs_key=None, optional=None):
        self.prefs_key = prefs_key or self.prefs_key
        if optional is not None:
            self.optional = set(optional)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        hidden = self._hidden_names()
        for c, name in enumerate(headers):
            self.setColumnHidden(c, c > 0 and name in hidden)
        self._natural = {}

    def _hidden_names(self):
        if not self.prefs_key:
            return set(self.optional)
        import json
        saved = PREFS["get"](f"columns:{self.prefs_key}")
        return set(json.loads(saved)) if saved else set(self.optional)

    def _header_menu(self, pos):
        menu = QMenu(self)
        menu.addSection("Columns")
        for c, name in enumerate(self.headers()):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(c))
            action.setEnabled(c > 0)  # the first column always stays
            action.toggled.connect(lambda on, c=c: self._toggle(c, on))
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _toggle(self, column, visible):
        self.setColumnHidden(column, not visible)
        if self.prefs_key:
            import json
            hidden = [n for c, n in enumerate(self.headers()) if self.isColumnHidden(c)]
            PREFS["set"](f"columns:{self.prefs_key}", json.dumps(hidden))
        self.fit()

    # --- widths -----------------------------------------------------------
    def measure(self):
        """Remember each column's natural (content) width and how narrow it may get; call after filling.
        Badge, number and date columns keep their width; free text gives way first."""
        self._natural, self._minimum = {}, {}
        fm = self.horizontalHeader().fontMetrics()
        for c in range(self.columnCount()):
            if self.isColumnHidden(c):
                continue
            self.resizeColumnToContents(c)
            natural = min(self.columnWidth(c), 480)
            self._natural[c] = natural
            sample = [self.item(r, c) for r in range(min(self.rowCount(), 50))]
            sample = [i for i in sample if i is not None and i.text()]
            # badges, numbers and short date/number-like values are never cut; names and other text are
            keep = sample and all(i.data(BADGE_ROLE) or i.textAlignment() & Qt.AlignmentFlag.AlignRight
                                  or (len(i.text()) <= 12 and any(ch.isdigit() for ch in i.text()))
                                  for i in sample)
            header = fm.horizontalAdvance(self.horizontalHeaderItem(c).text()) + 28 if self.horizontalHeaderItem(c) else 60
            self._minimum[c] = natural if keep else min(natural, max(header, 72))
        self.fit()

    def fit(self):
        cols = [c for c in range(self.columnCount()) if not self.isColumnHidden(c)]
        if not cols:
            return
        if any(c not in self._natural for c in cols):
            for c in cols:
                if c not in self._natural:
                    self.resizeColumnToContents(c)
                    self._natural[c] = min(self.columnWidth(c), 480)
        available = self.viewport().width() - 2
        natural = [self._natural[c] for c in cols]
        minimum = [getattr(self, "_minimum", {}).get(c, 60) for c in cols]
        widths = fit_widths(natural, available, minimum)
        spare = available - sum(widths)
        if spare > 0:
            grow = self.stretch if self.stretch in cols else cols[-1]
            widths[cols.index(grow)] += spare
        for c, w in zip(cols, widths):
            self.setColumnWidth(c, w)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


def make_table(headers, stretch=None, selectable=True, prefs_key=None, optional=()) -> FitTable:
    return FitTable(headers, stretch, selectable, prefs_key, optional)


def fill_table(table: QTableWidget, rows, resize=True):
    sorting = table.isSortingEnabled()
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    two_line = False
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            item = cell if isinstance(cell, QTableWidgetItem) else SortItem(cell)
            if not item.toolTip() and len(item.text()) > 24:
                item.setToolTip(item.text())  # full text on hover when the column cuts it short
            two_line = two_line or bool(item.data(SUB_ROLE))
            table.setItem(r, c, item)
    table.setSortingEnabled(sorting)
    table.verticalHeader().setDefaultSectionSize(48 if two_line else 34)
    if resize:
        if isinstance(table, FitTable):
            table.measure()
        else:
            for c in range(table.columnCount()):
                table.resizeColumnToContents(c)
                table.setColumnWidth(c, min(table.columnWidth(c), 420))


def fill_char_combo(combo: QComboBox, characters, keep=None, all_label=None):
    keep = combo.currentData() if keep is None else keep
    combo.blockSignals(True)
    combo.clear()
    if all_label:
        combo.addItem(all_label, 0)
    for ch in characters:
        combo.addItem(ch["name"], ch["character_id"])
    idx = combo.findData(keep)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.blockSignals(False)


class Page(QWidget):
    """A sidebar page. refresh_view() redraws from the local cache; it must not touch the network."""
    title = ""
    icon = "fa5s.circle"
    section = "Overview"

    def vbox(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        return lay

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    def refresh_view(self):
        pass

    def on_char_changed(self, character_id):
        self.refresh_view()


def avatar_placeholder(size):
    """Grey circle with an astronaut icon, shown until a portrait has been downloaded."""
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#6c757d"))
    p.drawEllipse(0, 0, size, size)
    inner = int(size * 0.55)
    p.drawPixmap((size - inner) // 2, (size - inner) // 2, icon("fa5s.user-astronaut", "#e9ecef").pixmap(inner, inner))
    p.end()
    return out
