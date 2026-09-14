"""Spotify, source et destination : autorisation, transport, adaptateur.

Aucun test n'ouvre le reseau. L'OAuth n'est pas contourne pour autant : le vrai
flux PKCE tourne, contre un navigateur et un serveur de jetons doubles, et la
redirection passe par un vrai socket sur 127.0.0.1 — c'est justement la piece
qu'on veut voir marcher.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest
from doubles import (
    FakeResponse,
    FakeSpotify,
    StubAuth,
    spotify_album,
    spotify_playlist,
    spotify_track,
)

import apple2tidal as a2t
from apple2tidal.providers import spotify as sp

CLIENT = "un-client-id"


@pytest.fixture
def store(state_dir):
    return a2t.Store(state_dir / "spotify")


@pytest.fixture
def account(store):
    """Un compte Spotify vide, et l'adaptateur branche dessus."""
    def build(dry_run=False, workers=1, **kw):
        fake = FakeSpotify(**kw)
        api = sp.SpotifyApi(StubAuth(), session=fake)
        return fake, sp.Spotify(store, dry_run=dry_run, workers=workers, api=api)
    return build


# --------------------------------------------------------------------- PKCE
def test_challenge_follows_rfc_7636():
    """Le defi est le SHA-256 du secret, en base64url sans remplissage."""
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert sp.make_challenge(verifier) == expected
    assert "=" not in sp.make_challenge(verifier)


def test_verifier_stays_within_the_allowed_length():
    for _ in range(20):
        v = sp.make_verifier()
        assert 43 <= len(v) <= 128
        assert set(v) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


# ---------------------------------------------------------------- client ID
def test_client_id_comes_from_the_environment(store, monkeypatch):
    monkeypatch.setenv(sp.CLIENT_ID_ENV, "  depuis-l-env  ")
    assert sp.read_client_id(store) == "depuis-l-env"


def test_client_id_falls_back_to_the_service_folder(store, monkeypatch):
    monkeypatch.delenv(sp.CLIENT_ID_ENV, raising=False)
    store.dir.mkdir(parents=True)
    (store.dir / "app.json").write_text(json.dumps({"client_id": "depuis-le-fichier"}),
                                        encoding="utf-8")
    assert sp.read_client_id(store) == "depuis-le-fichier"


def test_a_missing_client_id_explains_how_to_get_one(store, monkeypatch, capsys):
    """Sans app Spotify, l'outil ne peut rien faire : il doit dire quoi faire,
    pas lever une pile d'appels."""
    monkeypatch.delenv(sp.CLIENT_ID_ENV, raising=False)
    store.dir.mkdir(parents=True)
    with pytest.raises(SystemExit):
        sp.read_client_id(store)
    out = capsys.readouterr().out
    assert "developer.spotify.com/dashboard" in out
    assert sp.CLIENT_ID_ENV in out
    assert "127.0.0.1" in out


# ------------------------------------------------------------ flux complet
class TokenServer:
    """Le point de terminaison de jeton de Spotify, double. Enregistre les
    formulaires recus pour qu'un test puisse verifier ce qui a ete demande."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.forms: list[dict] = []

    def __call__(self, url, data=None, timeout=None, headers=None):
        self.forms.append(dict(data or {}))
        return self.answers.pop(0) if self.answers else FakeResponse(200, {
            "access_token": "jeton", "refresh_token": "refresh",
            "expires_in": 3600, "scope": " ".join(sp.SCOPES)})


def browser_that_accepts(state_override=None, error=None):
    """Un navigateur qui suit la redirection, dans un fil : le serveur local
    n'ecoute qu'apres l'appel a l'ouvreur."""
    def open_url(url):
        query = parse_qs(urlparse(url).query)
        redirect = query["redirect_uri"][0]
        answer = {"state": state_override or query["state"][0]}
        answer["error" if error else "code"] = error or "le-code"
        target = redirect + "?" + "&".join(f"{k}={v}" for k, v in answer.items())

        def visit():
            try:
                urllib.request.urlopen(target, timeout=5).read()
            except Exception:  # noqa: BLE001  le test lit le resultat, pas l'erreur HTTP
                pass
        threading.Thread(target=visit, daemon=True).start()
        return True
    return open_url


def test_the_authorization_round_trip_writes_a_session(store, monkeypatch):
    """Le vrai serveur de redirection, le vrai calcul PKCE, le vrai echange."""
    monkeypatch.setenv(sp.CLIENT_ID_ENV, CLIENT)
    tokens = TokenServer()
    auth = sp.SpotifyAuth(store, opener=browser_that_accepts(), post=tokens)

    assert auth.token() == "jeton"

    form = tokens.forms[0]
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "le-code"
    assert form["client_id"] == CLIENT
    assert form["redirect_uri"].startswith("http://127.0.0.1:")
    assert sp.make_challenge(form["code_verifier"])  # le secret est bien celui envoye
    saved = json.loads(store.session_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh"
    assert saved["expires_at"] > 0


def test_an_answer_carrying_the_wrong_state_is_refused(store, monkeypatch):
    """Sans cette verification, n'importe quelle page ouverte pourrait pousser
    un code d'autorisation dans le serveur local."""
    monkeypatch.setenv(sp.CLIENT_ID_ENV, CLIENT)
    auth = sp.SpotifyAuth(store, opener=browser_that_accepts(state_override="autre"),
                          post=TokenServer())
    with pytest.raises(SystemExit):
        auth.token()


