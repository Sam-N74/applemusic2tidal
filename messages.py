"""Messages affichés par apple2tidal, en anglais et en français.

Deux dictionnaires plats, une fonction `t("clé", **params)`. Pas de gettext :
l'outil tient dans un fichier, les chaînes se relisent d'un coup d'œil et une
traduction supplémentaire ne demande qu'un dictionnaire de plus.

La langue se résout dans cet ordre : `--lang`, puis `APPLE2TIDAL_LANG`, puis
l'anglais. tests/test_messages.py garantit que les dictionnaires restent
alignés, clés et paramètres.
"""

from __future__ import annotations

import os
import sys

ENV_VAR = "APPLE2TIDAL_LANG"
DEFAULT_LANG = "en"

EN: dict[str, str] = {
    # ---------------------------------------------------------------- argparse
    "cli.description": "Apple Music → TIDAL",
    "cli.help.library": "Export .xml (Music app) or .json (export_apple_music.js). "
                        "Not needed with --wipe.",
    "cli.help.playlists": "Recreate the playlists",
    "cli.help.favorites": "Whole library → TIDAL favorite tracks",
    "cli.help.loved": "Only 'loved' tracks → favorites",
    "cli.help.albums": "Albums listed by the export → favorite albums (matched by UPC; "
                       "guessed from the tracks when the export lists none)",
    "cli.help.all": "= --playlists --favorites --albums",
    "cli.help.only": "Only process these playlist name(s)",
    "cli.help.skip_smart": "Skip smart playlists",
    "cli.help.overwrite": "Clear and recreate existing playlists",
    "cli.help.wipe": "DESTRUCTIVE: wipes the TIDAL account entirely and stops (no import)",
    "cli.help.keep_followed": "With --wipe: keep the playlists you follow from other users",
    "cli.help.reset": "DESTRUCTIVE: wipes the TIDAL account (playlists you created + favorites) "
                      "before importing",
    "cli.help.reset_scope": "imported (default) = only delete playlists named after an Apple "
                            "playlist; all = delete every playlist you own",
    "cli.help.yes": "Skip the confirmation prompt for --reset",
    "cli.help.threshold": "Minimum match score (0-100), default {default:.0f}",
    "cli.help.rematch": "Ignore the cache and search again for the unmatched tracks",
    "cli.help.dry_run": "Write nothing to TIDAL",
    "cli.help.delay": "Pause between requests (s), 0 by default",
    "cli.help.workers": "Parallel TIDAL requests (default 8; lower it if rate-limited)",
    "cli.help.lang": "Output language: en (default) or fr. Also read from APPLE2TIDAL_LANG.",

    # ----------------------------------------------------------- refus argparse
    "cli.error.wipe_with_import": "--wipe deletes and stops: do not combine it with an import "
                                  "action (use --reset to wipe then re-import)",
    "cli.error.library_missing": "missing export path",
    "cli.error.reset_needs_action": "--reset goes with the matching import action "
                                    "(e.g. --reset --all)",
    "cli.error.no_action": "Specify at least one action: --playlists / --favorites / --loved / "
                           "--albums / --all / --wipe",

    # ------------------------------------------------------------ confirmations
    "confirm.word": "DELETE",
    "confirm.prompt": "  Type {word} to confirm: ",
    "confirm.wipe_warning": "  The account will be wiped and NOTHING will be re-imported. "
                            "This is IRREVERSIBLE.",
    "confirm.reset_warning": "  This is IRREVERSIBLE. TIDAL has no trash bin.",
    "confirm.cancelled": "  Cancelled, nothing was touched.",

    # ------------------------------------------------------------------- TIDAL
    "tidal.connected": "[TIDAL] connected: {user}",
    "tidal.login_failed": "TIDAL login failed.",
    "tidal.api_refused": "the TIDAL API refused the operation (returned False)",
    "net.rate_limit_pause": "  [rate-limit] pausing {seconds}s",
    "search.error": "  [search err] {query!r}: {error}",

    # -------------------------------------------------------- travail parallèle
    "parallel.progress": "  {label}: {done}/{total}",
    "parallel.done": "  [ok] {label}: {done}/{total}      ",
    "parallel.item_error": "  [err] {label} {item}: {error}",
    "label.favorites_removed": "favorites removed",
    "label.albums_removed": "albums removed",
    "label.artists_removed": "artists removed",
    "label.playlists_unfollowed": "playlists unfollowed",
    "label.favorites_one_by_one": "favorites (one by one)",
    "label.albums_added": "albums added",

    # ------------------------------------------------------------- suppression
    "wipe.dry_playlist": '  [dry] would delete playlist "{name}" ({n} tracks)',
    "wipe.deleted_playlist": '  [del] playlist "{name}"',
    "wipe.playlist_error": '  [err] playlist "{name}": {error}',
    "wipe.playlist_not_found": '  [err] "{name}" not found: {error}',
    "wipe.not_owned": '  [skip] "{name}" is not a playlist you own',
    "wipe.dry_favorites": "  [dry] would remove {n} tracks from favorites",
    "wipe.dry_albums": "  [dry] would remove {n} albums from favorites",
    "wipe.dry_artists": "  [dry] would remove {n} artists from favorites",
    "wipe.dry_followed": "  [dry] would unfollow {n} playlist(s)",
    "wipe.summary": "  to delete: {playlists} playlist(s), {tracks} favorite tracks, "
                    "{albums} favorite albums, {artists} favorite artists",
    "wipe.summary_followed": ", {n} followed playlist(s)",
    "reset.summary": "  to delete: {to_delete} of {own} playlist(s), {tracks} favorite tracks, "
                     "{albums} favorite albums",

    # ------------------------------------------------------------ vérification
    "verify.playlists_left": "  [!] {n} playlist(s) still present: {names}",
    "verify.tracks_left": "  [!] {n} tracks still in favorites",
    "verify.albums_left": "  [!] {n} albums still in favorites",
    "verify.ok": "  [check] account properly wiped",
    "verify.failed": "  [check] deletion failed (see above) — nothing will be imported on top",

    # --------------------------------------------------------------- playlists
    "playlist.dry": '  [dry] playlist "{name}": {n} tracks',
    "playlist.exists": '  [skip] "{name}" already exists (--overwrite to clear and recreate it)',
    "playlist.ambiguous": '  [skip] {n} TIDAL playlists are named "{name}": nothing was touched, '
                          "rename or delete one and run again",
    "playlist.created": '  [ok] "{name}": {n} tracks',
    "playlist.no_match": '  [skip] "{name}": no track matched',
    "playlist.description": "Imported from Apple Music",
    "playlist.description_missing": " ({n} tracks not found)",

    # ----------------------------------------------------------------- favoris
    "favorites.dry_tracks": "  [dry] {n} tracks → favorites",
    "favorites.dry_albums": "  [dry] {n} albums → favorites",
    "favorites.already": "  {n} already in favorites, skipped",
    "favorites.nothing": "  [ok] nothing to add",
    "favorites.progress": "  favorites: {done}/{total}",
    "favorites.added": "  [ok] {n} tracks added to favorites",

    # -------------------------------------------------------------------- cache
    "cache.unreadable": "[cache] {path} unreadable ({error}), starting from scratch",
    "cache.migrated": "[cache] {n} entries migrated to the new format ({unique} unique)",
    "cache.threshold_changed": "[cache] {n} cached entries no longer agree with threshold "
                               "{threshold:.0f} — searching again",

    # ----------------------------------------------------------------- matching
    "match.plan": "[match] {needed} tracks → {groups} distinct, {todo} to search "
                  "(cache: {cached})",
    "match.line": "  {i}/{total} {flag} {artist} — {name}  ({score})",
    "match.line_target": "  → {tidal_artist} — {tidal_title}",
    "match.error": "  [err] {error}",
    "match.finished": "[match] done in {seconds:.0f}s ({rate:.1f} tracks/s)",
    "match.report": "[match] {found}/{total} matched — unmatched listed in {path}",

    # -------------------------------------------------------------------- Apple
    "apple.summary": "[Apple] {tracks} tracks ({isrc} with ISRC), {playlists} playlists",
    "apple.playlist_line": "   - {name} ({n}){smart}",
    "apple.smart_tag": " [smart]",

    # ------------------------------------------------------------ déroulé général
    "section.playlists": "[TIDAL] playlists",
    "section.favorites": "[TIDAL] favorites",
    "section.albums": "[TIDAL] albums",
    "albums.detected": "  {n} full albums detected",
    "albums.line": "  - {artist} — {name}  [{source}]",
    "albums.resolving": "  looking up {n} albums by UPC ({cached} already known)…",
    "albums.source_upc": "UPC",
    "albums.source_tracks": "guessed from tracks",
    "albums.no_declared_list": "  this export lists no albums: they are guessed from the "
                               "tracks (the JSON export from music.apple.com lists them)",
    "main.dry_run_notice": "[TIDAL] --dry-run active: SIMULATION, nothing will be deleted.",
    "main.reading_account": "[TIDAL] reading account state…",
    "main.current_account": "[TIDAL] current account state…",
    "main.backup_written": "  backup written to {path}",
    "main.playlist_line": "    - {name} ({n})",
    "main.import_cancelled": "Import cancelled: the account was not wiped as requested.",
    "main.done": "Done.",
    "main.done_with_errors": "Done, with errors (see above).",
}

