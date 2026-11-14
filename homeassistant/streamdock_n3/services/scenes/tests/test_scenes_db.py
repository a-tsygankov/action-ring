import sqlite3
import threading

from services.scenes.db import Db
from services.scenes.model import Film, Scene, SourceResult


def mem():
    return sqlite3.connect(":memory:", check_same_thread=False)


def hit(*starts, source="videoskip"):
    return SourceResult("hit", tuple(Scene(s, s + 10, "sex", source) for s in starts))


def test_film_roundtrip_and_runtime_match():
    db = Db(mem()); fid = db.save_film(Film("Dracula", "dracula", 7800, 2025, "X", ("A", "B"), 1, "tt1"), 100.0)
    assert db.find_film("dracula", 7700) == (fid, Film("Dracula", "dracula", 7800, 2025, "X", ("A", "B"), 1, "tt1"))
    assert db.find_film("dracula", 5000) is None
    assert db.find_film("dracula", None)[0] == fid
    assert db.save_film(Film("Dracula", "dracula", 7800, 2025), 200.0) == fid     # same row
    assert db.find_film("dracula", 7800)[1] == Film("Dracula", "dracula", 7800, 2025, "X", ("A", "B"), 1, "tt1")


def test_save_film_is_fill_in_only():
    db = Db(mem()); fid = db.save_film(Film("D", "d", None, None), 1.0)
    assert db.save_film(Film("D", "d", 7800, 2025, None, (), 5, None), 2.0) == fid   # unknown runtime matches any
    assert db.save_film(Film("D", "d", 7800, None, "Y", ("A",)), 3.0) == fid
    assert db.find_film("d", None)[1] == Film("D", "d", 7800, 2025, "Y", ("A",), 5, None)
    # stored known values win over different new ones
    assert db.save_film(Film("D2", "d", 7900, 2024, "Z", ("B",), 6, "tt9"), 4.0) == fid
    assert db.find_film("d", None)[1] == Film("D", "d", 7800, 2025, "Y", ("A",), 5, "tt9")


def test_find_film_picks_closest_runtime_else_latest():
    db = Db(mem())
    a = db.save_film(Film("D", "d", 6000, 1990), 10.0)
    b = db.save_film(Film("D", "d", 7800, 2025), 20.0)
    assert a != b
    assert db.find_film("d", 6100)[0] == a
    assert db.find_film("d", 7700)[0] == b
    assert db.find_film("d", None)[0] == b
    db.save_film(Film("D", "d", 6000, 1990), 30.0)
    assert db.find_film("d", None)[0] == a


def test_results_replace_and_states():
    db = Db(mem()); fid = db.save_film(Film("D", "d", 7800), 0.0)
    db.save_result(fid, "videoskip", SourceResult("hit", (Scene(10, 20, "sex", "videoskip"),)), 50.0)
    db.save_result(fid, "nsfwhen", SourceResult("miss"), 60.0)
    db.save_result(fid, "videoskip", SourceResult("hit", (Scene(30, 40, "nudity", "videoskip"),)), 70.0)
    assert [s.start_s for s in db.scenes(fid)] == [30]
    assert db.lookup_state(fid) == {"videoskip": ("hit", 70.0), "nsfwhen": ("miss", 60.0)}
    assert db.lookups_since(55.0) == 1 and db.lookups_since(80.0) == 0


def test_blocked_and_error_keep_scenes_miss_and_ambiguous_clear_them():
    db = Db(mem()); fid = db.save_film(Film("D", "d"), 0.0)
    db.save_result(fid, "videoskip", hit(10), 1.0)
    for status in ("blocked", "error"):
        db.save_result(fid, "videoskip", SourceResult(status), 2.0)
        assert [s.start_s for s in db.scenes(fid)] == [10]
        assert db.lookup_state(fid)["videoskip"] == (status, 2.0)
    for status in ("miss", "ambiguous"):
        db.save_result(fid, "videoskip", hit(10), 3.0)
        db.save_result(fid, "videoskip", SourceResult(status), 4.0)
        assert db.scenes(fid) == []
        assert db.lookup_state(fid)["videoskip"] == (status, 4.0)