def test_a_refused_authorization_stops_there(store, monkeypatch):
    monkeypatch.setenv(sp.CLIENT_ID_ENV, CLIENT)
    auth = sp.SpotifyAuth(store, opener=browser_that_accepts(error="access_denied"),
                          post=TokenServer())
    with pytest.raises(SystemExit):
        auth.token()


def test_an_expired_token_is_refreshed_without_the_browser(store, monkeypatch):
    monkeypatch.setenv(sp.CLIENT_ID_ENV, CLIENT)
    store.dir.mkdir(parents=True)
    store.session_file.write_text(json.dumps({
        "access_token": "vieux", "refresh_token": "refresh", "expires_at": 0,
        "scope": " ".join(sp.SCOPES)}), encoding="utf-8")
    tokens = TokenServer(FakeResponse(200, {"access_token": "neuf", "expires_in": 3600,
                                            "scope": " ".join(sp.SCOPES)}))
    opened = []
    auth = sp.SpotifyAuth(store, opener=opened.append, post=tokens)

    assert auth.token() == "neuf"

    assert opened == [], "un jeton rafraichissable ne doit pas rouvrir le navigateur"
    assert tokens.forms[0]["grant_type"] == "refresh_token"
    # Spotify ne renvoie pas toujours un refresh_token : garder l'ancien
    assert json.loads(store.session_file.read_text(encoding="utf-8"))["refresh_token"] == "refresh"


def test_a_session_missing_a_scope_asks_again(store, monkeypatch):
    """Une version qui demande un droit de plus doit redemander l'accord, sinon
    le transfert casse au milieu sur un 403."""
    monkeypatch.setenv(sp.CLIENT_ID_ENV, CLIENT)
    store.dir.mkdir(parents=True)
    store.session_file.write_text(json.dumps({
        "access_token": "vieux", "refresh_token": "refresh", "expires_at": 10 ** 10,
        "scope": "user-library-read"}), encoding="utf-8")
    tokens = TokenServer()
    auth = sp.SpotifyAuth(store, opener=browser_that_accepts(), post=tokens)

    assert auth.token() == "jeton"
    assert tokens.forms[0]["grant_type"] == "authorization_code"


# ------------------------------------------------------------------ transport
def test_a_rate_limit_is_waited_out_then_retried(monkeypatch, capsys):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    fake = FakeSpotify()
    fake.answers = [FakeResponse(429, {}, {"Retry-After": "7"}),
                    FakeResponse(200, {"id": "sam"})]
    api = sp.SpotifyApi(StubAuth(), session=fake)

    assert api.get("/me") == {"id": "sam"}
    assert "7" in capsys.readouterr().out


def test_a_revoked_token_is_asked_again_once():
    fake = FakeSpotify()
    auth = StubAuth()
    fake.answers = [FakeResponse(401, {}), FakeResponse(200, {"id": "sam"})]
    api = sp.SpotifyApi(auth, session=fake)

    assert api.get("/me") == {"id": "sam"}
    assert auth.forgotten == 1


def test_a_refusal_is_raised_not_swallowed():
    fake = FakeSpotify()
    fake.answers = [FakeResponse(403, {"error": {"message": "nope"}})]
    api = sp.SpotifyApi(StubAuth(), session=fake)
    with pytest.raises(sp.SpotifyError):
        api.get("/me")


def test_pagination_follows_every_page():
    fake = FakeSpotify(saved_tracks=[spotify_track(tid=f"t{i}") for i in range(120)])
    api = sp.SpotifyApi(StubAuth(), session=fake)

    got = api.paginate("/me/tracks")

    assert len(got) == 120
    assert [c[1] for c in fake.calls].count("/me/tracks") == 3  # 50 + 50 + 20


