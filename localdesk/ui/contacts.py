"""Contact list per character, plus syncing contacts from one character to others."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QRadioButton, QSplitter,
                               QVBoxLayout)

from .. import contacts_sync
from ..sync import S_CONTACTS, S_WRITE_CONTACTS
from .widgets import (USER_ROLE, Card, Page, SortItem, button, fill_char_combo, fill_table, make_table, muted,
                      standing_color, status_color)


def _standing(s):
    return f"{s:+.1f}" if s else "0.0"


class ContactsPage(Page):
    title = "Contacts"
    icon = "fa5s.address-book"
    section = "Character"

    def __init__(self, ctx):
        super().__init__(ctx)
        split = QSplitter(Qt.Orientation.Vertical)
        split.setHandleWidth(14)
        self.vbox().addWidget(split)

        self.list_card = Card("Contacts", accent="primary", margins=0)
        self.table = make_table(["Name", "Type", "Standing", "Blocked", "Watched"], stretch=0)
        self.list_card.add(self.table, 1)
        split.addWidget(self.list_card)

        box = Card("Sync contacts between characters", accent="info")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(lambda _: self._fill_targets())
        grid.addWidget(QLabel("<b>Copy from</b>"), 0, 0)
        grid.addWidget(self.source, 1, 0)
        grid.addWidget(QLabel("<b>To these characters</b>"), 2, 0)
        self.targets = QListWidget()
        self.targets.setMaximumHeight(130)
        self.targets.setStyleSheet("border: 1px solid palette(mid); border-radius: 4px;")
        grid.addWidget(self.targets, 3, 0)

        opts = QVBoxLayout()
        opts.addWidget(QLabel("<b>Mode</b>"))
        self.mode_group = QButtonGroup(self)
        for i, (key, label) in enumerate(contacts_sync.MODES.items()):
            rb = QRadioButton(label)
            rb.setProperty("mode", key)
            rb.setChecked(i == 0)
            self.mode_group.addButton(rb)
            opts.addWidget(rb)
        self.skip_own = QCheckBox("Don't copy my own characters as contacts (and never remove them)")
        self.skip_own.setChecked(True)
        alts = QHBoxLayout()
        self.add_alts = QCheckBox("Add all my characters to each other at standing")
        self.alt_standing = QDoubleSpinBox()
        self.alt_standing.setRange(-10, 10)
        self.alt_standing.setSingleStep(5)
        self.alt_standing.setValue(10)
        self.alt_standing.setDecimals(1)
        self.alt_standing.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.alt_standing.setFixedWidth(70)
        alts.addWidget(self.add_alts)
        alts.addWidget(self.alt_standing)
        alts.addStretch(1)
        opts.addSpacing(6)
        opts.addWidget(QLabel("<b>Options</b>"))
        opts.addWidget(self.skip_own)
        opts.addLayout(alts)
        opts.addStretch(1)
        grid.addLayout(opts, 0, 1, 4, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        box.body.addLayout(grid)

        buttons = QHBoxLayout()
        preview = button("Preview changes", "fa5s.search")
        preview.clicked.connect(self._preview)
        self.apply_btn = button("Apply changes", "fa5s.check", "success")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        self.plan_label = muted(wrap=False)
        buttons.addWidget(preview)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.plan_label, 1)
        box.body.addLayout(buttons)
        self.plan_table = make_table(["Character", "Action", "Contact", "Type", "Standing"], stretch=2)
        self.plan_table.setStyleSheet("border: 1px solid palette(mid);")
        box.add(self.plan_table, 1)
        box.add(muted("Corporation/alliance contacts and labels aren't copied (labels are per character). ESI caches "
                      "contact lists for up to 5 minutes, so a fresh preview may lag slightly."))
        split.addWidget(box)
        split.setSizes([280, 520])
        self._plans = {}

    def refresh_view(self):
        chars = self.ctx.characters()
        fill_char_combo(self.source, chars, keep=self.source.currentData() or self.ctx.current_char)
        self._fill_targets()
        cid = self.ctx.current_char
        v = self.ctx.view
        contacts = v.get(cid, "contacts") or [] if cid else []
        names = v.names([c["contact_id"] for c in contacts])
        rows = [[SortItem(names.get(c["contact_id"], c["contact_id"])),
                 c["contact_type"],
                 SortItem(_standing(c["standing"]), c["standing"], badge=standing_color(c["standing"])),
                 "Yes" if c.get("is_blocked") else "",
                 "Yes" if c.get("is_watched") else ""] for c in contacts]
        fill_table(self.table, rows)
        self.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        name = self.ctx.window.char_combo.currentText() if cid else "—"
        note = "" if not cid or self.ctx.syncer.has_scope(cid, S_CONTACTS) else " (no contacts permission)"
        self.list_card.set_title(f"Contacts of {name} ({len(contacts)}){note}")

    def _fill_targets(self):
        checked = {self.targets.item(i).data(USER_ROLE) for i in range(self.targets.count())
                   if self.targets.item(i).checkState() == Qt.CheckState.Checked}
        self.targets.clear()
        src = self.source.currentData()
        for ch in self.ctx.characters():
            if ch["character_id"] == src:
                continue
            can_write = S_WRITE_CONTACTS in ch["scopes"]
            item = QListWidgetItem(ch["name"] + ("" if can_write else "  (no write permission)"))
            item.setData(USER_ROLE, ch["character_id"])
            if can_write:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if ch["character_id"] in checked
                                   else Qt.CheckState.Unchecked)
            else:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.targets.addItem(item)
        self._plans = {}
        self.apply_btn.setEnabled(False)

    def _selected_targets(self):
        return [self.targets.item(i).data(USER_ROLE) for i in range(self.targets.count())
                if self.targets.item(i).checkState() == Qt.CheckState.Checked]

    def _preview(self):
        source, targets = self.source.currentData(), self._selected_targets()
        if not source or not targets:
            QMessageBox.information(self, "Contacts sync", "Pick a source character and tick at least one target.")
            return
        if not self.ctx.syncer.has_scope(source, S_CONTACTS):
            QMessageBox.information(self, "Contacts sync", "The source character didn't grant contacts access.")
            return
        mode = self.mode_group.checkedButton().property("mode")
        own_ids = [c["character_id"] for c in self.ctx.characters()]
        exclude = own_ids if self.skip_own.isChecked() else []
        extra = {i: ("character", self.alt_standing.value()) for i in own_ids} if self.add_alts.isChecked() else {}
        syncer = self.ctx.syncer

        def job(progress):
            progress("Fetching current contact lists…")
            path = "/characters/{}/contacts/"
            src = syncer.fetch(source, "contacts", path.format(source), paged=True, force=True)
            plans = {}
            for t in targets:
                current = syncer.fetch(t, "contacts", path.format(t), paged=True, force=True)
                plans[t] = contacts_sync.plan(src, current, t, mode, extra, exclude)
            syncer.resolve({c.contact_id for changes in plans.values() for c in changes})
            return plans

        self.ctx.run(job, self._show_plan, label="Preparing contact sync preview…")

    def _show_plan(self, plans):
        self._plans = plans
        v = self.ctx.view
        names = v.names({c.contact_id for ch in plans.values() for c in ch} | set(plans))
        rows, counts = [], {"add": 0, "update": 0, "remove": 0}
        for target, changes in plans.items():
            for c in changes:
                counts[c.action] += 1
                standing = _standing(c.standing) if c.action != "update" \
                    else f"{_standing(c.old_standing)} → {_standing(c.standing)}"
                rows.append([names.get(target, target),
                             SortItem(c.action, badge=status_color(c.action)),
                             names.get(c.contact_id, c.contact_id), c.contact_type,
                             SortItem(standing, c.standing, badge=standing_color(c.standing))])
        fill_table(self.plan_table, rows)
        total = sum(counts.values())
        self.plan_label.setText(f"{counts['add']} to add, {counts['update']} to update, "
                                f"{counts['remove']} to remove" if total else "Everything is already in sync ✓")
        self.apply_btn.setEnabled(bool(total))
        self.ctx.window.data_changed.emit()

    def _apply(self):
        plans = {t: c for t, c in self._plans.items() if c}
        if not plans:
            return
        removes = sum(1 for ch in plans.values() for c in ch if c.action == "remove")
        warn = f"\n\n{removes} contact(s) will be REMOVED." if removes else ""
        if QMessageBox.question(self, "Apply contact changes",
                                f"Apply {sum(map(len, plans.values()))} change(s) to {len(plans)} character(s)?"
                                f"{warn}") != QMessageBox.StandardButton.Yes:
            return
        esi, syncer = self.ctx.esi, self.ctx.syncer

        def job(progress):
            results = {}
            for target, changes in plans.items():
                results[target] = contacts_sync.apply(esi, target, changes, progress)
                try:
                    syncer.fetch(target, "contacts", f"/characters/{target}/contacts/", paged=True, force=True)
                except Exception:
                    pass
            return results

        def done(results):
            names = self.ctx.view.names(set(results) | {cid for _, f in results.values() for cid, _ in f})
            failed_total = 0
            for target, (applied, failures) in results.items():
                self.ctx.log(f"Contacts sync → {names.get(target, target)}: {applied} change(s) applied"
                             + (f", {len(failures)} failed" if failures else ""))
                for cid, err in failures:
                    failed_total += 1
                    self.ctx.log(f"   could not update {names.get(cid, cid)}: {err}")
            self._plans = {}
            self.apply_btn.setEnabled(False)
            self.plan_table.setRowCount(0)
            self.plan_label.setText("Done" + (f" — {failed_total} failure(s), see Activity log" if failed_total
                                              else " ✓"))
            self.ctx.window.data_changed.emit()

        self.ctx.run(job, done, label="Applying contact changes…")
