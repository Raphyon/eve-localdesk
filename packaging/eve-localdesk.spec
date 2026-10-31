# PyInstaller build spec:  pyinstaller --noconfirm packaging/eve-localdesk.spec
# Produces dist/EVE LocalDesk/ (Windows), dist/EVE LocalDesk.app (macOS) or dist/eve-localdesk/ (Linux).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from localdesk import __version__  # noqa: E402

NAME = "eve-localdesk" if sys.platform.startswith("linux") else "EVE LocalDesk"
ICON = {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform)

datas = collect_data_files("qtawesome") + [
    (str(ROOT / "localdesk" / "assets"), "localdesk/assets"),
    (str(ROOT / "esi-credentials.example.json"), "."),
]
# Qt modules the app doesn't use (keeps the download smaller). QtWebEngine and what it needs stay in.
excludes = ["tkinter", "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
            "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtBluetooth", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
            "PySide6.QtNfc", "PySide6.QtQuick3D", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
            "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql",
            "PySide6.QtStateMachine", "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtDesigner",
            "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtHttpServer", "PySide6.QtPdfWidgets"]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore"],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ROOT / "packaging" / ICON) if ICON else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EVE LocalDesk.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="io.github.eve-localdesk",
        info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                    "NSHighResolutionCapable": True},
    )
