"""Copy EVE client settings (overview, window layout, account settings) between characters/accounts."""
import json
import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import eve_settings, paths
from ..model import fmt_time
from .widgets import USER_ROLE, Card, Page, SortItem, button, fill_table, make_table, muted

HELP = ("The row you <b>select</b> is copied <b>from</b>; the rows you <b>tick</b> are overwritten. "
        "<b>Characters</b> (core_char_*.dat) hold the overview, window layout and positions, chat channels and other "
        "per-character UI. <b>Accounts</b> (core_user_*.dat) hold account-wide settings such as keyboard shortcuts. "
        "Select the row to copy <i>from</i>, tick the rows to copy <i>to</i>, then click Copy. Targets are backed up "
        "first. <b>Close every EVE client before copying</b>; the client rewrites these files when it exits.")


class FileTable(QWidget):
    """One table of settings files with a 'copy to' checkbox column."""

    def __init__(self, page, kind):
        super().__init__()
        self.page, self.kind = page, kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if kind == "char":
            headers = ["Copy to", "Character", "ID", "Last modified", "Size"]
        else:
            headers = ["Copy to", "Account (double-click to name it)", "Probably these characters", "ID",
                       "Last modified", "Size"]
        self.mtime_col = headers.index("Last modified")
        self.table = make_table(headers, stretch=1)
        if kind == "user":
            self.table.cellDoubleClicked.connect(self._rename)
        self.table.currentCellChanged.connect(lambda *_: self._update_source())
        self.source_label = QLabel()
        self.source_label.setContentsMargins(14, 10, 14, 6)
        lay.addWidget(self.source_label)
        lay.addWidget(self.table)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 10, 12, 12)
        copy = button("Copy selected row → ticked rows", "fa5s.copy", "primary")
        copy.clicked.connect(lambda: page.copy(self))
        tick_all = button("Tick all", "fa5s.check-square")
        tick_all.clicked.connect(lambda: self._tick(True))
        untick = button("Untick all", "fa5s.square")
        untick.clicked.connect(lambda: self._tick(False))
        bar.addWidget(copy)
        bar.addStretch(1)
        bar.addWidget(tick_all)
        bar.addWidget(untick)
        lay.addLayout(bar)
        self.files = []

    def fill(self, files, label_for, guess_for=None):
        self.files = files
        self.label_for = label_for
        rows = []
        for i, f in enumerate(files):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Unchecked)
            check.setData(USER_ROLE, i)
            row = [check, SortItem(label_for(f), data=i, color=None if f.exists else "#6c757d")]
            if guess_for:
                row.append(SortItem(guess_for(f) or "—", tip="Characters whose settings were saved at the same "
                                                           "time as this account file, i.e. the last characters "
                                                           "you logged out of on this account."))
            rows.append(row + [SortItem(f.owner_id, f.owner_id),
                         SortItem(fmt_time(f.mtime, True) if f.exists else "no file yet (will be created)",
                                  f.mtime or 0),
                         SortItem(f"{f.size / 1024:,.0f} KB" if f.exists else "", f.size, right=True)])
        fill_table(self.table, rows)
        self.table.sortItems(self.mtime_col, Qt.SortOrder.DescendingOrder)
        self._update_source()

    def _update_source(self):
        src = self.selected()
        if src is None:
            self.source_label.setText("Copying <b>from</b>: nothing yet. Click the row you want to copy from "
                                      "(e.g. your main), then tick the rows to copy to.")
        elif not src.exists:
            self.source_label.setText("<span style='color:#dc3545'>That character has no settings file yet, so "
                                      "it can only be copied to.</span>")
        else:
            self.source_label.setText(f"Copying <b>from</b>: <b style='color:#007bff'>{self.label_for(src)}</b>"
                                      f" &nbsp;({src.path.name}, saved {fmt_time(src.mtime)})")

    def _tick(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 1) is None:
            return None
        return self.files[self.table.item(row, 1).data(USER_ROLE)] if row >= 0 else None

    def ticked(self):
        return [self.files[self.table.item(r, 0).data(USER_ROLE)] for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]

    def _rename(self, row, _col):
        from PySide6.QtWidgets import QInputDialog
        f = self.files[self.table.item(row, 1).data(USER_ROLE)]
        key = f"account_label:{f.owner_id}"
        text, ok = QInputDialog.getText(self, "Name this account", f"Label for account file {f.owner_id}:",
                                        text=self.page.ctx.db.get_setting(key, ""))
        if ok:
            self.page.ctx.db.set_setting(key, text.strip())
            self.page.rescan()


