"""Corporation page: overview (info, wallets, structures, moon chunks), mining ledger with mining tax,
and member activity."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import corporation as corp_mod
from ..model import fmt_duration, fmt_isk, fmt_time, fmt_time_short, parse_time
from .theme import ACCENT
from .widgets import (Card, InfoBox, Page, SortItem, button, fill_table, make_table, muted)

FUEL_WARNING = 7 * 86400
GROUP_HEADERS = {
    "character": ["Character", "Corporation", "Quantity", "Est. value", "Tax owed", "Share"],
    "ore": ["Ore", "Quantity", "Est. value", "Miners", "Share"],
    "structure": ["Structure", "Quantity", "Est. value", "Miners", "Share"],
    "day": ["Day", "Quantity", "Est. value", "Miners"],
}


def _combo(items):
    combo = QComboBox()
    for label, value in items:
        combo.addItem(label, value)
    return combo


class CorporationPage(Page):
    title = "Corporation"
    icon = "fa5s.building"
    section = "Corporation"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = self.vbox()
        bar = QHBoxLayout()
        self.corp = QComboBox()
        self.corp.setMinimumWidth(260)
        self.corp.currentIndexChanged.connect(lambda _: self.refresh_view())
        self.info = muted(wrap=True)
        bar.addWidget(self.corp)
        bar.addWidget(self.info, 1)
        lay.addLayout(bar)

        boxes = QHBoxLayout()
        boxes.setSpacing(14)
        self.box_members = InfoBox("fa5s.users", ACCENT["info"], "Members")
        self.box_wallet = InfoBox("fa5s.coins", ACCENT["success"], "Corp wallets (ISK)")
        self.box_structures = InfoBox("fa5s.industry", ACCENT["warning"], "Structures")
        self.box_mined = InfoBox("fa5s.gem", ACCENT["purple"], "Mined this month (ISK)")
        for b in (self.box_members, self.box_wallet, self.box_structures, self.box_mined):
            boxes.addWidget(b)
        lay.addLayout(boxes)

        card = Card(margins=0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._overview_tab(), "Overview")
        self.tabs.addTab(self._mining_tab(), "Mining ledger")
        self.tabs.addTab(self._members_tab(), "Members")
        card.add(self.tabs, 1)
        lay.addWidget(card, 1)
        self.empty = muted("None of your characters is in a player corporation yet.")
        lay.addWidget(self.empty)

    # --- tabs ---------------------------------------------------------------
    def _overview_tab(self):
        split = QSplitter()
        split.setHandleWidth(10)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(14, 10, 4, 10)
        self.structures_title = QLabel("<b>Structures &amp; fuel</b>")
        lv.addWidget(self.structures_title)
        self.structures = make_table(["Structure", "Type", "Fuel left", "State"], stretch=0, prefs_key="corp_structures")
        lv.addWidget(self.structures, 1)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 10, 14, 10)
        rv.addWidget(QLabel("<b>Moon chunks</b>"))
        self.chunks = make_table(["Moon", "Arrives", "In"], stretch=0, prefs_key="corp_chunks")
        rv.addWidget(self.chunks, 1)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([620, 420])
        return split

    def _mining_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        bar = QHBoxLayout()
        self.m_period = _combo(corp_mod.PERIODS)
        self.m_structure = QComboBox()
        self.m_group = _combo(corp_mod.GROUPS)
        self.m_tax = QDoubleSpinBox()
        self.m_tax.setRange(0, 100)
        self.m_tax.setDecimals(1)
        self.m_tax.setSuffix(" %")
        self.m_tax.setToolTip("Mining tax: the share of the mined value each miner owes the corporation. "
                              "Saved per corporation.")
        for widget in (self.m_period, self.m_structure, self.m_group):
            widget.currentIndexChanged.connect(lambda _: self._fill_mining())
        self.m_tax.valueChanged.connect(self._tax_changed)
        copy = button("Copy as CSV", "fa5s.file-csv", tip="Copy this table for a spreadsheet")
        copy.clicked.connect(self._copy_csv)
        for label, widget in (("Period", self.m_period), ("Structure", self.m_structure), ("Group by", self.m_group),
                              ("Mining tax", self.m_tax)):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget)
            bar.addSpacing(6)
        bar.addStretch(1)
        bar.addWidget(copy)
        v.addLayout(bar)
        self.m_summary = QLabel()
        self.m_summary.setWordWrap(True)
        v.addWidget(self.m_summary)
        self.m_table = make_table(GROUP_HEADERS["character"], stretch=0)
        v.addWidget(self.m_table, 1)
        self.m_note = muted()
        v.addWidget(self.m_note)
        self._csv = ([], [])
        return w

    def _members_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10)
        self.members_summary = QLabel()
        v.addWidget(self.members_summary)
        self.members = make_table(["Member", "Last logon", "Inactive", "Location", "Ship"], stretch=3,
                                  prefs_key="corp_members_activity")
        v.addWidget(self.members, 1)
        return w

    # --- refresh --------------------------------------------------------------
    def current_corp(self):
        return self.corp.currentData()

    def refresh_view(self):
        corps = corp_mod.my_corporations(self.ctx.db)
        keep = self.corp.currentData()
        self.corp.blockSignals(True)
        self.corp.clear()
        for corp in corps:
            info = self.ctx.db.cache_get(0, f"corp_info:{corp}") or {}
            ticker = f" [{info['ticker']}]" if info.get("ticker") else ""
            self.corp.addItem(self.ctx.view.name(corp, f"Corporation #{corp}") + ticker, corp)
        idx = self.corp.findData(keep)
        self.corp.setCurrentIndex(max(idx, 0))
        self.corp.blockSignals(False)
        has = bool(corps)
        self.empty.setVisible(not has)
        self.tabs.setEnabled(has)
        corp = self.current_corp()
        if not corp:
            return
        self._fill_info(corp)
        self._fill_overview(corp)
        self._fill_structure_filter(corp)
        self.m_tax.blockSignals(True)
        self.m_tax.setValue(float(self.ctx.db.get_setting(f"mining_tax:{corp}", "0") or 0))
        self.m_tax.blockSignals(False)
        self._fill_mining()
        self._fill_members(corp)

    def _fill_info(self, corp):
        v, db = self.ctx.view, self.ctx.db
        info = db.cache_get(0, f"corp_info:{corp}") or {}
        parts = []
        if info.get("alliance_id"):
            parts.append(f"Alliance: <b>{v.name(info['alliance_id'])}</b>")
        if info.get("ceo_id"):
            parts.append(f"CEO: {v.name(info['ceo_id'])}")
        if info.get("tax_rate") is not None:
            parts.append(f"Corp tax {info['tax_rate'] * 100:.1f}%")
        if info.get("date_founded"):
            parts.append(f"founded {fmt_time_short(parse_time(info['date_founded']))}")
        members = ", ".join(c["name"] for c in self.ctx.characters()
                            if c["character_id"] in corp_mod.my_corporations(db).get(corp, []))
        parts.append(f"your characters: {members}")
        self.info.setText(" · ".join(parts))
        self.box_members.set(f"{info['member_count']:,}" if info.get("member_count") else "—")

    def _fill_overview(self, corp):
        db, syncer, v = self.ctx.db, self.ctx.syncer, self.ctx.view
        now = time.time()
        wallets, _ = corp_mod.first_with_access(db, syncer, corp, "corp_wallets")
        self.box_wallet.set(fmt_isk(sum(w.get("balance", 0) for w in wallets)) if wallets else "no access")
        structures, _ = corp_mod.first_with_access(db, syncer, corp, "corp_structures")
        rows, low = [], 0
        for s in structures or []:
            fuel = parse_time(s.get("fuel_expires"))
            left = fuel - now if fuel else None
            if left is not None and left < FUEL_WARNING:
                low += 1
            rows.append([SortItem(s.get("name", ""), sub=v.name(s.get("system_id"))),
                         v.name(s.get("type_id")),
                         SortItem(fmt_duration(left) if left and left > 0 else ("OUT" if fuel else "—"),
                                  left if left is not None else 9e12, right=True,
                                  color=ACCENT["danger"] if left is not None and left < 2 * 86400
                                  else ACCENT["orange"] if left is not None and left < FUEL_WARNING else None,
                                  tip=f"Fuel runs out {fmt_time(fuel)}" if fuel else None),
                         SortItem((s.get("state") or "").replace("_", " "))])
        fill_table(self.structures, rows)
        self.structures.sortItems(2, Qt.SortOrder.AscendingOrder)
        if structures is None:
            self.box_structures.set("no access")
            self.structures_title.setText("<b>Structures &amp; fuel</b> <span style='color:#6c757d'>(needs a "
                                          "Station Manager)</span>")
        else:
            self.box_structures.set(f"{len(structures)}" + (f" · {low} low fuel" if low else ""))
            self.structures_title.setText("<b>Structures &amp; fuel</b>")
        chunks, _ = corp_mod.first_with_access(db, syncer, corp, "corp_extractions")
        crow = []
        for e in chunks or []:
            arrive = parse_time(e.get("chunk_arrival_time"))
            if arrive and arrive > now - 3 * 86400:
                crow.append([SortItem(v.name(e.get("moon_id")), sub=v.location_name(e["structure_id"])
                                      if e.get("structure_id") else None),
                             SortItem(fmt_time_short(arrive), arrive, tip=fmt_time(arrive)),
                             SortItem(fmt_duration(arrive - now) if arrive > now else "ready", arrive, right=True,
                                      color=ACCENT["success"] if arrive <= now else None)])
        fill_table(self.chunks, crow)
        self.chunks.sortItems(1, Qt.SortOrder.AscendingOrder)
        month = corp_mod.summarize(corp_mod.mining_entries(db, corp, "this_month"), syncer.price_map(), "ore")
        self.box_mined.set(fmt_isk(sum(r["value"] for r in month)) if month else "—")

    def _fill_structure_filter(self, corp):
        keep = self.m_structure.currentData()
        self.m_structure.blockSignals(True)
        self.m_structure.clear()
        self.m_structure.addItem("All structures", None)
        for oid in corp_mod.mining_observers(self.ctx.db, corp):
            self.m_structure.addItem(self.ctx.view.location_name(oid), oid)
        idx = self.m_structure.findData(keep)
        self.m_structure.setCurrentIndex(max(idx, 0))
        self.m_structure.blockSignals(False)

    def _tax_changed(self, value):
        corp = self.current_corp()
        if corp:
            self.ctx.db.set_setting(f"mining_tax:{corp}", str(value))
        self._fill_mining()

    def _fill_mining(self):
        corp = self.current_corp()
        if not corp:
            return
        db, v = self.ctx.db, self.ctx.view
        group = self.m_group.currentData()
        entries = corp_mod.mining_entries(db, corp, self.m_period.currentData(), self.m_structure.currentData())
        rows = corp_mod.summarize(entries, self.ctx.syncer.price_map(), group, self.m_tax.value())
        headers = GROUP_HEADERS[group]
        self.m_table.setRowCount(0)
        self.m_table.set_headers(headers, prefs_key=f"corp_mining_{group}", optional=[])
        table_rows, csv_rows = [], []
        for r in rows:
            label = {"character": lambda k: v.name(k), "ore": lambda k: v.name(k),
                     "structure": lambda k: v.location_name(k), "day": lambda k: k}[group](r["key"])
            qty = SortItem(f"{r['quantity']:,}", r["quantity"], right=True)
            value = SortItem(fmt_isk(r["value"]), r["value"], right=True, tip=f"{r['value']:,.2f} ISK")
            share = SortItem(f"{r['share']:.1f}%", r["share"], right=True)
            if group == "character":
                table_rows.append([label, v.name(r["corp_id"]), qty, value,
                                   SortItem(fmt_isk(r["tax"]), r["tax"], right=True, tip=f"{r['tax']:,.2f} ISK"),
                                   share])
                csv_rows.append([label, v.name(r["corp_id"]), r["quantity"], round(r["value"], 2),
                                 round(r["tax"], 2), round(r["share"], 2)])
            elif group == "day":
                table_rows.append([label, qty, value, SortItem(r["miners"], r["miners"], right=True)])
                csv_rows.append([label, r["quantity"], round(r["value"], 2), r["miners"]])
            else:
                table_rows.append([label, qty, value, SortItem(r["miners"], r["miners"], right=True), share])
                csv_rows.append([label, r["quantity"], round(r["value"], 2), r["miners"], round(r["share"], 2)])
        fill_table(self.m_table, table_rows)
        if group == "day":
            self.m_table.sortItems(0, Qt.SortOrder.DescendingOrder)
        else:
            self.m_table.sortItems(headers.index("Est. value"), Qt.SortOrder.DescendingOrder)
        self._csv = (headers, csv_rows)
        total = sum(r["value"] for r in rows)
        miners = len({e["character_id"] for e in entries})
        tax = total * self.m_tax.value() / 100
        if entries:
            self.m_summary.setText(f"<b>{sum(e['quantity'] for e in entries):,}</b> units · <b>{fmt_isk(total)}</b> "
                                   f"ISK estimated · {miners} miner(s)"
                                   + (f" · tax owed <b>{fmt_isk(tax)}</b> ISK" if tax else ""))
        else:
            self.m_summary.setText(self._no_mining_reason(corp))
        first, last = corp_mod.history_span(db, corp)
        span = f"Local history: {first} to {last}. " if first else ""
        self.m_note.setText(span + "ESI only keeps 30 days; the app keeps everything it has seen, so history grows "
                                   "as long as it syncs at least every few weeks. Values use CCP's average prices "
                                   "for the raw ore.")

    def _no_mining_reason(self, corp):
        db = self.ctx.db
        chars = corp_mod.my_corporations(db).get(corp, [])
        states = [db.cache_get(c, "corp_mining_access") for c in chars]
        if any(s and s.get("ok") for s in states):
            return "No mining recorded in this period."
        if states and all(s and s.get("no_access") for s in states):
            return ("<span style='color:#dc3545'>None of your characters in this corporation has the Accountant "
                    "role, which ESI requires for the mining ledger.</span>")
        return ("No mining data yet. It needs a character with the Accountant role and the "
                "esi-industry.read_corporation_mining.v1 scope; the ledger refreshes hourly.")

    def _fill_members(self, corp):
        v = self.ctx.view
        rows, _ = corp_mod.member_activity(self.ctx.db, self.ctx.syncer, corp)
        out = []
        week = month = 0
        for m in rows:
            days = m.get("days_inactive")
            if days is not None and days <= 7:
                week += 1
            if days is not None and days <= 30:
                month += 1
            badge = (ACCENT["success"] if days is not None and days <= 7 else
                     ACCENT["warning"] if days is not None and days <= 30 else ACCENT["danger"])
            out.append([v.name(m.get("character_id")),
                        SortItem(fmt_time_short(parse_time(m.get("logon_date"))), parse_time(m.get("logon_date")) or 0,
                                 tip=fmt_time(parse_time(m.get("logon_date")))),
                        SortItem(f"{days:.0f} d" if days is not None else "?", days if days is not None else 9e9,
                                 badge=badge),
                        v.location_name(m["location_id"]) if m.get("location_id") else "",
                        v.name(m.get("ship_type_id"), "") if m.get("ship_type_id") else ""])
        fill_table(self.members, out)
        self.members.sortItems(2, Qt.SortOrder.DescendingOrder)
        if rows:
            self.members_summary.setText(f"<b>{len(rows)}</b> members · <b>{week}</b> active in the last 7 days · "
                                         f"<b>{month}</b> in the last 30 days · <b>{len(rows) - month}</b> inactive "
                                         f"for over 30 days")
        else:
            self.members_summary.setText("Member tracking needs a character with the Director role and the "
                                         "esi-corporations.track_members.v1 scope.")

    def _copy_csv(self):
        header, rows = self._csv
        if rows:
            QApplication.clipboard().setText(corp_mod.to_csv(header, rows))
            self.ctx.window.statusBar().showMessage(f"Copied {len(rows)} rows as CSV", 4000)
