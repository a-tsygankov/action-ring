"""scenes model: title normalisation and timestamp parsing."""

import pytest

from services.scenes.model import norm_title, parse_time


def test_norm_title():
    assert norm_title("Babylon (2022)") == "babylon"
    assert norm_title("  The  Matrix ") == "matrix"
    assert norm_title("Amélie") == "amelie"
    assert norm_title("Fast & Furious") == norm_title("Fast and Furious") == "fast and furious"
    assert norm_title("Blade Runner: The Final Cut") == "blade runner the final cut"
    assert norm_title("Se7en") == "se7en"
    assert norm_title("") == ""


def test_parse_time():
    assert parse_time("0:49:03") == 2943
    assert parse_time("49:03") == 2943
    assert parse_time("0:00:03.95") == 3.95
    with pytest.raises(ValueError):
        parse_time("soon")


def test_split_title():
    from services.scenes.model import split_title
    assert split_title("Novocaine (2025)") == ("Novocaine", 2025)
    assert split_title("  Blade Runner 2049 ") == ("Blade Runner 2049", None)
    assert split_title("2012 (2009)") == ("2012", 2009)
    assert split_title("(2025)") == ("(2025)", None)          # nothing left: keep as it is
    assert split_title("Odd (3025)") == ("Odd (3025)", None)   # not a plausible year