class ClientSettingsPage(Page):
    title = "Client settings"
    icon = "fa5s.sliders-h"
    section = "Tools"

    def __init__(self, ctx):
        super().__init__(ctx)
        lay = self.vbox()
        top = Card("EVE settings profile", accent="primary")
        bar = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.setMinimumWidth(500)
        self.profile.currentIndexChanged.connect(lambda _: self._profile_changed())
        browse = button("Browse…", "fa5s.folder-open")
        browse.clicked.connect(self._browse)
        rescan = button("", "fa5s.sync-alt", tip="Rescan")
        rescan.clicked.connect(self.refresh_view)
        open_btn = button("", "fa5s.external-link-alt", tip="Open this folder")
        open_btn.clicked.connect(lambda: self.profile.currentData() and
                                 QDesktopServices.openUrl(QUrl.fromLocalFile(self.profile.currentData())))
        bar.addWidget(self.profile, 1)
        bar.addWidget(browse)
        bar.addWidget(open_btn)
        bar.addWidget(rescan)
        top.body.addLayout(bar)
        self.warning = QLabel()
        self.warning.setObjectName("warn")
        top.add(self.warning)
        top.add(muted(HELP))
        lay.addWidget(top)

        files = Card(margins=0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.chars = FileTable(self, "char")
        self.users = FileTable(self, "user")
        self.tabs.addTab(self.chars, "Characters: overview + UI layout")
        self.tabs.addTab(self.users, "Accounts: shortcuts + account-wide")
        files.add(self.tabs, 1)
        lay.addWidget(files, 3)
        lay.addWidget(muted("Which account file is which? The Accounts tab's \"Probably these characters\" column "
                            "matches each account file to the characters saved at the same moment (the client saves "
                            "both when you log out). To be certain, log out of your main, exit the game and click "
                            "Rescan: your main's account file is then the newest one. Double-click it to name it."))

        box = Card("Backups for this profile", margins=0)
        restore = button("Restore selected", "fa5s.undo")
        restore.clicked.connect(self._restore)
        open_backups = button("", "fa5s.folder-open", tip="Open the backups folder")
        open_backups.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._backup_root()))))
        box.add_tool(restore)
        box.add_tool(open_backups)
        self.backups = make_table(["Created", "What", "Files"], stretch=1)
        box.add(self.backups, 1)
        lay.addWidget(box, 2)
        self._looked_up = set()

    # --- profiles ---------------------------------------------------------
    def _extra_dirs(self):
        return json.loads(self.ctx.db.get_setting("settings_dirs", "[]"))

    def refresh_view(self):
        keep = self.profile.currentData() or self.ctx.db.get_setting("settings_profile")
        profiles = eve_settings.find_profiles() + eve_settings.find_profiles(self._extra_dirs())
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in dict.fromkeys(profiles):
            self.profile.addItem(str(p), str(p))
        if not profiles:
            self.profile.addItem("No EVE settings found automatically. Use Browse… to pick the folder", None)
        idx = self.profile.findData(keep)
        self.profile.setCurrentIndex(max(idx, 0))
        self.profile.blockSignals(False)
        self._profile_changed()

    def _browse(self):
        start = self.profile.currentData() or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "Pick an EVE settings folder (e.g. settings_Default) or the "
                                                   "CCP/EVE folder", start)
        if not d:
            return
        if not eve_settings.find_profiles([d]):
            QMessageBox.warning(self, "No settings found", "No core_char_*.dat / core_user_*.dat files there.")
            return
        dirs = self._extra_dirs()
        if d not in dirs:
            dirs.append(d)
            self.ctx.db.set_setting("settings_dirs", json.dumps(dirs))
        self.ctx.db.set_setting("settings_profile", str(eve_settings.find_profiles([d])[0]))
        self.profile.setCurrentIndex(-1)
        self.refresh_view()

    def _profile_changed(self):
        profile = self.profile.currentData()
        if profile:
            self.ctx.db.set_setting("settings_profile", profile)
        self.rescan()

    def _backup_root(self):
        profile = self.profile.currentData()
        root = eve_settings.backup_root_for(profile, paths.backups_dir()) if profile else paths.backups_dir()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def rescan(self):
        profile = self.profile.currentData()
        running = eve_settings.eve_running()
        self.warning.setText("⚠ EVE appears to be running. Close all clients before copying settings."
                             if running else "")
        if not profile:
            self.chars.fill([], str)
            self.users.fill([], str)
            self.backups.setRowCount(0)
            return
        chars, users = eve_settings.scan(profile)
        have = {f.owner_id for f in chars}
        registered = {c["character_id"]: c["name"] for c in self.ctx.characters()}
        chars += [eve_settings.char_file(profile, cid) for cid in registered if cid not in have]
        names = self.ctx.view.names([f.owner_id for f in chars])
        unknown = {f.owner_id for f in chars} - set(names) - self._looked_up
        if unknown:
            self._looked_up |= unknown
            self.ctx.run(lambda p: self.ctx.syncer.resolve(unknown), lambda _: self.rescan(), on_error=lambda e: None)
        self.chars.fill(chars, lambda f: registered.get(f.owner_id) or names.get(f.owner_id) or "(unknown)")
        char_names = {f.owner_id: registered.get(f.owner_id) or names.get(f.owner_id) or str(f.owner_id)
                      for f in chars}
        guesses = eve_settings.match_accounts(chars, users)
        self.users.fill(users, lambda f: self.ctx.db.get_setting(f"account_label:{f.owner_id}") or "(unnamed)",
                        lambda f: ", ".join(char_names[c] for c in guesses.get(f.owner_id, [])))
        backups = eve_settings.list_backups(self._backup_root())
        fill_table(self.backups, [[SortItem(fmt_time(b["created"], True), b["created"], data=str(b["path"])),
                                   b["label"], ", ".join(f["name"] for f in b["files"]) or "(none)"]
                                  for b in backups])
        self.backups.sortItems(0, Qt.SortOrder.DescendingOrder)

    # --- actions ----------------------------------------------------------
    def _confirm_closed(self):
        if eve_settings.eve_running():
            return QMessageBox.warning(
                self, "EVE is running",
                "An EVE client seems to be running. It will overwrite these files when it exits.\n\n"
                "Close all clients first. Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        return True

    def copy(self, table: FileTable):
        source = table.selected()
        if source is None or not source.exists:
            QMessageBox.information(self, "Copy settings", "Select the row to copy FROM first (it must have a file).")
            return
        targets = [t for t in table.ticked() if t.path != source.path]
        if not targets:
            QMessageBox.information(self, "Copy settings", "Tick at least one other row to copy TO.")
            return
        src_name = table.table.item(table.table.currentRow(), 1).text()
        if QMessageBox.question(self, "Copy settings",
                                f"Copy {source.path.name} ({src_name}) over {len(targets)} file(s)?\n\n"
                                + "\n".join(t.path.name for t in targets[:15])
                                + ("\n…" if len(targets) > 15 else "")
                                + "\n\nThe current files are backed up first.") != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_closed():
            return
        try:
            backup = eve_settings.copy_settings(source.path, [t.path for t in targets], self._backup_root())
        except Exception as e:
            QMessageBox.warning(self, "Copy failed", str(e))
            return
        self.ctx.log(f"Copied {source.path.name} to {len(targets)} file(s); backup in {backup}")
        self.rescan()
        QMessageBox.information(self, "Done", f"Settings copied to {len(targets)} file(s).")

    def _restore(self):
        row = self.backups.currentRow()
        if row < 0:
            return
        path = self.backups.item(row, 0).data(USER_ROLE)
        if QMessageBox.question(self, "Restore backup", f"Restore the files from this backup?\n{path}\n\n"
                                "The current files are backed up first.") != QMessageBox.StandardButton.Yes:
            return
        if not self._confirm_closed():
            return
        restored = eve_settings.restore(path, self._backup_root())
        self.ctx.log(f"Restored {len(restored)} settings file(s) from {path}")
        self.rescan()