FR: dict[str, str] = {
    # ---------------------------------------------------------------- argparse
    "cli.description": "Apple Music → TIDAL",
    "cli.help.library": "Export .xml (app Musique) ou .json (export_apple_music.js). "
                        "Inutile avec --wipe.",
    "cli.help.playlists": "Recréer les playlists",
    "cli.help.favorites": "Toute la bibliothèque → titres favoris TIDAL",
    "cli.help.loved": "Seulement les titres 'aimés' → favoris",
    "cli.help.albums": "Albums listés par l'export → albums favoris (retrouvés par UPC ; "
                       "déduits des titres si l'export n'en liste aucun)",
    "cli.help.all": "= --playlists --favorites --albums",
    "cli.help.only": "Nom(s) de playlist à traiter uniquement",
    "cli.help.skip_smart": "Ignorer les playlists intelligentes",
    "cli.help.overwrite": "Vider et recréer les playlists existantes",
    "cli.help.wipe": "DESTRUCTIF : vide entièrement le compte TIDAL et s'arrête (aucun import)",
    "cli.help.keep_followed": "Avec --wipe : garder les playlists d'autres utilisateurs que tu suis",
    "cli.help.reset": "DESTRUCTIF : vide le compte TIDAL (playlists créées par toi + favoris) "
                      "avant l'import",
    "cli.help.reset_scope": "imported (défaut) = ne supprime que les playlists portant le nom "
                            "d'une playlist Apple ; all = supprime toutes tes playlists",
    "cli.help.yes": "Ne pas demander confirmation pour --reset",
    "cli.help.threshold": "Score min de matching (0-100), défaut {default:.0f}",
    "cli.help.rematch": "Ignorer le cache et rechercher à nouveau les non-trouvés",
    "cli.help.dry_run": "Ne rien écrire sur TIDAL",
    "cli.help.delay": "Pause entre requêtes (s), 0 par défaut",
    "cli.help.workers": "Requêtes TIDAL en parallèle (défaut 8 ; baisser si rate-limit)",
    "cli.help.lang": "Langue de la sortie : en (défaut) ou fr. Lue aussi depuis APPLE2TIDAL_LANG.",

    # ----------------------------------------------------------- refus argparse
    "cli.error.wipe_with_import": "--wipe supprime et s'arrête : ne le combine pas avec une "
                                  "action d'import (utilise --reset pour vider puis réimporter)",
    "cli.error.library_missing": "chemin de l'export manquant",
    "cli.error.reset_needs_action": "--reset s'utilise avec l'action d'import correspondante "
                                    "(ex : --reset --all)",
    "cli.error.no_action": "Précise au moins une action : --playlists / --favorites / --loved / "
                           "--albums / --all / --wipe",

    # ------------------------------------------------------------ confirmations
    "confirm.word": "SUPPRIMER",
    "confirm.prompt": "  Tape {word} pour confirmer : ",
    "confirm.wipe_warning": "  Le compte sera vidé et RIEN ne sera réimporté. C'est IRRÉVERSIBLE.",
    "confirm.reset_warning": "  C'est IRRÉVERSIBLE. TIDAL ne propose pas de corbeille.",
    "confirm.cancelled": "  Annulé, rien n'a été touché.",

    # ------------------------------------------------------------------- TIDAL
    "tidal.connected": "[TIDAL] connecté : {user}",
    "tidal.login_failed": "Connexion TIDAL échouée.",
    "tidal.api_refused": "l'API TIDAL a refusé l'opération (retour False)",
    "net.rate_limit_pause": "  [rate-limit] pause {seconds}s",
    "search.error": "  [search err] {query!r}: {error}",

    # -------------------------------------------------------- travail parallèle
    "parallel.progress": "  {label} : {done}/{total}",
    "parallel.done": "  [ok] {label} : {done}/{total}      ",
    "parallel.item_error": "  [err] {label} {item}: {error}",
    "label.favorites_removed": "favoris retirés",
    "label.albums_removed": "albums retirés",
    "label.artists_removed": "artistes retirés",
    "label.playlists_unfollowed": "playlists non suivies",
    "label.favorites_one_by_one": "favoris (unitaire)",
    "label.albums_added": "albums ajoutés",

    # ------------------------------------------------------------- suppression
    "wipe.dry_playlist": "  [dry] supprimerait la playlist « {name} » ({n} titres)",
    "wipe.deleted_playlist": "  [del] playlist « {name} »",
    "wipe.playlist_error": "  [err] playlist « {name} » : {error}",
    "wipe.playlist_not_found": "  [err] « {name} » introuvable : {error}",
    "wipe.not_owned": "  [skip] « {name} » n'est pas une playlist que tu possèdes",
    "wipe.dry_favorites": "  [dry] retirerait {n} titres des favoris",
    "wipe.dry_albums": "  [dry] retirerait {n} albums des favoris",
    "wipe.dry_artists": "  [dry] retirerait {n} artistes des favoris",
    "wipe.dry_followed": "  [dry] arrêterait de suivre {n} playlist(s)",
    "wipe.summary": "  à supprimer : {playlists} playlist(s), {tracks} titres favoris, "
                    "{albums} albums favoris, {artists} artistes favoris",
    "wipe.summary_followed": ", {n} playlist(s) suivies",
    "reset.summary": "  à supprimer : {to_delete} playlist(s) sur {own}, {tracks} titres favoris, "
                     "{albums} albums favoris",

    # ------------------------------------------------------------ vérification
    "verify.playlists_left": "  [!] {n} playlist(s) toujours présentes : {names}",
    "verify.tracks_left": "  [!] {n} titres encore en favoris",
    "verify.albums_left": "  [!] {n} albums encore en favoris",
    "verify.ok": "  [vérif] compte bien vidé",
    "verify.failed": "  [vérif] la suppression a échoué (voir ci-dessus) — rien ne sera importé "
                     "par-dessus",

    # --------------------------------------------------------------- playlists
    "playlist.dry": "  [dry] playlist « {name} » : {n} titres",
    "playlist.exists": "  [skip] « {name} » existe déjà (--overwrite pour la vider et recréer)",
    "playlist.ambiguous": "  [skip] {n} playlists TIDAL portent le nom « {name} » : rien n'a "
                          "été touché, renomme ou supprime l'une d'elles puis relance",
    "playlist.created": "  [ok] « {name} » : {n} titres",
    "playlist.no_match": "  [skip] « {name} » : aucun titre trouvé",
    "playlist.description": "Importée d'Apple Music",
    "playlist.description_missing": " ({n} titres non trouvés)",

    # ----------------------------------------------------------------- favoris
    "favorites.dry_tracks": "  [dry] {n} titres → favoris",
    "favorites.dry_albums": "  [dry] {n} albums → favoris",
    "favorites.already": "  {n} déjà en favoris, ignorés",
    "favorites.nothing": "  [ok] rien à ajouter",
    "favorites.progress": "  favoris : {done}/{total}",
    "favorites.added": "  [ok] {n} titres ajoutés aux favoris",

    # -------------------------------------------------------------------- cache
    "cache.unreadable": "[cache] {path} illisible ({error}), on repart de zéro",
    "cache.migrated": "[cache] {n} entrées migrées vers le nouveau format ({unique} uniques)",
    "cache.threshold_changed": "[cache] {n} entrées en cache ne s'accordent plus avec le seuil "
                               "{threshold:.0f} — nouvelle recherche",

    # ----------------------------------------------------------------- matching
    "match.plan": "[match] {needed} titres → {groups} distincts, {todo} à rechercher "
                  "(cache : {cached})",
    "match.line": "  {i}/{total} {flag} {artist} — {name}  ({score})",
    "match.line_target": "  → {tidal_artist} — {tidal_title}",
    "match.error": "  [err] {error}",
    "match.finished": "[match] terminé en {seconds:.0f}s ({rate:.1f} titres/s)",
    "match.report": "[match] {found}/{total} trouvés — non trouvés listés dans {path}",

    # -------------------------------------------------------------------- Apple
    "apple.summary": "[Apple] {tracks} titres ({isrc} avec ISRC), {playlists} playlists",
    "apple.playlist_line": "   - {name} ({n}){smart}",
    "apple.smart_tag": " [smart]",

    # ------------------------------------------------------------ déroulé général
    "section.playlists": "[TIDAL] playlists",
    "section.favorites": "[TIDAL] favoris",
    "section.albums": "[TIDAL] albums",
    "albums.detected": "  {n} albums complets détectés",
    "albums.line": "  - {artist} — {name}  [{source}]",
    "albums.resolving": "  recherche de {n} albums par UPC ({cached} déjà connus)…",
    "albums.source_upc": "UPC",
    "albums.source_tracks": "déduit des titres",
    "albums.no_declared_list": "  cet export ne liste pas les albums : ils sont déduits des "
                               "titres (l'export JSON de music.apple.com les liste)",
    "main.dry_run_notice": "[TIDAL] --dry-run actif : SIMULATION, rien ne sera supprimé.",
    "main.reading_account": "[TIDAL] lecture de l'état du compte…",
    "main.current_account": "[TIDAL] état actuel du compte…",
    "main.backup_written": "  sauvegarde écrite dans {path}",
    "main.playlist_line": "    - {name} ({n})",
    "main.import_cancelled": "Import annulé : le compte n'a pas été vidé comme demandé.",
    "main.done": "Terminé.",
    "main.done_with_errors": "Terminé avec des erreurs (voir ci-dessus).",
}

