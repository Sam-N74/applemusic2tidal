"""Fixtures partagees. Les tests n'ouvrent jamais le reseau ni un vrai compte TIDAL."""

from types import SimpleNamespace

import pytest

import apple2tidal as a2t


def track(name="Song", artist="Artist", album="Album", album_artist=None,
          duration_ms=200_000, loved=False, isrc=None, tid="1"):
    return a2t.AppleTrack(
        id=tid, name=name, artist=artist, album=album,
        album_artist=album_artist if album_artist is not None else artist,
        duration_ms=duration_ms, loved=loved, isrc=isrc,
    )


def tidal_track(name="Song", artist="Artist", album="Album", duration=200, popularity=50):
    """Double minimal d'un tidalapi.Track : seuls les champs lus par score_candidate."""
    art = SimpleNamespace(name=artist)
    return SimpleNamespace(
        id=abs(hash((name, artist))) % 10**6,
        name=name, artist=art, artists=[art],
        album=SimpleNamespace(name=album, id=1),
        duration=duration, popularity=popularity, available=True,
    )


@pytest.fixture
def mk_track():
    return track


@pytest.fixture
def mk_tidal_track():
    return tidal_track


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Redirige l'etat disque (.apple2tidal/) vers un dossier temporaire."""
    d = tmp_path / ".apple2tidal"
    monkeypatch.setattr(a2t, "STATE_DIR", d)
    monkeypatch.setattr(a2t, "CACHE_FILE", d / "matches.json")
    monkeypatch.setattr(a2t, "REPORT_FILE", d / "unmatched.csv")
    return d
