# Ukraine : la guerre vue du ciel

Une carte unique, mois par mois depuis février 2022 :

- **couche principale** : probabilité d'occupation russe, agrégée à partir de sources divergentes. Les camps (ukrainien, russe, tiers) pèsent chacun un tiers. La carte trace la ligne médiane et les lignes 10 % et 90 %, et donne les km² occupés et l'écart entre la vue russe et la vue ukrainienne ;
- **couches activables** :
  - feux anormaux (NASA FIRMS, comparés à la normale 2012-2021) ;
  - pertes russes documentées (WarSpotting) ;
  - combats (UCDP) ;
  - arcs de missiles et de drones (lieu de lancement → oblast visé).
- **sous la carte** : compteurs Oryx, missiles et drones lancés.

Page : `site/index.html`, à ouvrir en local ou à publier tel quel. Elle utilise d3 7.9.0 depuis cdnjs.

## Mise à jour

```sh
python3 scripts/fetch.py raw --api --nrt      # FIRMS : archives, API 2026 (clé FIRMS_MAP_KEY), flux 7 jours
python3 scripts/fetch_osint.py raw            # VIINA, WarSpotting, Oryx, missiles et drones
python3 scripts/build.py raw                  # site/data.js (feux)
python3 scripts/build_osint.py raw            # site/osint.js
python3 scripts/build_front.py raw/viina      # site/front*.js, data/front_*.csv (environ 15 min ; --rapide pour tester)
```

`.github/workflows/update.yml` lance ces commandes chaque jour. La clé FIRMS est passée en secret GitHub (`FIRMS_MAP_KEY`) et n'est jamais écrite dans un fichier. Le dossier `raw/` n'est pas versionné : il est mis en cache par le workflow.

Règle de prudence : aucune donnée de front ni de pertes plus récente que le dernier mois complet terminé depuis au moins 7 jours. Le front est un instantané de fin de mois. Les pertes sont regroupées en mailles de 0,1° par mois, sans unité ni modèle. Le flux quasi temps réel ne sert qu'aux feux.

## Crédits et licences (repris en pied de page du site)

