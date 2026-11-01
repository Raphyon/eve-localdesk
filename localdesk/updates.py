"""Startup update check: one request to GitHub's public releases API when the app starts."""
import re
import sys

REPO = "Raphyon/eve-localdesk"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def parse_version(text):
    """'v0.3.0' -> (0, 3, 0); anything unparsable sorts lowest."""
    parts = re.findall(r"\d+", text or "")[:3]
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts)) if parts else (0, 0, 0)


def check(http, current_version, timeout=10):
    """Return {version, page, download, notes} if GitHub has a newer release than current_version, else
    None. Network problems also return None: an update check must never get in the way."""
    try:
        r = http.get(LATEST_API, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    latest = data.get("tag_name", "")
    if parse_version(latest) <= parse_version(current_version):
        return None
    suffix = {"win32": "-Setup.exe", "darwin": "-macos.zip"}.get(sys.platform, "-linux-x86_64.tar.gz")
    download = next((a.get("browser_download_url") for a in data.get("assets", [])
                     if a.get("name", "").endswith(suffix)), None)
    return {"version": latest.lstrip("v"), "page": data.get("html_url") or RELEASES_PAGE,
            "download": download, "notes": (data.get("body") or "").strip()}
