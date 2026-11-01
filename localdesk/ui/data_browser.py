"""Generic table browser for every module defined in datasets.py."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QSplitter, QTreeWidget,
                               QTreeWidgetItem)

from ..datasets import BY_KEY, DATASETS
from ..model import fmt_time, fmt_time_short, parse_time
from .widgets import (USER_ROLE, Card, Page, SortItem, button, fill_table, isk_color, make_table, muted,
                      standing_color, status_color)

MAX_ROWS = 10000
SLOT_ORDER = ["LoSlot", "MedSlot", "HiSlot", "RigSlot", "SubSystemSlot", "ServiceSlot"]
DEADLINE_WORDS = ("expire", "ends", "arrives", "fracture")


def to_eft(view, fit):
    """Render an ESI fitting as EFT text, the format the in-game fitting window can import."""
    items = fit.get("items", [])
    names = view.names([fit["ship_type_id"]] + [i["type_id"] for i in items])
    name = lambda t: names.get(t, f"Type #{t}")
    lines = [f"[{name(fit['ship_type_id'])}, {fit.get('name', '')}]"]
    for prefix in SLOT_ORDER:
        slot = sorted((i for i in items if str(i["flag"]).startswith(prefix)),
                      key=lambda i: int("".join(ch for ch in str(i["flag"]) if ch.isdigit()) or 0))
        if slot:
            lines += [name(i["type_id"]) for i in slot] + [""]
    rest = [i for i in items if not any(str(i["flag"]).startswith(p) for p in SLOT_ORDER)]
    for i in rest:
        lines.append(f"{name(i['type_id'])} x{i['quantity']}")
    return "\n".join(lines).rstrip() + "\n"


class DataBrowserPage(Page):
    title = "Data browser"
    icon = "fa5s.table"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        modules = Card("Modules", margins=0)
        modules.setMinimumWidth(170)
        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setIndentation(14)
        self.nav.setStyleSheet("QTreeView::item { padding: 5px 2px; }")
        groups = {}
        for ds in DATASETS:
            if ds.group not in groups:
                groups[ds.group] = QTreeWidgetItem([ds.group.upper()])
                f = groups[ds.group].font(0)
                f.setBold(True)
                f.setPointSizeF(f.pointSizeF() * 0.85)
                groups[ds.group].setFont(0, f)
                groups[ds.group].setForeground(0, QColor("#6c757d"))
                groups[ds.group].setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.nav.addTopLevelItem(groups[ds.group])
            item = QTreeWidgetItem([ds.title])
            item.setData(0, USER_ROLE, ds.key)
            groups[ds.group].addChild(item)
        self.nav.expandAll()
        self.nav.currentItemChanged.connect(lambda *_: self.refresh_view())
        modules.add(self.nav, 1)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(modules)
        lay.addWidget(split)

        self.card = Card("—", accent="primary", margins=0)
        self.count = muted(wrap=False)
        self.all_chars = QCheckBox("All characters")
        self.all_chars.setChecked(True)
        self.all_chars.toggled.connect(self.refresh_view)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter rows…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh_view)
        self.search.textChanged.connect(lambda _: self._debounce.start())
        self.action_bar = QHBoxLayout()
        self.card.add_tool(self.count)
        self.card.tools.addLayout(self.action_bar)
        self.card.add_tool(self.all_chars)
        self.card.add_tool(self.search)
        self.note = QLabel()
        self.note.setObjectName("note")
        self.note.setWordWrap(True)
        self.card.body.setSpacing(0)
        self.card.add(self.note)
        self.table = make_table(["—"])
        self.card.add(self.table, 1)
        split.addWidget(self.card)
        split.setStretchFactor(1, 1)
        split.setSizes([205, 1000])
        self._rows = []
        self._action_buttons = []
        self.nav.setCurrentItem(groups["Character"].child(0))

    def dataset(self):
        item = self.nav.currentItem()
        key = item.data(0, USER_ROLE) if item else None
        return BY_KEY.get(key)

    def show_dataset(self, key):
        for i in range(self.nav.topLevelItemCount()):
            group = self.nav.topLevelItem(i)
            for j in range(group.childCount()):
                if group.child(j).data(0, USER_ROLE) == key:
                    self.nav.setCurrentItem(group.child(j))

    def refresh_view(self):
        ds = self.dataset()
        if ds is None:
            return
        self._set_actions(ds)
        chars = self.ctx.characters()
        cids = [c["character_id"] for c in chars] if self.all_chars.isChecked() else \
            ([self.ctx.current_char] if self.ctx.current_char else [])
        owner_names = {c["character_id"]: c["name"] for c in chars}
        syncer, view = self.ctx.syncer, self.ctx.view

        rows, no_access, seen_corps = [], [], set()
        for cid in cids:
            data = syncer.dataset_rows(cid, ds)
            if data is None:
                no_access.append(owner_names.get(cid, str(cid)))
                continue
            if ds.corp:
                corp = (view.get(cid, "info") or {}).get("corporation_id")
                if corp in seen_corps:
                    continue
                if data:
                    seen_corps.add(corp)
            rows += [(cid, r) for r in data]

        ids = {col.value(r) for col in ds.cols if col.kind in ("name", "type", "location") for _, r in rows}
        names = view.names({i for i in ids if isinstance(i, int)})
        query = self.search.text().strip().lower()
        owner_header = "Corporation" if ds.corp else "Character"
        headers = [owner_header] + [c.header for c in ds.cols]
        table_rows, self._rows = [], []
        now = time.time()
        for cid, r in rows:
            if ds.corp:
                corp = (view.get(cid, "info") or {}).get("corporation_id")
                owner = view.name(corp)
            else:
                owner = owner_names.get(cid, "")
            cells = [SortItem(owner, data=len(self._rows))]
            for col in ds.cols:
                cells.append(self._cell(col, col.value(r), names, now))
            if query and not any(query in c.text().lower() for c in cells):
                continue
            self._rows.append((cid, r))
            table_rows.append(cells)
            if len(table_rows) >= MAX_ROWS:
                break
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.set_headers(headers, prefs_key=f"data:{ds.key}",
                               optional=[c.header for c in ds.cols if c.optional])
        self.table.setSortingEnabled(True)
        fill_table(self.table, table_rows)
        date_col = next((i for i, c in enumerate(ds.cols, 1) if c.kind == "date"), None)
        if date_col is not None:
            self.table.sortItems(date_col, Qt.SortOrder.DescendingOrder)

        self.card.set_title(ds.title if ds.group == "Character" else f"Corporation · {ds.title}")
        notes = [ds.note] if ds.note else []
        if any(c.optional for c in ds.cols):
            notes.append("Right-click a column header to show or hide columns.")
        if no_access:
            reason = "missing corp role or scope" if ds.corp else f"scope {ds.scope} not granted"
            notes.append(f"No access for: {', '.join(no_access)} ({reason}).")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))
        self.count.setText(f"{len(table_rows):,} rows" + (" (capped)" if len(table_rows) >= MAX_ROWS else ""))

    def _cell(self, col, raw, names, now):
        kind = col.kind
        if raw is None or raw == "":
            return SortItem("", -1e18 if kind in ("int", "float", "isk", "date", "standing") else "")
        if kind == "date":
            ts = parse_time(raw) if isinstance(raw, str) else float(raw)
            color = None
            if any(w in col.header.lower() for w in DEADLINE_WORDS):
                color = "#dc3545" if ts < now else "#fd7e14" if ts - now < 48 * 3600 else None
            return SortItem(fmt_time_short(ts), ts, color=color, tip=fmt_time(ts))
        if kind == "isk":
            color = isk_color(raw) if col.header in ("Amount", "Total") else None
            return SortItem(f"{raw:,.2f}", raw, right=True, color=color)
        if kind == "int":
            return SortItem(f"{raw:,}", raw, right=True)
        if kind == "float":
            return SortItem(f"{raw:,.2f}", raw, right=True)
        if kind == "standing":
            return SortItem(f"{raw:+.2f}", raw, badge=standing_color(raw))
        if kind == "bool":
            return SortItem("Yes" if raw else "", 1 if raw else 0)
        if kind == "badge":
            text = str(raw).replace("_", " ")
            return SortItem(text, text, badge=status_color(raw) or "#6c757d")
        if kind == "location":
            return SortItem(names.get(raw) or self.ctx.view.location_name(raw))
        if kind in ("name", "type"):
            return SortItem(names.get(raw, f"#{raw}"))
        return SortItem(str(raw))

    # --- per-dataset actions ----------------------------------------------
    def _set_actions(self, ds):
        for b in self._action_buttons:
            b.deleteLater()
        self._action_buttons = []
        for label, fn in ds.actions:
            b = button(label, "fa5s.external-link-alt" if "zKill" in label else "fa5s.copy")
            b.clicked.connect(lambda _=False, fn=fn: getattr(self, fn)())
            self.action_bar.addWidget(b)
            self._action_buttons.append(b)

    def _selected_row(self):
        row = self.table.currentRow()
        if row < 0:
            self.count.setText("Select a row first")
            return None
        return self._rows[self.table.item(row, 0).data(USER_ROLE)][1]

    def copy_eft(self):
        fit = self._selected_row()
        if fit:
            QApplication.clipboard().setText(to_eft(self.ctx.view, fit))
            self.count.setText("EFT copied to clipboard. Import it in game from the Fitting window.")

    def open_zkill(self):
        km = self._selected_row()
        if km:
            webbrowser.open(f"https://zkillboard.com/kill/{km['killmail_id']}/")
