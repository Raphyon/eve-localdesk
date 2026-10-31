"""Where the EVE SSO Client ID comes from, in order of priority:
1. esi-credentials.json next to the app or in the data folder: {"ClientID": "...", "CallbackPort": 8642}
2. the value saved from the Setup dialog.
Only a Client ID is needed; with PKCE there is no secret key."""
import json
import sys
from pathlib import Path

from . import paths

FILE_NAME = "esi-credentials.json"


def _file_values():
    app_dir = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
    for folder in (Path(__file__).resolve().parent.parent, app_dir, paths.data_dir()):
        f = folder / FILE_NAME
        if f.is_file():
            try:
                return json.loads(f.read_text())
            except ValueError:
                continue
    return {}


def client_id(db):
    from_file = (_file_values().get("ClientID") or "").strip()
    if from_file and from_file != "your-client-id-here":
        return from_file
    return db.get_setting("client_id")


def callback_port(db, default):
    return int(_file_values().get("CallbackPort") or db.get_setting("callback_port", str(default)))
