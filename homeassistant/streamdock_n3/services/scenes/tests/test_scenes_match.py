from services.scenes.match import Candidate, pick
from services.scenes.model import Film

FILM = Film("Tuner", "tuner", runtime_s=6600, year=2026)


def test_pick_rules():
    c = lambda **k: Candidate(ref="r", title="Tuner", **k)
    assert pick(FILM, [c(year=2026)])[1] == "hit"
    assert pick(FILM, [c(year=1999)])[1] == "miss"          # lone but contradicting
    assert pick(FILM, [c()])[1] == "miss"                   # title alone is not enough
    got = pick(FILM, [c(year=2026), c(year=2025)])
    assert (got[0].year, got[1]) == (2026, "hit")
    assert pick(FILM, [c(year=2026), c(year=2026)])[1] == "ambiguous"
    assert pick(FILM, [c(year=2026, runtime_s=6500), c(year=2026, runtime_s=3000)])[0].runtime_s == 6500
    assert pick(FILM, [Candidate("r", "Tuna", year=2026)])[1] == "miss"


def test_pick_ids_and_tiebreaks():
    f = Film("Tuner", "tuner", year=2026, imdb_id="tt1", director="A B", cast=("x", "y", "z"))
    c = lambda **k: Candidate(ref="r", title="Tuner", **k)
    assert pick(f, [c(imdb_id="tt2", year=2026)])[1] == "miss"
    assert pick(f, [c(imdb_id="tt1")])[1] == "hit"
    assert pick(f, [c(year=2026), c(imdb_id="tt1", year=2026)])[0].imdb_id == "tt1"
    assert pick(f, [c(year=2026, director="a  b"), c(year=2026, director="C")])[0].director == "a  b"
    assert pick(f, [c(year=2026, cast=("x", "y")), c(year=2026, cast=("x",))])[0].cast == ("x", "y")
    assert pick(f, [c(year=2026), c(year=2026)])[1] == "ambiguous"


def test_pick_runtime_breaks_year_tie():
    c = lambda **k: Candidate(ref="r", title="Tuner", **k)
    got = pick(FILM, [c(year=2026, runtime_s=6500), c(year=2026)])
    assert (got[0].runtime_s, got[1]) == (6500, "hit")
