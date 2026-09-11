# apple2tidal

[English](README.md) · **Français**

Transfère playlists, bibliothèque, titres aimés et albums d'Apple Music vers TIDAL.

## 1. Exporter la bibliothèque Apple Music

### Option A — depuis le navigateur (recommandé, fournit les ISRC)

1. Ouvrir https://music.apple.com et se connecter.
2. F12 → onglet **Console**. Si Chrome affiche un avertissement, taper `allow pasting` puis Entrée.
3. Coller le contenu de `export_apple_music.js`, puis Entrée.
4. Attendre les logs `[export] …` ; `apple_library.json` se télécharge à la fin.

Le JSON contient titres, playlists, titres aimés, albums, ainsi que l'ISRC de chaque titre lié au catalogue. TIDAL sait les résoudre directement (`get_tracks_by_isrc`), la recherche approchée ne sert donc qu'en secours.

### Option B — depuis l'app Musique (Mac) / iTunes (Windows)

**Fichier → Bibliothèque → Exporter la bibliothèque…** → `Bibliothèque.xml`. Ce format ne contient pas d'ISRC : le matching est uniquement approché.

## 2. Installer

```bash
pip install -r requirements.txt
```

## 3. Lancer

```bash
# Première étape conseillée : matching seul, aucune écriture sur TIDAL
python apple2tidal.py apple_library.json --dry-run

# Puis, au choix :
python apple2tidal.py apple_library.json --playlists              # recrée les playlists
python apple2tidal.py apple_library.json --favorites              # toute la bibliothèque → titres favoris
python apple2tidal.py apple_library.json --loved                  # seulement les titres « aimés » → favoris
python apple2tidal.py apple_library.json --albums                 # albums complets → albums favoris
python apple2tidal.py apple_library.json --all                    # tout
```

Au premier lancement, un lien `link.tidal.com/XXXXX` s'affiche : il suffit de l'ouvrir et de se connecter, le script poursuit seul. La session est enregistrée dans `.apple2tidal/tidal_session.json`.

## Options

| Option | Effet |
|---|---|
| `--only "Nom"` | Ne traite que cette playlist (répétable) |
| `--skip-smart` | Ignore les playlists intelligentes |
| `--overwrite` | Vide et recrée une playlist TIDAL du même nom (sinon elle est ignorée) |
| `--threshold 85` | Score minimal de matching (défaut 78). Plus haut = moins de faux positifs, plus de non-trouvés |
| `--rematch` | Retente les titres non trouvés (utile après avoir abaissé le seuil) |
| `--workers 8` | Requêtes TIDAL en parallèle (défaut 8) |
| `--delay 0.5` | Pause entre requêtes, à ajouter seulement en cas de rate-limit |
| `--reset` | **Destructif** : vide le compte TIDAL avant l'import (voir ci-dessous) |
| `--reset-scope all` | `--reset` supprime *toutes* les playlists, pas seulement les homonymes Apple |
| `--yes` | Saute la confirmation de `--reset` |
| `--lang fr` | Langue de la sortie : `en` (défaut) ou `fr`. Lue aussi depuis la variable d'environnement `APPLE2TIDAL_LANG`. Le mot de confirmation des suppressions suit la langue : `DELETE` en anglais, `SUPPRIMER` en français |

## Vider le compte TIDAL sans rien réimporter (`--wipe`)

```bash
python apple2tidal.py --wipe --dry-run   # liste ce qui serait supprimé
python apple2tidal.py --wipe             # demande confirmation, supprime, s'arrête
```

Supprime les playlists, les titres favoris, les albums favoris, les artistes favoris, et se désabonne des playlists suivies (`--keep-followed` pour les conserver). Aucun import n'est effectué ensuite : le compte reste vide.

Le fichier d'export n'est pas nécessaire. Une sauvegarde JSON est écrite avant suppression, comme pour `--reset`.

**`--wipe` n'est pas `--reset`** : `--reset` vide *puis réimporte* dans la même commande, le compte reflète donc l'export Apple à la fin. Pour un compte vierge, c'est `--wipe`.

## Repartir de zéro (`--reset`)

```bash
python apple2tidal.py apple_library.json --all --reset --dry-run   # montre ce qui serait supprimé
python apple2tidal.py apple_library.json --all --reset             # confirmation, puis suppression + réimport
```

