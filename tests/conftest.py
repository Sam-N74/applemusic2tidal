"""Fixtures partagees. Les tests n'ouvrent jamais le reseau ni un vrai compte TIDAL."""

import pytest
from doubles import make_client, tidal_track, track

from apple2tidal import messages, state


@pytest.fixture(autouse=True)
def default_language(monkeypatch):
    """La suite verifie la sortie anglaise : APPLE2TIDAL_LANG ne doit pas la deplacer."""
    monkeypatch.delenv(messages.ENV_VAR, raising=False)
    monkeypatch.setattr(messages, "_current", messages.DEFAULT_LANG)


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
    monkeypatch.setattr(state, "STATE_DIR", d)
    monkeypatch.setattr(state, "REPORT_FILE", d / "unmatched.csv")
    return d


@pytest.fixture
def tidal_client(state_dir):
    """Fabrique un client Tidal sans passer par __init__, qui exige un OAuth."""
    def build(dry_run=False, workers=2):
        return make_client(state_dir / "tidal", dry_run=dry_run, workers=workers)
    return build
