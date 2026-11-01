"""Generic tables for the modules defined in datasets.py: the Data browser (character modules) and the
Corporation page's modules both use DatasetView."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QSplitter,
                               QTreeWidget, QTreeWidgetItem)

from .. import corporation as corp_mod
from ..datasets import BY_KEY, DATASETS
from ..model import fmt_isk, fmt_time, fmt_time_short, parse_time
from .widgets import (USER_ROLE, Card, Page, SortItem, button, fill_table, isk_color, make_table, muted,
                      standing_color, status_color)

MAX_ROWS = 10000
SLOT_ORDER = ["LoSlot", "MedSlot", "HiSlot", "RigSlot", "SubSystemSlot", "ServiceSlot"]
DEADLINE_WORDS = ("expire", "ends", "arrives", "fracture")
TOTAL_COLUMNS = ("Amount", "Total", "Est. value")


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


class DatasetView(Card):
    """One dataset as a filterable, sortable table in a card. mode="characters" shows your characters'
    rows (optionally all of them); mode="corp" shows a corporation's rows, read through whichever of your
    characters in that corporation has access."""

    def __init__(self, ctx, mode="characters"):
        super().__init__("—", accent="primary", margins=0)
        self.ctx, self.mode = ctx, mode
        self.ds, self.corp = None, None
        self.count = muted(wrap=False)
        self.all_chars = QCheckBox("All characters")
        self.all_chars.setChecked(True)
        self.all_chars.toggled.connect(self.refresh)
        self.all_chars.setVisible(mode == "characters")
        self.division = QComboBox()
        self.division.currentIndexChanged.connect(lambda _: self.refresh())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter rows…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _: self._debounce.start())
        self.action_bar = QHBoxLayout()
        self.add_tool(self.count)
        self.tools.addLayout(self.action_bar)
        self.add_tool(self.all_chars)
        self.add_tool(self.division)
        self.add_tool(self.search)
        self.note = QLabel()
        self.note.setObjectName("note")
        self.note.setWordWrap(True)
        self.body.setSpacing(0)
        self.add(self.note)
        self.table = make_table(["—"])
        self.add(self.table, 1)
        self._rows, self._action_buttons, self._division_key = [], [], None

    def set_dataset(self, ds, corp=None):
        changed = ds is not self.ds or corp != self.corp
        self.ds, self.corp = ds, corp
        if changed:
            self._fill_divisions()
        self.refresh()

    # --- data -------------------------------------------------------------
    def _division_col(self):
        return next((c for c in self.ds.cols if c.kind == "division"), None) if self.ds else None

    def _division_kind(self):
        col = self._division_col()
        return "hangar" if col is not None and col.get == "hangar" else "wallet"

    def _fill_divisions(self):
        col = self._division_col()
        self.division.blockSignals(True)
        self.division.clear()
        if col is not None:
            names = corp_mod.division_names(self.ctx.db, self.corp)[self._division_kind()] if self.corp else {}
            self.division.addItem("All divisions", None)
            for d in range(1, 8):
                self.division.addItem(names.get(d, f"Division {d}"), d)
        self.division.setVisible(col is not None)
        self.division.blockSignals(False)

    def _gather(self):
        """([(character id, row)], [names of characters without access])"""
        ds, db, syncer = self.ds, self.ctx.db, self.ctx.syncer
        chars = self.ctx.characters()
        names = {c["character_id"]: c["name"] for c in chars}
        if self.mode == "corp":
            rows, cid = corp_mod.first_with_access(db, syncer, self.corp, ds.key)
            if rows is None:
                return [], [names.get(c, str(c)) for c in corp_mod.my_corporations(db).get(self.corp, [])]
            return [(cid, r) for r in rows], []
        cids = [c["character_id"] for c in chars] if self.all_chars.isChecked() else \
            ([self.ctx.current_char] if self.ctx.current_char else [])
        rows, no_access = [], []
        for cid in cids:
            data = syncer.dataset_rows(cid, ds)
            if data is None:
                no_access.append(names.get(cid, str(cid)))
            else:
                rows += [(cid, r) for r in data]
        return rows, no_access

    def refresh(self):
        ds = self.ds
        if ds is None:
            return
        self._set_actions(ds)
        view = self.ctx.view
        rows, no_access = self._gather()
        division = self.division.currentData() if self.division.isVisible() else None
        col = self._division_col()
        if division is not None and col is not None:
            rows = [(c, r) for c, r in rows if col.value(r) == division]
        self._division_names = (corp_mod.division_names(self.ctx.db, self.corp)[self._division_kind()]
                                if self.corp else {})
        ids = {c.value(r) for c in ds.cols if c.kind in ("name", "type", "location") for _, r in rows}
        names = view.names({i for i in ids if isinstance(i, int)})
        owner_names = {c["character_id"]: c["name"] for c in self.ctx.characters()}
        query = self.search.text().strip().lower()
        with_owner = self.mode == "characters"
        headers = (["Character"] if with_owner else []) + [c.header for c in ds.cols]
        table_rows, self._rows = [], []
        totals = {c.header: 0.0 for c in ds.cols if c.kind == "isk" and c.header in TOTAL_COLUMNS}
        now = time.time()
        for cid, r in rows:
            cells = [SortItem(owner_names.get(cid, ""), data=len(self._rows))] if with_owner else []
            for c in ds.cols:
                cells.append(self._cell(c, c.value(r), names, now))
            if not with_owner:
                cells[0].setData(USER_ROLE, len(self._rows))
            if query and not any(query in cell.text().lower() for cell in cells):
                continue
            for header in totals:
                value = next(c.value(r) for c in ds.cols if c.header == header)
                totals[header] += value or 0
            self._rows.append((cid, r))
            table_rows.append(cells)
            if len(table_rows) >= MAX_ROWS:
                break
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.set_headers(headers, prefs_key=f"{self.mode}:{ds.key}",
                               optional=[c.header for c in ds.cols if c.optional])
        self.table.setSortingEnabled(True)
        fill_table(self.table, table_rows)
        date_col = next((i for i, c in enumerate(ds.cols, int(with_owner)) if c.kind == "date"), None)
        if date_col is not None:
            self.table.sortItems(date_col, Qt.SortOrder.DescendingOrder)

        self.set_title(ds.title)
        notes = [ds.note] if ds.note else []
        if no_access:
            reason = "missing corp role or scope" if ds.corp else f"scope {ds.scope} not granted"
            who = ", ".join(no_access) if no_access else "none of your characters"
            notes.append(f"No access for: {who} ({reason}).")
        elif self.mode == "corp" and not corp_mod.my_corporations(self.ctx.db).get(self.corp):
            notes.append("None of your characters is in this corporation.")
        if any(c.optional for c in ds.cols):
            notes.append("Right-click a column header to show or hide columns.")
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))
        summary = f"{len(table_rows):,} rows" + (" (capped)" if len(table_rows) >= MAX_ROWS else "")
        for header, total in totals.items():
            summary += f" · {header.lower()} {fmt_isk(total)}"
        self.count.setText(summary)

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
            return SortItem(fmt_isk(raw), raw, right=True, color=color, tip=f"{raw:,.2f} ISK")
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
        if kind == "division":
            return SortItem(self._division_names.get(raw, str(raw)) if isinstance(raw, int) else str(raw),
                            raw if isinstance(raw, int) else 99)
        if kind == "location":
            return SortItem(names.get(raw) or self.ctx.view.location_name(raw))
        if kind in ("name", "type"):
            return SortItem(names.get(raw, f"#{raw}"))
        return SortItem(str(raw))

    # --- per-dataset actions ----------------------------------------------
    def _set_actions(self, ds):
        if getattr(self, "_actions_for", None) is ds:
            return
        self._actions_for = ds
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
        self.nav.setRootIsDecorated(False)
        self.nav.setStyleSheet("QTreeView::item { padding: 5px 2px; }")
        first = None
        for ds in DATASETS:
            if ds.group != "Character":  # corporation modules live on the Corporation page
                continue
            item = QTreeWidgetItem([ds.title])
            item.setData(0, USER_ROLE, ds.key)
            self.nav.addTopLevelItem(item)
            first = first or item
        self.nav.currentItemChanged.connect(lambda *_: self.refresh_view())
        modules.add(self.nav, 1)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(modules)
        self.view = DatasetView(ctx, "characters")
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([205, 1000])
        lay.addWidget(split)
        self.table = self.view.table
        self.nav.setCurrentItem(first)

    def dataset(self):
        item = self.nav.currentItem()
        return BY_KEY.get(item.data(0, USER_ROLE)) if item else None

    def show_dataset(self, key):
        for i in range(self.nav.topLevelItemCount()):
            if self.nav.topLevelItem(i).data(0, USER_ROLE) == key:
                self.nav.setCurrentItem(self.nav.topLevelItem(i))

    def refresh_view(self):
        ds = self.dataset()
        if ds is not None:
            self.view.set_dataset(ds)
