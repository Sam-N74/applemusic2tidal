"""Fixtures partagees. Les tests n'ouvrent jamais le reseau ni un vrai compte TIDAL."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import apple2tidal as a2t
import messages


@pytest.fixture(autouse=True)
def default_language(monkeypatch):
    """La suite verifie la sortie anglaise : APPLE2TIDAL_LANG ne doit pas la deplacer."""
    monkeypatch.delenv(messages.ENV_VAR, raising=False)
    monkeypatch.setattr(messages, "_current", messages.DEFAULT_LANG)


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


@pytest.fixture
def tidal_client():
    """Fabrique un client Tidal sans passer par __init__, qui exige un OAuth.

    tests/test_destructive.py a son propre `make_client`, identique : les deux
    convergeront quand le Lot 6 decoupera le module.
    """
    def build(dry_run=False, workers=2):
        c = a2t.Tidal.__new__(a2t.Tidal)
        c.dry = dry_run
        c.delay = 0.0
        c.workers = workers
        c._lock = threading.Lock()
        c._pause_until = 0.0
        c.session = MagicMock()
        return c
    return build
