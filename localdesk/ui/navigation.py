"""Navigation: fleet locations, gate routes with jump bridges and alternatives, capital jump planner,
jump range, a Dotlan-style map, and jump-bridge / docking-access management."""
import json
import time
import webbrowser

from PySide6.QtCore import QSize, QStringListModel, Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from .. import navigation as nav
from ..model import fmt_isk
from ..sync import S_SEARCH_STRUCTURES, S_STRUCTURES, S_WAYPOINT
from ..universe import parse_bridge_list
from .map_view import StarMap
from .theme import ACCENT
from .widgets import (USER_ROLE, Card, Page, SortItem, button, fill_char_combo, fill_table, make_table, muted,
                      sec_color, sec_text)

ROUTE_MODES = [("Shortest", "shortest"), ("Safer (prefer high-sec)", "secure"), ("Less secure (avoid high-sec)",
                                                                                 "insecure")]


class VerticalScroll(QScrollArea):
    """Scrolls vertically only and is always wide enough for its contents, whatever the font size or
    display scaling."""

    def __init__(self, widget):
        super().__init__()
        self.setWidget(widget)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def _width(self):
        return self.widget().sizeHint().width() + self.verticalScrollBar().sizeHint().width() + 2

    def sizeHint(self):
        return QSize(self._width(), super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(self._width(), 120)


def compact(combo, chars=16):
    """Don't let the longest entry (e.g. a long ship name) decide how wide the panel gets."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(chars)
    return combo


def section(title):
    label = QLabel(title)
    label.setStyleSheet("font-size: 14px; font-weight: 600; padding-top: 6px; border-bottom: 1px solid palette(mid);")
    return label


def level_combo():
    combo = compact(QComboBox(), 8)
    for level in range(6):
        combo.addItem(f"Level {level}", level)
    combo.setCurrentIndex(5)
    return combo


def sec_item(sec):
    return SortItem(sec_text(sec), sec if sec is not None else -99, badge=sec_color(sec))


def dotlan_url(name, kind="system"):
    return f"https://evemaps.dotlan.net/{kind}/" + name.replace(" ", "_")


def hms(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    return f"{h}:{rem // 60:02d}:{rem % 60:02d}"


class NavigationPage(Page):
    title = "Navigation"
    icon = "fa5s.route"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._universe = None
        self._routes = []
        self._jump = []
        self._last = None  # which planner ran last, for "avoid & replan"
        lay = self.vbox()
        self.banner = QWidget()
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(0, 0, 0, 0)
        self.banner_label = QLabel("Map data is loading or hasn't been downloaded yet. Routes fall back to ESI "
                                   "(no jump bridges) until it's ready.")
        self.banner_label.setObjectName("warn")
        retry = button("Download map data", "fa5s.download")
        retry.clicked.connect(lambda: self.ctx.window.load_universe(download=True))
        bl.addWidget(self.banner_label, 1)
        bl.addWidget(retry)
        lay.addWidget(self.banner)

        card = Card(margins=0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(lambda _: self._ensure_maps())
        self.names_model = QStringListModel()
        self.avoid_model = QStringListModel()
        self.tabs.addTab(self._fleet_tab(), "Fleet")
        self.tabs.addTab(self._route_tab(), "Route")
        self.tabs.addTab(self._jump_tab(), "Jump planner")
        self.tabs.addTab(self._range_tab(), "Jump range")
        self.tabs.addTab(self._map_tab(), "Map")
        self.tabs.addTab(self._access_tab(), "Bridges && access")
        card.add(self.tabs, 1)
        lay.addWidget(card, 1)

    # --- building blocks ----------------------------------------------------
    def _system_edit(self, placeholder):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        completer = QCompleter(self.names_model, edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        edit.setCompleter(completer)
        return edit

    def _from_row(self, edit):
        row = QHBoxLayout()
        here = button("", "fa5s.crosshairs", tip="Use the selected character's current system")
        here.clicked.connect(lambda: self._fill_from_char(edit))
        row.addWidget(edit, 1)
        row.addWidget(here)
        return row

    def _panel(self):
        panel = QWidget()
        panel.setMinimumWidth(340)
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 14, 14, 14)
        form = QFormLayout()
        form.setVerticalSpacing(9)
        v.addLayout(form)
        return panel, v, form

    def _results(self, headers, stretch):
        """Summary line + table on top, map underneath."""
        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        summary = QLabel()
        summary.setContentsMargins(14, 10, 14, 10)
        summary.setWordWrap(True)
        table = make_table(headers, stretch=stretch)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda pos, t=table: self._table_menu(t, pos))
        tv.addWidget(summary)
        tv.addWidget(table, 1)
        split.addWidget(top)
        smap = StarMap()
        smap.system_menu.connect(self._system_menu)
        split.addWidget(smap)
        split.setSizes([300, 420])
        return split, summary, table, smap

    @staticmethod
    def _wrap(panel, right):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(panel if isinstance(panel, QScrollArea) else VerticalScroll(panel))
        h.addWidget(right, 1)
        return w

    # --- tabs -----------------------------------------------------------------
    def _fleet_tab(self):
        self.fleet = make_table(["Character", "System", "Sec", "Docked at", "Online"], stretch=3, prefs_key="fleet")
        self.fleet.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fleet.customContextMenuRequested.connect(lambda pos: self._table_menu(self.fleet, pos, col=1))
        return self.fleet

    def _route_tab(self):
        panel, v, form = self._panel()
        self.r_from = self._system_edit("e.g. Jita")
        self.r_to = self._system_edit("e.g. 1DQ1-A")
        self.r_avoid = QLineEdit()
        self.r_avoid.setPlaceholderText("Systems or regions, e.g. Uedama, Niarja")
        self.r_mode = compact(QComboBox())
        for label, value in ROUTE_MODES:
            self.r_mode.addItem(label, value)
        self.r_bridges = QCheckBox("Use jump bridges")
        self.r_bridges.setChecked(True)
        form.addRow("From", self._from_row(self.r_from))
        form.addRow("To", self.r_to)
        form.addRow("Avoid", self.r_avoid)
        form.addRow("Prefer", self.r_mode)
        form.addRow("", self.r_bridges)
        plan = button("Find routes", "fa5s.route", "primary")
        plan.clicked.connect(self._plan_route)
        self.r_to.returnPressed.connect(self._plan_route)
        v.addWidget(plan)
        v.addSpacing(6)
        v.addWidget(QLabel("<b>Routes</b>"))
        self.r_alts = QListWidget()
        self.r_alts.setMaximumHeight(140)
        self.r_alts.currentRowChanged.connect(self._show_route)
        v.addWidget(self.r_alts)
        v.addWidget(muted("Right-click a system in the list or on the map to avoid it and re-plan."))
        v.addStretch(1)
        ap = QHBoxLayout()
        self.r_char = compact(QComboBox(), 12)
        self.r_waypoints = button("Set waypoints", "fa5s.map-marker-alt",
                                  tip="Set the route in game (a waypoint at each jump bridge)")
        self.r_waypoints.clicked.connect(self._set_waypoints)
        ap.addWidget(self.r_char, 1)
        ap.addWidget(self.r_waypoints)
        v.addWidget(QLabel("Autopilot for"))
        v.addLayout(ap)
        copy = button("Copy route", "fa5s.copy")
        copy.clicked.connect(lambda: self._copy(self._route_text()))
        v.addWidget(copy)
        right, self.r_summary, self.r_table, self.r_map = self._results(["#", "System", "Sec", "Region", "Via"], 3)
        return self._wrap(panel, right)

    def _jump_tab(self):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 12, 22, 14)
        v.setSpacing(7)
        scroll = VerticalScroll(panel)

        v.addWidget(section("Waypoints"))
        row = QHBoxLayout()
        self.j_sys = self._system_edit("Add a system…")
        self.j_sys.returnPressed.connect(self._add_waypoint)
        add = button("Add", "fa5s.plus", tip="Add this system as the next waypoint")
        add.clicked.connect(self._add_waypoint)
        here = button("", "fa5s.crosshairs", tip="Add the pilot's current system")
        here.clicked.connect(self._add_pilot_system)
        row.addWidget(self.j_sys, 1)
        row.addWidget(add)
        row.addWidget(here)
        v.addLayout(row)
        self.j_wps = self._drag_list()
        v.addWidget(self.j_wps)
        row = QHBoxLayout()
        row.addWidget(muted("Drag to sort. First = start, last = destination.", wrap=True), 1)
        rm = button("", "fa5s.times", tip="Remove the selected waypoint")
        rm.clicked.connect(lambda: self._remove_selected(self.j_wps))
        clear = button("", "fa5s.trash", tip="Clear all waypoints")
        clear.clicked.connect(self.j_wps.clear)
        row.addWidget(rm)
        row.addWidget(clear)
        v.addLayout(row)
        plan = button("Go", "fa5s.rocket", "primary")
        plan.clicked.connect(self._plan_jumps)
        v.addWidget(plan)

        v.addWidget(section("Jump options"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.j_pilot = compact(QComboBox())
        self.j_pilot.setToolTip("Fills in the skill levels below and decides which structures and beacons you "
                                "can use.")
        self.j_pilot.currentIndexChanged.connect(lambda _: self._pilot_changed())
        self.j_ship = compact(QComboBox())
        self.j_ship.setMaxVisibleItems(25)
        self._ships_key = None
        self.j_ship.currentIndexChanged.connect(lambda _: self._options_changed())
        self.j_jdc, self.j_jfc, self.j_jf = (level_combo() for _ in range(3))
        for combo in (self.j_jdc, self.j_jfc, self.j_jf):
            combo.currentIndexChanged.connect(lambda _: self._options_changed())
        self.j_prefer = QCheckBox()
        self.j_prefer.setToolTip("Prefer midpoints with an NPC station, a structure the pilot can access or a "
                                 "cyno beacon, when that doesn't add jumps.")
        self.j_incursions = QCheckBox()
        self.j_save = QCheckBox()
        self.j_save.setToolTip("Remember the pilot, ship, levels and options next time.")
        for cb in (self.j_prefer, self.j_incursions, self.j_save):
            cb.toggled.connect(lambda _: self._options_changed())
        form.addRow("Pilot", self.j_pilot)
        form.addRow("Ship", self.j_ship)
        form.addRow("Jump Drive Calibration", self.j_jdc)
        form.addRow("Jump Fuel Conservation", self.j_jfc)
        form.addRow("Jump Freighter", self.j_jf)
        form.addRow("Prefer station systems", self.j_prefer)
        form.addRow("Avoid incursions", self.j_incursions)
        form.addRow("Save settings", self.j_save)
        v.addLayout(form)
        self.j_info = QLabel()
        self.j_info.setWordWrap(True)
        v.addWidget(self.j_info)

        v.addWidget(section("Avoid systems / regions"))
        row = QHBoxLayout()
        self.j_avoid_edit = QLineEdit()
        self.j_avoid_edit.setPlaceholderText("System or region…")
        completer = QCompleter(self.avoid_model, self.j_avoid_edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.j_avoid_edit.setCompleter(completer)
        self.j_avoid_edit.returnPressed.connect(self._add_avoid)
        avoid = button("Avoid", "fa5s.ban")
        avoid.clicked.connect(self._add_avoid)
        row.addWidget(self.j_avoid_edit, 1)
        row.addWidget(avoid)
        v.addLayout(row)
        self.j_avoid = QListWidget()
        self.j_avoid.setMaximumHeight(90)
        v.addWidget(self.j_avoid)
        rm = button("Remove selected", "fa5s.times")
        rm.clicked.connect(lambda: self._remove_selected(self.j_avoid))
        v.addWidget(rm)

        v.addWidget(section("Jump beacon network"))
        self.j_beacons = muted()
        v.addWidget(self.j_beacons)

        v.addWidget(section("Load / save route"))
        row = QHBoxLayout()
        self.j_saved = compact(QComboBox(), 12)
        load = button("Load", "fa5s.folder-open")
        load.clicked.connect(self._load_route)
        save = button("Save", "fa5s.save")
        save.clicked.connect(self._save_route)
        delete = button("", "fa5s.trash", tip="Delete the selected saved route")
        delete.clicked.connect(self._delete_route)
        row.addWidget(self.j_saved, 1)
        for b in (load, save, delete):
            row.addWidget(b)
        v.addLayout(row)
        copy = button("Copy jump route", "fa5s.copy")
        copy.clicked.connect(lambda: self._copy(" → ".join(self._universe.name(s) for s, _ in self._jump)
                                                if self._jump else ""))
        v.addWidget(copy)
        v.addStretch(1)

        right, self.j_summary, self.j_table, self.j_map = self._results(
            ["#", "System", "Sec", "Region", "Jump (LY)", "Fuel", "Jump timer", "Fatigue after", "Dock at"], 8)
        self._pilot_id = None
        self._restoring = True  # don't overwrite saved settings while building the panel
        self._fill_ships()
        self._restoring = False
        self._restore_settings()
        return self._wrap(scroll, right)

    def _range_tab(self):
        panel, v, form = self._panel()
        self.g_from = self._system_edit("e.g. 1DQ1-A")
        self.g_range = QDoubleSpinBox()
        self.g_range.setRange(0.5, 15)
        self.g_range.setDecimals(2)
        self.g_range.setSuffix(" LY")
        self.g_range.setValue(7.0)
        self.g_jumpable = QCheckBox("Only systems a jump drive can target")
        self.g_jumpable.setChecked(True)
        form.addRow("From", self._from_row(self.g_from))
        form.addRow("Range", self.g_range)
        form.addRow("", self.g_jumpable)
        use = button("Use the jump planner's range", "fa5s.link")
        use.clicked.connect(lambda: self.g_range.setValue(self._jump_range()))
        find = button("Find systems in range", "fa5s.bullseye", "primary")
        find.clicked.connect(self._find_range)
        self.g_from.returnPressed.connect(self._find_range)
        v.addWidget(use)
        v.addWidget(find)
        v.addWidget(muted("Your structures are those the jump planner's pilot can access."))
        v.addStretch(1)
        right, self.g_summary, self.g_table, self.g_map = self._results(
            ["System", "Sec", "Region", "Distance (LY)", "NPC stations", "Your structures"], 5)
        return self._wrap(panel, right)

    def _map_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 10)
        self.m_find = self._system_edit("Find a system…")
        self.m_find.setMaximumWidth(260)
        self.m_find.returnPressed.connect(self._map_find)
        go = button("", "fa5s.search", tip="Show on map")
        go.clicked.connect(self._map_find)
        self.m_bridges = QCheckBox("Jump bridges")
        self.m_bridges.setChecked(True)
        self.m_chars = QCheckBox("My characters")
        self.m_chars.setChecked(True)
        for cb in (self.m_bridges, self.m_chars):
            cb.toggled.connect(lambda _: self._map_overlays())
        bar.addWidget(self.m_find)
        bar.addWidget(go)
        bar.addSpacing(12)
        bar.addWidget(self.m_bridges)
        bar.addWidget(self.m_chars)
        bar.addStretch(1)
        bar.addWidget(muted("Scroll to zoom (system names appear as you zoom in), drag to pan, right-click a "
                            "system for actions. Purple lines are gates between regions.", wrap=True), 2)
        v.addLayout(bar)
        self.m_map = StarMap()
        self.m_map.system_menu.connect(self._system_menu)
        v.addWidget(self.m_map, 1)
        return w

    def _access_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        self.a_status = muted(wrap=False)
        redownload = button("Re-download map data", "fa5s.download")
        redownload.clicked.connect(lambda: self.ctx.window.load_universe(download=True))
        top.addWidget(self.a_status, 1)
        top.addWidget(redownload)
        v.addLayout(top)
        split = QSplitter(Qt.Orientation.Vertical)
        b = QWidget()
        bv = QVBoxLayout(b)
        bv.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        self.a_bridge_title = QLabel("<b>Jump bridges</b>")
        scan = button("Scan with my characters", "fa5s.satellite-dish", "primary",
                      tip="Ask ESI which structures and jump bridges each character can access")
        scan.clicked.connect(self._scan_access)
        imp = button("Import list…", "fa5s.file-import")
        imp.clicked.connect(self._import_bridges)
        clear = button("Clear imported", "fa5s.trash")
        clear.clicked.connect(self._clear_imported)
        bar.addWidget(self.a_bridge_title, 1)
        for x in (scan, imp, clear):
            bar.addWidget(x)
        bv.addLayout(bar)
        self.a_bridges = make_table(["From", "Sec", "To", "Sec", "Source", "Structure"], stretch=5)
        bv.addWidget(self.a_bridges, 1)
        split.addWidget(b)
        s = QWidget()
        sv = QVBoxLayout(s)
        sv.setContentsMargins(0, 8, 0, 0)
        self.a_struct_title = QLabel("<b>Structures your characters can access</b>")
        sv.addWidget(self.a_struct_title)
        self.a_structs = make_table(["Structure", "System", "Sec", "Region", "Characters"], stretch=0)
        sv.addWidget(self.a_structs, 1)
        split.addWidget(s)
        v.addWidget(split, 1)
        v.addWidget(muted("Found automatically through each character's ESI structure access (needs the "
                          "esi-search.search_structures.v1 and esi-universe.read_structures.v1 scopes) and "
                          "refreshed every 6 hours. Bridges you can't see through ESI can be imported as a list "
                          "(one per line, e.g. “1DQ1-A » 8WA-Z6” or “A <-> B”)."))
        return w

    # --- shared helpers ---------------------------------------------------------
    def _chars(self):
        return self.ctx.characters()

    def _bridges(self):
        if not self._universe:
            return []
        return nav.all_bridges(self.ctx.db, self._universe, [c["character_id"] for c in self._chars()])

    def _dockable(self, pilot):
        u = self._universe
        systems = {s["system_id"] for s in nav.accessible_structures(self.ctx.db, [pilot] if pilot else []).values()}
        return lambda sid: bool(u.stations.get(sid)) or sid in systems

    def _structures_by_system(self, pilot):
        out = {}
        for s in nav.accessible_structures(self.ctx.db, [pilot] if pilot else []).values():
            out.setdefault(s["system_id"], []).append(s["name"])
        return out

    def _fill_from_char(self, edit):
        cid = self.ctx.current_char
        system = self.ctx.view.location(cid)["system"] if cid else "—"
        if system and system != "—":
            edit.setText(system)

    def _copy(self, text):
        if text:
            QApplication.clipboard().setText(text)
            self.ctx.window.statusBar().showMessage("Copied to clipboard", 4000)

    def _error(self, label, e):
        label.setText(f"<span style='color:{ACCENT['danger']}'>{e}</span>")

    def _ensure_maps(self):
        if not self._universe:
            return
        current = self.tabs.currentWidget()
        for smap in (self.r_map, self.j_map, self.g_map, self.m_map):
            if smap.universe is not self._universe and current.isAncestorOf(smap):
                smap.set_universe(self._universe)
                if smap is self.m_map:
                    self._map_overlays()
                    cid = self.ctx.current_char
                    sid = self.ctx.view.location(cid)["system_id"] if cid else None
                    if sid:
                        smap.center_on_system(sid, 1.0)

    def _map_overlays(self):
        if self.m_map.universe is None:
            return
        self.m_map.show_bridges(self._bridges(), self.m_bridges.isChecked())
        by_system = {}
        for c in self._chars():
            sid = self.ctx.view.location(c["character_id"])["system_id"]
            if sid:
                by_system.setdefault(sid, []).append(c["name"])
        self.m_map.show_characters(by_system, self.m_chars.isChecked())

    # --- refresh ----------------------------------------------------------------
    def refresh_view(self):
        u = self.ctx.universe
        if u is not self._universe:
            self._universe = u
            if u:
                self.names_model.setStringList(sorted(s.name for s in u.systems.values()))
                self.avoid_model.setStringList(sorted([s.name for s in u.systems.values()] + list(u.regions.values())))
        self.banner.setVisible(u is None)
        chars = self._chars()
        fill_char_combo(self.r_char, chars, keep=self.r_char.currentData() or self.ctx.current_char)
        self._fill_ships()
        last_pilot = self.ctx.db.get_setting("jump_pilot")
        fill_char_combo(self.j_pilot, chars, keep=self.j_pilot.currentData()
                        or (int(last_pilot) if last_pilot else None) or self.ctx.current_char)
        if self.j_pilot.currentData() != self._pilot_id:
            self._pilot_changed()
        else:
            self._options_changed()  # fatigue may have changed with the last sync
        self._fill_beacons()
        self._fill_saved()
        self._fill_fleet()
        self._fill_access()
        self._ensure_maps()
        self._map_overlays()

    def _fill_fleet(self):
        v, u = self.ctx.view, self._universe
        rows = []
        for ch in self._chars():
            s = v.summary(ch["character_id"])
            loc = s["location"]
            sid = loc["system_id"]
            rows.append([SortItem(s["name"], sub=s["ship"]),
                         SortItem(loc["system"], data=sid, sub=u.region_name(sid) if u and sid else None),
                         sec_item(s["security"]), loc["docked"] or "(in space)",
                         SortItem("Online" if s["online"] else "Offline",
                                  badge=ACCENT["success"] if s["online"] else ACCENT["secondary"])])
        fill_table(self.fleet, rows)

    def _fill_access(self):
        u = self._universe
        self.a_status.setText(f"Map data: {len(u.systems):,} systems, {sum(len(g) for g in u.gates.values()) // 2:,} "
                              f"gates, {sum(len(s) for s in u.stations.values()):,} NPC stations" if u
                              else "Map data not loaded yet.")
        if not u:
            return
        bridges = self._bridges()
        self.a_bridge_title.setText(f"<b>Jump bridges ({len(bridges)})</b>")
        self.r_bridges.setText(f"Use jump bridges ({len(bridges)} known)")
        fill_table(self.a_bridges, [[SortItem(u.name(b["a"]), data=b["a"]), sec_item(u.sec(b["a"])),
                                     SortItem(u.name(b["b"]), data=b["b"]), sec_item(u.sec(b["b"])),
                                     SortItem(b["source"], badge=ACCENT["info"] if b["source"] == "ESI"
                                              else ACCENT["secondary"]), b["name"]] for b in bridges])
        names = {c["character_id"]: c["name"] for c in self._chars()}
        structs = nav.accessible_structures(self.ctx.db, list(names)).values()
        self.a_struct_title.setText(f"<b>Structures your characters can access ({len(structs)})</b>")
        fill_table(self.a_structs, [[s["name"], SortItem(u.name(s["system_id"]), data=s["system_id"]),
                                     sec_item(u.sec(s["system_id"]) if s["system_id"] in u.systems else None),
                                     u.region_name(s["system_id"]),
                                     ", ".join(names.get(c, str(c)) for c in s["characters"])]
                                    for s in structs])

    # --- context menus ----------------------------------------------------------
    def _table_menu(self, table, pos, col=1):
        row = table.rowAt(pos.y())
        if row < 0:
            return
        item = table.item(row, col)
        sid = item.data(USER_ROLE) if item else None
        if sid:
            self._system_menu(int(sid), table.viewport().mapToGlobal(pos))

    def _system_menu(self, sid, global_pos):
        if not self._universe or sid not in self._universe.systems:
            return
        name = self._universe.name(sid)
        menu = QMenu(self)
        menu.addSection(name)
        menu.addAction("Route from here", lambda: (self.r_from.setText(name), self.g_from.setText(name),
                                                   self._set_waypoint_end(name, first=True)))
        menu.addAction("Route to here", lambda: (self.r_to.setText(name), self._set_waypoint_end(name, first=False)))
        menu.addAction("Add as jump waypoint", lambda: self._add_waypoint(name))
        if self._last:
            menu.addAction("Avoid this system && re-plan", lambda: self._avoid(name))
        menu.addAction("Jump range from here", lambda: (self.g_from.setText(name), self.tabs.setCurrentIndex(3),
                                                        self._find_range()))
        menu.addAction("Show on map", lambda: (self.tabs.setCurrentIndex(4), self._ensure_maps(),
                                               self.m_map.center_on_system(sid)))
        menu.addSeparator()
        menu.addAction("Open system on Dotlan", lambda: webbrowser.open(dotlan_url(name)))
        region = self._universe.region_name(sid)
        if region:
            menu.addAction(f"Open {region} on Dotlan", lambda: webbrowser.open(dotlan_url(region, "map")))
        menu.exec(global_pos)

    def _avoid(self, name):
        if self._last == "jump":
            self._add_avoid(name)
            return self._plan_jumps()
        current = [t.strip() for t in self.r_avoid.text().split(",") if t.strip()]
        if name.lower() not in (c.lower() for c in current):
            self.r_avoid.setText(", ".join(current + [name]))
        self._plan_route()

    # --- route planner ------------------------------------------------------------
    def _plan_route(self):
        self._last = "route"
        origin, dest = self.r_from.text().strip(), self.r_to.text().strip()
        if not origin or not dest:
            return
        mode = self.r_mode.currentData()
        u = self._universe
        if u is None:  # fallback: ESI's route endpoint (no bridges, one route)
            avoid = [a.strip() for a in self.r_avoid.text().split(",") if a.strip()]
            self.r_summary.setText("Planning with ESI…")
            self.ctx.run(lambda p: self.ctx.syncer.plan_route(origin, dest, mode, avoid), self._show_esi_route,
                         on_error=lambda e: self._error(self.r_summary, e))
            return
        try:
            o, d = u.require(origin), u.require(dest)
            avoid = u.resolve_avoid(self.r_avoid.text().split(","))
        except ValueError as e:
            return self._error(self.r_summary, e)
        bridges = [(b["a"], b["b"]) for b in self._bridges()] if self.r_bridges.isChecked() else []
        self.r_summary.setText("Finding routes…")
        self.ctx.run(lambda p: u.route_alternatives(o, d, mode, avoid, bridges), self._routes_found,
                     on_error=lambda e: self._error(self.r_summary, e))

    def _show_esi_route(self, route):
        self._routes = [[(s["id"], "start" if i == 0 else "gate") for i, s in enumerate(route)]]
        rows = [[SortItem(i, i, right=True), SortItem(s["name"]), sec_item(s["security"]), "", ""]
                for i, s in enumerate(route)]
        fill_table(self.r_table, rows)
        self.r_summary.setText(f"<b>{len(route) - 1}</b> jumps (ESI route; map data not loaded)")

    def _routes_found(self, paths):
        u = self._universe
        self._routes = [u.describe(p) for p in paths]
        self.r_alts.blockSignals(True)
        self.r_alts.clear()
        for i, steps in enumerate(self._routes, 1):
            secs = [u.sec(s) for s, _ in steps]
            n_bridges = sum(1 for _, how in steps if how == "bridge")
            unsafe = sum(1 for s, _ in steps if not u.is_highsec(s))
            text = (f"Route {i}: {len(steps) - 1} jumps · lowest {sec_text(min(secs))} · {unsafe} low/null"
                    + (f" · {n_bridges} bridge{'s' if n_bridges != 1 else ''}" if n_bridges else ""))
            QListWidgetItem(text, self.r_alts)
        self.r_alts.blockSignals(False)
        if not self._routes:
            self.r_table.setRowCount(0)
            self.r_summary.setText(f"<span style='color:{ACCENT['danger']}'>No route found. Try fewer avoided "
                                   f"systems.</span>")
            return
        self.r_alts.setCurrentRow(0)
        self._show_route(0)

    def _show_route(self, index):
        if not (0 <= index < len(self._routes)):
            return
        u, steps = self._universe, self._routes[index]
        rows = []
        for i, (sid, how) in enumerate(steps):
            via = {"start": SortItem("Start"), "gate": SortItem("Gate"),
                   "bridge": SortItem("Jump bridge", badge=ACCENT["info"])}[how]
            rows.append([SortItem(i, i, right=True), SortItem(u.name(sid), data=sid), sec_item(u.sec(sid)),
                         u.region_name(sid), via])
        fill_table(self.r_table, rows)
        self.r_table.sortItems(0, Qt.SortOrder.AscendingOrder)
        secs = [u.sec(s) for s, _ in steps]
        regions = list(dict.fromkeys(u.region_name(s) for s, _ in steps))
        self.r_summary.setText(f"<b>{len(steps) - 1}</b> jumps · lowest security <span style='color:"
                               f"{sec_color(min(secs))}'><b>{sec_text(min(secs))}</b></span> · "
                               f"{' → '.join(regions)}")
        if self.r_map.universe is self._universe:
            self.r_map.show_route(steps, "gate")
        self.r_waypoints.setEnabled(True)

    def _current_route(self):
        row = self.r_alts.currentRow()
        return self._routes[row] if 0 <= row < len(self._routes) else None

    def _route_text(self):
        steps = self._current_route()
        if not steps:
            return ""
        u = self._universe
        return " → ".join(u.name(s) + (" (bridge)" if how == "bridge" else "") for s, how in steps)

    def _set_waypoints(self):
        steps, cid = self._current_route(), self.r_char.currentData()
        if not steps or not cid:
            return
        if not self.ctx.syncer.has_scope(cid, S_WAYPOINT):
            QMessageBox.information(self, "Missing permission", "This character didn't grant esi-ui.write_waypoint.v1.")
            return
        # In-game autopilot can't use jump bridges, so add a waypoint at each bridge's entry system.
        points = [steps[i - 1][0] for i, (_, how) in enumerate(steps) if how == "bridge"] + [steps[-1][0]]
        points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]

        def job(progress):
            for i, sid in enumerate(points):
                self.ctx.syncer.set_waypoint(cid, sid, clear_others=(i == 0))
            return len(points)

        self.ctx.run(job, lambda n: self.ctx.window.statusBar().showMessage(f"Set {n} waypoint(s) in game", 5000))

    # --- jump planner ---------------------------------------------------------------
    @staticmethod
    def _drag_list():
        lst = QListWidget()
        lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        lst.setDefaultDropAction(Qt.DropAction.MoveAction)
        lst.setMaximumHeight(130)
        return lst

    @staticmethod
    def _remove_selected(lst):
        for item in lst.selectedItems():
            lst.takeItem(lst.row(item))

    @staticmethod
    def _items(lst):
        return [lst.item(i).text() for i in range(lst.count())]

    def _add_waypoint(self, name=None):
        name = (name or self.j_sys.text()).strip()
        if not name or not self._universe:
            return
        sid = self._universe.find(name)
        if sid is None:
            return self._error(self.j_summary, f"Unknown solar system: {name}")
        self.j_wps.addItem(self._universe.name(sid))
        self.j_sys.clear()

    def _add_pilot_system(self):
        pilot = self.j_pilot.currentData()
        system = self.ctx.view.location(pilot)["system"] if pilot else "—"
        if system and system != "—":
            self._add_waypoint(system)

    def _set_waypoint_end(self, name, first):
        if first:
            if self.j_wps.count():
                self.j_wps.item(0).setText(name)
            else:
                self.j_wps.addItem(name)
        elif self.j_wps.count() >= 2:
            self.j_wps.item(self.j_wps.count() - 1).setText(name)
        else:
            self.j_wps.addItem(name)

    def _add_avoid(self, name=None):
        name = (name or self.j_avoid_edit.text()).strip()
        if not name or not self._universe:
            return
        try:
            self._universe.resolve_avoid([name])
        except ValueError as e:
            return self._error(self.j_summary, e)
        if name.lower() not in (n.lower() for n in self._items(self.j_avoid)):
            sid = self._universe.find(name)
            self.j_avoid.addItem(self._universe.name(sid) if sid else
                                 self._universe.regions[self._universe.region_by_name[name.lower()]])
        self.j_avoid_edit.clear()

    def _fill_ships(self):
        ships = self.ctx.db.cache_get(0, "jump_ships")
        catalogue = bool(ships)
        ships = ships or nav.fallback_ships()
        key = tuple(s["name"] for s in ships)
        if key == self._ships_key:
            return
        self._ships_key = key
        keep = self._ship()["name"] if self.j_ship.count() else None
        self.j_ship.blockSignals(True)
        self.j_ship.clear()
        if catalogue:
            model = self.j_ship.model()
            for label, hulls in nav.grouped_ships(ships):
                self.j_ship.addItem(label.upper())
                header = model.item(self.j_ship.count() - 1)
                header.setFlags(Qt.ItemFlag.NoItemFlags)  # a heading, not a choice
                font = header.font()
                font.setBold(True)
                font.setPointSizeF(font.pointSizeF() * 0.85)
                header.setFont(font)
                for ship in hulls:
                    self.j_ship.addItem("    " + ship["name"], ship)
        else:
            for ship in ships:
                self.j_ship.addItem(ship["name"], ship)
        self._select_ship(keep)
        self.j_ship.blockSignals(False)
        self._options_changed()

    def _select_ship(self, name):
        first = None
        for i in range(self.j_ship.count()):
            data = self.j_ship.itemData(i)
            if not data:
                continue
            if first is None:
                first = i
            if data["name"] == name:
                self.j_ship.setCurrentIndex(i)
                return
        self.j_ship.setCurrentIndex(first if first is not None else 0)

    def _ship(self):
        return self.j_ship.currentData() or nav.fallback_ships()[0]

    def _jump_range(self):
        return self._ship()["range_base"] * (1 + 0.2 * self.j_jdc.currentData())

    def _fuel_multiplier(self):
        return nav.fuel_reduction(self._ship()["group"], self.j_jfc.currentData(), self.j_jf.currentData())

    def _fatigue_now(self):
        pilot = self.j_pilot.currentData()
        return nav.current_fatigue(self.ctx.db, pilot, time.time()) if pilot else 0.0

    def _pilot_changed(self):
        """A different pilot: take the skill levels from that character."""
        pilot = self.j_pilot.currentData()
        self._pilot_id = pilot
        if pilot:
            self.ctx.db.set_setting("jump_pilot", str(pilot))
        if not self._restoring:
            for combo, skill in ((self.j_jdc, nav.JDC_SKILL), (self.j_jfc, nav.JFC_SKILL),
                                 (self.j_jf, nav.JF_SKILL)):
                level = nav.skill_level(self.ctx.db, pilot, skill)
                if level is not None:
                    combo.setCurrentIndex(level)
        self._options_changed()

    def _options_changed(self):
        """Range and fuel follow from the ship and levels; they aren't editable."""
        ship = self._ship()
        isotope = self.ctx.view.name(ship["fuel_type"], "isotopes") if ship.get("fuel_type") else "isotopes"
        if ship["fuel_per_ly"]:
            per_ly = ship["fuel_per_ly"] * self._fuel_multiplier()
            fuel = f"{per_ly:,.0f} {isotope} per LY"
        else:
            fuel = "fuel data loads after the next sync"
        self.j_info.setText(f"Range <b>{self._jump_range():.2f} LY</b> · {fuel}<br>"
                            f"<span style='color:#6c757d'>Pilot's jump fatigue now: {hms(self._fatigue_now())}</span>")
        if not self._restoring:
            self._store_settings()

    def _store_settings(self):
        if not self.j_save.isChecked():
            if self.ctx.db.get_setting("jump_settings"):
                self.ctx.db.set_setting("jump_settings", "")
            return
        self.ctx.db.set_setting("jump_settings", json.dumps({
            "pilot": self.j_pilot.currentData(), "ship": self._ship()["name"],
            "jdc": self.j_jdc.currentData(), "jfc": self.j_jfc.currentData(), "jf": self.j_jf.currentData(),
            "prefer": self.j_prefer.isChecked(), "incursions": self.j_incursions.isChecked()}))

    def _restore_settings(self):
        raw = self.ctx.db.get_setting("jump_settings")
        if not raw:
            return
        s = json.loads(raw)
        self._restoring = True
        try:
            fill_char_combo(self.j_pilot, self._chars(), keep=s.get("pilot"))
            self._pilot_id = self.j_pilot.currentData()
            self._select_ship(s.get("ship"))
            self.j_jdc.setCurrentIndex(s.get("jdc", 5))
            self.j_jfc.setCurrentIndex(s.get("jfc", 4))
            self.j_jf.setCurrentIndex(s.get("jf", 4))
            self.j_prefer.setChecked(s.get("prefer", False))
            self.j_incursions.setChecked(s.get("incursions", False))
            self.j_save.setChecked(True)
        finally:
            self._restoring = False
        self._options_changed()

    def _preferred(self, pilot):
        """Midpoints worth preferring: NPC station, accessible structure or accessible cyno beacon."""
        dockable = self._dockable(pilot)
        beacons = {b["system_id"] for b in nav.cyno_beacons(self.ctx.db, [pilot] if pilot else [])}
        return lambda sid: dockable(sid) or sid in beacons

    def _plan_jumps(self):
        self._last = "jump"
        u = self._universe
        if u is None:
            return self._error(self.j_summary, "Map data isn't loaded yet.")
        names = self._items(self.j_wps)
        if len(names) < 2:
            return self._error(self.j_summary, "Add at least a start and a destination waypoint.")
        try:
            waypoints = [u.require(n) for n in names]
            avoid = u.resolve_avoid(self._items(self.j_avoid))
        except ValueError as e:
            return self._error(self.j_summary, e)
        ly, pilot = self._jump_range(), self.j_pilot.currentData()
        midpoints = "prefer" if self.j_prefer.isChecked() else "any"
        preferred, incursions, syncer = self._preferred(pilot), self.j_incursions.isChecked(), self.ctx.syncer
        self.j_summary.setText("Planning jumps…")

        def job(progress):
            blocked = set(avoid)
            if incursions:
                blocked |= nav.incursion_systems(syncer)
            return u.jump_route_via(waypoints, ly, preferred, midpoints, blocked)

        self.ctx.run(job, self._jumps_found, on_error=lambda e: self._error(self.j_summary, e))

    def _jumps_found(self, result):
        u = self._universe
        route, marks = result
        self._jump = route or []
        if not route:
            self.j_table.setRowCount(0)
            self.j_summary.setText(f"<span style='color:{ACCENT['danger']}'>No jump route with a "
                                   f"{self._jump_range():.2f} LY range. Try fewer avoided systems.</span>")
            return
        pilot, ship = self.j_pilot.currentData(), self._ship()
        dockable = self._dockable(pilot)
        structures = self._structures_by_system(pilot)
        beacons = {b["system_id"] for b in nav.cyno_beacons(self.ctx.db, [pilot] if pilot else [])}
        fatigue = nav.fatigue_plan([ly for _, ly in route[1:]], ship["factor"], self._fatigue_now())
        per_ly = ship["fuel_per_ly"]
        fuel = [nav.fuel_for_jump(ly, per_ly, self._fuel_multiplier()) for _, ly in route[1:]] if per_ly else []
        marks = set(marks)
        rows, undocked = [], 0
        for i, (sid, ly) in enumerate(route):
            docks = []
            if u.stations.get(sid):
                docks.append(f"{len(u.stations[sid])} NPC station{'s' if len(u.stations[sid]) != 1 else ''}")
            docks += structures.get(sid, [])[:2]
            if sid in beacons:
                docks.append("Cyno beacon")
            if i not in marks and not dockable(sid):
                undocked += 1
            f = fatigue[i - 1] if i else None
            rows.append([SortItem(i, i, right=True), SortItem(u.name(sid), data=sid, bold=i in marks),
                         sec_item(u.sec(sid)), u.region_name(sid),
                         SortItem(f"{ly:.2f}" if i else "", ly, right=True),
                         SortItem(f"{fuel[i - 1]:,}" if i and fuel else "", fuel[i - 1] if i and fuel else 0,
                                  right=True),
                         SortItem(hms(f["reactivation"]) if f else "", f["reactivation"] if f else 0, right=True),
                         SortItem(hms(f["after"]) if f else "", f["after"] if f else 0, right=True,
                                  color=ACCENT["danger"] if f and f["after"] >= nav.FATIGUE_CAP else None),
                         SortItem(", ".join(docks) or ("—" if i else ""),
                                  color=ACCENT["danger"] if not docks and i not in marks else None)])
        fill_table(self.j_table, rows)
        self.j_table.sortItems(0, Qt.SortOrder.AscendingOrder)
        total = sum(ly for _, ly in route)
        travel = sum(f["reactivation"] for f in fatigue[:-1])
        note = f" · <span style='color:{ACCENT['danger']}'>{undocked} midpoint(s) without a dock</span>" if undocked else ""
        if fuel:
            isotope = self.ctx.view.name(ship["fuel_type"], "isotopes") if ship.get("fuel_type") else "isotopes"
            price = self.ctx.syncer.price_map().get(ship.get("fuel_type"), 0)
            cost = f" (≈ {fmt_isk(sum(fuel) * price)} ISK)" if price else ""
            fuel_text = f"<b>{sum(fuel):,}</b> {isotope}{cost}"
        else:
            fuel_text = "fuel unknown until the ship data has synced"
        self.j_summary.setText(f"<b>{len(route) - 1}</b> jumps · {total:.2f} LY · {fuel_text} · waiting on jump "
                               f"timers {hms(travel)} · fatigue at the end <b>{hms(fatigue[-1]['after'])}</b>{note}")
        if self.j_map.universe is self._universe:
            self.j_map.show_route(route, "jump")

    def _fill_saved(self):
        keep = self.j_saved.currentText()
        self.j_saved.clear()
        self.j_saved.addItems(sorted(nav.saved_routes(self.ctx.db)))
        idx = self.j_saved.findText(keep)
        if idx >= 0:
            self.j_saved.setCurrentIndex(idx)

    def _save_route(self):
        if self.j_wps.count() < 2:
            return self._error(self.j_summary, "Add at least a start and a destination before saving.")
        default = f"{self.j_wps.item(0).text()} → {self.j_wps.item(self.j_wps.count() - 1).text()}"
        name, ok = QInputDialog.getText(self, "Save route", "Name:", text=self.j_saved.currentText() or default)
        if ok and name.strip():
            nav.save_route(self.ctx.db, name.strip(), self._items(self.j_wps), self._items(self.j_avoid))
            self._fill_saved()
            self.j_saved.setCurrentText(name.strip())

    def _load_route(self):
        route = nav.saved_routes(self.ctx.db).get(self.j_saved.currentText())
        if not route:
            return
        self.j_wps.clear()
        self.j_wps.addItems(route["waypoints"])
        self.j_avoid.clear()
        self.j_avoid.addItems(route["avoid"])
        self._plan_jumps()

    def _delete_route(self):
        name = self.j_saved.currentText()
        if name and QMessageBox.question(self, "Delete route", f"Delete the saved route “{name}”?") \
                == QMessageBox.StandardButton.Yes:
            nav.delete_route(self.ctx.db, name)
            self._fill_saved()

    def _fill_beacons(self):
        names = {c["character_id"]: c["name"] for c in self._chars()}
        beacons = nav.cyno_beacons(self.ctx.db, list(names))
        if not beacons:
            self.j_beacons.setText("No cyno beacons found. Beacons your characters can access show up here "
                                   "after a structure access scan (Bridges & access).")
            return
        u = self._universe
        lines = [f"<b>{u.name(b['system_id']) if u else b['system_id']}</b>: {b['name']}" for b in beacons[:12]]
        more = f"<br>…and {len(beacons) - 12} more" if len(beacons) > 12 else ""
        self.j_beacons.setText(f"{len(beacons)} beacon(s), preferred as midpoints:<br>" + "<br>".join(lines) + more)

    # --- range ------------------------------------------------------------------------
    def _find_range(self):
        u = self._universe
        if u is None:
            return self._error(self.g_summary, "Map data isn't loaded yet.")
        try:
            o = u.require(self.g_from.text())
        except ValueError as e:
            return self._error(self.g_summary, e)
        ly, jumpable = self.g_range.value(), self.g_jumpable.isChecked()
        found = u.in_range(o, ly, jumpable)
        structures = self._structures_by_system(self.j_pilot.currentData())
        fill_table(self.g_table, [[SortItem(u.name(sid), data=sid), sec_item(u.sec(sid)), u.region_name(sid),
                                   SortItem(f"{d:.2f}", d, right=True),
                                   SortItem(len(u.stations.get(sid, [])) or "", len(u.stations.get(sid, [])),
                                            right=True),
                                   ", ".join(structures.get(sid, []))] for sid, d in found])
        self.g_table.sortItems(3, Qt.SortOrder.AscendingOrder)
        docks = sum(1 for sid, _ in found if u.stations.get(sid) or sid in structures)
        self.g_summary.setText(f"<b>{len(found)}</b> systems within {ly:.2f} LY of {u.name(o)} · {docks} with a "
                               f"station or your structure")
        self._ensure_maps()
        if self.g_map.universe is self._universe:
            self.g_map.show_highlight(o, [sid for sid, _ in found], ly)

    # --- map tab ----------------------------------------------------------------------
    def _map_find(self):
        if self._universe:
            sid = self._universe.find(self.m_find.text())
            if sid:
                self.m_map.center_on_system(sid)

    # --- bridges & access ---------------------------------------------------------------
    def _scan_access(self):
        chars = [c for c in self._chars() if S_SEARCH_STRUCTURES in c["scopes"] and S_STRUCTURES in c["scopes"]]
        if not chars:
            QMessageBox.information(self, "Missing permission",
                                    "None of your characters granted esi-search.search_structures.v1 and "
                                    "esi-universe.read_structures.v1. Add those scopes to your EVE application, "
                                    "then add the characters again.")
            return

        def job(progress):
            errors = []
            for c in chars:
                progress(f"Scanning structure access: {c['name']}…")
                try:
                    self.ctx.syncer.scan_access(c["character_id"], force=True)
                except Exception as e:
                    errors.append(f"{c['name']}: {e}")
            return errors

        def done(errors):
            for e in errors:
                self.ctx.log(f"Structure access scan failed for {e}")
            self.ctx.window.data_changed.emit()

        self.ctx.run(job, done, label="Scanning structure access…")

    def _import_bridges(self):
        if not self._universe:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Import jump bridges")
        dlg.resize(520, 420)
        v = QVBoxLayout(dlg)
        v.addWidget(muted("Paste one bridge per line, e.g. “1DQ1-A » 8WA-Z6”, “A <-> B” or “A, B”. Ansiblex "
                          "names copied from the game work too."))
        text = QPlainTextEdit()
        v.addWidget(text, 1)
        replace = QCheckBox("Replace the previously imported list")
        v.addWidget(replace)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        pairs, bad = parse_bridge_list(text.toPlainText(), self._universe)
        existing = [] if replace.isChecked() else nav.manual_bridges(self.ctx.db)
        nav.set_manual_bridges(self.ctx.db, existing + pairs)
        msg = f"Imported {len(pairs)} bridge(s)."
        if bad:
            msg += f"\n\nCouldn't read {len(bad)} line(s):\n" + "\n".join(bad[:10])
        QMessageBox.information(self, "Import jump bridges", msg)
        self.ctx.log(f"Imported {len(pairs)} jump bridge(s)")
        self.refresh_view()

    def _clear_imported(self):
        if QMessageBox.question(self, "Clear imported bridges", "Remove all imported jump bridges?") \
                == QMessageBox.StandardButton.Yes:
            nav.set_manual_bridges(self.ctx.db, [])
            self.refresh_view()
