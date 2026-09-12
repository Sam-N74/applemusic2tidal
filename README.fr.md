# apple2tidal

[English](README.md) · **Français**

Transfère playlists, bibliothèque, titres aimés et albums d'Apple Music vers TIDAL. Tout tourne sur ta machine : ta bibliothèque n'est envoyée nulle part, aucun plafond de titres, et chaque titre non trouvé est consigné avec son score.

## Pourquoi celui-ci

**Rien ne quitte ta machine.** Aucun compte à créer, aucun service à qui confier ton historique d'écoute. L'outil lit l'export Apple sur ton disque et parle à TIDAL directement depuis ton ordinateur. Les services de transfert en ligne ne peuvent pas fonctionner ainsi — leur produit *est* un serveur qui lit ta bibliothèque. Soundiiz le documente lui-même : le contenu des bibliothèques est stocké sur des serveurs Google Cloud aux États-Unis.

**Aucune limite, en une commande.** Une bibliothèque entière passe en une seule exécution. Sur les offres gratuites d'en face, la même bibliothèque demande neuf transferts manuels (Soundiiz s'arrête à 200 titres par transfert, un à la fois), ou reste tout simplement hors de portée (TuneMyMusic plafonne à 500 titres au total, FreeYourMusic à 600 titres et une playlist).

**Le transfert est vérifiable.** Chaque titre qui n'est pas passé atterrit dans `unmatched.csv`, avec le meilleur score atteint et les playlists concernées. Le seuil est réglable, `--dry-run` montre ce qui se passerait avant la moindre écriture, et le cache permet de rejouer une décision. Personne ne te dit « 43 titres non trouvés » avant de fermer la fenêtre.

**Et c'est réversible.** `--wipe` revide le compte TIDAL : sauvegarde JSON d'abord, confirmation, puis vérification que la suppression a bien eu lieu. Un import raté n'est pas une impasse.

## À quoi ressemble une vraie exécution

1800 titres, 11 playlists, 97 % de correspondance exacte. C'est une vraie bibliothèque, pas un banc d'essai. La plupart des correspondances sont exactes parce que l'export navigateur porte l'ISRC de chaque titre, que TIDAL résout directement ; la recherche approchée ne traite que le reste.

Ce reste est consigné, dans `.apple2tidal/unmatched.csv` :

```csv
artist,title,album,best_score,playlists
Artiste,Titre (feat. Quelqu'un) [Bonus Track],Album (Expanded Edition),71.2,Road trip; Late night
```

Cette ligne est inventée — le vrai fichier, c'est ta bibliothèque, et c'est pourquoi rien n'en est reproduit ici. Sur l'exécution ci-dessus, six titres y ont atterri : deux éditions chopped and screwed de mixtape, une version bonus d'édition étendue que TIDAL orthographie autrement, et trois dont le meilleur candidat plafonne sous 70, c'est-à-dire rien d'assez proche pour être accepté. Chacun tient sur une ligne, retrouvable à la main en une minute.

## Pourquoi pas Soundiiz

Soundiiz, TuneMyMusic et FreeYourMusic font plusieurs choses mieux, et ça vaut la peine de le dire :

- **20+ plateformes.** Cet outil fait Apple Music → TIDAL, et rien d'autre.
- **Synchronisation planifiée**, sur leurs offres payantes. Ici c'est un import ponctuel : relancé, il ne refait pas le travail, mais il ne surveille pas non plus les changements côté Apple.
- **Zéro installation.** Ils tournent dans un onglet. Ici il faut Python, et une console pour l'export.

Ce qu'ils ne peuvent pas offrir :

| | apple2tidal | Soundiiz | TuneMyMusic | FreeYourMusic |
|---|---|---|---|---|
| Où va ta bibliothèque | reste sur ton disque | serveurs Google Cloud, États-Unis | leurs serveurs | leurs serveurs |
| Limite gratuite | aucune | 200 titres par transfert, un à la fois ; albums, artistes et titres aimés réservés au premium | 500 titres au total | 600 titres, une playlist |
| Offre payante | — | ~39 €/an | ~24 $/an | ~5 $, puis abonnements |
| Rapport de ce qui a échoué | `unmatched.csv`, scoré, par playlist | un décompte | un décompte | un décompte |

