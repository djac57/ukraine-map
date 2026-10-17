# Ukraine : la guerre vue du ciel

Une carte unique, mois par mois depuis février 2022 :

- **couche principale** : probabilité d'occupation russe, agrégée à partir de sources divergentes. Les camps (ukrainien, russe, tiers) pèsent chacun un tiers. La carte trace la ligne médiane et les lignes 10 % et 90 %, et donne les km² occupés et l'écart entre la vue russe et la vue ukrainienne ;
- **couches activables** :
  - feux anormaux (NASA FIRMS, comparés à la normale 2012-2021) ;
  - pertes russes documentées (WarSpotting) ;
  - combats (UCDP) ;
  - arcs de missiles et de drones (lieu de lancement → oblast visé).
- **sous la carte** : compteurs Oryx, missiles et drones lancés.

Pages :
- `site/index.html` : la carte et la courbe des km², rien d'autre. Les options sont rangées dans le menu « calques », fermé par défaut.
- `site/methode.html` : méthode, calibration, sensibilité, sources et licences, désaccord, panneaux annexes (pertes, Oryx, feux, missiles).

Le code est commun aux deux pages (`site/app.js`, `site/style.css`). Il utilise d3 7.9.0 depuis cdnjs. Après une modification de `app.js` ou de `style.css`, il faut lancer `python3 scripts/stamp.py`.

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
| WRI Global Power Plant Database v1.3.0 (2021) | couche infrastructures : centrales | neutre | CC BY 4.0 (World Resources Institute) | **utilisé** (`site/infra.js`, mention dans la légende) |
| OurAirports (airports.csv) | couche infrastructures : aéroports | neutre | domaine public | **utilisé** |
| Natural Earth 10m railroads | couche infrastructures : voies ferrées | neutre | domaine public | **utilisé** |
| OpenStreetMap (Overpass) : gares, voies principales, zones industrielles, cokeries, mines, aérodromes | couche infrastructures | neutre | ODbL, © contributeurs OpenStreetMap | **utilisé**, objets statiques seulement |
| Canal officiel des alertes « Повітряна тривога » (t.me/air_alert_ua), aperçu public | P(contact au sol) : heures de « menace d'artillerie » par hromada et par mois | officiel ukrainien | faits publics, repris agrégés ; source citée | **utilisé** (`data/alertes_artillerie.csv`, mai 2022 – septembre 2026, figé : collecte hors workflow) |
| Vadimkin/ukrainian-air-raid-sirens-dataset (`states.json`) | table mots-dièse → hromada pour les alertes | neutre | MIT | **utilisé** (table seulement) |
| OCHA COD-AB Ukraine, niveau 3 (hromadas) | jointure alertes → mailles | neutre | CC BY-IGO | **utilisé** |
| Journal public de DeepStateMap.live (déclarations datées) | front : correction de la colonne DeepState de VIINA | pro-ukrainien | licence DeepStateMap, section 3 : contenus textuels réutilisables avec mention et lien ; API libre pour un usage bénévole non commercial (section 2) | **utilisé**. Seuls les faits sont publiés (date, localité GeoNames, sens) dans `data/dsm_log.csv` ; ni texte ni polygone |
| Communiqués du ministère russe de la Défense (aperçu public t.me/s/mod_russia) | front : annonces de prise (noyaux persistants) | pro-russe | faits non protégeables ; le Code civil russe (art. 1259 §6) exclut du droit d'auteur les documents officiels et les simples informations sur des faits | **utilisé**. Seuls les faits sont publiés (date, localité GeoNames, coordonnées, source) dans `data/claims_modru.csv` ; aucun texte n'est repris |
| Communiqués de l'état-major ukrainien (aperçu public t.me/s/GeneralStaffZSU) | front : annonces de reprise | pro-ukrainien | mêmes règles (faits seulement) | **utilisé** : `data/claims_gsua.csv` |
| Cartes du ministère russe de la Défense, Rybar (images) | — | pro-russe | droit d'auteur sur les cartes elles-mêmes | **ni numérisées ni redistribuées** |
| UAControlMap, cartes de M0nstas, Playfra | — | variable | aucune licence de réutilisation publiée | **cités comme références seulement** |
| Fortifications | — | — | aucun jeu ouvert trouvé | **pas de couche** |
| Wikimedia Commons, « August 2024 Kursk Oblast incursion.svg » (CC0) et « Kursk front of the Russo-Ukrainian War.svg » (CC BY-SA 4.0) | tiers | — | réutilisables (attribution et partage à l'identique pour la seconde) | **pas encore intégrées** : le géoréférencement version par version reste à faire |
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
- **Sources ponctuelles** :
  - annonces des médias russes et ukrainiens relevées par VIINA : 90 jours, à moins de 20 km du front ;
  - annonces officielles du ministère russe de la Défense et de l'état-major ukrainien : force divisée par deux tous les ~N jours (délai médian de confirmation d'une annonce MoD RF par une carte tierce, recalculé à chaque construction) ; elles tombent quand une carte d'un autre camp ou tierce les contredit trois mois de suite.
  - âge par localité : une carte que contredit un changement plus récent d'une autre carte y vieillit depuis ce changement (σ élargi de v par mois, 90 jours au plus).
  - fins de série : une carte qui s'arrête (ISW, fin juin 2026) garde son dernier statut et s'efface avec l'âge ; les indices dont le dernier mois manque (UCDP) reprennent le dernier mois disponible. `check_arrows.py` vérifie que ces fins ne déplacent pas la ligne (déplacement national médian < 0,3 km).
  - journal public de DeepStateMap (`scripts/fetch_dsmlog.py`) : les déclarations datées « X libérée / occupée » corrigent la colonne DeepState de VIINA quand elle est en retard (faits seulement, `data/dsm_log.csv`). La géométrie de DeepStateMap n'est pas utilisée : des changements non annoncés peuvent manquer (été 2026 au nord de Lyman).
  - Toutes sont traitées comme des noyaux gaussiens dont la force est calibrée par leur taux de confirmation par les sources tierces.
