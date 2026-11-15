"""Multi-select, Ctrl+A, Ctrl+C and Space-to-tick on tables, lists and trees (offscreen Qt)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QMenu, QTableWidgetItem  # noqa: E402

from localdesk.ui.widgets import (SortItem, TreeItem, fill_table, install_selection_tools,  # noqa: E402
                                  make_table)

app = QApplication.instance() or QApplication([])


def ctrl(view, key):
    QTest.keyClick(view, key, Qt.KeyboardModifier.ControlModifier)


def test_table_select_all_and_copy():
    t = make_table(["System", "Jumps", "Hidden"])
    fill_table(t, [["Jita", SortItem("3", 3)], ["Amarr", SortItem("9", 9)], ["Dodixie", SortItem("5", 5)]])
    t.setColumnHidden(2, True)
    t.show()
    ctrl(t, Qt.Key.Key_A)
    assert len({i.row() for i in t.selectedIndexes()}) == 3
    ctrl(t, Qt.Key.Key_C)
    lines = QApplication.clipboard().text().splitlines()
    assert lines[0] == "System\tJumps"                 # header row, hidden column left out
    assert sorted(lines[1:]) == sorted(["Jita\t3", "Amarr\t9", "Dodixie\t5"])
    t.clearSelection()
    t.selectRow(1)
    ctrl(t, Qt.Key.Key_C)
    assert QApplication.clipboard().text().count("\n") == 0   # one row: no header


def test_space_ticks_selected_rows():
    t = make_table(["Copy to", "Character"])
    rows = []
    for name in ("A", "B", "C"):
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        check.setCheckState(Qt.CheckState.Unchecked)
        rows.append([check, name])
    fill_table(t, rows)
    t.show()
    ctrl(t, Qt.Key.Key_A)
    QTest.keyClick(t, Qt.Key.Key_Space)
    assert all(t.item(r, 0).checkState() == Qt.CheckState.Checked for r in range(3))
    QTest.keyClick(t, Qt.Key.Key_Space)               # all ticked: Space unticks them
    assert all(t.item(r, 0).checkState() == Qt.CheckState.Unchecked for r in range(3))


def test_list_multi_select_and_ticks():
    lst = QListWidget()
    install_selection_tools(lst)
    for name in ("Alt 1", "Alt 2", "Alt 3"):
        item = QListWidgetItem(name, lst)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
    lst.show()
    ctrl(lst, Qt.Key.Key_A)
    assert len(lst.selectedItems()) == 3
    QTest.keyClick(lst, Qt.Key.Key_Space)
    assert all(lst.item(i).checkState() == Qt.CheckState.Checked for i in range(3))


def test_tree_copy_and_menu_hook():
    from PySide6.QtWidgets import QTreeWidget
    tree = QTreeWidget()
    tree.setHeaderLabels(["Item", "Qty"])
    top = TreeItem(["Jita IV - Moon 4", "2"])
    top.addChild(TreeItem(["Rifter", "1"]))
    tree.addTopLevelItem(top)
    calls = []
    install_selection_tools(tree, menu_hook=lambda menu, pos: calls.append(menu.addAction("Name this location…")))
    tree.expandAll()
    tree.show()
    ctrl(tree, Qt.Key.Key_A)
    ctrl(tree, Qt.Key.Key_C)
    assert QApplication.clipboard().text().splitlines() == ["Item\tQty", "Jita IV - Moon 4\t2", "Rifter\t1"]
    menu = QMenu()
    tree.menu_hook(menu, None)
    assert calls and calls[0].text() == "Name this location…"