Ce que `--reset` supprime dépend des actions demandées :
- avec `--playlists` : les playlists appartenant au compte (les playlists d'autres utilisateurs, simplement suivies, ne sont pas touchées)
- avec `--favorites` / `--loved` : tous les titres favoris
- avec `--albums` : tous les albums favoris

Par défaut (`--reset-scope imported`), seules les playlists portant le nom d'une playlist de l'export Apple sont supprimées ; celles créées directement sur TIDAL sont préservées. `--reset-scope all` supprime toutes les playlists du compte.

Avant toute suppression, l'état du compte (playlists avec leurs titres, favoris, albums) est écrit dans `.apple2tidal/backup_AAAAMMJJ_HHMMSS.json`. Il s'agit d'une sauvegarde lisible, pas d'un bouton « annuler » : TIDAL n'a pas de corbeille, une playlist supprimée l'est définitivement.

## Fonctionnement du matching

Un titre disposant d'un ISRC (export JSON) est résolu directement. Sinon, il est cherché sur TIDAL via plusieurs requêtes (artiste + titre nettoyé, titre seul, etc.) et les candidats sont notés :
- titre 55 %, artiste 35 %, album 10 % (approché, insensible aux accents et à la casse, suffixes « feat. », « Remastered » et assimilés retirés)
- pénalité si les durées diffèrent de plus de 5 s, forte pénalité au-delà de 20 s

Les résultats sont mis en cache dans `.apple2tidal/matches.json` : une exécution interrompue reprend où elle s'était arrêtée. Les titres non trouvés sont listés dans `.apple2tidal/unmatched.csv`, avec les playlists concernées, pour un traitement manuel.

## Vitesse

Trois facteurs entrent en jeu :

- **Parallélisme** : `--workers 8` par défaut. `--workers 16` est environ deux fois plus rapide ; si des messages `[rate-limit] pause Xs` apparaissent, redescendre à 4-6 (ou ajouter `--delay 0.2`), faute de quoi le temps passé en backoff dépasse le gain.
- **Déduplication** : un titre présent dans cinq playlists ne coûte qu'une recherche. Deux entrées sont considérées identiques si elles partagent un ISRC ou, à défaut, un artiste et un titre normalisés.
- **Cache** : `.apple2tidal/matches.json`. Une seconde exécution ne relance aucune recherche. Ce fichier ne doit pas être supprimé.

Côté écriture, les titres déjà en favoris sont détectés et ignorés, et les suppressions de `--reset` sont parallélisées.

## Sécurité et vie privée

Le dépôt ne contient que du code, aucun secret. Les fichiers suivants ne doivent jamais être committés ; tous sont couverts par le `.gitignore` fourni :

| Fichier | Raison |
|---|---|
| `.apple2tidal/tidal_session.json` | Jetons OAuth TIDAL. Quiconque les obtient accède au compte sans mot de passe. |
| `.apple2tidal/backup_*.json` | Copie complète du contenu du compte TIDAL. |
| `apple_library.json`, `*.xml` | Bibliothèque Apple entière (titres, playlists, ISRC). |
| `.apple2tidal/matches.json`, `unmatched.csv` | Historique d'écoute en clair. |

Si l'un d'eux a déjà été committé, `git rm --cached` ne suffit pas : le fichier reste dans l'historique. Il faut réécrire l'historique (`git filter-repo`) **et** révoquer la session TIDAL depuis les paramètres du compte.

`export_apple_music.js` lit le cookie `media-user-token` et le jeton Apple présent dans la page pour appeler l'API. Il utilise la session existante, dans le navigateur, et n'envoie rien ailleurs. Comme pour tout script à coller en console, une relecture avant exécution est recommandée.

## Limites

- `tidalapi` n'est pas une bibliothèque officielle : un changement côté TIDAL peut casser l'outil à tout moment.
- Les playlists Apple ne sont pas synchronisées ensuite ; il s'agit d'un import ponctuel.
- Les titres exclusifs à Apple, ou référencés sous un autre nom sur TIDAL, se retrouvent dans `unmatched.csv`.
- Le parallélisme repose sur la session HTTP de `tidalapi` ; au-delà d'environ 16 workers, l'effet principal est le rate-limiting de TIDAL.
- Les dossiers de playlists ne sont pas recréés (les playlists qu'ils contiennent le sont, à plat).

## Licence

MIT.
