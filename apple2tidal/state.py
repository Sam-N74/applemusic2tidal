"""Etat local : le dossier .apple2tidal/ et ce qu'on y ecrit.

Un sous-dossier par service de destination, indexe sur l'identite universelle
(`isrc:`, `q:`, `upc:`) : l'ISRC d'un titre designe le meme titre TIDAL qu'il
vienne d'Apple ou d'ailleurs, un cache par couple source→destination paierait
deux fois les memes recherches. Le rapport des non-trouves reste a la racine :
il decrit une execution, pas un service.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .messages import t
from .model import Track, dedup_key

STATE_DIR = Path(".apple2tidal")
REPORT_FILE = STATE_DIR / "unmatched.csv"


class Store:
    """Session OAuth, cache des matchs et sauvegardes d'un service."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.session_file = directory / "session.json"
        self.cache_file = directory / "matches.json"

    def load_cache(self) -> dict[str, dict]:
        return load_cache(self.cache_file)

    def save_cache(self, cache: dict) -> None:
        save_cache(cache, self.cache_file)

    def backup_path(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / f"backup_{time.strftime('%Y%m%d_%H%M%S')}.json"

    def adopt(self, old_dir: Path, renames: dict[str, str]) -> int:
        """Rapatrie l'etat ecrit a plat par les versions precedentes.

        `renames` associe un nom de fichier dans `old_dir` a son nom ici ; les
        sauvegardes `backup_*.json` suivent sans changer de nom. Un fichier deja
        present ici n'est jamais ecrase. Renvoie le nombre de fichiers deplaces.
        """
        moves = [(old_dir / old, self.dir / new) for old, new in renames.items()]
        moves += [(p, self.dir / p.name) for p in old_dir.glob("backup_*.json")]
        moved = 0
        for src, dst in moves:
            if src.is_file() and not dst.exists():
                self.dir.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
                moved += 1
        if moved:
            print(t("state.moved", n=moved, path=self.dir))
        return moved


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        txt = path.read_text(encoding="utf-8").strip()
        return json.loads(txt) if txt else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(t("cache.unreadable", path=path, error=e))
        return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(path)  # atomique : jamais de fichier à moitié écrit


# Champs ecrits du temps ou TIDAL etait la seule destination possible.
LEGACY_MATCH_FIELDS = {"tidal_id": "id", "tidal_title": "title", "tidal_artist": "artist"}


def migrate_cache(cache: dict, tracks: dict[str, Track]) -> dict:
    """Remet un cache ancien au format courant : d'abord la cle, puis les champs.

    Les deux migrations sont independantes et un cache assez vieux demande les
    deux. Chacune se reconnait a ce qu'elle voit et ne touche rien sinon.
    """
    return _rename_fields(_reindex_by_identity(cache, tracks))


def _reindex_by_identity(cache: dict, tracks: dict[str, Track]) -> dict:
    """Ancien cache indexé par ID Apple -> réindexé par clé d'identité."""
    if not cache or any(k.startswith(("isrc:", "q:", "upc:")) for k in cache):
        return cache
    out, n = {}, 0
    for apple_id, m in cache.items():
        a = tracks.get(apple_id)
        if a:
            out.setdefault(dedup_key(a), m)
            n += 1
    print(t("cache.migrated", n=n, unique=len(out)))
    return out


def _rename_fields(cache: dict) -> dict:
    """`tidal_id` -> `id` : l'identifiant est celui de la destination du jour, et
    il y en a desormais plus d'une. Le cache est indexe sur l'identite
    universelle du titre, pas sur le service : le renommer evite de le jeter.

    Une valeur nulle est une decision — ce titre n'a pas ete trouve — et elle
    survit au renommage, sans quoi la migration relancerait des centaines de
    recherches deja faites.
    """
    stale = [k for k, m in cache.items()
             if isinstance(m, dict) and not LEGACY_MATCH_FIELDS.keys().isdisjoint(m)]
    if not stale:
        return cache
    for k in stale:
        cache[k] = {LEGACY_MATCH_FIELDS.get(f, f): v for f, v in cache[k].items()}
    print(t("cache.renamed", n=len(stale)))
    return cache
