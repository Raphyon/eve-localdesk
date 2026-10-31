"""Small ESI client: token refresh, retries, error-limit backoff, pagination and cache expiry."""
import email.utils
import threading
import time

import requests

from . import __version__, credentials, sso

DEFAULT_BASE = "https://esi.evetech.net/latest"
USER_AGENT = f"EVE-LocalDesk/{__version__} (local desktop app; python-requests)"


class ESIError(Exception):
    def __init__(self, status, message, path=""):
        super().__init__(f"{status} {message}" + (f" ({path})" if path else ""))
        self.status = status
        self.message = message
        self.path = path


class ESI:
    def __init__(self, db, base=DEFAULT_BASE):
        self.db = db
        self.base = base.rstrip("/")
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        self._locks = {}
        self._locks_guard = threading.Lock()
        self._pause_until = 0.0

    def _char_lock(self, character_id):
        with self._locks_guard:
            return self._locks.setdefault(character_id, threading.Lock())

    def access_token(self, character_id):
        """A valid access token for the character, refreshing it if needed (refresh tokens rotate)."""
        with self._char_lock(character_id):
            ch = self.db.get_character(character_id)
            if ch is None:
                raise ESIError(0, f"character {character_id} is not registered")
            if ch["access_token"] and ch["access_expires"] > time.time() + 60:
                return ch["access_token"]
            client_id = credentials.client_id(self.db)
            if not client_id:
                raise ESIError(0, "no EVE SSO client ID configured (open Setup)")
            try:
                tokens = sso.refresh(client_id, ch["refresh_token"])
            except sso.SSOError as e:
                raise ESIError(401, f"token refresh failed - re-add the character ({e})") from e
            self.db.update_tokens(character_id, tokens["access_token"],
                                  tokens.get("refresh_token", ch["refresh_token"]),
                                  time.time() + tokens.get("expires_in", 1199))
            return tokens["access_token"]

    def request(self, method, path, character_id=None, params=None, json=None):
        url = self.base + path
        for attempt in range(4):
            wait = self._pause_until - time.time()
            if wait > 0:
                time.sleep(wait)
            headers = {}
            if character_id:
                headers["Authorization"] = "Bearer " + self.access_token(character_id)
            try:
                r = self.http.request(method, url, params=params, json=json, headers=headers, timeout=30)
            except requests.RequestException as e:
                if attempt == 3:
                    raise ESIError(0, f"network error: {e}", path) from e
                time.sleep(2 ** attempt)
                continue

            remain = r.headers.get("X-ESI-Error-Limit-Remain")
            reset = int(r.headers.get("X-ESI-Error-Limit-Reset", "60") or 60)
            if remain is not None and int(remain) < 20:
                self._pause_until = time.time() + reset
            if r.status_code == 420:
                self._pause_until = time.time() + reset
                continue
            if r.status_code == 429:
                self._pause_until = time.time() + int(r.headers.get("Retry-After", "10") or 10)
                continue
            if r.status_code in (502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("error", r.text)
                except ValueError:
                    msg = r.text
                raise ESIError(r.status_code, str(msg)[:300], path)
            return r
        raise ESIError(0, "gave up after repeated retries", path)

    @staticmethod
    def expires(response):
        header = response.headers.get("Expires")
        if header:
            try:
                return email.utils.parsedate_to_datetime(header).timestamp()
            except (TypeError, ValueError):
                pass
        return time.time() + 300

    def get(self, path, character_id=None, params=None):
        r = self.request("GET", path, character_id, params)
        return (r.json() if r.content else None), self.expires(r)

    def get_paged(self, path, character_id=None, params=None):
        params = dict(params or {}, page=1)
        r = self.request("GET", path, character_id, params)
        data, expires = r.json(), self.expires(r)
        for page in range(2, int(r.headers.get("X-Pages", 1)) + 1):
            params["page"] = page
            data.extend(self.request("GET", path, character_id, params).json())
        return data, expires
