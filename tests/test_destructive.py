"""Operations destructives : perimetre de suppression, retry, verification.

Rien ne touche un vrai compte : la session TIDAL est remplacee par des doubles.
`Tidal.__new__` est utilise volontairement pour construire l'objet sans passer
par `__init__`, qui exige une connexion OAuth.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import tidalapi

import apple2tidal as a2t


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Les backoffs sont reels dans le code : on les neutralise pour garder la suite rapide."""
    monkeypatch.setattr(a2t.time, "sleep", lambda *_: None)


def make_client(dry_run=False, workers=2) -> a2t.Tidal:
    t = a2t.Tidal.__new__(a2t.Tidal)
    t.dry = dry_run
    t.delay = 0.0
    t.workers = workers
    t._lock = __import__("threading").Lock()
    t._pause_until = 0.0
    t.session = MagicMock()
    t.session.user.favorites = MagicMock()
    return t


def own_playlist(pid, name, num_tracks=10, own=True):
    return {"id": pid, "name": name, "num_tracks": num_tracks, "own": own, "tracks": []}


def snapshot(playlists=(), tracks=(), albums=(), artists=(), followed=()):
    return {
        "playlists": list(playlists),
        "favorite_tracks": [{"id": i} for i in tracks],
        "favorite_albums": [{"id": i} for i in albums],
        "favorite_artists": [{"id": i} for i in artists],
        "followed_playlists": [{"id": i} for i in followed],
    }


# ------------------------------------------------------------------ _call_ok
def test_call_ok_raises_on_false_return():
    """tidalapi renvoie False au lieu de lever : sans ce garde-fou, on croit avoir supprime."""
    t = make_client()
    with pytest.raises(RuntimeError):
        t._call_ok(lambda: False)


def test_call_ok_accepts_none_and_true():
    t = make_client()
    assert t._call_ok(lambda: None) is True
    assert t._call_ok(lambda: True) is True


def test_call_retries_on_rate_limit(capsys):
    t = make_client()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 Too Many Requests")
        return "ok"

    assert t._call(flaky, retries=5) == "ok"
    assert len(calls) == 3
    assert "rate-limit" in capsys.readouterr().out


def test_call_reraises_after_last_retry():
    t = make_client()

    def always_fails():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        t._call(always_fails, retries=2)


# ----------------------------------------------------------------- _parallel
def test_parallel_counts_successes_and_survives_failures(capsys):
    t = make_client()
    seen = []

    def remove(item_id):
        seen.append(item_id)
        if item_id == "2":
            raise RuntimeError("refus API")
        return True

    assert t._parallel(remove, [1, 2, 3], workers=2, label="test") == 2
    assert {"1", "2", "3"} <= set(seen)
    # l'item en echec est rejoue : _call retente toute exception, pas seulement les 429
    assert seen.count("2") > 1
    assert "refus API" in capsys.readouterr().out


def test_parallel_on_empty_list_does_nothing():
    t = make_client()
    assert t._parallel(MagicMock(), [], workers=2, label="test") == 0


# ---------------------------------------------------------------- wipe
def test_wipe_only_deletes_named_playlists():
    t = make_client()
    objs = {pid: MagicMock(spec=tidalapi.playlist.UserPlaylist) for pid in ("a", "b", "c")}
    t._pl_objects = objs
    snap = snapshot(playlists=[own_playlist("a", "Garder"),
                               own_playlist("b", "Importee"),
                               own_playlist("c", "Aussi importee")])

    t.wipe(snap, playlists=True, favorites=False, albums=False,
           only_names={"Importee", "Aussi importee"})

    objs["a"].delete.assert_not_called()
    objs["b"].delete.assert_called_once()
    objs["c"].delete.assert_called_once()


def test_wipe_never_deletes_playlists_not_owned():
    t = make_client()
    t._pl_objects = {"x": MagicMock(spec=tidalapi.playlist.UserPlaylist)}
    snap = snapshot(playlists=[own_playlist("x", "Playlist d'un autre", own=False)])

    t.wipe(snap, playlists=True, favorites=False, albums=False, only_names=None)

    t._pl_objects["x"].delete.assert_not_called()


def test_wipe_skips_object_that_is_not_a_user_playlist(capsys):
    """Un objet Playlist (non editable) ne doit jamais partir en suppression."""
    t = make_client()
    t._pl_objects = {"x": MagicMock(spec=tidalapi.playlist.Playlist)}
    t.session.playlist.return_value = MagicMock(spec=tidalapi.playlist.Playlist)
    snap = snapshot(playlists=[own_playlist("x", "Suivie")])

    t.wipe(snap, playlists=True, favorites=False, albums=False, only_names=None)

    assert "[skip]" in capsys.readouterr().out