LANGUAGES: dict[str, dict[str, str]] = {"en": EN, "fr": FR}

_current = DEFAULT_LANG


def _from_argv(argv: list[str]) -> str | None:
    """Lit --lang avant argparse : les textes d'aide doivent deja etre traduits."""
    for i, arg in enumerate(argv):
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def resolve_lang(argv: list[str] | None = None, env: dict[str, str] | None = None) -> str:
    """--lang, puis la variable d'environnement, puis l'anglais.

    Une valeur inconnue est ignoree plutot que rejetee ici : argparse la
    refusera ensuite, avec un message dans la langue qu'on aura retenue.
    """
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    for candidate in (_from_argv(argv), env.get(ENV_VAR)):
        if candidate and candidate.strip().lower() in LANGUAGES:
            return candidate.strip().lower()
    return DEFAULT_LANG


def set_lang(lang: str) -> str:
    global _current
    _current = lang if lang in LANGUAGES else DEFAULT_LANG
    return _current


def get_lang() -> str:
    return _current


def t(key: str, **params) -> str:
    """Message traduit. Une cle absente leve : le test de parite l'interdit."""
    try:
        template = LANGUAGES[_current][key]
    except KeyError:
        raise KeyError(f"message inconnu : {key}") from None
    return template.format(**params) if params else template
