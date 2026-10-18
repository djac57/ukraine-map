"""Densité de probabilité p(x, t) = P(maille x sous contrôle russe au mois t), à partir de sources divergentes.

    python3 scripts/build_front.py <dossier_viina> [--sans-indices] [--rapide]

Entrées :
- VIINA 2.0 (Zhukov et Ayers, ODbL) dans <dossier_viina> : control_<année>.zip (statut quotidien de
  33 141 localités selon DeepStateMap, ISW, Wikipédia et Wikipédia corrigé), ev1pd_<année>.zip
  (dépêches géocodées des médias des deux camps), gn_UA_tess.geojson (pavage des localités) ;
- indices de proximité du front, déjà agrégés par les autres scripts : site/data.js (mailles de feux
  en excès), site/osint.js (pertes WarSpotting, événements UCDP).
Sorties : site/front.js (métadonnées, surfaces, calibration), site/front_<vue>.js (champs de probabilité
quantifiés, un fichier par vue), data/front_areas.csv, data/front_calibration.csv, data/front_sensibilite.csv.

Modèle (détaillé sur la page, section « Méthode ») :
1. chaque source-polygone s (DeepStateMap, ISW, Wikipédia, Wikipédia corrigé, et les deux enveloppes
   « la plus russe » / « la plus ukrainienne » des cartes) donne p_s(x) = sigmoïde(d_s(x) / σ_s), où d_s
   est la distance signée (km) à la limite de la zone que s classe russe, positive côté russe ;
   σ_s = (σ0_s + v·âge_s) · (1 + α·h(x)), h = densité locale d'indices de combats (pertes, feux, UCDP) ;
2. les annonces de prises (médias russes) et de reprises (médias ukrainiens) relevées par VIINA sont
   des noyaux gaussiens autour de la localité, de rayon ≈ sa taille ;
3. fusion par pool logarithmique : logit p = Σ w_s logit p_s, poids normalisés par camp (un tiers
   chacun pour les camps ukrainien, russe et tiers), répartis entre les sources présentes ; une
   source qui ne couvre pas la maille ne vote pas ;
4. filtre temporel : logit p(t) = (1-β)·observation(t) + β·[logit p(t-1) diffusé de v·1 mois] ;
5. σ0_s calibrés par validation croisée (une source laissée de côté), v estimé sur l'historique ;
6. intervalle à 95 % des km² par Monte Carlo (lignes décalées dans σ_s, sources rééchantillonnées).
"""
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import zipfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402

SRC_DIR = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "viina"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAST = "--rapide" in sys.argv

# ---------- grille ----------
R = 6371007.2
LON0, LAT0 = 31.5, 48.5
SIZE = 2000.0
CELL_KM = SIZE / 1000
X0, X1, Y0, Y1 = -700e3, 700e3, -470e3, 470e3
NX, NY = int((X1 - X0) / SIZE), int((Y1 - Y0) / SIZE)
N = NX * NY

# ---------- sources et poids ----------
VIINA = ["dsm", "isw", "wiki", "boost"]
POLY = ["dsm", "isw", "wiki", "boost", "uamin", "rumax"]          # contrôle, tracés (via VIINA)
POINT = ["uaclaims", "ruclaims", "gsua", "modru"]                  # contrôle, annonces (noyaux)
CAMP = {"dsm": "ua", "uamin": "ua", "uaclaims": "ua", "gsua": "ua", "isw": "tiers", "wiki": "tiers",
        "boost": "tiers", "rumax": "ru", "ruclaims": "ru", "modru": "ru"}
W = {"dsm": 1.0, "uamin": 0.5, "uaclaims": 0.5, "gsua": 1.0, "isw": 1.0, "wiki": 0.5, "boost": 0.5,
     "rumax": 1.0, "ruclaims": 0.5, "modru": 1.0}
CAMPS = ("ua", "ru", "tiers")
VIEWS = {"equilibre": CAMPS, "ua": ("ua",), "ru": ("ru",), "tiers": ("tiers",)}
# variantes « sans X » (vue équilibrée), précalculées pour les cases à cocher de la page
VARIANTS = {
    "sans_dsm": {"drop": {"dsm"}}, "sans_isw": {"drop": {"isw"}}, "sans_wiki": {"drop": {"wiki", "boost"}},
    "sans_enveloppes": {"drop": {"uamin", "rumax"}}, "sans_medias": {"drop": {"uaclaims", "ruclaims"}},
    "sans_modru": {"drop": {"modru"}}, "sans_gsua": {"drop": {"gsua"}},
    "sans_presence": {"presence": False}, "sans_indices": {"cues": False},
}
CODE = {"": 0, "UA": 1, "RU": 2, "CONTESTED": 3}
SIGN = {1: -1, 2: 1, 3: 0}
RANK = {1: 0, 3: 1, 2: 2}
FRONT_ADM1 = ["Kharkiv", "Luhans'k", "Donets'k", "Zaporizhzhya", "Kherson", "Dnipropetrovs'k", "Sumy", "Mykolayiv", "Chernihiv", "Kiev"]

DEFAULT = {
    "cap_km": 60.0,        # au-delà, la maille est « loin du front » : logit saturé à ±lmax
    "lmax": 12.0,          # logit borné à ±12
    "mc_block_km": 50.0,   # Monte Carlo : décalages tirés par blocs de 50 km
    "alpha": 0.5,          # indices de combats (feux en excès, UCDP) : σ × (1 + α·h)
    "h0": 0.1,             # h = 1 - exp(-densité / h0), densité lissée sur ~6 km
    "gamma": 0.5,          # présence documentée du camp adverse : σ × (1 + γ·présence) côté opposé
    "beta": 0.3,           # part de l'a priori (mois précédent diffusé) dans le filtre temporel
    "claim_logit": 1.73,   # annonces des médias : p = 0,85 au centre (recalé par la calibration)
    "claim_days": 90,      # annonces des médias : 90 jours
    "near_km": 20.0,       # … à moins de 20 km d'une zone tenue / contestée
    "official_stale": 3,
    "official_decay": True,       # décroissance des annonces officielles (sinon : persistantes jusqu'au démenti)
    "stale_local": True,          # âge réel par localité
    "stale_cap_days": 90,         # plafond de l'âge par localité : au-delà, la carte ne perd plus de poids (évite une dérive sans fin)
    "official_half_life": 30.0,  # jours ; recalculé : délai médian entre une annonce du MoD RF et sa confirmation par une carte tierce
    "band_prior": 4.0,     # bande russe : a priori « russe » (logit +4, p ≈ 0,98) que seules des preuves de contrôle renversent
    "band_ua_ctrl": 7.0,   # localité russe annoncée « libérée » par le MoD RF : contrôle ukrainien avant cette date (logit −7 au centre)   # annonce officielle levée après 3 mois de démenti par les autres sources du même camp
    "sigma_scale": 1.0,
    "v_scale": 1.0,
    "proxies": True,
    "camp_balance": True,
    "presence": True,
    "cues": True,
}
RU_BAND_KM = 80.0       # bande côté russe (Koursk, Belgorod, Briansk…) : même champ p, sans découpage à la frontière
RU_CLAIM_RADIUS = 2.5   # rayon des noyaux d'annonces sur des localités russes (pas de pavage) : km
LAG_DAYS = 7
PRE_DATE = "20220223"
CRIMEA_ADM1 = {"11", "20"}
RU_MEDIA = {"ria", "ntv", "kp", "ng"}
UA_MEDIA = {"24tvua", "espreso", "liga", "nv", "pravdaua", "unian", "militarnyy", "interfaxua", "forbesua"}
MC_DRAWS = 12 if FAST else 100
INF = 1e9


def laea(lon, lat):
    l, p, l0, p0 = map(math.radians, (lon, lat, LON0, LAT0))
    k = math.sqrt(2 / (1 + math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0)))
    return R * k * math.cos(p) * math.sin(l - l0), R * k * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0))


def inv_laea(x, y):
    rho = math.hypot(x, y)
    if rho == 0:
        return LON0, LAT0
    c = 2 * math.asin(rho / (2 * R))
    p0, l0 = math.radians(LAT0), math.radians(LON0)
    lat = math.asin(math.cos(c) * math.sin(p0) + y * math.sin(c) * math.cos(p0) / rho)
    lon = l0 + math.atan2(x * math.sin(c), rho * math.cos(p0) * math.cos(c) - y * math.sin(p0) * math.sin(c))
    return math.degrees(lon), math.degrees(lat)