- **Présence** : les pertes russes géolocalisées (WarSpotting) prouvent une présence russe. Côté ukrainien de la ligne, elles élargissent σ (γ) sans faire basculer le contrôle. Rien d'équivalent n'est réutilisable côté ukrainien.
- **Trois états** : contrôle russe (p > 0,9), zone grise (0,1 – 0,9), contrôle ukrainien (p < 0,1). La surface et la largeur moyenne de la zone grise sont publiées par mois et par secteur. Le concept vient de l'analyste OSINT Clément Molin (@clement_molin).
- **Russie frontalière** : la grille s'étend sur 80 km côté russe, sans découpage à la frontière. Pas de filtre temporel dans cette bande (preuves déjà rétrospectives), et le filtre ukrainien ne la diffuse pas à travers la frontière.
  - L'a priori y est « russe » (logit +4).
  - Les preuves de contrôle ukrainien sont les localités annoncées « libérées » par le MoD RF (contrôle ukrainien avant cette date, depuis le 6 août 2024 pour Koursk) et les prises annoncées par l'état-major ukrainien.
  - La carte n'y montre que la probabilité de contrôle ukrainien (en bleu, au-delà de 5 %).
  - Les km² sous contrôle ukrainien en Russie (ligne médiane) forment une courbe séparée : environ 500 à 730 km² d'août 2024 à février 2025.
