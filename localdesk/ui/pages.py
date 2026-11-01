"""Dashboard, character sheet, assets and the activity log."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QTabWidget, QTreeWidget)

from .. import paths
from ..sso import SCOPES
from ..sync import location_kind
from ..model import ROMAN, fmt_duration, fmt_isk, fmt_sp, fmt_time, fmt_time_short, parse_time
from .. import alerts
from .theme import ACCENT
from .widgets import (USER_ROLE, Card, InfoBox, Page, SortItem, TreeItem, fill_table, avatar_placeholder, isk_color, make_table, muted, round_pixmap, sec_color, sec_text)


def _placeholder(text):
    label = muted(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setContentsMargins(30, 30, 30, 30)
    return label


def sec_item(sec):
    return SortItem(sec_text(sec), sec if sec is not None else -99, badge=sec_color(sec))


class DashboardPage(Page):
    title = "Dashboard"
    icon = "fa5s.tachometer-alt"
    section = "Overview"
    HEADERS = ["Character", "Location", "Sec", "Ship", "Wallet", "SP", "Training", "Mail"]

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = self.vbox()
        boxes = QHBoxLayout()
        boxes.setSpacing(14)
        self.box_chars = InfoBox("fa5s.users", ACCENT["info"], "Characters")
        self.box_isk = InfoBox("fa5s.coins", ACCENT["success"], "Total wallet (ISK)")
        self.box_sp = InfoBox("fa5s.graduation-cap", ACCENT["warning"], "Total skill points")
        self.box_mail = InfoBox("fa5s.envelope", ACCENT["primary"], "Unread mail")
        self.box_alerts = InfoBox("fa5s.exclamation-triangle", ACCENT["danger"], "Alerts")
        for b in (self.box_chars, self.box_isk, self.box_sp, self.box_mail, self.box_alerts):
            boxes.addWidget(b)
        lay.addLayout(boxes)
        card = Card("Characters", accent="primary", margins=0)
        card.add_tool(muted("Double-click a character to open their sheet", wrap=False))
        self.table = make_table(self.HEADERS, stretch=6, prefs_key="dashboard")
        self.table.cellDoubleClicked.connect(self._open)
        card.add(self.table, 1)
        self.empty = _placeholder("No characters yet.\n\nClick the cog (top right) to enter your EVE application's "
                                  "Client ID, then click “Add character” to log in.")
        card.add(self.empty, 1)
        lay.addWidget(card, 1)

    def refresh_view(self):
        rows, isk, sp = [], 0.0, 0
        chars = self.ctx.characters()
        self.empty.setVisible(not chars)
        self.table.setVisible(bool(chars))
        now = time.time()
        for ch in chars:
            s = self.ctx.view.summary(ch["character_id"])
            isk += s["wallet"] or 0
            sp += s["sp"] or 0
            tr = s["training"]
            loc = s["location"]
            if tr["state"] == "training":
                left = fmt_duration(tr["current_end"] - now)
                queue_left = tr["end"] - now
                training = SortItem(tr["text"], tr["current_end"] or 0,
                                    sub=f"{left} left · queue {fmt_duration(queue_left)}",
                                    color=ACCENT["orange"] if queue_left < 86400 else None,
                                    tip=f"Queue ends {fmt_time(tr['end'])}"
                                        + (" (less than 24h of training left)" if queue_left < 86400 else ""))
            else:
                training = SortItem(tr["text"], 0, badge=ACCENT["warning"] if tr["state"] == "paused"
                                    else ACCENT["danger"])
            corp = s["corporation"] + (f" [{s['alliance']}]" if s["alliance"] else "")
            ship_type, _, ship_name = s["ship"].partition(" — ")
            rows.append([
                SortItem(("● " if s["online"] else "") + s["name"], s["name"].lower(), data=s["id"],
                         bold=bool(s["online"]), sub=corp,
                         tip=f"{'Online · ' if s['online'] else ''}last synced {fmt_time(s['last_sync'])}"),
                SortItem(loc["system"], sub=loc["docked"] or "in space",
                         tip=loc["docked"] or f"{loc['system']} (in space)"),
                sec_item(s["security"]),
                SortItem(ship_type, sub=ship_name or None),
                SortItem(fmt_isk(s["wallet"]), s["wallet"] or 0, right=True,
                         tip=f"{s['wallet']:,.2f} ISK" if s["wallet"] is not None else None),
                SortItem(fmt_isk(s["sp"]).replace(".00", ".0") if s["sp"] else "—", s["sp"] or 0, right=True,
                         tip=f"{fmt_sp(s['sp'])} skill points" if s["sp"] else None),
                training,
                SortItem(s["unread"] if s["unread"] else "", s["unread"] or 0,
                         badge=ACCENT["primary"] if s["unread"] else None),
            ])
        fill_table(self.table, rows)
        unread = sum((self.ctx.view.get(c["character_id"], "mail_labels") or {}).get("total_unread_count") or 0
                     for c in chars)
        urgent = sum(1 for a in alerts.compute(self.ctx.db, self.ctx.syncer, self.ctx.view)
                     if a["level"] != "info")
        self.box_chars.set(len(chars))
        self.box_isk.set(fmt_isk(isk))
        self.box_sp.set(fmt_sp(sp))
        self.box_mail.set(unread)
        self.box_alerts.set(urgent)

    def _open(self, row, _col):
        self.ctx.window.select_char(self.table.item(row, 0).data(USER_ROLE))
        self.ctx.window.show_page("Character")


class CharacterPage(Page):
    title = "Character"
    icon = "fa5s.user-astronaut"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        profile = Card(accent="primary", margins=18)
        profile.setFixedWidth(320)
        self.portrait = QLabel()
        self.portrait.setFixedSize(128, 128)
        profile.body.addWidget(self.portrait, 0, Qt.AlignmentFlag.AlignHCenter)
        self.name_label = QLabel()
        self.name_label.setObjectName("profileName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.corp_label = muted()
        self.corp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.facts = QLabel()
        self.facts.setTextFormat(Qt.TextFormat.RichText)
        self.facts.setWordWrap(True)
        self.scope_note = QLabel()
        self.scope_note.setObjectName("warn")
        self.scope_note.setWordWrap(True)
        for w in (self.name_label, self.corp_label, self.facts, self.scope_note):
            profile.add(w)
        profile.body.addStretch(1)
        lay.addWidget(profile)
        self.info = self.name_label  # kept for the "no character" message

        tabs_card = Card(margins=0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.queue = make_table(["#", "Skill", "Level", "Starts", "Finishes", "Time left"], stretch=1)
        self.skills = QTreeWidget()
        self.skills.setHeaderLabels(["Skill", "Level", "Skill points"])
        self.skills.setSortingEnabled(True)
        self.skills.setAlternatingRowColors(True)
        self.skills.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.journal = make_table(["Date", "Type", "Amount", "Balance", "Description"], stretch=4)
        self.clones = QTreeWidget()
        self.clones.setHeaderLabels(["Clones & implants"])
        self.tabs.addTab(self.queue, "Skill queue")
        self.tabs.addTab(self.skills, "Skills")
        self.tabs.addTab(self.journal, "Wallet journal")
        self.tabs.addTab(self.clones, "Clones && implants")
        tabs_card.add(self.tabs, 1)
        lay.addWidget(tabs_card, 1)
        self._portraits = {}
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_queue)
        self._tick.start(30_000)

    def refresh_view(self):
        cid = self.ctx.current_char
        if not cid:
            self.name_label.setText("No character selected")
            for label in (self.corp_label, self.facts, self.scope_note):
                label.clear()
            self.portrait.setPixmap(avatar_placeholder(128))
            for t in (self.queue, self.journal):
                t.setRowCount(0)
            self.skills.clear()
            self.clones.clear()
            return
        self._load_portrait(cid)
        v = self.ctx.view
        s = v.summary(cid)
        attrs = v.get(cid, "attributes") or {}
        sec = s["security_status"]
        attr_text = ", ".join(f"{k[:3].title()} {attrs[k]}" for k in
                              ("intelligence", "memory", "perception", "willpower", "charisma") if k in attrs)
        missing = set(SCOPES) - set(s["scopes"])
        self.name_label.setText(s["name"])
        self.corp_label.setText(s["corporation"] + (f"\n{s['alliance']}" if s["alliance"] else ""))
        sec_color_ = sec_color(s["security"]) or "#6c757d"
        facts = [
            ("Security status", f"{sec:.2f}" if sec is not None else "—"),
            ("Location", f"{s['location']['text']} <span style='color:{sec_color_}'><b>"
                         f"{sec_text(s['security'])}</b></span>"),
            ("Ship", s["ship"]),
            ("Wallet", f"<b>{fmt_isk(s['wallet'])}</b> ISK"),
            ("Skill points", f"<b>{fmt_sp(s['sp'])}</b>"),
        ]
        if s["unallocated_sp"]:
            facts.append(("Unallocated SP", fmt_sp(s["unallocated_sp"])))
        facts.append(("Training", s["training"]["text"]))
        facts.append(("Attributes", attr_text or "—"))
        if attrs.get("bonus_remaps"):
            facts.append(("Remaps available", str(attrs["bonus_remaps"])))
        facts.append(("Last sync", fmt_time(s["last_sync"])))
        rows = "".join(f"<tr><td style='padding:6px 0; color:#6c757d'><b>{k}</b></td>"
                       f"<td style='padding:6px 0 6px 10px' align='right'>{v}</td></tr>" for k, v in facts)
        self.facts.setText(f"<table width='100%' cellspacing='0'>{rows}</table>")
        self.scope_note.setText(f"Missing {len(missing)} scope(s), so some data won't load. Add the character "
                                f"again to grant them." if missing else "")
        self._refresh_queue()
        self._fill_skills(cid)
        self._fill_journal(cid)
        self._fill_clones(cid)

    def _load_portrait(self, cid):
        if cid in self._portraits:
            self.portrait.setPixmap(self._portraits[cid])
            return
        path = paths.portraits_dir() / f"{cid}.jpg"

        def show(_=None):
            pix = QPixmap(str(path))
            if not pix.isNull():
                self._portraits[cid] = round_pixmap(pix, 128)
                self.ctx.window._update_user_panel(self.ctx.current_char)
                if self.ctx.current_char == cid:
                    self.portrait.setPixmap(self._portraits[cid])

        if path.exists():
            show()
            return
        self.portrait.setPixmap(avatar_placeholder(128))

        def download(_progress):
            r = self.ctx.esi.http.get(f"https://images.evetech.net/characters/{cid}/portrait?size=128", timeout=20)
            r.raise_for_status()
            path.write_bytes(r.content)

        self.ctx.run(download, show, on_error=lambda e: None)

    def _refresh_queue(self):
        cid = self.ctx.current_char
        if not cid:
            return
        now = time.time()
        rows = []
        for q in sorted(self.ctx.view.get(cid, "skillqueue") or [], key=lambda q: q.get("queue_position", 0)):
            start, finish = parse_time(q.get("start_date")), parse_time(q.get("finish_date"))
            if finish and finish < now:
                continue
            training = start is not None and start <= now
            rows.append([
                SortItem(q.get("queue_position", 0) + 1, q.get("queue_position", 0), right=True),
                SortItem(self.ctx.view.name(q["skill_id"]), bold=training),
                SortItem(ROMAN[q["finished_level"]], q["finished_level"]),
                SortItem(fmt_time(start), start or 0),
                SortItem(fmt_time(finish) if finish else "paused", finish or 0),
                SortItem(fmt_duration(finish - now) if finish else "—", finish or 0, right=True),
            ])
        fill_table(self.queue, rows)

    def _fill_skills(self, cid):
        self.skills.clear()
        data = self.ctx.view.get(cid, "skills") or {}
        group_of = self.ctx.view.skill_group_of()
        names = self.ctx.view.names([s["skill_id"] for s in data.get("skills", [])])
        groups = {}
        for s in data.get("skills", []):
            groups.setdefault(group_of.get(s["skill_id"], "Other"), []).append(s)
        for group, skills in groups.items():
            total = sum(s["skillpoints_in_skill"] for s in skills)
            parent = TreeItem([group, f"{len(skills)} skills", fmt_sp(total)], {2: total})
            f = parent.font(0)
            f.setBold(True)
            parent.setFont(0, f)
            for s in skills:
                trained, active = s["trained_skill_level"], s["active_skill_level"]
                level = ROMAN[trained] + (f" (active {ROMAN[active]})" if active != trained else "")
                parent.addChild(TreeItem([names.get(s["skill_id"], s["skill_id"]), level,
                                          fmt_sp(s["skillpoints_in_skill"])],
                                         {1: trained, 2: s["skillpoints_in_skill"]}))
            self.skills.addTopLevelItem(parent)
        self.skills.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.skills.resizeColumnToContents(1)

    def _fill_journal(self, cid):
        rows = []
        for j in self.ctx.view.get(cid, "journal") or []:
            ts = parse_time(j.get("date"))
            amount = j.get("amount", 0) or 0
            rows.append([
                SortItem(fmt_time_short(ts), ts, tip=fmt_time(ts)),
                SortItem(j.get("ref_type", "").replace("_", " ")),
                SortItem(f"{amount:,.2f}", amount, color=isk_color(amount), right=True),
                SortItem(f"{j.get('balance', 0) or 0:,.2f}", j.get("balance", 0) or 0, right=True),
                SortItem(j.get("description", "")),
            ])
        fill_table(self.journal, rows)
        self.journal.sortItems(0, Qt.SortOrder.DescendingOrder)

    def _fill_clones(self, cid):
        v = self.ctx.view
        self.clones.clear()
        implants = v.get(cid, "implants") or []
        active = TreeItem([f"Active clone — {len(implants)} implant(s)"])
        for t in implants:
            active.addChild(TreeItem([v.name(t)]))
        self.clones.addTopLevelItem(active)
        clones = v.get(cid, "clones") or {}
        home = (clones.get("home_location") or {}).get("location_id")
        self.clones.addTopLevelItem(TreeItem([f"Home station: {v.location_name(home) if home else '—'}"]))
        last = parse_time(clones.get("last_clone_jump_date"))
        jumps = TreeItem([f"Jump clones ({len(clones.get('jump_clones', []))})"
                          + (f" — last jump {fmt_time(last)}" if last else "")])
        for jc in clones.get("jump_clones", []):
            label = v.location_name(jc["location_id"]) + (f" — “{jc['name']}”" if jc.get("name") else "")
            node = TreeItem([f"{label} ({len(jc.get('implants', []))} implants)"])
            for t in jc.get("implants", []):
                node.addChild(TreeItem([v.name(t)]))
            jumps.addChild(node)
        self.clones.addTopLevelItem(jumps)
        self.clones.expandAll()


class AssetsPage(Page):
    title = "Assets"
    icon = "fa5s.boxes"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = self.vbox()
        card = Card("Assets", accent="primary", margins=0)
        self.all_chars = QCheckBox("All characters")
        self.all_chars.setChecked(True)
        self.all_chars.toggled.connect(self.refresh_view)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search items, e.g. Raven, PLEX, Large Shield…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(340)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh_view)
        self.search.textChanged.connect(lambda _: self._debounce.start())
        self.total = muted(wrap=False)
        card.add_tool(self.total)
        card.add_tool(self.all_chars)
        card.add_tool(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item / location", "Quantity", "Est. value (ISK)", "Owner", "Where"])
        self.tree.setSortingEnabled(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._location_menu)
        card.add(self.tree, 1)
        lay.addWidget(card, 1)
        lay.addWidget(muted("Values use CCP's average market prices; blueprint copies count as 0. "
                            "ESI refreshes assets about once an hour. Right-click a structure your characters "
                            "can't dock at to give it a name."))

    def refresh_view(self):
        v = self.ctx.view
        if self.all_chars.isChecked():
            cids = [c["character_id"] for c in self.ctx.characters()]
        else:
            cids = [self.ctx.current_char] if self.ctx.current_char else []
        owners = {c["character_id"]: c["name"] for c in self.ctx.characters()}
        roots = v.asset_tree(cids)
        query = self.search.text().strip().lower()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        if query:
            matches = [(n, p) for n, p in v.flatten_assets(roots) if query in n["name"].lower()]
            for node, path in matches[:5000]:
                item = self._node_item(node, owners, path)
                self.tree.addTopLevelItem(item)
            value = sum(n["value"] for n, _ in matches)
            self.total.setText(f"{len(matches)} matches · {fmt_isk(value)} ISK")
        else:
            grand = 0.0
            kinds = self.ctx.db.get_name_entries([r["location_id"] for r in roots])
            for root in roots:
                grand += root["total"]
                kind = kinds.get(root["location_id"], (None, None))[1]
                name = root["name"] + ("  (your name)" if kind == "structure_custom" else "")
                top = TreeItem([name, len(root["items"]), fmt_isk(root["total"]), "", ""],
                               {1: len(root["items"]), 2: root["total"]}, data=root["location_id"])
                if kind in ("structure_unknown", "structure_custom"):
                    top.setToolTip(0, "None of your characters can dock here, so EVE won't say what this "
                                      "structure is. Right-click to give it your own name.")
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                for node in root["items"]:
                    top.addChild(self._node_item(node, owners))
                self.tree.addTopLevelItem(top)
            self.total.setText(f"{len(roots)} locations · {fmt_isk(grand)} ISK")
        self.tree.sortByColumn(self.tree.sortColumn(), self.tree.header().sortIndicatorOrder())
        self.tree.setUpdatesEnabled(True)
        for c in (1, 2, 3):
            self.tree.resizeColumnToContents(c)

    def _location_menu(self, pos):
        item = self.tree.itemAt(pos)
        lid = item.data(0, USER_ROLE) if item is not None and item.parent() is None else None
        if not lid or location_kind(int(lid)) != "structure":
            return
        lid = int(lid)
        name, kind = self.ctx.db.get_name_entries([lid]).get(lid, (None, None))
        if kind == "structure":
            return  # EVE already gave us its real name
        menu = QMenu(self)
        menu.addAction("Name this location…", lambda: self._name_location(lid, name if kind == "structure_custom"
                                                                           else ""))
        if kind == "structure_custom":
            menu.addAction("Remove my name", lambda: self._clear_location_name(lid))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _name_location(self, lid, current):
        text, ok = QInputDialog.getText(self, "Name this location",
                                        "None of your characters can dock here, so EVE won't share its name.\n"
                                        "Give it your own (kept until EVE reveals the real one):", text=current)
        if ok and text.strip():
            self.ctx.db.put_names([(lid, text.strip(), "structure_custom")])
            self.ctx.window.data_changed.emit()

    def _clear_location_name(self, lid):
        self.ctx.db.put_names([(lid, f"Unknown structure #{lid} (no docking access)", "structure_unknown")])
        self.ctx.window.data_changed.emit()

    def _node_item(self, node, owners, path=""):
        a = node["item"]
        flag = a.get("location_flag", "")
        item = TreeItem([node["name"], f"{a['quantity']:,}", fmt_isk(node["total"]) if node["total"] else "",
                         owners.get(node["owner"], ""), path or flag],
                        {1: a["quantity"], 2: node["total"]})
        for child in node["children"]:
            item.addChild(self._node_item(child, owners))
        return item


class LogPage(Page):
    title = "Activity log"
    icon = "fa5s.list"
    section = "Tools"

    def __init__(self, ctx):
        super().__init__(ctx)
        card = Card("Activity log", margins=0)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("border: none;")
        card.add(self.text, 1)
        self.vbox().addWidget(card)

    def append(self, message):
        self.text.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")


from .mail import MailPage  # noqa: E402
from .contacts import ContactsPage  # noqa: E402
from .client_settings import ClientSettingsPage  # noqa: E402
from .data_browser import DataBrowserPage  # noqa: E402
from .alerts_page import AlertsPage  # noqa: E402
from .navigation import NavigationPage  # noqa: E402
from .corporation import CorporationPage  # noqa: E402

PAGES = [DashboardPage, AlertsPage, CharacterPage, AssetsPage, NavigationPage, MailPage, ContactsPage,
         DataBrowserPage, CorporationPage, ClientSettingsPage, LogPage]