# --------------------------------------------------------------------- source
def test_read_builds_a_library_from_the_account(account):
    fake, spotify = account(
        saved_tracks=[spotify_track(tid="t1", name="Song A", artist="Artist A",
                                    isrc="USUM71703861")],
        playlists=[spotify_playlist(pid="p1", name="Road trip", owner="sam")],
        playlist_items={"p1": [spotify_track(tid="t1", name="Song A", artist="Artist A",
                                             isrc="USUM71703861"),
                               spotify_track(tid="t2", name="Song B", artist="Artist B")]},
        saved_albums=[spotify_album(aid="al9", name="Album A", artist="Artist A",
                                    upc="123", total_tracks=11)])

    lib = spotify.read()

    assert set(lib.tracks) == {"t1", "t2"}
    assert lib.tracks["t1"].isrc == "USUM71703861"
    assert lib.tracks["t1"].loved is True      # dans les titres aimes
    assert lib.tracks["t2"].loved is False     # seulement dans une playlist
    assert lib.tracks["t1"].year == 2011
    assert [(p.name, p.track_ids) for p in lib.playlists] == [("Road trip", ["t1", "t2"])]
    assert [(al.name, al.upc, al.track_count) for al in lib.albums] == [("Album A", "123", 11)]


def test_read_skips_local_files_and_podcast_episodes(account):
    fake, spotify = account(
        playlists=[spotify_playlist(pid="p1")],
        playlist_items={"p1": [
            spotify_track(tid="t1"),
            spotify_track(tid="t2", is_local=True),
            spotify_track(tid="t3", type="episode"),
            {"id": None, "name": "sans identifiant"},
        ]})

    lib = spotify.read()

    assert list(lib.tracks) == ["t1"]
    assert lib.playlists[0].track_ids == ["t1"]


def test_read_leaves_alone_the_playlists_the_user_only_follows(account):
    """Spotify refuse le contenu d'une playlist qu'on ne fait que suivre : la
    demander serait un 403 au milieu de la lecture."""
    fake, spotify = account(
        playlists=[spotify_playlist(pid="p1", name="A moi", owner="sam"),
                   spotify_playlist(pid="p2", name="Editoriale", owner="spotify"),
                   spotify_playlist(pid="p3", name="Partagee", owner="ami",
                                    collaborative=True)],
        playlist_items={"p1": [spotify_track(tid="t1")],
                        "p2": [spotify_track(tid="t9")],
                        "p3": [spotify_track(tid="t3")]})

    lib = spotify.read()

    assert sorted(p.name for p in lib.playlists) == ["A moi", "Partagee"]
    assert "/playlists/p2/items" not in [c[1] for c in fake.calls]


# ------------------------------------------------------------------ catalogue
def test_an_isrc_finds_the_exact_track(account):
    fake, spotify = account(catalog=[spotify_track(tid="t1", name="Song A",
                                                   isrc="USUM71703861")])

    found = spotify.tracks_by_isrc("USUM71703861")

    assert [c.id for c in found] == ["t1"]
    assert found[0].duration_s == 200.0
    assert fake.calls[-1][2]["q"] == "isrc:USUM71703861"


def test_a_track_unavailable_in_the_market_is_not_a_candidate(account):
    fake, spotify = account(catalog=[
        spotify_track(tid="t1", name="Song A", isrc="X", is_playable=False),
        spotify_track(tid="t2", name="Song A", isrc="X")])
    assert [c.id for c in spotify.tracks_by_isrc("X")] == ["t2"]


def test_an_upc_finds_the_album(account):
    fake, spotify = account(catalog=[
        spotify_track(tid="t1", album="Album A", album_id="al9", album_upc="123")])
    assert spotify.album_by_upc("123") == "al9"
    assert spotify.album_by_upc("999") is None


def test_a_search_failure_costs_no_match_but_no_crash(account, capsys):
    fake, spotify = account()
    fake.answers = [FakeResponse(500, {}), FakeResponse(500, {}), FakeResponse(500, {}),
                    FakeResponse(500, {}), FakeResponse(500, {})]
    assert spotify.search_tracks("rien") == []
    assert "search err" in capsys.readouterr().out