- **Flèches « qui pousse où »** : déplacement de la ligne 50 % le long de sa normale, mois par mois (position de la ligne de chaque mois cherchée à 30 km au plus, du seul côté possible ; ≈ −Δp/|∇p| pour de petits déplacements, sans le plafond de 0,5/|∇p|), sur des tronçons de 100, 50, 20 ou 10 km. Une flèche apparaît si |v| moyen ≥ 0,7 km/mois avec le même sens 3 mois sur 3 (2 sur 3 au-delà de 1 km/mois), ou après un saut ≥ 3 km en un mois. Vue d'ensemble : les trois échelles sont regroupées et éclaircies à une flèche tous les 30 km. Les flèches sont calculées dans le navigateur à partir de `front_*.js`. `scripts/check_arrows.py` contrôle le placement, les cas connus, le cas calme (Dnipro) et la couverture de la vue d'ensemble ; en cas d'échec, rien n'est publié.
- **Nowcast (signaux de combat)** (`scripts/nowcast.py`, coché par défaut sur la page) : logit p' = logit p + β · s · g(d) · tanh(tendance / 0,03). s = deuxième plus fort de trois indicateurs normalisés (feux en excès FIRMS, combats UCDP, pertes WarSpotting) ; g = max(0, 1 − d / 30 km) ; tendance = variation locale de p sur deux mois. β est choisi chaque mois en walk-forward sur la vérité « consensus des cartes deux mois plus tard » (Brier sur la bande de 30 km et sur les mailles qui changent de camp) ; les formes « zone contestée » et « côté des combats » ont été testées et rejetées. Aucune donnée d'état-major. Sorties : `site/front_nowcast.js` (champ), `site/front_combat.js` (couche « combats intenses », score rapporté à sa moyenne nationale près de la ligne), bilan de validation dans `front.js` (`nowcast`).
- **Flèches d'annonces** : changements de contrôle datés des 30 jours précédant l'instantané (journal DeepStateMap, état-major UA, MoD RF, bascules DeepStateMap et Wikipédia de VIINA), pondérés par la fiabilité mesurée de chaque source (part confirmée par une autre carte sous 90 jours), regroupés par camp à 15 km ; il faut deux sources, ou une annonce datée et deux localités (une carte qui recode seule ses localités est une révision). Données dans `front.js` (`months[].ev`, `events`), tracé dans le navigateur.
- **Zone grise de contact** (`scripts/sectors.py`) : P(contact au sol) par maille, régression logistique walk-forward (vérité proxy : bascule dans les 3 mois ou désaccord des cartes) sur des variables génériques sans nom de lieu : distance signée à la ligne, combats UCDP au sol et leur part (les frappes de missiles, drones et aviation sont écartées d'après les champs UCDP, `build_osint.py`, couche `ucdp.ground`), pertes WarSpotting, feux aux coordonnées des détections VIIRS et leur z, masque urbain, continuité avec la ligne, heures de « menace d'artillerie » des alertes officielles (indice ponctuel, côté ukrainien seulement ; gain walk-forward à distance égale 0,681 → 0,714, présent chaque année et des deux côtés). Sortie `site/front_contact.js` (P × 63). La distance à la ligne porte l'essentiel ; à distance égale, les signaux ajoutent peu (AUC 0,68 contre 0,66 sans les alertes). Les alertes sont figées au dernier mois collecté (report de deux mois au-delà) : la collecte du canal (≈ 338 000 messages) n'est pas encore dans le workflow.
- **z-scores sectoriels** : chaque maille (lissée ~5 km) par rapport à ses 12 mois précédents, pour les feux, les combats UCDP, les pertes et le mouvement de la ligne (`site/front_zf.js`, `front_zu.js`, `front_zw.js`, `front_zm.js`, q = (z + 2) × 5) ; couche « combats intenses » = moyenne des deux plus forts z ≥ 2.
- **σ local** : σ0 × m(x), m appris sur les erreurs locales de la validation croisée (volatilité de la ligne, activité anormale, désaccord local, Dnipro et réservoir de Kakhovka) ; plancher 1 km ; rétrécissement limité à 0,5 ; calibration vérifiée par strate (stable, active, Dnipro), bilan dans `front.js` (`localSigma.validation`).
- **Infrastructures** (`scripts/fetch_infra.py`, `scripts/build_infra.py`, couche décochée ; la collecte Overpass, découpée en tuiles de 2°, échoue souvent en 502/504 et se relance) : voies ferrées (Natural Earth, OpenStreetMap), gares des localités de plus de 20 000 habitants, centrales (WRI Global Power Plant Database v1.3.0, CC BY 4.0), zones industrielles de plus de 1 km², cokeries et mines, aéroports (OurAirports) et aérodromes ; statiques et publiques, sans information de frappe ni de dommages. Données stables : à relancer à la main de loin en loin. Testé comme filtre des feux pour P(contact) (feux retirés à moins de 1 à 2 km des sites industriels, cokeries, mines et centrales thermiques, 618 mailles) : aucun gain en walk-forward (AUC à distance égale 0,6812 → 0,6811), car ces mailles ne portent presque aucun feu en excès (part médiane < 0,1 %). Filtre non retenu ; la couche reste un contexte.
- **Cadence** : le code gère des pas récents de 3 jours sur 90 jours (dernier pas à J-7, signaux de combat sur 14 jours, filtre temporel ajusté au pas) ; désactivés pour l'instant (`DAILY_DAYS = 0` dans `build_front.py`) en attendant le calcul incrémental.
- **Préparation des flèches « qui pousse où » (V2)** : p est sauvegardé par mois et par vue (`site/front_*.js`), ainsi que l'écart-type Monte Carlo de p par maille (`site/front_mcsd.js`, 0-63 pour 0-0,5).
- **Variantes « sans X »** : elles sont précalculées pour chaque source, et la page permet de retirer une source à la fois du calcul.
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
