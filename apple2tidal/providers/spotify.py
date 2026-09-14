"""Spotify, source *et* destination, via l'API officielle.

Seul module qui parle a Spotify. Trois pieces, de la plus basse a la plus
haute : `SpotifyAuth` tient le jeton, `SpotifyApi` fait les requetes, `Spotify`
traduit le JSON en modele du moteur. C'est cette couture qui rend les tests
possibles sans reseau — l'adaptateur recoit son API, la vraie ou une fausse —
et elle sert aussi a debugger : une panne se situe dans une des trois.

L'autorisation est un Authorization Code avec PKCE, redirige vers un serveur
local ephemere. Pas de secret client, donc rien de sensible dans le depot.
Spotify autorise explicitement `http://127.0.0.1:<port>` en HTTP, refuse
`localhost`, et accepte qu'une redirection enregistree sans port en recoive un
au moment de la demande : c'est ce qui permet de prendre un port libre au
hasard plutot que d'en imposer un.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..messages import t
from ..model import Album, Candidate, Library, Playlist, Track
from ..state import Store

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

CLIENT_ID_ENV = "APPLE2TIDAL_SPOTIFY_CLIENT_ID"
AUTH_TIMEOUT = 300.0        # au-dela, l'utilisateur a ferme l'onglet
PAGE = 50                   # maximum accepte par /me/tracks, /me/albums, /playlists/…/items
SEARCH_LIMIT = 10           # maximum accepte par /search
LIBRARY_CHUNK = 40          # maximum accepte par /me/library
PLAYLIST_CHUNK = 100        # maximum accepte par /playlists/…/items

# Tout ce que l'outil sait faire, demande en une fois : une autorisation
# partielle produirait des 403 au milieu d'un transfert, apres des ecritures.
SCOPES = [
    "user-library-read", "user-library-modify",
    "playlist-read-private", "playlist-read-collaborative",
    "playlist-modify-private", "playlist-modify-public",
    "user-follow-read", "user-follow-modify",
]


class SpotifyError(RuntimeError):
    """Une reponse que l'appelant ne peut pas exploiter."""


