# apple2tidal

Transfère playlists, bibliothèque, titres aimés et albums d'Apple Music vers TIDAL.

## 1. Exporter la bibliothèque Apple Music

### Option A — depuis le navigateur (recommandé, donne les ISRC)

1. Ouvre https://music.apple.com et connecte-toi.
2. F12 → onglet **Console**. Si Chrome affiche un avertissement, tape `allow pasting` puis Entrée.
3. Colle le contenu de `export_apple_music.js`, Entrée.
4. Attends les logs `[export] …` ; `apple_library.json` se télécharge à la fin.

Le JSON contient titres, playlists, titres aimés, albums, et l'ISRC de chaque titre lié au catalogue → le matching TIDAL est exact (`get_tracks_by_isrc`), la recherche fuzzy ne sert qu'en secours.

### Option B — depuis l'app Musique (Mac) / iTunes (Windows)

**Fichier → Bibliothèque → Exporter la bibliothèque…** → `Bibliothèque.xml`. Pas d'ISRC dans ce format : matching fuzzy uniquement.

## 2. Installer

```bash
pip install -r requirements.txt
```

## 3. Lancer

```bash
# Étape conseillée : matching seul, aucune écriture sur TIDAL
python apple2tidal.py apple_library.json --dry-run

# Puis, au choix :
python apple2tidal.py apple_library.json --playlists              # recrée les playlists
python apple2tidal.py apple_library.json --favorites              # toute la bibliothèque → titres favoris
python apple2tidal.py apple_library.json --loved                  # seulement les titres "aimés" → favoris
python apple2tidal.py apple_library.json --albums                 # albums complets → albums favoris
python apple2tidal.py apple_library.json --all                    # tout
```

Au premier lancement, un lien `link.tidal.com/XXXXX` s'affiche : ouvre-le, connecte-toi, le script continue seul. La session est sauvegardée dans `.apple2tidal/tidal_session.json`.

## Options utiles

| Option | Effet |
|---|---|
| `--only "Nom"` | Ne traite que cette playlist (répétable) |
| `--skip-smart` | Ignore les playlists intelligentes |
| `--overwrite` | Vide et recrée une playlist TIDAL du même nom (sinon elle est ignorée) |
| `--threshold 85` | Score minimal de matching (défaut 78). Monter = moins de faux positifs, plus de non-trouvés |
| `--rematch` | Retente les titres non trouvés (utile après avoir baissé le seuil) |
| `--workers 8` | Requêtes TIDAL en parallèle (défaut 8) |
| `--delay 0.5` | Pause entre requêtes, à ajouter seulement si TIDAL rate-limit |
| `--reset` | **Destructif** : vide le compte TIDAL avant l'import (voir ci-dessous) |
| `--reset-scope all` | `--reset` supprime *toutes* tes playlists, pas seulement les homonymes Apple |
| `--yes` | Saute la confirmation de `--reset` |

## Repartir de zéro (`--reset`)

```bash
python apple2tidal.py apple_library.json --all --reset --dry-run   # montre ce qui serait supprimé
python apple2tidal.py apple_library.json --all --reset             # demande confirmation puis supprime + réimporte
```

Ce que `--reset` supprime, en fonction des actions demandées :
- avec `--playlists` : tes playlists TIDAL (celles que tu as créées ; les playlists d'autres utilisateurs que tu suis ne sont pas touchées)
- avec `--favorites` / `--loved` : tous tes titres favoris
- avec `--albums` : tous tes albums favoris

Par défaut (`--reset-scope imported`), seules les playlists portant le nom d'une playlist de ton export Apple sont supprimées — celles que tu as créées toi-même sur TIDAL survivent. `--reset-scope all` supprime toutes tes playlists.

Avant toute suppression, l'état du compte (playlists avec leurs titres, favoris, albums) est écrit dans `.apple2tidal/backup_AAAAMMJJ_HHMMSS.json`. C'est une sauvegarde lisible, pas un bouton "annuler" : TIDAL n'a pas de corbeille, une playlist supprimée l'est définitivement.

## Comment ça matche

Si le titre a un ISRC (export JSON), il est résolu directement. Sinon chaque titre est cherché sur TIDAL (plusieurs requêtes : artiste + titre nettoyé, titre seul…) et les candidats sont notés :
- titre 55 %, artiste 35 %, album 10 % (fuzzy, insensible aux accents/casse, "feat.", "Remastered", etc. retirés)
- pénalité si la durée diffère de plus de 5 s, forte pénalité au-delà de 20 s

Les résultats sont mis en cache dans `.apple2tidal/matches.json` : interrompre et relancer reprend où ça en était. Les titres non trouvés sont listés dans `.apple2tidal/unmatched.csv` avec les playlists concernées, pour les ajouter à la main.

## Vitesse

Trois choses jouent :

- **Parallélisme** : `--workers 8` par défaut. `--workers 16` va environ deux fois plus vite ; si tu vois des `[rate-limit] pause Xs`, redescends à 4-6 (ou ajoute `--delay 0.2`), sinon tu passes plus de temps en backoff qu'en requêtes.
- **Déduplication** : un titre présent dans cinq playlists ne coûte qu'une recherche. Deux entrées sont considérées identiques si elles ont le même ISRC (ou, à défaut, le même artiste + titre normalisés).
- **Cache** : `.apple2tidal/matches.json`. Une deuxième exécution ne refait aucune recherche. Ne le supprime pas.

À l'écriture, les titres déjà en favoris sont détectés et ignorés, et les suppressions de `--reset` sont parallélisées.

## Limites

- `tidalapi` est une lib non officielle : si TIDAL change son API, ça peut casser.
- Les playlists Apple ne sont pas synchronisées ensuite ; c'est un import ponctuel.
- Les titres exclusifs à Apple ou sous un autre nom sur TIDAL finiront dans `unmatched.csv`.
- Le parallélisme repose sur la session HTTP de `tidalapi` ; au-delà de ~16 workers tu risques surtout de te faire limiter par TIDAL.
- Les dossiers de playlists ne sont pas recréés (les playlists à l'intérieur le sont, à plat).
