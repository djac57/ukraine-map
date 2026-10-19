# Ukraine : la guerre vue du ciel — état du projet

Document de reprise. Il décrit le but, les règles, l'architecture, le modèle, les résultats chiffrés, les décisions et
ce qui reste à faire. Les chiffres sont ceux du dernier build complet (octobre 2026) ; un build ultérieur les
réécrit dans `site/front.js` (méta) et dans les journaux de CI.

Dossier de recherche persistant, hors dépôt : `/Users/douglas.comin/Desktop/perso/ukraine-recherche/`.
Il contient les analyses, les prototypes et les rapports de signaux qui ont mené aux choix ci-dessous ;
voir la section « Signaux testés ».

---

## 1. But et règles de prudence

**But.** Estimer, mois par mois depuis février 2022, la probabilité p qu'une maille de 2 km soit sous contrôle russe,
en combinant toutes les sources ouvertes ; en tirer la ligne la plus probable (p = 50 %), une bande d'incertitude
honnête et des flèches « qui pousse où », sans jamais suivre d'unités.

**Règles (non négociables)**

- **J-7 au minimum** : aucune donnée de moins de 7 jours n'est affichée ; la destruction cumulée a un mois de recul de plus.
- **Agrégation seulement** : mailles de 2 km au plus fin, secteurs de 24 ou 50 km ; aucun composite fin publié.
- **Pas de suivi d'unités**, pas de position de brigades, aucune liste de frappes ni d'état de dommages récents.
  Infrastructures : statiques et publiques uniquement.
- **Pluralisme** : les sources des deux camps et les tiers ; quand une source n'a pas d'équivalent dans l'autre camp,
  l'asymétrie est documentée (ex. bilans de l'état-major UA contre bilans du MoD RF).
- **Licences vérifiées avant tout usage** ; une source incompatible n'est ni redistribuée ni versionnée.
  Faits officiels (annonces, listes, bilans) : hors droit d'auteur (Ukraine : art. 8 de la loi n° 2811-IX ;
  Russie : art. 1259 §6 du Code civil). Mentions obligatoires : Copernicus, GeoNames, OSM (ODbL), WRI (CC BY 4.0), etc.
- **Pas de « parroting »** : la détection doit venir de nos signaux (feux, alertes, radar, combats, asymétrie),
  pas de la géométrie d'une carte tierce. DeepStateMap : option A seulement (déclarations datées de son journal,
  faits), jamais sa géométrie (décision confirmée le 18 octobre 2026).
- **95 % par secteur** : la bande d'incertitude doit être vraie à chaque endroit (secteur de 24 km), pas seulement en
  moyenne.
- **Pas de nom de lieu dans les règles du modèle** : les tests sur des villes sont des diagnostics non bloquants
  (« trop strict, c'est de l'overfit »).

---

## 2. Architecture

### Scripts (`scripts/`)

