"""Normalisation, nettoyage et scoring : le coeur du matching."""

import pytest

import apple2tidal as a2t


# --------------------------------------------------------------------- norm
@pytest.mark.parametrize("raw, expected", [
    ("Björk", "bjork"),
    ("BEYONCÉ", "beyonce"),
    ("Simon & Garfunkel", "simon and garfunkel"),
    ("Don\u2019t Stop", "don't stop"),
    ("  multiple   spaces  ", "multiple spaces"),
    ("AC/DC", "ac dc"),
])
def test_norm(raw, expected):
    assert a2t.norm(raw) == expected


# -------------------------------------------------------------- clean_title
@pytest.mark.parametrize("raw, expected", [
    ("Song (feat. Someone)", "Song"),
    ("Song (2011 Remaster)", "Song"),
    ("Song - Remastered", "Song"),
    ("Song (Radio Edit)", "Song"),
    ("Song (Live)", "Song"),
    ("Song (feat. A) (Remastered)", "Song"),   # suffixes empiles
    ("Song", "Song"),
])
def test_clean_title(raw, expected):
    assert a2t.clean_title(raw) == expected


def test_clean_title_preserves_meaningful_parentheses():
    # regression : ne pas amputer un titre dont la parenthese fait partie du nom
    assert a2t.clean_title("Sgt. Pepper's Lonely Hearts Club Band (Reprise)") \
        == "Sgt. Pepper's Lonely Hearts Club Band (Reprise)"


@pytest.mark.parametrize("raw, expected", [
    ("Artist feat. Other", "Artist"),
    ("Artist ft. Other", "Artist"),
    ("Artist, Other", "Artist"),
    ("Artist & Other", "Artist"),
    ("Artist x Other", "Artist"),
    ("Artist", "Artist"),
])
def test_clean_artist(raw, expected):
    assert a2t.clean_artist(raw) == expected


# --------------------------------------------------------- score_candidate
def test_score_exact_match_is_high(mk_track, mk_tidal_track):
    s = a2t.score_candidate(mk_track(), mk_tidal_track())
    assert s > 95


def test_score_unrelated_is_low(mk_track, mk_tidal_track):
    s = a2t.score_candidate(
        mk_track(name="Yesterday", artist="The Beatles", album="Help!"),
        mk_tidal_track(name="Sandstorm", artist="Darude", album="Before the Storm"),
    )
    assert s < 40


def test_score_ignores_accents_and_feat(mk_track, mk_tidal_track):
    s = a2t.score_candidate(
        mk_track(name="Crazy in Love (feat. Jay-Z)", artist="BEYONCÉ", album="Dangerously in Love"),
        mk_tidal_track(name="Crazy In Love", artist="Beyonce", album="Dangerously in Love"),
    )
    assert s >= a2t.DEFAULT_THRESHOLD


def test_duration_mismatch_penalised(mk_track, mk_tidal_track):
    ref = a2t.score_candidate(mk_track(duration_ms=200_000), mk_tidal_track(duration=200))
    close = a2t.score_candidate(mk_track(duration_ms=200_000), mk_tidal_track(duration=210))
    far = a2t.score_candidate(mk_track(duration_ms=200_000), mk_tidal_track(duration=260))
    assert ref > close > far
    assert ref - far >= 25  # un ecart > 20 s doit disqualifier


def test_live_version_scores_below_studio(mk_track, mk_tidal_track):
    """Un live de 30 s plus long ne doit pas battre la version studio."""
    apple = mk_track(name="Song", duration_ms=200_000)
    studio = a2t.score_candidate(apple, mk_tidal_track(name="Song", duration=200))
    live = a2t.score_candidate(apple, mk_tidal_track(name="Song (Live)", duration=230))
    assert studio > live


# ------------------------------------------------------------- dedup_key
def test_dedup_key_prefers_isrc(mk_track):
    a = mk_track(tid="1", isrc="usum71703861")
    b = mk_track(tid="2", name="Autre titre", artist="Autre", isrc="USUM71703861")
    assert a2t.dedup_key(a) == a2t.dedup_key(b) == "isrc:USUM71703861"


def test_dedup_key_falls_back_to_artist_title(mk_track):
    a = mk_track(tid="1", name="Crazy in Love (feat. Jay-Z)", artist="Beyoncé")
    b = mk_track(tid="2", name="Crazy In Love", artist="BEYONCE feat. Jay-Z")
    assert a2t.dedup_key(a) == a2t.dedup_key(b)
    assert a2t.dedup_key(a).startswith("q:")


def test_dedup_key_separates_same_title_different_artist(mk_track):
    a = mk_track(name="Hurt", artist="Nine Inch Nails")
    b = mk_track(name="Hurt", artist="Johnny Cash")
    assert a2t.dedup_key(a) != a2t.dedup_key(b)