# --------------------------------------------------------------------------- #
#  PKCE et redirection locale
# --------------------------------------------------------------------------- #
def make_verifier() -> str:
    """Secret ephemere, 43 a 128 caracteres de l'alphabet autorise (RFC 7636)."""
    return secrets.token_urlsafe(64)[:128]


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def callback_handler(holder: dict, expected: dict):
    """Fabrique le handler qui recoit la redirection et n'en garde que la query.

    L'hote est verifie : un navigateur redirige vers 127.0.0.1 envoie
    cet hote, une page tierce qui viserait ce port par un nom de domaine
    resolu sur la boucle locale en enverrait un autre. Le code d'autorisation
    passe par cette URL, il ne doit repondre qu'a la redirection attendue.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802  (nom impose par http.server)
            if self.headers.get("Host") != expected.get("host"):
                self.send_error(403)
                return
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            answered = "code" in query or "error" in query
            if answered:
                holder.update(query)
            body = ("<!doctype html><meta charset=\"utf-8\"><title>apple2tidal</title>"
                    "<p style=\"font:16px system-ui;padding:2em\">"
                    + t("spotify.callback_page") + "</p>").encode("utf-8")
            self.send_response(200 if answered else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass  # le serveur ne vit que quelques secondes : pas de bruit

    return Handler


def read_client_id(store: Store, given: str | None = None) -> str:
    """Client ID de l'app Spotify : argument, variable d'environnement, fichier.

    Il n'y en a pas d'embarque dans le depot, et ce n'est pas un oubli : une
    app Spotify neuve est en mode developpement, limitee a cinq comptes que son
    proprietaire inscrit un par un. Un identifiant partage ne servirait donc
    qu'a cinq personnes, et le premier inscrit deciderait pour les suivants.
    """
    if given:
        return given.strip()
    from_env = os.environ.get(CLIENT_ID_ENV)
    if from_env and from_env.strip():
        return from_env.strip()
    app_file = store.dir / "app.json"
    if app_file.is_file():
        declared = json.loads(app_file.read_text(encoding="utf-8")).get("client_id")
        if declared:
            return str(declared).strip()
    print(t("spotify.no_client_id"))
    print(t("spotify.client_id_step1"))
    print(t("spotify.client_id_step2"))
    print(t("spotify.client_id_step3", env=CLIENT_ID_ENV, path=app_file))
    sys.exit(1)


class SpotifyAuth:
    """Le jeton : obtenu par PKCE, garde sur disque, rafraichi quand il expire.

    `opener` et `post` sont injectes pour que les tests exercent le flux sans
    navigateur ni reseau. Le flux lui-meme n'est pas contourne : c'est le meme
    code qui tourne des deux cotes.
    """

    def __init__(self, store: Store, client_id: str | None = None,
                 opener=webbrowser.open, post=requests.post):
        self.store = store
        self.store.dir.mkdir(parents=True, exist_ok=True)
        self.client_id = read_client_id(store, client_id)
        self.opener = opener
        self.post = post
        self._session: dict | None = None
        self._lock = threading.Lock()

    # -- surface publique
    def token(self) -> str:
        """Un jeton valide, quitte a rafraichir ou a redemander l'autorisation."""
        with self._lock:
            data = self._session or self._load()
            if not data or not self._scopes_cover(data):
                data = self._authorize()
            elif data.get("expires_at", 0.0) <= time.time() + 60:
                data = self._refresh(data)
            self._session = data
            return data["access_token"]

    def forget(self) -> None:
        """Oublie le jeton en memoire : le prochain appel le redemande. Sert
        quand le service repond 401 alors qu'on le croyait encore valide."""
        with self._lock:
            self._session = None
            if self.store.session_file.exists():
                data = self._load()
                data.pop("expires_at", None)
                self._write(data)

    # -- disque
    def _load(self) -> dict:
        if not self.store.session_file.is_file():
            return {}
        try:
            return json.loads(self.store.session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _write(self, data: dict) -> dict:
        self.store.dir.mkdir(parents=True, exist_ok=True)
        self.store.session_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            self.store.session_file.chmod(0o600)   # sans effet sur Windows, utile ailleurs
        except OSError:
            pass
        return data

    def _scopes_cover(self, data: dict) -> bool:
        """Une version qui demande un droit de plus doit redemander l'accord :
        sinon le transfert casse au milieu, sur un 403."""
        return set(SCOPES) <= set((data.get("scope") or "").split())

    # -- flux
    def _authorize(self) -> dict:
        verifier = make_verifier()
        expected_state = secrets.token_urlsafe(16)
        holder: dict[str, str] = {}
        # le port n'existe qu'une fois le socket ouvert, et le handler doit le
        # connaitre : on le lui passe par un dictionnaire rempli juste apres.
        expected: dict[str, str] = {}
        server = HTTPServer(("127.0.0.1", 0), callback_handler(holder, expected))
        expected["host"] = f"127.0.0.1:{server.server_port}"
        server.timeout = 1.0
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = AUTH_URL + "?" + urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "state": expected_state,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": make_challenge(verifier),
        })
        print(t("spotify.opening_browser"))
        print(t("spotify.waiting_callback", redirect=redirect))
        print("   " + url)
        self.opener(url)

        deadline = time.monotonic() + AUTH_TIMEOUT
        try:
            while not holder and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if not holder:
            sys.exit(t("spotify.auth_timeout", seconds=AUTH_TIMEOUT))
        if holder.get("error"):
            sys.exit(t("spotify.auth_failed", error=holder["error"]))
        if not secrets.compare_digest(holder.get("state", ""), expected_state):
            sys.exit(t("spotify.auth_failed", error=t("spotify.state_mismatch")))
        return self._exchange({
            "grant_type": "authorization_code",
            "code": holder["code"],
            "redirect_uri": redirect,
            "client_id": self.client_id,
            "code_verifier": verifier,
        })

    def _refresh(self, data: dict) -> dict:
        if not data.get("refresh_token"):
            return self._authorize()
        return self._exchange({
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": self.client_id,
        })

    def _exchange(self, form: dict) -> dict:
        r = self.post(TOKEN_URL, data=form, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            sys.exit(t("spotify.token_failed", status=r.status_code,
                       body=(r.text or "")[:200]))
        got = r.json()
        return self._write({
            "access_token": got["access_token"],
            # une reponse de rafraichissement peut ne pas en renvoyer un neuf :
            # on garde alors celui qu'on avait, sinon la session se perd.
            "refresh_token": got.get("refresh_token") or form.get("refresh_token", ""),
            "expires_at": time.time() + float(got.get("expires_in") or 3600),
            "scope": got.get("scope") or " ".join(SCOPES),
            "client_id": self.client_id,
        })


# --------------------------------------------------------------------------- #
#  Transport
# --------------------------------------------------------------------------- #
class SpotifyApi:
    """Les requetes : jeton pose, 429 respectes, pagination suivie.

    Le backoff est partage entre les threads, comme cote TIDAL : Spotify compte
    les appels sur une fenetre glissante de 30 secondes, donc huit threads qui
    se font refuser chacun de leur cote continueraient a taper pendant que le
    premier attend.
    """

    def __init__(self, auth: SpotifyAuth, delay: float = 0.0, session=None):
        self.auth = auth
        self.delay = delay
        self.http = session if session is not None else requests.Session()
        self._lock = threading.Lock()
        self._pause_until = 0.0

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None, retries: int = 5):
        url = path if path.startswith("http") else API_BASE + path
        for i in range(retries):
            wait_for = self._pause_until - time.monotonic()
            if wait_for > 0:
                time.sleep(wait_for)
            r = self.http.request(method, url, params=params, json=body, timeout=30,
                                  headers={"Authorization": "Bearer " + self.auth.token()})
            if self.delay:
                time.sleep(self.delay)
            if r.status_code == 429:
                pause = float(r.headers.get("Retry-After") or 2 ** i)
                with self._lock:
                    if self._pause_until - time.monotonic() < pause:
                        self._pause_until = time.monotonic() + pause
                        print(t("net.rate_limit_pause", seconds=pause))
                continue
            if r.status_code == 401 and i == 0:
                self.auth.forget()      # jeton revoque : une seule nouvelle tentative
                continue
            if r.status_code >= 500 and i < retries - 1:
                time.sleep(2 ** i)
                continue
            if r.status_code >= 400:
                raise SpotifyError(t("spotify.http_error", status=r.status_code,
                                     method=method, path=path))
            return r.json() if r.content else {}
        raise SpotifyError(t("spotify.http_error", status=429, method=method, path=path))

    def get(self, path: str, **params):
        return self.request("GET", path, params=params or None)

    def paginate(self, path: str, root: str | None = None, **params) -> list:
        """Toutes les pages d'une collection. `root` designe l'objet de pagination
        quand il est imbrique (`/me/following` le range sous `artists`)."""
        out: list = []
        url: str | None = None
        while True:
            raw = self.request("GET", url) if url else self.get(path, limit=PAGE, **params)
            page = (raw.get(root) if root else raw) or {}
            out.extend(x for x in (page.get("items") or []) if x)
            url = page.get("next")
            if not url:
                return out


