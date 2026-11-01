"""EVE SSO login for a desktop app: OAuth2 authorization code flow with PKCE, so no client secret
is needed. A throwaway listener on localhost catches the redirect, then shuts down."""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
DEFAULT_PORT = 8642

# Every scope here must also be ticked on your application at developers.eveonline.com.
SCOPES = [
    "publicData",
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-assets.read_assets.v1",
    "esi-location.read_location.v1",
    "esi-location.read_ship_type.v1",
    "esi-location.read_online.v1",
    "esi-mail.read_mail.v1",
    "esi-mail.send_mail.v1",
    "esi-mail.organize_mail.v1",
    "esi-characters.read_contacts.v1",
    "esi-characters.write_contacts.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-clones.read_clones.v1",
    "esi-clones.read_implants.v1",
    "esi-universe.read_structures.v1",
    "esi-ui.write_waypoint.v1",
    "esi-search.search_structures.v1",
    "esi-corporations.read_divisions.v1",
]
# plus everything the data browser modules need (contracts, industry, killmails, corp data, ...)
from .datasets import SCOPES as _DATASET_SCOPES  # noqa: E402

SCOPES += [s for s in _DATASET_SCOPES if s not in SCOPES]

_PAGE = """<html><body style="font-family:sans-serif;background:#f4f6f9;color:#212529;padding:40px">
<h2>{title}</h2><p>{text}</p>{script}</body></html>"""
# Browsers only let a page close a tab they consider script-closable, so this works in some
# browsers/setups and is harmless in the rest (the message stays visible).
_AUTO_CLOSE = "<script>setTimeout(function () { window.open('', '_self'); window.close(); }, 600);</script>"


class SSOError(Exception):
    pass


def callback_url(port=DEFAULT_PORT):
    return f"http://localhost:{port}/callback"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def decode_jwt(token: str) -> dict:
    """Read the claims of the access token. It arrives straight from CCP over TLS, so the
    signature isn't checked here."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def character_from_token(access_token: str) -> dict:
    claims = decode_jwt(access_token)
    scopes = claims.get("scp", [])
    if isinstance(scopes, str):
        scopes = [scopes]
    return {
        "character_id": int(claims["sub"].split(":")[-1]),
        "name": claims.get("name", ""),
        "owner_hash": claims.get("owner"),
        "scopes": scopes,
    }


def _token_request(data: dict) -> dict:
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=30, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "login.eveonline.com",
        })
    except requests.RequestException as e:
        raise SSOError(f"Could not reach EVE SSO: {e}") from e
    if r.status_code != 200:
        raise SSOError(f"EVE SSO token request failed ({r.status_code}): {r.text[:300]}")
    return r.json()


def refresh(client_id: str, refresh_token: str) -> dict:
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id})


@dataclass
class LoginRequest:
    url: str
    state: str
    verifier: str
    redirect_uri: str


def prepare_login(client_id, port=DEFAULT_PORT, scopes=SCOPES) -> LoginRequest:
    """Build the EVE SSO authorize URL with a fresh PKCE verifier and state."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    redirect_uri = callback_url(port)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return LoginRequest(url, state, verifier, redirect_uri)


def callback_params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def finish_login(client_id, request: LoginRequest, params: dict):
    """Check the callback parameters and exchange the code for tokens. Returns (tokens, character_info)."""
    if params.get("state") != request.state:
        raise SSOError("Login response had the wrong state value; please try again")
    if "code" not in params:
        raise SSOError(f"Login failed: {params.get('error_description') or params.get('error') or 'no code returned'}")
    tokens = _token_request({
        "grant_type": "authorization_code",
        "code": params["code"],
        "client_id": client_id,
        "code_verifier": request.verifier,
    })
    return tokens, character_from_token(tokens["access_token"])


def login(client_id, port=DEFAULT_PORT, scopes=SCOPES, timeout=300, cancel: threading.Event = None,
          open_browser=webbrowser.open):
    """Log in through the system web browser, catching the redirect on a throwaway localhost listener.
    Returns (tokens, character_info)."""
    request = prepare_login(client_id, port, scopes)
    result, done = {}, threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update(callback_params(self.path))
            ok = result.get("state") == request.state and "code" in result
            page = _PAGE.format(
                title="Login complete" if ok else "Login failed",
                text="This tab will try to close itself. If it doesn't, close it and go back to EVE LocalDesk."
                if ok else "Something went wrong. Go back to EVE LocalDesk and try again.",
                script=_AUTO_CLOSE if ok else "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
            done.set()

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise SSOError(f"Could not listen on port {port} for the login callback: {e}") from e
    server.timeout = 0.5

    open_browser(request.url)
    deadline = time.time() + timeout
    try:
        while not done.is_set():
            if cancel is not None and cancel.is_set():
                raise SSOError("Login cancelled")
            if time.time() > deadline:
                raise SSOError("Timed out waiting for the EVE login to finish in the browser")
            server.handle_request()
    finally:
        server.server_close()
    return finish_login(client_id, request, result)
