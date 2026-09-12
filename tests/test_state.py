"""Etat local par service : le dossier .apple2tidal/<service>/ et l'adoption de
l'ancien etat a plat, ecrit par les versions precedentes."""

from unittest.mock import MagicMock

import apple2tidal as a2t
from apple2tidal.providers import tidal as tidal_provider


def flat_state(state_dir):
    state_dir.mkdir(parents=True)
    (state_dir / "tidal_session.json").write_text("session", encoding="utf-8")
    (state_dir / "matches.json").write_text("{}", encoding="utf-8")
    (state_dir / "backup_20260101_000000.json").write_text("backup", encoding="utf-8")


def test_adopt_moves_the_flat_files_once(state_dir, capsys):
    flat_state(state_dir)
    store = a2t.Store(state_dir / "tidal")

    assert store.adopt(state_dir, tidal_provider.LEGACY_FILES) == 3

    assert store.session_file.read_text(encoding="utf-8") == "session"
    assert store.cache_file.read_text(encoding="utf-8") == "{}"
    assert (store.dir / "backup_20260101_000000.json").read_text(encoding="utf-8") == "backup"
    assert not (state_dir / "tidal_session.json").exists()
    assert not (state_dir / "matches.json").exists()
    assert "3 file(s) moved" in capsys.readouterr().out
    # deja adopte : plus rien a faire, et rien a dire
    assert store.adopt(state_dir, tidal_provider.LEGACY_FILES) == 0
    assert capsys.readouterr().out == ""


def test_adopt_never_overwrites_the_new_state(state_dir):
    flat_state(state_dir)
    store = a2t.Store(state_dir / "tidal")
    store.dir.mkdir()
    store.cache_file.write_text('{"isrc:X": {}}', encoding="utf-8")

    assert store.adopt(state_dir, tidal_provider.LEGACY_FILES) == 2

    assert store.cache_file.read_text(encoding="utf-8") == '{"isrc:X": {}}'
    assert (state_dir / "matches.json").exists()


def test_adopt_on_a_fresh_install_is_silent(state_dir, capsys):
    assert a2t.Store(state_dir / "tidal").adopt(state_dir, tidal_provider.LEGACY_FILES) == 0
    assert capsys.readouterr().out == ""


def test_tidal_client_adopts_the_flat_state_before_logging_in(state_dir, monkeypatch):
    """Le client relit la session a son nouvel emplacement : sans l'adoption, un
    utilisateur a jour devrait se reconnecter et perdrait son cache."""
    flat_state(state_dir)
    session = MagicMock()
    session.login_session_file.return_value = True
    session.check_login.return_value = True
    session.user.username = "sam"
    monkeypatch.setattr(tidal_provider.tidalapi, "Session", lambda: session)
    store = a2t.Store(state_dir / "tidal")

    a2t.Tidal(store, dry_run=True)

    session.login_session_file.assert_called_once_with(store.session_file)
    assert store.session_file.read_text(encoding="utf-8") == "session"