# --------------------------------------------------------------------------- #
#  L'adaptateur
# --------------------------------------------------------------------------- #
def _candidate(item: dict) -> Candidate:
    album = item.get("album") or {}
    return Candidate(
        id=item["id"],
        name=item.get("name") or "",
        artists=[a.get("name") or "" for a in (item.get("artists") or [])],
        album=album.get("name") or "",
        album_id=album.get("id"),
        duration_s=(item.get("duration_ms") or 0) / 1000 or None,
        popularity=item.get("popularity") or 0,
    )


def _track(item: dict) -> Track:
    album = item.get("album") or {}
    artists = [a.get("name") or "" for a in (item.get("artists") or [])]
    album_artists = [a.get("name") or "" for a in (album.get("artists") or [])]
    return Track(
        name=item.get("name") or "",
        artist=artists[0] if artists else "",
        album=album.get("name") or "",
        album_artist=(album_artists or artists or [""])[0],
        duration_ms=int(item.get("duration_ms") or 0),
        loved=False,
        year=_year(album.get("release_date")),
        isrc=(item.get("external_ids") or {}).get("isrc") or None,
    )


def _year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    head = str(release_date)[:4]
    return int(head) if head.isdigit() else None


class Spotify:
    """Source et destination : le meme service des deux cotes, donc une classe.

    Cote lecture, Spotify a ce qui manque a l'export XML d'Apple : l'ISRC sur
    chaque titre et l'UPC sur chaque album, donc le pivot exact du moteur.
    Cote ecriture, une particularite qui n'existe pas chez TIDAL : posseder une
    playlist et la suivre sont le meme lien, donc la retirer de la
    bibliotheque ne la detruit pas. C'est dit au moment de le faire.
    """

    name = "Spotify"

    def __init__(self, store: Store, dry_run: bool = False, delay: float = 0.0,
                 workers: int = 8, api: SpotifyApi | None = None,
                 client_id: str | None = None):
        self.store = store
        self.dry = dry_run
        self.workers = workers
        store.dir.mkdir(parents=True, exist_ok=True)
        self.api = api if api is not None else SpotifyApi(
            SpotifyAuth(store, client_id), delay=delay)
        me = self.api.get("/me")
        self.user_id = me.get("id") or ""
        print(t("spotify.connected", user=me.get("display_name") or self.user_id))

    # ----------------------------------------------------------------- Source
    def read(self) -> Library:
        """La bibliotheque : titres aimes, playlists possedees, albums sauvegardes.

        Seules les playlists dont l'utilisateur est proprietaire ou
        collaborateur sont lues, parce que Spotify refuse les autres : le
        contenu d'une playlist qu'on ne fait que suivre repond 403.
        """
        print(t("spotify.reading"))
        tracks: dict[str, Track] = {}
        for saved in self.api.paginate("/me/tracks"):
            self._remember(tracks, saved.get("track"), loved=True)

        playlists: list[Playlist] = []
        for raw in self._my_playlists():
            ids: list[str] = []
            for entry in self.api.paginate(f"/playlists/{raw['id']}/items"):
                key = self._remember(tracks, entry.get("track") or entry.get("item"))
                if key:
                    ids.append(key)
            if ids:
                playlists.append(Playlist(name=raw.get("name") or "", track_ids=ids))

        albums = [
            Album(name=(saved.get("album") or {}).get("name") or "",
                  artist=next((a.get("name") or "" for a in
                               ((saved.get("album") or {}).get("artists") or [])), ""),
                  track_count=int((saved.get("album") or {}).get("total_tracks") or 0),
                  upc=((saved.get("album") or {}).get("external_ids") or {}).get("upc"))
            for saved in self.api.paginate("/me/albums")
            if (saved.get("album") or {}).get("name")
        ]
        return Library(tracks, playlists, albums)

    def _remember(self, tracks: dict[str, Track], item: dict | None,
                  loved: bool = False) -> str | None:
        """Range un titre sous son identifiant Spotify. Les fichiers locaux et
        les episodes de podcast n'ont rien a faire dans un transfert."""
        if not item or item.get("is_local") or not item.get("id"):
            return None
        if item.get("type") not in (None, "track"):
            return None
        key = str(item["id"])
        known = tracks.get(key)
        if known is None:
            known = tracks[key] = _track(item)
        known.loved = known.loved or loved
        return key

    def _my_playlists(self) -> list[dict]:
        return [p for p in self.api.paginate("/me/playlists")
                if (p.get("owner") or {}).get("id") == self.user_id
                or p.get("collaborative")]

    # ------------------------------------------------------------- catalogue
    def tracks_by_isrc(self, isrc: str) -> list[Candidate]:
        return self._search_tracks(f"isrc:{isrc}")

    def search_tracks(self, query: str) -> list[Candidate]:
        return self._search_tracks(query)

    def _search_tracks(self, query: str) -> list[Candidate]:
        try:
            found = self.api.get("/search", q=query, type="track", limit=SEARCH_LIMIT)
        except (SpotifyError, requests.RequestException) as e:
            print(t("search.error", query=query, error=e))
            return []
        items = ((found.get("tracks") or {}).get("items")) or []
        return [_candidate(x) for x in items
                if x and x.get("id") and x.get("is_playable") is not False]

    def album_by_upc(self, upc: str) -> str | None:
        """Album portant cet UPC, ou None. Un UPC absent du catalogue n'est pas
        une panne : la recherche repond simplement une liste vide."""
        try:
            found = self.api.get("/search", q=f"upc:{upc}", type="album", limit=1)
        except (SpotifyError, requests.RequestException):
            return None
        items = ((found.get("albums") or {}).get("items")) or []
        return items[0].get("id") if items else None

    # --------------------------------------------------------------- ecriture
    def existing_playlists(self) -> dict[str, list[dict]]:
        """Playlists du compte, groupees par nom. Seules celles qu'on possede :
        on ne peut pas vider celle de quelqu'un d'autre, et --overwrite ne doit
        pas croire qu'il le pourrait."""
        out: dict[str, list[dict]] = {}
        for p in self._my_playlists():
            out.setdefault(p.get("name") or "", []).append(p)
        return out

    def create_playlist(self, name: str, desc: str, track_ids: list[str],
                        existing: dict, overwrite: bool) -> None:
        if self.dry:
            print(t("playlist.dry", name=name, n=len(track_ids)))
            return
        uris = [f"spotify:track:{x}" for x in track_ids]
        same_name = existing.get(name) or []
        if len(same_name) > 1:
            print(t("playlist.ambiguous", n=len(same_name), name=name))
            return
        if same_name and not overwrite:
            print(t("playlist.exists", name=name))
            return
        if same_name:
            playlist_id = same_name[0]["id"]
            # remplacer ecrase le contenu : c'est ce que --overwrite veut dire,
            # et ca evite de vider puis remplir en deux temps.
            self.api.request("PUT", f"/playlists/{playlist_id}/items",
                             body={"uris": uris[:PLAYLIST_CHUNK]})
            rest = uris[PLAYLIST_CHUNK:]
        else:
            created = self.api.request("POST", "/me/playlists", body={
                "name": name, "description": desc, "public": False})
            playlist_id = created["id"]
            rest = uris
        for i in range(0, len(rest), PLAYLIST_CHUNK):
            self.api.request("POST", f"/playlists/{playlist_id}/items",
                             body={"uris": rest[i:i + PLAYLIST_CHUNK]})
        print(t("playlist.created", name=name, n=len(uris)))

    def favorite_tracks(self, ids: list[str]) -> None:
        if self.dry:
            print(t("favorites.dry_tracks", n=len(ids)))
            return
        self._library("PUT", [f"spotify:track:{x}" for x in ids],
                      t("label.favorites_added"))

    def favorite_albums(self, ids: list[str]) -> None:
        if self.dry:
            print(t("favorites.dry_albums", n=len(ids)))
            return
        self._library("PUT", [f"spotify:album:{x}" for x in ids],
                      t("label.albums_added"))

    def _library(self, method: str, uris: list[str], label: str) -> int:
        """Ajoute ou retire des URI de la bibliotheque, par paquets de 40.

        Un seul endpoint couvre titres, albums, artistes et playlists, et il
        est idempotent : reposer un titre deja sauvegarde ne le double pas, ce
        qui evite de relire toute la bibliotheque avant d'ecrire.
        """
        if not uris:
            print(t("favorites.nothing"))
            return 0
        done = 0
        for i in range(0, len(uris), LIBRARY_CHUNK):
            chunk = uris[i:i + LIBRARY_CHUNK]
            try:
                self.api.request(method, "/me/library", params={"uris": ",".join(chunk)})
                done += len(chunk)
            except (SpotifyError, requests.RequestException) as e:
                print(t("parallel.item_error", label=label, item=len(chunk), error=e))
            print(t("parallel.progress", label=label, done=done, total=len(uris)), end="\r")
        print(t("parallel.done", label=label, done=done, total=len(uris)))
        return done

    # ----------------------------------------------------------- suppressions
    def snapshot(self, workers: int = 8) -> dict:
        """Sauvegarde lisible du compte, avant toute suppression.

        Meme forme que chez les autres services — c'est la ligne de commande
        qui la lit et la compte, elle n'a pas a savoir a qui elle parle.
        """
        raw = self.api.paginate("/me/playlists")
        mine = {p["id"] for p in raw
                if (p.get("owner") or {}).get("id") == self.user_id or p.get("collaborative")}
        items_of: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as ex:
            futures = {ex.submit(self.api.paginate, f"/playlists/{p['id']}/items"): p["id"]
                       for p in raw if p["id"] in mine}
            for f in as_completed(futures):
                try:
                    items_of[futures[f]] = f.result()
                except Exception:  # noqa: BLE001
                    items_of[futures[f]] = []

        data = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "playlists": [
                {"id": p["id"], "name": p.get("name") or "",
                 "num_tracks": (p.get("items") or p.get("tracks") or {}).get("total", 0),
                 "url": (p.get("external_urls") or {}).get("spotify"),
                 "own": p["id"] in mine,
                 "tracks": [{"id": (e.get("track") or {}).get("id"),
                             "name": (e.get("track") or {}).get("name") or "",
                             "artist": next((a.get("name") or "" for a in
                                             ((e.get("track") or {}).get("artists") or [])), "")}
                            for e in items_of.get(p["id"], []) if e.get("track")]}
                for p in raw
            ],
            "favorite_tracks": [{"id": (s.get("track") or {}).get("id"),
                                 "name": (s.get("track") or {}).get("name") or "",
                                 "artist": next((a.get("name") or "" for a in
                                                 ((s.get("track") or {}).get("artists") or [])), "")}
                                for s in self.api.paginate("/me/tracks") if s.get("track")],
            "favorite_albums": [{"id": (s.get("album") or {}).get("id"),
                                 "name": (s.get("album") or {}).get("name") or "",
                                 "artist": next((a.get("name") or "" for a in
                                                 ((s.get("album") or {}).get("artists") or [])), "")}
                                for s in self.api.paginate("/me/albums") if s.get("album")],
            "favorite_artists": [{"id": a.get("id"), "name": a.get("name") or ""}
                                 for a in self.api.paginate("/me/following", root="artists",
                                                            type="artist")],
            "followed_playlists": [{"id": p["id"], "name": p.get("name") or ""}
                                   for p in raw if p["id"] not in mine],
        }
        path = self.store.backup_path()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"path": path, **data}

    def wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
             only_names: set[str] | None = None, artists: bool = False,
             followed: bool = False) -> None:
        if playlists:
            targets = [p for p in snap["playlists"]
                       if p["own"] and (only_names is None or p["name"] in only_names)]
            if self.dry:
                for p in targets:
                    print(t("wipe.dry_playlist", name=p["name"], n=p["num_tracks"]))
            elif targets:
                print(t("spotify.playlists_are_unfollowed"))
                self._library("DELETE", [f"spotify:playlist:{p['id']}" for p in targets],
                              t("label.playlists_removed"))
                for p in targets:
                    print(t("wipe.deleted_playlist", name=p["name"]))
        self._remove_if("track", snap.get("favorite_tracks"), favorites,
                        "wipe.dry_favorites", t("label.favorites_removed"))
        self._remove_if("album", snap.get("favorite_albums"), albums,
                        "wipe.dry_albums", t("label.albums_removed"))
        self._remove_if("artist", snap.get("favorite_artists"), artists,
                        "wipe.dry_artists", t("label.artists_removed"))
        self._remove_if("playlist", snap.get("followed_playlists"), followed,
                        "wipe.dry_followed", t("label.playlists_unfollowed"))

    def _remove_if(self, kind: str, entries: list | None, asked: bool,
                   dry_key: str, label: str) -> None:
        if not asked:
            return
        ids = [e["id"] for e in (entries or []) if e.get("id")]
        if self.dry:
            print(t(dry_key, n=len(ids)))
            return
        if ids:
            self._library("DELETE", [f"spotify:{kind}:{x}" for x in ids], label)

    def verify_wipe(self, snap: dict, playlists: bool, favorites: bool, albums: bool,
                    only_names: set[str] | None = None) -> bool:
        """Relit le compte apres suppression et signale ce qui reste."""
        if self.dry:
            return True
        ok = True
        if playlists:
            expected = {p["name"] for p in snap["playlists"]
                        if p["own"] and (only_names is None or p["name"] in only_names)}
            left = [p.get("name") or "" for p in self._my_playlists()
                    if (p.get("name") or "") in expected]
            if left:
                ok = False
                print(t("verify.playlists_left", n=len(left), names=", ".join(left[:5]))
                      + (" …" if len(left) > 5 else ""))
        if favorites:
            n = len(self.api.paginate("/me/tracks"))
            if n:
                ok = False
                print(t("verify.tracks_left", n=n))
        if albums:
            n = len(self.api.paginate("/me/albums"))
            if n:
                ok = False
                print(t("verify.albums_left", n=n))
        print(t("verify.ok") if ok else t("verify.failed"))
        return ok

