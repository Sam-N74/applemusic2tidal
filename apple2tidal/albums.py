"""Albums : regroupement par album, resolution par UPC ou par les titres deja matches."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .model import Album, Track, dedup_key, norm

AlbumId = int | str
UpcLookup = Callable[[str], AlbumId | None]


def tracks_by_album(tracks: dict[str, Track]) -> dict[tuple[str, str], list[Track]]:
    """Regroupe les titres par (artiste de l'album, album). L'artiste de l'album,
    pas celui du titre : sinon une compilation eclate en autant d'albums."""
    out: dict[tuple[str, str], list[Track]] = defaultdict(list)
    for a in tracks.values():
        if a.album:
            out[(norm(a.album_artist), norm(a.album))].append(a)
    return out


def _majority_album_id(ts: list[Track], cache: dict) -> AlbumId | None:
    """Album majoritaire chez la destination parmi les titres deja matches, ou None si egalite."""
    ids = [x for x in (cache.get(dedup_key(a), {}).get("album_id") for a in ts) if x]
    if not ids:
        return None
    top = max(set(ids), key=ids.count)
    return top if ids.count(top) * 2 > len(ids) else None


def resolve_albums(declared: list[Album], tracks: dict[str, Track],
                   cache: dict, upc_lookup: UpcLookup, workers: int = 1
                   ) -> list[tuple[Album, AlbumId, str]]:
    """Associe chaque album declare par la source a un album de la destination.

    Deux chemins : l'UPC quand la source le fournit — exact, et il retrouve aussi
    les albums dont on ne possede qu'une partie des titres — sinon l'album
    majoritaire parmi les titres matches. `upc_lookup` est injecte pour que cette
    fonction reste testable sans compte.

    Les UPC sont cherches d'abord, dedoublonnes et en parallele : une
    bibliotheque en compte des centaines et chacun est une requete.

    Renvoie [(album, id chez la destination, "upc" | "tracks")].
    """
    upcs = sorted({al.upc for al in declared if al.upc})
    if workers > 1 and len(upcs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            by_upc = dict(zip(upcs, ex.map(upc_lookup, upcs), strict=True))
    else:
        by_upc = {u: upc_lookup(u) for u in upcs}

    grouped = tracks_by_album(tracks)
    out: list[tuple[Album, AlbumId, str]] = []
    seen: set[AlbumId] = set()
    for al in declared:
        album_id, source = (by_upc.get(al.upc) if al.upc else None), "upc"
        if album_id is None:
            album_id, source = _majority_album_id(grouped.get(al.key, []), cache), "tracks"
        if album_id and album_id not in seen:
            seen.add(album_id)
            out.append((al, album_id, source))
    return out


def guess_albums(tracks: dict[str, Track], cache: dict) -> list[tuple[Album, AlbumId, str]]:
    """Repli pour une source qui ne declare aucun album (export XML) : on les
    devine a partir des titres, avec le meme garde-fou qu'avant — au moins 3
    titres dans la bibliotheque et 80 % d'entre eux matches."""
    out: list[tuple[Album, AlbumId, str]] = []
    seen: set[AlbumId] = set()
    for ts in tracks_by_album(tracks).values():
        if len(ts) < 3:
            continue
        matched = [a for a in ts if cache.get(dedup_key(a), {}).get("album_id")]
        if len(matched) / len(ts) < 0.8:
            continue
        album_id = _majority_album_id(matched, cache)
        if album_id and album_id not in seen:
            seen.add(album_id)
            out.append((Album(name=ts[0].album, artist=ts[0].album_artist,
                              track_count=len(ts)), album_id, "tracks"))
    return out


def cached_upc_lookup(cache: dict, lookup: UpcLookup, rematch: bool = False) -> UpcLookup:
    """Enveloppe `lookup` d'une memoire disque, sous le prefixe `upc:`.

    Une bibliotheque declare des centaines d'UPC et chacun est une requete, alors
    que le catalogue ne bouge pas d'une execution a l'autre. L'absence est
    memorisee comme le reste : c'est elle qui coute le plus cher. `--rematch`
    rejoue les absences, au cas ou l'album soit arrive depuis.

    L'ecriture se fait depuis plusieurs threads, mais sur des cles distinctes, et
    poser une cle est atomique : pas de verrou a prendre.
    """
    def resolve(upc: str) -> AlbumId | None:
        key = "upc:" + upc
        entry = cache.get(key)
        if entry is not None and not (rematch and entry.get("album_id") is None):
            return entry.get("album_id")
        album_id = lookup(upc)
        cache[key] = {"album_id": album_id}
        return album_id
    return resolve
