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
import barrier_tiling  # noqa: E402
import source_consensus  # noqa: E402

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
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))   # décalage de publication : J-1 depuis le 19/10/2026 (décision de l'utilisateur ; J-7 auparavant)
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


def carry_forward(status, verbose=True):
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
    if ended and verbose:
        print("séries arrêtées, prolongées par leur dernier statut (vieillissement progressif) : "
              + ", ".join(f"{s} ({d[:4]}-{d[4:6]}-{d[6:]})" for s, d in ended.items()), flush=True)
    return ended


def apply_dsm_log(status, places, write=True):
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
    if write:   # (False : passage des décades, sur une séquence de dates plus dense ; le journal publié reste celui des mois)
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
    if not write:
        return fixed, sorted(set(resolved))
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
    stale_keys = set()

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
                    stale_keys.add((src, g, d))
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
    rate, table, confirm = {}, {}, {}
    for src in EV_SOURCES:
        ok = tot = 0
        delays, years = [], defaultdict(lambda: [0, 0])
        for d, lon, lat, camp, s, g in ev:
            if s != src or not g:
                continue
            d0 = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
            later = [x for x in days if d < x <= (d0 + dt.timedelta(days=90)).strftime("%Y%m%d")]
            codes = [(x, status[x].get(g)) for x in later]
            judged = [(x, c[j]) for x, c in codes if c for j in EV_JUDGES[src] if c[j] in (1, 2)]
            if not judged:
                continue
            tot += 1
            want = 2 if camp == 1 else 1
            hit = [x for x, v in judged if v == want]
            if src in ("modru", "gsua"):
                confirm[(src, g, d)] = (dt.date(int(hit[0][:4]), int(hit[0][4:6]), int(hit[0][6:])) - d0).days if hit else -1
            years[d[:4]][1] += 1
            if hit:
                ok += 1
                years[d[:4]][0] += 1
                # délai : jours entre l'annonce et le premier instantané où une autre carte la confirme
                delays.append((dt.date(int(hit[0][:4]), int(hit[0][4:6]), int(hit[0][6:])) - d0).days)
        rate[src] = round(ok / tot, 3) if tot else 0.5
        dl = sorted(delays)
        table[src] = {"n": tot, "ok": ok, "rate": rate[src], "stale": stale_n.get(src, 0),
                      "delay": [dl[int(f * (len(dl) - 1))] for f in (0.25, 0.5, 0.75)] if dl else None,
                      "years": {y: v for y, v in sorted(years.items())}}
    control_events.table = table
    control_events.confirm = confirm
    control_events.stale = stale_keys
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


def read_status(dates_needed, extra=None):
    """extra (historique HIST) : {AAAAMMJJ: None} rempli de tableaux compacts (localités × 4 codes, 255 = absente) sur
    l'index read_status.gidx ; `status` ne contient que dates_needed (+ PRE_DATE), comme sans historique."""
    want = {d.strftime("%Y%m%d") for d in dates_needed} | {PRE_DATE}
    status, seen_dates = defaultdict(dict), set()
    if extra is not None:
        import numpy as np
        rows = defaultdict(bytearray)     # par date : (localité sur 3 octets, 4 codes) = 7 octets par ligne
        gidx = read_status.gidx = {}
        for path in sorted(f for f in os.listdir(SRC_DIR) if f.startswith("control_") and f.endswith(".zip")):
            with zipfile.ZipFile(os.path.join(SRC_DIR, path)) as z:
                with z.open(z.namelist()[0]) as fh:
                    head = fh.readline().decode().strip().split(",")
                    cl = [head.index(f"status_{c}") for c in VIINA]
                    for line in io.TextIOWrapper(fh, encoding="utf-8"):
                        a = line.split(",", 2)
                        if a[1] not in extra:
                            continue
                        r = line.rstrip("\n").split(",")
                        g = gidx.setdefault(r[0], len(gidx))
                        rows[a[1]] += g.to_bytes(3, "little") + bytes((CODE.get(r[cl[0]], 0), CODE.get(r[cl[1]], 0),
                                                                       CODE.get(r[cl[2]], 0), CODE.get(r[cl[3]], 0)))
        for d in list(rows):
            x = np.frombuffer(bytes(rows.pop(d)), np.uint8).reshape(-1, 7)
            gi = x[:, 0].astype(np.int64) | x[:, 1].astype(np.int64) << 8 | x[:, 2].astype(np.int64) << 16
            arr = np.full((len(gidx), 4), 255, np.uint8)
            arr[gi] = x[:, 3:]
            extra[d] = arr
        rows = None
        for d in [d for d, v in extra.items() if v is None]:
            del extra[d]
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
DAILY_DAYS = int(os.environ.get("DAILY_DAYS", "0"))   # période récente en pas de quelques jours (90 prévu ; 0 : tout mensuel)
STEP_DAYS = 3        # pas de la période récente
WINDOW_DAYS = 14     # fenêtre glissante des indicateurs de combat d'un pas récent
# historique infra-mensuel (20/10/2026, voir HistSide et docs/ETAT.md § 6) : instantanés EN BRANCHE de la chaîne
# mensuelle (sorties mensuelles et pas récents inchangés), publiés à part, un fichier par année (site/front_hist_*.js).
# HIST = "" : éteint ; "jours" : chaque jour ; "decades" : les 10 et 20 ; HIST_FROM = "AAAA-MM" : depuis ce mois.
HIST = os.environ.get("HIST", "").strip().lower()
HIST_FROM = os.environ.get("HIST_FROM", "").strip()
HIST_VIEWS = ("equilibre", "ua", "ru", "tiers")     # vues calculées (+ nowcast) ; variantes « sans X » : non
HIST_PUBLISH = ("equilibre", "nowcast", "ua", "ru", "tiers")
HIST_CACHE = os.environ.get("HIST_CACHE", os.path.join(ROOT, "raw", "front_hist_cache"))   # "" : pas de cache brut
HIST_FREEZE = int(os.environ.get("HIST_FREEZE", "45"))      # jours : plus anciens que limite − 45 j = historique figé
HIST_BUDGET_MIN = float(os.environ.get("HIST_BUDGET_MIN", "0"))   # minutes de recalcul des jours figés périmés (0 : sans limite)
HIST_ADOPT = os.environ.get("HIST_ADOPT") == "1"     # adopter les entrées sans empreinte (migration, mêmes entrées)

# Parallélisation en CI (docs/ETAT.md § 8) : FRONT_VIEWS désigne le ou les LOTS à construire dans ce passage, séparés par
# des virgules ; un lot est « vues » (les 4 vues + nowcast + toutes les couches, écrit front.js sans les surfaces des
# variantes ni la sensibilité), le nom d'une variante « sans X » (front_<variante>.js + parts/front_<variante>.json) ou
# « sens » (front_sensibilite.csv + parts/front_sens.json). FRONT_VIEWS vide = tout en un seul passage, OCTET POUR OCTET
# identique au comportement historique. scripts/front_parts.py réassemble les lots (surfaces « v » et sensibilité par date).
FRONT_VIEWS = os.environ.get("FRONT_VIEWS", "").strip()
FRONT_LOTS = [x.strip() for x in FRONT_VIEWS.split(",") if x.strip()]
PARTS = os.path.join(ROOT, "parts")


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

    def get(self, date, strict=False):
        """(combats, présence russe, présence ukrainienne) du pas, au format de load_layers.
        Feux de la fenêtre ramenés à l'échelle de load_layers (× 0,1, comme les mois ET comme le chemin HIST) : avant le
        22/10/2026 ce × 0,1 manquait sur les pas récents (chemin non-strict), d'où une densité de combats ~10× trop forte
        et une bande d'incertitude ~10× trop sensible aux feux sur ces pas (cause racine du contrôle des flèches désactivé).
        strict (historique infra-mensuel, HIST) : UCDP et pertes du DERNIER MOIS ÉCOULÉ (publiés au mois : le mois en cours
        n'est pas connu à la date de la décade) ; sinon : UCDP et pertes du mois de la date."""
        mi = self.months.index(date.strftime("%Y-%m")) - (1 if strict else 0)
        _, pres, _, ucdp = self.layers
        pu = [(lon, lat, 3.0) for d, lon, lat in self.claims if date - dt.timedelta(days=75) < d <= date + dt.timedelta(days=45)]
        fires = [(x, y, w * 0.1) for x, y, w in self.fires(date)]
        return fires + list(ucdp.get(mi, [])), pres.get(mi, []), pu

    def indicators(self, date, strict=False):
        """Indicateurs bruts du nowcast pour le pas : feux (fenêtre), UCDP (mois), pertes (mois), sur la grille.
        strict (décades) : UCDP et pertes du dernier mois écoulé."""
        import numpy as np
        mi = self.months.index(date.strftime("%Y-%m")) - (1 if strict else 0)
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


# ---------- parallélisation des vues dans un instantané (docs/ETAT.md § 8, lot « vues ») ----------
# Les quatre vues (equilibre, ua, ru, tiers) d'un même instantané sont INDÉPENDANTES : chacune ne lit que inp (partagé,
# en lecture seule) et son propre prev[name] (figé à l'entrée de l'instantané). On les calcule donc dans des processus
# forkés (inp et prev HÉRITÉS par copie sur écriture : rien à sérialiser à l'aller ; seuls obs et L reviennent). Le
# résultat est RECOMBINÉ dans l'ordre de `fields` (identique au passage séquentiel) : Monte Carlo et on_month reçoivent
# exactement les mêmes objets, d'où une sortie OCTET POUR OCTET identique. month_inputs (partagé) et le Monte Carlo
# (numpy, état latent apparié) restent séquentiels.
_VW = None   # (model, inp, prev, P, v_km, ndays) posé par le parent avant le fork, lu par les workers via l'héritage


def _view_task(spec):
    import array
    model, inp, prev, P, v_km, ndays = _VW
    name, camps, drop, ch = spec
    Pf, use = P, inp
    if ch:   # variantes « sans présence / sans indices » : σ recalculé (hors lot « vues »)
        Pf = dict(P)
        Pf.update(ch)
        use = model.rewiden(inp, Pf)
    obs = model.pool(use, camps, Pf, drop=drop)
    L = model.filter(obs, prev[name], Pf, v_km, ndays)
    return name, array.array("d", obs), array.array("d", L)


