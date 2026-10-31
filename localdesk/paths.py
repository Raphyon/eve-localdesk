"""Where the app keeps its data on this computer."""
import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("LOCALDESK_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "eve-localdesk"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "eve-localdesk"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "eve-localdesk"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sub(name: str) -> Path:
    d = data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "localdesk.sqlite3"


def backups_dir() -> Path:
    return _sub("settings-backups")


def portraits_dir() -> Path:
    return _sub("portraits")