| Script | Rôle |
|---|---|
| `fetch.py` | Feux FIRMS (archives, API de l'année, flux 7 jours). Clé `FIRMS_MAP_KEY` (environnement ou `~/.config/firms/credentials`), jamais affichée. |
| `fetch_osint.py` | VIINA, UCDP, Oryx, missiles, WarSpotting (reprise incrémentale). |
| `fetch_claims.py` | Aperçu public Telegram : MoD RF et état-major UA (annonces de prise, bilans quotidiens). Messages gardés en local, jamais publiés. |
| `fetch_dsmlog.py` | Journal public de DeepStateMap (déclarations datées). |
| `fetch_telegram.py`, `tglib.py`, `tg_sens.py`, `build_telegram_layer.py` | Signaux Telegram (15 canaux, aperçu public t.me/s sans compte) : relecture des 45 derniers jours (même pas d'échantillonnage que l'historique), messages de combat ou de présence gardés avec leur texte dans `raw/telegram/` (cache Actions, jamais versionné) ; construction : lieux (GeoNames UA + `data/ru_border.tsv`) ; deux attributions (§ 6) : au MESSAGE pour le modèle (`data/telegram_troncons.csv`, `tglib.events_message`), à la PHRASE pour l'affichage (mailles et `site/telegram_troncons.js`, `tglib.events`) ; « qui attaque » (`tg_sens.classify_strict`) et « présence signalée », comptes par tronçon de 24 km × mois et décade et par maille de 2 km × mois et 30 derniers jours, fusion incrémentale (une période n'est remplacée que si elle est entièrement relue et pas plus pauvre), coupure J-7. Sorties `data/telegram_troncons.csv`, `data/telegram_cells.csv`, `site/telegram_troncons.js`, `site/telegram_cells.js`. Historique reconstruit avec `--seed ukraine-recherche/signals3/internal` (avec l'ancienne attribution au message entier, il reproduisait exactement `sens_troncons_month.csv`). |
| `fetch_alerts_in_ua.py` | alerts.in.ua (menaces d'artillerie), sauté sans `ALERTS_IN_UA_TOKEN`. Identifiants d'oblast 3-31 à vérifier au premier passage. |
| `fetch_infra.py`, `build_infra.py` | Couche infrastructures (Natural Earth, WRI, OurAirports, OSM/Overpass), toute l'Ukraine. À relancer à la main de loin en loin. |
| `sar_update.py` | Radar Sentinel-1 (Planetary Computer, sans compte) : abandon agricole et bâti modifié, mensuel. |
| `build.py`, `build_osint.py`, `build_claims.py` | Feux, couches OSINT, annonces géocodées. |
| `build_official_signals.py` | Faits des bilans quotidiens (assauts et frappes de l'état-major UA, localités des bilans du MoD RF) par maille et par mois ; en CI, seuls les 3 derniers mois sont recalculés. |
| `build_front.py` | Le modèle (voir §3) : champ p, nowcast, bande locale, P(contact), z, flèches d'annonces, couches. Écrit `site/front*.js` et `data/front_*.csv`. |
| `sectors.py` | Secteurs : z-scores, P(contact), asymétrie « combats derrière la ligne », radar, faits officiels. |
| `nowcast.py` | Nowcast (les combats déplacent p), calibré en walk-forward. |
| `conformal.py` | Bande d'incertitude locale (intervalles conformes par secteur). |
| `build_advance.py` | Ampleur de l'avancée à t+1 / t+2 par tronçon de 24 km (deux étages + conformal par tronçon × classe de risque), validation walk-forward et drapeaux `show` intégrés → `site/front_advance.js` (non branché en CI ni sur la carte, § 8 point 2 quater). |
| `check_arrows.py` | Tous les contrôles (bloquants et diagnostics), voir §5. |
| `stamp.py` | Empreintes anti-cache des scripts dans les pages. |
| `envkeys.py` | Lecture des secrets depuis l'environnement, jamais écrits ni affichés (le nom évite la règle `*secret*` du .gitignore). |
| `build_toponyms.py` | `site/toponyms.js` (toponymes EN/UK/RU, GeoNames) à partir de `ukraine-recherche/i18n/places_i18n.json` ; à relancer seulement si le fond de carte change. |
| `guard.py` | Garde-fous des fichiers d'entrée. |

### Données versionnées (`data/`)

`front_areas.csv`, `front_calibration.csv`, `front_sensibilite.csv` (sorties) ; `claims_*.csv`, `dsm_log.csv`
(faits) ; `alertes_artillerie.csv` (menaces d'artillerie, mai 2022 - 7 sept. 2026) ;
`liste_hostilites_hromada_month.csv` et `hromada_cells.json` (liste officielle rejouée, hromadas → mailles) ; `sar_cells.csv` (radar,
≤ 15 km de la ligne) ; `officiels_cells_month.csv`, `officiels_couverture.csv` (bilans officiels) ;
`telegram_troncons.csv` (signaux Telegram par tronçon de 24 km × mois et × décade, comptes seulement : pas, debut,
troncon, i0, j0, camp_source, n_localises, n_combat, n_att_ua, n_att_ru, n_sq, n_presence_ua, n_presence_ru, partiel) ;
`ru_border.tsv` (localités GeoNames RU des oblasts de Koursk, Belgorod et Briansk, CC BY 4.0, entrée).

### Site (`site/`)

`i18n.js` (chaînes de la carte en FR/EN/UK/RU, `window.tr`), `toponyms.js` (toponymes), `methode_i18n.js` (titres de la
page méthode), `index.html` (carte, `?lang=xx`), `methode.html` (méthode et validations), `app.js` (toute la logique d'affichage, flèches,
info-bulles, contrôles exposés en `window.__uaf*`), `style.css`, `front.js` (méta, mois, mailles), `front_<vue>.js`
(champs quantifiés par mois : equilibre, ua, ru, tiers, nowcast, contact, combat, destr, asym, zf/zu/zw/zm, mcsd,
variantes `sans_*`), `data.js` (feux), `osint.js`, `geo.js`, `basemap.js`, `infra.js`.

### Workflows (`.github/workflows/`)

- `update.yml` : quotidien. Collecte (FIRMS, OSINT, annonces, alerts.in.ua si secret, journal DeepState, Telegram 45 j), construction,
  `stamp.py`, `check_arrows.py` ; les sorties ne sont versionnées que si les contrôles passent ; déploiement Pages.
- `sar.yml` : le 9 de chaque mois à 02:00, `sar_update.py` (45-60 min), écrit `data/sar_cells.csv`.
- **Tout YAML est validé** (`yaml.safe_load`) par `check_arrows.py` (bloquant). Un « : » non protégé dans un nom
  d'étape a déjà bloqué tout le déploiement : mettre les noms d'étape entre guillemets.

### Secrets

| Secret | État |
|---|---|
| `FIRMS_MAP_KEY` | en place. |
| `EARTHDATA_TOKEN` | en place sur djac57/ukraine-map. **À renouveler tous les 60 jours ; prochaine échéance : 17/12/2026.** HyP3 : 8 000 crédits par mois. Passer par `env: EARTHDATA_TOKEN: ${{ secrets.EARTHDATA_TOKEN }}`. |
| `ALERTS_IN_UA_TOKEN` | demandé, en attente. |

---

## 3. L'algorithme, étape par étape (formules)

Notations : x = une maille de 2 km ; t = un instantané (fin de mois, J-7) ; s = une source ; k = un secteur (tuile de
24 km) ; σ(z) = 1 / (1 + e^(−z)) la fonction logistique, logit(p) = ln(p / (1 − p)). Par convention, **p est la
probabilité de contrôle russe** partout (bande russe comprise) ; les distances signées sont **positives côté russe**.

### 3.1 Grille, projection, distances signées
- Projection azimutale équivalente de Lambert (LAEA) centrée sur 31,5° E, 48,5° N, rayon R = 6 371 007,2 m ; grille de
  2 km, NX × NY = 700 × 470 mailles (`build_front.py` : `laea`, `grid_index`, constantes `SIZE`, `X0`, `Y1`).
- Domaine : l'Ukraine dans ses frontières internationales et une bande russe de 80 km (`RU_BAND_KM`).
- Pour chaque source-carte s (DeepStateMap, ISW, Wikipédia, Wikipédia + VIINA, enveloppes) : signe par maille
  (+1 russe, −1 ukrainien, 0 contesté) d'après le statut de la localité (pavage de Voronoï des localités VIINA),
  puis distance signée d_s(x, t) au bord de la zone russe (chanfrein, plafonnée à 60 km)
  (`poly_signs`, `signed_distance`).

### 3.2 Vraisemblance par source
- p_s(x, t) = σ( clip( (d_s(x, t) − b_{s,k}(t)) / σ_s(x, t), ±12 ) ) — logit borné à ±l_max = 12 (`month_inputs`, `pool`).
- **Décalage signé par secteur** b_{s,k}(t) (`compute_offsets`) : médiane pondérée, sur les mois q ≤ t − 2 seulement,
  des écarts e = d_s à la ligne de « vérité » (consensus des trois cartes à q + 2), poids 0,5^((t−q)/12) (demi-vie
  12 mois), emprunt aux 8 tuiles voisines (poids 0,3), rétrécissement × n / (n + 60), borné à ±6 km, nul à moins de
  10 km du Dnipro.
- **Échelle** σ_s(x, t) = (σ0_s + v·âge_s(x, t)/30) · (1 + α·h(x, t)) [+ présence : × (1 + γ·présence) côté opposé] :
  - σ0_s calibré en validation croisée (chaque carte prédite par les autres ; 3 passes, pente recalée) :
    DeepStateMap 6,0 km, ISW 3,1 km, Wikipédia 5,1 km (`calibrate`) ;
  - v = vitesse typique du front (km/mois, `estimate_speed`), âge = jours depuis le dernier changement de la carte à
    cette localité alors qu'une autre carte a changé depuis (plafond 90 jours) (`stale_sigma`) ;
  - h = 1 − exp(−densité/h0), h0 = 0,1, densité lissée (~6 km) des indices de combat (feux en excès, UCDP) ; α = 0,5 ;
    γ = 0,5.

### 3.3 Annonces
- Noyaux gaussiens autour des localités annoncées (`kernel`, `kernel_xy`), force calibrée par le taux de confirmation par
  une carte tierce (noyaux actuels : MoD RF 0,2, état-major UA 0,2, médias russes 0,2, médias ukrainiens 0,73).
- Annonces officielles (MoD RF, état-major UA) : décroissance de demi-vie T½ = délai médian de confirmation par une
  carte tierce (≈ 19 jours, recalculé à chaque build) ; levée après 60 jours de contradiction par une autre carte ;
  médias (VIINA) : 90 jours, à moins de 20 km d'une zone tenue ou contestée.
- Filtre des mentions périmées (navigateur) : une annonce sur une localité que deux cartes sur trois donnaient déjà au
  camp annonceur 60 jours plus tôt n'est pas une prise.
- Bande russe : a priori logit +4 (p ≈ 0,98) ; une localité russe annoncée libérée par le MoD RF est ukrainienne
  (logit −7) dans les 45 jours précédents ; Koursk : depuis le 6/8/2024.
- **Liste officielle rejouée** (à intégrer) : au mois t, seules les versions de l'arrêté publiées avant t comptent
  (`ukraine-recherche/signals2/replay/`).

### 3.4 Fusion
- logit p(x, t) = (1/|C|) Σ_{c∈C} [ Σ_{s∈c} w_s · logit p_s(x, t) / Σ_{s∈c} w_s ], C = camps présents à la maille
  parmi {ukrainien, russe, tiers} (un tiers chacun ; une source absente ne vote pas) ; w_s : DeepStateMap 1, ISW 1,
  Wikipédia 0,5, Wikipédia + VIINA 0,5, enveloppes 0,5/1, annonces 0,5/1 (`W`, `pool`).
- Filtre temporel (`filter`) : logit p(t) = (1 − β')·obs(t) + β'·[logit p(t − 1) diffusé d'un rayon v·√(Δt/30)],
  β' = β^(Δt/30), β = 0,3 ; aucun filtre dans la bande russe.

### 3.5 Nowcast (`nowcast.py`)
- L'(x, t) = logit p(x, t) + β_n · s(x, t) · g(x, t) · tanh(trend(x, t) / 0,03) :
  - s = deuxième plus fort des indicateurs normalisés 1 − exp(−I_n / H_n) (feux en excès, UCDP, pertes WarSpotting,
    assauts rapportés par l'état-major UA), H_n = 90e centile dans la bande ; deux signaux d'accord au moins ;
  - g = max(0, 1 − dist / 30 km) à la ligne 50 % ;
  - trend = variation de p sur deux mois (le sens dans lequel la ligne bouge déjà) ;
  - β_n choisi chaque mois sur les seuls mois dont la vérité (consensus à t + 2) est connue ; valeur finale 1,0.

### 3.6 z-scores sectoriels (`sectors.py` : `z`)
- z_n(x, t) = (X_n(x, t) − μ_n(x)) / max(σ_n(x), plancher), μ et σ sur les 12 mois précédents (mois t exclu),
  X lissé à ~5 km ; plancher √μ·0,5 + 2 % du maximum (Poisson). Les feux sont des **excès** (détections moins la
  normale du même mois 2012-2021), donc déjà désaisonnalisés.
- Combats intenses : deuxième plus fort des z ≥ 2, près de la ligne.

### 3.7 P(contact au sol) (`sectors.py` : `pcontact`, `pc_features`)
- Étape 1 : logit P₀ = w₀ + Σ_j w_j · (X_j − μ_j)/σ_j, régression logistique (pénalité L2 légère) réentraînée chaque
  mois sur les 18 derniers mois étiquetés (t' ≤ t − 3) ; vérité proxy : la maille change de camp dans les 3 mois, ou
  les cartes s'y contredisent. Variables : |distance| et côté, UCDP au sol et part au sol, pertes, feux et z feux,
  urbain, continuité avec la ligne, urbain × distance, radar (abandon agricole côté russe, côté ukrainien, pas de
  cultures en 2021 ; ≤ 15 km de la ligne), état-major UA (assauts, frappes aériennes), MoD RF (localités des bilans),
  liste officielle rejouée (part des localités de la hromada en hostilités actives connue à la fin du mois, et
  indicatrice « pas encore publiée » avant décembre 2022) (`official_list`).
- Étape 2 « certaine mais rare » : logit P = logit P₀ + γ·log1p(h) + δ·1[h > 0], γ, δ ≥ 0, h = heures de menace
  d'artillerie lissées ; ajustés sur les 12 derniers mois avec logit P₀ en décalage fixe (sans alerte, P = P₀).

### 3.8 Bande d'incertitude locale (`conformal.py`)
- Erreurs signées e_k(t) : position de la ligne de vérité (consensus à t + 2) par rapport à la ligne 50 % publiée, le
  long de la distance signée D (dé-quantifiées de ±1 km).
- Bornes du secteur k au mois t : quantiles pondérés q_{α/2} et q_{1−α/2} des erreurs passées (mois ≤ t − 2), poids
  0,5^((t−s)/6) × [1 pour le secteur, 0,3·exp(−d/25 km) pour les voisins à moins de 75 km].
- Conformal adaptatif : α_k ← clip(α_k + 0,05·(0,05 − taux d'échec observé), 0,005, 0,3), avec deux mois de retard.
- Reconstruction : p(x) = σ(ln 19 · D(x) / s(x)), s = −q_{α/2} si D < 0, q_{1−α/2} sinon (bornes interpolées entre
  tuiles), si bien que les isolignes 5 % et 95 % tombent sur les bornes ; seule la ligne contestée en Ukraine est
  recalibrée.

### 3.9 Aires
- Aire attendue = Σ_x p(x)·aire(x) ; médiane = aire où p ≥ 0,5 ; zone grise = aire où 0,05 ≤ p ≤ 0,95.
- Intervalle à 95 % : Monte Carlo, 100 tirages, décalages des lignes de chaque source tirés par blocs de 50 km
  (`mc_block_km`), quantiles 2,5 et 97,5 %.

### 3.10 Flèches (`app.js`)
- Gradient (`computeArrows`) : pour chaque tronçon (100, 50, 20, 10 km) de la ligne 50 %, déplacement mensuel le long
  de la normale = écart des positions de la ligne de chaque mois (`crossing`, ±30 km, un seul côté) ; à défaut
  −Δlogit p / |∇logit p| ; pour de petits déplacements, v_n ≈ −Δp/|∇p|. Flèche si |v| ≥ v_min avec le même sens
  2 ou 3 mois sur 3, ou saut ≥ 3 km en un mois ; base sur la ligne (≤ 2 km), angle à la normale < 30°.
- Localités qui ont basculé (`eventArrows`) : annonces datées des 30 derniers jours, regroupées à 15 km, pondérées par
  la fiabilité mesurée de chaque source ; seuil de corroboration ; sens tiré du déplacement réel de la ligne
  (`lineShift`), à défaut des mailles basculées, des localités basculées, du gradient ; confronté aux localités et
  mailles basculées (sinon cercle creux) ; annonces en retard sur les cartes = confirmations.
- Incertaines : asymétrie ≥ 0,2 du côté du camp que la flèche fait avancer et dernier mois contraire.
- Garde-fou de direction (`dirViolation`, `guardDir`) : jamais vers une zone déjà tenue (p du mois précédent ≥ 0,95 ou
  ≤ 0,05), ni à travers la frontière d'État vers son propre territoire (sondé jusqu'à 10 km), tête où le camp qui
  avance était moins probable qu'à la base ; sinon réorientation le long de la limite (tangente, sens des mailles ou
  des localités basculées) ou flèche incertaine.

### 3.11 Zone de contact, combats derrière la ligne, combats intenses
- Zone de contact = P(contact au sol) (opacité des hachures).
- Combats derrière la ligne : A(x) = moyenne des indicateurs normalisés 1 − exp(−X/H) (feux, UCDP, pertes : signaux
  vus des deux côtés ; H = 90e centile des champs lissés) ; a(x) = (Ā_R − Ā_U)/(Ā_R + Ā_U), moyennes gaussiennes
  (σ = 12 km) de chaque côté de la ligne, à ≤ 10 km ; version RELATIVE : z = (a − moyenne des 12 mois précédents) /
  max(écart-type, 0,2) ; couche là où |a| ≥ 0,2, du côté dominant, et z ≥ 2 ce mois-ci ou dans les deux précédents
  (`sectors.asym`).
- Poussée possible (`app.js` : `possibleArrows`, retournement dans `computeArrows`) : flèche bleue en pointillés vers le
  côté russe quand |a| ≥ 0,4 du côté russe deux mois de suite, ou quand une flèche « avancée russe » est contredite
  par l'asymétrie (≥ 0,2) avec un dernier mois sans gain russe.

### 3.12 Secteurs susceptibles de bouger (`sectors.py` : `pmove`)

- Tronçon k = tuile de 24 km (12 × 12 mailles) traversée par la ligne. Distances à la ligne du champ des CARTES (équilibré), pas du nowcast. Position Y_{t,k} = (mailles russes du consensus
  des cartes × 4 km²) / 24 km, en km de profondeur. À défaut de consensus au mois t (dernier mois) : champ p > 0,5.
- Cible : |Y_{t+2,k} − Y_{t,k}| ≥ 0,5 km, sans sens.
- Variables à t, moyennes à ≤ 20 km de la ligne :
  - log(1 + assauts de l'état-major lissés σ = 2,5) ;
  - asymétrie a_w côté russe et son z relatif (borné à ±5) ;
  - feux en excès, UCDP, pertes (`indicator_grids`, comptes ponctuels lissés σ = 6 km puis 4 km) : 1 − exp(−lissé / P90) ;
    sans le premier lissage, l'AUC tombe à 0,78 (l'étude v4 lisait des indicateurs déjà lissés) ;
  - mouvement récent |Y_t − Y_{t−1}| et |Y_t − Y_{t−3}|.
- Régression logistique ridge (λ = 3), variables standardisées, réentraînée chaque mois sur les tronçons-mois
  q ∈ [t − 20, t − 2]. Même règle pour le dernier mois (vérité à q + 2 connue à t).
- Publication : champ `pmove` (q = 1 + 62·P sur les mailles à ≤ 3 km de la ligne, 0 ailleurs), P par tronçon dans
  `months[].pm` ([colonne, ligne, P %]) pour le survol, validation dans `FRONT.pmove`. Les pas de 3 jours reprennent le
  dernier mois calculé.
- Absents de la CI, donc retirés du modèle publié : Telegram (+0,003 d'AUC hors ligne) et liste rejouée (0).
- Validation du build (walk-forward, 2023-01 → 2026-08, n = 5 245 tronçons-mois, taux de base 13 %) : **AUC 0,805**.
  Hors ligne, l'étude donnait 0,83 avec Telegram et la liste ; le même code sur les données de l'étude : 0,80 à 0,82.

### 3.13 Validation
- Walk-forward : chaque paramètre appris au mois t n'utilise que des mois dont la vérité était connue à t.
- Brier (nowcast) sur la bande de 30 km et sur les mailles qui changent de camp ; AUC globale et « à distance égale »
  (moyenne pondérée des AUC dans les tranches 0-3, 3-6, 6-10, 10-20 km) ; couverture de la bande par secteur et par
  strate (stable, active, Dnipro) ; contributions = perte d'AUC quand on retire un signal.
- Contrôles automatiques : voir §5.

---

## 4. Résultats de validation (chiffres)

Chiffres du build d'octobre 2026 (journal du build et méta de `site/front.js`).

- **σ0 (validation croisée)** : DeepStateMap 6,0 km, ISW 3,1 km, Wikipédia 5,1 km.
- **σ local paramétrique** : rejeté (calibration stable 0,052 → 0,062, active 0,019 → 0,071).
- **Bande conforme locale** (walk-forward 2023-2026, 61 secteurs) : couverture médiane par secteur 95,4 %,
  10e centile 89,9 %, secteurs entre 90 et 99 % : 79 % (bande unique : 44 %) ; par strate : stable 94,4 %, active 95,3 %,
  Dnipro 96,9 %. L'ancienne bande publiée couvrait 99,9 % (trop large). Zone grise de septembre 2026 : 41 852 → 12 552 km².
- **Décalage par source** : 10e centile de couverture 89,2 → 89,9 % ; Brier du nowcast (mailles changeantes) 0,3516 → 0,3488.
- **Nowcast** (walk-forward 2022-10 → 2026-07) : Brier bande 0,0473 → 0,0472 ; mailles qui changent de camp
  0,3595 → 0,3434 avec les assauts de l'état-major comme 4e indicateur (0,3488 sans).
- **P(contact au sol)** : AUC walk-forward 0,925, à distance égale 0,801. Contributions (perte d'AUC à distance égale
  quand on retire le signal) : radar +0,031, liste officielle rejouée +0,027, menaces d'artillerie +0,021, état-major UA
  (faits) +0,004, UCDP au sol +0,001, MoD RF (faits) 0, pertes ≈ 0, urbain/continuité −0,001, feux −0,003.
  Black Marble testé : +0,0006, non retenu. Telegram « qui attaque / présence » : −0,0014, non retenu.
- **Liste officielle** : hors ligne, à distance égale, +0,012 rejouée contre +0,009 avec l'état actuel (dates
  rétroactives) : l'état actuel donnerait un gain trompeur.
- **Menaces d'artillerie** : à distance égale 0,681 (sans) → 0,714 (intensité) → 0,726 (indicatrice) → 0,731 (deux
  étapes, retenue) ; contact observé 73 % avec alerte, 30 % sans (≤ 10 km côté ukrainien).
- **Filtre des feux par les infrastructures** : 0,6812 → 0,6811, non retenu.
- **Combats derrière la ligne** : médiane nationale +0,04 ; Vivaldi 2026 : +0,85 (avril), +0,61 (mai), +0,43 (juin) ;
  Kharkiv 2022 : +0,19 à +0,26 avant l'offensive ; Robotyne 2023 : +0,11 à +0,20 ; Kherson 2022 : rien. Seule, elle ne
  prédit pas la reprise ukrainienne (bilan du build, par tuiles : asymétrie > +0,3 → gain russe 18 %, reprise 13 %,
  rien 68 %). Garde-fou ciblé : 33 flèches russes touchées sur 358 ; gain russe ensuite 24 % contre 45 % pour les autres.
  **Poussée ukrainienne possible** (asymétrie ≥ 0,4 côté russe deux mois de suite) : 31 cas, reprise ukrainienne 23 %
  contre 12 % en moyenne, gain russe 6 %. Inverse (combats derrière la ligne ukrainienne) : gain russe 14 % contre 24 %,
  donc pas de flèche rouge (décision de l'utilisateur en attente). Telegram comme indice de sens : reprise 36 % mais
  gain russe 30 % (signal de mouvement, pas de direction), non retenu. Correction d'octobre 2026 : seuls les signaux
  vus des deux côtés (feux, UCDP, pertes), fenêtre glissante de 12 km, échelles sur les champs lissés.
  Version relative (z ≥ 2 par rapport aux 12 mois précédents) : secteurs de 24 km, z ≥ 2 et a ≥ 0,2 → reprise UA 16 %,
  gain RU 17 % (base 12 % et 26 % ; absolu a > 0,3 : 12 % et 12 %) ; poussée possible relative (a ≥ 0,4 deux mois et
  z ≥ 1,5) : 14 cas, reprise UA 36 %, gain RU 7 %. Vivaldi ressort en avril, mai et juin 2026.
- **Juin 2026 au nord de Lyman** : la flèche rouge était la moyenne d'avril (2,19 km) et mai (0,97 km) ; juin immobile
  (0,04 km). Elle est désormais « incertaine ».

---

### KPI PRINCIPAL : information hors cartes (indicateur INTERNE, non publié)

**Objectif fixé par l'utilisateur le 18/10/2026** : faire MONTER la part d'information hors cartes (a), aujourd'hui
3,9 % (soit 96 % de dépendance aux cartes), TOUT EN améliorant la précision. Cible de v4 : (a) ≥ 10 % sur 12 mois,
sans dégrader le Brier à t+2 (mailles à ≤ 30 km et mailles qui changent) ni la couverture de la bande 95 % par secteur
(médiane ≥ 95 %, aucune strate sous 90 %). **Règle** : tout lot qui touche le champ rapporte AVANT et APRÈS les deux
familles de chiffres, dépendance (a, b, c) et précision (Brier, couverture par secteur). Une couche d'information qui
ne modifie pas le champ ne compte pas.

### Dépendance aux cartes (indicateur INTERNE, non publié)

Demandé par l'utilisateur le 18/10/2026 : on veut voir ce chiffre MONTER, c'est-à-dire la dépendance aux cartes baisser,
au fil des versions. `scripts/map_dependence.py` (imprimé à la fin de chaque `build_front.py`). Champ final = nowcast
publié (« avec signaux de combat ») ; cartes seules = champ équilibré. Bande de ±30 km autour de la ligne 50 % finale,
mailles ukrainiennes, moyenne des 12 derniers mois (et dernier mois).
- (a) part de l'information hors cartes : moyenne |logit p_final − logit p_cartes| / moyenne |logit p_final| ;
- (b) part des mailles dont l'état (russe p > 0,95 / gris / ukrainien p < 0,05) diffère ;
- (c) écart moyen de position entre les deux lignes 50 % (km, moyenne symétrique) ;
- (d) indice de l'étude v4 : |p_cartes − p_données seules| moyen 0,015 à 0,023 (hors build, `analyses/nowcast/`).

| Version | Période | (a) hors cartes | (b) état changé | (c) écart de ligne | Dernier mois (a / b / c) |
|---|---|---|---|---|---|
| 9a2616f (en ligne le 18/10) | 2025-10 → 2026-09 | 3,87 % | 3,64 % | 0,58 km | 2,89 % / 2,89 % / 0,40 km |
| lot P(mouvement) (18/10) | idem | 3,87 % | 3,64 % | 0,58 km | identique : la couche ne modifie pas le champ |
| essai Telegram dans p (19/10, non retenu) | idem | 4,05 % | 3,66 % | 0,58 km | 3,08 % / 2,93 % / 0,40 km (précision dégradée : voir § 6) |

Ce qui fera bouger ces chiffres : des signaux intégrés au champ final (nowcast), pas des couches d'information.

## 5. Contrôles (`scripts/check_arrows.py`)

Bloquants : YAML des workflows ; schéma de `front.js` (types, `grid.R` ≈ rayon terrestre) ; placement des flèches
(base sur la ligne, angle < 30°) ; sens des flèches de gradient (−∇p / +∇p) ; sens des flèches d'annonces contre le
territoire basculé ; cas connus (Avdiïvka, Otcheretyne, Vovtchansk, Koursk expansion et reflux, Niou-York/Toretsk,
Vouhledar, Selydove/Kourakhove, Pokrovsk, Koupiansk) ; sens interdits à Koursk ; flèches d'annonces (Vivaldi, Avdiïvka,
Pokrovsk, Koursk) ; fins de série (ISW, UCDP) ; vue d'ensemble (une flèche à moins de 50 km de tout tronçon en
mouvement) ; zone grise de contact détectée chaque mois.
Alertes non bloquantes : Vivaldi (flèches bleues et surface reprise), Dnipro calme. Diagnostics : P(contact) aux
centres de Kharkiv, Zaporijjia, Soumy, Dnipro, Poltava (≤ 8 %) et sur le saillant de Vivaldi.

---

## 6. Décisions de l'utilisateur (avec date)

- 2026-10 (avant le 17) : DeepStateMap option A (journal daté, faits ; pas de géométrie). Confirmé le 18/10 :
  pas de « parroting », Vivaldi doit venir de nos signaux.
- Travail LLM mis de côté (projet multi-théâtres ultérieur).
- Prévision : nos données seulement, pas l'état-major ; couche « prévision » Russie seulement, Ukraine « trop peu
  fiable pour être affichée » (≈ 1 sur 13).
- Tests nommés par ville : supprimés (« trop strict, c'est de l'overfit ») ; diagnostics non bloquants seulement.
- 17/10 : Vivaldi et Dnipro calme en ALERTE (non bloquantes).
- 18/10 : « garde tout » : destruction cumulée gardée telle quelle ; assauts et frappes de l'état-major UA autorisés
  dans P(contact) et le nowcast, origine affichée « état-major UA (faits) », exclus de la prévision ; équivalents
  MoD RF en parallèle ; collecte incrémentale des bilans en CI.
- 18/10 : menaces d'artillerie gardées, « certaines mais rares » ; canal arrêté le 7/9/2026 → « données arrêtées le … ».
- 18/10 : 95 % par secteur (intervalles conformes locaux).
- 18/10 : info-bulle seulement au zoom (k ≥ 4), 3 lignes au plus ; détails au clic.
- 18/10 : légende des flèches cerclées : « localités qui ont basculé (sources, 30 jours) ».
- 18/10 : infrastructures sur toute l'Ukraine, visibles seulement au zoom, style léger.
- 18/10 : i18n complète demandée (FR/EN/UK/RU, toponymes GeoNames, test de mots-témoins) : faite pour la carte.
- 18/10 : flèches réorientées le long de la limite plutôt que supprimées ; flèche incertaine si aucun sens établi.
- 18/10 : poussée possible : flèche BLEUE en pointillés quand on se bat derrière la ligne russe ; la flèche rouge
  symétrique n'est pas validée sur le passé, question posée à l'utilisateur.
- 18/10 : MAX abandonné ; bilans MoD RF de 2026 comblés par les reprises ouvertes (faits seulement).
- 18/10 : Telegram à tester comme indice de sens (fait : non retenu).
- 18/10 : couche « secteurs susceptibles de bouger » (P(mouvement), AUC 0,83) validée, COCHÉE par défaut : liseré
  sans sens (violet, puis AMBRE côté ukrainien le 18/10 au soir), ⓘ avec AUC et taux de base, P en % au survol (§ 3.12).
- 18/10 : flèches : une seule règle de longueur pour tous les types (pleines, pointillées, « poussée possible »,
  annonces) : proportionnelle au mouvement, plancher de lisibilité 20 px (hampe comprise), plafond 40 px en vue
  d'ensemble et 70 px au zoom (relevé le 18/10 au soir : 26 px ne laissait voir que des têtes) ; le minimum des
  pointillées ne sert qu'à la lisibilité de la hampe et ne dépasse pas ce plafond (`arrowPx`). Tête jamais en Russie
  hors bande de Koursk tenue par l'Ukraine, pour tous les types (`headGuard` : projection sur la tangente de la
  frontière, sinon pas de flèche) ; test bloquant à la longueur dessinée (vue d'ensemble, zoom 3 et 6). Cas d'origine :
  Vovtchansk, septembre 2026.
- 18/10 : légende dynamique : une entrée par case cochable, avec échantillon, visible seulement si la case est cochée
  (test bloquant LEGENDE : chaque case a son entrée en 4 langues). Styles distincts : zone grise = hachures grises,
  combats intenses = diagonales rouges, combats derrière la ligne = pointillé violet (brun, puis violet quand
  P(mouvement) est passé en ambre), secteurs susceptibles de bouger = liseré ambre #E8A200 côté ukrainien.
  Légende groupée (Ligne / Mouvements / Combats / Contexte), groupes repliables, groupe vide masqué.
- 18/10 : liseré P(mouvement) élargi en vue d'ensemble (± 10 km affichés), rien sous P = 10 %, opacité selon P.
- 18/10 : sous la carte, un seul lien « Données, méthode et sources » (4 langues) vers `methode.html#sources` ; section
  « Sources et licences » en haut de la page méthode avec toutes les attributions (ODbL, CC BY, CC BY-NC-SA, CC BY-IGO,
  Copernicus Sentinel et DEM, WarSpotting, sources officielles) ; cadence affichée conforme : instantanés MENSUELS à J-7,
  calcul relancé chaque jour, pas quotidien pas encore actif (test bloquant : lien, ancre, 4 langues).
- 19/10 (retours) : impacts S2 en LISERÉ de chaque côté de la ligne (brun côté russe, vert sombre côté ukrainien,
  opacité selon z, rien sous z = 1) ; relief hypsométrique adouci (ombrage 0,08 au lieu de 0,35, léger flou), routes plus
  épaisses et plus sombres quand le relief est coché ; Telegram : seulement les tronçons à ≤ 30 km de la ligne,
  signaux traversant la ligne en losange plein cerné, signaux du même côté (confirmation de contrôle) en losange creux ;
  infrastructures visibles dès 0,8 km/px (étiquettes dès 0,3) : pictogrammes sombres sur pastille (gare, centrale,
  aéroport, usine), voies ferrées gris foncé à traverses, étiquettes détourées.
- 19/10 : **Telegram dans le champ p (point 2 ter) : testé, NON RETENU**, pipeline inchangé
  (`ukraine-recherche/analyses/telegram_p/RAPPORT.md`). Terme de logit sur le nowcast publié, agrégats par maille de
  2 km × mois (attaques et présence signalées, côté de la maille), θ appris en walk-forward (Brier, vérité à t+2).
  AVANT → APRÈS (variante de base, 2023-01 → 2026-07) : Brier ≤ 30 km 0,03291 → 0,03296 ; mailles qui changent de camp
  0,3585 → 0,3651 ; AUC bande 0,9913 → 0,9909 ; couverture par secteur (champ publié, ligne vraie interpolée) médiane
  95,7 → 94,4 %, P10 81,0 → 81,0 %, secteurs sous 90 % 23,7 → 28,8 % ; KPI (a) 3,87 → 4,05 %. Meilleure variante sur la
  bande (≥ 4 km de la ligne) : 0,03271 mais mailles changeantes 0,3665 et couverture médiane 92,4 %. Raison : l'activité
  signalée du même côté ne « rassure » pas (la maille change de camp 6,5 fois plus souvent : 1,44 % contre 0,22 %),
  le traversant signale le mouvement (× 14) mais pas le sens (poids appris nul). Telegram reste un signal de MOUVEMENT.
- 19/10 : couche « impacts détectés (Sentinel-2) », FIGÉE, décochée : z local des impacts par tronçon de 20 km, côté
  russe (barre brune) et ukrainien (vert sombre), fenêtre 30 j (`build_impacts_layer.py` → `site/impacts_troncons.js`,
  depuis `sar/out/impacts_troncons.csv`) ; ⓘ : ~1 détection sur 2 est un cratère, Lyman z +6,9 fin mai 2026, signal faible
  sur tout le front (34 à 61 cas). Méthode : « affiché comme indicateur ».
- 18/10 nuit : couche « signaux Telegram (30 j) », FIGÉE (collecte CI à venir), décochée : par tronçon de 24 km et
  par mois, losange bleu = attaques/présence ukrainiennes signalées par 9 canaux russes, losange rouge = russes
  signalées par 5 canaux ukrainiens ; agrégats seulement (`scripts/build_telegram_layer.py` →
  `site/telegram_troncons.js`, depuis `signals3/sens_troncons_month.csv` ; aucun texte ni nom de canal) ; ⓘ : signale
  le mouvement (66 % contre 36 %), pas le sens ; données arrêtées en septembre 2026. Méthode : Telegram passe de « en
  cours » à « affiché comme indicateur ».
- 18/10 nuit : flèches TRONQUÉES juste avant une barrière dure (agent terrain : fleuve ≥ 40 m³/s ou plan d'eau,
  escarpement, canal nommé ; `site/barrieres_dures.js`, `site/troncature_fleches.js`) : ni supprimées ni réorientées,
  info-bulle « arrêtée par une barrière naturelle » ; test bloquant (aucune flèche dessinée ne franchit une barrière dure) :
  1 937 flèches tronquées sur 6 653 dessinées (zooms 1 à 8). Sur le passé, la ligne ne dépasse la barrière d'ici t+2 que
  dans 5,8 % des cas ; la troncature ramène l'écart longueur / avancée réalisée de 3,69 à 2,16 km.
- 18/10 nuit : relief HYPSOMÉTRIQUE par défaut (agent terrain : vert pâle selon l'altitude, eaux bleu pâle, ombrage
  discret ; `site/relief/hypso*`, 1,53 Mo), dessiné à la place du fond terrestre, SOUS routes, densité et étiquettes ;
  en thème sombre (non réglé) ou avec `?relief=ombrage`, l'ombrage seul. Case toujours décochée par défaut.
- 18/10 nuit : annonces officielles (MoD RF, état-major UA) en couche DÉDIÉE décochée « annonces officielles
  (30 jours) » : seulement les 30 jours précédant le pas affiché, estompées de 30 à 60 jours, jamais plus ; cliquables
  (date, localité, source, confirmation par une autre carte avec le délai, ou « non confirmée », « non jugeable »,
  « mention ancienne »). Journal publié dans `FRONT.claimLog` (modru 596 localités, gsua 65). Les cases « annonces »
  des tracés bruts sont retirées. Densité des feux LISSÉE (taches gaussiennes par maille, palette brique, anneaux pour
  les mailles du mois, plus de carrés).
- 18/10 soir : bouton « 2021 » des feux SUPPRIMÉ (bouton, tracé, légende, ligne « même mois en 2021 » du tableau) ;
  la normale 2012-2021 ne sert plus qu'au calcul des feux anormaux (décision de l'utilisateur).
- 18/10 soir : missiles et drones à DEUX SENS (une seule case). Bleu pétrole : frappes russes sur l'Ukraine (Ivaniuk ;
  Kyiv ville sans volume propre prend la teinte de l'oblast, les rapports disant « oblast de Kyiv »). Olive : frappes
  ukrainiennes SIGNALÉES sur la Russie et les zones occupées, par région et par mois (`build_strikes_ru.py`,
  `site/strikes_ru.js`, régions russes `site/regions_ru.js` tirées de Natural Earth) : jours où la région est citée
  dans les bilans d'interceptions du MoD RF (annonces ; `fetch_mod_drones.py`, fil public mod_russia, 3 862 messages,
  8 381 mentions régionales 2022-03 → 2026-10, nettement moins complet depuis avril 2026 : passage du MoD à MAX),
  mentions des autorités régionales de Belgorod, Koursk, Briansk (`data/gouverneurs_region_month.csv`, agrégats de
  signals4) et feux anormaux à ≤ 1,5 km d'un site industriel ou énergétique d'infra.js (231 sur 193 sites ; aucun
  site publié). Aucune zone de lancement ukrainienne. CI : `fetch_mod_drones.py raw --recent` (~4 000 derniers
  messages) puis `build_strikes_ru.py`, qui garde les mois déjà publiés hors de la période relue. Collecte CI des
  gouverneurs : non faite (fichier figé, à brancher). Moscou et au-delà : régions présentes mais en bord d'emprise.
- 18/10 soir : « Pistes écartées » complétée avec toutes les pistes créatives abandonnées (26 au total : brouillage GPS
  et GSM/IODA, Nova Poshta, Ukrzaliznytsia, écoles, évacuations, KTO, listes OCHA/NRC, Discord, Lostarmour,
  GeoConfirmed, cartes MoD RF et Rybar, MAX, alertes et KAB, Black Marble, Ookla, OIM, Sentinel-2 fortifications,
  impacts S1, Meteosat…), chacune avec sa raison en une ligne.
- 18/10 soir (correction de l'utilisateur) : la section devient « Pistes en cours de travail » (LLM à retravailler ;
  destruction cumulée en refonte : InSAR désaisonnalisé, validation UNOSAT et villes connues ; TERRAIN DUR : grands
  fleuves Dnipro, Siverskyi Donets, Oskil large, reliefs abrupts ; TELEGRAM gardé comme INDICATEUR affiché) et
  « Pistes écartées » (le reste). Terrain dur : au minimum contrainte d'affichage (aucune flèche ne traverse le
  Dnipro ; flèches et liseré P(mouvement) atténués le long d'une barrière dure), dans le modèle seulement si le test de
  l'agent terrain le valide. Telegram : couche d'indicateur décochée « sources russes : attaques ou présence
  ukrainiennes signalées (30 j) » et l'inverse, par tronçon, ⓘ honnête (signale le mouvement, 66 % contre 36 %, pas le
  sens) ; collecte CI des 15 canaux (agrégats seulement), ~2 h, planifiée après les frappes ukrainiennes.
- 18/10 soir : page méthode, section « Pistes testées et non retenues » (FR et EN, titre et résumé en UK et RU) :
  19 pistes avec leur raison chiffrée (LLM, données seules, Telegram, Ookla, OIM, GPSJam, IODA, éditions OSM, alertes,
  Black Marble, terrain, barrière, Sentinel-2, impacts S1, Meteosat, destruction, fortifications, filtre infra, σ local).
- 18/10 soir : tableau public de fiabilité des sources (page méthode, « Fiabilité des sources ») : par source,
  annonces jugeables, part confirmée par une autre carte sous 90 jours, délai de confirmation (P25 · médiane · P75),
  mentions anciennes écartées, part par année. Valeurs du build : journal DeepState 89 % (322 jugeables, délai médian
  19 j), état-major UA 59 % (56 ; 19 j), MoD RF 65 % (1 063 ; 19 j ; 395 mentions anciennes écartées), bascules
  DeepState 85 %, Wikipédia 75 % (délais de ~30 j : résolution des instantanés mensuels).
- 18/10 soir : FORTIFICATIONS ABANDONNÉES (décision de l'utilisateur) : OSM trop incomplet, détection automatique sur
  Sentinel-2 en échec. Rien n'est intégré. À rouvrir seulement avec l'autorisation écrite de Brady Africk.
- 18/10 soir : couche « missiles et drones » refaite (retour « tout part et arrive de partout »). Diagnostic : pas
  d'inversion ni de lieu de lancement en territoire ukrainien contrôlé (61 lieux, tous en Russie, en zone occupée, en
  mer ou en Biélorussie ; test bloquant MISSILES : 161 lancements situés en Ukraine, tous en zone tenue par la Russie au
  mois du lancement). Le « spaghetti » venait du produit cartésien lieux × oblasts visés de chaque rapport, dessiné en
  arcs pleins. Nouvelle vue : choroplèthe des oblasts visés (volume du mois) et zones de lancement (cercles ∝ √n, lieux
  identiques regroupés) ; arcs seulement au survol d'une zone ou d'un oblast (ses flux, tête côté cible) ; décochée.
- 18/10 soir : couche « destruction cumulée » RETIRÉE du site (case et légende) : échec de la validation UNOSAT
  (agent signaux 4 : AUC 0,49 sur 246 mailles de Marioupol, Kharkiv, Tchernihiv, 2022 ; rappel nul au seuil 0,2 ;
  artefact saisonnier : médiane nationale 0,35 en juillet-septembre ; Bakhmout 0,03 en mai 2023, 0,52 en juillet 2024).
  Version corrigée testée (`built_damage_excess`, persistance 3 mois consécutifs, mois à médiane > 0,1 écartés :
  13 mois) : AUC poolée 0,28 (429 mailles, la plupart sans 3 mois de radar au moment des évaluations) : échec aussi.
  Le build produit encore `front_destr.js` (non chargé) ; la destruction n'entre dans aucun modèle. À reprendre avec
  une vérité 2023-2026 (pas d'UNOSAT public après 2022) et un indice désaisonnalisé.
- 18/10 soir : liseré P(mouvement) dessiné AU-DESSUS des feux et des hachures, avec un fin contour sombre
  (drop-shadow) ; palette des feux passée en rouge brique (clair #b0301c / #9a4a3a, sombre #ff5a4a / #b8604f) : plus
  d'ocre ni d'orange clair qui noyaient l'ambre. Combinaisons vérifiées (feux + combats + zone grise + P(mouvement) +
  relief), en clair et en sombre.
- 18/10 soir : TÊTES de flèche plafonnées pour tous les types : demi-largeur ≤ 6 px (tête ≤ 12 px de large, ≤ 9,6 px
  de long) et ≤ 28 % de la longueur ; épaisseur des flèches d'annonces ≤ 5 px. Garde-fou de tête durci : la flèche
  prolongée de 4 km ne doit pas entrer en Russie (Vovtchansk 09/2026 : la flèche d'annonces pointait encore au
  nord-est aux zooms ≥ 5). Test bloquant aux zooms 1, 1,5, 2, 3, 4, 6 et 8 (taille de tête et tête hors de Russie).
- 18/10 : couche relief publiée, décochée par défaut, au-dessus de la densité (opacité 0,5). Covariables de terrain
  non retenues pour le modèle.
- 18/10 : page méthode réduite à : but, algorithme pas à pas, validation (tableaux), décisions et limites, sources et
  licences. Carte et panneaux annexes (surfaces, désaccord, feux, ciel, Oryx, repères) masqués (`div.annexe hidden`) :
  app.js, partagé avec la carte, attend encore ces éléments (dette : séparer un `methode.js`).
- 19/10/2026 : **signaux Telegram en CI** (point 2 du point de clôture). Collecte quotidienne des 15 canaux par l'aperçu
  public t.me/s (sans compte ni clé, une requête à la fois, pause 0,8 s, User-Agent explicite), fenêtre de 45 jours, même
  pas d'échantillonnage que l'historique ; reprise depuis la fin de la couverture précédente moins 3 jours ; budget de
  45 min, canaux les moins à jour d'abord. Textes seulement dans `raw/telegram/` (cache Actions ; en local
  `ukraine-recherche/signals3/internal/ci/`). Publiés : comptes par tronçon de 24 km (mois, décade) et par maille de 2 km
  (mois, 30 derniers jours), coupure J-7, aucun texte, aucun nom de canal. Fusion : une période n'est remplacée que si
  elle est entièrement relue et pas plus pauvre (95 %) ; garde-fou guard (code 3) sur les tronçons-mois.
- 19/10/2026 : **attribution à la phrase** (bug signalé : un message comptait pour toutes les localités citées, bulletins
  récapitulatifs étalés jusqu'à 760 km, losanges loin dans le camp adverse). Désormais une attaque ou une présence ne
  compte que pour les localités de sa phrase ; une phrase étalée sur plus de 30 km ne garde que son tronçon le plus
  cité (`TG_SPREAD=top` ; « drop » mesuré : 6 % de messages en moins, même part traversante) ; adjectifs de région ou
  d'axe (« Брянской области », « Херсонском направлении ») écartés : ils tombaient sur des villages homonymes, souvent en
  Crimée ; lieux reconnus aussi après « населенный пункт / село / н. п. » et dans les énumérations. Mesure sur tout
  l'historique (sources russes et ukrainiennes) : messages attribués 55 611 → 26 077 ; étalement par message p90 593 →
  128 km ; attributions message × tronçon : attaques UA 10 317 → 1 776, attaques RU 22 030 → 4 247, présence UA
  6 077 → 706, présence RU 1 921 → 281 ; présences adverses « traversantes » à plus de 6 km de la ligne 43,7 % → 38,7 %
  (le reste est surtout réel : raids et ДРГ en Russie, incursion de Koursk, infiltrations ; médiane 116 km avant le
  correctif des adjectifs).
- 19/10/2026 : **deux usages, deux attributions**. Avec l'attribution à la phrase, le modèle de sens
  (`build_direction.py`) perdait (AUC sens 0,738 → 0,707 à ≥ 1 km, 0,786 → 0,755 à ≥ 2 km) : les bulletins multi-lieux
  portent une information d'ACTIVITÉ par secteur, fausse comme « présence à tel point ». Donc :
  MODÈLE = `data/telegram_troncons.csv`, attribution au MESSAGE (`tglib.events_message`, règle de signals3) avec le
  géocodage corrigé ; AFFICHAGE = `data/telegram_cells.csv`, `site/telegram_cells.js` et `site/telegram_troncons.js`,
  attribution à la PHRASE (`tglib.events`). Validation interne de `build_direction.py` sur le CSV au message :
  AUC sens 0,760 (≥ 1 km) et 0,808 (≥ 2 km) ; flèche russe précision 0,702 / 0,528 ; « poussée possible » précision
  0,014 / 0,0 (environ 1 alerte par mois, très bruité). Même règle sans les correctifs de géocodage (`TG_GEO=v1`, mesure
  seulement) : AUC 0,747 / 0,799, poussée possible 0,066 / 0,053. Correctifs gardés (AUC meilleure ; ils retirent
  surtout des faux lieux en Crimée), la poussée possible est à revoir dans `build_direction.py`.
- 19/10 (décisions de l'utilisateur sur l'étude contre-offensives) : (1) VALIDÉ, ajouter le « dans quel sens » à
  P(mouvement), modèle à deux étages P(bouge) × P(sens | bouge), Telegram en anomalie par tronçon (deux camps), impacts
  S2 par côté utilisés « si par chance c'est disponible », sans pénaliser leur absence ; flèche « poussée russe
  probable » (ukrainienne seulement si le walk-forward le justifie) ; (2) « poussée possible » à AMÉLIORER (asymétrie +
  Telegram + S2 + P(mouvement)), pas à garder telle quelle ; (3) étude refaite au pas de 10 jours. **Fait le 19/10**
  (`ukraine-recherche/analyses/sens/RAPPORT.md`) :
  - vérité au pas de 10 jours (consensus des cartes à la décade), entrées = fichiers publiés du site + Telegram + S2 ;
  - `scripts/build_direction.py` → `site/front_sens.js`, lancé en local ; **non branché** : pas d'étape de workflow,
    affichage à intégrer par l'utilisateur (format et consignes dans le rapport et au § 8) ;
  - P(sens UA | bouge) : logistique ridge réapprise à chaque décade sur les 12 derniers mois de tronçons qui ont bougé
    d'au moins 1 km (asymétrie publiée, mouvement récent signé, Telegram 30 et 10 jours). AUC du sens (vérité
    décadaire, 2023-03 → 2026-06) : 0,78 à ≥ 1 km et 0,85 à ≥ 2 km, contre 0,67 hier (mensuel) ;
  - flèche « poussée russe probable » = P(bouge) ≥ 97e centile des 12 mois précédents ET P(sens RU) ≥ 0,8 :
    précision 0,43 (RU ≥ 2 km) contre 0,28 pour P(bouge) seule, rappel 0,16 contre 0,11, 2,6 alertes par mois dont
    1,6 fausses. Le seuil proposé hier (produit > 98e centile) ne tient pas au pas décadaire (0,25 de précision,
    6,4 alertes par mois) ;
  - pas de flèche « poussée ukrainienne probable » (précision ≤ 0,10) ;
  - « poussée possible » améliorée = P(UA | bouge) ≥ 0,3 ET [ancienne règle OU (indice additif D_UA ≥ 1,5 ET
    P(bouge) ≥ 80e centile)]. Précision 0,11 contre 0,06 (UA ≥ 1 km) et 0,08 contre 0,03 (≥ 2 km), même volume
    (1,1 alerte par mois), Vivaldi conservé (102 jours avant les cartes) ;
  - S2 : présent sans pénalité, mais sans gain dans P(sens) (0,854 → 0,847, option désactivée). Il sert dans
    l'indice de « poussée possible » et comme indice concordant ;
  - le gain du pas décadaire est de 10 à 20 jours d'avance sur S2 et Telegram (Vivaldi S2 : 61 jours contre 40), sans
    gain de précision.
- 19/10/2026 : **modèle d'AMPLEUR de l'avancée** (décision de l'utilisateur du 19/10 : « de combien de km, avec
  intervalle calibré par secteur ») ; étude `ukraine-recherche/analyses/ampleur/RAPPORT.md`, walk-forward strict
  2023-2026, mêmes tronçons-mois pour tous les modèles. Résultat :
  - le « combien » se prévoit un peu. Le modèle à deux étages (P(bouge) × P(sens | bouge) × ampleur | bouge) bat la
    persistance de −5 % (t+1) et −7 % (t+2) en CRPS. En MAE de la médiane, il fait jeu égal (0,234 contre 0,236 km à
    t+2), parce que 85 % des tronçons-mois ne bougent pas ;
  - Kalman et la tendance ne battent pas la persistance, et v4 + seuil fait jeu égal ;
  - l'apport vient du décalage du nowcast, de la zone grise et des signaux d'activité. Terrain dur, voisins et S2 :
    rien ;
  - les intervalles sont calibrés par classe de risque et par secteur. À t+2, ils couvrent de 80,6 à 96,9 % à 80 %, et
    de 92,3 à 98,7 % à 95 %. Ils font 0,07 km de large sur un tronçon calme et 5,7 km sur un tronçon à risque ;
  - les ouvertures de front surprises restent hors intervalle : Vovtchansk 7 %, Koursk 8 % des tronçons en mouvement ;
  - les offensives durables sont sous-estimées : Pokrovsk 2025 prévu à 933 km² pour 2 400 réalisés, alors que la
    tendance donnait 2 812 ;
  - km² agrégés : aucun intervalle calibré (couverture 80 % de 53 à 71 %), donc pas de prévision sur la courbe.
  **Proposition** : `scripts/build_advance.py` → `site/front_advance.js`, écrit localement et NON branché. Seul
  l'horizon t+2 par tronçon passe la règle de publication intégrée, recalculée à chaque build : CRPS de production
  0,2225 contre 0,2353, toutes les classes et tous les secteurs ≥ nominal − 5 pts. t+1 (CRPS 0,1361 contre 0,1357) et
  les km² ont `show` à faux et ne s'affichent pas. Décision d'affichage à prendre par l'utilisateur (§ 8, point 2 quater).

---

## 7. Signaux testés

| Signal | Résultat | Statut |
|---|---|---|
| Feux VIIRS en excès (et z) | utiles pour la zone de combat ; ≈ 0 à distance égale dans P(contact) | retenu |
| UCDP (sol / à distance) | faible apport | retenu |
| WarSpotting | ≈ 0 | retenu |
| Menaces d'artillerie (canal officiel) | +0,05 à distance égale (deux étapes) | retenu ; données arrêtées le 7/9/2026 |
| Alertes aériennes, KAB | rien | rejeté |
| Brouillage GPS (gpsjam) | rien ; licence absente | rejeté |
| Radar S1, abandon agricole | +0,058 | retenu |
| Radar S1, bâti modifié | couche « destruction cumulée » | couche d'information |
| Black Marble | +0,0006 | rejeté |
| Bilans état-major UA (assauts) | +0,010 (P(contact)), nowcast 0,3488 → 0,3434 | retenu |
| Bilans MoD RF (localités) | ≈ 0 ; 2024-01 → 2026-02 presque chaque jour, 2026-03 → 10 : 9 à 15 jours par mois (citations Interfax, RIA, Telegram) | retenu pour le pluralisme |
| Liste officielle des hostilités (rejouée, 59 actes) | +0,027 à distance égale | retenu |
| Telegram « qui attaque / présence » (15 canaux) | P(contact) −0,0014 ; sens : non (signale un MOUVEMENT : 66 % des tronçons cités bougent contre 36 %) ; P(mouvement) +0,003 d'AUC | signal de MOUVEMENT, jamais de sens ; dans P(mouvement) dès qu'il est collecté en CI (absent aujourd'hui) |
| Telegram dans le champ p (19/10, par maille de 2 km : même côté = confirmation, traversant = présence adverse ; terme de logit walk-forward sur le nowcast publié, `analyses/telegram_p/`) | Brier ≤ 30 km 0,03291 → 0,03296, mailles changeantes 0,3585 → 0,3651, couverture médiane par secteur 95,7 → 94,4 %, KPI (a) 3,87 → 4,05 % ; même côté : la maille change de camp 6,5 × plus souvent ; traversant : poids appris nul | NON retenu (signal de mouvement, pas de contrôle) |
| VIINA events | redondants | optionnels (v4) |
| IODA, éditions OSM | rien | rejetés |
| Sentinel-2 refait (front ±15 km, NDVI/NBR/texture/sol nu, `sar/rapport_S2.md`) | seul +0,024 d'AUC à distance égale (0,541 → 0,565) ; avec le radar 0,583 → 0,582 ; P(mouvement) 0,745 → 0,732 | redondant avec le radar ; couche de contexte et remplaçant du radar |
| Impacts KAB/obus (`sar/rapport_IMPACTS.md`) | S2 10 m : ≈ 1 détection sur 2, gradient vers la ligne, plus fort côté défenseur ; nord de Lyman : asymétrie vers le côté russe en août 2026 (1,8×), un mois avant les cartes ; S1 inexploitable | non intégré (comptes trop faibles) ; diagnostic d'asymétrie possible en saison claire |
| Ookla Open Data (baisse des tests de débit mobile + fixe, bloc 10 km, trimestriel ; CC BY-NC-SA 4.0 ; `signals4/ookla`) | agent : +0,036 à +0,046 contre une base feux+UCDP+pertes ; dans le faisceau complet du build (avec liste rejouée, radar, artillerie), masqué côté occupé après 2025-06 : AUC 0,9245 → 0,9271 (+0,0026) mais à distance égale 0,8006 → 0,7995 (−0,0011) ; contribution à distance égale −0,0011 | NON retenu (redondant avec la liste rejouée et le radar ; licence NC-SA contaminante pour un gain nul) |
| Covariables de terrain (GLO-90, WorldCover, HydroRIVERS, OSM ; `terrain/ETAT_TERRAIN.md`) | P(mouvement) +0,008 walk-forward mais −0,017 en validation spatiale ; vitesse et sens : rien ; P(contact) +0,018 → −0,011 en spatial, +0,001 avec la mémoire de la maille ; crêtes et Dnipro freinent le front (cote 0,83 par 10 m), rivières moyennes non | NON retenues (gain = mémoire du lieu) ; relief affiché comme couche |
| InSAR (cohérence 12 j, 2024-01 → 2026-06) | P(mouvement) 0,710 → 0,707 ; sens 0,68 → 0,64 | non retenu |
| Asymétrie des combats | voir §4 | couche + garde-fou ; moteur v4 à tester |

**Banc d'essai unique et matrice SIGNAL × RÔLE (19/10/2026)** — consigne : « in fine on doit TOUT tester dans les calculs
de proba, de détection et de prévision ». Code et résultats : `ukraine-recherche/analyses/banc/` (`README.md` : ajouter
un signal = un module de `signaux/`) ; matrice complète : `ukraine-recherche/analyses/banc/MATRICE.md`. 36 signaux +
2 placebos × 5 rôles (A champ p, B largeur de la bande locale, C contact, D va bouger, E sens / ampleur ; F repris de
`analyses/contre_offensives/`), walk-forward strict (standardisations et poids appris sur le passé seulement), chaque
signal mesuré sur sa période, avec erreur type et seuil de bruit (placebo par tronçon ; placebo STATIQUE pour la
« mémoire du lieu », +0,0035 d'AUC à distance égale dans P(contact)). Références du banc : A Brier ≤ 30 km 0,03291,
mailles changeantes 0,3585, KPI (a) 3,87 % ; B médiane 95,3 %, P10 90,1 %, largeur 22,7 km ; C AUC 0,923 / 0,786 à
distance égale ; D AUC 0,830 ; E sens 0,632, 1,99 km contre 2,02 pour la persistance. Résumé :
- **A champ p : aucun signal retenu.** Tous (liste, UCDP, IODA, OIM, Telegram…) baissent le Brier ≤ 30 km (jusqu'à −8 %)
  mais montent celui des mailles qui changent de camp (+0,02 à +0,09) : le constat Telegram du 19/10 vaut pour TOUS les
  signaux. Le KPI (a) monte avec n'importe quel terme (placebo : 7,9 %) : il ne vaut que joint à la précision.
- **B largeur de bande : le rôle le plus prometteur.** À couverture égale, la bande rétrécit avec P(mouvement) (−12 %),
  UCDP au sol (−13 %), terrain (−16 %), OIM (−14 %), VIINA (−8 %), IODA (−6 %) ; Telegram, liste, barrières, Ookla
  relèvent le P10 de 1,2 à 1,9 pt. « Telegram élargit la bande » : l'erreur grandit peu avec l'activité Telegram, γ appris
  ≤ 0 ; gain réel (P10 +1,7 pt, secteurs < 90 % 9,8 → 6,6 %) mais par recalibrage, pas par élargissement.
- **C contact** : production confirmée (liste +0,032, radar +0,019, artillerie +0,016, assauts +0,008, UCDP au sol
  +0,004) ; nouveaux : Sentinel-2 refait +0,019, terrain +0,016 (dont 0,0035 de mémoire du lieu), IODA +0,016, OIM
  +0,010, KAB, mouvement récent, bâti radar, VIINA +0,003 à +0,006 ; asymétrie −0,005 (nuit).
- **D va bouger** : contributions mouvement récent +0,016, assauts +0,016, pertes +0,005 ; ajouts liste +0,017 (dont
  +0,005 seulement hors 2022 : l'indicatrice « liste pas encore publiée » isole février-avril 2022), Ookla +0,011, OIM
  +0,008 ; Telegram ≤ 0. Apprendre sans février-avril 2022 : 0,830 → 0,848.
- **E sens / ampleur** : assauts de l'état-major seuls forts (sens +0,084, −0,038 km) ; attributs FIRMS +0,017 ;
  Telegram orienté +0,013 (non significatif au seuil de 0,5 km ; l'étude contre-offensives trouve +0,11 à 2 km).
- Non testables : UNOSAT (vérité 2022), direction des KAB (38 blocs-mois), écoles, journal DeepState (carte), Oryx,
  missiles, Meteosat (compte), fortifications, destruction intra-ville.
| **19/10/2026 : détecteur de contre-offensives / percées** (asymétrie + Telegram + impacts S2 + P(mouvement), tronçon de 24 km × mois, walk-forward strict, cibles ≥ 1, 2 et 5 km à 1-3 mois, 11 événements ; `ukraine-recherche/analyses/contre_offensives/RAPPORT.md`) | « va bouger » : rien ne bat P(mouvement) (ANY ≥ 2 km 0,835 ; modèles enrichis 0,78-0,81). Sens parmi les tronçons qui bougent : Telegram est le meilleur indice (AUC 0,56 → 0,67 à 2 km, 0,58 → 0,71 à 5 km), asymétrie seule 0,55. Poussées russes : deux étages P(mvt) × P(sens) avec Telegram, AUC 0,876 → 0,889, rappel à 2 % de fausses alertes 0,25 → 0,31. Contre-offensives ukrainiennes : aucun détecteur fiable (AUC orientées 0,48-0,63) ; la règle « poussée possible » reste la plus précise (18-29 %, rappel 2-14 %). Événements : Kharkiv et Kherson 2022 non anticipés ; Koursk vu 1 mois avant par Telegram (2 tronçons sur 3, hasard 5 %) ; Pokrovsk et Vouhledar 2-4 mois avant par P(mvt) ; Vivaldi : alertes en avril-mai (asymétrie, Telegram) puis en août-septembre (S2 côté russe), non chiffrable (absent du consensus du 17/10). S2 : utile pour le sens (0,67), 13 % des tronçons-mois seulement | proposition : sens probable (second étage) sur P(mouvement) quand Telegram sera en CI ; règle « poussée possible » inchangée ; rien d'intégré |
| **19/10/2026 : « dans quel sens », au pas de 10 jours** (vérité : consensus des cartes à la décade ; entrées publiées du site + Telegram 30/10 j + impacts S2 30/10 j ; walk-forward strict ; `ukraine-recherche/analyses/sens/RAPPORT.md`) | AUC du sens (2023+, ≥ 1 / ≥ 2 km) : asymétrie publiée 0,62 / 0,63 ; Telegram seul 0,60 / 0,64 ; S2 seul 0,62 / 0,67 (cas mesurables) ; additif 0,65 / 0,70 ; second étage en fenêtre glissante de 12 mois **0,78 / 0,85** (production), contre 0,64 / 0,69 en fenêtre croissante (2022 fausse l'apprentissage). Sans Telegram complet : 0,73 / 0,78 ; 2025-26 ≈ 0,5. S2 dans le sens : 0,854 → 0,847 (sans gain). Flèche RU (P(bouge) ≥ 97e c. ET P(RU) ≥ 0,8) : précision 0,43 / rappel 0,16 contre 0,28 / 0,11 pour P(bouge) seule (RU ≥ 2 km), 2,6 alertes par mois. « Poussée possible » : ancienne règle 0,06 / 0,03 de précision (≥ 1 / ≥ 2 km), règle améliorée 0,11 / 0,08 ; Kharkiv, Kherson, Robotyne et Koursk manqués, Vivaldi vu 102 jours avant. Pas de 10 jours : même précision, 10 à 20 jours d'avance sur S2 et Telegram | **retenu** : `scripts/build_direction.py` → `site/front_sens.js` (non branché) |
| **19/10/2026 : AMPLEUR de l'avancée** (km signés par tronçon de 24 km à t+1 / t+2, 39 quantiles, conformal local par tronçon × classe de risque P(mouvement) ; persistance, tendance, Kalman, v4 + seuil, deux étages, régression quantile, boosting ; `ukraine-recherche/analyses/ampleur/RAPPORT.md`) | CRPS t+2 (toutes / bougent) : persistance 0,218 / 1,70 ; tendance 0,231 / 1,67 ; Kalman 0,219 / 1,68 ; v4 0,218 / 1,69 ; **deux étages 0,203 / 1,63** ; entrées CI 0,207 / 1,69 ; QR 0,215 / 1,53 ; boosting 0,209 / 1,58 (95 % raté : 88 %). t+1 : 0,131 → 0,125. MAE : jeu égal. Ablations t+2 : sans nowcast ni zone grise 0,213 ; sans Telegram 0,204 ; sans S2, terrain dur ou voisins : 0,202-0,203 (rien). Couverture t+2 par classe de risque, 80 % : 95,9 / 83,4 / 82,3 % ; 95 % : 98,1 / 95,5 / 95,3 %. Par secteur : 80,6-96,9 % et 92,3-98,7 %. Tronçons en mouvement dans l'intervalle 80 % : ≈ 50 % (Vovtchansk 7 %, Koursk 8 %, Robotyne 78 %, Pokrovsk 2025 66 %). km² agrégés : MAE t+2 360 (tendance) à 415 (persistance), aucune couverture calibrée. Décades non faites (vérité mensuelle) | t+2 par tronçon : `scripts/build_advance.py` → `site/front_advance.js` (non branché, `show` intégré) ; t+1 et km² : non affichables |

Rapports détaillés : `ukraine-recherche/signals/`, `signals2/` (faisceau, VERDICT.txt, `replay/` pour la liste
officielle), `signals3/`, `sar/` (rapport_SAR_V2.md), `backtest/`, `front/` (journaux de build et de contrôle),
`osint/`, `i18n/` (toponymes GeoNames), `analyses/` (nowcast, conformal, v4, lyman, flèches).

---

## 8. Feuille de route

### Point de clôture du 19/10/2026 (nuit du 18 au 19)

**Fait et publié le 18/10** (détails au § 6) : secteurs susceptibles de bouger (AUC 0,80) ; flèches homogènes, têtes
plafonnées, jamais en Russie, tronquées aux barrières dures ; légende dynamique groupée ; méthode allégée, sources et
licences en tête, pistes en cours / écartées / indicateur ; relief hypsométrique ; destruction cumulée retirée (échec
UNOSAT) ; missiles et drones à deux sens ; annonces officielles en couche dédiée (30 jours, cliquables) ; tableau public
de fiabilité des sources ; densité des feux lissée ; comparaison 2021 retirée ; signaux Telegram (figés) ; KPI interne
de dépendance aux cartes (3,87 % d'information hors cartes).

**Prochaines intégrations recommandées par le banc d'essai** (19/10/2026, `ukraine-recherche/analyses/banc/MATRICE.md`,
gains walk-forward ; rien n'est intégré, pipeline inchangé) :
- (i) **Bande locale modulée par P(mouvement)** (`conformal.py`) : échelle du secteur × exp(γ·a), a = P(mouvement)
  standardisée sur le passé, γ choisi chaque mois (score de Winkler) : largeur 22,7 → 20,0 km (−12 %), couverture
  inchangée (médiane 95,3 %, P10 90,1 → 90,2 %, secteurs < 90 % 9,8 %). Aucune donnée nouvelle.
- (ii) **P(mouvement)** (`sectors.pmove`) : apprentissage sans février-avril 2022 (0,830 → 0,848), puis liste rejouée
  (→ 0,853) ; Ookla en plus → 0,862 si sa licence CC BY-NC-SA est acceptée.
- (iii) **P(contact)** (`sectors.pcontact`) : mouvement récent, bâti radar, KAB (déjà dans le dépôt) : 0,785 → 0,797 à
  distance égale ; Sentinel-2 refait (+0,019) et terrain (+0,016) ensuite, après validation par blocs spatiaux.
- Ensuite : sens des flèches (assauts + attributs FIRMS + Telegram orienté : 0,632 → 0,673), avec le second étage de
  l'étude contre-offensives ; champ p : écrire un terme qui ne resserre jamais p avant de retester (forme actuelle
  négative pour tous les signaux).

**Reste à faire, dans l'ordre proposé** :
1. **« Qui avait raison »** : pour DeepState, ISW, Wikipédia et notre ligne, distance moyenne à la vérité retardée
   (consensus à t+2), par mois et par secteur ; au survol d'un tronçon (« sur 12 mois, la source la plus proche de la
   réalité constatée ensuite est X ») et en tableau sur la page méthode, notre ligne comprise (mesure de valeur ajoutée).
2. **Telegram en CI** : **FAIT le 19/10/2026** (§ 6). Modules portés dans `scripts/` (`tglib.py` = collecte, lieux,
   maille et tronçon ; `tg_sens.py` = copie de `signals3/sens.py` ; `fetch_telegram.py` ; `build_telegram_layer.py`),
   dictionnaire `data/ru_border.tsv`. Étapes ajoutées dans `update.yml` (collecte avec budget de 45 min, construction
   après `build_strikes_ru.py`). Sorties : `site/telegram_troncons.js` (format inchangé + `upto`), `site/telegram_cells.js`
   (par maille de 2 km, nouveau, pas encore chargé par app.js), `data/telegram_troncons.csv` (tronçon × mois et × décade)
   et `data/telegram_cells.csv` (maille × mois et 30 derniers jours). Attribution au message pour le CSV des tronçons (modèle),
   à la phrase pour l'affichage (§ 6). Reste côté affichage (app.js, i18n.js, autre chantier) : retirer « données arrêtées en septembre 2026 » de
   `lgTgTip` (4 langues), charger `telegram_cells.js` pour placer les losanges à la maille.
   **Le « dans quel sens » en dépend** (19/10) : `build_direction.py` lit `data/telegram_troncons.csv` avec les
   5 comptes par camp de la source (`n_combat`, `n_ua`, `n_ru`, `n_presence_ua`, `n_presence_ru`). Il accepte `k`
   ou `troncon` + `i0`/`j0`, un temps en `date`, `jour`, `decade` ou `mois`, et `camp_source`. Le jour ou la décade
   est préférable au mois.
   - Sans ce fichier : agrégats figés de recherche en local, puis `site/telegram_troncons.js` en CI. Cette couche,
     réduite, ne garde pas les attaques revendiquées par chaque camp : l'AUC du sens tombe de 0,85 à 0,77, et à
     ≈ 0,5 en 2025-2026.
2 bis-sens. **« Dans quel sens », à intégrer à l'affichage** (19/10, `ukraine-recherche/analyses/sens/RAPPORT.md`).
   - **Production prête, non branchée** :
     - `python3 scripts/build_direction.py` tourne en ~3 s après `build_front.py` (il lit `front.js`,
       `front_equilibre.js`, `front_asym.js`, Telegram et `data/impacts_troncons_decades.csv`, copie figée de
       `sar/out/`) ;
     - il écrit `site/front_sens.js` (~216 Ko) ;
     - étape de workflow à ajouter après `build_front.py`, avant `stamp.py` / `check_arrows.py`.
   - **Format** : `window.FRONT_SENS = {steps, rows, flags, cues, rules, sources, validationEtude, validationBuild}`.
     - `steps` : décades ISO (10, 20, fin de mois) jusqu'à la date J-7.
     - `rows[i]` : liste des tronçons du pas i où P(bouge) ≥ 10 % ou qui portent un drapeau. Chaque tronçon est
       `[colonne, ligne, P(bouge) %, P(sens RU | bouge) % ou null, drapeaux, indices]`. Le centre est au même repère
       que `months[].pm` : tuile de 12 mailles, `k = (ligne // 12) * 1000 + colonne // 12`.
     - Drapeaux : 1 = poussée russe probable ; 2 = poussée possible (UA, nouvelle règle) ; 4 = ancienne règle (pour
       comparaison, à NE PAS dessiner).
     - Indices (bits) : 1 = Telegram, activité russe en excès ; 2 = Telegram, activité ukrainienne en excès ;
       4 = impacts S2 plus denses côté ukrainien ; 8 = impacts S2 plus denses côté russe ; 16 = combats derrière la
       ligne ukrainienne ; 32 = combats derrière la ligne russe ; 64 = gain russe récent ; 128 = reprise ukrainienne
       récente ; 256 = S2 disponible ; 512 = Telegram disponible.
   - **Affichage proposé** :
     - pas affiché → dernière décade ≤ la date du pas ;
     - flèche « poussée russe probable » (rouge, pleine, même règle de longueur et mêmes garde-fous) partant de la
       ligne 50 % la plus proche du centre du tronçon, vers le côté ukrainien (p < 0,5) ;
     - « poussée possible » : la flèche bleue en pointillés actuelle, mais déclenchée par le drapeau 2 au lieu de
       `possibleArrows`, qu'elle remplace ;
     - au survol, quand P(bouge) ≥ 10 % : « sens probable : avancée russe (p %) » ou « reprise ukrainienne
       (100 − p %) », plus les indices concordants en une ligne ;
     - ⓘ avec `validationEtude` : précision 0,43, rappel 0,16, 2,6 alertes par mois ; « signale surtout des
       poussées déjà engagées » ;
     - rien sur le drapeau 4.
2 quater. **« De combien », à décider puis brancher** (19/10, `ukraine-recherche/analyses/ampleur/RAPPORT.md`).
   - **Production prête, non branchée** :
     - `python3 scripts/build_advance.py` tourne en ~6 s, numpy et scipy seulement ;
     - il lit `front.js`, `front_equilibre.js` (indispensable), `front_nowcast.js` et `data/telegram_troncons.csv`
       (facultatifs : il se dégrade proprement, testé sans les deux) ;
     - il écrit `site/front_advance.js` (16 Ko) ;
     - étape de workflow à ajouter après `build_front.py`, et après Telegram s'il tourne avant.
   - **Format** : `window.FRONT_ADVANCE = {columns, horizons: {"1", "2"}}`. Pour chaque horizon :
     - `base`, `cible` ;
     - `show` = {troncons, km2} ;
     - `tiles` : [col, row, médiane, lo80, hi80, lo95, hi95, P(mvt) %, espérance], en km de profondeur (× 24 = km²),
       > 0 pour une avancée russe ; centre du tronçon au même repère que `months[].pm` ;
     - `km2`, `km2_hist` ;
     - `validation` : CRPS contre persistance, couvertures par classe et par secteur.
   - **Règle de publication** :
     - par tronçon : CRPS meilleur que la persistance, chaque classe ≥ nominal − 3 pts, chaque secteur ≥ nominal − 5 pts ;
     - km² : couverture ≥ 70 / 88 % ET meilleur que la persistance et que la tendance ;
     - aujourd'hui : t+2 par tronçon OUI ; t+1 NON ; km² NON.
   - **Affichage proposé** (simple) :
     - case décochée « avancée possible d'ici 2 mois », groupe Mouvements ;
     - seulement les tronçons dont l'intervalle 80 % atteint au moins 1 km d'un côté (13 sur 148 aujourd'hui) ;
     - barre fine perpendiculaire à la ligne : côté ukrainien, rouge pâle, longueur hi80 ; côté russe, bleu pâle,
       longueur |lo80| ; sans tête de flèche, mêmes garde-fous et même troncature aux barrières dures ;
     - au survol (k ≥ 4) : « d'ici fin novembre : entre a et b km (80 %), médiane m ; 95 % : c à d » ;
     - ⓘ : couverture par secteur, surprises (Vovtchansk, Koursk) hors intervalle, offensives durables sous-estimées ;
     - rien si `show.troncons` est faux, et aucune prévision km² sur la courbe.
   - **Suites** :
     - terme « tendance en régime établi » dans l'étage d'ampleur : Pokrovsk 2025 sous-estimé ;
     - P(sens | bouge) de `build_direction.py` (AUC 0,85) à la place du deuxième étage ;
     - pas de 10 jours sur la vérité décadaire de `analyses/sens/`.
2 ter. **Telegram dans le champ p** (demande de l'utilisateur, 19/10) : que ces signaux PÈSENT dans la prédiction ET dans
   l'état des lieux : même côté = confirmation de contrôle (resserre la bande), traversant = présence adverse (élargit
   ou fait pencher). À tester en walk-forward dans v4 avec le KPI de dépendance et la précision.
   **Testé le 19/10 : NON RETENU** (§ 6 et `ukraine-recherche/analyses/telegram_p/RAPPORT.md`) : KPI (a) 3,87 → 4,05 %
   seulement, mailles qui changent de camp 0,3585 → 0,3651, couverture par secteur en baisse. Piste restante : bande
   locale qui s'ÉLARGIT là où Telegram signale de l'activité (largeur conditionnelle dans `conformal.py`).
2 bis. **Destruction relative dans les villes** (11 villes, AUC 0,85 intra-ville) : demander à l'agent radar d'écrire
   `<ville>_cells.csv` (maille 2 km, mois, part détruite persistante) ; il n'existe aujourd'hui que des statistiques
   T à 10 m (`work_destr_hr/*_T.npz`, 10 villes). Couche ensuite ~20 min : percentile intra-ville, décochée, ⓘ
   « comparer les quartiers d'une même ville, pas les villes entre elles ».
3. **Cadence quotidienne** : mesurée le 18/10 avec `DAILY_DAYS=90` (56 mois + 29 pas de 3 jours) : 37 min 45 en local
   contre 30 à 34 min en mensuel ; tient dans le plafond CI de 90 min. Reste : activer, vérifier l'affichage des pas
   récents (couches mensuelles reprises), puis la version « incrémentale » (cache des mois historiques entre deux CI).
4. **Terrain dur dans le modèle** : seulement si le test de l'agent terrain le valide (aujourd'hui : contrainte
   d'affichage seulement).
5. **Destruction cumulée** : refonte (cohérence InSAR désaisonnalisée, vérité 2023-2026, villes connues).
6. **Gouverneurs russes en CI** (fichier figé aujourd'hui) ; FIRMS sur sites industriels déjà calculé à chaque build.
7. **Prévision russe**, URL partageables (date, zoom, calques, langue), puis le projet LLM (§ 8, point 10).
8. Dette : séparer un `methode.js` (la page méthode charge encore app.js et masque la carte), `front_destr.js` encore
   produit sans être chargé, thème sombre du relief hypsométrique.

Ordre décidé le 18/10/2026 (identique à `~/Desktop/perso/REPRISE.md`, section « Feuille de route Ukraine ») :

1. Version test : mobile, 3 styles de flèches, flèches réorientées le long de la limite, info-bulle au zoom, combats
   derrière la ligne, assauts de l'état-major. **Fait** (octobre 2026).
2. i18n complète (interface + toponymes FR/EN/UK/RU) et infrastructures de l'ouest. **Fait** pour la carte ; page
   méthode : titres en 4 langues, algorithme en FR et EN, corps en français.
3. Liste officielle des hostilités rejouée version par version (**faite**, figée à octobre 2026 : automatiser la mise à
   jour depuis zakon.rada.gov.ua), et **Telegram
   « qui attaque » / « présence signalée »** dans les probabilités (`ukraine-recherche/signals3/` :
   sens_troncons_month.csv, presence_troncons_day30.csv, bilan_sens.json, RAPPORT_TELEGRAM_SENS.md) : variables du
   nowcast et de P(contact) (attaques ukrainiennes et présence ukrainienne vues par les sources russes, attaques russes
   vues par les sources russes, présence russe vue par les sources ukrainiennes ; 30 jours, par tronçon), poids appris
   en walk-forward, interaction avec l'asymétrie à tester, ligne de survol « sources russes : attaques ukrainiennes
   signalées (30 j) » ; collecte incrémentale des 15 canaux en CI (aperçu t.me/s, sans compte, agrégats seulement).
   Testé en octobre 2026 : rien pour P(contact), et comme indice de sens il signale le mouvement plutôt que la
   direction. **En tête de v4**, comme variable d'un « P(mouvement) » ou du nowcast.
4. Moteur v4 : ligne déduite des signaux, cartes réduites à un témoin avec décalage et poids appris, ligne « données
   seules », indicateur de dépendance aux cartes ; asymétrie comme moteur de la ligne si le walk-forward le justifie.
   Premier essai (18/10/2026, `ukraine-recherche/analyses/nowcast/v4_dataonly.py`, `v4_dataonly2.py`) : témoin des cartes
   retardé de 3 mois + modèle walk-forward de bascule (distance, côté, feux, UCDP, pertes, assauts, tendance). Brier
   (≤ 30 km, 2023-2026) : persistance 0,0481 / données seules 0,0529 / cartes du mois 0,0461 ; sur les mailles qui ont
   changé de camp : 0,362 / 0,334 / 0,277. Les signaux récupèrent environ un tiers de ce qu'apportent trois mois de
   cartes sur les mailles qui bougent, mais créent trop de fausses bascules ailleurs : recalibré en walk-forward, le
   poids appris tombe à 0 (pas mieux que la persistance). Avec la liste rejouée et le radar en plus (`v4_dataonly3.py`) :
   0,0524 / 0,334, même conclusion.
   **Variante hybride** (`v4_hybrid3.py` : les cartes restent l'ossature, une correction du logit des cartes apprise
   en walk-forward anticipe le mouvement d'ici t+2). Brier (≤ 30 km, 2023-2026 ; champ de cartes ancien, avant la
   bande conforme) : cartes 0,0466 ; cartes recalibrées (a·logit p + b) 0,0296 ; hybride avec signaux 0,0264 (−11 %
   par rapport aux cartes recalibrées). Sur les mailles qui changent de camp : 0,455 → 0,457 (pas de gain : l'apport
   est près de la ligne, pas sur les bascules). Contributions (perte quand on retire) : état-major +0,0009, artillerie
   +0,0006, feux/UCDP/pertes +0,0006 ; radar, Telegram, asymétrie et liste ≈ 0 ou négatifs dans ce cadre.
   **Espace-état par tronçon** (`v4_kalman.py` : position de la ligne en km de profondeur par tuile de 24 km, filtre
   de Kalman position-vitesse ; signaux de lieu en dérive walk-forward). Erreur absolue à t+2 (toutes les tuiles /
   tuiles qui bougent ≥ 0,5 km) : persistance 0,269 / 2,013 km ; Kalman seul 0,286 / 2,010 ; Kalman + signaux
   0,441 / 1,823 (meilleur là où ça bouge, mais dérive parasite ailleurs) ; **persistance + signaux déclenchés au-delà
   d'un seuil appris : 0,267 / 1,953 km** (bat la persistance sur les deux, modestement : −3 % sur les tuiles qui
   bougent). Piste : un classifieur « le tronçon va bouger » (Telegram en P(mouvement), asymétrie relative, radar
   récent) pour déclencher la dérive, puis un vrai modèle le long de la ligne (processus gaussien).
   **P(mouvement) par tronçon** (même script) : « la ligne bougera d'au moins 0,5 km d'ici t+2 », classifieur
   walk-forward : AUC 0,831 (13 % des tronçons bougent). Apports (perte d'AUC quand on retire) : feux/UCDP/pertes
   0,037 ; mouvement récent 0,015 ; état-major 0,007 ; Telegram 0,003 ; liste 0 ; radar bâti −0,001 ; asymétrie −0,006.
   Le mouvement récent seul : 0,731. Utilisé pour déclencher la dérive : 0,272 / 1,995 km, pas mieux que le seuil
   simple. Le « où » se prévoit bien, le « de combien et dans quel sens » beaucoup moins. Une couche « secteurs
   susceptibles de bouger » : validée par l'utilisateur le 18/10/2026, publiée (voir § 3.12) ; AUC du build 0,805
   (0,83 hors ligne avec Telegram et la liste, absents de la CI).
   **Sentinel-2 et InSAR** (agent radar, `ukraine-recherche/sar/`, script `analyses/nowcast/v4_pm_sar.py`), comparés sur
   le MÊME sous-ensemble de tronçons-mois où la source existe (variables moyennées par côté à ≤ 15 km de la ligne) :
   - P(mouvement) : Sentinel-2 (anomalies NDVI, NBR, texture / 2021, sol nu, brûlures ; n = 1 033) 0,745 → 0,732 ;
     InSAR (cohérence 12 j, 2024-01 → 2026-06 ; n = 431) 0,710 → 0,707. Aucun gain.
   - SENS (« la Russie avance » parmi les tronçons qui bougent) : mouvement récent signé seul 0,55 (S2) / 0,50 (InSAR) ;
     + signaux de lieu 0,65 / 0,68 ; + S2 0,64 ; + InSAR 0,64. Les signaux de lieu aident un peu à prévoir le sens,
     l'imagerie non. L'agent radar trouve aussi que S2 n'ajoute rien à P(contact) une fois le radar V2 présent
     (0,583 → 0,582 à distance égale) et que les impacts détectés (S2 à 10 m) restent trop clairsemés pour le modèle.
   - Décision : ni S2 ni InSAR dans le modèle ; S2 reste une couche de contexte possible (non publiée). Il faut des signaux plus spécifiques du lieu de bascule
   (radar d'abandon et de destruction récents, liste rejouée, asymétrie relative, P(mouvement) Telegram) et un vrai
   modèle espace-état le long de la ligne.
5. Cadence quotidienne (fenêtre de 14 jours, décalage de 7 jours ; code présent, `DAILY_DAYS = 0`), puis prévision
   russe (« prévision (nos données) », Russie seulement ; Ukraine « trop peu fiable pour être affichée », ≈ 1 sur 13).
6. Historique InSAR (HyP3, 8 000 crédits par mois, `EARTHDATA_TOKEN`), jeton alerts.in.ua quand il arrive
   (`fetch_alerts_in_ua.py` prêt ; agrégation par hromada → maille à écrire).
7. Bilans MoD RF depuis mars 2026 (MAX abandonné : compte russe exigé) : comblés le 18/10/2026 par les citations
   d'Interfax et de RIA Novosti (Internet Archive) et les messages Telegram restants ; mil.ru (géobloqué) et VK : rien ;
   9 à 15 jours couverts par mois. À automatiser en CI (scripts dans `ukraine-recherche/modfill/`). Faits seulement.
8. ~~À refaire : Sentinel-2 (rejet trop rapide)~~ **FAIT le 18/10/2026** (`sar/rapport_S2.md` : utile seul, +0,024,
   redondant avec le radar) ; impacts KAB/obus **FAIT** (`sar/rapport_IMPACTS.md`). Reste : remarque de Molin sur le
   NOMBRE d'impacts d'obus avant Vivaldi (série par décade, z local ; confiée à l'agent radar, résultat dans `sar/`) ;
   frappes en Russie ≤ 300 km ; cartes Commons de Koursk (CC0) ; défenses connues (OSM, zone russe).
8 bis. **Chantier TERRAIN** (agent lancé le 18/10/2026, état dans `ukraine-recherche/terrain/ETAT_TERRAIN.md`) :
   covariables de relief, rivières, crêtes, lignes d'arbres, villages-forteresses ; couche relief (ombrage) sur la carte ;
   question ouverte de la vue 3D inclinée, qui demanderait de passer à MapLibre GL (à évaluer : coût, poids, licences).
8 quater. **« Mémoire de la maille » dans P(contact)** (étude terrain : 0,796 → 0,917 à distance égale) : énorme,
   mais à tester proprement (risque de circularité : la vérité « contact » est en partie l'historique de la maille).
   Validation par blocs spatiaux et décalage temporel strict avant tout usage.
8 quinquies. **Couche relief** : publiée le 18/10/2026 (ombrage Copernicus GLO-90, 3 niveaux, 2,1 Mo + courbes 0,7 Mo,
   chargés seulement quand la case est cochée ; au-dessus de la densité, opacité 0,5 ; `?relief=under` en option).
   Vue 3D (MapLibre) : NON sur la carte principale (LAEA non gérée) ; page « 3D d'un secteur » possible (2-4 j).
   A priori « barrière » pour placer la ligne : testé, NÉGATIF (erreur 2,603 → 2,603 km ; tout κ > 0 dégrade).
   Fortifications : ABANDONNÉES le 18/10 (OSM trop incomplet, détection S2 en échec) ; à rouvrir seulement avec
   l'autorisation de Brady Africk.
   Plus tard : demander l'autorisation à Playfra (X @Playfra0, dépôt GitHub playframap, surtout côté UA) et à Brady
   Africk (côté RU) pour une densité agrégée par maille de 10 km, avec crédit, non commerciale. Si les deux acceptent,
   couche symétrique. Le blocage est juridique (pas de licence, droit des bases de données), pas sécuritaire : les
   données sont publiques et connues des deux camps.
8 ter. **Telegram dans P(mouvement)** : signal de MOUVEMENT seulement, jamais de sens. Testé le 18/10 au soir avec le
   code de production (`sectors.pmove(..., tg=…)`, mentions de combat localisées par tronçon-mois, 15 canaux, fichier
   de recherche `signals3/sens_troncons_month.csv`) : AUC 0,8038 → 0,8056 (+0,002, n = 5 239), dans le bruit. La collecte
   en CI (crawler de 15 canaux, géoparsing GeoNames, agrégats seuls) n'est PAS construite : coût élevé pour un gain
   non démontré. Le paramètre `tg` reste disponible. À reprendre avec un meilleur géoparsing (phrase par phrase,
   fenêtres de 10 jours) ou plus de canaux.
9. Messages à Clément Molin (X @clement_molin, t.me/clement_molin) et à Tytelman, une fois v4 en ligne.
10. **PROJET LLM — non abandonné, à démarrer après tout le reste.** L'utilisateur y tient.
    - Un LLM qui lit la carte et les signaux : tenailles, saillants, poussées cachées, explications par secteur.
    - Backtest à l'aveugle (sans noms ni dates, rotation et miroir des cartes, un sous-agent neuf par mois) sur de
      nombreux théâtres d'opérations, et seulement sur les mois postérieurs à la date de coupure du modèle.
    - Matériel : `ukraine-recherche/backtest/llm/` (V1) et `backtest/v2/llm/` (V2, variante hybride).
    - Résultats d'octobre 2026 : LLM seul AUC 0,62 contre persistance 0,86 ; l'hybride ne bat pas la régression ; un
      point fort : la poche encerclée repérée.
    - Verdict de l'utilisateur : « en l'état pas assez bon » (et non « abandonné »).

### 8.1 bis Résultats de recherche du 18/10/2026 (recopiés tels quels de REPRISE.md ; détails dans ukraine-recherche/)

- **v4 « données seules »** (sans les cartes) : **négatif**. Brier 0,0524 contre 0,0481 pour la persistance. Les signaux voient le mouvement là où ça bouge, mais créent de fausses bascules ailleurs.
- **v4 hybride** (cartes + signaux) : −11 % de Brier près de la ligne, pas sur les bascules.
- **Espace-état (Kalman)** par tronçon : −9 % d'erreur là où ça bouge, avec dérive ailleurs. Seule la variante « persistance + signaux déclenchés au-delà d'un seuil » bat la persistance partout.
- **P(mouvement)** par tronçon (bougera d'au moins 0,5 km à t+2) : **AUC 0,83**. C'est devenu la couche « secteurs susceptibles de bouger », cochée par défaut. Le « où ça va bouger » se prévoit bien, le « combien et dans quel sens » beaucoup moins.
- **Sentinel-2 refait (`sar/rapport_S2.md`)** : le rejet initial était bien trop rapide. Seul, il ajoute +0,024 d'AUC à distance égale, mais rien une fois le radar présent : les deux mesurent l'abandon agricole. On le garde comme couche de contexte et comme remplaçant du radar.
- **Impacts KAB/obus (`sar/rapport_IMPACTS.md`)** :
  - Sentinel-2 à 10 m détecte des cratères probables, environ une détection sur deux, avec un gradient vers la ligne, plus fort du côté qui se défend ;
  - Sentinel-1 n'est pas exploitable ;
  - au nord de Lyman, l'asymétrie bascule en août 2026, un mois avant les cartes, mais les comptes sont trop faibles avant.
- **Remarque de Molin** (relayée par l'utilisateur) : le NOMBRE d'impacts d'obus a augmenté avant Vivaldi. C'est à tester précisément : série par décade et z local. Tâche confiée à l'agent radar ; résultat dans `sar/`.
- **Telegram « qui attaque »** : ne prédit pas le SENS. En revanche, il signale un secteur qui va BOUGER (66 % de mouvement contre 36 % en moyenne). À utiliser dans P(mouvement), pas comme direction.
- **Combats derrière la ligne, en version relative (z ≥ 2)** : les flèches bleues « poussée possible » ont été suivies d'une reprise ukrainienne dans 36 % des cas, contre 12 % en moyenne. Seulement 14 cas : c'est un indice.
- **Terrain** (relief, rivières, crêtes, lignes d'arbres, forteresses) et **couche relief / « 3D »** : agent lancé le 18/10, résultats dans `ukraine-recherche/terrain/ETAT_TERRAIN.md`. Une vraie vue 3D inclinée demanderait de changer de moteur de carte (MapLibre GL) : à évaluer.

### 8.1 ter Décisions du 18/10/2026, après-midi (recopiées telles quelles de REPRISE.md)

- **Petits ajouts réguliers** pour un retour fréquent : publier dès que chaque petit lot est prêt.
- **Terrain** : l'utilisateur tient à l'idée que les barrières physiques (rivières, forêts denses) fixent le front. Le test « prévision du mouvement » est négatif. Le test « emplacement de la ligne » (a priori barrière dans la bande d'incertitude) est en cours. La couche relief est prête : ombrage Copernicus GLO-90, case décochée par défaut, au-dessus de la densité.
- **Fortifications, À COMPTER DU 18/10/2026** : un instantané actuel, mis à jour régulièrement, sans historique. Côté russe seulement, couche décochée, densité par maille. Sources : OSM, détection automatique de structures linéaires sur Sentinel-2, Brady Africk seulement avec autorisation.

### 8.2 Pistes d'amélioration pour plus tard (reprises telles quelles de REPRISE.md) (brainstorm du 18/10/2026, avec tout le contexte en tête)

- **Géolocalisations de vidéos de combat** (catalogué le 19/10/2026, candidat fort pour le moteur de fusion) : comptes qui géolocalisent et datent les vidéos (ex. X @moklasen, contributeur de WarSpotting, GeoConfirmed, UAControlMap, lost_warinua, warunitobserver). Chaque fiche donne date, coordonnées et nature (« frappe sur position ukrainienne » = présence UA à ce point à cette date) : indice ponctuel, rare et fort, du type Telegram mais géolocalisé à la vidéo. Sources agrégées à privilégier : GeoConfirmed (carte publique, export à vérifier, licence à vérifier) et WarSpotting (déjà utilisé pour les pertes). Règles : on retire toute mention d'unité (pas de suivi d'unités), maille ≥ 2 km, J−7 minimum ; licences à vérifier avant usage.

- **Couloirs de drones et missiles par raïon et par semaine** (décidé le 19/10/2026, pour plus tard) : archives publiques de l'armée de l'air ukrainienne et des chaînes de suivi aérien (ex. t.me/StrategicaviationT) ; source candidate cataloguée le 19/10/2026 : t.me/falconintel (trajectoires de drones reconstituées automatiquement par nuit d'attaque + points d'impact, « not 100% accurate » ; en images, licence à vérifier, à recouper avec l'armée de l'air), texte seulement, sans compte, J−7 minimum, agrégées ; jamais de suivi en temps réel. Affine la couche « missiles et drones » (aujourd'hui à l'oblast) ; contexte, pas dans le calcul de la ligne.

**Modèle**
- Ligne latente en vrai modèle espace-état (Kalman ou filtre particulaire le long du front, processus gaussien le long de la ligne) au lieu du pool de log-odds suivi d'un lissage.
- Poids des sources hiérarchiques et variables dans le temps, par source × secteur × période. On mesure ainsi la dérive de fiabilité (ISW prudente, DeepState rapide, MoD RF en avance sur le terrain).
- Séparer deux champs, « contrôle » et « présence », pour modéliser les infiltrations et les trois sous-zones de la zone grise de Molin (contrôle UA diffus, disputé, contrôle RU diffus).
- Covariables de terrain : rivières, crêtes, lisières et haies (le front suit les « tree lines »), villages-forteresses, nœuds routiers et ferroviaires comme objectifs probables d'une poussée.
- Saisonnalité : raspoutitsa, feuillage, neige. Elles jouent sur la vitesse des poussées et sur les capteurs (feux en hiver, Sentinel-2 sous nuages).
- Prévision : intervalles conformes par secteur pour le mouvement à t+1, et étude d'événements standardisée sur toutes les contre-offensives (Kharkiv, Kherson, Robotyne, Koursk, Vivaldi).
- Signal combiné « asymétrie des combats × Telegram attaques ukrainiennes × radar » pour les contre-offensives. C'est le point faible actuel : Vivaldi n'apparaît pas assez tôt.

**Données**
- Lumières nocturnes Black Marble en entier (le compte Earthdata est en place), à la nuit et pas au mois.
- InSAR (cohérence) en historique complet. Sentinel-2 refait correctement. Bande thermique Landsat (incendies, artillerie).
- Telegram : géoparsing phrase par phrase, fenêtres de 10 jours, petit classifieur local de l'attaquant (validé sur l'échantillon annoté), plus de canaux des deux camps.
- Canaux des administrations régionales qui listent les villages bombardés chaque jour (Kherson, Zaporijjia, Kharkiv, Donetsk OVA) : série « villages bombardés par jour ».
- Historique des modifications Wikipédia et Wikidata des articles de villages : pics d'éditions au moment des combats ou prises, mesurables.
- Demander des autorisations écrites : DeepState (géométrie), GeoConfirmed, UAControlMap (50 000 vidéos géolocalisées), Brady Africk (fortifications).
- ACLED (compte gratuit à créer) pour les types d'événements, si la licence permet les agrégats.
- Coupures d'électricité et de réseau mobile par commune (cartes de couverture des opérateurs, horaires de coupure). Signal faible, à tester.
- Côté russe, combler les communiqués MoD après mars 2026 via mil.ru, TASS, RIA et Interfax (MAX abandonné).

**Produit / UX**
- « Ce qui a changé cette semaine » : un court résumé texte généré à partir des données, avec sources, sur la page et en flux RSS.
- URL partageables qui gardent la date, le zoom, les calques et la langue (permaliens par secteur).
- Export ouvert du champ p, des km² et des métriques par secteur en GeoJSON/CSV sous ODbL (obligatoire à cause de VIINA), plus une petite API JSON.
- Tableau public de fiabilité des sources : part des annonces MoD RF, état-major UA et DeepState confirmées par un tiers, délai médian. Très parlant et pluraliste. Un « vérificateur d'annonces » qui confronte chaque annonce à la réalité retardée.
- Frise des événements clés (Vivaldi, Koursk, Avdiïvka…) reliée au curseur, avec sources.
- Accessibilité : palette lisible pour les daltoniens, résumé textuel pour lecteur d'écran, contraste en mode sombre.
- Performance : rendu WebGL ou tuiles vectorielles et chargement mois par mois, nécessaires avec la cadence quotidienne.

**Fiabilité et exploitation**
- Surveillance des sources : bannière automatique quand une source s'arrête (comme le canal d'alertes au 07/09/2026 ou MoD RF sur MAX).
- Rappels de rotation des clés (Earthdata le 17/12/2026), et régénération de toutes les clés à la fin.
- Tests unitaires des extracteurs (Telegram, communiqués, liste officielle), builds reproductibles, instantanés de données en release GitHub.
- Relire la page licences et crédits (ODbL, CC BY-NC-SA des missiles, WarSpotting non commercial, Copernicus).

**Diffusion**
- Molin, Tytelman.
- **Signaler à Yuri Zhukov (auteur de VIINA) le retard de la colonne DeepState** sur le saillant de Lyman, et proposer un échange.
- Une fois v4 validée, une note méthodologique publique de type preprint.

**Généralisation, et lien avec le projet Afrique en attente.** Consigne de l'utilisateur : **d'abord bien finir l'Ukraine**. Chaque théâtre a ses spécificités et ses propres sources, il n'y aura pas de copier-coller.
- Le pipeline (FIRMS, radar, Telegram, UCDP, intervalles conformes par secteur) se transpose à d'autres fronts : **Soudan (RSF/SAF), Sahel, RDC/M23, Myanmar**. C'est un angle naturel pour le projet Afrique, et la base de tests multi-théâtres du projet LLM.

### 8.3 Notes techniques à chaud (dette, tests manquants, fragilités)

- **Ce que je ferais différemment** : poser dès le départ une vérité retardée unique (consensus à t+2) et un cadre
  walk-forward commun à tous les modules ; calculer les flèches au build (JSON testable en Python) plutôt que dans le
  navigateur ; remplacer le recalibrage a posteriori du champ (bande conforme) par un modèle génératif de la ligne (v4).
- **Dette technique** : `build_front.py` (~2 300 lignes) mêle données, modèle, validation et écriture, avec des boucles
  Python par maille (35 à 50 min par build) ; à découper et vectoriser. `app.js` (~1 900 lignes) porte la logique des
  flèches et des garde-fous ; `check_arrows.py` doit lancer un navigateur pour les tester (≈ 10 min). Champs quantifiés
  sur 6 bits (0-63) : queues de probabilité grossières. Plusieurs paramètres réglés hors ligne (garde-fous, seuils de
  flèches) ne sont pas versionnés à part.
- **Tests manquants** : tests unitaires des extracteurs (bilans de l'état-major et du MoD RF, liste officielle,
  Telegram) ; non-régression chiffrée des aires et de la couverture par strate (seuils bloquants) ; i18n de
  `methode.html` ; test mobile en CI (`cdp_test.mjs` n'est lancé qu'en local) ; test des workflows avec un brut vide.
- **Fragilités de la CI** : le cache du brut (`actions/cache`) est un point unique : s'il est perdu, la recollecte
  (Telegram ≈ 340 000 messages, FIRMS, VIINA) est longue ; `continue-on-error` sur la plupart des étapes masque des
  échecs (seules les sorties validées par `check_arrows` sont versionnées, mais une source peut rester figée sans
  alerte) ; `update.yml` et `sar.yml` peuvent pousser en même temps (`git pull --rebase` ajouté) ; build proche du
  timeout de 90 min si les données grossissent ; expiration des secrets (Earthdata le 17/12/2026).
- **Surveillance** : bannière automatique quand une source s'arrête (canal d'alertes le 7/9/2026, MoD RF sur MAX) ;
  aujourd'hui seules les fins de série ISW et UCDP sont testées.

### 8.4 Généralisation à d'autres fronts

Consigne de l'utilisateur : **on finit d'abord complètement l'Ukraine**. Chaque théâtre aura ses spécificités et ses
sources propres : ce ne sera pas un copier-coller.

- **Générique** (transposable) : grille LAEA et distances signées ; fusion par pool de log-odds avec camps équilibrés ;
  décalage par source ; filtre temporel ; nowcast par signaux physiques ; P(contact) en walk-forward ; bande
  d'incertitude conforme locale ; flèches (gradient, localités basculées, garde-fous de direction) ; asymétrie des
  combats ; validation (vérité retardée, Brier, AUC à distance égale, couverture par secteur) ; FIRMS, radar
  Sentinel-1, UCDP.
- **Propre à l'Ukraine** : VIINA (DeepStateMap, ISW, Wikipédia par localité), journal DeepStateMap, bilans de
  l'état-major ukrainien et du MoD RF, liste officielle des territoires d'hostilités, alertes d'artillerie, canaux
  Telegram RU/UA, WarSpotting, Oryx, découpage des hromadas.
- Candidats : Soudan (RSF/SAF), Sahel, RDC/M23, Myanmar (lien avec le projet Afrique et la base multi-théâtres du
  projet LLM).

---

## 9. Relancer un build local et les tests

```sh
cd /Users/douglas.comin/Desktop/perso/ukraine-feux
python3 scripts/fetch.py raw --api --nrt          # FIRMS (clé FIRMS_MAP_KEY)
python3 scripts/fetch_osint.py raw
python3 scripts/fetch_claims.py raw
python3 scripts/fetch_dsmlog.py raw/viina
python3 scripts/build.py raw
python3 scripts/build_osint.py raw
python3 scripts/build_claims.py raw raw/viina
python3 scripts/build_official_signals.py raw raw/viina      # --tout : tous les mois du brut
python3 -u scripts/build_front.py raw/viina                  # 35 à 50 min
python3 scripts/stamp.py
python3 scripts/check_arrows.py                              # 0 échec exigé
python3 -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"
```

Infrastructures (de loin en loin) : `python3 scripts/fetch_infra.py <brut>` puis
`python3 scripts/build_infra.py <brut>/infra`.

---

## 10. Pièges connus

- **Overpass** (OSM) : 406 avec un User-Agent de navigateur (utiliser « ukraine-feux/1.0 (non-commercial
  research) ») ; 502/504 fréquents : requêtes en tuiles de 2°, plusieurs essais, parfois des heures.
- **Canal d'alertes** (t.me/air_alert_ua) : muet depuis le 7/9/2026 vers 09 h (aperçu public et miroir Vadimkin) ;
  pas de remplaçant ouvert.
- **zakon.rada.gov.ua** peut bloquer une IP (403 « DDoS protection ») : copies Internet Archive.
- **Git destructif interdit** dans le projet (checkout --, reset, clean, restore, stash drop) : un `git checkout -- site/`
  a effacé des modifications non commitées. Écraser ou reconstruire les fichiers générés.
- **Formes de commande** : pas de `bash -c` ni `sh -c` imbriqués, pas de `rm`, pas de `sed -i` ; scripts dans un fichier
  et lancés directement (`python3 -I chemin/script.py`).
- **YAML** : noms d'étape avec « : » entre guillemets ; validation bloquante.
- **Fuite d'information** : toute donnée dont la disponibilité dépend du futur (emprise radar historique, dates
  rétroactives de la liste officielle) doit être restreinte ou rejouée.
- **`grid.R`** : ne jamais nommer `R` une variable locale dans `build_front.py` (rayon terrestre).
- **Variables numpy** dans `json.dumps` : `default=` déjà prévu.