def grid_index(lon, lat):
    x, y = laea(lon, lat)
    i, j = int((x - X0) // SIZE), int((Y1 - y) // SIZE)
    return j * NX + i if 0 <= i < NX and 0 <= j < NY else -1


def in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


LATIN = re.compile(r"^[A-Za-z'’ʼ` -]+$")


def name_key(n):
    """Clé de translittération tolérante : Seredne = Serednie, Kolodyazi = Kolodiazi, Zelenyi = Zelenyy."""
    n = re.sub(r"['’ʼ`]", "", n.lower()).replace("-", " ").strip()
    for a, b in (("kh", "h"), ("zh", "z"), ("ts", "c"), ("ch", "c"), ("sh", "s"), ("y", "i"), ("ii", "i"),
                 ("ie", "e"), ("ia", "a"), ("iu", "u"), ("io", "o"), ("j", "i")):
        n = n.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", n)


def carry_forward(status):
    """Une carte dont la série s'arrête (l'ISW fin juin 2026) n'est pas retirée d'un coup : son dernier statut connu
    est prolongé, et son âge (σ0 + v · âge) le fait s'effacer progressivement. La retirer brutalement faisait sauter
    le champ (≈ +1,5 km en moyenne sur tout le front en juillet 2026)."""
    days = sorted(status)
    ended = {}
    for n, s in enumerate(VIINA):
        have = [d for d in days if any(c[n] for c in status[d].values())]
        if have and have[-1] != days[-1]:
            last = have[-1]
            ended[s] = last
            keep = {g: c[n] for g, c in status[last].items() if c[n]}
            for d in days:
                if d > last:
                    st = status[d]
                    for g, code in keep.items():
                        if g in st:
                            st[g] = st[g][:n] + (code,) + st[g][n + 1:]
    if ended:
        print("séries arrêtées, prolongées par leur dernier statut (vieillissement progressif) : "
              + ", ".join(f"{s} ({d[:4]}-{d[4:6]}-{d[6:]})" for s, d in ended.items()), flush=True)
    return ended


def apply_dsm_log(status, places):
    """Corrige la colonne DeepState de VIINA par les déclarations datées de DeepState (« X libérée le jour J »).

    Une déclaration prime sur le statut VIINA tant que VIINA n'a pas changé depuis (sinon VIINA est plus récent) et
    qu'aucune déclaration contraire ne l'a remplacée. (Un plafond de 365 jours ramenait des erreurs de codage de VIINA :
    Kherson, codée russe côté DeepState de 2022 à 2024.) Localités sans coordonnées : par leur nom, parmi les homonymes proches du front ce jour-là
    (localités ukrainiennes et russes à moins de 10 km), au plus près des autres liens du message. Écrit les faits
    retenus dans data/dsm_log.csv et renvoie le nombre de corrections par instantané."""
    path = os.path.join(SRC_DIR, "dsm_log_raw.json")
    if not os.path.exists(path):
        print("journal DeepState absent : colonne DeepState de VIINA non corrigée", flush=True)
        return {}, []
    rows = json.load(open(path, encoding="utf-8"))
    days = sorted(status)
    pidx = {p["id"]: k for k, p in enumerate(places)}
    byname, bucket = defaultdict(set), defaultdict(list)
    for k, p in enumerate(places):
        for n in p["names"]:
            if n:
                byname[name_key(n)].add(k)
        bucket[(round(p["lat"] * 10), round(p["lon"] * 10))].append(k)

    def km(k, lat, lon):
        return math.hypot((places[k]["lon"] - lon) * 111.3 * math.cos(math.radians(lat)), (places[k]["lat"] - lat) * 111.3)

    def around(lat, lon, r):
        out = []
        for a in range(-2, 3):
            for b in range(-2, 3):
                out += [k for k in bucket.get((round(lat * 10) + a, round(lon * 10) + b), ()) if km(k, lat, lon) <= r]
        return out

    def ref_day(date):
        later = [d for d in days if d >= date]
        return later[0] if later else days[-1]

    def near_front(k, day):
        st, seen = status[ref_day(day)], set()
        for q in around(places[k]["lat"], places[k]["lon"], 10):
            seen |= {c for c in st.get(places[q]["id"], ()) if c in (1, 2)}
        return seen == {1, 2}

    resolved, pending = [], []
    for r in rows:
        day = r["date"].replace("-", "")
        if r["lat"] is not None:
            c = [k for k in around(r["lat"], r["lon"], 2)]
            k = min(c, key=lambda q: km(q, r["lat"], r["lon"])) if c else None
        else:
            c = [q for q in byname.get(name_key(r["name"]), ()) if near_front(q, day)]
            if r["ctx"]:
                la, lo = sum(x[0] for x in r["ctx"]) / len(r["ctx"]), sum(x[1] for x in r["ctx"]) / len(r["ctx"])
                c = [q for q in c if km(q, la, lo) <= 40]
                k = min(c, key=lambda q: km(q, la, lo)) if c else None
            else:
                k = c[0] if len(c) == 1 else None
                if len(c) > 1:
                    pending.append((day, r["camp"], c))
                    continue
        if k is not None:
            resolved.append((day, r["camp"], k))
    # homonymes restants : au plus près des localités déjà situées dans les déclarations du même jour
    for day, camp, c in pending:
        same = [k for d, cp, k in resolved if d == day]
        if same:
            la, lo = sum(places[k]["lat"] for k in same) / len(same), sum(places[k]["lon"] for k in same) / len(same)
            best = min(c, key=lambda q: km(q, la, lo))
            if km(best, la, lo) <= 40:
                resolved.append((day, camp, best))
    events = defaultdict(list)
    for day, camp, k in sorted(set(resolved)):
        events[places[k]["id"]].append((day, 1 if camp == "ua" else 2))
    with open(os.path.join(ROOT, "data", "dsm_log.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "geonameid", "nom", "lon", "lat", "camp"])
        for day, camp, k in sorted(set(resolved)):
            p = places[k]
            w.writerow([f"{day[:4]}-{day[4:6]}-{day[6:]}", p["id"], p["name"], round(p["lon"], 4), round(p["lat"], 4), camp])
    # correction, instantané par instantané (VIINA garde la main s'il a changé après la déclaration)
    fixed, last, since, prev_day = {}, {}, {}, None
    for day in days:
        st, n = status[day], 0
        for g, codes in st.items():
            old = last.get(g)
            for i, c in enumerate(codes):
                if old is None or old[i] != c:
                    since[(g, i)] = prev_day or "00000000"  # cette colonne a changé après l'instantané précédent
            last[g] = codes
        for g, evs in events.items():
            e = [x for x in evs if x[0] <= day]
            if not e or g not in st:
                continue
            eday, code = e[-1]
            # la déclaration tombe si VIINA-DeepState a changé depuis, ou si une autre carte est passée depuis au
            # statut contraire (Vovtchansk « libérée » en 2022, reprise ensuite par la Russie sans « occupée » au journal)
            if since.get((g, 0), "00000000") >= eday or any(
                    c and c != code and since.get((g, i), "00000000") >= eday for i, c in enumerate(st[g]) if i):
                continue
            if st[g][0] != code:
                st[g] = (code,) + tuple(st[g][1:])
                n += 1
        fixed[day] = n
        prev_day = day
    print(f"journal DeepState : {len(rows)} déclarations, {len(set(resolved))} localités situées, "
          f"{sum(fixed.values())} corrections de la colonne DeepState de VIINA (instantanés cumulés)", flush=True)
    return fixed, sorted(set(resolved))


EV_SOURCES = ["dsmlog", "gsua", "modru", "dsm", "wiki"]   # journal DeepState, état-major UA, MoD RF, bascules des cartes
EV_JUDGES = {"dsmlog": (1, 2), "dsm": (1, 2), "gsua": (1, 2), "modru": (1, 2), "wiki": (0, 1)}


def control_events(status, places, official, dsm_events):
    """Changements de contrôle datés (flèches d'événements) et fiabilité mesurée de chaque source.

    Événements : déclarations du journal DeepState, annonces de l'état-major ukrainien et du MoD RF (localités
    ukrainiennes et russes), bascules des colonnes DeepState (corrigée) et Wikipédia de VIINA d'un instantané au
    suivant. Fiabilité : part des événements qu'une AUTRE carte (ISW, Wikipédia ; DeepState et ISW pour Wikipédia)
    confirme dans les 90 jours. Renvoie [(AAAAMMJJ, lon, lat, camp 1 russe / 0 ukrainien, source)] et {source: taux}."""
    days = sorted(d for d in status if d != PRE_DATE)
    pidx = {p["id"]: k for k, p in enumerate(places)}
    ev = []
    # mention ancienne : localité que les cartes (2 sur 3) donnaient déjà au camp annonceur 60 jours avant l'annonce ;
    # ce n'est pas une prise (ex. : « Vouhledar » dans des communiqués du MoD RF de novembre 2024, prise début octobre)
    stale_n = defaultdict(int)

    def stale(g, d, camp):
        if not g:
            return False
        ref = (dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) - dt.timedelta(days=60)).strftime("%Y%m%d")
        before = [x for x in days if x <= ref]
        if not before:
            return False
        c = status[before[-1]].get(g)
        return bool(c) and sum(1 for x in c[:3] if x == (2 if camp == 1 else 1)) >= 2

    for day, camp, k in dsm_events:
        c1 = 1 if camp == "ru" else 0
        if stale(places[k]["id"], day, c1):
            stale_n["dsmlog"] += 1
            continue
        ev.append((day, places[k]["lon"], places[k]["lat"], c1, "dsmlog", places[k]["id"]))
    for src, camp in (("modru", 1), ("gsua", 0)):
        for key, dates in official.get(src, {}).items():
            if isinstance(key, tuple):
                lon, lat, g = key[1], key[2], None
            else:
                lon, lat, g = places[key]["lon"], places[key]["lat"], places[key]["id"]
            for d in dates:
                if stale(g, d, camp):
                    stale_n[src] += 1
                    continue
                ev.append((d, lon, lat, camp, src, g))
    print("annonces écartées comme mentions anciennes (localité déjà au camp annonceur 60 jours avant) : " + ", ".join(f"{k} {v}" for k, v in stale_n.items()), flush=True)
    for prev, day in zip(days, days[1:]):
        a, b = status[prev], status[day]
        for g, codes in b.items():
            old = a.get(g)
            if not old:
                continue
            for n, src in ((0, "dsm"), (2, "wiki")):
                if old[n] in (1, 2) and codes[n] in (1, 2) and old[n] != codes[n] and g in pidx:
                    k = pidx[g]
                    ev.append((day, places[k]["lon"], places[k]["lat"], 1 if codes[n] == 2 else 0, src, g))
    rate = {}
    for src in EV_SOURCES:
        ok = tot = 0
        for d, lon, lat, camp, s, g in ev:
            if s != src or not g:
                continue
            d0 = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
            later = [x for x in days if d < x <= (d0 + dt.timedelta(days=90)).strftime("%Y%m%d")]
            codes = [status[x].get(g) for x in later]
            judged = [c[j] for c in codes if c for j in EV_JUDGES[src] if c[j] in (1, 2)]
            if not judged:
                continue
            tot += 1
            ok += (2 if camp == 1 else 1) in judged
        rate[src] = round(ok / tot, 3) if tot else 0.5
    print("événements datés : " + ", ".join(f"{s} {sum(1 for e in ev if e[4] == s)} (confirmés par un tiers : {rate[s]:.0%})" for s in EV_SOURCES), flush=True)
    return [e[:5] for e in sorted(ev)], rate


def rasterize(tess_path):
    gj = json.load(open(tess_path))
    places, polys = [], []
    for f in gj["features"]:
        g, pr = f["geometry"], f["properties"]
        if not g:
            continue
        idx = len(places)
        places.append({"id": str(int(pr["geonameid"])), "adm1": str(pr.get("admin1_code") or "").split(".")[0],
                       "adm1n": pr.get("ADM1_NAME") or "", "name": pr.get("name") or "",
                       "names": [pr.get("name") or "", pr.get("asciiname") or ""] + [a for a in str(pr.get("alternatenames") or "").split(",") if LATIN.match(a)],
                       "lat": float(pr["latitude"]), "lon": float(pr["longitude"])})
        for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            xs = [q[0] for q in poly[0]]
            ys = [q[1] for q in poly[0]]
            polys.append((idx, poly, (min(xs), min(ys), max(xs), max(ys))))
    bucket = defaultdict(list)
    for k, (_, _, (x0, y0, x1, y1)) in enumerate(polys):
        for a in range(int(math.floor(x0 * 10)), int(math.floor(x1 * 10)) + 1):
            for b in range(int(math.floor(y0 * 10)), int(math.floor(y1 * 10)) + 1):
                bucket[(a, b)].append(k)
    cell = [-1] * N
    for j in range(NY):
        y = Y1 - (j + 0.5) * SIZE
        for i in range(NX):
            lon, lat = inv_laea(X0 + (i + 0.5) * SIZE, y)
            for k in bucket.get((int(math.floor(lon * 10)), int(math.floor(lat * 10))), ()):
                idx, poly, (x0, y0, x1, y1) = polys[k]
                if x0 <= lon <= x1 and y0 <= lat <= y1 and in_ring(lon, lat, poly[0]) \
                        and not any(in_ring(lon, lat, h) for h in poly[1:]):
                    cell[j * NX + i] = idx
                    break
    return cell, places


def read_status(dates_needed):
    want = {d.strftime("%Y%m%d") for d in dates_needed} | {PRE_DATE}
    status, seen_dates = defaultdict(dict), set()
    for path in sorted(f for f in os.listdir(SRC_DIR) if f.startswith("control_") and f.endswith(".zip")):
        with zipfile.ZipFile(os.path.join(SRC_DIR, path)) as z:
            with z.open(z.namelist()[0]) as fh:
                head = fh.readline().decode().strip().split(",")
                col = {c: head.index(f"status_{c}") for c in VIINA}
                for line in io.TextIOWrapper(fh, encoding="utf-8"):
                    a = line.split(",", 2)
                    seen_dates.add(a[1])
                    if a[1] not in want:
                        continue
                    r = line.rstrip("\n").split(",")
                    status[a[1]][r[0]] = tuple(CODE.get(r[col[c]], 0) for c in VIINA)
    return status, max(seen_dates)


def read_claims():
    claims = defaultdict(list)
    for path in sorted(f for f in os.listdir(SRC_DIR) if f.startswith("ev1pd_") and f.endswith(".zip")):
        with zipfile.ZipFile(os.path.join(SRC_DIR, path)) as z:
            with z.open(z.namelist()[0]) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")):
                    ctl, occ = r.get("t_control_b") == "1", r.get("t_occupy_b") == "1"
                    if not (ctl or occ) or not r.get("geonameid"):
                        continue
                    src = {x.strip() for x in r["sources"].split(",")}
                    gid = str(int(float(r["geonameid"])))
                    if r.get("a_rus_b") == "1" and src & RU_MEDIA:
                        claims[gid].append((r["date"], "ru"))
                    if r.get("a_ukr_b") == "1" and ctl and src & UA_MEDIA:
                        claims[gid].append((r["date"], "ua"))
    for v in claims.values():
        v.sort()
    return claims


def claim_state(lst, day, window_start):
    last = None
    for d, side in lst:
        if d > day:
            break
        last = (d, side)
    return last[1] if last and last[0] > window_start else None


# ---------- géométrie sur la grille ----------
def chamfer(seeds, cap_cells):
    """Distance (unités de chanfrein 3-4 : 3 par maille) à la graine la plus proche, dans une fenêtre."""
    D = [INF] * N
    if not seeds:
        return D
    js = [k // NX for k in seeds]
    is_ = [k % NX for k in seeds]
    m = int(cap_cells / 3) + 2
    j0, j1 = max(0, min(js) - m), min(NY, max(js) + m + 1)
    i0, i1 = max(0, min(is_) - m), min(NX, max(is_) + m + 1)
    for k in seeds:
        D[k] = 0
    for j in range(j0, j1):
        b = j * NX
        for i in range(i0, i1):
            k = b + i
            v = D[k]
            if i > i0:
                t = D[k - 1] + 3
                if t < v:
                    v = t
            if j > j0:
                u = k - NX
                t = D[u] + 3
                if t < v:
                    v = t
                if i > i0:
                    t = D[u - 1] + 4
                    if t < v:
                        v = t
                if i < i1 - 1:
                    t = D[u + 1] + 4
                    if t < v:
                        v = t
            D[k] = v
    for j in range(j1 - 1, j0 - 1, -1):
        b = j * NX
        for i in range(i1 - 1, i0 - 1, -1):
            k = b + i
            v = D[k]
            if i < i1 - 1:
                t = D[k + 1] + 3
                if t < v:
                    v = t
            if j < j1 - 1:
                u = k + NX
                t = D[u] + 3
                if t < v:
                    v = t
                if i < i1 - 1:
                    t = D[u + 1] + 4
                    if t < v:
                        v = t
                if i > i0:
                    t = D[u - 1] + 4
                    if t < v:
                        v = t
            D[k] = v
    return D


def signed_distance(sign, U, inU, cap_km):
    """sign[k] ∈ {-1, 0, 1} (None : la source ne dit rien). Renvoie {maille: d en km}, positif côté russe."""
    seeds = []
    for k in U:
        s = sign[k]
        if s is None:
            continue
        if s == 0:
            seeds.append(k)
            continue
        for q in (k - 1, k + 1, k - NX, k + NX):
            if 0 <= q < N and inU[q]:
                t = sign[q]
                if t is not None and t != s:
                    seeds.append(k)
                    break
    D = chamfer(seeds, cap_km / CELL_KM * 3)
    out = {}
    for k in U:
        s = sign[k]
        if s is None:
            continue
        dist = D[k] / 3 * CELL_KM + CELL_KM / 2
        out[k] = 0.0 if s == 0 else s * (dist if dist <= cap_km else 1e4)
    return out


def box_blur(vals, r):
    if r < 1:
        return vals[:]
    w = 2 * r + 1
    out = [0.0] * N
    for j in range(NY):
        b = j * NX
        s = 0.0
        for i in range(0, min(r + 1, NX)):
            s += vals[b + i]
        for i in range(NX):
            out[b + i] = s / w
            a, c = i - r, i + r + 1
            if a >= 0:
                s -= vals[b + a]
            if c < NX:
                s += vals[b + c]
    out2 = [0.0] * N
    for i in range(NX):
        s = 0.0
        for j in range(0, min(r + 1, NY)):
            s += out[j * NX + i]
        for j in range(NY):
            out2[j * NX + i] = s / w
            a, c = j - r, j + r + 1
            if a >= 0:
                s -= out[a * NX + i]
            if c < NY:
                s += out[c * NX + i]
    return out2


def gauss_blur(vals, sigma_cells):
    """Trois flous boîte de rayon r : variance ≈ r(r+1), donc r ≈ σ."""
    r = max(0, int(round(sigma_cells)))
    out = vals
    for _ in range(3):
        out = box_blur(out, r)
    return out


def sig(x):
    return 1 / (1 + math.exp(-x))




def box_blur_win(vals, r, win):
    """Flou boîte limité à une fenêtre (j0, j1, i0, i1) ; hors fenêtre, valeurs inchangées."""
    j0, j1, i0, i1 = win
    out = vals[:]
    w = 2 * r + 1
    tmp = vals[:]
    for j in range(j0, j1):
        b = j * NX
        s = 0.0
        for i in range(i0, min(i0 + r + 1, i1)):
            s += vals[b + i]
        for i in range(i0, i1):
            tmp[b + i] = s / w
            a, c = i - r, i + r + 1
            if a >= i0:
                s -= vals[b + a]
            if c < i1:
                s += vals[b + c]
    for i in range(i0, i1):
        s = 0.0
        for j in range(j0, min(j0 + r + 1, j1)):
            s += tmp[j * NX + i]
        for j in range(j0, j1):
            out[j * NX + i] = s / w
            a, c = j - r, j + r + 1
            if a >= j0:
                s -= tmp[a * NX + i]
            if c < j1:
                s += tmp[c * NX + i]
    return out


def gauss_blur_win(vals, sigma_cells, win):
    r = max(1, int(round(sigma_cells)))
    out = vals
    for _ in range(3):
        out = box_blur_win(out, r, win)
    return out


# ---------- indices : combats (feux, UCDP) et présence (pertes documentées) ----------
def load_layers(months):
    def js(name, var):
        path = os.path.join(ROOT, "site", name)
        if not os.path.exists(path):
            return None
        s = open(path, encoding="utf-8").read()
        return json.loads(s[len(f"window.{var}="):].rstrip().rstrip(";"))
    D = js("data.js", "DATA")
    O = js("osint.js", "OSINT")
    midx = {m: i for i, m in enumerate(months)}
    combat = defaultdict(list)
    presence_ru = defaultdict(list)
    if D:
        for mi, cells in enumerate(D["cells"]):
            m = D["months"][mi]
            for j in range(0, len(cells), 4):
                combat[midx[m]].append(((cells[j + 1] + 0.5) * D["cell"], (cells[j] + 0.5) * D["cell"],
                                        max(0.0, cells[j + 2] - cells[j + 3]) * 0.1))
    if O:
        # fin de série (UCDP publié avec un mois de retard, etc.) : les derniers mois manquants reprennent le dernier
        # mois disponible, pour que l'arrêt d'une source ne fasse pas bouger la ligne
        def carry(d, label):
            if not d:
                return
            last = max(d)
            for mi in range(last + 1, len(months)):
                d[mi] = list(d[last])
            if last + 1 < len(months):
                print(f"{label} : arrêt après {months[last]}, dernier mois reconduit jusqu'en {months[-1]}", flush=True)
        ucdp = defaultdict(list)
        p = O["ucdp"]["pts"]
        for j in range(0, len(p), 5):
            ucdp[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 3])))
        carry(ucdp, "UCDP")
        for mi, pts in ucdp.items():
            combat[mi].extend(pts)
        p = O["ws"]["pts"]  # pertes RUSSES documentées : présence russe à cet endroit ce mois-là
        for j in range(0, len(p), 5):
            presence_ru[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 4])))
        carry(presence_ru, "WarSpotting")
    # présence ukrainienne en Russie : une reprise annoncée par le MoD RF prouve une présence ukrainienne
    # dans les semaines qui précèdent (75 jours) ; elle élargit l'incertitude sans faire basculer le contrôle
    presence_ua = defaultdict(list)
    path = os.path.join(ROOT, "data", "claims_modru.csv")
    if os.path.exists(path):
        ends = []
        for m in months:
            y, mo = int(m[:4]), int(m[5:])
            ends.append((dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)))
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if not r["geonameid"].startswith("ru"):
                continue
            d = dt.date.fromisoformat(r["date"])
            for mi, e in enumerate(ends):
                if e - dt.timedelta(days=75) < d <= e + dt.timedelta(days=45):
                    presence_ua[mi].append((float(r["lon"]), float(r["lat"]), 3.0))
    return combat, presence_ru, presence_ua, (ucdp if O else {})


LOCAL_SIGMA_ON = False  # σ local ajusté et validé, mais non appliqué : erreur de calibration stable 0,052 → 0,062, actif 0,019 → 0,071
LOCAL_MIN = 0.5      # σ local : rétrécissement maximal (0,2 rendait les secteurs actifs trop sûrs : 95 % → 86 % observés)
DAILY_DAYS = 0       # période récente en pas de quelques jours (90 à l'étape « NOWCAST 2 » ; 0 : tout mensuel)
STEP_DAYS = 3        # pas de la période récente
WINDOW_DAYS = 14     # fenêtre glissante des indicateurs de combat d'un pas récent