def test_wipe_dry_run_deletes_nothing(capsys):
    t = make_client(dry_run=True)
    obj = MagicMock(spec=tidalapi.playlist.UserPlaylist)
    t._pl_objects = {"a": obj}
    fav = t.session.user.favorites
    snap = snapshot(playlists=[own_playlist("a", "Importee")], tracks=[1, 2], albums=[3])

    t.wipe(snap, playlists=True, favorites=True, albums=True, only_names=None,
           artists=True, followed=True)

    obj.delete.assert_not_called()
    fav.remove_track.assert_not_called()
    fav.remove_album.assert_not_called()
    assert "[dry]" in capsys.readouterr().out


def test_wipe_removes_favorites_and_albums():
    t = make_client()
    fav = t.session.user.favorites
    snap = snapshot(tracks=[1, 2, 3], albums=[10], artists=[20], followed=[30])

    t.wipe(snap, playlists=False, favorites=True, albums=True, only_names=None,
           artists=True, followed=True)

    assert fav.remove_track.call_count == 3
    assert fav.remove_album.call_count == 1
    assert fav.remove_artist.call_count == 1
    assert fav.remove_playlist.call_count == 1


def test_wipe_keeps_followed_playlists_when_not_asked():
    t = make_client()
    fav = t.session.user.favorites
    t.wipe(snapshot(followed=[30]), playlists=False, favorites=False, albums=False,
           only_names=None, artists=False, followed=False)
    fav.remove_playlist.assert_not_called()


# ------------------------------------------------------------- verify_wipe
def test_verify_wipe_true_when_account_is_empty():
    t = make_client()
    t.session.user.playlists.return_value = []
    t.session.user.favorites.tracks.return_value = []
    t.session.user.favorites.albums.return_value = []
    snap = snapshot(playlists=[own_playlist("a", "Importee")], tracks=[1], albums=[2])

    assert t.verify_wipe(snap, playlists=True, favorites=True, albums=True) is True


def test_verify_wipe_false_when_playlist_survived(capsys):
    t = make_client()
    t.session.user.playlists.return_value = [SimpleNamespace(name="Importee")]
    t.session.user.favorites.tracks.return_value = []
    t.session.user.favorites.albums.return_value = []
    snap = snapshot(playlists=[own_playlist("a", "Importee")])

    assert t.verify_wipe(snap, playlists=True, favorites=False, albums=False) is False
    assert "still present" in a2t.norm(capsys.readouterr().out)


def test_verify_wipe_false_when_favorites_remain():
    t = make_client()
    t.session.user.playlists.return_value = []
    t.session.user.favorites.tracks.side_effect = [[MagicMock()], []]
    t.session.user.favorites.albums.return_value = []
    snap = snapshot(tracks=[1])

    assert t.verify_wipe(snap, playlists=False, favorites=True, albums=False) is False


def test_verify_wipe_short_circuits_in_dry_run():
    t = make_client(dry_run=True)
    assert t.verify_wipe(snapshot(), playlists=True, favorites=True, albums=True) is True
    t.session.user.playlists.assert_not_called()


# -------------------------------------------------- playlists homonymes
def user_playlist(name, pid=None):
    pl = MagicMock(spec=tidalapi.playlist.UserPlaylist)
    pl.name = name
    pl.id = pid or name
    return pl


def test_existing_playlists_keeps_both_homonyms():
    """Indexees par nom, deux playlists du meme nom n'en laissaient qu'une."""
    t = make_client()
    t.session.user.playlists.return_value = [user_playlist("Rock", "a"),
                                             user_playlist("Rock", "b"),
                                             user_playlist("Jazz", "c")]
    existing = t.existing_playlists()
    assert sorted(existing) == ["Jazz", "Rock"]
    assert [p.id for p in existing["Rock"]] == ["a", "b"]


def test_overwrite_refuses_an_ambiguous_name(capsys):
    """Deux playlists "Rock" : --overwrite en viderait une au hasard."""
    t = make_client()
    a, b = user_playlist("Rock", "a"), user_playlist("Rock", "b")

    t.create_playlist("Rock", "desc", [1, 2], {"Rock": [a, b]}, overwrite=True)

    a.clear.assert_not_called()
    b.clear.assert_not_called()
    t.session.user.create_playlist.assert_not_called()
    assert "[skip]" in capsys.readouterr().out


def test_overwrite_clears_the_only_playlist_of_that_name():
    t = make_client()
    pl = user_playlist("Rock", "a")

    t.create_playlist("Rock", "desc", [1, 2], {"Rock": [pl]}, overwrite=True)

    pl.clear.assert_called_once()
    pl.add.assert_called_once()


def test_existing_playlist_is_left_alone_without_overwrite(capsys):
    t = make_client()
    pl = user_playlist("Rock", "a")

    t.create_playlist("Rock", "desc", [1], {"Rock": [pl]}, overwrite=False)

    pl.clear.assert_not_called()
    pl.add.assert_not_called()
    assert "already exists" in capsys.readouterr().out


def test_a_new_name_is_created():
    t = make_client()
    t.create_playlist("Nouvelle", "desc", [1], {}, overwrite=False)
    t.session.user.create_playlist.assert_called_once_with("Nouvelle", "desc")