Pour déplacer 200 titres une fois, autant prendre Soundiiz : c'est plus rapide. Pour déplacer une bibliothèque à laquelle tu tiens, ou si tu quittes Apple justement parce que tu préfères ne pas confier ton historique d'écoute à une autre plateforme, c'est pour ça que celui-ci existe.

## 1. Installer

```bash
pip install -r requirements.txt
```

## 2. Exporter la bibliothèque Apple Music

### Option A — depuis le navigateur (recommandé, fournit les ISRC)

1. Ouvrir https://music.apple.com et se connecter.
2. F12 → onglet **Console**. Si Chrome affiche un avertissement, taper `allow pasting` puis Entrée.
3. Coller le contenu de `export_apple_music.js`, puis Entrée.
4. Attendre les logs `[export] …` ; `apple_library.json` se télécharge à la fin.

Le JSON contient titres, playlists, titres aimés, albums, ainsi que l'ISRC de chaque titre lié au catalogue. TIDAL sait les résoudre directement (`get_tracks_by_isrc`), la recherche approchée ne sert donc qu'en secours.

### Option B — depuis l'app Musique (Mac) / iTunes (Windows)

**Fichier → Bibliothèque → Exporter la bibliothèque…** → `Bibliothèque.xml`. Ce format ne contient pas d'ISRC : le matching est uniquement approché.

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
| `--overwrite` | Vide et recrée une playlist TIDAL du même nom (sinon elle est ignorée). Si deux playlists TIDAL portent ce nom, aucune n'est touchée |
| `--threshold 85` | Score minimal de matching (défaut 78). Plus haut = moins de faux positifs, plus de non-trouvés. Les matchs en cache qui ne passent plus le nouveau seuil sont recherchés à nouveau |
| `--rematch` | Recherche à nouveau les titres restés introuvables à seuil égal |
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

Les résultats sont mis en cache dans `.apple2tidal/matches.json` : une exécution interrompue reprend où elle s'était arrêtée. Chaque entrée retient le seuil sous lequel elle a été décidée, si bien que changer `--threshold` révalue ce qui doit l'être. `--albums` y met aussi en cache ses recherches par UPC : la deuxième exécution ne coûte plus une seule requête. Les titres non trouvés sont listés dans `.apple2tidal/unmatched.csv`, avec les playlists concernées, pour un traitement manuel.

## Vitesse

Trois facteurs entrent en jeu :

- **Parallélisme** : `--workers 8` par défaut. `--workers 16` est environ deux fois plus rapide ; si des messages `[rate-limit] pause Xs` apparaissent, redescendre à 4-6 (ou ajouter `--delay 0.2`), faute de quoi le temps passé en backoff dépasse le gain.
- **Déduplication** : un titre présent dans cinq playlists ne coûte qu'une recherche. Deux entrées sont considérées identiques si elles partagent un ISRC ou, à défaut, un artiste et un titre normalisés.
- **Cache** : `.apple2tidal/matches.json`. Une seconde exécution ne relance aucune recherche. Ce fichier ne doit pas être supprimé.

Côté écriture, les titres déjà en favoris sont détectés et ignorés, et les suppressions de `--reset` sont parallélisées.

## Fichiers qui doivent rester locaux

Rien n'est envoyé où que ce soit, mais les fichiers que l'outil écrit sont ta bibliothèque en clair. Le dépôt ne contient que du code, aucun secret, et tous les fichiers ci-dessous sont couverts par le `.gitignore` fourni :

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

MIT. Gratuit, et ça le restera — la promesse « rien ne quitte ta machine » ne vaut quelque chose que si elle tient encore l'an prochain.

Si l'outil t'a épargné un après-midi, dis-le dans une issue. Ça suffit.
