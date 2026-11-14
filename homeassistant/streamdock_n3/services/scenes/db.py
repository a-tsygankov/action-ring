"""scenes: SQLite cache of films, per-website lookup outcomes and scenes."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading

from .model import Film, Scene, SourceResult

SCHEMA_VERSION = 1
RUNTIME_TOLERANCE_S = 180
_KEEP_SCENES = ("blocked", "error")  # temporary failures must not wipe good data

_SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    norm_title TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    runtime_s INTEGER,
    director TEXT,
    cast_json TEXT NOT NULL DEFAULT '[]',
    tmdb_id INTEGER,
    imdb_id TEXT,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS films_norm ON films (norm_title);
CREATE TABLE IF NOT EXISTS lookups (
    film_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (film_id, source)
);
CREATE TABLE IF NOT EXISTS scenes (
    film_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    category TEXT NOT NULL,
    synced_with TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS scenes_film ON scenes (film_id);
"""

_FILM_COLS = "id, norm_title, title, year, runtime_s, director, cast_json, tmdb_id, imdb_id"


def _film_from_row(row) -> Film:
    _, norm, title, year, runtime_s, director, cast_json, tmdb_id, imdb_id = row
    return Film(title, norm, runtime_s, year, director, tuple(json.loads(cast_json or "[]")), tmdb_id, imdb_id)


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    def find_film(self, norm: str, runtime_s: int | None) -> tuple[int, Film] | None:
        with self._lock:
            return self._find_film(norm, runtime_s)

    def _find_film(self, norm, runtime_s):
        rows = self._conn.execute(
            f"SELECT {_FILM_COLS} FROM films WHERE norm_title = ? ORDER BY updated_at DESC, id DESC",
            (norm,),
        ).fetchall()
        if runtime_s is not None:
            def distance(row):
                return RUNTIME_TOLERANCE_S if row[4] is None else abs(row[4] - runtime_s)
            rows = [r for r in rows if distance(r) <= RUNTIME_TOLERANCE_S]
            rows.sort(key=distance)  # stable: ties keep the most recently updated first
        if not rows:
            return None
        return rows[0][0], _film_from_row(rows[0])

    def save_film(self, film: Film, now: float) -> int:
        with self._lock:
            found = self._find_film(film.norm, film.runtime_s)
            if found is None:
                cur = self._conn.execute(
                    "INSERT INTO films (norm_title, title, year, runtime_s, director, cast_json, tmdb_id, imdb_id,"
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (film.norm, film.title, film.year, film.runtime_s, film.director,
                     json.dumps(list(film.cast)), film.tmdb_id, film.imdb_id, now),
                )
                self._conn.commit()
                return cur.lastrowid
            film_id, old = found
            # Fill-in only: a missing value in the new film never erases a known one.
            merged = Film(**{
                f.name: getattr(film, f.name) or getattr(old, f.name) for f in dataclasses.fields(Film)
            })
            self._conn.execute(
                "UPDATE films SET title = ?, year = ?, runtime_s = ?, director = ?, cast_json = ?, tmdb_id = ?,"
                " imdb_id = ?, updated_at = ? WHERE id = ?",
                (merged.title, merged.year, merged.runtime_s, merged.director, json.dumps(list(merged.cast)),
                 merged.tmdb_id, merged.imdb_id, now, film_id),
            )
            self._conn.commit()
            return film_id

    def lookup_state(self, film_id: int) -> dict[str, tuple[str, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, status, fetched_at FROM lookups WHERE film_id = ?", (film_id,)
            ).fetchall()
        return {source: (status, fetched_at) for source, status, fetched_at in rows}

    def save_result(self, film_id: int, source: str, result: SourceResult, now: float) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO lookups (film_id, source, status, fetched_at) VALUES (?, ?, ?, ?)",
                    (film_id, source, result.status, now),
                )
                if result.status not in _KEEP_SCENES:
                    self._conn.execute("DELETE FROM scenes WHERE film_id = ? AND source = ?", (film_id, source))
                if result.status == "hit":
                    self._conn.executemany(
                        "INSERT INTO scenes (film_id, source, start_s, end_s, category, synced_with, note)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(film_id, source, s.start_s, s.end_s, s.category, s.synced_with, s.note)
                         for s in result.scenes],
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def scenes(self, film_id: int) -> list[Scene]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT start_s, end_s, category, source, synced_with, note FROM scenes WHERE film_id = ?"
                " ORDER BY start_s, end_s, source",
                (film_id,),
            ).fetchall()
        return [Scene(*row) for row in rows]

    def lookups_since(self, since: float) -> int:
        with self._lock:
            (count,) = self._conn.execute(
                "SELECT COUNT(DISTINCT film_id) FROM lookups WHERE fetched_at > ?", (since,)
            ).fetchone()
        return count
