"""Modele neutre : ce que le moteur manipule, sans identifiant d'aucun service.

Un `Track` n'a pas d'identifiant : celui de la source est la cle du dictionnaire
qui le porte, et la cle d'identite universelle (`dedup_key`) est l'ISRC ou, a
defaut, le couple artiste|titre normalise. Un `Candidate` est un titre du
catalogue de la destination, reduit aux champs que le scoring lit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class Track:
    name: str
    artist: str
    album: str
    album_artist: str
    duration_ms: int
    loved: bool
    year: int | None = None
    isrc: str | None = None


@dataclass
class Playlist:
    name: str
    track_ids: list[str] = field(default_factory=list)   # cles du dict de titres de la source
    smart: bool = False


@dataclass
class Album:
    """Album declare par la source. L'UPC identifie la sortie, comme l'ISRC un titre."""
    name: str
    artist: str
    track_count: int = 0
    upc: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (norm(self.artist), norm(self.name))


@dataclass
class Library:
    """Ce qu'une source livre au moteur, en une lecture."""
    tracks: dict[str, Track] = field(default_factory=dict)
    playlists: list[Playlist] = field(default_factory=list)
    albums: list[Album] = field(default_factory=list)    # vide si la source n'en declare pas


@dataclass
class Candidate:
    """Un titre du catalogue de la destination, vu par le moteur."""
    id: int | str
    name: str
    artists: list[str]
    album: str = ""
    album_id: int | str | None = None
    duration_s: float | None = None
    popularity: int = 0

    @property
    def artist(self) -> str:
        return self.artists[0] if self.artists else ""


@dataclass
class Match:
    """Decision prise pour un titre chez la destination. Les champs ne nomment
    aucun service : le cache qui les porte est indexe sur l'identite du titre,
    et la destination change d'une execution a l'autre."""
    id: int | str | None
    score: float
    title: str = ""
    artist: str = ""
    album_id: int | str | None = None
    threshold: float = 0.0   # seuil sous lequel la decision a ete prise


# --------------------------------------------------------------------------- #
#  Normalisation
# --------------------------------------------------------------------------- #
_PAREN_JUNK = re.compile(
    r"\s*[\(\[\-–—]\s*(feat\.?|ft\.?|featuring|remaster(ed)?|\d{4} remaster|mono|stereo|"
    r"live|edit|radio edit|single version|album version|deluxe|bonus track|explicit|"
    r"clean|original mix|extended|version|remix)[^\)\]]*[\)\]]?\s*$",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", "and").replace("’", "'")
    s = re.sub(r"[^\w\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s: str) -> str:
    prev = None
    while prev != s:
        prev, s = s, _PAREN_JUNK.sub("", s)
    return s.strip()


def clean_artist(s: str) -> str:
    s = _FEAT.sub("", s)
    return re.split(r"\s*[,&/]\s*|\s+x\s+", s)[0].strip() or s


def dedup_key(a: Track) -> str:
    """Cle d'identite d'un titre : deux entrees identiques = une seule recherche."""
    if a.isrc:
        return "isrc:" + a.isrc.upper()
    return "q:" + norm(clean_artist(a.artist)) + "|" + norm(clean_title(a.name))
