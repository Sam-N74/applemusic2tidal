"""Garde-fous de la ligne de commande.

Tous ces cas sont rejetes par argparse avant la moindre connexion TIDAL :
aucun test ici ne touche le reseau.
"""

import sys

import pytest

import apple2tidal as a2t


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["apple2tidal.py", *argv])
    with pytest.raises(SystemExit) as exc:
        a2t.main()
    return exc.value.code


def test_help_exits_cleanly(monkeypatch):
    assert run(monkeypatch, "--help") == 0


def test_action_is_required(monkeypatch, capsys):
    assert run(monkeypatch, "library.json") == 2
    assert "at least one action" in capsys.readouterr().err


def test_library_path_required_without_wipe(monkeypatch, capsys):
    assert run(monkeypatch, "--playlists") == 2
    assert "missing export path" in capsys.readouterr().err


def test_wipe_refuses_to_be_combined_with_an_import(monkeypatch, capsys):
    """--wipe supprime et s'arrete : le combiner ferait croire a un import."""
    assert run(monkeypatch, "library.json", "--wipe", "--all") == 2
    assert "--wipe" in capsys.readouterr().err


def test_reset_requires_an_import_action(monkeypatch, capsys):
    assert run(monkeypatch, "library.json", "--reset") == 2
    assert "--reset" in capsys.readouterr().err


def test_unknown_option_is_rejected(monkeypatch):
    assert run(monkeypatch, "library.json", "--nimportequoi") == 2

# ----------------------------------------------------- choix de la source et de la cible
def test_the_same_service_cannot_be_both_ends(monkeypatch, capsys):
    assert run(monkeypatch, "--from", "spotify", "--to", "spotify", "--all") == 2
    assert "--from and --to must differ" in capsys.readouterr().err


def test_an_unknown_service_is_refused_with_the_list_of_the_real_ones(monkeypatch, capsys):
    assert run(monkeypatch, "library.json", "--from", "deezer", "--all") == 2
    err = capsys.readouterr().err
    assert "apple" in err and "spotify" in err


def test_tidal_is_not_offered_as_a_source(monkeypatch, capsys):
    """TIDAL sait ecrire, pas encore lire : mieux vaut le refuser tout de suite
    que de casser apres la connexion."""
    assert run(monkeypatch, "--from", "tidal", "--all") == 2
    assert "invalid choice" in capsys.readouterr().err


def test_an_export_file_is_still_required_for_apple(monkeypatch, capsys):
    assert run(monkeypatch, "--to", "spotify", "--playlists") == 2
    assert "missing export path" in capsys.readouterr().err


def test_a_spotify_source_needs_no_export_file(monkeypatch, state_dir):
    """La seule commande qui change vraiment : plus de fichier a fournir."""
    seen = {}
    monkeypatch.setattr(a2t.cli, "build_source", lambda args: _FakeSource(seen))
    monkeypatch.setattr(a2t.cli, "build_destination", lambda args, store: _FakeDest(seen))
    monkeypatch.setattr(a2t.cli, "transfer", lambda lib, dest, store, opts: seen.setdefault(
        "transfer", (dest.name, opts.playlists)))
    monkeypatch.setattr(sys, "argv", ["apple2tidal", "--from", "spotify", "--playlists"])

    a2t.main()

    assert seen["transfer"] == ("TIDAL", True)
    assert seen["read"] == 1


class _FakeSource:
    name = "Spotify"

    def __init__(self, seen):
        self.seen = seen

    def read(self):
        self.seen["read"] = self.seen.get("read", 0) + 1
        return a2t.Library()


class _FakeDest:
    name = "TIDAL"

    def __init__(self, seen):
        self.seen = seen
