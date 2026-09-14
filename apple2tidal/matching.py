"""Le moteur de matching : scorer un candidat, en choisir un, decider si le cache suffit.

Rien ici ne connait un service. La destination n'est vue qu'a travers ses deux
methodes de catalogue, `tracks_by_isrc` et `search_tracks`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import Candidate, Match, Track, clean_artist, clean_title, norm

if TYPE_CHECKING:
    from .providers import Destination

try:
    from rapidfuzz import fuzz
except ImportError:  # fallback stdlib
    import difflib

    class fuzz:  # type: ignore
        @staticmethod
        def token_set_ratio(a: str, b: str) -> float:
            sa, sb = set(a.split()), set(b.split())
            inter = " ".join(sorted(sa & sb))
            return 100 * max(
                difflib.SequenceMatcher(None, inter, " ".join(sorted(sa))).ratio(),
                difflib.SequenceMatcher(None, inter, " ".join(sorted(sb))).ratio(),
                difflib.SequenceMatcher(None, a, b).ratio(),
            )

        @staticmethod
        def ratio(a: str, b: str) -> float:
            return 100 * difflib.SequenceMatcher(None, a, b).ratio()


# Score minimal pour accepter un match fuzzy. En dessous, le titre part dans unmatched.csv.
DEFAULT_THRESHOLD = 78.0


def score_candidate(a: Track, cand: Candidate) -> float:
    title_s = fuzz.token_set_ratio(norm(clean_title(a.name)), norm(clean_title(cand.name)))
    artist_s = max(
        fuzz.token_set_ratio(norm(a.artist), norm(" ".join(cand.artists))),
        fuzz.token_set_ratio(norm(clean_artist(a.artist)), norm(clean_artist(cand.artist))),
    )
    album_s = fuzz.token_set_ratio(norm(clean_title(a.album)), norm(clean_title(cand.album)))

    score = 0.55 * title_s + 0.35 * artist_s + 0.10 * album_s
    # Pénalité durée : > 5 s d'écart pénalise, > 20 s disqualifie quasi
    if a.duration_ms and cand.duration_s:
        diff = abs(a.duration_ms / 1000 - cand.duration_s)
        if diff > 20:
            score -= 25
        elif diff > 5:
            score -= 8
    # Bonus si titre exact
    if norm(a.name) == norm(cand.name):
        score += 3
    return score


def match(a: Track, dest: Destination, threshold: float) -> Match:
    # 1) ISRC : exact
    if a.isrc:
        cands = dest.tracks_by_isrc(a.isrc)
        if cands:
            cand = max(cands, key=lambda x: (score_candidate(a, x), x.popularity or 0))
            return Match(cand.id, 100.0, cand.name, cand.artist, cand.album_id, threshold)
    # 2) recherche fuzzy
    queries = [
        f"{clean_artist(a.artist)} {clean_title(a.name)}",
        f"{a.artist} {a.name}",
        f"{clean_title(a.name)} {clean_artist(a.album_artist)}",
        clean_title(a.name),
    ]
    seen: set[int | str] = set()
    best: tuple[float, Candidate] | None = None
    for q in dict.fromkeys(q.strip() for q in queries if q.strip()):
        for cand in dest.search_tracks(q):
            if cand.id in seen:
                continue
            seen.add(cand.id)
            s = score_candidate(a, cand)
            if best is None or s > best[0]:
                best = (s, cand)
        if best and best[0] >= 92:  # assez bon, inutile de continuer
            break
    if best and best[0] >= threshold:
        s, cand = best
        return Match(cand.id, round(s, 1), cand.name, cand.artist, cand.album_id, threshold)
    return Match(None, round(best[0], 1) if best else 0.0, threshold=threshold)


def needs_match(entry: dict | None, threshold: float, rematch: bool) -> bool:
    """Faut-il (re)chercher ce titre chez la destination ?

    Une entree de cache porte une decision — accepte ou non — prise sous un seuil
    donne. Si le seuil courant renversait cette decision, l'entree est perimee :
    remonter --threshold doit vraiment durcir le tri, le baisser doit vraiment
    rouvrir les titres refuses de peu. Un cache anterieur a ce champ se juge sur
    son score, qui suffit a trancher.
    """
    if not entry:
        return True
    matched = entry.get("id") is not None
    if rematch and not matched:
        return True
    return matched != (entry.get("score", 0.0) >= threshold)