# ------------------------------------------------------------------- ecriture
def test_a_playlist_is_created_with_its_tracks(account):
    fake, spotify = account(catalog=[spotify_track(tid=f"t{i}") for i in range(1, 4)])

    spotify.create_playlist("Road trip", "desc", ["t1", "t2", "t3"], {}, overwrite=False)

    assert [p["name"] for p in fake.playlists] == ["Road trip"]
    created = fake.playlists[0]["id"]
    assert [t["id"] for t in fake.playlist_items[created]] == ["t1", "t2", "t3"]
    assert fake.calls[-2][3]["public"] is False   # privee par defaut


def test_a_long_playlist_is_sent_in_chunks(account):
    ids = [f"t{i}" for i in range(250)]
    fake, spotify = account(catalog=[spotify_track(tid=i) for i in ids])

    spotify.create_playlist("Longue", "desc", ids, {}, overwrite=False)

    adds = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/items")]
    assert [len(c[3]["uris"]) for c in adds] == [100, 100, 50]
    assert len(fake.playlist_items["new1"]) == 250


def test_overwrite_replaces_the_content_of_the_existing_playlist(account):
    fake, spotify = account(
        playlists=[spotify_playlist(pid="p1", name="Road trip", owner="sam")],
        playlist_items={"p1": [spotify_track(tid="vieux")]},
        catalog=[spotify_track(tid="t1"), spotify_track(tid="t2")])

    spotify.create_playlist("Road trip", "desc", ["t1", "t2"],
                            spotify.existing_playlists(), overwrite=True)

    assert [t["id"] for t in fake.playlist_items["p1"]] == ["t1", "t2"]
    assert len(fake.playlists) == 1, "aucune playlist ne doit etre creee en double"


def test_two_playlists_of_the_same_name_are_left_untouched(account, capsys):
    """Meme regle que cote TIDAL : on ne peut pas deviner laquelle vider."""
    fake, spotify = account(
        playlists=[spotify_playlist(pid="p1", name="Road trip", owner="sam"),
                   spotify_playlist(pid="p2", name="Road trip", owner="sam")],
        playlist_items={"p1": [spotify_track(tid="vieux")], "p2": []},
        catalog=[spotify_track(tid="t1")])

    spotify.create_playlist("Road trip", "desc", ["t1"],
                            spotify.existing_playlists(), overwrite=True)

    assert [t["id"] for t in fake.playlist_items["p1"]] == ["vieux"]
    assert fake.playlist_items["p2"] == []
    assert "Road trip" in capsys.readouterr().out


def test_favorites_go_out_in_packets_of_forty(account):
    ids = [f"t{i}" for i in range(95)]
    fake, spotify = account(catalog=[spotify_track(tid=i) for i in ids])

    spotify.favorite_tracks(ids)

    writes = [c for c in fake.calls if c[1] == "/me/library" and c[0] == "PUT"]
    assert [len(c[2]["uris"].split(",")) for c in writes] == [40, 40, 15]
    assert len(fake.saved_tracks) == 95


def test_dry_run_writes_nothing_at_all(account, capsys):
    fake, spotify = account(dry_run=True, catalog=[spotify_track(tid="t1")])

    spotify.create_playlist("Road trip", "desc", ["t1"], {}, overwrite=False)
    spotify.favorite_tracks(["t1"])
    spotify.favorite_albums(["al1"])

    assert fake.playlists == [] and fake.saved_tracks == [] and fake.saved_albums == []
    assert "[dry]" in capsys.readouterr().out


# -------------------------------------------------------------- suppressions
@pytest.fixture
def furnished(account):
    """Un compte avec de quoi supprimer : deux playlists a soi, une suivie."""
    return account(
        saved_tracks=[spotify_track(tid="t1"), spotify_track(tid="t2")],
        saved_albums=[spotify_album(aid="al1")],
        artists=[{"id": "ar1", "name": "Artist"}],
        playlists=[spotify_playlist(pid="p1", name="A moi", owner="sam", total=2),
                   spotify_playlist(pid="p2", name="Autre", owner="sam", total=1),
                   spotify_playlist(pid="p3", name="Suivie", owner="quelquun")],
        playlist_items={"p1": [spotify_track(tid="t1"), spotify_track(tid="t2")],
                        "p2": [spotify_track(tid="t2")]})