- **Front** : « Contains information from VIINA, which is made available here under the Open Database License (ODbL). » Zhukov, Yuri and Natalie Ayers (2023), *VIINA 2.0*, Harvard University. La grille de probabilité et les surfaces dérivées (`site/front*.js`, `data/front_*.csv`) sont publiées sous **ODbL 1.0** (attribution, partage à l'identique). DeepStateMap, l'ISW et Wikipédia n'interviennent que par leur **statut par localité, relevé par VIINA** ; aucun de leurs polygones n'est reproduit.
- **Feux** : NASA FIRMS (LANCE / ESDIS), VIIRS S-NPP 375 m. « We acknowledge the use of data and/or imagery from NASA's Fire Information for Resource Management System (FIRMS), part of NASA's Earth Science Data and Information System (ESDIS). »
- **Pertes russes géolocalisées** : WarSpotting. Réutilisation **non commerciale avec crédit**.
- **Compteurs de pertes** : Oryx, via scarnecchia/oryx_data, **CC BY 4.0**.
- **Missiles et drones** : « Massive missile attacks on Ukraine », Petro Ivaniuk, **CC BY-NC-SA 4.0**. Les agrégats et les arcs dérivés (`site/osint.js`) restent sous **CC BY-NC-SA 4.0**.
- **Combats** : UCDP Georeferenced Event Dataset, université d'Uppsala, **CC BY 4.0**.
- **Fond de carte** : Natural Earth, domaine public.

Du fait des licences NC (WarSpotting, Ivaniuk), le site et ses données dérivées ne peuvent pas servir à un usage commercial.

## Publication

- Le site est servi tel quel depuis `site/` : tous les chemins sont relatifs et chaque script porte une empreinte `?v=`. Il fonctionne aussi sous un sous-chemin, par exemple `/ukraine-map/`.
- Le workflow `.github/workflows/update.yml` :
  - **sur `schedule` et `workflow_dispatch`** : récupération, construction, commit des sorties, puis déploiement ;
  - **sur `push`** : déploiement seul.
- Chaque étape de récupération et de construction a son propre délai et ne bloque pas les suivantes en cas d'échec (`continue-on-error`).
- Le cache `raw/` est sauvegardé même si une étape échoue. WarSpotting reprend donc là où il s'était arrêté : le premier remplissage prend environ une heure et s'étale sur plusieurs passages.
- **Garde-fous** : `build.py`, `build_osint.py` et `build_front.py` refusent d'écraser une sortie publiée par une version plus pauvre, et sortent alors avec le code 3. Les cas bloquants :
  - un fichier manquant ;
  - un nombre de pertes, d'événements, de détections ou de mois en recul de plus de 5 % ;
  - un dernier mois plus ancien que celui déjà publié.

## Sources, licences et dates de consultation (17 octobre 2026)

| Source | Usage | Camp | Licence / conditions | Verdict |
|---|---|---|---|---|
| [VIINA 2.0](https://github.com/zhukovyuri/VIINA), Zhukov & Ayers, Georgetown / Harvard. Statut quotidien de 33 141 localités selon DeepStateMap, l'ISW et Wikipédia, plus des dépêches géocodées | front (toutes les vues) | — (agrégateur) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), avec attribution et partage à l'identique | **utilisé**. Seul un agrégat est publié (grille de probabilité de 2 km, surfaces). `site/front.js` et `data/front_areas.csv` sont proposés sous ODbL |
| DeepStateMap (via VIINA, colonne `status_dsm`) | front | pro-ukrainien | [contrat de licence](https://deepstatemap.live/license-en.html) : API soumise à accord, redistribution interdite, matériaux visuels libres avec mention | **polygones bruts écartés**. Le statut par localité ne vient que de VIINA (ODbL). Avant une diffusion large, il vaut mieux demander l'accord de DeepState : <https://api.deepstatemap.live/request> |
| Archives GitHub tierces de DeepStateMap (p. ex. cyterat/deepstate-map-data, GPL-3.0) | — | pro-ukrainien | une licence tierce ne peut pas relicencier des données DeepState | **écarté** |
| ISW / CTP (via VIINA, `status_isw`) | front | tiers (institut américain) | aucune licence de réutilisation publiée pour les cartes et les KMZ | **polygones écartés**. Le statut par localité vient de VIINA. L'ISW s'arrête dans VIINA en juin 2026 |
| Wikipédia, carte participative (via VIINA, `status_wiki` et `status_boost`) | front | tiers | CC BY-SA | **utilisé via VIINA** |
| Annonces des médias ukrainiens (24tv, Espreso, LIGA, NV, Pravda, UNIAN, Militarnyi, Interfax-Ukraine, Forbes UA), relevées par VIINA | front, proxy « annonces ukrainiennes » | pro-ukrainien | ODbL (VIINA) | **utilisé** : 6 970 annonces de contrôle ukrainien sur la période |
| Annonces des médias russes (RIA Novosti, NTV, KP, Nezavissimaïa Gazeta), relevées par VIINA | front, proxy « annonces russes » | pro-russe | ODbL (VIINA) | **utilisé** : 324 annonces, presque toutes avant 2025, car RIA et NTV sortent du corpus après 2024 |
| Ministère russe de la Défense (cartes, communiqués) | — | pro-russe | pas de géodonnées publiées ; les communiqués ne sont que du texte | **non intégré directement**. Couvert en partie par les annonces relayées par RIA et NTV |
| Rybar, Suriyak, « militarymaps », Militaryland, Project Owl (UA Control Map) | — | pro-russes (Rybar, Suriyak, militarymaps) ; autres variables | aucune licence publiée | **écartés** |
| OpenStreetMap | — | — | ODbL | **sans objet** : OSM ne cartographie pas le front |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/), VIIRS S-NPP : archives 2012-2025, API zone 2026 (traitement standard jusqu'au 30 juin 2026, puis quasi temps réel) | feux | — | données NASA libres, avec mention | **utilisé** |
| [WarSpotting](https://ukr.warspotting.net/about/) (API) | pertes russes géolocalisées | — | usage non lucratif autorisé avec crédit ; 10 requêtes par 10 s | **utilisé** (pertes russes seulement) |
| [Oryx](https://www.oryxspioenkop.com/) via [scarnecchia/oryx_data](https://github.com/scarnecchia/oryx_data) | compteurs de pertes par camp et par type | — | CC BY 4.0 (jeu dérivé) ; seuls des agrégats sont repris | **utilisé**. La série s'arrête au 21 août 2025 |
| [UCDP GED 26.1](https://ucdp.uu.se/downloads/) et versions « candidate » mensuelles 2026 | combats (couche, et indice de proximité du front) | — | CC BY 4.0 | **utilisé**. Jusqu'à août 2026, précision de 25 km ou mieux |
| [Lostarmour](https://lostarmour.info/) | pertes ukrainiennes (point de vue pro-russe) | pro-russe | aucune licence ni condition de réutilisation publiée | **écarté** |
| [GeoConfirmed](https://geoconfirmed.org/) | pertes et événements géolocalisés | tiers | pas de licence publiée ; API réservée aux comptes ayant un rôle | **écarté** |
| [Massive missile attacks on Ukraine](https://www.kaggle.com/datasets/piterfm/massive-missile-attacks-on-ukraine), Petro Ivaniuk | missiles et drones par mois | — | CC BY-NC-SA 4.0 | **utilisé**. Agrégats mensuels sous la même licence |
| Bombes planantes (KAB) | — | — | seulement des chiffres de presse épars | **non intégré** |
| ACLED | — | — | compte obligatoire ; publication d'agrégats transformés autorisée pour un usage non commercial | **non utilisé** : l'utilisateur doit créer le compte lui-même |
| Communiqués du ministère russe de la Défense (drones abattus, prises) | — | pro-russe | aucun jeu de données ouvert et daté | **non intégré** |
| [Natural Earth](https://www.naturalearthdata.com/) | fond de carte | — | domaine public | **utilisé** |

## Méthode du front (détail sur la page, section « Méthode »)

- **Définition** : p(x, t) est la probabilité qu'une maille de 2 km (projection azimutale équivalente de Lambert) soit sous contrôle russe à la fin du mois t.
- **Sources-cartes** : DeepStateMap, ISW, Wikipédia et Wikipédia corrigé, reconstitués à partir des statuts par localité de VIINA, plus deux enveloppes (la plus russe et la plus ukrainienne). Pour chacune, p_s = sigmoïde(d_s / σ_s), avec d_s la distance signée à sa limite et σ_s = (σ0_s + v·âge_s)·(1 + α·h).
- **Sources ponctuelles** : annonces des médias russes (prises) et ukrainiens (reprises), traitées comme des noyaux gaussiens. Une annonce compte 90 jours, à moins de 20 km du front, et tombe si le camp adverse annonce le contraire.
- **Fusion** : pool logarithmique. Chaque camp (ukrainien, russe, tiers) pèse un tiers ; une source absente ne vote pas.
- **Temps** : β = 0,3 sur l'a priori du mois précédent, diffusé de v = 0,67 km par mois.
- **Indices de combats** (pertes, feux, UCDP) : ils élargissent σ, sans jamais déplacer la ligne.
- **Calibration** : validation croisée (une source laissée de côté), puis σ0 divisés par la pente de calibration (1,79). Les résultats sont dans `data/front_calibration.csv`.
- **Incertitude** : intervalle à 90 % par Monte Carlo (40 tirages ; décalages tirés par blocs de 50 km ; sources rééchantillonnées dans chaque camp).
- **Sensibilité** : `data/front_sensibilite.csv`.
- **Limites** :
  - aucune carte pro-russe réutilisable : la vue russe est un proxy ;
  - les sources se recopient en partie ;
  - le territoire russe n'est pas couvert ;
  - DeepStateMap n'est disponible qu'à partir d'avril 2022 et l'ISW s'arrête en juin 2026.
