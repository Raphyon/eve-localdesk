"""AdminLTE-style look (the admin theme SeAT is built on): dark sidebar, light content, cards."""
from PySide6.QtGui import QColor, QIcon, QPalette

ACCENT = {"primary": "#007bff", "success": "#28a745", "info": "#17a2b8", "warning": "#ffc107",
          "danger": "#dc3545", "secondary": "#6c757d", "orange": "#fd7e14", "purple": "#6f42c1"}

TOKENS = {
    "light": dict(bg="#f4f6f9", card="#ffffff", text="#212529", muted="#6c757d", border="#dee2e6",
                  stripe="#f8f9fa", hover="#f1f3f5", select="#e3effd", input="#ffffff", input_border="#ced4da",
                  btn="#f8f9fa", btn_border="#ced4da", btn_hover="#e2e6ea", header="#ffffff", scroll="#c1c7cd",
                  sidebar="#343a40", sidebar_border="#4b545c", sidebar_text="#c2c7d0", focus="#80bdff"),
    "dark": dict(bg="#1e2227", card="#2a2f35", text="#e9ecef", muted="#9aa3ab", border="#3c434a",
                 stripe="#2f353b", hover="#353c43", select="#24466d", input="#1e2227", input_border="#4a525a",
                 btn="#353b42", btn_border="#4a525a", btn_hover="#414951", header="#2a2f35", scroll="#4f5861",
                 sidebar="#16191c", sidebar_border="#2c3237", sidebar_text="#c2c7d0", focus="#3f8cff"),
}


def icon(name, color="#c2c7d0"):
    try:
        import qtawesome
        return qtawesome.icon(name, color=color)
    except Exception:  # icon pack not installed: the app still works, just without icons
        return QIcon()