def test_the_snapshot_has_the_shape_the_command_line_expects(furnished):
    fake, spotify = furnished

    snap = spotify.snapshot(workers=2)

    assert set(snap) >= {"path", "playlists", "favorite_tracks", "favorite_albums",
                         "favorite_artists", "followed_playlists"}
    assert {p["name"]: p["own"] for p in snap["playlists"]} == {
        "A moi": True, "Autre": True, "Suivie": False}
    assert [p["name"] for p in snap["followed_playlists"]] == ["Suivie"]
    assert [t["id"] for t in snap["favorite_tracks"]] == ["t1", "t2"]
    assert [a["id"] for a in snap["favorite_albums"]] == ["al1"]
    assert [a["id"] for a in snap["favorite_artists"]] == ["ar1"]
    # la sauvegarde est sur le disque avant toute suppression
    assert json.loads(snap["path"].read_text(encoding="utf-8"))["playlists"]
    own = next(p for p in snap["playlists"] if p["id"] == "p1")
    assert [t["id"] for t in own["tracks"]] == ["t1", "t2"]


def test_a_wipe_removes_everything_it_was_asked_for(furnished, capsys):
    fake, spotify = furnished
    snap = spotify.snapshot(workers=1)

    spotify.wipe(snap, playlists=True, favorites=True, albums=True,
                 artists=True, followed=True)

    assert fake.playlists == []
    assert fake.saved_tracks == [] and fake.saved_albums == [] and fake.artists == []
    assert spotify.verify_wipe(snap, playlists=True, favorites=True, albums=True) is True
    assert "unfollow" in capsys.readouterr().out.lower()


def test_a_wipe_can_be_narrowed_to_the_playlists_of_the_source(furnished):
    fake, spotify = furnished
    snap = spotify.snapshot(workers=1)

    spotify.wipe(snap, playlists=True, favorites=False, albums=False,
                 only_names={"A moi"})

    assert sorted(p["id"] for p in fake.playlists) == ["p2", "p3"]
    assert len(fake.saved_tracks) == 2, "les favoris n'etaient pas demandes"


def test_a_playlist_someone_else_owns_is_never_removed(furnished):
    fake, spotify = furnished
    snap = spotify.snapshot(workers=1)

    spotify.wipe(snap, playlists=True, favorites=False, albums=False)

    assert [p["id"] for p in fake.playlists] == ["p3"]


def test_a_dry_wipe_only_describes(furnished, capsys):
    fake, spotify = furnished
    spotify.dry = True
    snap = spotify.snapshot(workers=1)

    spotify.wipe(snap, playlists=True, favorites=True, albums=True, artists=True)

    assert len(fake.playlists) == 3 and len(fake.saved_tracks) == 2
    assert "[dry]" in capsys.readouterr().out
    assert spotify.verify_wipe(snap, playlists=True, favorites=True, albums=True) is True


def test_verify_reports_what_survived(furnished, capsys):
    fake, spotify = furnished
    snap = spotify.snapshot(workers=1)
    spotify.wipe(snap, playlists=False, favorites=True, albums=False)

    assert spotify.verify_wipe(snap, playlists=True, favorites=True, albums=False) is False
    assert "still present" in capsys.readouterr().out


# ------------------------------------------------ le contrat, de bout en bout
def test_a_full_transfer_runs_against_spotify_without_touching_the_engine(state_dir, account):
    """Le meme `transfer()` que pour TIDAL, sur le second service : c'est le
    test de verite du contrat pose au Lot 6."""
    fake, spotify = account(catalog=[
        spotify_track(tid="s1", name="Song A", artist="Artist A", album="Album A",
                      isrc="USUM71703861", album_id="al1", album_upc="123"),
        spotify_track(tid="s2", name="Song B", artist="Artist B", album="Album B",
                      album_id="al2")])
    library = a2t.Library(
        tracks={"1": a2t.Track(name="Song A", artist="Artist A", album="Album A",
                               album_artist="Artist A", duration_ms=200_000, loved=False,
                               isrc="USUM71703861"),
                "2": a2t.Track(name="Song B", artist="Artist B", album="Album B",
                               album_artist="Artist B", duration_ms=200_000, loved=False)},
        playlists=[a2t.Playlist(name="Road trip", track_ids=["1", "2"])],
        albums=[a2t.Album(name="Album A", artist="Artist A", track_count=1, upc="123")])
    store = a2t.Store(state_dir / "spotify")

    cache = a2t.transfer(library, spotify, store,
                         a2t.Options(playlists=True, favorites=True, albums=True, workers=1))

    assert cache["isrc:USUM71703861"]["id"] == "s1"
    assert cache["upc:123"] == {"album_id": "al1"}
    assert [t["id"] for t in fake.playlist_items["new1"]] == ["s1", "s2"]
    assert sorted(t["id"] for t in fake.saved_tracks) == ["s1", "s2"]
    assert [a["id"] for a in fake.saved_albums] == ["al1"]