class WindowLayers:
    """Indicateurs de combat d'un pas récent : fenêtre glissante de WINDOW_DAYS jours finissant à la date du pas.
    Feux : détections des mailles en excès (site/data.js, datées au jour), moins la normale de la maille au prorata
    des jours ; UCDP et pertes WarSpotting : mois de la date (publiés au mois) ; présence ukrainienne en Russie :
    annonces du MoD RF de la fenêtre −75 / +45 jours autour de la date."""

    def __init__(self, months, layers):
        self.months, self.layers = months, layers
        D = json.loads(open(os.path.join(ROOT, "site", "data.js"), encoding="utf-8").read()[len("window.DATA="):].rstrip().rstrip(";"))
        self.cell = D["cell"]
        self.mu = {}
        for mi, cells in enumerate(D["cells"]):
            for j in range(0, len(cells), 4):
                self.mu[(cells[j], cells[j + 1], D["months"][mi])] = cells[j + 3]
        self.det = defaultdict(lambda: defaultdict(int))  # jour → maille → détections (mailles en excès)
        p = D["pts"]
        for j in range(0, len(p), 5):
            m = D["months"][p[j + 2]]
            day = f"{m}-{p[j + 3]:02d}"
            c = (math.floor(p[j] / 100 / self.cell + 1e-9), math.floor(p[j + 1] / 100 / self.cell + 1e-9))
            self.det[day][c] += 1
        self.claims = []
        path = os.path.join(ROOT, "data", "claims_modru.csv")
        if os.path.exists(path):
            self.claims = [(dt.date.fromisoformat(r["date"]), float(r["lon"]), float(r["lat"]))
                           for r in csv.DictReader(open(path, encoding="utf-8")) if r["geonameid"].startswith("ru")]

    def fires(self, date):
        cnt, days = defaultdict(int), defaultdict(int)
        for k in range(WINDOW_DAYS):
            d = date - dt.timedelta(days=k)
            m = d.strftime("%Y-%m")
            days[m] += 1
            for c, n in self.det.get(d.isoformat(), {}).items():
                cnt[(c, m)] += n
        out = []
        for (c, m), n in cnt.items():
            y, mo = int(m[:4]), int(m[5:])
            nd = (dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.date(y, mo, 1)).days
            w = n - self.mu.get((c[0], c[1], m), 0.0) * days[m] / nd
            if w > 0:
                out.append(((c[1] + 0.5) * self.cell, (c[0] + 0.5) * self.cell, w * 30.0 / WINDOW_DAYS))  # ramené au mois
        return out

    def get(self, date):
        """(combats, présence russe, présence ukrainienne) du pas, au format de load_layers."""
        mi = self.months.index(date.strftime("%Y-%m"))
        _, pres, _, ucdp = self.layers
        pu = [(lon, lat, 3.0) for d, lon, lat in self.claims if date - dt.timedelta(days=75) < d <= date + dt.timedelta(days=45)]
        return self.fires(date) + list(ucdp.get(mi, [])), pres.get(mi, []), pu

    def indicators(self, date):
        """Indicateurs bruts du nowcast pour le pas : feux (fenêtre), UCDP (mois), pertes (mois), sur la grille."""
        import numpy as np
        mi = self.months.index(date.strftime("%Y-%m"))
        _, pres, _, ucdp = self.layers
        grids = []
        for pts in (self.fires(date), ucdp.get(mi, []), pres.get(mi, [])):
            g = np.zeros(N, np.float32)
            for lon, lat, w in pts:
                k = grid_index(lon, lat)
                if k >= 0:
                    g[k] += w
            grids.append(g)
        return grids


def indicator_grids(months):
    """Indicateurs de combat bruts par mois sur la grille (nowcast) : feux en excès, combats UCDP, pertes WarSpotting.
    Les fins de série reprennent le dernier mois disponible (voir load_layers)."""
    import numpy as np
    D = json.loads(open(os.path.join(ROOT, "site", "data.js"), encoding="utf-8").read()[len("window.DATA="):].rstrip().rstrip(";"))
    O = json.loads(open(os.path.join(ROOT, "site", "osint.js"), encoding="utf-8").read()[len("window.OSINT="):].rstrip().rstrip(";"))
    midx = {m: i for i, m in enumerate(months)}
    pts = [defaultdict(list) for _ in range(3)]
    for mi, cells in enumerate(D["cells"]):
        t = midx.get(D["months"][mi])
        if t is None:
            continue
        for j in range(0, len(cells), 4):
            pts[0][t].append(((cells[j + 1] + 0.5) * D["cell"], (cells[j] + 0.5) * D["cell"], max(0.0, cells[j + 2] - cells[j + 3])))
    for n, (key, wi) in ((1, ("ucdp", 3)), (2, ("ws", 4))):
        p = O[key]["pts"]
        for j in range(0, len(p), 5):
            pts[n][p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + wi])))
    for n in (1, 2):
        if pts[n]:
            last = max(pts[n])
            for t in range(last + 1, len(months)):
                pts[n][t] = list(pts[n][last])
    # 4e indicateur : assauts rapportés par l'état-major ukrainien (faits, par jour couvert ; build_official_signals.py)
    gs = defaultdict(dict)
    path = os.path.join(ROOT, "data", "officiels_cells_month.csv")
    if os.path.exists(path):
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r["source"] == "etatmajor_assauts" and r["mois"] in midx:
                gs[midx[r["mois"]]][int(r["maille"])] = float(r["par_jour"])
    out = {}
    for t in range(len(months)):
        grids = []
        for n in range(3):
            g = np.zeros(N, np.float32)
            for lon, lat, w in pts[n].get(t, ()):
                k = grid_index(lon, lat)
                if k >= 0:
                    g[k] += w
            grids.append(g)
        g = np.zeros(N, np.float32)
        for k, v in gs.get(t, {}).items():
            if 0 <= k < N:
                g[k] += v
        grids.append(g)
        out[t] = grids
    return out


def density_field(points, h0):
    if not points:
        return None
    raw = [0.0] * N
    for lon, lat, w in points:
        k = grid_index(lon, lat)
        if k >= 0:
            raw[k] += w
    dens = gauss_blur(raw, 3.0)
    return [1 - math.exp(-v / h0) if v > 1e-6 else 0.0 for v in dens]


def russian_band(cell):
    """Marque -2 les mailles russes à moins de RU_BAND_KM de l'Ukraine (polygone Natural Earth de site/geo.js)."""
    src = open(os.path.join(ROOT, "site", "geo.js"), encoding="utf-8").read()
    geo = json.loads(src[len("window.GEO="):].rstrip().rstrip(";"))
    polys = []
    for f in geo["countries"]:
        if f["properties"]["iso"] == "RU":
            for poly in f["geometry"]["coordinates"]:
                xs = [q[0] for q in poly[0]]
                ys = [q[1] for q in poly[0]]
                polys.append((poly, (min(xs), min(ys), max(xs), max(ys))))
    edge = [k for k in range(N) if cell[k] >= 0 and any(0 <= q < N and cell[q] < 0 for q in (k - 1, k + 1, k - NX, k + NX))]
    D = chamfer(edge, RU_BAND_KM / CELL_KM * 3 + 6)
    n = 0
    for k in range(N):
        if cell[k] != -1 or D[k] / 3 * CELL_KM > RU_BAND_KM:
            continue
        j, i = divmod(k, NX)
        lon, lat = inv_laea(X0 + (i + 0.5) * SIZE, Y1 - (j + 0.5) * SIZE)
        for poly, (x0, y0, x1, y1) in polys:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and in_ring(lon, lat, poly[0]) and not any(in_ring(lon, lat, h) for h in poly[1:]):
                cell[k] = -2
                n += 1
                break
    print(f"bande russe : {n} mailles ({n * CELL_KM ** 2:,.0f} km²)", flush=True)
    return cell


def act_since(act, k):
    v = act[k]
    return v if isinstance(v, str) else "0"