# ---------- modèle ----------
class Model:
    def __init__(self, months, snap, status, claims, official, cell, places):
        self.months, self.snap, self.status, self.claims, self.official = months, snap, status, claims, official
        self.cell, self.places = cell, places
        self.bar = None   # barrier_tiling.BarrierTiling (noyaux d'annonces arrêtés aux barrières dures)
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
                if d2 > 4 * r * r or (self.bar and self.bar.cut(c0, j2 * NX + i2)):   # pas à travers une barrière dure
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
        a = self.bar.anchor[k] if self.bar else -1   # maille hors fleuve la plus proche de la localité (sa rive)
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
                if d2 > 4 * r * r or (a >= 0 and self.bar.cut(a, j2 * NX + i2)):   # pas à travers une barrière dure
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
        # historique (HIST) : réglages appris du mois de RÉFÉRENCE (dernier mois écoulé), pas du mois en cours
        dref = getattr(self, "dec_ref", None) or {}
        okey = m if len(m) == 7 else dref.get(m, m[:7])
        offs = (getattr(self, "offsets", None) or {}).get(okey, {}) if OFFSET_ON else {}
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
        else:  # pas récent ou décade : fenêtre glissante (voir WindowLayers)
            cb, pp, pq = self.window.get(d, strict=m in dref)
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
                "flags": dict(flags), "age": {s: state["age"][s] for s in dist}, "xy_active": xy_active, "band_ev": band_ev,
                "wm": {s: v for s, v in (getattr(self, "wmult", None) or {}).get(okey, {}).items() if s in dist}}

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
        # poids dynamique par carte × secteur × mois (source_consensus.py) : w_s · m_s(x) dans le camp ; le camp pèse
        # à proportion du multiplicateur moyen de ses sources (Σ w·m / Σ w) ; sans multiplicateur : pool inchangé
        wm = inp.get("wm") or {}
        polys = [(inp["lv"][s], c, w, s, wm.get(s)) for s, c, w in srcs if s in inp["lv"]]
        pts = [(inp["ker"][s], c, w) for s, c, w in srcs if s not in inp["lv"]]
        for u in rng:
            num = {}
            den = {}
            den0 = {}
            for col, c, w, s, mm in polys:
                v = col[u]
                if v is None:
                    continue
                if shift is not None:
                    d = inp["dist"][s][u] + shift[s].get(block[u], 0.0)
                    v = max(-lmax, min(lmax, d / (inp["sigma"][s] * inp["widen"][u] + inp.get("extra", {}).get(s, {}).get(u, 0.0))))
                wu = w if mm is None else w * float(mm[u])
                num[c] = num.get(c, 0.0) + wu * v
                den[c] = den.get(c, 0.0) + wu
                den0[c] = den0.get(c, 0.0) + w
            for ker, c, w in pts:
                v = ker.get(u)
                if v is None:
                    continue
                num[c] = num.get(c, 0.0) + w * v
                den[c] = den.get(c, 0.0) + w
                den0[c] = den0.get(c, 0.0) + w
            if not self.isUA[u]:  # bande russe : a priori + preuves, identique pour toutes les vues
                out[u] = P["band_prior"] + inp["band_ev"].get(u, 0.0)
            elif den:
                if wm and WMULT_CAMP:
                    om = {c: den[c] / den0[c] for c in den}
                    out[u] = sum(om[c] * num[c] / den[c] for c in den) / sum(om.values())
                else:
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

    def run(self, P, sigma0, v_km, layers, kstrength, month_list, fields, on_month=None, mc=False, side=None, parallel=False):
        """fields : {nom: (camps, drop, changements de P)}. on_month(m, inp, {nom: p sur U}, mc).
        side (HIST) : objet HistSide ; après l'instantané m, les jours side.after[m] sont calculés EN BRANCHE
        (copie de l'état, filtre depuis prev du mois m) : rien de ce qui suit dans la chaîne n'en dépend.
        parallel : calcule les vues de chaque instantané dans des processus forkés (voir _view_task) ; la sortie reste
        OCTET POUR OCTET celle du passage séquentiel (FRONT_MP=0 force le séquentiel ; repli séquentiel si fork échoue)."""
        state = {"age": {}, "key": {}, "official": {}}
        prev = {f: None for f in fields}
        base_inp = None
        ctx, nproc, specs = None, 0, [(name, camps, drop, ch) for name, (camps, drop, ch) in fields.items()]
        if parallel and len(fields) > 1 and os.environ.get("FRONT_MP", "1") != "0":
            import multiprocessing
            nproc = min(len(fields), os.cpu_count() or 1)
            if nproc > 1:
                ctx = multiprocessing.get_context("fork")
        if mc:
            import numpy as np
            pers = getattr(self, "mc_persist", None) or {"w": 0.0, "phi": 0.0}
            # état latent des tirages, conservé d'un instantané à l'autre (tirages appariés, voir monte_carlo_paired)
            self._mc = {"rng": np.random.default_rng(42), "w": pers["w"], "phi": pers["phi"], "z": {}, "u": {}, "draws": {},
                        "dates": {}, "time": 0.0, "n": 0}
        for m in month_list:
            inp = self.month_inputs(m, P, sigma0, v_km, layers, state, kstrength)
            ps = {}
            mcres = None
            views_mc = {}
            obsL = {}   # {name: (obs, L)} calculé en parallèle (fork) ou en séquence
            if ctx is not None:
                global _VW
                _VW = (self, inp, prev, P, v_km, inp.get("ndays", 30))
                try:
                    with ctx.Pool(nproc) as _pool:
                        for name, obs, L in _pool.map(_view_task, specs):
                            obsL[name] = (obs, L)
                except Exception as e:   # pas de fork fiable : repli séquentiel, sortie identique
                    sys.stderr.write(f"build_front.py : vues en parallèle indisponibles ({e}), repli séquentiel\n")
                    ctx, obsL = None, {}
                finally:
                    _VW = None
            if not obsL:   # séquentiel (ctx None, ou repli)
                for name, (camps, drop, ch) in fields.items():
                    Pf = P
                    use = inp
                    if ch:  # variantes sans présence / sans indices : σ recalculé sans l'élargissement correspondant
                        Pf = dict(P)
                        Pf.update(ch)
                        use = self.rewiden(inp, Pf)
                    obs = self.pool(use, camps, Pf, drop=drop)
                    obsL[name] = (obs, self.filter(obs, prev[name], Pf, v_km, inp.get("ndays", 30)))
            for name in fields:   # recombinaison dans l'ordre de `fields` : identique au passage séquentiel
                obs, L = obsL[name]
                full = [0.0] * N
                for u, k in enumerate(self.U):
                    full[k] = L[u]
                prev[name] = full
                ps[name] = [sig(x) for x in L]
                if mc and name in VIEWS and (len(m) == 7 or m == getattr(self, "last_key", None)):
                    views_mc[name] = (obs, L)
            if views_mc:
                if P["camp_balance"] and set(views_mc) == set(VIEWS):
                    mcres = self.monte_carlo_paired(inp, P, views_mc, m)
                    if m == getattr(self, "last_month", None):
                        # contrôle : ancien Monte Carlo (tirages indépendants, boucle Python) sur le même instantané
                        import time
                        t0 = time.time()
                        old = self.monte_carlo(inp, P, *views_mc["equilibre"])
                        self._mc["old"] = {"time": round(time.time() - t0, 1), "lo": round(old[0]), "hi": round(old[1]),
                                           "new": [round(mcres[0]), round(mcres[1])]}
                        print(f"  Monte Carlo {m} : apparié {self._mc['last_time']:.1f} s, intervalle du stock "
                              f"{round(mcres[0]):,}–{round(mcres[1]):,} km² ; ancien {self._mc['old']['time']:.1f} s, "
                              f"{round(old[0]):,}–{round(old[1]):,} km²", flush=True)
                else:
                    mcres = self.monte_carlo(inp, P, *views_mc["equilibre"])
            if on_month:
                on_month(m, inp, ps, mcres)
            if side is not None and m in side.after:
                for key in side.after[m]:
                    side.step(self, key, P, sigma0, v_km, layers, kstrength, state, prev)
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

    def monte_carlo_paired(self, inp, P, views, key):
        """Monte Carlo à tirages APPARIÉS d'un instantané à l'autre (même modèle d'erreur que monte_carlo, vectorisé, pour
        les quatre vues d'un coup). Tirage d : décalage de chaque carte par bloc de mc_block_km, σ_s · z, et
        rééchantillonnage des sources dans chaque camp. z = √w · c + √(1 − w) · a : c permanent, a AR(1) de coefficient φ
        par mois (φ^(Δjours/30,44) entre deux instantanés) ; le choix des sources garde son tirage avec la même probabilité
        w + (1 − w) · φ^k. w et φ : persistance mesurée des écarts des cartes (mc_persistence). Le tirage d d'un instantané
        et le tirage d d'un autre sont donc corrélés comme les erreurs de position réelles : la différence des surfaces,
        tirage par tirage, donne l'intervalle du CHANGEMENT. Perturbation centrée : logit perturbé moins logit non perturbé
        calculés avec la même formule (l'ancienne version retranchait obs, calculé avec le σ local : léger biais).
        Renvoie (bas, haut, écart-type par maille) du stock pour la vue équilibrée ; écarts par tirage dans self._mc."""
        import time
        import numpy as np
        t0 = time.time()
        st = self._mc
        D, rng, w, phi = MC_DRAWS, st["rng"], st["w"], st["phi"]
        day = inp["day"]
        tau = dt.date(int(day[:4]), int(day[4:6]), int(day[6:])).toordinal()
        lmax, b1 = P["lmax"], 1 - P["beta"]
        bands = {v: np.array([u for u in range(self.nU) if self.isUA[u] and -6 < obs[u] < 6], np.int64) for v, (obs, L) in views.items()}
        B = np.unique(np.concatenate(list(bands.values())))
        nB = len(B)
        bs = max(1, int(P["mc_block_km"] / CELL_KM))
        Ub = np.asarray(self.U, np.int64)[B]
        ublk, binv = np.unique((Ub // NX // bs) * 1000 + (Ub % NX) // bs, return_inverse=True)

        def step(e):  # fait vieillir l'état latent jusqu'à tau ; renvoie r = φ^Δ (mois)
            r = phi ** (max(0, tau - e[0]) / 30.44)
            e[0] = tau
            return r
        Z = {}
        for s in inp["lv"]:
            Zs = np.empty((D, len(ublk)))
            for j, b in enumerate(ublk):
                e = st["z"].get((s, int(b)))
                if e is None:
                    e = st["z"][(s, int(b))] = [tau, rng.standard_normal(D), rng.standard_normal(D)]
                elif e[0] != tau:
                    r = step(e)
                    e[2] = r * e[2] + math.sqrt(max(0.0, 1 - r * r)) * rng.standard_normal(D)
                Zs[:, j] = math.sqrt(w) * e[1] + math.sqrt(1 - w) * e[2]
            Z[s] = Zs
        srcs = list(inp["lv"]) + [s for s in POINT if inp["ker"].get(s)]
        by_camp = defaultdict(list)
        for s in srcs:
            by_camp[CAMP[s]].append(s)
        pick = {s: np.zeros(D) for s in srcs}
        for c, lst in by_camp.items():
            for j in range(len(lst)):
                e = st["u"].get((c, j))
                if e is None:  # [date, part permanente ?, u permanent, u renouvelable]
                    e = st["u"][(c, j)] = [tau, rng.random(D) < w, rng.random(D), rng.random(D)]
                elif e[0] != tau:
                    r = step(e)
                    e[3] = np.where(rng.random(D) >= r, rng.random(D), e[3])
                idx = np.minimum((np.where(e[1], e[2], e[3]) * len(lst)).astype(np.int64), len(lst) - 1)
                for i, s in enumerate(lst):
                    pick[s] += idx == i
        # [Σ w·m·v tirage, Σ w·m tirage, Σ w·m·v, Σ w·m, Σ w tirage, Σ w] ; m : poids dynamique (source_consensus.py)
        acc = {c: [np.zeros((D, nB)), np.zeros((D, nB)), np.zeros(nB), np.zeros(nB), np.zeros((D, nB)), np.zeros(nB)] for c in CAMPS}
        wmi = inp.get("wm") or {}
        widen = np.asarray(inp["widen"], np.float64)[B]
        ex = inp.get("extra", {})
        Bl = B.tolist()
        for s in srcs:
            if CAMP[s] not in acc:
                continue
            if s in inp["lv"]:
                col, dd, exs = inp["lv"][s], inp["dist"][s], ex.get(s, {})
                valid = np.array([col[u] is not None for u in Bl])
                dist = np.array([dd[u] if col[u] is not None else 0.0 for u in Bl], np.float64)
                den = inp["sigma"][s] * widen + np.array([exs.get(u, 0.0) for u in Bl])
                v0 = np.clip(dist / den, -lmax, lmax)
                vd = np.clip((dist[None, :] + inp["sigma"][s] * Z[s][:, binv]) / den[None, :], -lmax, lmax)
            else:
                kv = [inp["ker"][s].get(u) for u in Bl]
                valid = np.array([x is not None for x in kv])
                v0 = np.array([0.0 if x is None else x for x in kv])
                vd = v0[None, :]
            v0 = np.where(valid, v0, 0.0)
            vd = np.where(valid[None, :], vd, 0.0)
            wd = (W[s] * pick[s])[:, None]
            mB = np.asarray(wmi[s], np.float64)[B] if s in wmi else np.ones(nB)
            a = acc[CAMP[s]]
            a[0] += wd * mB[None, :] * vd
            a[1] += wd * (mB * valid)[None, :]
            a[2] += W[s] * mB * v0
            a[3] += W[s] * mB * valid
            a[4] += wd * valid[None, :]
            a[5] += W[s] * valid
        out = {}
        sd = {}
        lo = hi = None
        for v, (obs, L) in views.items():
            o, o0 = 0.0, 0.0
            n, n0 = 0, 0
            for c in VIEWS[v]:
                # moyenne des camps pondérée par ω_c = Σ w·m / Σ w (= 1 sans poids dynamique) : ω_c · (Σ w·m·v / Σ w·m) = Σ w·m·v / Σ w
                a = acc[c]
                ok, ok0 = a[4] > 0, a[5] > 0
                if WMULT_CAMP:
                    o = o + np.where(ok, a[0] / np.where(ok, a[4], 1.0), 0.0)
                    o0 = o0 + np.where(ok0, a[2] / np.where(ok0, a[5], 1.0), 0.0)
                    n = n + np.where(ok, a[1] / np.where(ok, a[4], 1.0), 0.0)
                    n0 = n0 + np.where(ok0, a[3] / np.where(ok0, a[5], 1.0), 0.0)
                else:  # chaque camp garde son tiers ; le poids dynamique ne joue qu'à l'intérieur du camp
                    o = o + np.where(ok, a[0] / np.where(ok, a[1], 1.0), 0.0)
                    o0 = o0 + np.where(ok0, a[2] / np.where(ok0, a[3], 1.0), 0.0)
                    n, n0 = n + ok, n0 + ok0
            o = np.where(n > 0, o / np.where(n > 0, n, 1.0), 0.0)
            o0 = np.where(n0 > 0, o0 / np.where(n0 > 0, n0, 1.0), 0.0)
            pos = np.searchsorted(B, bands[v])
            Lb = np.asarray([L[u] for u in bands[v].tolist()], np.float64)
            pv = 1 / (1 + np.exp(-(Lb[None, :] + b1 * (o[:, pos] - o0[None, pos]))))
            if "isUA" not in st:
                st["isUA"] = np.asarray(self.isUA, bool)
            base = float((1 / (1 + np.exp(-np.asarray(L, np.float64)[st["isUA"]]))).sum())
            band_area = float((1 / (1 + np.exp(-Lb))).sum())
            areas = (base - band_area + pv.sum(1)) * CELL_KM ** 2
            out[v] = areas - base * CELL_KM ** 2   # écart du tirage à l'espérance du champ (km²)
            if v == "equilibre":
                srt = np.sort(areas)
                lo, hi = float(srt[int(0.025 * (D - 1))]), float(srt[int(round(0.975 * (D - 1)))])
                s_ = pv.std(0)
                sd = {int(u): float(x) for u, x in zip(bands[v].tolist(), s_)}
        st["draws"][key] = out
        st["dates"][key] = tau
        st["last_time"] = time.time() - t0
        st["time"] += st["last_time"]
        st["n"] += 1
        return [lo, hi, sd]

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

    def metrics_np(self, p, crimea, pre, sectors=False):
        """metrics vectorisé (numpy), mêmes définitions ; sommes en float64 (à l'arrondi près des sommes Python).
        Utilisé pour le seul historique infra-mensuel (HIST) : les instantanés mensuels et les pas récents gardent metrics."""
        import numpy as np
        if getattr(self, "_mnp", None) is None:
            cu = np.array([self.cell[k] for k in self.U], np.int64)
            nbm = np.full((self.nU, 4), self.nU, np.int64)
            for u, q in enumerate(self.nb):
                nbm[u, :len(q)] = q
            names = sorted(set(self.sector))
            sid = {n: i for i, n in enumerate(names)}
            self._mnp = (cu, nbm, names, np.array([sid[s] for s in self.sector], np.int64))
        cu, nbm, names, secu = self._mnp
        crim = np.asarray(crimea, bool)
        prez = np.asarray(pre, bool)
        p = np.asarray(p, np.float64)
        pe = np.append(p, 1.0)
        edge = (p >= 0.5) & (pe[nbm] < 0.5).any(1)
        band = cu == -2
        ua = ~band
        pl = np.where(ua, cu, 0)
        hc = ua & ~crim[pl]
        d22 = hc & ~prez[pl]
        rows = (p, (p >= 0.5).astype(float), ((p >= 0.05) & (p <= 0.95)).astype(float), (p > 0.95).astype(float))
        a2 = CELL_KM ** 2
        res = {per: [round(float(r[m].sum()) * a2) for r in rows] for per, m in (("tout", ua), ("horsCrimee", hc), ("depuis2022", d22))}
        ru_exp = float(np.where(p < 0.95, 1 - p, 0.0)[band].sum())
        ru_med = float((p[band] < 0.5).sum())
        res["russie_ua"] = [round(ru_exp * a2), round(ru_med * a2)]
        L_km = int(edge.sum()) * CELL_KM
        grey = res["tout"][2]
        res["front_km"] = round(L_km)
        res["grey_width_km"] = round(grey / L_km, 2) if L_km else None
        if sectors:
            ga = np.bincount(secu[ua], weights=rows[2][ua], minlength=len(names))
            ge = np.bincount(secu[ua], weights=edge[ua].astype(float), minlength=len(names))
            res["sectors"] = {n: [round(float(ga[i]) * a2), round(float(ga[i]) * a2 / (ge[i] * CELL_KM), 2) if ge[i] >= 12 else None]
                              for i, n in enumerate(names) if n in FRONT_ADM1 and (ga[i] or ge[i])}
        return res


# ---------- historique infra-mensuel (HIST : jours ou décades, 20/10/2026) ----------
def _copy_state(st):
    """Copie de l'état persistant de month_inputs (âges, clés, changements, codes, annonces actives) : les valeurs sont
    immuables, une copie des dictionnaires suffit."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in st.items()}
    out["official"] = {s: dict(v) for s, v in st.get("official", {}).items()}
    return out


class HistStatus:
    """Statuts VIINA des jours de l'historique, dérivés SANS retraiter la chaîne mensuelle : pour chaque localité et
    chaque carte, si le statut brut du jour est celui du brut du mois de référence M, on reprend le statut TRAITÉ de M
    (prolongation des séries arrêtées, barrières dures, journal DeepState) ; sinon (VIINA a changé depuis M), le statut
    brut du jour ; enfin les déclarations datées de DeepState postérieures à M et au plus tard le jour s'appliquent à la
    colonne DeepState si VIINA-DeepState n'a pas changé depuis M (règle de apply_dsm_log : VIINA plus récent garde la main)."""

    def __init__(self, raw_c, gidx, status, refday, day, dsm_events, places, ended=None):
        import numpy as np
        n = len(gidx)
        # séries arrêtées (carry_forward) : après leur dernier jour, la colonne reprend celle du mois traité (prolongée)
        self.ended = {VIINA.index(s): d for s, d in (ended or {}).items()}
        self.gid = [None] * n
        for g, i in gidx.items():
            self.gid[i] = g
        self.refday, self.day = refday, day
        need = set(refday.values()) | set(day.values())
        self.raw = {d: v for d, v in raw_c.items() if d in need}
        self.proc = {}
        for rd in set(refday.values()):
            arr = np.full((n, 4), 255, np.uint8)
            for g, c in status[rd].items():
                i = gidx.get(g)
                if i is not None:
                    arr[i] = c
            self.proc[rd] = arr
        self.ev = sorted((d, gidx[places[k]["id"]], 1 if camp == "ua" else 2) for d, camp, k in dsm_events if places[k]["id"] in gidx)

    def compact(self, key):
        import numpy as np
        d, rd = self.day[key], self.refday[key]
        a, rM, pM = self.raw[d], self.raw[rd], self.proc[rd]
        out = np.where(a == rM, pM, a)
        for n, dl in self.ended.items():
            if d > dl and rd >= dl:
                out[:, n] = np.where(a[:, 0] == 255, 255, np.where(pM[:, n] == 255, 0, pM[:, n]))
        last = {}
        for ed, i, code in self.ev:
            if rd < ed <= d:
                last[i] = code
        if last:
            ii = np.fromiter(last, np.int64)
            cc = np.fromiter(last.values(), np.uint8)
            ok = (a[ii, 0] == rM[ii, 0]) & (a[ii, 0] != 255)
            out[ii[ok], 0] = cc[ok]
        return out

    def dict_for(self, key):
        g = self.gid
        return {g[i]: tuple(r) for i, r in enumerate(self.compact(key).tolist()) if r[0] != 255}


class HistHash:
    """Empreinte des ENTRÉES d'un jour de l'historique (sha1, 16 caractères) : VERSION du code, réglages globaux arrondis
    (σ0, v, force des noyaux à 0,01 près ; demi-vie au jour ; autres réglages exacts), statuts VIINA dérivés du jour,
    statuts traités de tous les mois de référence jusqu'à M (état de la chaîne), annonces officielles datées jusqu'au jour
    + 46 jours (bande russe, comme band_evidence), annonces des médias de la fenêtre de 90 jours, feux de la fenêtre de
    14 jours et normales de leurs mois, UCDP et pertes du mois M, reprises annoncées par le MoD RF de la fenêtre
    −75 / +45 jours, décalages par carte de M. Une entrée qui arrive en retard (rétro-collecte, révision de VIINA, nouveau
    réglage) change l'empreinte des jours concernés, qui sont recalculés."""

    def __init__(self, model, hstat, months, layers, P, sigma0, v_km, kstrength):
        import bisect
        import numpy as np
        self.bisect = bisect
        self.model, self.hstat, self.months, self.layers = model, hstat, months, layers
        glob = (HistSide.VERSION, sorted((k, round(v, 3) if isinstance(v, float) else v) for k, v in P.items() if k != "official_half_life"),
                round(P["official_half_life"]), sorted((s, round(v, 2)) for s, v in sigma0.items()), round(v_km, 2),
                sorted((s, round(v, 2)) for s, v in kstrength.items()), HIST_VIEWS, OFFSET_ON, WMULT_ON)
        self.glob = hashlib.sha1(repr(glob).encode()).digest()
        # états traités des mois de référence, en préfixe chronologique
        self.pref = {}
        h = hashlib.sha1()
        for rd in sorted(hstat.proc):
            h.update(hstat.proc[rd].tobytes())
            self.pref[rd] = h.digest()
        off = sorted((d, src, repr(k)) for src in ("modru", "gsua") for k, dates in model.official.get(src, {}).items() for d in dates)
        self.off_d = [x[0] for x in off]
        self.off_h = []
        h = hashlib.sha1()
        for x in off:
            h.update(repr(x).encode())
            self.off_h.append(h.digest())
        cl = sorted((d, g, s) for g, lst in model.claims.items() for d, s in lst)
        self.cl_d, self.cl = [x[0] for x in cl], cl
        W_ = model.window
        self.det = {d: hashlib.sha1(repr(sorted(v.items())).encode()).digest() for d, v in W_.det.items()}
        mu = defaultdict(list)
        for (i, j, m), v in W_.mu.items():
            mu[m].append((i, j, v))
        self.mu = {m: hashlib.sha1(repr(sorted(v)).encode()).digest() for m, v in mu.items()}
        self.ru = sorted(W_.claims)
        self.ru_d = [x[0] for x in self.ru]
        self.np = np

    def __call__(self, key):
        b = self.bisect
        model, hs = self.model, self.hstat
        d = model.snap[key]
        rd = hs.refday[key]
        M = model.dec_ref[key]
        h = hashlib.sha1(self.glob)
        h.update(key.encode())
        h.update(hs.compact(key).tobytes())
        h.update(self.pref[rd])
        i = b.bisect_right(self.off_d, (d + dt.timedelta(days=46)).strftime("%Y%m%d"))
        h.update(self.off_h[i - 1] if i else b"")
        lo, hi = b.bisect_right(self.cl_d, (d - dt.timedelta(days=91)).strftime("%Y%m%d")), b.bisect_right(self.cl_d, d.strftime("%Y%m%d"))
        h.update(repr(self.cl[lo:hi]).encode())
        mos = set()
        for j in range(WINDOW_DAYS):
            x = d - dt.timedelta(days=j)
            mos.add(x.strftime("%Y-%m"))
            h.update(self.det.get(x.isoformat(), b""))
        for m in sorted(mos):
            h.update(self.mu.get(m, b""))
        mi = self.months.index(M)
        h.update(repr((self.layers[3].get(mi, []), self.layers[1].get(mi, []))).encode())
        lo, hi = b.bisect_right(self.ru_d, d - dt.timedelta(days=75)), b.bisect_right(self.ru_d, d + dt.timedelta(days=45))
        h.update(repr(self.ru[lo:hi]).encode())
        offs = (getattr(model, "offsets", None) or {}).get(M, {}) if OFFSET_ON else {}
        for s in sorted(offs):
            h.update(s.encode() + self.np.asarray(offs[s], self.np.float32).tobytes())
        return h.hexdigest()[:16]


class HistSide:
    """Instantanés HISTORIQUES infra-mensuels (HIST : tous les jours, ou les 10 et 20 ; la fin de mois est l'instantané
    mensuel), calculés EN BRANCHE depuis l'état du dernier mois écoulé M : filtre temporel depuis le champ de M (pas de
    1 à 30 jours), persistance des sources copiée, réglages appris de M (décalages par carte, β du nowcast, bande
    locale), indicateurs publiés au mois (UCDP, pertes) de M, feux de la fenêtre de 14 jours finissant à la date. La
    chaîne mensuelle n'est pas touchée (sorties mensuelles et pas récents identiques) ; les réglages sont réappris sur
    tout l'historique à chaque build, seul le calcul coûteux du champ de chaque jour est gardé.

    Gel PAR EMPREINTE (HistHash) : un jour antérieur à limite − HIST_FREEZE jours (45) est repris tel quel si son
    empreinte d'entrées est inchangée, (1) des fichiers PUBLIÉS site/front_hist_<année>*.js (champ « h » de chaque jour ;
    aucun calcul), ou (2) du cache brut HIST_CACHE (défaut raw/front_hist_cache/, une entrée par jour : champs des 4 vues
    avant bande locale sur 16 bits et codes des cartes ; points de reprise d'un premier calcul long). Sinon il est
    recalculé, du plus ancien au plus récent, dans la limite de HIST_BUDGET_MIN minutes de calcul (0 : sans limite) ;
    au-delà, l'ancienne version (périmée) est gardée et le recalcul reprend au passage suivant. Les 45 derniers jours
    sont toujours recalculés. Changer VERSION recalcule tout (changement du modèle). HIST_ADOPT=1 : les entrées SANS
    empreinte (calcul antérieur à l'empreinte, mêmes entrées) sont adoptées avec l'empreinte du jour."""
    VERSION = "1"

    def __init__(self, model, keys, ref, frozen, published, hstat, hashes, stale_pub):
        self.model, self.keys, self.ref, self.hstat = model, keys, ref, hstat
        self.frozen, self.published, self.hashes = frozen, published, hashes   # published : repris tels quels
        self.stale_pub = stale_pub        # jours publiés d'empreinte périmée (gardés si le budget est épuisé)
        self.after = defaultdict(list)
        for k in sorted(keys):
            if k not in published:
                self.after[ref[k]].append(k)
        self.res = {}           # clé → données en mémoire, ou "disk" (cache brut), ou "stale" (version publiée périmée)
        self.n_cache = self.n_new = self.n_skip = self.n_stale = self.n_redo = 0
        self.t_cmp = 0.0
        self.dir = HIST_CACHE

    def _path(self, key):
        return os.path.join(self.dir, f"{key}.pkl")

    def _entry(self, key):
        import pickle
        import zlib
        if not self.dir or not os.path.exists(self._path(key)):
            return None
        try:
            e = pickle.loads(zlib.decompress(open(self._path(key), "rb").read()))
        except Exception:  # noqa: BLE001 (entrée illisible : recalculée)
            return None
        return e if e.get("version") == self.VERSION else None

    def load(self, key, h=None, any_hash=False):
        e = self._entry(key)
        if e is None:
            return None
        eh = e.get("h") or e["data"].get("h")
        if any_hash or eh == h or (eh is None and HIST_ADOPT):
            e["data"]["h"] = eh if any_hash else h
            return e["data"]
        return None

    def save(self, key, data):
        import pickle
        import zlib
        os.makedirs(self.dir, exist_ok=True)
        blob = zlib.compress(pickle.dumps({"version": self.VERSION, "h": data.get("h"), "created": dt.date.today().isoformat(),
                                           "data": data}, protocol=4), 6)
        tmp = self._path(key) + ".tmp"
        open(tmp, "wb").write(blob)
        os.replace(tmp, self._path(key))

    def get(self, key):
        r = self.res.get(key)
        if r == "disk":
            return self.load(key, self.hashes.get(key))
        if r == "disk-stale":
            return self.load(key, any_hash=True)
        return r

    def step(self, model, key, P, sigma0, v_km, layers, kstrength, state, prev):
        import time
        import numpy as np
        h = self.hashes.get(key)
        if key in self.frozen:
            if self.load(key, h) is not None:
                self.res[key] = "disk"
                self.n_cache += 1
                return
            self.n_redo += 1
            if HIST_BUDGET_MIN and self.t_cmp > HIST_BUDGET_MIN * 60:
                # budget épuisé : version périmée gardée (publiée, sinon cache), recalcul au passage suivant
                if key in self.stale_pub:
                    self.res[key] = "stale"
                    self.n_stale += 1
                elif self.load(key, any_hash=True) is not None:
                    self.res[key] = "disk-stale"
                    self.n_stale += 1
                else:
                    self.n_skip += 1
                return
        t1 = time.time()
        st = _copy_state(state)
        dkey = model.snap[key].strftime("%Y%m%d")
        old = model.status.get(dkey)
        model.status[dkey] = self.hstat.dict_for(key)
        try:
            inp = model.month_inputs(key, P, sigma0, v_km, layers, st, kstrength)
        finally:
            if old is None:
                model.status.pop(dkey, None)
            else:
                model.status[dkey] = old
        ps = {}
        for name in HIST_VIEWS:
            obs = model.pool(inp, VIEWS[name], P)
            L = model.filter(obs, prev[name], P, v_km, inp["ndays"])
            ps[name] = np.round(1.0 / (1.0 + np.exp(-np.clip(np.asarray(L, np.float64), -40, 40))) * 65535).astype(np.uint16)
        cur = {}
        for k, b in enumerate(inp["base"]):
            fl = inp["flags"].get(k, 0)
            if any(c > 1 for c in b) or fl:
                cur[k] = b[0] | b[1] << 2 | b[2] << 4 | b[3] << 6 | fl
        data = {"ps": ps, "cur": cur, "ndays": inp["ndays"], "sigma": {s: round(v, 2) for s, v in inp["sigma"].items()},
                "age": {s: round(v) for s, v in inp["age"].items()}, "h": h}
        if self.dir:
            self.save(key, data)
            self.res[key] = "disk"
        else:
            self.res[key] = data
        self.n_new += 1
        self.t_cmp += time.time() - t1
        if self.n_new % 25 == 1:
            print(f"  historique {key} (branche depuis {self.ref[key]}, {data['ndays']} j) : calculé en {time.time() - t1:.1f} s ; "
                  f"{self.n_new} calculés, {self.n_cache} relus du cache", flush=True)


def _encode_np(q, prevq):
    """encode_field vectorisé (mêmes octets) : valeurs 0-63, 255 = inchangé, longueurs en varint, base 64."""
    import numpy as np
    v = np.frombuffer(bytes(q), np.uint8).astype(np.int16)
    if prevq is not None:
        v = np.where(np.frombuffer(bytes(prevq), np.uint8).astype(np.int16) == v, 255, v)
    cut = np.flatnonzero(np.diff(v)) + 1
    starts, ends = np.r_[0, cut], np.r_[cut, len(v)]
    out = bytearray()
    for a, b in zip(starts.tolist(), ends.tolist()):
        out.append(int(v[a]))
        n = b - a
        while True:
            c = n & 0x7F
            n >>= 7
            out.append(c | 0x80 if n else c)
            if not n:
                break
    return base64.b64encode(bytes(out)).decode()


def _decode_seq(enc, n):
    """Décodage d'une suite encodée (différences d'une entrée à la précédente) : liste de bytes ou None."""
    out, prev = [], bytes(n)
    for e in enc:
        if not e:
            out.append(None)
            continue
        b, q, o, i = base64.b64decode(e), bytearray(n), 0, 0
        while i < len(b):
            v = b[i]
            i += 1
            k = sh = 0
            while True:
                c = b[i]
                i += 1
                k |= (c & 127) << sh
                sh += 7
                if not c & 128:
                    break
            q[o:o + k] = prev[o:o + k] if v == 255 else bytes([v]) * k
            o += k
        out.append(bytes(q))
        prev = out[-1]
    return out


def _hist_path(Y, f=None):
    return os.path.join(ROOT, "site", f"front_hist_{Y}.js" if f is None else f"front_hist_{Y}_{f}.js")


def _after_eq(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


def read_published_hist():
    """Métadonnées des fichiers publiés site/front_hist_<année>.js de même VERSION et même cadence : {date ISO: méta}."""
    import glob as _glob
    out = {}
    for path in sorted(_glob.glob(os.path.join(ROOT, "site", "front_hist_[0-9][0-9][0-9][0-9].js"))):
        try:
            H = _after_eq(path)
        except (OSError, ValueError):
            continue
        if H.get("version") != HistSide.VERSION or H.get("cadence") != HIST:
            continue
        if not all(os.path.exists(path[:-3] + f"_{f}.js") for f in HIST_PUBLISH):
            continue
        for k, mo in zip(H["keys"], H["months"]):
            out[k] = mo
    return out


def published_hist_fields(Y, nU):
    """Champs publiés (6 bits) d'une année : {date ISO: {vue: bytes}}."""
    H = _after_eq(_hist_path(Y))
    F = {f: _decode_seq(_after_eq(_hist_path(Y, f)), nU) for f in HIST_PUBLISH}
    return {k: {f: F[f][j] for f in HIST_PUBLISH} for j, k in enumerate(H["keys"]) if all(F[f][j] is not None for f in HIST_PUBLISH)}


def write_hist(side, hist, hist_ref, pub_hist, months, fm, snap, months_out, codes_store, ncast, ind, lbs, model, crimea,
               pre, today, limit):
    """Historique (HIST) : nowcast en branche (β du mois de référence, champ des cartes du dernier mois ≤ date − 60 jours
    pour la tendance, indicateurs de la fenêtre stricte), bande locale du mois de référence, surfaces, intervalle Monte
    Carlo du mois de référence reporté autour de l'espérance du jour, états des cartes en différences par rapport au mois
    de référence. Un fichier par année : site/front_hist_<année>.js (méta) et site/front_hist_<année>_<vue>.js (champs,
    même codage que front_<vue>.js, différences d'un jour au précédent dans l'année). Une année dont tous les jours sont
    figés et déjà publiés n'est pas réécrite. Renvoie l'index publié dans FRONT.hist."""
    import time
    import numpy as np
    from conformal import LN19
    t0 = time.time()
    by_year = defaultdict(list)
    for k in sorted(hist, key=lambda k: hist[k]):
        by_year[hist[k].year].append(k)
    bc = {}

    def remap(f, p, t):
        if bc.get(f, (None,))[0] != t:
            bc[f] = (t, lbs[f].bounds(t))
        b = bc[f][1]
        sd = lbs[f].sd(p) if b is not None else None
        if sd is None:
            return p
        lo, hi = b
        D = sd + 1.0
        sc = np.where(D < 0, -lo, hi)
        q = (1.0 / (1.0 + np.exp(-np.clip(LN19 * D / sc, -40, 40)))).astype(np.float32)
        return np.where(lbs[f].s.isUA, q, p).astype(np.float32)

    def post(k, data):
        fd, M = hist[k], hist_ref[k]
        tM = months.index(M)
        ps = {f: data["ps"][f].astype(np.float32) / np.float32(65535) for f in HIST_VIEWS}
        lim60 = fd - dt.timedelta(days=60)
        cand = [m for m in fm if snap[m] <= lim60 and months.index(m) in ncast.P]
        p_back = ncast.P[months.index(cand[-1] if cand else fm[0])]
        ind_d = list(model.window.indicators(fd, strict=True)) + list(ind[tM][3:4])
        pn, _, _ = ncast.apply_arrays(ps["equilibre"], ind_d, p_back, ncast.beta[tM])
        ps["nowcast"] = np.asarray(pn, np.float32)
        mo = {"date": fd.isoformat(), "ref": tM, "ndays": data["ndays"], "a": {}, "beta": ncast.beta[tM],
              "sigma": data["sigma"], "age": data["age"], "h": data.get("h")}
        qs = {}
        for f in HIST_PUBLISH:
            p1 = remap(f, ps[f], tM)
            qs[f] = np.clip(np.round(p1 * 63), 0, 63).astype(np.uint8).tobytes()
            met = model.metrics_np(p1, crimea, pre, sectors=(f == "equilibre"))
            mo["a"][f] = {kk: met[kk] for kk in ("tout", "horsCrimee", "depuis2022")}
            mo["a"][f]["russie"] = met["russie_ua"]
            if f == "equilibre":
                mo["grey"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"], "front_km": met["front_km"], "sectors": met.get("sectors", {})}
            elif f == "nowcast":
                mo["greyNow"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"]}
        mM = months_out[M]
        if mM and mM.get("mc"):
            mo["mc"] = {per: [x - mM["a"]["equilibre"][per][0] + mo["a"]["equilibre"][per][0] for x in mM["mc"][per]] for per in mM["mc"]}
            d_ = mo["a"]["nowcast"]["tout"][0] - mo["a"]["equilibre"]["tout"][0]
            mo["mcNow"] = {per: [x + d_ for x in mo["mc"][per]] for per in mo["mc"]}
        base_c, cur = codes_store.get(M, {}), data["cur"]
        diff = [[q, c] for q, c in cur.items() if base_c.get(q) != c] + [[q, 0] for q in base_c if q not in cur]
        mo["d"] = [x for qc in sorted(diff) for x in qc]
        return mo, qs

    index, n_new, n_pub, n_kept = {}, 0, 0, 0
    for Y, ks in sorted(by_year.items()):
        if all(k in pub_hist for k in ks) and os.path.exists(_hist_path(Y)):
            n_kept += len(ks)
            sz = sum(os.path.getsize(_hist_path(Y, f)) for f in HIST_PUBLISH) + os.path.getsize(_hist_path(Y))
            index[str(Y)] = {"n": len(ks), "first": hist[ks[0]].isoformat(), "last": hist[ks[-1]].isoformat(), "ko": sz // 1024}
            continue
        old = {k: side.stale_meta[k] for k in ks if side.res.get(k) == "stale"}
        pubq = published_hist_fields(Y, model.nU) if any(k in pub_hist or k in old for k in ks) else {}
        metas, enc, prevq = [], {f: [] for f in HIST_PUBLISH}, {f: None for f in HIST_PUBLISH}
        for k in ks:
            iso = hist[k].isoformat()
            if (k in pub_hist or k in old) and iso in pubq:
                mo, qs = (pub_hist[k] if k in pub_hist else old[k]), pubq[iso]
                n_pub += 1
            else:
                data = side.get(k)
                if data is None:
                    continue
                mo, qs = post(k, data)
                n_new += 1
            metas.append(mo)
            for f in HIST_PUBLISH:
                enc[f].append(_encode_np(qs[f], prevq[f]))
                prevq[f] = qs[f]
        if not metas:
            continue
        H = {"version": HistSide.VERSION, "cadence": HIST, "generated": today.isoformat(), "limit": limit.isoformat(), "year": Y,
             "keys": [mo["date"] for mo in metas], "fields": list(HIST_PUBLISH),
             "method": "instantanés calculés en BRANCHE depuis l'état du dernier mois écoulé (ref, indice dans FRONT.months) : "
                       "filtre temporel depuis son champ (pas de ndays jours), décalages par carte, β du nowcast et bande locale "
                       "de ce mois, feux de la fenêtre de 14 jours, UCDP et pertes du mois écoulé ; fins de mois : FRONT.months ; "
                       "d = différences de codes des cartes par rapport au mois ref ; mc = intervalle du mois ref reporté",
             "months": metas}
        js = f"(window.FRONT_HIST=window.FRONT_HIST||{{}})[{json.dumps(str(Y))}]=" + json.dumps(
            H, ensure_ascii=False, separators=(",", ":"), default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + ";"
        open(_hist_path(Y), "w", encoding="utf-8").write(js)
        sz = len(js)
        for f in HIST_PUBLISH:
            fjs = f"(window.FRONTH=window.FRONTH||{{}})[{json.dumps(f'{Y}/{f}')}]=" + json.dumps(enc[f], separators=(",", ":")) + ";"
            open(_hist_path(Y, f), "w").write(fjs)
            sz += len(fjs)
        index[str(Y)] = {"n": len(metas), "first": metas[0]["date"], "last": metas[-1]["date"], "ko": sz // 1024}
        print(f"historique {Y} : {len(metas)} jours écrits ({sz // 1024} Ko)", flush=True)
    print(f"historique : {n_new} jours post-traités, {n_pub} repris des fichiers publiés, {n_kept} dans des années inchangées "
          f"({time.time() - t0:.0f} s)", flush=True)
    return {"cadence": HIST, "version": HistSide.VERSION, "fields": list(HIST_PUBLISH), "years": index,
            "files": "front_hist_<année>.js (window.FRONT_HIST[année]) et front_hist_<année>_<vue>.js (window.FRONTH[\"année/vue\"])"}


# ---------- calibration ----------
OFFSET_ON = True    # décalage signé par source et par secteur, retiré avant la mise en commun (voir compute_offsets)
# poids dynamique par carte × secteur × mois (source_consensus.py) : codé, testé dans le build réel du 19/10/2026,
# NON RETENU (mailles qui changent de camp dégradées sur 2025-2026, avance de DeepState au nord de Lyman cassée ; § 6).
# Désactivé ; essais locaux : SC_ON=1 SC_ARGS="mmax=1.0,both=1,H=3" [SC_CAMP=0]
WMULT_ON = os.environ.get("SC_ON") == "1"
WMULT_CAMP = os.environ.get("SC_CAMP", "1") == "1"   # le camp pèse à proportion du multiplicateur moyen de ses sources
WMULT_ARGS = {k: (v == "1") if k in ("durable", "both") else int(v) if k in ("H", "TS") else float(v) for k, v in (a.split("=") for a in os.environ.get("SC_ARGS", "").split(",") if a)}


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
            ERR[(t, s)] = (tile[idx[ok]], e[ok], idx[ok])
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
    model.mc_persist = mc_persistence(ERR, out, fm, uj, ui, P)
    return out


def mc_persistence(ERR, out, fm, uj, ui, P):
    """Persistance dans le temps de l'erreur de position des cartes (tirages appariés du Monte Carlo, voir
    Model.monte_carlo_paired). Écart résiduel de chaque carte (DeepState, ISW, Wikipédia) à la ligne de vérité retardée
    (consensus à t + 2), décalage appris retiré (celui que le modèle retire lui-même), moyenné par bloc de mc_block_km
    (les blocs du Monte Carlo) ; corrélation (non centrée : erreur de moyenne nulle) entre le mois t et le mois t − k,
    même carte, même bloc. Ajustement ρ(k) = w + (1 − w) · φ^k : part permanente w et part AR(1) de coefficient φ par mois.
    Le bruit de maille dans la moyenne par bloc et l'erreur propre du consensus abaissent ρ : estimation prudente
    (intervalle du changement plutôt trop large)."""
    import numpy as np
    bs = max(1, int(P["mc_block_km"] / CELL_KM))
    blk = (uj // bs) * 1000 + ui // bs
    R = {}
    for (t, s), (_, e, ix) in ERR.items():
        b = out.get(fm[t], {}).get(s)
        r = e - (b[ix] if b is not None else 0.0)
        ub, inv, cnt = np.unique(blk[ix], return_inverse=True, return_counts=True)
        mean = np.bincount(inv, weights=r) / cnt
        R[(t, s)] = {int(k): float(v) for k, v, c in zip(ub, mean, cnt) if c >= 5}
    rho = {}
    for k in range(1, 13):
        sxy = sxx = syy = 0.0
        n = 0
        for (t, s), cur in R.items():
            old = R.get((t - k, s))
            if not old:
                continue
            for bk, v in cur.items():
                if bk in old:
                    sxy += v * old[bk]
                    sxx += v * v
                    syy += old[bk] ** 2
                    n += 1
        if n >= 30 and sxx > 0 and syy > 0:
            rho[k] = (sxy / math.sqrt(sxx * syy), n)
    if len(rho) < 3:  # trop peu de données : tirages indépendants d'un mois à l'autre (intervalle large, prudent)
        print("  persistance des écarts : trop peu de paires, tirages indépendants d'un mois à l'autre", flush=True)
        return {"w": 0.0, "phi": 0.0, "rho": {}}
    best = (1e18, 0.0, 0.0)
    for w in [i / 100 for i in range(0, 96)]:
        for ph in [i / 100 for i in range(0, 100)]:
            err = sum(n * (r - (w + (1 - w) * ph ** k)) ** 2 for k, (r, n) in rho.items())
            if err < best[0]:
                best = (err, w, ph)
    _, w, ph = best
    print("  persistance des écarts de position (bloc de %d km, résidu après décalage appris) : " % int(P["mc_block_km"])
          + ", ".join(f"ρ({k}) {r:.2f}" for k, (r, n) in sorted(rho.items()) if k in (1, 2, 3, 6, 12))
          + f" ; ajusté w {w:.2f}, φ {ph:.2f} (ρ(1) {w + (1 - w) * ph:.2f}, ρ(12) {w + (1 - w) * ph ** 12:.2f})", flush=True)
    return {"w": w, "phi": ph, "rho": {k: [round(r, 3), n] for k, (r, n) in sorted(rho.items())}}


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


def meta_js(meta):
    """Sérialisation unique de site/front.js, partagée par main() et par front_parts.py (assemblage des lots).
    Le réassemblage réinsère les surfaces des variantes (clé « v » par date) et la sensibilité, puis rappelle cette
    fonction : le front.js reconstruit est alors OCTET POUR OCTET celui d'un passage unique."""
    return "window.FRONT=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":"),
                                        default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + ";"


def write_manifest(lot, rels):
    """Lot parallèle : liste des fichiers produits (chemins relatifs à la racine) dans parts/manifest_<lot>.txt."""
    os.makedirs(PARTS, exist_ok=True)
    with open(os.path.join(PARTS, f"manifest_{lot}.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rels) + "\n")


def compute_sensitivity(model, P, sig0, v_km, layers, kstrength, fm, crimea, pre):
    """Analyse de sensibilité : surfaces des quatre vues au dernier instantané pour neuf jeux de paramètres alternatifs.
    Détachée du pipeline des vues (indépendante : ne lit que le modèle calibré). Rejouée telle quelle dans le lot « sens »."""
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
    return sens


def wanted_dates(months, cutoff_day):
    out = {}
    for m in months:
        y, mo = int(m[:4]), int(m[5:])
        end = dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)
        if end > cutoff_day:
            break
        out[m] = end
    return out


CHANGE_LAGS = (("m1", 30), ("m12", 365))   # mêmes paires que frameAt(fi, 30) et frameAt(fi, 365) dans site/app.js


def change_intervals(model, months, dk, months_out, days_out, fm, cons_area):
    """Intervalle à 95 % du CHANGEMENT de surface occupée (tout le territoire) entre un instantané et celui de 30 / 365
    jours avant (même choix que frameAt dans site/app.js) : centiles 2,5 et 97,5 de la différence des écarts des tirages
    APPARIÉS (monte_carlo_paired), ajoutés au changement publié. Écrit mo["dmc"] = {"m1": [bas, haut], "m12": [bas, haut]}
    (vue équilibrée) et mo["dmcV"] = {"ua"|"ru"|"tiers": {...}}. Pas récents sans tirages propres : couple de tirages du
    dernier instantané tiré et de celui qui le précède du même délai (comme le stock, écarts reportés). Contrôle sur
    l'historique : la variation du consensus des cartes (t + H) tombe-t-elle dans l'intervalle ?"""
    import numpy as np
    st = getattr(model, "_mc", None)
    if not st or not st.get("draws"):
        return None
    draws, tau = st["draws"], st["dates"]
    order = list(months) + list(dk)
    mlist = [months_out.get(m) for m in months] + [days_out.get(k) for k in dk]
    pdate = [dt.date.fromisoformat(mo["date"]).toordinal() if mo else None for mo in mlist]
    mck = sorted(draws, key=lambda k: tau[k])

    def frame_at(i, days):  # copie de frameAt (site/app.js) : dernier instantané daté ≤ date − (jours − 3), lui exclu
        t, best, bt = pdate[i] - (days - 3), -1, None
        for j, d in enumerate(pdate):
            if d is not None and d <= t and (bt is None or d > bt) and j != i:
                best, bt = j, d
        return best

    def mc_at(d, excl=None):
        c = [k for k in mck if tau[k] <= d and k != excl]
        return c[-1] if c else None

    def q(e):
        e = np.sort(e)
        n = len(e)
        return float(e[int(0.025 * (n - 1))]), float(e[int(round(0.975 * (n - 1)))])
    # étalonnage walk-forward (conformal) de la largeur : score = |vérité − centre| / demi-largeur sur les couples mensuels
    # (fm[t], fm[t − k]), vérité = variation du consensus des cartes à t + 1 (retard propre des cartes) ; à la date d, facteur
    # = quantile 95 % (rang ⌈(n + 1)·0,95⌉) des 24 derniers scores dont la vérité est connue à d (date de fm[t + 1] ≤ d), dès 12 scores ;
    # avant : facteur 1 (Monte Carlo brut). Les tirages appariés donnent la forme et la variation d'un instantané à
    # l'autre ; l'étalonnage corrige l'échelle (le Monte Carlo ne voit que l'erreur de position des cartes).
    if os.environ.get("MC_DUMP"):  # diagnostic local : tirages et vérités pour étudier l'étalonnage hors du build
        import pickle
        pickle.dump({"draws": draws, "tau": tau, "cons": cons_area, "fm": fm,
                     "a": {m: {v: months_out[m]["a"][v]["tout"][0] for v in VIEWS} for m in fm if months_out.get(m)},
                     "mc": {m: months_out[m]["mc"]["tout"] for m in fm if months_out.get(m)}}, open(os.environ["MC_DUMP"], "wb"))
    H_CAL, CAL_WINDOW = 1, 24
    scores = {lab: [] for lab, _ in CHANGE_LAGS}
    kofl = {"m1": 1, "m12": 12}
    for lab in scores:
        k = kofl[lab]
        for t in range(k, len(fm) - H_CAL):
            a, b = fm[t], fm[t - k]
            if a not in draws or b not in draws or fm[t + H_CAL] not in cons_area or fm[t - k + H_CAL] not in cons_area:
                continue
            lo, hi = q(draws[a]["equilibre"] - draws[b]["equilibre"])
            d = months_out[a]["a"]["equilibre"]["tout"][0] - months_out[b]["a"]["equilibre"]["tout"][0]
            truth = cons_area[fm[t + H_CAL]] - cons_area[fm[t - k + H_CAL]]
            scores[lab].append((tau[fm[t + H_CAL]], tau[a], abs(truth - (d + (lo + hi) / 2)) / max(1.0, (hi - lo) / 2), truth, d, lo, hi))

    def scale(lab, day):
        # fenêtre glissante des CAL_WINDOW derniers scores : l'expanding gardait les mois de 2022 (cartes en retard de
        # 10 000 à 30 000 km²) et triplait la largeur pour toujours (facteur 3,2 à 30 jours, 4,0 à 12 mois)
        past = sorted(x[2] for x in sorted(x for x in scores[lab] if x[0] <= day)[-CAL_WINDOW:])
        n = len(past)
        if n < 12:
            return 1.0
        return past[min(n - 1, math.ceil((n + 1) * 0.95) - 1)]

    def widen(lo, hi, s):
        c, h = (lo + hi) / 2, (hi - lo) / 2
        return c - s * h, c + s * h
    n_rep = 0
    for i, mo in enumerate(mlist):
        if not mo:
            continue
        dm, dv = {}, {}
        for lab, days in CHANGE_LAGS:
            j = frame_at(i, days)
            if j < 0:
                continue
            kD, kR = order[i], order[j]
            if kD not in draws or kR not in draws:
                kD = mc_at(pdate[i])
                kR = mc_at(tau[kD] - (days - 3), excl=kD) if kD else None
                if not kR:
                    continue
                n_rep += 1
            s = scale(lab, pdate[i])
            for v in VIEWS:
                lo, hi = widen(*q(draws[kD][v] - draws[kR][v]), s)
                d = mo["a"][v]["tout"][0] - mlist[j]["a"][v]["tout"][0]
                iv = [round(d + lo), round(d + hi)]
                if v == "equilibre":
                    dm[lab] = iv
                else:
                    dv.setdefault(v, {})[lab] = iv
        if dm:
            mo["dmc"] = dm
        if dv:
            mo["dmcV"] = dv
    # contrôle : instantanés mensuels, vérité = consensus des cartes à t + H (H = 1 : retard propre des cartes)
    chk = {}
    for H in (0, 1):
        for lab, k in (("m1", 1), ("m12", 12)):
            n = hit = hit_i = z_n = z_hit = 0
            n_c = hit_c = z_hit_c = z_n_c = 0
            wid, wid_i, wst, err, wid_c = [], [], [], [], []
            for t in range(k, len(fm) - H):
                a, b = fm[t], fm[t - k]
                if a not in draws or b not in draws or fm[t + H] not in cons_area or fm[t - k + H] not in cons_area:
                    continue
                lo, hi = q(draws[a]["equilibre"] - draws[b]["equilibre"])
                # mêmes marges, tirages NON appariés (tirage d contre tirage d − 1, indépendants) : comparaison
                li, hi_i = q(draws[a]["equilibre"] - np.roll(draws[b]["equilibre"], 1))
                d = months_out[a]["a"]["equilibre"]["tout"][0] - months_out[b]["a"]["equilibre"]["tout"][0]
                truth = cons_area[fm[t + H]] - cons_area[fm[t - k + H]]
                n += 1
                hit += d + lo <= truth <= d + hi
                hit_i += d + li <= truth <= d + hi_i
                wid.append(hi - lo)
                wid_i.append(hi_i - li)
                wst.append(months_out[a]["mc"]["tout"][1] - months_out[a]["mc"]["tout"][0])
                err.append(abs(d - truth))
                quiet = abs(truth) < 100 * k  # « rien ne bouge » : moins de 100 km² par mois selon le consensus
                if quiet:
                    z_n += 1
                    z_hit += d + lo <= 0 <= d + hi
                # intervalle publié (étalonné sur le passé seulement, ≥ 12 scores connus à la date de l'instantané)
                if sum(1 for x in scores[lab] if x[0] <= tau[a]) >= 12:
                    lc, hc = widen(lo, hi, scale(lab, tau[a]))
                    n_c += 1
                    hit_c += d + lc <= truth <= d + hc
                    wid_c.append(hc - lc)
                    if quiet:
                        z_n_c += 1
                        z_hit_c += d + lc <= 0 <= d + hc
            if n:
                chk[f"H{H}_{lab}"] = {"n": n, "cover": round(hit / n, 3), "coverIndep": round(hit_i / n, 3),
                                      "widthMedian": round(float(np.median(wid))), "widthIndepMedian": round(float(np.median(wid_i))),
                                      "stockWidthMedian": round(float(np.median(wst))), "absErrMedian": round(float(np.median(err))),
                                      "zeroIn": [z_hit, z_n], "calibrated": {"n": n_c, "cover": round(hit_c / n_c, 3) if n_c else None,
                                      "widthMedian": round(float(np.median(wid_c))) if wid_c else None, "zeroIn": [z_hit_c, z_n_c]}}
    out = {"lags": dict(CHANGE_LAGS), "w": st["w"], "phi": st["phi"], "rho": (getattr(model, "mc_persist", None) or {}).get("rho", {}),
           "draws": MC_DRAWS, "nMC": st["n"], "timeS": round(st["time"], 1), "old": st.get("old"), "replicated": n_rep, "check": chk,
           "calib": {"truth": "consensus des cartes à t + 1", "minScores": 12, "window": CAL_WINDOW, "factor": None}}
    print(f"intervalle du changement (tirages appariés, w {st['w']:.2f}, φ {st['phi']:.2f}) : {st['n']} Monte Carlo en "
          f"{st['time']:.0f} s ; {n_rep} couples reportés (pas récents)", flush=True)
    for kk, c in chk.items():
        print(f"  contrôle {kk} : couverture {c['cover']:.0%} (non apparié {c['coverIndep']:.0%}), n {c['n']}, largeur médiane "
              f"{c['widthMedian']:,} km² (non apparié {c['widthIndepMedian']:,} ; stock {c['stockWidthMedian']:,}), écart médian au "
              f"consensus {c['absErrMedian']:,} km², 0 dans l'intervalle quand le consensus ne bouge pas : {c['zeroIn'][0]}/{c['zeroIn'][1]}", flush=True)
        cc = c["calibrated"]
        if cc["n"]:
            print(f"    étalonné (walk-forward) : couverture {cc['cover']:.0%} sur {cc['n']}, largeur médiane {cc['widthMedian']:,} km², "
                  f"0 dedans au calme {cc['zeroIn'][0]}/{cc['zeroIn'][1]}", flush=True)
    last_day = max(pdate[i] for i, mo in enumerate(mlist) if mo)
    fac = {lab: round(scale(lab, last_day), 3) for lab in scores}
    out["calib"]["factor"] = fac
    print(f"  facteurs d'étalonnage actuels : {fac} ({ {lab: len(v) for lab, v in scores.items()} } scores)", flush=True)
    last = [mo for mo in mlist if mo and mo.get("dmc")]
    if last:
        lm = last[-1]
        print(f"  {lm['date']} : " + " ; ".join(f"{lab} {lm['dmc'][lab][0]:,} – {lm['dmc'][lab][1]:,} km²" for lab in lm["dmc"]), flush=True)
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
    # historique (HIST) : chaque jour (ou les 10 et 20) jusqu'à la date limite, hors fins de mois (instantanés mensuels) ;
    # clés internes « H » + date (une date peut aussi être un pas récent de la chaîne : deux instantanés distincts)
    hist = {}
    if HIST in ("jours", "decades"):
        for m in months:
            if HIST_FROM and m < HIST_FROM:
                continue
            y, mo = int(m[:4]), int(m[5:])
            end = (dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)).day
            for dd in (range(1, end) if HIST == "jours" else (10, 20)):
                x = dt.date(y, mo, dd)
                if x <= limit:
                    hist["H" + x.isoformat()] = x
    raw_c = None
    if hist:
        raw_c = {d.strftime("%Y%m%d"): None for d in list(hist.values()) + list(snap.values()) + list(daily.values())}
    status, last_viina = read_status(list(snap.values()) + list(daily.values()), extra=raw_c)
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
    # historique : mois de référence = dernier mois écoulé, qui doit être un instantané tiré ; statut VIINA du jour requis
    hist_ref = {}
    for k, d in sorted(hist.items()):
        M = (d.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
        if d.strftime("%Y%m%d") in raw_c and M in snap:
            hist_ref[k] = M
    hist = {k: hist[k] for k in hist_ref}
    if HIST:
        print(f"historique ({HIST}) : {len(hist)} instantanés en branche ({min(hist.values()) if hist else '-'} → "
              f"{max(hist.values()) if hist else '-'}) ; figé avant {limit - dt.timedelta(days=HIST_FREEZE)} ; cache brut "
              f"{HIST_CACHE or 'aucun'}", flush=True)
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
    # barrières dures (19/10/2026) : ni le pavage ni les statuts VIINA ne passent d'une rive à l'autre (barrier_tiling.py)
    cell, bar = barrier_tiling.apply(cell, places, laea, X0, Y1, SIZE, NX, NY, ROOT, status=status)
    dsm_fixed, dsm_events = apply_dsm_log(status, places)
    hist_status = None
    if hist:
        hist_status = HistStatus(raw_c, read_status.gidx, status, {k: snap[hist_ref[k]].strftime("%Y%m%d") for k in hist},
                                 {k: d.strftime("%Y%m%d") for k, d in hist.items()}, dsm_events, places, ended=ended)
        raw_c = None
    official = load_official_claims(pid_index)
    events, ev_rate = control_events(status, places, official, dsm_events)
    model = Model(months, dict(snap, **daily, **hist), status, claims, official, cell, places)
    model.dec_ref = hist_ref
    model.bar = bar
    model.last_key = frames[-1]
    model.last_month = fm[-1]
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
    if WMULT_ON:  # poids dynamique par carte × secteur × mois, appris sur la vérité retardée (source_consensus.py, § 6)
        model.wmult = source_consensus.compute(model, fm, NX, NY, CELL_KM, ended=ended, **WMULT_ARGS)
        source_consensus.check_no_future(model, fm, model.wmult, NX, NY, CELL_KM, ended=ended, **WMULT_ARGS)
    v_km, speeds = estimate_speed(model, fm)
    print(f"vitesse typique du front : {v_km:.2f} km/mois", flush=True)
    sample = fm[::4] if not FAST else fm[::12]
    sig0, mae, calib, slope = calibrate(model, sample, P)
    kstrength, kdetail = calibrate_claims(model, fm, P)

    # spécification de champ (camps, sources retirées, changements de P) pour chaque variante « sans X »
    def variant_field(spec):
        ch = {}
        if spec.get("presence") is False:
            ch["presence"] = False
        if spec.get("cues") is False:
            ch["cues"] = False
        return (CAMPS, tuple(spec.get("drop", ())), ch or None)

    # lots parallèles (FRONT_VIEWS) : « vues » = pipeline complet, variantes et sensibilité détachées (voir docs/ETAT.md § 8)
    allowed = {"vues", "sens"} | set(VARIANTS)
    bad = [x for x in FRONT_LOTS if x not in allowed]
    if bad:
        print(f"build_front.py : lot(s) inconnu(s) dans FRONT_VIEWS : {', '.join(bad)} ; attendus : {', '.join(sorted(allowed))}")
        sys.exit(2)
    do_vues = (not FRONT_LOTS) or ("vues" in FRONT_LOTS)
    do_sens = (not FRONT_LOTS) or ("sens" in FRONT_LOTS)
    want_variants = [v for v in VARIANTS if v in FRONT_LOTS]
    if want_variants and do_vues:
        print(f"build_front.py : lot « vues » demandé avec des variantes {want_variants} ; les variantes sont des lots séparés, ignorées ici")
        want_variants = []

    # ---------- lots détachés : variantes « sans X » et/ou sensibilité, sans le pipeline des vues ----------
    if FRONT_LOTS and not do_vues:
        order = list(months) + sorted(daily)

        def fdate_lot(m):
            return snap.get(m) or daily[m]
        if want_variants:
            vfields = {name: variant_field(VARIANTS[name]) for name in want_variants}
            qv = {name: {} for name in want_variants}
            sv = {name: {} for name in want_variants}

            def on_variant(m, inp, ps, mcres):
                for f, p in ps.items():
                    met = model.metrics(p, crimea, pre)
                    s = {k: met[k] for k in ("tout", "depuis2022")}
                    s["grey_width_km"] = met["grey_width_km"]
                    sv[f][fdate_lot(m).isoformat()] = s
                    qv[f][m] = bytes(min(63, int(x * 63 + 0.5)) for x in p)
            model.run(P, sig0, v_km, layers, kstrength, frames, vfields, on_month=on_variant, mc=False, side=None)
            os.makedirs(PARTS, exist_ok=True)
            for name in want_variants:
                arr, prev = [], None
                for key in order:
                    q = qv[name].get(key)
                    arr.append(None if q is None else encode_field(q, prev))
                    prev = q if q is not None else prev
                fjs = f"(window.FRONTP=window.FRONTP||{{}})[{json.dumps(name)}]=" + json.dumps(arr, separators=(",", ":")) + ";"
                open(os.path.join(ROOT, "site", f"front_{name}.js"), "w").write(fjs)
                json.dump({"v": sv[name]}, open(os.path.join(PARTS, f"front_{name}.json"), "w", encoding="utf-8"),
                          ensure_ascii=False, separators=(",", ":"))
                write_manifest(name, [f"site/front_{name}.js", f"parts/front_{name}.json"])
                print(f"lot {name} : front_{name}.js + parts/front_{name}.json ({len(sv[name])} instantanés)", flush=True)
        if do_sens:
            sens = compute_sensitivity(model, P, sig0, v_km, layers, kstrength, fm, crimea, pre)
            os.makedirs(PARTS, exist_ok=True)
            json.dump(sens, open(os.path.join(PARTS, "front_sens.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, separators=(",", ":"))
            with open(os.path.join(ROOT, "data", "front_sensibilite.csv"), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["variante"] + [f"{v}_{p}_km2" for v in ("equilibre", "ua", "ru", "tiers") for p in ("tout", "depuis2022")] + ["zone_grise_km2"])
                w.writerows(sens)
            write_manifest("sens", ["data/front_sensibilite.csv", "parts/front_sens.json"])
            print(f"lot sens : front_sensibilite.csv + parts/front_sens.json ({len(sens)} variantes)", flush=True)
        return

    fields = {v: (camps, (), None) for v, camps in VIEWS.items()}
    if not FRONT_LOTS:   # passage unique : les variantes sont dans le même run (comportement historique, octet pour octet)
        for name, spec in VARIANTS.items():
            fields[name] = variant_field(spec)

    U = model.U
    months_out = {m: None for m in months}
    days_out = {}
    allf = list(fields) + ["nowcast", "combat", "contact", "zf", "zu", "zw", "zm", "destr", "asym", "pmove"]
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
    cons_area = {}

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
            # surface occupée selon le consensus (égalité : moitié), pour le contrôle de l'intervalle du changement
            cval = [1.0 if c == 1 else 0.5 if (c == -1 and any(x in (1, 2) for x in b[:3])) else 0.0 for c, b in zip(cons, inp["base"])]
            cons_area[m] = sum(cval[cell[k]] for k in U if cell[k] >= 0) * CELL_KM ** 2
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

    import time as _time
    side, pub_hist = None, {}
    if hist:
        froz = {k for k, d in hist.items() if d <= limit - dt.timedelta(days=HIST_FREEZE)}
        t0h = _time.time()
        hasher = HistHash(model, hist_status, months, layers, P, sig0, v_km, kstrength)
        hashes = {k: hasher(k) for k in hist}
        pub_all = {"H" + k: v for k, v in read_published_hist().items() if "H" + k in froz}
        pub_hist = {k: v for k, v in pub_all.items() if v.get("h") == hashes[k] or (HIST_ADOPT and v.get("h") is None)}
        for k, v in pub_hist.items():
            v["h"] = hashes[k]
        stale_pub = {k: v for k, v in pub_all.items() if k not in pub_hist}
        side = HistSide(model, hist, hist_ref, froz, set(pub_hist), hist_status, hashes, set(stale_pub))
        side.stale_meta = stale_pub
        print(f"historique : {len(froz)} jours figés ; {len(pub_hist)} repris des fichiers publiés (empreinte inchangée), "
              f"{len(stale_pub)} publiés d'empreinte changée (à recalculer, du plus ancien) ; empreintes en "
              f"{_time.time() - t0h:.0f} s", flush=True)
    print("modèle principal et variantes…", flush=True)
    t_run = _time.time()
    model.run(P, sig0, v_km, layers, kstrength, frames, fields, on_month=on_month, mc=True, side=side, parallel=True)
    if side is not None:
        print(f"historique : {side.n_new} calculés ({side.t_cmp:.0f} s), {side.n_cache} relus du cache brut, {len(pub_hist)} "
              f"repris des fichiers publiés, {side.n_redo} figés à recalculer (empreinte changée) dont {side.n_stale} gardés "
              f"périmés et {side.n_skip} sautés (budget) ; chaîne complète {_time.time() - t_run:.0f} s", flush=True)

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
            # champ publié dès z ≥ 1 (le seuil d'affichage se règle dans app.js, COMBAT_Z) ; intense_km2 garde le seuil z ≥ 2
            warm = (top2 >= 1.0) & (np.abs(sdl) <= 30)
            qstore["combat"][m] = bytes(np.where(warm, np.clip(np.round(top2 * 10), 10, 63), 0).astype(np.uint8).tolist())
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
    sect.kab()
    sect.ookla()
    # champ des CARTES (équilibré, avant la bande locale) : mouvement récent de P(contact)
    sect.PEQ = {t: np.frombuffer(qstore["equilibre"][months[t]], np.uint8).astype(np.float32) / 63 for t in tsm if months[t] in qstore["equilibre"]}
    print(f"liste officielle rejouée : {int((sect.LIST > 0).sum()):,} mailles-mois en hostilités actives (connues à la date)", flush=True)
    print("faits officiels : " + ", ".join(f"{k} {int((v > 0).sum()):,} mailles-mois" for k, v in sect.OF.items()), flush=True)
    print(f"radar : abandon agricole renseigné sur {int(np.isfinite(sect.AG).sum()):,} mailles-mois", flush=True)
    print(f"alertes KAB : {int((sect.KAB > 0).sum()):,} mailles-mois ; Ookla : {int((sect.OOK != 0).any(1).sum())} mois renseignés", flush=True)
    PC, PCwf = sect.pcontact(tsm, SDm, sect.G, urb, Zf_store, truth, dis_cell, ART)
    print(f"P(contact au sol) : AUC walk-forward {sect.pc_auc:.3f}, à distance égale {sect.pc_auc_eq:.3f} ; coefficients {sect.pc_coef}" if sect.pc_auc else "P(contact au sol) : pas de validation", flush=True)
    pc_before = None
    if sect.pc_auc:   # avant / après les ajouts du 19/10/2026 (mouvement récent, bâti radar, KAB), mêmes mois et mailles
        a_, e_ = sect.pcontact(tsm, SDm, sect.G, urb, Zf_store, truth, dis_cell, ART, drop=sect.PC_NEW)
        pc_before = {"auc": round(a_, 4), "aucEq": round(e_, 4), "without": list(sect.PC_NEW)}
        print(f"  sans {', '.join(sect.PC_NEW)} : AUC {a_:.4f}, à distance égale {e_:.4f}", flush=True)
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
    # « secteurs susceptibles de bouger » : P(la ligne bouge d'au moins 0,5 km d'ici t+2) par tronçon de 24 km, sans sens,
    # walk-forward (sectors.pmove) ; publiée en liseré sur les mailles à ≤ 3 km de la ligne, q = 1 + P × 62 (0 = rien) ;
    # les instantanés intermédiaires reprennent le dernier mois calculé
    # distances à la ligne des CARTES (champ équilibré, pas le nowcast) : la cible est le mouvement du consensus des cartes
    SDe = {t: sect.signed_dist(("e", t), np.frombuffer(qstore["equilibre"][months[t]], np.uint8).astype(np.float32) / 63)[0] for t in tsm if months[t] in qstore["equilibre"]}
    tsp = [t for t in tsm if t in SDe]
    # étapes du 19/10/2026 (même code, mêmes tronçons-mois évalués) : modèle d'avant, apprentissage sans février-avril
    # 2022, + liste officielle rejouée, + Ookla (modèle publié)
    pm_steps = []
    for lab, kw in (("avant (apprentissage dès février 2022, sans liste ni Ookla)", dict(train_from="", use_list=False, use_ookla=False)),
                    ("apprentissage sans février-avril 2022", dict(use_list=False, use_ookla=False)),
                    ("+ liste officielle rejouée", dict(use_ookla=False))):
        _, a_, _, n_ = sect.pmove(tsp, SDe, truth, P_now, ind, **kw)
        pm_steps.append([lab, round(a_, 4) if a_ else None, n_])
    PMV, pm_auc, pm_base, pm_n = sect.pmove(tsp, SDe, truth, P_now, ind)
    pm_steps.append(["+ Ookla (publié)", round(pm_auc, 4) if pm_auc else None, pm_n])
    print("secteurs susceptibles de bouger, étapes : " + " ; ".join(f"{a} {b} (n={c})" for a, b, c in pm_steps), flush=True)
    print(f"secteurs susceptibles de bouger : AUC walk-forward {pm_auc:.3f} (2023+, n={pm_n}, taux de base {pm_base:.0%})" if pm_auc else "secteurs susceptibles de bouger : pas de validation", flush=True)
    pm_q, pm_t = {}, {}
    for t, ptile in PMV.items():
        pt = np.array([ptile.get(int(k), -1.0) for k in sect.pm_tile], np.float32)
        q = np.where((pt >= 0) & (np.abs(SDm[t]) <= 3), 1 + np.round(np.clip(pt, 0, 1) * 62), 0)
        pm_q[months[t]] = bytes(q.astype(np.uint8).tolist())
        # par tronçon : centre (colonne, ligne de la grille) et P en %, pour le survol
        pm_t[months[t]] = [[int((k % 1000) * 12 + 6), int((k // 1000) * 12 + 6), round(p * 100)] for k, p in sorted(ptile.items())]
    for fk in frames:
        cand_m = [m for m in pm_q if m <= fk[:7]]
        if cand_m:
            qstore["pmove"][fk] = pm_q[max(cand_m)]
            (months_out[fk] if len(fk) == 7 else days_out[fk])["pm"] = pm_t[max(cand_m)]
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
    # modulation de la bande par P(mouvement) du tronçon (conformal.py, `act`) : testée dans ce build le 19/10/2026 et NON
    # RETENUE (largeur 22,7 → 23,3 km, P10 +0,6 pt = bruit du placebo ; le −12 % du banc venait de tuiles codées 0, une
    # échelle commune à tout le mois, voir docs/ETAT.md § 6). Pour la réactiver : act = standardize_activity(PMV …).
    act_lb = None
    local_band, lbs = {}, {}
    for f in list(VIEWS) + ["nowcast"]:
        Pf = {months.index(m): np.frombuffer(qstore[f][m], np.uint8).astype(np.float32) / 63 for m in frames if len(m) == 7 and m in qstore[f]}
        lb = LocalBand(sect, months, truth_lb, TS=TS_LB, name=f, act=act_lb).fit(Pf, strata_lb)
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
            if r.get("modulation"):
                md = r["modulation"]
                print(f"  modulation par P(mouvement) ({f}) : sans {md['before']} → avec {md['after']} ; γ ≠ 0 {md['shareNonZero']}, "
                      f"γ moyen {md['meanGamma']}, dernier {md['gammaLast']}", flush=True)
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
        lbs[f] = lb   # gardée pour l'historique (HIST)
        sect._sd = {k: v for k, v in sect._sd.items() if k[0] not in ("lb", "lbr")}
    # historique (HIST) : passage en flux, jour par jour, fichiers écrits année par année
    hist_meta = None
    if side is not None:
        hist_meta = write_hist(side, hist, hist_ref, pub_hist, months, fm, snap, months_out, codes_store, ncast, ind, lbs,
                               model, crimea, pre, today, limit)
    lastm_lb = months_out[fm[-1]]
    print(f"zone grise après bande locale ({fm[-1]}) : {lastm_lb['grey']['km2']:,} km², {lastm_lb['grey']['width_km']} km de large ; par strate {lastm_lb['grey'].get('strata')}", flush=True)
    dmc_meta = change_intervals(model, months, dk, months_out, days_out, fm, cons_area)

    # sensibilité : calculée ici dans le passage unique ; dans un lot « vues » parallèle elle est détachée (lot « sens »)
    sens = compute_sensitivity(model, P, sig0, v_km, layers, kstrength, fm, crimea, pre) if do_sens else []

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
    # journal des annonces officielles (couche cliquable) : par source et par localité, [date, délai de confirmation par une
    # autre carte en jours (−1 : jamais confirmée sous 90 jours ; null : non jugeable), 1 si mention ancienne écartée] ;
    # dates jusqu'au dernier instantané publié (J-7)
    claim_log = {}
    conf_ = getattr(control_events, "confirm", {})
    stl_ = getattr(control_events, "stale", set())
    last_day = max(fdate(k) for k in frames).strftime("%Y%m%d")
    for src in ("modru", "gsua"):
        lg = {}
        for k, dates in official.get(src, {}).items():
            g = None if isinstance(k, tuple) else places[k]["id"]
            kk = f"x{k[1]}_{k[2]}" if isinstance(k, tuple) else str(k)
            ds = sorted(d for d in dates if d <= last_day)
            if not ds:
                continue
            if kk not in claim_places:
                claim_places[kk] = [k[1], k[2], "Russie"] if isinstance(k, tuple) else [round(places[k]["lon"], 4), round(places[k]["lat"], 4), places[k]["name"]]
            lg[kk] = [[d, conf_.get((src, g, d)), 1 if (src, g, d) in stl_ else 0] for d in ds]
        claim_log[src] = lg
    meta = {
        "generated": today.isoformat(), "viinaLast": last_viina, "limit": limit.isoformat(),
        "grid": {"nx": NX, "ny": NY, "size": SIZE, "x0": X0, "y1": Y1, "lon0": LON0, "lat0": LAT0, "R": R},
        "viina": VIINA, "poly": POLY, "point": POINT, "camps": CAMP, "weights": W, "views": {k: list(v) for k, v in VIEWS.items()},
        "variants": {k: {"drop": sorted(v.get("drop", ())), "presence": v.get("presence", True), "cues": v.get("cues", True)} for k, v in VARIANTS.items()},
        "params": P, "v": round(v_km, 2), "speeds": [round(x, 2) for x in speeds],
        "sigma0": {s: round(v, 2) for s, v in sig0.items()}, "mae": mae, "calibration": calib, "sensitivity": sens,
        "claimStrength": {s: round(v, 2) for s, v in kstrength.items()}, "claimReliability": kdetail, "claimStats": cstats,
        "claimPlaces": claim_places,
        "claimLog": claim_log,
        "mcDraws": MC_DRAWS, "calibSlope": round(slope, 2), "calibMonths": len(sample), "dmc": dmc_meta,
        "runs": base64.b64encode(bytes(runs)).decode(), "flags": base64.b64encode(flags).decode(), "nPlaces": len(places),
        "events": {"sources": EV_SOURCES, "reliability": ev_rate, "windowDays": 30, "table": getattr(control_events, "table", {})},
        "contact": {"method": "P(contact au sol), régression logistique walk-forward", "auc": sect.pc_auc, "coef": sect.pc_coef,
                    "features": sect.PC_NAMES, "aucEq": sect.pc_auc_eq, "contrib": pc_contrib, "before": pc_before, "artillery": {"source": "canal officiel des alertes « Повітряна тривога » (t.me/air_alert_ua), menaces d'artillerie agrégées par hromada (limites OCHA COD-AB, CC BY-IGO) puis par maille", "gain": "AUC à distance égale 0,681 → 0,731 en walk-forward (deux étapes : une alerte relève P, son absence ne le change pas)", "stopped": "2026-09-07"}, "bins": list(range(-58, 62, 4)),
                    "profile": [round(float(x), 4) for x in np.mean(np.array(contact_prof), axis=0)] if contact_prof else []},
        "localSigma": getattr(model, "local", None) or getattr(model, "local_report", None),
        "localBand": local_band,
        "asym": {"bilan": asym_bilan, "thr": 0.2, "tile_km": 24},
        "pmove": {"auc": round(pm_auc, 3) if pm_auc else None, "base": round(pm_base, 3) if pm_base else None, "n": pm_n, "tile_km": 24, "thr_km": 0.5,
                  "horizon_months": 2, "encoding": "q = 1 + P × 62 sur les mailles à ≤ 3 km de la ligne, 0 ailleurs",
                  "features": getattr(sect, "pm_names", []), "trainFrom": "2022-05", "steps": pm_steps,
                  "ookla": "Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps (https://github.com/teamookla/ookla-open-data), CC BY-NC-SA 4.0 ; agrégats par bloc de 10 km × trimestre",
                  "missingInCI": ["Telegram (mentions de combat, dossier de recherche)"]},
        "zscores": {"refMonths": 12, "names": ["feux", "UCDP", "pertes", "mouvement"], "encoding": "q = (z + 2) × 5"},
        "nowcast": dict(rep, names=["feux", "UCDP", "pertes"], widthKm=30, r0=0.03, grid=[0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]),
        "nMonths": len(months), "steps": dk, "stepDays": STEP_DAYS, "windowDays": WINDOW_DAYS,
        "months": mlist,
    }
    if hist_meta:
        meta["hist"] = hist_meta
    # valeurs numpy (σ local, zone de contact, z) converties en types natifs. Dans un lot « vues » parallèle, les surfaces
    # des variantes (mo["v"]) et la sensibilité sont vides ici : front_parts.py les réinsère par date et rappelle meta_js.
    js = meta_js(meta)
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
    if do_sens:   # dans un lot « vues » pur, la sensibilité est le lot « sens » (CSV + parts/front_sens.json détachés)
        with open(os.path.join(ROOT, "data", "front_sensibilite.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["variante"] + [f"{v}_{p}_km2" for v in ("equilibre", "ua", "ru", "tiers") for p in ("tout", "depuis2022")] + ["zone_grise_km2"])
            w.writerows(sens)
    if not FRONT_LOTS:
        # passage unique : empreinte anti-cache de index.html ici (stamp.py la referait ; gardée pour l'identité octet)
        page = os.path.join(ROOT, "site", "index.html")
        s = open(page, encoding="utf-8").read()
        s2 = re.sub(r'src="front\.js(\?v=[\w-]+)?"', f'src="front.js?v={hashlib.sha1(js.encode()).hexdigest()[:10]}"', s)
        s2 = re.sub(r'src="front_equilibre\.js(\?v=[\w-]+)?"', f'src="front_equilibre.js?v={digests["equilibre"]}"', s2)
        s2 = re.sub(r'src="front_nowcast\.js(\?v=[\w-]+)?"', f'src="front_nowcast.js?v={digests["nowcast"]}"', s2)
        s2 = re.sub(r'src="front_contact\.js(\?v=[\w-]+)?"', f'src="front_contact.js?v={digests["contact"]}"', s2)
        s2 = re.sub(r'const FRONT_V = \{[^}]*\}', "const FRONT_V = " + json.dumps(digests), s2)
        if s2 != s:
            open(page, "w", encoding="utf-8").write(s2)
    else:
        # lot « vues » : les sorties produites, pour l'assemblage (front_parts.py). L'index et FRONT_V seront refaits par stamp.py.
        rels = ["site/front.js"] + [f"site/front_{f}.js" for f in allf] + ["site/front_mcsd.js",
                "data/front_areas.csv", "data/front_calibration.csv"]
        write_manifest("vues", rels)
        if do_sens:
            os.makedirs(PARTS, exist_ok=True)
            json.dump(sens, open(os.path.join(PARTS, "front_sens.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, separators=(",", ":"))
            write_manifest("sens", ["data/front_sensibilite.csv", "parts/front_sens.json"])
        print(f"lot vues : {len(rels)} fichiers + parts/manifest_vues.txt", flush=True)
    lastm = [mo for mo in mlist if mo][-1]
    a = lastm["a"]["equilibre"]["tout"]
    print(f"front.js : {len(js) // 1024} Ko · {lastm['date']} : {a[0]:,} km² (95 % {lastm['mc']['tout'][0]:,}–{lastm['mc']['tout'][1]:,}) · "
          f"zone grise {lastm['grey']['km2']:,} km² ({lastm['grey']['width_km']} km) · σ0 {meta['sigma0']} · noyaux {meta['claimStrength']}")
    # indicateur INTERNE de dépendance aux cartes (non publié ; suivi version par version dans docs/ETAT.md)
    if not FRONT_LOTS:   # nécessite tous les champs (variantes comprises) ; sauté dans un lot parallèle
        try:
            import map_dependence
            print("KPI principal, information hors cartes (a, à faire monter ; interne) : " + json.dumps(map_dependence.summary(map_dependence.measures(os.path.join(ROOT, "site"))), ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 (diagnostic seulement, ne bloque pas le build)
            print(f"dépendance aux cartes : non calculée ({e})")


if __name__ == "__main__":
    main()
