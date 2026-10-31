"""Find, copy, back up and restore EVE client settings files.

core_char_<characterID>.dat holds per-character UI state: window layout, overview setup,
chat channels and so on. core_user_<userID>.dat holds account-wide settings like shortcuts and
some window/audio options. Copying one file over another copies those settings.
The EVE client must be closed while copying; it rewrites these files on exit.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CHAR_RE = re.compile(r"^core_char_(\d+)\.dat$")
USER_RE = re.compile(r"^core_user_(\d+)\.dat$")


@dataclass
class SettingsFile:
    path: Path
    kind: str          # "char" or "user"
    owner_id: int
    mtime: float = None  # None means the file doesn't exist yet (a copy target only)
    size: int = 0

    @property
    def exists(self):
        return self.mtime is not None


def candidate_roots():
    """Usual places for the CCP/EVE folder, including Wine/Proton prefixes on Linux."""
    home = Path.home()
    roots = []
    if sys.platform == "win32":
        if os.environ.get("LOCALAPPDATA"):
            roots.append(Path(os.environ["LOCALAPPDATA"]) / "CCP" / "EVE")
    elif sys.platform == "darwin":
        roots += [home / "Library/Application Support/CCP/EVE",
                  home / "Library/Application Support/EVE Online/p_drive/User/Application Data/CCP/EVE"]
    else:
        tail = Path("AppData/Local/CCP/EVE")
        for steam in (home / ".steam/steam", home / ".local/share/Steam",
                      home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
            roots.append(steam / "steamapps/compatdata/8500/pfx/drive_c/users/steamuser" / tail)
        user = os.environ.get("USER", "")
        for prefix in (home / ".wine", home / "Games/eve-online", home / "Games/eve-online-launcher"):
            roots.append(prefix / "drive_c/users" / user / tail)
    return [r for r in roots if r.is_dir()]


def is_profile(path):
    path = Path(path)
    return path.is_dir() and any(CHAR_RE.match(p.name) or USER_RE.match(p.name) for p in path.iterdir())


def find_profiles(roots=None):
    """Settings profile folders (…/<install>_tranquility/settings_<Name>) under the given roots."""
    found = []
    for root in (candidate_roots() if roots is None else [Path(r) for r in roots]):
        if not root.is_dir():
            continue
        if is_profile(root):
            found.append(root)
            continue
        found += [p for p in sorted(root.glob("settings_*")) if p.is_dir()]
        found += [p for p in sorted(root.glob("*/settings_*")) if p.is_dir()]
    return list(dict.fromkeys(found))


def scan(profile):
    chars, users = [], []
    for p in sorted(Path(profile).iterdir()):
        for regex, kind, out in ((CHAR_RE, "char", chars), (USER_RE, "user", users)):
            m = regex.match(p.name)
            if m and p.is_file():
                st = p.stat()
                out.append(SettingsFile(p, kind, int(m.group(1)), st.st_mtime, st.st_size))
    return chars, users


def match_accounts(chars, users, window=120):
    """Guess which characters belong to which account file.

    The client writes a character's core_char file and its account's core_user file together when it
    saves settings (on logout or exit), so each character file is matched to the account file saved
    closest in time, within `window` seconds. Returns {user_id: [character_id, ...]}."""
    out = {u.owner_id: [] for u in users}
    existing_users = [u for u in users if u.exists]
    for c in chars:
        if not c.exists or not existing_users:
            continue
        best = min(existing_users, key=lambda u: abs(u.mtime - c.mtime))
        if abs(best.mtime - c.mtime) <= window:
            out[best.owner_id].append(c.owner_id)
    return out


def char_file(profile, character_id):
    """A (possibly not yet existing) settings file for a character in this profile."""
    path = Path(profile) / f"core_char_{character_id}.dat"
    if path.exists():
        st = path.stat()
        return SettingsFile(path, "char", character_id, st.st_mtime, st.st_size)
    return SettingsFile(path, "char", character_id)


def eve_running():
    """True/False if we could tell, None if we couldn't."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq exefile.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10, creationflags=0x08000000).stdout
            return "exefile.exe" in out.lower()
        if Path("/proc").is_dir():
            for p in Path("/proc").iterdir():
                if p.name.isdigit():
                    try:
                        if b"exefile.exe" in (p / "cmdline").read_bytes():
                            return True
                    except OSError:
                        pass
            return False
        return subprocess.run(["pgrep", "-f", "exefile"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return None


def backup_root_for(profile, base):
    profile = Path(profile)
    digest = hashlib.sha1(str(profile.resolve()).encode()).hexdigest()[:8]
    return Path(base) / f"{profile.parent.name}-{profile.name}-{digest}"


def backup(files, backup_root, label):
    files = [Path(f) for f in files if Path(f).exists()]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(backup_root) / stamp
    n = 1
    while dest.exists():
        n += 1
        dest = Path(backup_root) / f"{stamp}-{n}"
    dest.mkdir(parents=True)
    manifest = {"created": time.time(), "label": label, "files": []}
    for f in files:
        shutil.copy2(f, dest / f.name)
        manifest["files"].append({"name": f.name, "original": str(f)})
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return dest


def copy_settings(source, targets, backup_root):
    """Back up the targets, then overwrite them with `source`. Returns the backup folder."""
    source = Path(source)
    targets = [Path(t) for t in targets if Path(t).resolve() != source.resolve()]
    if not targets:
        raise ValueError("Nothing to copy to")
    if not source.is_file():
        raise ValueError(f"{source} does not exist")
    dest = backup(targets, backup_root, f"Before copying {source.name} to {len(targets)} file(s)")
    for t in targets:
        # copyfile, not copy2: keep a fresh modification time so the "newest file" trick still works
        shutil.copyfile(source, t)
    return dest


def list_backups(backup_root):
    out = []
    root = Path(backup_root)
    if not root.is_dir():
        return out
    for d in root.iterdir():
        manifest = d / "manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
            except ValueError:
                continue
            out.append({"path": d, **data})
    return sorted(out, key=lambda b: b["created"], reverse=True)


def restore(backup_dir, backup_root):
    """Put the backed-up files back where they came from (backing up the current ones first)."""
    data = json.loads((Path(backup_dir) / "manifest.json").read_text())
    originals = [Path(f["original"]) for f in data["files"]]
    backup(originals, backup_root, f"Before restoring backup {Path(backup_dir).name}")
    for f, original in zip(data["files"], originals):
        shutil.copyfile(Path(backup_dir) / f["name"], original)
    return originals
