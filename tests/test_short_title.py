"""Hazard titles: kept as the source gives them when they fit, else cut at the end of a word, never inside one."""

from app.collectors.base import TITLE_MAX, short_title


def test_a_title_that_fits_is_kept_as_it_is():
    assert short_title("Tropical Cyclone ARNAB-26 (India)") == "Tropical Cyclone ARNAB-26 (India)"
    exact = "x" * TITLE_MAX
    assert short_title(exact) == exact


def test_a_long_title_is_cut_after_a_whole_word():
    # GDACS's title of 28 October 2026, cut at 96 characters it read "... China, South Kor".
    title = "Tropical Cyclone CHAMPI-26 (Philippines, Guam, Northern Mariana Islands, Japan, China, South Korea)"
    short = short_title(title)
    assert len(short) <= TITLE_MAX
    assert short == "Tropical Cyclone CHAMPI-26 (Philippines, Guam, Northern Mariana Islands, Japan, China, South…"
    assert title.startswith(short[:-1])


def test_no_separator_is_left_before_the_ellipsis():
    assert short_title("Flood warning for the rivers of the north, the east and the south", 44) == "Flood warning for the rivers of the north…"


def test_one_word_longer_than_half_the_limit_is_cut_inside_it():
    assert short_title("a " + "b" * 30, 20) == "a " + "b" * 17 + "…"
