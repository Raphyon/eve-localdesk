from localdesk import updates


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class Http:
    def __init__(self, resp=None, error=None):
        self.resp, self.error, self.calls = resp, error, []

    def get(self, url, **kw):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.resp


RELEASE = {"tag_name": "v0.4.0", "html_url": "https://github.com/Raphyon/eve-localdesk/releases/tag/v0.4.0",
           "body": "notes", "assets": [{"name": "EVE-LocalDesk-0.4.0-Setup.exe", "browser_download_url": "https://x/s.exe"},
                                       {"name": "EVE-LocalDesk-0.4.0-macos.zip", "browser_download_url": "https://x/m.zip"}]}


def test_versions():
    assert updates.parse_version("v0.10.0") > updates.parse_version("0.9.9")
    assert updates.parse_version("v1.2") == (1, 2, 0)
    assert updates.parse_version("junk") == (0, 0, 0)


def test_check(monkeypatch):
    monkeypatch.setattr(updates.sys, "platform", "win32")
    http = Http(Resp(200, RELEASE))
    info = updates.check(http, "0.3.0")
    assert info["version"] == "0.4.0" and info["download"] == "https://x/s.exe"
    assert http.calls == [updates.LATEST_API]
    assert updates.check(Http(Resp(200, RELEASE)), "0.4.0") is None        # up to date
    assert updates.check(Http(Resp(200, RELEASE)), "0.5.0") is None        # newer than the release
    assert updates.check(Http(Resp(404, {})), "0.3.0") is None             # no releases / repo gone
    assert updates.check(Http(error=OSError("offline")), "0.3.0") is None  # offline never errors
