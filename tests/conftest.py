import base64
import json
import time

import pytest

from localdesk.db import Database
from localdesk.esi import ESIError
from localdesk.sync import S_ASSETS, S_CONTACTS, S_WRITE_CONTACTS, S_MAIL


def make_jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


class FakeResponse:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {}
        self.content = b"x" if data is not None else b""

    def json(self):
        return self._data


class FakeESI:
    """Answers ESI paths from a dict; records write calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def access_token(self, cid):
        return "token"

    def request(self, method, path, character_id=None, params=None, json=None):
        self.calls.append((method, path, character_id, params, json))
        handler = self.routes.get((method, path))
        if handler is None:
            raise ESIError(404, "not found", path)
        data = handler(params, json) if callable(handler) else handler
        return FakeResponse(data)

    def get(self, path, character_id=None, params=None):
        return self.request("GET", path, character_id, params).json(), time.time() + 300

    def get_paged(self, path, character_id=None, params=None):
        return self.get(path, character_id, params)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.sqlite3")


def add_char(db, cid, name, scopes=(S_ASSETS, S_CONTACTS, S_WRITE_CONTACTS, S_MAIL)):
    db.upsert_character({"character_id": cid, "name": name, "scopes": list(scopes)},
                        {"access_token": "a", "refresh_token": "r", "expires_in": 1200})
