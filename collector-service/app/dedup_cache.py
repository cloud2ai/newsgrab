"""SQLite-backed dedup cache, keyed primarily by resolved real URL.

A secondary raw_link_index lets repeated encounters of the SAME raw
Google News redirect link skip calling playwright-service entirely
(Google's redirect links are stable per-article), while expiry is
governed solely by `articles.cached_at` -- raw_link_index has no TTL of
its own, it only joins through `articles` at read time.

Each method opens and closes its own connection rather than holding one
open for the object's lifetime, since this is called from async request
handlers via a synchronous sqlite3 API -- keeping connections short-lived
avoids any cross-request connection-sharing concerns.
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import DEDUP_CACHE_TTL_SECONDS


class DedupCache:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS articles ("
                "real_url TEXT PRIMARY KEY, article_json TEXT NOT NULL, cached_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS raw_link_index ("
                "raw_link TEXT PRIMARY KEY, real_url TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def get_by_raw_link(self, raw_link: str) -> Optional[Dict[str, Any]]:
        cutoff = time.time() - DEDUP_CACHE_TTL_SECONDS
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT a.article_json FROM raw_link_index r "
                "JOIN articles a ON r.real_url = a.real_url "
                "WHERE r.raw_link = ? AND a.cached_at > ?",
                (raw_link, cutoff),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def get_by_real_url(self, real_url: str) -> Optional[Dict[str, Any]]:
        cutoff = time.time() - DEDUP_CACHE_TTL_SECONDS
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT article_json FROM articles WHERE real_url = ? AND cached_at > ?",
                (real_url, cutoff),
            ).fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def remember(self, real_url: str, raw_link: str, article: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO articles (real_url, article_json, cached_at) VALUES (?, ?, ?)",
                (real_url, json.dumps(article), time.time()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO raw_link_index (raw_link, real_url) VALUES (?, ?)",
                (raw_link, real_url),
            )
            conn.commit()
        finally:
            conn.close()

    def link_raw_to_real(self, raw_link: str, real_url: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO raw_link_index (raw_link, real_url) VALUES (?, ?)",
                (raw_link, real_url),
            )
            conn.commit()
        finally:
            conn.close()
