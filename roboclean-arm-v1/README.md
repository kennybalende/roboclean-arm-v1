# RoboClean Arm-V1 — Poste de commande et gestion

Jumeau numérique, pilotage et **gestion des données** du bras de nettoyage de façades vitrées
(UCAC-ICAM, Douala). Implémente le dossier d'ingénierie : modèle géométrique inverse, séquence de
nettoyage, régulateur PI d'effort (12,5 N), sécurité STO (> 15 N), réservoirs, liaison Arduino.

## Architecture
```
Simulateur web ──┐                         ┌─ historique des cycles
Programme Python ─┼─ POST /api/cycles ─► Serveur Node.js ─► SQLite (data/roboclean.db)
Tableau de bord ◄─┘   GET /api/...          └─ alarmes, paramètres, export CSV
```
| Chemin | Rôle |
|---|---|
| `server/server.js`, `server/schema.sql` | API REST + serveur de fichiers, base SQLite (sans dépendance, Node ≥ 22.5) |
| `server/test/api.test.js` | Tests de l'API (`npm test`) |
| `web/index.html`, `style.css`, `app.js` | Tableau de bord : statistiques, historique, alarmes, paramètres, CSV |
| `web/simulateur.html` | Simulateur du robot ; envoie chaque cycle terminé au serveur |
| `roboclean_controle.py` | Application Python (tkinter) + `--headless` + `--api` |
| `tests/` | Tests Python (`python -m unittest discover -s tests`) |
| `Dockerfile`, `docker-compose.yml` | Déploiement en un conteneur, base persistante |
| `.github/workflows/` | CI (Python + Node), GitHub Pages, exécutables à chaque tag `v*` |

## Démarrage
```bash
npm start                      # http://localhost:3000  (tableau de bord + API)
python roboclean_controle.py --api http://localhost:3000            # interface graphique
python roboclean_controle.py --headless --proto --api http://localhost:3000
docker compose up -d           # alternative avec Docker
```
Ouvrez `http://localhost:3000` : chaque cycle lancé depuis le simulateur ou Python y apparaît.

## API
| Route | Description |
|---|---|
| `GET/POST /api/cycles`, `GET/DELETE /api/cycles/:id` | Historique des cycles |
| `GET /api/cycles.csv` | Export CSV |
| `GET /api/stats` | Taux de réussite, durée moyenne, Fz max, eau utilisée |
| `GET/POST /api/events` | Journal des alarmes (un STO crée une alerte critique) |
| `GET/PUT /api/settings` | Paramètres (limite de force plafonnée à 15 N) |

> L'API n'a pas d'authentification : à utiliser sur un réseau local de confiance, ou derrière un
> proxy protégé. La version GitHub Pages n'héberge que le front : saisissez l'adresse de votre serveur
> dans le tableau de bord.

## Publier sur GitHub
```bash
./publier.sh roboclean-arm-v1
```
