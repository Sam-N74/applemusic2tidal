"""Garde-fous de la ligne de commande.

Tous ces cas sont rejetes par argparse avant la moindre connexion TIDAL :
aucun test ici ne touche le reseau.
"""

import pytest

import apple2tidal as a2t


def run(monkeypatch, *argv):
    monkeypatch.setattr(a2t.sys, "argv", ["apple2tidal.py", *argv])
    with pytest.raises(SystemExit) as exc:
        a2t.main()
    return exc.value.code


def test_help_exits_cleanly(monkeypatch):
    assert run(monkeypatch, "--help") == 0


def test_action_is_required(monkeypatch, capsys):
    assert run(monkeypatch, "library.json") == 2
    assert "au moins une action" in capsys.readouterr().err


def test_library_path_required_without_wipe(monkeypatch, capsys):
    assert run(monkeypatch, "--playlists") == 2
    assert "export manquant" in capsys.readouterr().err


def test_wipe_refuses_to_be_combined_with_an_import(monkeypatch, capsys):
    """--wipe supprime et s'arrete : le combiner ferait croire a un import."""
    assert run(monkeypatch, "library.json", "--wipe", "--all") == 2
    assert "--wipe" in capsys.readouterr().err


def test_reset_requires_an_import_action(monkeypatch, capsys):
    assert run(monkeypatch, "library.json", "--reset") == 2
    assert "--reset" in capsys.readouterr().err


def test_unknown_option_is_rejected(monkeypatch):
    assert run(monkeypatch, "library.json", "--nimportequoi") == 2