def test_scenes_roundtrip_all_fields_across_sources_ordered():
    db = Db(mem()); fid = db.save_film(Film("D", "d"), 0.0)
    db.save_result(fid, "nsfwhen", SourceResult("hit", (Scene(5, 9, "nudity", "nsfwhen", "Max", "n"),)), 1.0)
    db.save_result(fid, "videoskip", hit(2), 1.0)
    assert db.scenes(fid) == [Scene(2, 12, "sex", "videoskip"), Scene(5, 9, "nudity", "nsfwhen", "Max", "n")]


def test_shared_connection_across_threads():
    db = Db(mem()); errors = []

    def work(i):
        try:
            fid = db.save_film(Film(f"F{i}", f"f{i}"), float(i))
            db.save_result(fid, "videoskip", hit(i), float(i))
            db.scenes(fid)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and db.lookups_since(-1.0) == 8


def test_lookups_since_can_leave_out_a_source():
    db = Db(mem())
    a = db.save_film(Film("A", "a"), 0.0); b = db.save_film(Film("B", "b"), 0.0)
    db.save_result(a, "tmdb", SourceResult("hit"), 10.0)
    db.save_result(b, "tmdb", SourceResult("miss"), 10.0)
    db.save_result(b, "videoskip", SourceResult("miss"), 10.0)
    assert db.lookups_since(0.0) == 2
    assert db.lookups_since(0.0, exclude=("tmdb",)) == 1


def test_level_roundtrip_and_upgrade_from_v1_refetches_videoskip():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    db = Db(conn)
    fid = db.save_film(Film("T", "t", 11700), 0.0)
    db.save_result(fid, "videoskip", SourceResult("hit", (Scene(10, 20, "sex", "videoskip", severity=2),)), 1.0)
    assert db.scenes(fid)[0].severity == 2

    old = sqlite3.connect(":memory:", check_same_thread=False)      # a version-1 database, as on the device
    old.executescript("""
        CREATE TABLE films (id INTEGER PRIMARY KEY AUTOINCREMENT, norm_title TEXT NOT NULL, title TEXT NOT NULL,
            year INTEGER, runtime_s INTEGER, director TEXT, cast_json TEXT NOT NULL DEFAULT '[]', tmdb_id INTEGER,
            imdb_id TEXT, updated_at REAL NOT NULL);
        CREATE TABLE lookups (film_id INTEGER NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
            fetched_at REAL NOT NULL, PRIMARY KEY (film_id, source));
        CREATE TABLE scenes (film_id INTEGER NOT NULL, source TEXT NOT NULL, start_s REAL NOT NULL, end_s REAL NOT NULL,
            category TEXT NOT NULL, synced_with TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '');
        INSERT INTO films (norm_title, title, runtime_s, updated_at) VALUES ('t', 'T', 11700, 0);
        INSERT INTO lookups VALUES (1, 'videoskip', 'hit', 5), (1, 'nsfwhen', 'miss', 5), (1, 'tmdb', 'hit', 5);
        INSERT INTO scenes (film_id, source, start_s, end_s, category) VALUES (1, 'videoskip', 10, 20, 'sex'),
            (1, 'nsfwhen', 30, 40, 'nudity');
        PRAGMA user_version = 1;
    """)
    up = Db(old)
    assert [s.source for s in up.scenes(1)] == ["nsfwhen"]           # level-less VideoSkip scenes are dropped
    assert set(up.lookup_state(1)) == {"nsfwhen", "tmdb"}            # ...and VideoSkip is due again
    assert old.execute("PRAGMA user_version").fetchone()[0] == 2
    Db(old)                                                          # opening again changes nothing
    assert set(up.lookup_state(1)) == {"nsfwhen", "tmdb"}