def official_half_life(official, status, places):
    """Délai médian (jours) entre une annonce du MoD RF et le premier instantané où une carte tierce (ISW, Wikipédia)
    montre la localité russe, sur les annonces confirmées dans l'année : le temps que les cartes rattrapent
    l'annonce, au-delà duquel le noyau n'apporte plus d'information propre."""
    days = sorted(status)
    lags = []
    for k, dates in official.get("modru", {}).items():
        if isinstance(k, tuple):
            continue
        g = places[k]["id"]
        for t in dates:
            t0 = dt.date(int(t[:4]), int(t[4:6]), int(t[6:]))
            for d in days:
                if d < t:
                    continue
                d0 = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
                if (d0 - t0).days > 365:
                    break
                c = status[d].get(g)
                if c and 2 in (c[1], c[2]):
                    lags.append((d0 - t0).days)
                    break
    lags.sort()
    return (lags[len(lags) // 2], len(lags)) if lags else (DEFAULT["official_half_life"], 0)


def load_official_claims(pid_index):
    """Annonces officielles géocodées (faits) : {source: {indice de localité: [dates AAAAMMJJ]}}."""
    out = {"modru": defaultdict(list), "gsua": defaultdict(list)}
    for src in out:
        path = os.path.join(ROOT, "data", f"claims_{src}.csv")
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            k = pid_index.get(r["geonameid"])
            if k is not None:
                out[src][k].append(r["date"].replace("-", ""))
            else:  # localité russe (bande frontalière) : repérée par ses coordonnées
                out[src][("xy", round(float(r["lon"]), 4), round(float(r["lat"]), 4))].append(r["date"].replace("-", ""))
    for src in out:
        for v in out[src].values():
            v.sort()
    return out


# ---------- modèle ----------
class Model:
    def __init__(self, months, snap, status, claims, official, cell, places):
        self.months, self.snap, self.status, self.claims, self.official = months, snap, status, claims, official
        self.cell, self.places = cell, places
        self.U = [k for k in range(N) if cell[k] != -1]
        self.nU = len(self.U)
        self.uidx = [-1] * N
        for u, k in enumerate(self.U):
            self.uidx[k] = u
        self.inU = [c != -1 for c in cell]
        self.isUA = [cell[k] >= 0 for k in self.U]
        self.maskUA = [1.0 if c >= 0 else 0.0 for c in cell]
        self.maskBand = [1.0 if c == -2 else 0.0 for c in cell]
        self.maskU = [1.0 if x else 0.0 for x in self.inU]
        self.pid = [p["id"] for p in places]
        self.lat = [p["lat"] for p in places]
        self.lon = [p["lon"] for p in places]
        self.ncell = defaultdict(int)
        for c in cell:
            if c >= 0:
                self.ncell[c] += 1
        self.center = [grid_index(p["lon"], p["lat"]) for p in places]
        self.radius = [max(CELL_KM, math.sqrt(self.ncell[k] * CELL_KM ** 2 / math.pi)) for k in range(len(places))]
        # voisinage 4-connexe dans U (pour la longueur du front)
        self.nb = [[self.uidx[q] for q in (k - 1, k + 1, k - NX, k + NX) if 0 <= q < N and self.inU[q]] for k in self.U]
        self.sector = [places[cell[k]]["adm1n"] if cell[k] >= 0 else "Russie" for k in self.U]

    def base_codes(self, day):
        st = self.status[day]
        return [st.get(i, (0, 0, 0, 0)) for i in self.pid]

    def poly_signs(self, base, proxies=True):
        per_place = {s: [None] * len(base) for s in POLY}
        present = {s: False for s in POLY}
        for k, b in enumerate(base):
            av = [c for c in b if c]
            for n, s in enumerate(VIINA):
                if b[n]:
                    per_place[s][k] = SIGN[b[n]]
                    present[s] = True
            if av and proxies:
                per_place["rumax"][k] = SIGN[max(av, key=lambda c: RANK[c])]
                per_place["uamin"][k] = SIGN[min(av, key=lambda c: RANK[c])]
                present["rumax"] = present["uamin"] = True
        # bande russe : faute de tracé, toutes les cartes la tiennent pour russe (a priori p = 1)
        # bande russe : les cartes VIINA ne la couvrent pas (None) ; elle a son a priori et ses preuves (voir band_evidence)
        return {s: [per_place[s][c] if c >= 0 else None for c in self.cell] for s in POLY if present[s]}

    def near_index(self, base):
        hot = defaultdict(list)
        for k, b in enumerate(base):
            if any(c > 1 for c in b):
                hot[(int(self.lat[k] * 4), int(self.lon[k] * 4))].append(k)

        def near(k, km):
            a, b = int(self.lat[k] * 4), int(self.lon[k] * 4)
            rr = int(km / 25) + 1
            for da in range(-rr, rr + 1):
                for db in range(-rr, rr + 1):
                    for q in hot.get((a + da, b + db), ()):
                        dy = (self.lat[q] - self.lat[k]) * 111.2
                        dx = (self.lon[q] - self.lon[k]) * 111.2 * math.cos(math.radians(self.lat[k]))
                        if dx * dx + dy * dy <= km * km:
                            return True
            return False
        return near

    def kernel_xy(self, lon, lat, r, logit, target):
        c0 = grid_index(lon, lat)
        if c0 < 0:
            return
        cj, ci = divmod(c0, NX)
        rc = int(math.ceil(2 * r / CELL_KM))
        for dj in range(-rc, rc + 1):
            for di in range(-rc, rc + 1):
                j2, i2 = cj + dj, ci + di
                if not (0 <= j2 < NY and 0 <= i2 < NX):
                    continue
                u = self.uidx[j2 * NX + i2]
                if u < 0:
                    continue
                d2 = (dj * dj + di * di) * CELL_KM ** 2
                if d2 > 4 * r * r:
                    continue
                v = logit * math.exp(-d2 / (2 * r * r))
                if abs(v) > abs(target.get(u, 0.0)):
                    target[u] = v

    def kernel(self, k, logit, target):
        """Ajoute à target {u: logit} le noyau gaussien de la localité k (on garde le plus fort)."""
        r = self.radius[k]
        if self.center[k] < 0:
            return
        cj, ci = divmod(self.center[k], NX)
        rc = int(math.ceil(2 * r / CELL_KM))
        for dj in range(-rc, rc + 1):
            for di in range(-rc, rc + 1):
                j2, i2 = cj + dj, ci + di
                if not (0 <= j2 < NY and 0 <= i2 < NX):
                    continue
                u = self.uidx[j2 * NX + i2]
                if u < 0:
                    continue
                d2 = (dj * dj + di * di) * CELL_KM ** 2
                if d2 > 4 * r * r:
                    continue
                v = logit * math.exp(-d2 / (2 * r * r))
                if abs(v) > abs(target.get(u, 0.0)):
                    target[u] = v

    def local_features(self, m, dist, held=None):
        """Variables du σ local au mois m sur U : (activité anormale z ≥ 0, volatilité de la ligne, désaccord, Dnipro)."""
        import numpy as np
        f = self.feat[m if m in self.feat else m[:7]]
        srcs = [s for s in ("dsm", "isw", "wiki") if s in dist and s != held]
        if len(srcs) >= 2:
            A = np.array([[np.nan if v is None else max(-30.0, min(30.0, v)) for v in dist[s]] for s in srcs], np.float32)
            D = np.nan_to_num(np.nanstd(A, axis=0), nan=0.0)
        else:
            D = np.zeros(self.nU, np.float32)
        return f["zA"], f["vol"], D, f["bar"]

    def local_mult(self, m, dist):
        import numpy as np
        zA, vol, D, bar = self.local_features(m, dist)
        c = self.local["coef"]
        fx = c[0] + c[1] * zA + c[2] * np.log1p(vol) + c[3] * np.log1p(D) + c[4] * np.exp(-bar / 5.0)
        return np.clip(np.exp(fx - self.local["fmean"]), LOCAL_MIN, 3.0)

    def stale_sigma(self, st, v_km, P):
        """Élargissement de σ (km) par maille, d'après l'âge réel (jours) de la carte à chaque localité."""
        if not st:
            return {}
        return {u: v_km * P["v_scale"] * st[self.cell[k]] / 30.0 for u, k in enumerate(self.U)
                if self.cell[k] >= 0 and self.cell[k] in st}

    def month_inputs(self, m, P, sigma0, v_km, layers, state, kstrength):
        """Tout ce qui ne dépend pas des poids : distances, σ, noyaux, indices. state : persistance entre mois."""
        d = self.snap[m]
        day = d.strftime("%Y%m%d")
        wstart = (d - dt.timedelta(days=P["claim_days"])).strftime("%Y%m%d")
        base = self.base_codes(day)
        signs = self.poly_signs(base, P["proxies"])
        ndays = (d - state["prev_day"]).days if state.get("prev_day") else 0
        for s, arr in signs.items():
            key = hash(tuple(arr[k] for k in self.U[::5]))
            state["age"][s] = state["age"].get(s, 0.0) + ndays if state["key"].get(s) == key else 0.0
            state["key"][s] = key
        state["prev_day"] = d
        dist = {}
        offs = (getattr(self, "offsets", None) or {}).get(m if len(m) == 7 else m[:7], {}) if OFFSET_ON else {}
        for s, arr in signs.items():
            dd = signed_distance(arr, self.U, self.inU, P["cap_km"])
            b = offs.get(s)
            if b is None:
                dist[s] = [dd.get(k) for k in self.U]
            else:  # décalage de la carte dans ce secteur (appris sur le passé) retiré avant la mise en commun
                dist[s] = [None if (v := dd.get(k)) is None else v if abs(v) >= 1e3 else v - float(b[u]) for u, k in enumerate(self.U)]
        sigma = {s: sigma0[s] * P["sigma_scale"] + v_km * P["v_scale"] * state["age"][s] / 30.0 for s in dist}
        # âge réel par localité : une carte que contredit un changement plus récent d'une autre carte (VIINA ou
        # journal DeepState) vieillit à cette localité depuis ce changement ; son σ s'y élargit d'autant (v · âge)
        chg, prevc = state.setdefault("chg", {}), state.setdefault("codes", {})
        stale = {s: {} for s in VIINA}
        for k, b in enumerate(base):
            old = prevc.get(k)
            for n, s in enumerate(VIINA):
                if old is None or old[n] != b[n]:
                    chg[(s, k)] = day
            prevc[k] = b
            for n, s in enumerate(VIINA):
                if not b[n]:
                    continue
                newer = [chg[(o, k)] for m, o in enumerate(VIINA) if m != n and b[m] and b[m] != b[n] and chg[(o, k)] > chg[(s, k)]]
                if newer and P["stale_local"]:
                    t = min(newer)
                    age = (d - dt.date(int(t[:4]), int(t[4:6]), int(t[6:]))).days
                    stale[s][k] = min(age, P["stale_cap_days"]) if P["stale_cap_days"] else age
        if len(m) == 7:
            mi = self.months.index(m)
            combat, pres, pres_ua = layers[:3]
            cb, pp, pq = combat.get(mi, []), pres.get(mi, []), pres_ua.get(mi, [])
        else:  # pas récent : fenêtre glissante (voir WindowLayers)
            cb, pp, pq = self.window.get(d)
        h = density_field(cb, P["h0"]) if P["alpha"] > 0 else None
        pr = density_field(pp, P["h0"]) if P["gamma"] > 0 else None
        pu = density_field(pq, P["h0"]) if P["gamma"] > 0 else None
        near = self.near_index(base)
        ker = {s: {} for s in POINT}
        flags = defaultdict(int)
        if P["proxies"]:
            # annonces des médias (VIINA) : 90 jours, près du front, levées par une annonce contraire
            for k, i in enumerate(self.pid):
                lst = self.claims.get(i)
                if not lst:
                    continue
                side = claim_state(lst, day, wstart)
                if not side or not near(k, P["near_km"]):
                    continue
                src = "ruclaims" if side == "ru" else "uaclaims"
                self.kernel(k, (1 if side == "ru" else -1) * kstrength[src], ker[src])
                flags[k] |= 512 if side == "ru" else 256
        # annonces officielles : persistantes jusqu'à démenti prolongé des autres sources du même camp
        for src, sgn, bit in (("modru", 1, 1024), ("gsua", -1, 2048)):
            act = state["official"].setdefault(src, {})
            for k, dates in self.official.get(src, {}).items():
                if any(x <= day for x in dates) and k not in act:
                    act[k] = None if not isinstance(k, tuple) else min(x for x in dates if x <= day)
            # localités russes : une annonce de prise ukrainienne tient jusqu'à une annonce russe contraire à moins de 5 km
            for k in [x for x in act if isinstance(x, tuple)]:
                _, lon, lat = k
                if src == "gsua":
                    later = [x for (kk, dates) in self.official.get("modru", {}).items() if isinstance(kk, tuple)
                             and abs(kk[1] - lon) < 0.07 and abs(kk[2] - lat) < 0.045 for x in dates if act_since(act, k) < x <= day]
                    if later:
                        del act[k]
                        continue
                self.kernel_xy(lon, lat, RU_CLAIM_RADIUS, sgn * kstrength[src], ker[src])
            for k in [x for x in act if not isinstance(x, tuple)]:
                # démenti : une carte d'un autre camp ou tierce dit l'inverse (le MoD RF n'annonce jamais ses reculs :
                # attendre un démenti de toutes les cartes verrouillait en rouge des localités reprises)
                b = base[k]
                contrary = any(c == (1 if src == "modru" else 2) for c in b if c)
                # démenti continu depuis act[k] (date) ; tombée au bout de (official_stale − 1) × 30 jours, soit
                # trois instantanés mensuels de suite, quel que soit le pas de temps
                act[k] = (act[k] or day) if contrary else None
                if act[k] and (dt.date(int(day[:4]), int(day[4:6]), int(day[6:])) - dt.date(int(act[k][:4]), int(act[k][4:6]), int(act[k][6:]))).days >= (P["official_stale"] - 1) * 30:
                    del act[k]
                    continue
                # décroissance : demi-vie = délai médian de confirmation par les cartes ; une nouvelle annonce rafraîchit
                last = max(x for x in self.official[src][k] if x <= day)
                age = (d - dt.date(int(last[:4]), int(last[4:6]), int(last[6:]))).days
                self.kernel(k, sgn * kstrength[src] * (0.5 ** (age / P["official_half_life"]) if P["official_decay"] else 1.0), ker[src])
                flags[k] |= bit
        xy_active = {src: [k for k in state["official"].get(src, {}) if isinstance(k, tuple)] for src in ("modru", "gsua")}
        band_ev = self.band_evidence(d, P, kstrength)
        # élargissement de σ : indices de combats partout, présence russe côté ukrainien
        widen = [1.0] * self.nU
        if h or pr or pu:
            for u, k in enumerate(self.U):
                w = 1.0
                if h and P["cues"]:
                    w += P["alpha"] * h[k]
                if P["presence"] and ((pr and pr[k] > 0) or (pu and pu[k] > 0)):
                    ds = [dist[s][u] for s in dist if dist[s][u] is not None]
                    mean = sum(ds) / len(ds) if ds else 0
                    if pr and mean < 0:
                        w += P["gamma"] * pr[k]
                    if pu and mean > 0:  # présence ukrainienne documentée côté russe
                        w += 4 * P["gamma"] * pu[k]
                widen[u] = w
        lv = {}
        lmax = P["lmax"]
        extra = {s: self.stale_sigma(stale.get(s), v_km, P) for s in dist}
        # σ local (calibré, voir calibrate) : σ0 × m(x) d'après la volatilité de la ligne, l'activité anormale (z), le
        # désaccord local entre sources et le Dnipro ; plancher 1 km ; le terme d'âge reste additif
        loc = self.local_mult(m, dist) if getattr(self, "local", None) else None
        for s in dist:
            sg = sigma[s]
            col, ex = dist[s], extra[s]
            if loc is None:
                lv[s] = [None if col[u] is None else max(-lmax, min(lmax, col[u] / (sg * widen[u] + ex.get(u, 0.0)))) for u in range(self.nU)]
            else:
                b0 = sigma0[s] * P["sigma_scale"]
                agep = sg - b0
                lv[s] = [None if col[u] is None else max(-lmax, min(lmax, col[u] / ((max(1.0, b0 * loc[u]) + agep) * widen[u] + ex.get(u, 0.0))))
                         for u in range(self.nU)]
        return {"day": day, "ndays": ndays or 30, "base": base, "dist": dist, "sigma": sigma, "extra": extra, "widen": widen, "lv": lv, "ker": ker, "h": h, "pr": pr, "pu": pu,
                "flags": dict(flags), "age": {s: state["age"][s] for s in dist}, "xy_active": xy_active, "band_ev": band_ev}

    def band_evidence(self, d, P, kstrength):
        """Bande russe : somme des preuves (logits) par maille.
        - « libérée » selon le MoD RF au jour t ⇒ contrôle ukrainien de la localité avant t : depuis le début de
          l'incursion de Koursk (6 août 2024) pour la zone de Koursk, sinon pendant les 45 jours précédents ;
        - annonces de prise de l'état-major ukrainien (noyau ukrainien, force calibrée)."""
        ev = {}
        day = d.strftime("%Y%m%d")
        for (kind, lon, lat), dates in [(k, v) for k, v in self.official.get("modru", {}).items() if isinstance(k, tuple)]:
            kursk = lat > 50.85 and lon < 36.6 and any("20240806" <= x <= "20250531" for x in dates)
            for t in dates:
                start = "20240806" if kursk and t >= "20240806" else (dt.date(int(t[:4]), int(t[4:6]), int(t[6:])) - dt.timedelta(days=45)).strftime("%Y%m%d")
                if start <= day < t:
                    tmp = {}
                    self.kernel_xy(lon, lat, RU_CLAIM_RADIUS + 1.0, -P["band_ua_ctrl"], tmp)
                    for u, v in tmp.items():
                        if not self.isUA[u] and v < ev.get(u, 0.0):
                            ev[u] = v
                    break
        for (kind, lon, lat), dates in [(k, v) for k, v in self.official.get("gsua", {}).items() if isinstance(k, tuple)]:
            if any(x <= day for x in dates):
                tmp = {}
                self.kernel_xy(lon, lat, RU_CLAIM_RADIUS, -kstrength["gsua"], tmp)
                for u, v in tmp.items():
                    if not self.isUA[u] and v < ev.get(u, 0.0):
                        ev[u] = v
        return ev

    def pool(self, inp, camps, P, drop=(), cells=None, shift=None, pick=None, block=None):
        """Pool logarithmique normalisé par camp → liste de logits sur U (0 si personne ne vote)."""
        lmax = P["lmax"]
        srcs = []
        for s in list(inp["lv"]) + [s for s in POINT if inp["ker"].get(s)]:
            if s in drop or (pick is not None and not pick.get(s)):
                continue
            c = CAMP[s] if P["camp_balance"] else "tous"
            if c != "tous" and c not in camps:
                continue
            srcs.append((s, c, W[s] * (pick[s] if pick else 1)))
        rng = cells if cells is not None else range(self.nU)
        out = [0.0] * self.nU if cells is None else {}
        polys = [(inp["lv"][s], c, w, s) for s, c, w in srcs if s in inp["lv"]]
        pts = [(inp["ker"][s], c, w) for s, c, w in srcs if s not in inp["lv"]]
        for u in rng:
            num = {}
            den = {}
            for col, c, w, s in polys:
                v = col[u]
                if v is None:
                    continue
                if shift is not None:
                    d = inp["dist"][s][u] + shift[s].get(block[u], 0.0)
                    v = max(-lmax, min(lmax, d / (inp["sigma"][s] * inp["widen"][u] + inp.get("extra", {}).get(s, {}).get(u, 0.0))))
                num[c] = num.get(c, 0.0) + w * v
                den[c] = den.get(c, 0.0) + w
            for ker, c, w in pts:
                v = ker.get(u)
                if v is None:
                    continue
                num[c] = num.get(c, 0.0) + w * v
                den[c] = den.get(c, 0.0) + w
            if not self.isUA[u]:  # bande russe : a priori + preuves, identique pour toutes les vues
                out[u] = P["band_prior"] + inp["band_ev"].get(u, 0.0)
            elif den:
                out[u] = sum(num[c] / den[c] for c in den) / len(den)
        return out

    def filter(self, obs, prev, P, v_km, ndays=30):
        """logit(t) = (1-β)·obs + β·[logit(t-1) diffusé de v·Δt] (convolution normalisée, fenêtre du front).
        Pas de temps quelconque (mensuel, ou tous les 3 jours sur la période récente) : β^(Δt/30) et diffusion
        v·√(Δt/30), si bien que dix pas de 3 jours valent un pas mensuel."""
        if prev is None or P["beta"] <= 0:
            return obs
        lmax = P["lmax"]
        f = max(1, ndays) / 30.0
        if f < 0.9:
            P = dict(P, beta=P["beta"] ** f)
        r = max(1.0, v_km * P["v_scale"] / CELL_KM) if f >= 0.9 else v_km * P["v_scale"] * math.sqrt(f) / CELL_KM
        act = [k for u, k in enumerate(self.U) if abs(prev[k]) < lmax - 0.5 or abs(obs[u]) < lmax - 0.5]
        if not act:
            return [obs[u] if self.maskBand[k] else (1 - P["beta"]) * obs[u] + P["beta"] * prev[k] for u, k in enumerate(self.U)]
        m = int(3 * r) + 3
        js = [k // NX for k in act]
        is_ = [k % NX for k in act]
        win = (max(0, min(js) - m), min(NY, max(js) + m + 1), max(0, min(is_) - m), min(NX, max(is_) + m + 1))
        # diffusion séparée de part et d'autre de la frontière : l'a priori ukrainien ne déborde pas en Russie
        out = [0.0] * self.nU
        # bande russe : pas de filtre. Ses preuves sont déjà rétrospectives (une localité « libérée » à t était
        # ukrainienne depuis le 6 août 2024 jusqu'à t) ; le filtre n'y ajoutait qu'un retard : poche de Koursk qui
        # « grossissait » jusqu'en novembre 2024, reflux amorti
        for u, k in enumerate(self.U):
            if self.maskBand[k]:
                out[u] = obs[u]
        num = gauss_blur_win([prev[k] * self.maskUA[k] for k in range(N)], r, win)
        den = gauss_blur_win(self.maskUA, r, win)
        for u, k in enumerate(self.U):
            if not self.maskUA[k]:
                continue
            pr = num[k] / den[k] if den[k] > 1e-9 else prev[k]
            out[u] = (1 - P["beta"]) * obs[u] + P["beta"] * pr
        return out

    def run(self, P, sigma0, v_km, layers, kstrength, month_list, fields, on_month=None, mc=False):
        """fields : {nom: (camps, drop, changements de P)}. on_month(m, inp, {nom: p sur U}, mc)."""
        state = {"age": {}, "key": {}, "official": {}}
        prev = {f: None for f in fields}
        base_inp = None
        for m in month_list:
            inp = self.month_inputs(m, P, sigma0, v_km, layers, state, kstrength)
            ps = {}
            mcres = None
            for name, (camps, drop, ch) in fields.items():
                Pf = P
                use = inp
                if ch:  # variantes sans présence / sans indices : σ recalculé sans l'élargissement correspondant
                    Pf = dict(P)
                    Pf.update(ch)
                    use = self.rewiden(inp, Pf)
                obs = self.pool(use, camps, Pf, drop=drop)
                L = self.filter(obs, prev[name], Pf, v_km, inp.get("ndays", 30))
                full = [0.0] * N
                for u, k in enumerate(self.U):
                    full[k] = L[u]
                prev[name] = full
                ps[name] = [sig(x) for x in L]
                if mc and name == "equilibre" and (len(m) == 7 or m == getattr(self, "last_key", None)):
                    mcres = self.monte_carlo(inp, P, obs, L)
            if on_month:
                on_month(m, inp, ps, mcres)
        return prev

    def rewiden(self, inp, P):
        """Logits des tracés recalculés avec un autre élargissement (variantes sans présence / sans indices)."""
        h, pr, pu, dist, lmax = inp["h"], inp["pr"], inp.get("pu"), inp["dist"], P["lmax"]
        widen = [1.0] * self.nU
        for u, k in enumerate(self.U):
            w = 1.0
            if h and P["cues"]:
                w += P["alpha"] * h[k]
            if P["presence"] and ((pr and pr[k] > 0) or (pu and pu[k] > 0)):
                ds = [dist[s][u] for s in dist if dist[s][u] is not None]
                mean = sum(ds) / len(ds) if ds else 0
                if pr and mean < 0:
                    w += P["gamma"] * pr[k]
                if pu and mean > 0:
                    w += 4 * P["gamma"] * pu[k]
            widen[u] = w
        ex = inp.get("extra", {})
        lv = {s: [None if dist[s][u] is None else max(-lmax, min(lmax, dist[s][u] / (inp["sigma"][s] * widen[u] + ex.get(s, {}).get(u, 0.0))))
                  for u in range(self.nU)] for s in dist}
        out = dict(inp)
        out["widen"], out["lv"] = widen, lv
        return out

    def monte_carlo(self, inp, P, obs, L):
        band = [u for u in range(self.nU) if self.isUA[u] and -6 < obs[u] < 6]
        base_area = sum(sig(x) for u, x in enumerate(L) if self.isUA[u])
        band_area = sum(sig(L[u]) for u in band)
        rng = random.Random(42)
        by_camp = defaultdict(list)
        for s in list(inp["lv"]) + [s for s in POINT if inp["ker"].get(s)]:
            by_camp[CAMP[s]].append(s)
        bs = max(1, int(P["mc_block_km"] / CELL_KM))
        block = {u: (self.U[u] // NX // bs) * 1000 + (self.U[u] % NX) // bs for u in band}
        blocks = set(block.values())
        b1 = 1 - P["beta"]
        areas = []
        s1 = defaultdict(float)
        s2 = defaultdict(float)
        for _ in range(MC_DRAWS):
            shift = {s: {b: rng.gauss(0, inp["sigma"][s]) for b in blocks} for s in inp["lv"]}
            pick = defaultdict(int)
            for lst in by_camp.values():
                for _ in lst:
                    pick[rng.choice(lst)] += 1
            o = self.pool(inp, CAMPS, P, cells=band, shift=shift, pick=dict(pick), block=block)
            tot = 0.0
            for u in band:
                pv = sig(L[u] + b1 * (o.get(u, 0.0) - obs[u]))
                tot += pv
                s1[u] += pv
                s2[u] += pv * pv
            areas.append((base_area - band_area + tot) * CELL_KM ** 2)
        areas.sort()
        sd = {u: math.sqrt(max(0.0, s2[u] / MC_DRAWS - (s1[u] / MC_DRAWS) ** 2)) for u in band}
        # intervalle à 95 % : centiles 2,5 et 97,5 (100 tirages : ~2-3 tirages dans chaque queue)
        return [areas[int(0.025 * (len(areas) - 1))], areas[int(round(0.975 * (len(areas) - 1)))], sd]

    def grey_strata(self, p, f):
        """Largeur de la zone grise 0,05-0,95 du champ fusionné, par strate (même définition que validate_local) :
        mailles grises / mailles de bord de la ligne 50 % de la strate × 2 km."""
        g, e = defaultdict(int), defaultdict(int)
        for u, k in enumerate(self.U):
            if self.cell[k] < 0:
                continue
            st = "dnipro" if f["bar"][u] < 10 else "actif" if f["vol"][u] >= 0.5 or f["zA"][u] >= 1.0 else "stable"
            v = p[u]
            if 0.05 <= v <= 0.95:
                g[st] += 1
            if v >= 0.5 and any(p[q] < 0.5 for q in self.nb[u]):
                e[st] += 1
        return {st: round(g[st] * CELL_KM / e[st], 1) for st in e if e[st] >= 20}

    def metrics(self, p, crimea, pre, sectors=False):
        """Surfaces (espérance, médiane, zone grise 0,05-0,95, contrôle russe > 0,95) par périmètre ;
        largeur moyenne de la zone grise = surface grise / longueur de la ligne médiane."""
        a2 = CELL_KM ** 2
        out = {k: [0.0, 0.0, 0.0, 0.0] for k in ("tout", "horsCrimee", "depuis2022")}
        length = 0
        sec = defaultdict(lambda: [0.0, 0])
        ru_exp = ru_med = 0.0
        for u, k in enumerate(self.U):
            v = p[u]
            pl = self.cell[k]
            if pl == -2:  # bande russe : surface sous contrôle UKRAINIEN (1 - p), indicateur séparé
                ru_exp += (1 - v) if v < 0.95 else 0.0
                ru_med += 1.0 if v < 0.5 else 0.0
                if any(p[q] < 0.5 for q in self.nb[u]) and v >= 0.5:
                    length += 1
                continue
            row = (v, 1.0 if v >= 0.5 else 0.0, 1.0 if 0.05 <= v <= 0.95 else 0.0, 1.0 if v > 0.95 else 0.0)
            pers = ["tout"]
            if not crimea[pl]:
                pers.append("horsCrimee")
                if not pre[pl]:
                    pers.append("depuis2022")
            for per in pers:
                o = out[per]
                for i in range(4):
                    o[i] += row[i]
            edge = v >= 0.5 and any(p[q] < 0.5 for q in self.nb[u])
            if edge:
                length += 1
            if sectors:
                s = sec[self.sector[u]]
                s[0] += row[2]
                s[1] += 1 if edge else 0
        res = {k: [round(x * a2) for x in v] for k, v in out.items()}
        res["russie_ua"] = [round(ru_exp * a2), round(ru_med * a2)]
        L_km = length * CELL_KM
        grey = res["tout"][2]
        res["front_km"] = round(L_km)
        res["grey_width_km"] = round(grey / L_km, 2) if L_km else None
        if sectors:
            res["sectors"] = {n: [round(a * a2), round(a * a2 / (e * CELL_KM), 2) if e >= 12 else None]  # largeur seulement sur ≥ 24 km de ligne
                              for n, (a, e) in sec.items() if n in FRONT_ADM1 and (a or e)}
        return res


# ---------- calibration ----------
OFFSET_ON = True    # décalage signé par source et par secteur, retiré avant la mise en commun (voir compute_offsets)


def compute_offsets(model, fm, P, H=2, HL=12.0, TS=12, shrink=60.0, cap=6.0, bar=None):
    """Décalage signé de chaque carte (DeepState, ISW, Wikipédia), secteur par secteur (tuiles de 24 km), par rapport à la
    ligne de vérité retardée (consensus des cartes à t + H). Au mois t, médiane pondérée des écarts PASSÉS seulement
    (mois q ≤ t − H), oubli de demi-vie HL mois (12 : un oubli de 6 mois faisait trembler la ligne de mois en mois), emprunt aux 8 tuiles voisines (poids 0,3), rétréci vers 0 quand
    l'échantillon est petit (n / (n + shrink)), borné à ±cap km. Renvoie {mois: {source: décalage (km) par maille}} :
    la distance signée de la source est corrigée de d − b avant la mise en commun."""
    import numpy as np
    from scipy import ndimage as ndi
    held = ("dsm", "isw", "wiki")
    U = model.U
    uj = np.array([k // NX for k in U])
    ui = np.array([k % NX for k in U])
    tile = (uj // TS) * 1000 + ui // TS
    NTy, NTx = NY // TS + 1, NX // TS + 1
    bandk = [k for k in U if model.cell[k] == -2]
    D, T = {}, {}
    for t, m in enumerate(fm):
        base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
        signs = model.poly_signs(base, proxies=False)
        for a in signs.values():
            for k in bandk:
                a[k] = None
        D[t] = {}
        for s in held:
            if s in signs:
                dd = signed_distance(signs[s], U, model.inU, P["cap_km"])
                D[t][s] = np.array([dd[k] if k in dd and abs(dd[k]) < 1e3 else np.nan for k in U], np.float32)
        cons = []
        for b in base:
            v = [x for x in b[:3] if x in (1, 2)]
            cons.append(1 if v.count(2) > v.count(1) else 0 if v.count(1) > v.count(2) else -1)
        T[t] = np.array([cons[model.cell[k]] if model.cell[k] >= 0 else -1 for k in U], np.int8)
    ERR = {}
    for t in range(len(fm) - H):
        g = np.full((NY, NX), -1, np.int8)
        g[uj, ui] = T[t + H]
        ru, ua = g == 1, g == 0
        aua, aru = np.zeros_like(ru), np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            aua |= np.roll(ua, sh, axis=(0, 1))
            aru |= np.roll(ru, sh, axis=(0, 1))
        er, eu = np.flatnonzero((ru & aua)[uj, ui]), np.flatnonzero((ua & aru)[uj, ui])
        for s, d in D[t].items():
            e = np.r_[d[er] - CELL_KM / 2, d[eu] + CELL_KM / 2]
            idx = np.r_[er, eu]
            ok = np.isfinite(e) & (np.abs(e) <= 30)
            ERR[(t, s)] = (tile[idx[ok]], e[ok])
    out, stats = {}, defaultdict(list)
    for t, m in enumerate(fm):
        out[m] = {}
        for s in held:
            past = [q for q in range(t - H + 1) if (q, s) in ERR]
            if len(past) < 6:
                continue
            pk = np.concatenate([ERR[(q, s)][0] for q in past])
            pe = np.concatenate([ERR[(q, s)][1] for q in past])
            pw = np.concatenate([np.full(len(ERR[(q, s)][1]), 0.5 ** ((t - q) / HL)) for q in past])
            B = np.zeros((NTy, NTx), np.float32)
            for k in np.unique(pk):
                ty, tx = divmod(int(k), 1000)
                nb = {kk: (1.0 if kk == k else 0.3) for kk in ((ty + dy) * 1000 + tx + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1))}
                sel = np.isin(pk, list(nb))
                w = pw[sel] * np.array([nb[q] for q in pk[sel]])
                n = float(w.sum())
                o = np.argsort(pe[sel])
                c = np.cumsum(w[o])
                med = float(pe[sel][o][np.searchsorted(c, c[-1] / 2)])
                B[ty, tx] = max(-cap, min(cap, med * n / (n + shrink)))
                if t == len(fm) - 1:
                    stats[s].append(B[ty, tx])
            b = ndi.map_coordinates(B, [uj / TS - 0.5, ui / TS - 0.5], order=1, mode="nearest").astype(np.float32)
            # Dnipro : la ligne suit la rive ; un décalage appris y fait seulement trembler la ligne d'un mois à l'autre
            # (cas calme du Dnipro : 16 → 23 flèches de gradient parasites sans cette exclusion)
            if bar is not None:
                b[bar < 10] = 0.0
            out[m][s] = b
    for s, v in stats.items():
        v = np.abs(np.array(v))
        print(f"  décalage {s} ({fm[-1]}) : |b| médian {np.median(v):.2f} km, 90e centile {np.percentile(v, 90):.2f} km, tuiles |b| > 1 km : {np.mean(v > 1):.0%}", flush=True)
    return out


def calibrate(model, months_sample, P):
    """Validation croisée des tracés (σ0) : voir la page. Renvoie (σ0, écarts moyens, diagramme, pente)."""
    held = ["dsm", "isw", "wiki"]
    sig0 = {s: 4.0 for s in POLY}
    loc_rows = []
    mae, bins, slope = {}, [], 1.0
    for it in range(3):
        bins = [[0.0, 0] for _ in range(10)]
        err = defaultdict(list)
        for m in months_sample:
            base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
            signs = model.poly_signs(base, proxies=False)
            # calibration sur les tracés ukrainiens seulement : la bande russe (a priori) n'y participe pas
            band = [k for k in model.U if model.cell[k] == -2]
            for a in signs.values():
                for k in band:
                    a[k] = None
            dist = {s: signed_distance(a, model.U, model.inU, P["cap_km"]) for s, a in signs.items()}
            for s in held:
                if s not in dist:
                    continue
                others = [o for o in ("dsm", "isw", "wiki", "boost") if o in dist and o != s and not (s == "wiki" and o == "boost")]
                if not others:
                    continue
                L = {}
                for k in model.U:
                    num = den = 0.0
                    for o in others:
                        dd = dist[o].get(k)
                        if dd is None:
                            continue
                        num += W[o] * max(-P["lmax"], min(P["lmax"], dd / sig0[o]))
                        den += W[o]
                    if den:
                        L[k] = num / den
                med_sign = [None] * N
                for k, v in L.items():
                    med_sign[k] = 1 if v > 0 else -1
                dmed = signed_distance(med_sign, model.U, model.inU, P["cap_km"])
                arr = signs[s]
                for k in model.U:
                    sk = arr[k]
                    if sk is None or model.cell[k] < 0:  # calibration sur l'Ukraine seulement (la bande russe est un a priori)
                        continue
                    edge = sk == 0 or any(0 <= q < N and model.cell[q] >= 0 and arr[q] is not None and arr[q] != sk
                                          for q in (k - 1, k + 1, k - NX, k + NX))
                    # la frontière avec la bande russe est une limite commune à toutes les sources : exclue de la mesure
                    if edge and any(0 <= q < N and model.cell[q] == -2 for q in (k - 1, k + 1, k - NX, k + NX)):
                        edge = False
                    if edge and k in dmed:
                        err[s].append(min(abs(dmed[k]), P["cap_km"]))
                        # σ local : erreur de la source retirée à cette maille et variables locales (dernière passe)
                        if it == 2 and getattr(model, "feat", None):
                            u = model.uidx[k]
                            f = model.feat[m]
                            ds = [max(-30.0, min(30.0, dist[o][k])) for o in others if o in ("dsm", "isw", "wiki") and dist[o].get(k) is not None]
                            dsd = (sum((x - sum(ds) / len(ds)) ** 2 for x in ds) / len(ds)) ** 0.5 if len(ds) >= 2 else 0.0
                            loc_rows.append((min(abs(dmed[k]), P["cap_km"]), float(f["zA"][u]), float(f["vol"][u]), dsd, float(f["bar"][u])))
                    if it >= 1 and k in L:
                        pv = sig(L[k])
                        if 0.02 < pv < 0.98:
                            b = min(9, int(pv * 10))
                            bins[b][0] += 1.0 if sk == 1 else 0.5 if sk == 0 else 0.0
                            bins[b][1] += 1
        if it == 2:
            break
        mae = {s: sum(v) / len(v) for s, v in err.items() if v}
        for s in held:
            if s in mae:
                sig0[s] = max(CELL_KM / 2, mae[s] / (2 * math.log(2)))
        if it == 1:
            num = den = 0.0
            for i, (o, n) in enumerate(bins):
                if not n or i in (0, 9):
                    continue
                f = min(0.99, max(0.01, o / n))
                x = math.log(((i + 0.5) / 10) / (1 - (i + 0.5) / 10))
                num += n * x * math.log(f / (1 - f))
                den += n * x * x
            slope = num / den if den else 1.0
            for s in held:
                sig0[s] = max(CELL_KM / 2, sig0[s] / slope)
        sig0["boost"] = sig0["wiki"]
        sig0["rumax"] = sig0["uamin"] = 1.5 * max(sig0[s] for s in held)
        print("  calibration passe %d : %s" % (it + 1, ", ".join(f"{s} σ0 = {sig0[s]:.1f} km" for s in held)), flush=True)
    print(f"  pente de calibration avant recalage : {slope:.2f}", flush=True)
    if loc_rows:
        model.local = fit_local_sigma(loc_rows)
        model.local["validation"] = validate_local(model, months_sample, P, sig0)
        model.local["applied"] = LOCAL_SIGMA_ON
        if not LOCAL_SIGMA_ON:  # mesuré puis écarté : la calibration par strate se dégrade (voir la page méthode)
            model.local_report, model.local = model.local, None
    return sig0, {s: round(v, 2) for s, v in mae.items()}, [[round((i + 0.5) / 10, 2), round(o / n, 3) if n else None, n] for i, (o, n) in enumerate(bins)], slope


def fit_local_sigma(rows):
    """Moindres carrés : log(1 + erreur locale) ~ 1 + z activité + log(1 + volatilité) + log(1 + désaccord) + Dnipro.
    Renvoie les coefficients et la moyenne de f sur les mailles de calibration (multiplicateur moyen 1)."""
    import numpy as np
    R = np.array(rows, np.float64)
    y = np.log1p(R[:, 0])
    X = np.c_[np.ones(len(R)), R[:, 1], np.log1p(R[:, 2]), np.log1p(R[:, 3]), np.exp(-R[:, 4] / 5.0)]
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    fmean = float((X @ c).mean())
    pred = np.exp(X @ c - fmean)
    print("σ local : coefficients " + ", ".join(f"{n} {v:+.3f}" for n, v in zip(["const", "z activité", "volatilité", "désaccord", "Dnipro"], c))
          + f" ; multiplicateur médian {np.median(pred):.2f} (1er décile {np.percentile(pred, 10):.2f}, 9e {np.percentile(pred, 90):.2f}) sur {len(R)} mailles", flush=True)
    return {"coef": [float(x) for x in c], "fmean": fmean, "n": len(R)}


def validate_local(model, months_sample, P, sig0):
    """Calibration par strate (stable / active) : chaque carte prédite par les autres avec σ global puis σ local ; part
    observée russe par tranche de p prédit, et largeur médiane de la bande 5-95 % (mailles 0,05 < p < 0,95 par km de
    ligne). Strate active : volatilité ≥ 0,5 bascule sur 6 mois ou activité anormale z ≥ 1."""
    import numpy as np
    out = {}
    held = ["dsm", "isw", "wiki"]
    acc = {(mode, st): [[0.0, 0] for _ in range(10)] for mode in ("global", "local") for st in ("stable", "actif", "dnipro")}
    band = {(mode, st): [0, 0] for mode in ("global", "local") for st in ("stable", "actif", "dnipro")}
    for m in months_sample:
        base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
        signs = model.poly_signs(base, proxies=False)
        bandc = [k for k in model.U if model.cell[k] == -2]
        for a in signs.values():
            for k in bandc:
                a[k] = None
        dist = {s: signed_distance(a, model.U, model.inU, P["cap_km"]) for s, a in signs.items()}
        f = model.feat[m]
        for s in held:
            if s not in dist:
                continue
            others = [o for o in ("dsm", "isw", "wiki", "boost") if o in dist and o != s and not (s == "wiki" and o == "boost")]
            if not others:
                continue
            arr = signs[s]
            for k in model.U:
                sk = arr[k]
                if sk is None or model.cell[k] < 0:
                    continue
                u = model.uidx[k]
                ds = [max(-30.0, min(30.0, dist[o][k])) for o in others if o in held and dist[o].get(k) is not None]
                dsd = float(np.std(ds)) if len(ds) >= 2 else 0.0
                c = model.local["coef"]
                mloc = min(3.0, max(LOCAL_MIN, math.exp(c[0] + c[1] * f["zA"][u] + c[2] * math.log1p(f["vol"][u]) + c[3] * math.log1p(dsd)
                                                  + c[4] * math.exp(-f["bar"][u] / 5.0) - model.local["fmean"])))
                st = "dnipro" if f["bar"][u] < 10 else "actif" if f["vol"][u] >= 0.5 or f["zA"][u] >= 1.0 else "stable"
                for mode in ("global", "local"):
                    num = den = 0.0
                    for o in others:
                        dd = dist[o].get(k)
                        if dd is None:
                            continue
                        so = sig0[o] if mode == "global" else max(1.0, sig0[o] * mloc)
                        num += W[o] * max(-P["lmax"], min(P["lmax"], dd / so))
                        den += W[o]
                    if not den:
                        continue
                    pv = sig(num / den)
                    if 0.05 < pv < 0.95:
                        band[(mode, st)][0] += 1
                    if sk == 1 and any(arr[model.U[q]] == -1 for q in model.nb[u]):  # bord de la ligne de la carte retenue
                        band[(mode, st)][1] += 1
                    if 0.02 < pv < 0.98:
                        b = min(9, int(pv * 10))
                        acc[(mode, st)][b][0] += 1.0 if sk == 1 else 0.5 if sk == 0 else 0.0
                        acc[(mode, st)][b][1] += 1
    for (mode, st), bins in acc.items():
        out.setdefault(mode, {})[st] = {
            "bins": [[round((i + 0.5) / 10, 2), round(o / n, 3) if n else None, n] for i, (o, n) in enumerate(bins)],
            # largeur moyenne de la bande 5-95 % : mailles dans la bande / mailles de bord de la ligne × 2 km (avant correctif
            # d'octobre 2026, le dénominateur comptait toutes les mailles à ±2 km de chaque autre ligne : largeur sous-estimée ~×5)
            "band_km": round(band[(mode, st)][0] / max(band[(mode, st)][1], 1) * CELL_KM, 1)}
    for st in ("stable", "actif", "dnipro"):
        ece = {}
        for mode in ("global", "local"):
            b = [x for x in out[mode][st]["bins"] if x[1] is not None]
            n = sum(x[2] for x in b)
            ece[mode] = round(sum(abs(x[0] - x[1]) * x[2] for x in b) / n, 3) if n else None
            out[mode][st]["ece"] = ece[mode]
        print(f"  strate {st} : bande 5-95 % ≈ {out['global'][st]['band_km']} km (σ global) → {out['local'][st]['band_km']} km (σ local) ; "
              f"erreur de calibration {ece['global']} → {ece['local']}", flush=True)
    return out


def calibrate_claims(model, fm, P):
    """Fiabilité de chaque type d'annonce : part des localités annoncées que les sources TIERCES (ISW,
    Wikipédia) classent dans le camp annoncé à la fin du mois suivant. Force du noyau = logit(fiabilité)."""
    snaps = {m: model.snap[m].strftime("%Y%m%d") for m in fm}
    ends = sorted(snaps.values())

    def tiers_side(k, after_day):
        nxt = [e for e in ends if e > after_day]
        if len(nxt) < 2:
            return None
        b = model.status[nxt[1] if len(nxt) > 1 else nxt[0]].get(model.pid[k], (0, 0, 0, 0))
        av = [SIGN[c] for c in (b[1], b[2]) if c]
        return None if not av else sum(av) / len(av)
    rel = {}
    detail = {}
    tests = {"modru": (model.official.get("modru", {}), 1), "gsua": (model.official.get("gsua", {}), -1)}
    media = {"ruclaims": defaultdict(list), "uaclaims": defaultdict(list)}
    pidx = {i: k for k, i in enumerate(model.pid)}
    for gid, lst in model.claims.items():
        k = pidx.get(gid)
        if k is None:
            continue
        for d, side in lst:
            media["ruclaims" if side == "ru" else "uaclaims"][k].append(d)
    tests["ruclaims"] = (media["ruclaims"], 1)
    tests["uaclaims"] = (media["uaclaims"], -1)
    for src, (claims, sgn) in tests.items():
        ok = n = 0
        for k, dates in claims.items():
            if isinstance(k, tuple):  # localités russes : pas de source tierce pour vérifier
                continue
            for d in dates[:1]:  # première annonce
                t = tiers_side(k, d)
                if t is None:
                    continue
                n += 1
                ok += 1 if (t > 0 if sgn > 0 else t < 0) else 0
        r = ok / n if n else None
        detail[src] = [ok, n]
        rel[src] = P["claim_logit"] if r is None else math.log(min(0.95, max(0.55, r)) / (1 - min(0.95, max(0.55, r))))
        print(f"  fiabilité {src} : {ok}/{n} confirmées par les sources tierces → noyau logit {rel[src]:.2f}", flush=True)
    return rel, detail


def estimate_speed(model, months):
    prev, speeds = None, []
    for m in months:
        base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
        occ = []
        for b in base:
            av = [SIGN[c] for c in b if c]
            occ.append(bool(av) and sum(av) / len(av) > 0)
        mask = [occ[c] if c >= 0 else None for c in model.cell]
        edge = sum(1 for k in model.U if mask[k] is not None and any(0 <= q < N and mask[q] is not None and mask[q] != mask[k]
                                             for q in (k - 1, k + 1, k - NX, k + NX)))
        if prev is not None and m >= "2022-06" and edge:
            flipped = sum(1 for k in model.U if mask[k] is not None and mask[k] != prev[k])
            speeds.append(flipped * CELL_KM ** 2 / (edge / 2 * CELL_KM))
        prev = mask
    s = sorted(speeds)
    return (s[len(s) // 2] if s else 1.0), speeds


# ---------- sorties ----------
def encode_field(q, prevq):
    out = bytearray()

    def varint(n):
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | 0x80 if n else b)
            if not n:
                return
    run_v, run_n = None, 0
    for idx, v in enumerate(q):
        if prevq is not None and prevq[idx] == v:
            v = 255
        if v == run_v:
            run_n += 1
        else:
            if run_v is not None:
                out.append(run_v)
                varint(run_n)
            run_v, run_n = v, 1
    out.append(run_v)
    varint(run_n)
    return base64.b64encode(bytes(out)).decode()


def wanted_dates(months, cutoff_day):
    out = {}
    for m in months:
        y, mo = int(m[:4]), int(m[5:])
        end = dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)
        if end > cutoff_day:
            break
        out[m] = end
    return out


def main():
    guard.require_files("build_front.py", [os.path.join(SRC_DIR, "gn_UA_tess.geojson")]
                        + [os.path.join(SRC_DIR, f"control_{y}.zip") for y in range(2022, dt.date.today().year + 1)]
                        + [os.path.join(ROOT, "site", n) for n in ("data.js", "osint.js")])
    src = open(os.path.join(ROOT, "site", "data.js"), encoding="utf-8").read()
    months = json.loads(src[len("window.DATA="):-1])["months"]
    today = dt.date.today()
    limit = today - dt.timedelta(days=LAG_DAYS)
    snap = wanted_dates(months, limit)
    # période récente : un pas tous les STEP_DAYS jours sur DAILY_DAYS jours, le dernier pas à J-7 exactement
    recent = [limit - dt.timedelta(days=k * STEP_DAYS) for k in range(DAILY_DAYS // STEP_DAYS)][::-1]
    daily = {d.isoformat(): d for d in recent if d not in snap.values()}
    status, last_viina = read_status(list(snap.values()) + list(daily.values()))
    for m, d in list(snap.items()):
        if d.strftime("%Y%m%d") not in status:
            print(f"  {m} : pas de données VIINA au {d} — mois ignoré")
            del snap[m]
    for k, d in list(daily.items()):
        if d.strftime("%Y%m%d") not in status:
            del daily[k]
    fm = [m for m in months if m in snap]
    dk = sorted(daily)
    frames = sorted(fm + dk, key=lambda k: snap.get(k) or daily[k])
    print(f"{len(fm)} instantanés mensuels ({fm[0]} → {fm[-1]}) et {len(dk)} pas récents tous les {STEP_DAYS} jours "
          f"({dk[0] if dk else '-'} → {dk[-1] if dk else '-'}) ; VIINA jusqu'au {last_viina}", flush=True)
    old = guard.published("front.js", "FRONT")
    if old:
        olds = [m for m in old["months"][:old.get("nMonths", len(old["months"]))] if m]
        guard.check("build_front.py", [
            ("nombre d'instantanés", len(fm), len(olds), "max"),
            ("dernier instantané", snap[fm[-1]].isoformat(), olds[-1]["date"] if olds else None, "max"),
            ("localités", sum(len(v) for v in status.values()) // max(1, len(status)), old.get("nPlaces"), "count"),
        ])
    claims = read_claims()
    cell, places = rasterize(os.path.join(SRC_DIR, "gn_UA_tess.geojson"))
    cell = russian_band(cell)
    pid_index = {p["id"]: k for k, p in enumerate(places)}
    ended = carry_forward(status)
    dsm_fixed, dsm_events = apply_dsm_log(status, places)
    official = load_official_claims(pid_index)
    events, ev_rate = control_events(status, places, official, dsm_events)
    model = Model(months, dict(snap, **daily), status, claims, official, cell, places)
    model.last_key = frames[-1]
    crimea = [p["adm1"] in CRIMEA_ADM1 for p in places]
    pre = []
    for b in model.base_codes(PRE_DATE):
        av = [SIGN[c] for c in b if c]
        pre.append(bool(av) and sum(av) / len(av) > 0)
    P = dict(DEFAULT)
    hl, nhl = official_half_life(official, status, places)
    P["official_half_life"] = max(15.0, float(hl))
    print(f"annonces du MoD RF : demi-vie {P['official_half_life']:.0f} jours (délai médian de confirmation par une carte tierce, {nhl} annonces)", flush=True)
    if "--sans-indices" in sys.argv:
        P["alpha"] = 0.0
    layers = load_layers(months)
    model.window = WindowLayers(months, layers)
    # mesures sectorielles (zone de contact, z-scores) et variables du σ local, mois par mois (scripts/sectors.py)
    import numpy as np
    from sectors import Sectors
    sect = Sectors(model.U, [cell[k] for k in model.U], NX, NY, CELL_KM, months, grid_index, ROOT)
    model.feat = {}
    hist_cons = []
    ok_t = {months.index(m) for m in fm}
    for m in fm:
        b = model.base_codes(snap[m].strftime("%Y%m%d"))
        hist_cons.append([1 if [x for x in c[:3] if x in (1, 2)].count(2) > [x for x in c[:3] if x in (1, 2)].count(1)
                          else 0 if [x for x in c[:3] if x in (1, 2)].count(1) > [x for x in c[:3] if x in (1, 2)].count(2) else -1 for c in b])
        Z = sect.z(months.index(m), ok_t)
        model.feat[m] = {"zA": np.maximum(0, np.sort(Z, axis=0)[-2:].mean(0)).astype(np.float32),
                         "vol": sect.volatility(hist_cons[-7:]).astype(np.float32), "bar": sect.barrier}
    print(f"σ local : variables prêtes ({len(model.feat)} mois) ; Dnipro et réservoir de Kakhovka à moins de 5 km : "
          f"{int((sect.barrier < 5).sum())} mailles", flush=True)

    if OFFSET_ON:
        model.offsets = compute_offsets(model, fm, P, bar=np.asarray(sect.barrier))
    v_km, speeds = estimate_speed(model, fm)
    print(f"vitesse typique du front : {v_km:.2f} km/mois", flush=True)
    sample = fm[::4] if not FAST else fm[::12]
    sig0, mae, calib, slope = calibrate(model, sample, P)
    kstrength, kdetail = calibrate_claims(model, fm, P)

    fields = {v: (camps, (), None) for v, camps in VIEWS.items()}
    for name, spec in VARIANTS.items():
        ch = {}
        if spec.get("presence") is False:
            ch["presence"] = False
        if spec.get("cues") is False:
            ch["cues"] = False
        fields[name] = (CAMPS, tuple(spec.get("drop", ())), ch or None)

    U = model.U
    months_out = {m: None for m in months}
    days_out = {}
    allf = list(fields) + ["nowcast", "combat", "contact", "zf", "zu", "zw", "zm", "destr", "asym"]
    contact_prof = []
    P_now, dis_cell, Zf_store, line_km = {}, {}, {}, {}
    qstore = {f: {} for f in allf}   # champs quantifiés par instantané, encodés à la fin dans l'ordre publié
    sdstore = {}
    last_mc = {}
    day_p = {}
    # nowcast : champ de base, consensus des cartes et indicateurs de combat, mois par mois (voir scripts/nowcast.py)
    import numpy as np
    from nowcast import Nowcast
    lonlat = np.array([inv_laea(X0 + (k % NX + 0.5) * SIZE, Y1 - (k // NX + 0.5) * SIZE) for k in U], np.float32).T
    ncast = Nowcast(U, [cell[k] for k in U], NX, NY, CELL_KM, (lonlat[0], lonlat[1]), months)
    ind = indicator_grids(months)
    codes_store = {}
    claim_places = {}

    def fdate(m):
        return snap.get(m) or daily[m]

    def on_month(m, inp, ps, mcres):
        fd = fdate(m)
        mo = {"date": fd.isoformat(), "kind": "m" if len(m) == 7 else "d", "a": {}, "v": {}}
        if len(m) == 7:
            # consensus des cartes (DeepState corrigé, ISW, Wikipédia) par localité, puis par maille : vérité du nowcast
            cons = []
            for b in inp["base"]:
                v = [x for x in b[:3] if x in (1, 2)]
                cons.append(1 if v.count(2) > v.count(1) else 0 if v.count(1) > v.count(2) else -1)
            ncast.add_month(months.index(m), ps["equilibre"], [cons[cell[k]] if cell[k] >= 0 else -1 for k in U], ind[months.index(m)])
            # désaccord des cartes (une russe, une ukrainienne) : proxy de contact pour P(contact au sol)
            dis_pl = [1 if (2 in b[:3] and 1 in b[:3]) else 0 for b in inp["base"]]
            dis_cell[months.index(m)] = np.array([dis_pl[cell[k]] if cell[k] >= 0 else 0 for k in U], np.int8)
        else:
            day_p[m] = np.asarray(ps["equilibre"], np.float32)
        for f, p in ps.items():
            met = model.metrics(p, crimea, pre, sectors=(f == "equilibre"))
            if f in VIEWS:
                mo["a"][f] = {k: met[k] for k in ("tout", "horsCrimee", "depuis2022")}
                mo["a"][f]["russie"] = met["russie_ua"]
                if f == "equilibre":
                    mo["grey"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"], "front_km": met["front_km"],
                                  "sectors": met.get("sectors", {})}
                    if getattr(model, "feat", None) and m in model.feat:
                        mo["grey"]["strata"] = model.grey_strata(p, model.feat[m])
            else:
                mo["v"][f] = {k: met[k] for k in ("tout", "depuis2022")}
                mo["v"][f]["grey_width_km"] = met["grey_width_km"]
            qstore[f][m] = bytes(min(63, int(x * 63 + 0.5)) for x in p)
        tot = mo["a"]["equilibre"]["tout"][0]
        if mcres:
            lo, hi, sd = mcres
            # écart-type Monte Carlo de p par maille (pour les flèches) : 0-63 pour 0-0,5
            q = [0] * len(U)
            for u, v in sd.items():
                q[u] = min(63, int(v / 0.5 * 63 + 0.5))
            last_mc.update(lo=lo - tot, hi=hi - tot, sd=bytes(q))
        # pas récents sans tirages propres : écarts du dernier tirage reportés autour de l'espérance du pas
        sdstore[m] = last_mc["sd"]
        mo["mc"] = {per: [round(last_mc["lo"] + mo["a"]["equilibre"][per][0]), round(last_mc["hi"] + mo["a"]["equilibre"][per][0])]
                    for per in ("tout", "horsCrimee", "depuis2022")}
        pu, pr_ = ps["ua"], ps["ru"]
        dis = {"tout": [0.0, 0.0], "horsCrimee": [0.0, 0.0], "depuis2022": [0.0, 0.0]}
        for u, k in enumerate(U):
            pl = cell[k]
            if pl < 0:
                continue
            dl = abs(pr_[u] - pu[u])
            big = 1.0 if dl > 0.5 else 0.0
            for per in ("tout",) + (("horsCrimee",) if not crimea[pl] else ()) + (("depuis2022",) if not crimea[pl] and not pre[pl] else ()):
                dis[per][0] += big
                dis[per][1] += dl
        mo["dis"] = {k: [round(a * CELL_KM ** 2), round(b * CELL_KM ** 2)] for k, (a, b) in dis.items()}
        mo["sigma"] = {s: round(v, 2) for s, v in inp["sigma"].items()}
        mo["age"] = {s: round(v) for s, v in inp["age"].items()}
        cur = {}
        for k, b in enumerate(inp["base"]):
            fl = inp["flags"].get(k, 0)
            if any(c > 1 for c in b) or fl:
                cur[k] = b[0] | b[1] << 2 | b[2] << 4 | b[3] << 6 | fl
        codes_store[m] = cur  # différences calculées après coup, dans l'ordre publié (mois, puis pas récents)
        # annonces officielles actives ce mois (calque brut) : indices de localités ; nouvelles du mois à part
        day0 = (fd.replace(day=1) if len(m) == 7 else fd - dt.timedelta(days=30)).strftime("%Y%m%d")
        day1 = inp["day"]
        for src, bit in (("modru", 1024), ("gsua", 2048)):
            act = [k for k, f in inp["flags"].items() if f & bit] + inp["xy_active"][src]
            new = [k for k, dates in official.get(src, {}).items() if any(day0 <= x <= day1 for x in dates)]

            def kid(k):  # localité ukrainienne : indice ; localité russe : « x<lon>_<lat> »
                return f"x{k[1]}_{k[2]}" if isinstance(k, tuple) else str(k)
            mo[src] = {"actives": sorted(kid(k) for k in act), "nouvelles": sorted(kid(k) for k in new)}
            for k in act + new:
                if isinstance(k, tuple):
                    claim_places[kid(k)] = [k[1], k[2], "Russie"]
                else:
                    claim_places[kid(k)] = [round(places[k]["lon"], 4), round(places[k]["lat"], 4), places[k]["name"]]
        if len(m) == 7:
            months_out[m] = mo
        else:
            days_out[m] = mo
        g = mo["grey"]
        print(f"  {m} : {mo['a']['equilibre']['tout'][0]:,} km² (95 % {mo['mc']['tout'][0]:,}–{mo['mc']['tout'][1]:,}) · "
              f"RU {mo['a']['ru']['tout'][0]:,} · UA {mo['a']['ua']['tout'][0]:,} · zone grise {g['km2']:,} km², "
              f"{g['width_km']} km de large · MoD actives {len(mo['modru']['actives'])}", flush=True)

    print("modèle principal et variantes…", flush=True)
    model.run(P, sig0, v_km, layers, kstrength, frames, fields, on_month=on_month, mc=True)

    # ---------- nowcast : les combats déplacent p (β calibré mois par mois, walk-forward) ----------
    rep = ncast.calibrate()
    o, n_ = rep["overall"], rep["overall"]["nChanging"]
    print(f"nowcast : β final {rep['beta_final']} ; walk-forward {rep['period']} : Brier bande {o['band'][0]} → {o['band'][1]}, "
          f"mailles changeantes ({n_}) {o['changing'][0]} → {o['changing'][1]}", flush=True)
    beta_last = ncast.beta[months.index(fm[-1])]
    for m in frames:
        mo = months_out[m] if len(m) == 7 else days_out[m]
        if len(m) == 7:
            t = months.index(m)
            pn = ncast.apply(t, ncast.beta[t])
            sc, dl = ncast.score(t), ncast.dist_line(t)
            beta_m = ncast.beta[t]
        else:
            # pas récent : β du dernier mois calibré, indicateurs de la fenêtre glissante, tendance sur ~60 jours
            fd = fdate(m)
            back = [k for k in frames if fdate(k) <= fd - dt.timedelta(days=60)]
            kb = back[-1] if back else frames[0]
            p_back = day_p[kb] if len(kb) != 7 else ncast.P[months.index(kb)]
            beta_m = beta_last
            pn, sc, dl = ncast.apply_arrays(day_p[m], model.window.indicators(fd), p_back, beta_m)
        met = model.metrics(pn.tolist(), crimea, pre)
        mo["a"]["nowcast"] = {k: met[k] for k in ("tout", "horsCrimee", "depuis2022")}
        mo["a"]["nowcast"]["russie"] = met["russie_ua"]
        mo["greyNow"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"]}
        d = met["tout"][0] - mo["a"]["equilibre"]["tout"][0]
        mo["mcNow"] = {per: [x + d for x in mo["mc"][per]] for per in mo["mc"]}
        mo["beta"] = beta_m
        # événements datés des 30 jours précédant l'instantané : lon, lat, camp (1 russe), source (indice EV_SOURCES)
        d1 = fdate(m).strftime("%Y%m%d")
        d0 = (fdate(m) - dt.timedelta(days=30)).strftime("%Y%m%d")
        mo["ev"] = [x for e in events if d0 < e[0] <= d1 for x in (round(e[1], 3), round(e[2], 3), e[3], EV_SOURCES.index(e[4]))]
        qstore["nowcast"][m] = bytes(min(63, int(x * 63 + 0.5)) for x in pn.tolist())
        if len(m) == 7:
            t = months.index(m)
            P_now[t] = pn
            # zone grise (contact) : profil de chaque secteur (3 mois), mi-hauteur de part et d'autre de la ligne
            # profil national de l'activité selon la distance (page méthode) ; la zone elle-même est P(contact) ci-dessous
            _, _, prof, line_km_t = sect.contact(t, P_now)
            contact_prof.append(prof)
            line_km[t] = line_km_t
            # z-scores du secteur (12 mois précédents) : feux, combats, pertes, mouvement de la ligne ; combats intenses
            Z = sect.z(t, set(P_now))
            sdl = sect.signed_dist(("c", t), pn)[0]
            hot, top2 = sect.intense(Z, sdl)
            ref = [s for s in range(max(0, t - 12), t) if s in P_now and s - 1 in P_now]
            mv = sect.smooth(np.abs(pn - P_now[t - 1]), 2.5) if t - 1 in P_now else np.zeros(len(U), np.float32)
            if len(ref) >= 4:
                Rm = np.stack([sect.smooth(np.abs(P_now[s] - P_now[s - 1]), 2.5) for s in ref])  # (R est le rayon terrestre)
                zm = (mv - Rm.mean(0)) / np.maximum(Rm.std(0), 0.01)
            else:
                zm = np.zeros(len(U), np.float32)
            Zf_store[t] = Z[0]
            for name, zz in (("zf", Z[0]), ("zu", Z[1]), ("zw", Z[2]), ("zm", zm)):
                qstore[name][m] = bytes(np.clip(np.round((zz + 2) * 5), 0, 63).astype(np.uint8).tolist())  # z de −2 à +10,6
            qstore["combat"][m] = bytes(np.where(hot, np.clip(np.round(top2 * 10), 20, 63), 0).astype(np.uint8).tolist())
            mo["intense_km2"] = round(float(hot.sum()) * CELL_KM ** 2)

    # ---------- zone grise de contact : P(contact au sol) par maille, walk-forward ----------
    BM = json.loads(open(os.path.join(ROOT, "site", "basemap.js"), encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))
    urb = sect.urban(BM.get("places", []))
    tsm = sorted(P_now)
    SDm = {t: sect.signed_dist(("c", t), P_now[t])[0] for t in tsm}
    truth = {t: ncast.T[t] for t in tsm if t in ncast.T}
    ART = sect.artillery()
    sect.AG = sect.sar()
    sect.official()
    sect.official_list()
    print(f"liste officielle rejouée : {int((sect.LIST > 0).sum()):,} mailles-mois en hostilités actives (connues à la date)", flush=True)
    print("faits officiels : " + ", ".join(f"{k} {int((v > 0).sum()):,} mailles-mois" for k, v in sect.OF.items()), flush=True)
    print(f"radar : abandon agricole renseigné sur {int(np.isfinite(sect.AG).sum()):,} mailles-mois", flush=True)
    PC, PCwf = sect.pcontact(tsm, SDm, sect.G, urb, Zf_store, truth, dis_cell, ART)
    print(f"P(contact au sol) : AUC walk-forward {sect.pc_auc:.3f}, à distance égale {sect.pc_auc_eq:.3f} ; coefficients {sect.pc_coef}" if sect.pc_auc else "P(contact au sol) : pas de validation", flush=True)
    # « combats derrière la ligne » : asymétrie de l'activité de part et d'autre de la ligne 50 %, par secteur de 24 km
    # (indicateur sans direction ; garde-fou des flèches de gradient dans app.js) ; bilan : mouvement réel t → t+2
    asym_tiles, asym_rows = {}, []
    tl12 = (sect.jj // 12) * 1000 + sect.ii // 12
    for t in tsm:
        m = months[t]
        fld, tiles = sect.asym(t, SDm[t], ART)
        qstore["asym"][m] = bytes(np.clip(np.round(fld * 63), 0, 63).astype(np.uint8).tolist())
        asym_tiles[m] = tiles
        months_out[m]["asymKm2"] = round(float((fld > 0).sum()) * CELL_KM ** 2)
        if t in truth and t + 2 in truth:
            a0, a2 = truth[t], truth[t + 2]
            gain = np.where((a0 == 0) & (a2 == 1), 1, np.where((a0 == 1) & (a2 == 0), -1, 0))
            for k, a in tiles.items():
                g = int(gain[tl12 == k].sum())
                asym_rows.append((m, a, g))
    asym_bilan = {}
    if asym_rows:
        A_ = np.array([r[1] for r in asym_rows]); G_ = np.array([r[2] for r in asym_rows])
        for lab, lo, hi in (("< −0,3", -9, -0.3), ("−0,3 à −0,1", -0.3, -0.1), ("−0,1 à +0,1", -0.1, 0.1), ("+0,1 à +0,3", 0.1, 0.3), ("> +0,3", 0.3, 9)):
            sel = (A_ >= lo) & (A_ < hi)
            if sel.sum():
                asym_bilan[lab] = [int(sel.sum()), round(float(np.mean(G_[sel] > 0)), 3), round(float(np.mean(G_[sel] < 0)), 3), round(float(np.mean(G_[sel] == 0)), 3)]
        print("combats derrière la ligne : bilan (asymétrie → gain russe / reprise ukrainienne / rien, t → t+2) : " + " ; ".join(f"{k} n={v[0]} {v[1]:.0%}/{v[2]:.0%}/{v[3]:.0%}" for k, v in asym_bilan.items()), flush=True)
    # couche d'information « destruction cumulée » (radar, décochée) : part du bâti (≥ 5 % de bâti en 2021) dont la
    # rétrodiffusion s'écarte durablement de 2021, maximum courant jusqu'au mois PRÉCÉDENT (un mois de recul en plus du
    # J-7 de la donnée) ; mailles de 2 km seulement, aucun composite plus fin ; 63 = 50 % du bâti modifié ou plus
    BDc = np.zeros(len(U), np.float32)
    for t, m in enumerate(months):
        if m in qstore["equilibre"]:
            qstore["destr"][m] = bytes(np.clip(np.round(BDc / 0.5 * 63), 0, 63).astype(np.uint8).tolist())
        x = sect.BD[t]
        BDc = np.maximum(BDc, np.where(np.isfinite(x) & (sect.BS >= 0.05), np.nan_to_num(x), 0.0))
    print(f"destruction cumulée (radar) : {int((BDc >= 0.2).sum())} mailles bâties à ≥ 20 % de bâti modifié en fin de période", flush=True)
    # contribution de chaque signal : perte d'AUC (globale et à distance égale) quand on le retire du faisceau complet
    pc_contrib = []
    if sect.pc_auc and not FAST:
        for g in list(sect.PC_GROUPS) + ["menaces d'artillerie"]:
            a_, e_ = sect.pcontact(tsm, SDm, sect.G, urb, Zf_store, truth, dis_cell, ART, drop=(g,) if g in sect.PC_GROUPS else (), use_art=g in sect.PC_GROUPS)
            pc_contrib.append([g, round(sect.pc_auc - a_, 4), round(sect.pc_auc_eq - e_, 4)])
            print(f"  contribution {g} : AUC {sect.pc_auc - a_:+.4f} ; à distance égale {sect.pc_auc_eq - e_:+.4f}", flush=True)
    a2 = CELL_KM ** 2
    tile = (sect.jj // 25) * 1000 + sect.ii // 25
    for t in tsm:
        m = months[t]
        pc, sd = PC[t], SDm[t]
        qstore["contact"][m] = bytes(np.clip(np.round(pc * 63), 0, 63).astype(np.uint8).tolist())
        L = max(line_km.get(t, 1.0), 1.0)
        tiles = []
        for tk in np.unique(tile[np.abs(sd) <= 2]):
            mm = tile == tk
            Lt = max(float(((np.abs(sd) <= 1) & mm).sum()) * CELL_KM * math.pi / 4, 2.0)
            near = mm & (np.abs(sd) <= 10)
            comp = [float(np.log1p(sect.smooth(x, 1.5))[near].mean()) if near.any() else 0.0 for x in (sect.R[0, t], sect.G[t], sect.R[2, t], ART[t], sect.OF["etatmajor_assauts"][t], sect.OF["modru_localites"][t])]
            tot = sum(comp) or 1.0
            tiles.append((int((tk // 1000) * 25 + 12), int((tk % 1000) * 25 + 12),
                          round(float((pc * mm * (sd > 0)).sum() * a2 / Lt), 1), round(float((pc * mm * (sd <= 0)).sum() * a2 / Lt), 1),
                          round(float(pc[near].mean()) if near.any() else 0.0, 2), [round(c / tot, 2) for c in comp]))
        months_out[m]["contact"] = {"km2": round(float(pc.sum() * a2)), "w_ru": round(float((pc * (sd > 0)).sum() * a2 / L), 1),
                                    "w_ua": round(float((pc * (sd <= 0)).sum() * a2 / L), 1), "tiles": tiles, "wf": bool(PCwf[t])}

    # ---------- bande d'incertitude locale : intervalles conformes par secteur, walk-forward (scripts/conformal.py) ----------
    from conformal import LocalBand
    TS_LB = 12
    tl_lb = (sect.jj // TS_LB) * 1000 + sect.ii // TS_LB

    def tile_ll(k):
        return inv_laea(X0 + (k % 1000 + 0.5) * TS_LB * SIZE, Y1 - (k // 1000 + 0.5) * TS_LB * SIZE)
    # strate de chaque tuile, mois par mois (même règle que validate_local), sur les mailles à moins de 6 km de la ligne
    strata_lb = {}
    for t in tsm:
        fm_ = model.feat.get(months[t])
        if fm_ is None:
            continue
        near = np.abs(SDm[t]) <= 6
        bar, vol, zA = (np.asarray(fm_[x]) for x in ("bar", "vol", "zA"))
        st_t = {}
        for k in np.unique(tl_lb[near]):
            mm = near & (tl_lb == k)
            st_t[int(k)] = ("dnipro" if np.median(bar[mm]) < 10 else "actif" if np.median(vol[mm]) >= 0.5 or np.median(zA[mm]) >= 1.0 else "stable")
        strata_lb[t] = st_t
    truth_lb = {t: np.asarray(ncast.T[t]) for t in ncast.T}
    local_band = {}
    for f in list(VIEWS) + ["nowcast"]:
        Pf = {months.index(m): np.frombuffer(qstore[f][m], np.uint8).astype(np.float32) / 63 for m in frames if len(m) == 7 and m in qstore[f]}
        lb = LocalBand(sect, months, truth_lb, TS=TS_LB, name=f).fit(Pf, strata_lb)
        if f in ("equilibre", "nowcast"):
            local_band[f] = lb.report(tile_ll)
            local_band[f]["rawCoverage"] = lb.raw_coverage(Pf)
            # localité de repère (≥ 5 000 habitants) la plus proche du centre de chaque secteur, pour le tableau
            big_pl = [pl for pl in BM.get("places", []) if len(pl) > 3 and (pl[3] or 0) >= 5000]
            for row in local_band[f]["tiles"]:
                c = math.cos(math.radians(row[1]))
                row.append(min(big_pl, key=lambda pl: ((pl[0] - row[0]) * c) ** 2 + (pl[1] - row[1]) ** 2)[2] if big_pl else "")
            r = local_band[f]
            print(f"bande locale ({f}) : couverture par secteur médiane {r['local']['median']} (10e centile {r['local']['p10']}), secteurs dans [90 ; 99] % "
                  f"{r['local']['in90_99']} contre {r['global']['in90_99']} avec une bande unique ; bande publiée jusqu'ici : couverture {r['rawCoverage']} ; "
                  + " ; ".join(f"{st} {v['local']} (unique {v['global']}, {v['n']} points)" for st, v in r["strata"].items()), flush=True)
        for m in frames:
            if m not in qstore[f]:
                continue
            t = months.index(m if len(m) == 7 else m[:7])
            p0 = np.frombuffer(qstore[f][m], np.uint8).astype(np.float32) / 63
            p1 = lb.remap(p0, t, ("lbr", f, m))
            qstore[f][m] = bytes(np.clip(np.round(p1 * 63), 0, 63).astype(np.uint8).tolist())
            mo = months_out[m] if len(m) == 7 else days_out[m]
            met = model.metrics(p1.tolist(), crimea, pre, sectors=(f == "equilibre"))
            if f == "nowcast":
                mo["a"]["nowcast"] = {k: met[k] for k in ("tout", "horsCrimee", "depuis2022")}
                mo["a"]["nowcast"]["russie"] = met["russie_ua"]
                mo["greyNow"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"]}
                d = met["tout"][0] - mo["a"]["equilibre"]["tout"][0]
                mo["mcNow"] = {per: [x + d for x in mo["mc"][per]] for per in mo["mc"]}
                continue
            old = mo["a"][f]
            mo["a"][f] = {k: met[k] for k in ("tout", "horsCrimee", "depuis2022")}
            mo["a"][f]["russie"] = met["russie_ua"]
            if f == "equilibre":
                if mo.get("mc"):
                    mo["mc"] = {per: [x + met[per][0] - old[per][0] for x in mo["mc"][per]] if per in met else mo["mc"][per] for per in mo["mc"]}
                mo["grey"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"], "front_km": met["front_km"], "sectors": met.get("sectors", {})}
                if m in model.feat:
                    mo["grey"]["strata"] = model.grey_strata(p1, model.feat[m])
        sect._sd = {k: v for k, v in sect._sd.items() if k[0] not in ("lb", "lbr")}
    lastm_lb = months_out[fm[-1]]
    print(f"zone grise après bande locale ({fm[-1]}) : {lastm_lb['grey']['km2']:,} km², {lastm_lb['grey']['width_km']} km de large ; par strate {lastm_lb['grey'].get('strata')}", flush=True)

    variants = [
        ("défaut : camps à un tiers chacun", {}),
        ("poids par source, sans équilibre des camps", {"camp_balance": False}),
        ("sans enveloppes ni annonces des médias", {"proxies": False}),
        ("sans indices de combats (α = 0)", {"alpha": 0.0}),
        ("sans présence documentée (γ = 0)", {"gamma": 0.0}),
        ("sans filtre temporel (β = 0)", {"beta": 0.0}),
        ("σ0 doublés", {"sigma_scale": 2.0}),
        ("vitesse du front doublée", {"v_scale": 2.0}),
        ("sans vieillissement des sources (v = 0)", {"v_scale": 0.0}),
    ]
    sens = []
    tail = fm[-13:] if not FAST else fm[-3:]
    sfields = {"equilibre": (CAMPS, (), None), "ua": (("ua",), (), None), "ru": (("ru",), (), None), "tiers": (("tiers",), (), None)}
    for name, ch in variants:
        Pv = dict(P)
        Pv.update(ch)
        got = {}
        model.run(Pv, sig0, v_km, layers, kstrength, tail, sfields,
                  on_month=lambda m, inp, ps, mc: got.update(ps) if m == tail[-1] else None)
        row = [name]
        for f in ("equilibre", "ua", "ru", "tiers"):
            met = model.metrics(got[f], crimea, pre)
            row += [met["tout"][0], met["depuis2022"][0]]
        row.append(model.metrics(got["equilibre"], crimea, pre)["tout"][2])
        sens.append(row)
        print(f"  sensibilité · {name} : {row[1]:,} km²", flush=True)

    # ---------- écriture ----------
    runs, last, n = bytearray(), None, 0

    def flush(v, n):
        while n:
            k = min(n, 255)
            runs.extend((v & 0xFF, v >> 8, k))
            n -= k
    for c in cell:
        c = 65535 if c == -1 else 65534 if c == -2 else c
        if c == last:
            n += 1
        else:
            if last is not None:
                flush(last, n)
            last, n = c, 1
    flush(last, n)
    flags = bytes((1 if crimea[k] else 0) | (2 if pre[k] else 0) for k in range(len(places)))
    mlist = [months_out[m] for m in months] + [days_out[k] for k in dk]
    order = list(months) + dk
    prev_codes = {}
    for key in order:
        cur = codes_store.get(key)
        if cur is None:
            continue
        diff = [[k, c] for k, c in cur.items() if prev_codes.get(k) != c] + [[k, 0] for k in prev_codes if k not in cur]
        (months_out[key] if len(key) == 7 else days_out[key])["d"] = [x for kc in sorted(diff) for x in kc]
        prev_codes = cur

    def encode_seq(store):
        arr, prev = [], None
        for key in order:
            q = store.get(key)
            if q is None:
                arr.append(None)
                continue
            arr.append(encode_field(q, prev))
            prev = q
        return arr
    cstats = {}
    p_stats = os.path.join(ROOT, "data", "claims_stats.json")
    if os.path.exists(p_stats):
        cstats = json.load(open(p_stats))
    meta = {
        "generated": today.isoformat(), "viinaLast": last_viina, "limit": limit.isoformat(),
        "grid": {"nx": NX, "ny": NY, "size": SIZE, "x0": X0, "y1": Y1, "lon0": LON0, "lat0": LAT0, "R": R},
        "viina": VIINA, "poly": POLY, "point": POINT, "camps": CAMP, "weights": W, "views": {k: list(v) for k, v in VIEWS.items()},
        "variants": {k: {"drop": sorted(v.get("drop", ())), "presence": v.get("presence", True), "cues": v.get("cues", True)} for k, v in VARIANTS.items()},
        "params": P, "v": round(v_km, 2), "speeds": [round(x, 2) for x in speeds],
        "sigma0": {s: round(v, 2) for s, v in sig0.items()}, "mae": mae, "calibration": calib, "sensitivity": sens,
        "claimStrength": {s: round(v, 2) for s, v in kstrength.items()}, "claimReliability": kdetail, "claimStats": cstats,
        "claimPlaces": claim_places,
        "mcDraws": MC_DRAWS, "calibSlope": round(slope, 2), "calibMonths": len(sample),
        "runs": base64.b64encode(bytes(runs)).decode(), "flags": base64.b64encode(flags).decode(), "nPlaces": len(places),
        "events": {"sources": EV_SOURCES, "reliability": ev_rate, "windowDays": 30},
        "contact": {"method": "P(contact au sol), régression logistique walk-forward", "auc": sect.pc_auc, "coef": sect.pc_coef,
                    "features": sect.PC_NAMES, "aucEq": sect.pc_auc_eq, "contrib": pc_contrib, "artillery": {"source": "canal officiel des alertes « Повітряна тривога » (t.me/air_alert_ua), menaces d'artillerie agrégées par hromada (limites OCHA COD-AB, CC BY-IGO) puis par maille", "gain": "AUC à distance égale 0,681 → 0,731 en walk-forward (deux étapes : une alerte relève P, son absence ne le change pas)", "stopped": "2026-09-07"}, "bins": list(range(-58, 62, 4)),
                    "profile": [round(float(x), 4) for x in np.mean(np.array(contact_prof), axis=0)] if contact_prof else []},
        "localSigma": getattr(model, "local", None) or getattr(model, "local_report", None),
        "localBand": local_band,
        "asym": {"bilan": asym_bilan, "thr": 0.2, "tile_km": 24},
        "zscores": {"refMonths": 12, "names": ["feux", "UCDP", "pertes", "mouvement"], "encoding": "q = (z + 2) × 5"},
        "nowcast": dict(rep, names=["feux", "UCDP", "pertes"], widthKm=30, r0=0.03, grid=[0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]),
        "nMonths": len(months), "steps": dk, "stepDays": STEP_DAYS, "windowDays": WINDOW_DAYS,
        "months": mlist,
    }
    # valeurs numpy (σ local, zone de contact, z) converties en types natifs
    js = "window.FRONT=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":"),
                                      default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + ";"
    open(os.path.join(ROOT, "site", "front.js"), "w", encoding="utf-8").write(js)
    digests = {}
    for f in allf:
        arr = encode_seq(qstore[f])
        fjs = f"(window.FRONTP=window.FRONTP||{{}})[{json.dumps(f)}]=" + json.dumps(arr, separators=(",", ":")) + ";"
        open(os.path.join(ROOT, "site", f"front_{f}.js"), "w").write(fjs)
        digests[f] = hashlib.sha1(fjs.encode()).hexdigest()[:10]
    fjs = "(window.FRONTP=window.FRONTP||{})[\"mcsd\"]=" + json.dumps(encode_seq(sdstore), separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "front_mcsd.js"), "w").write(fjs)
    print(f"champs : {len(fields) + 3} fichiers front_*.js (dont nowcast, combat, contact) + front_mcsd.js (écart-type Monte Carlo, 0-63 pour 0-0,5)")
    with open(os.path.join(ROOT, "data", "front_areas.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mois", "date", "perimetre"] + [f"{v}_{x}_km2" for v in VIEWS for x in ("esperance", "mediane", "zone_grise", "controle_ru_90")]
                   + ["mc_p025_km2", "mc_p975_km2", "desaccord_km2", "desaccord_doux_km2", "zone_grise_largeur_km"])
        for mo in mlist:
            if not mo:
                continue
            for per in ("tout", "horsCrimee", "depuis2022"):
                w.writerow([mo["date"][:7], mo["date"], per] + [x for v in VIEWS for x in mo["a"][v][per]] + mo["mc"][per] + mo["dis"][per]
                           + [mo["grey"]["width_km"]])
    with open(os.path.join(ROOT, "data", "front_calibration.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["p_predite", "part_occupee_selon_source_retiree", "n_mailles"])
        w.writerows(calib)
    with open(os.path.join(ROOT, "data", "front_sensibilite.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variante"] + [f"{v}_{p}_km2" for v in ("equilibre", "ua", "ru", "tiers") for p in ("tout", "depuis2022")] + ["zone_grise_km2"])
        w.writerows(sens)
    page = os.path.join(ROOT, "site", "index.html")
    s = open(page, encoding="utf-8").read()
    s2 = re.sub(r'src="front\.js(\?v=[\w-]+)?"', f'src="front.js?v={hashlib.sha1(js.encode()).hexdigest()[:10]}"', s)
    s2 = re.sub(r'src="front_equilibre\.js(\?v=[\w-]+)?"', f'src="front_equilibre.js?v={digests["equilibre"]}"', s2)
    s2 = re.sub(r'src="front_nowcast\.js(\?v=[\w-]+)?"', f'src="front_nowcast.js?v={digests["nowcast"]}"', s2)
    s2 = re.sub(r'src="front_contact\.js(\?v=[\w-]+)?"', f'src="front_contact.js?v={digests["contact"]}"', s2)
    s2 = re.sub(r'const FRONT_V = \{[^}]*\}', "const FRONT_V = " + json.dumps(digests), s2)
    if s2 != s:
        open(page, "w", encoding="utf-8").write(s2)
    lastm = [mo for mo in mlist if mo][-1]
    a = lastm["a"]["equilibre"]["tout"]
    print(f"front.js : {len(js) // 1024} Ko · {lastm['date']} : {a[0]:,} km² (95 % {lastm['mc']['tout'][0]:,}–{lastm['mc']['tout'][1]:,}) · "
          f"zone grise {lastm['grey']['km2']:,} km² ({lastm['grey']['width_km']} km) · σ0 {meta['sigma0']} · noyaux {meta['claimStrength']}")


if __name__ == "__main__":
    main()
