"""SQLite storage: app settings, characters and their tokens, cached ESI data and resolved names."""
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS characters (
    character_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner_hash TEXT,
    scopes TEXT NOT NULL DEFAULT '[]',
    refresh_token TEXT NOT NULL,
    access_token TEXT,
    access_expires REAL NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    last_sync REAL
);
CREATE TABLE IF NOT EXISTS cache (
    character_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (character_id, key)
);
CREATE TABLE IF NOT EXISTS names (id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT);
"""


class Database:
    """Thread-safe wrapper around one SQLite connection. Character ID 0 is used for public data."""

    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _execute(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _executemany(self, sql, rows):
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # --- settings ---------------------------------------------------------
    def get_setting(self, key, default=None):
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        self._execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

    # --- characters -------------------------------------------------------
    @staticmethod
    def _char(row):
        if row:
            row["scopes"] = json.loads(row["scopes"])
        return row

    def list_characters(self):
        return [self._char(r) for r in self._all("SELECT * FROM characters ORDER BY name COLLATE NOCASE")]

    def get_character(self, character_id):
        return self._char(self._one("SELECT * FROM characters WHERE character_id = ?", (character_id,)))

    def upsert_character(self, info, tokens):
        self._execute(
            """INSERT INTO characters (character_id, name, owner_hash, scopes, refresh_token,
                                       access_token, access_expires, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(character_id) DO UPDATE SET
                   name = excluded.name, owner_hash = excluded.owner_hash, scopes = excluded.scopes,
                   refresh_token = excluded.refresh_token, access_token = excluded.access_token,
                   access_expires = excluded.access_expires""",
            (info["character_id"], info["name"], info.get("owner_hash"), json.dumps(info["scopes"]),
             tokens["refresh_token"], tokens["access_token"],
             time.time() + tokens.get("expires_in", 1199), time.time()),
        )
        self.put_names([(info["character_id"], info["name"], "character")])

    def update_tokens(self, character_id, access_token, refresh_token, expires_at):
        self._execute(
            "UPDATE characters SET access_token = ?, refresh_token = ?, access_expires = ? WHERE character_id = ?",
            (access_token, refresh_token, expires_at, character_id),
        )

    def mark_synced(self, character_id):
        self._execute("UPDATE characters SET last_sync = ? WHERE character_id = ?", (time.time(), character_id))

    def remove_character(self, character_id):
        self._execute("DELETE FROM characters WHERE character_id = ?", (character_id,))
        self._execute("DELETE FROM cache WHERE character_id = ?", (character_id,))

    # --- cached ESI data --------------------------------------------------
    def cache_row(self, character_id, key):
        row = self._one("SELECT * FROM cache WHERE character_id = ? AND key = ?", (character_id, key))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def cache_get(self, character_id, key, default=None):
        row = self.cache_row(character_id, key)
        return row["data"] if row else default

    def cache_put(self, character_id, key, data, expires_at):
        self._execute(
            "INSERT OR REPLACE INTO cache (character_id, key, data, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (character_id, key, json.dumps(data), time.time(), expires_at),
        )

    # --- id -> name -------------------------------------------------------
    def get_name_entries(self, ids):
        ids = [int(i) for i in ids if i]
        out = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._all(f"SELECT id, name, category FROM names WHERE id IN ({marks})", chunk):
                out[r["id"]] = (r["name"], r["category"])
        return out

    def get_names(self, ids):
        return {k: v[0] for k, v in self.get_name_entries(ids).items()}

    def put_names(self, entries):
        self._executemany("INSERT OR REPLACE INTO names (id, name, category) VALUES (?, ?, ?)", list(entries))
