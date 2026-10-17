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
        p = O["ucdp"]["pts"]
        for j in range(0, len(p), 5):
            combat[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 3])))
        p = O["ws"]["pts"]  # pertes RUSSES documentées : présence russe à cet endroit ce mois-là
        for j in range(0, len(p), 5):
            presence_ru[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 4])))
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
    return combat, presence_ru, presence_ua


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
        for s, arr in signs.items():
            dd = signed_distance(arr, self.U, self.inU, P["cap_km"])
            dist[s] = [dd.get(k) for k in self.U]
        sigma = {s: sigma0[s] * P["sigma_scale"] + v_km * P["v_scale"] * state["age"][s] / 30.0 for s in dist}
        mi = self.months.index(m)
        combat, pres, pres_ua = layers
        h = density_field(combat.get(mi, []), P["h0"]) if P["alpha"] > 0 else None
        pr = density_field(pres.get(mi, []), P["h0"]) if P["gamma"] > 0 else None
        pu = density_field(pres_ua.get(mi, []), P["h0"]) if P["gamma"] > 0 else None
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
                    act[k] = 0 if not isinstance(k, tuple) else min(x for x in dates if x <= day)
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
                # démenti : toutes les cartes du même camp disent l'inverse ce mois-ci
                b = base[k]
                av = [c for c in b if c]
                if src == "modru":
                    contrary = bool(av) and max(av, key=lambda c: RANK[c]) == 1
                else:
                    contrary = (b[0] == 2) if b[0] else (bool(av) and min(av, key=lambda c: RANK[c]) == 2)
                act[k] = act[k] + 1 if contrary else 0
                if act[k] >= P["official_stale"]:
                    del act[k]
                    continue
                self.kernel(k, sgn * kstrength[src], ker[src])
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
        for s in dist:
            sg = sigma[s]
            col = dist[s]
            lv[s] = [None if col[u] is None else max(-lmax, min(lmax, col[u] / (sg * widen[u]))) for u in range(self.nU)]
        return {"day": day, "base": base, "dist": dist, "sigma": sigma, "widen": widen, "lv": lv, "ker": ker, "h": h, "pr": pr, "pu": pu,
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
                    v = max(-lmax, min(lmax, d / (inp["sigma"][s] * inp["widen"][u])))
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

    def filter(self, obs, prev, P, v_km):
        """logit(t) = (1-β)·obs + β·[logit(t-1) diffusé de v·1 mois] (convolution normalisée, fenêtre du front)."""
        if prev is None or P["beta"] <= 0:
            return obs
        lmax = P["lmax"]
        r = max(1.0, v_km * P["v_scale"] / CELL_KM)
        act = [k for u, k in enumerate(self.U) if abs(prev[k]) < lmax - 0.5 or abs(obs[u]) < lmax - 0.5]
        if not act:
            return [(1 - P["beta"]) * obs[u] + P["beta"] * prev[k] for u, k in enumerate(self.U)]
        m = int(3 * r) + 3
        js = [k // NX for k in act]
        is_ = [k % NX for k in act]
        win = (max(0, min(js) - m), min(NY, max(js) + m + 1), max(0, min(is_) - m), min(NX, max(is_) + m + 1))
        # diffusion séparée de part et d'autre de la frontière : l'a priori ukrainien ne déborde pas en Russie
        out = [0.0] * self.nU
        for mask in (self.maskUA, self.maskBand):
            num = gauss_blur_win([prev[k] * mask[k] for k in range(N)], r, win)
            den = gauss_blur_win(mask, r, win)
            for u, k in enumerate(self.U):
                if not mask[k]:
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
                L = self.filter(obs, prev[name], Pf, v_km)
                full = [0.0] * N
                for u, k in enumerate(self.U):
                    full[k] = L[u]
                prev[name] = full
                ps[name] = [sig(x) for x in L]
                if mc and name == "equilibre":
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
        lv = {s: [None if dist[s][u] is None else max(-lmax, min(lmax, dist[s][u] / (inp["sigma"][s] * widen[u])))
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
def calibrate(model, months_sample, P):
    """Validation croisée des tracés (σ0) : voir la page. Renvoie (σ0, écarts moyens, diagramme, pente)."""
    held = ["dsm", "isw", "wiki"]
    sig0 = {s: 4.0 for s in POLY}
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
    return sig0, {s: round(v, 2) for s, v in mae.items()}, [[round((i + 0.5) / 10, 2), round(o / n, 3) if n else None, n] for i, (o, n) in enumerate(bins)], slope


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
    status, last_viina = read_status(snap.values())
    for m, d in list(snap.items()):
        if d.strftime("%Y%m%d") not in status:
            print(f"  {m} : pas de données VIINA au {d} — mois ignoré")
            del snap[m]
    fm = [m for m in months if m in snap]
    print(f"{len(fm)} instantanés mensuels ({fm[0]} → {fm[-1]}) ; VIINA jusqu'au {last_viina}", flush=True)
    old = guard.published("front.js", "FRONT")
    if old:
        olds = [m for m in old["months"] if m]
        guard.check("build_front.py", [
            ("nombre d'instantanés", len(fm), len(olds), "max"),
            ("dernier instantané", snap[fm[-1]].isoformat(), olds[-1]["date"] if olds else None, "max"),
            ("localités", sum(len(v) for v in status.values()) // max(1, len(status)), old.get("nPlaces"), "count"),
        ])
    claims = read_claims()
    cell, places = rasterize(os.path.join(SRC_DIR, "gn_UA_tess.geojson"))
    cell = russian_band(cell)
    pid_index = {p["id"]: k for k, p in enumerate(places)}
    official = load_official_claims(pid_index)
    model = Model(months, snap, status, claims, official, cell, places)
    crimea = [p["adm1"] in CRIMEA_ADM1 for p in places]
    pre = []
    for b in model.base_codes(PRE_DATE):
        av = [SIGN[c] for c in b if c]
        pre.append(bool(av) and sum(av) / len(av) > 0)
    P = dict(DEFAULT)
    if "--sans-indices" in sys.argv:
        P["alpha"] = 0.0
    layers = load_layers(months)

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
    enc = {f: {} for f in fields}
    prevq = {f: None for f in fields}
    state = {"codes": {}}
    enc_sd, prev_sd = {}, {"q": None}
    claim_places = {}

    def on_month(m, inp, ps, mcres):
        mo = {"date": snap[m].isoformat(), "a": {}, "v": {}}
        for f, p in ps.items():
            met = model.metrics(p, crimea, pre, sectors=(f == "equilibre"))
            if f in VIEWS:
                mo["a"][f] = {k: met[k] for k in ("tout", "horsCrimee", "depuis2022")}
                mo["a"][f]["russie"] = met["russie_ua"]
                if f == "equilibre":
                    mo["grey"] = {"km2": met["tout"][2], "width_km": met["grey_width_km"], "front_km": met["front_km"],
                                  "sectors": met.get("sectors", {})}
            else:
                mo["v"][f] = {k: met[k] for k in ("tout", "depuis2022")}
                mo["v"][f]["grey_width_km"] = met["grey_width_km"]
            q = [min(63, int(x * 63 + 0.5)) for x in p]
            enc[f][m] = encode_field(q, prevq[f])
            prevq[f] = q
        lo, hi, sd = mcres
        # écart-type Monte Carlo de p par maille (pour les futures flèches « qui pousse où ») : 0-63 pour 0-0,5
        q = [0] * len(U)
        for u, v in sd.items():
            q[u] = min(63, int(v / 0.5 * 63 + 0.5))
        enc_sd[m] = encode_field(q, prev_sd["q"])
        prev_sd["q"] = q
        tot = mo["a"]["equilibre"]["tout"][0]
        mo["mc"] = {per: [round(lo - tot + mo["a"]["equilibre"][per][0]), round(hi - tot + mo["a"]["equilibre"][per][0])]
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
        prev_codes = state["codes"]
        diff = [[k, c] for k, c in cur.items() if prev_codes.get(k) != c] + [[k, 0] for k in prev_codes if k not in cur]
        mo["d"] = [x for kc in sorted(diff) for x in kc]
        state["codes"] = cur
        # annonces officielles actives ce mois (calque brut) : indices de localités ; nouvelles du mois à part
        day0 = (snap[m].replace(day=1)).strftime("%Y%m%d")
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
        months_out[m] = mo
        g = mo["grey"]
        print(f"  {m} : {mo['a']['equilibre']['tout'][0]:,} km² (95 % {mo['mc']['tout'][0]:,}–{mo['mc']['tout'][1]:,}) · "
              f"RU {mo['a']['ru']['tout'][0]:,} · UA {mo['a']['ua']['tout'][0]:,} · zone grise {g['km2']:,} km², "
              f"{g['width_km']} km de large · MoD actives {len(mo['modru']['actives'])}", flush=True)

    print("modèle principal et variantes…", flush=True)
    model.run(P, sig0, v_km, layers, kstrength, fm, fields, on_month=on_month, mc=True)

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
    mlist = [months_out[m] for m in months]
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
        "months": mlist,
    }
    js = "window.FRONT=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "front.js"), "w", encoding="utf-8").write(js)
    digests = {}
    for f in fields:
        arr = [enc[f].get(m) for m in months]
        fjs = f"(window.FRONTP=window.FRONTP||{{}})[{json.dumps(f)}]=" + json.dumps(arr, separators=(",", ":")) + ";"
        open(os.path.join(ROOT, "site", f"front_{f}.js"), "w").write(fjs)
        digests[f] = hashlib.sha1(fjs.encode()).hexdigest()[:10]
    fjs = "(window.FRONTP=window.FRONTP||{})[\"mcsd\"]=" + json.dumps([enc_sd.get(m) for m in months], separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "front_mcsd.js"), "w").write(fjs)
    print(f"champs : {len(fields)} fichiers front_*.js + front_mcsd.js (écart-type Monte Carlo, 0-63 pour 0-0,5)")
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
    s2 = re.sub(r'const FRONT_V = \{[^}]*\}', "const FRONT_V = " + json.dumps(digests), s2)
    if s2 != s:
        open(page, "w", encoding="utf-8").write(s2)
    lastm = [mo for mo in mlist if mo][-1]
    a = lastm["a"]["equilibre"]["tout"]
    print(f"front.js : {len(js) // 1024} Ko · {lastm['date']} : {a[0]:,} km² (95 % {lastm['mc']['tout'][0]:,}–{lastm['mc']['tout'][1]:,}) · "
          f"zone grise {lastm['grey']['km2']:,} km² ({lastm['grey']['width_km']} km) · σ0 {meta['sigma0']} · noyaux {meta['claimStrength']}")


if __name__ == "__main__":
    main()