def stylesheet(t):
    a = ACCENT
    return f"""
    QWidget {{ font-size: 13px; color: {t['text']}; }}
    QMainWindow, QDialog, #content {{ background: {t['bg']}; }}
    #sidebar {{ background: {t['sidebar']}; }}
    #brand {{ border-bottom: 1px solid {t['sidebar_border']}; }}
    #brandText {{ color: #ffffff; font-size: 18px; font-weight: 300; }}
    #userPanel {{ border-bottom: 1px solid {t['sidebar_border']}; }}
    #userName {{ color: {t['sidebar_text']}; font-size: 14px; }}
    #version {{ color: #6c757d; font-size: 11px; }}
    #nav {{ background: transparent; border: none; color: {t['sidebar_text']}; outline: 0; }}
    #nav::item {{ padding: 8px 10px; margin: 1px 8px; border-radius: 4px; }}
    #nav::item:hover {{ background: rgba(255,255,255,0.08); color: #ffffff; }}
    #nav::item:selected {{ background: {a['primary']}; color: #ffffff; }}
    #header {{ background: {t['header']}; border-bottom: 1px solid {t['border']}; }}
    #pageTitle {{ font-size: 22px; font-weight: 400; }}
    #breadcrumb, #muted {{ color: {t['muted']}; }}
    #card {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 4px; }}
    #card[accent="primary"] {{ border-top: 3px solid {a['primary']}; }}
    #card[accent="success"] {{ border-top: 3px solid {a['success']}; }}
    #card[accent="danger"] {{ border-top: 3px solid {a['danger']}; }}
    #card[accent="warning"] {{ border-top: 3px solid {a['warning']}; }}
    #card[accent="info"] {{ border-top: 3px solid {a['info']}; }}
    #cardHeader {{ border-bottom: 1px solid {t['border']}; }}
    #cardTitle {{ font-size: 15px; font-weight: 600; }}
    #infobox {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: 4px; }}
    #infoLabel {{ color: {t['muted']}; font-size: 11px; font-weight: 600; }}
    #infoValue {{ font-size: 19px; font-weight: 700; }}
    #profileName {{ font-size: 20px; font-weight: 600; }}
    #note {{ color: {t['muted']}; padding: 8px 14px; border-bottom: 1px solid {t['border']}; }}
    #warn {{ color: {a['danger']}; font-weight: 600; }}
    QPushButton {{ background: {t['btn']}; border: 1px solid {t['btn_border']}; border-radius: 4px;
                   padding: 6px 12px; }}
    QPushButton:hover {{ background: {t['btn_hover']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton[kind="primary"] {{ background: {a['primary']}; border-color: {a['primary']}; color: #fff; }}
    QPushButton[kind="primary"]:hover {{ background: #0069d9; }}
    QPushButton[kind="success"] {{ background: {a['success']}; border-color: {a['success']}; color: #fff; }}
    QPushButton[kind="danger"] {{ background: {a['danger']}; border-color: {a['danger']}; color: #fff; }}
    QPushButton[kind="primary"]:disabled, QPushButton[kind="success"]:disabled {{
        background: {t['btn']}; border-color: {t['btn_border']}; color: {t['muted']}; }}
    QPushButton[kind="flat"] {{ background: transparent; border: none; padding: 6px; }}
    QPushButton[kind="flat"]:hover {{ background: {t['btn_hover']}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser {{
        background: {t['input']}; border: 1px solid {t['input_border']}; border-radius: 4px; padding: 5px 8px;
        selection-background-color: {a['primary']}; selection-color: #fff; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border-color: {t['focus']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {t['border']};
        selection-background-color: {a['primary']}; selection-color: #fff; outline: 0; }}
    QTableView, QTreeView, QListView {{ background: {t['card']}; alternate-background-color: {t['stripe']};
        border: none; gridline-color: {t['border']}; outline: 0;
        selection-background-color: {t['select']}; selection-color: {t['text']}; }}
    QTableView::item {{ padding: 2px 8px; border-bottom: 1px solid {t['border']}; }}
    QTreeView::item {{ padding: 4px 2px; }}
    QTableView::item:hover, QTreeView::item:hover {{ background: {t['hover']}; }}
    QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
        background: {t['select']}; color: {t['text']}; }}
    QHeaderView {{ background: {t['card']}; border: none; }}
    QHeaderView::section {{ background: {t['card']}; color: {t['text']}; font-weight: 600; border: none;
        border-bottom: 2px solid {t['border']}; padding: 8px; }}
    QTableCornerButton::section {{ background: {t['card']}; border: none; }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {t['border']}; }}
    QTabBar::tab {{ background: transparent; padding: 7px 14px; margin: 8px 2px 8px 0; border: none;
        border-radius: 4px; color: {t['muted']}; }}
    QTabBar::tab:selected {{ background: {a['primary']}; color: #fff; }}
    QTabBar::tab:hover:!selected {{ color: {t['text']}; background: {t['hover']}; }}
    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle {{ background: {t['scroll']}; border-radius: 5px; min-height: 30px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QStatusBar {{ background: {t['header']}; border-top: 1px solid {t['border']}; color: {t['muted']}; }}
    QStatusBar QLabel {{ color: {t['muted']}; }}
    QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px; }}
    QAbstractItemView::indicator, QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px; border: 1px solid {t['input_border']}; background: {t['input']};
        border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QAbstractItemView::indicator:checked, QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {a['primary']}; border-color: {a['primary']}; }}
    QAbstractItemView::indicator:disabled, QCheckBox::indicator:disabled {{ background: {t['stripe']}; }}
    """


def apply(app, name="light"):
    t = TOKENS.get(name, TOKENS["light"])
    app.setStyle("Fusion")
    p = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "card"), (QPalette.ColorRole.AlternateBase, "stripe"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "btn"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.ToolTipBase, "card"),
                      (QPalette.ColorRole.ToolTipText, "text"), (QPalette.ColorRole.PlaceholderText, "muted")):
        p.setColor(role, QColor(t[key]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT["primary"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor(ACCENT["primary"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t["muted"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t["muted"]))
    app.setPalette(p)
    app.setStyleSheet(stylesheet(t))
