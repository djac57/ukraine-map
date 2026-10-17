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
6. intervalle à 90 % des km² par Monte Carlo (lignes décalées dans σ_s, sources rééchantillonnées).
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
POLY = ["dsm", "isw", "wiki", "boost", "uamin", "rumax"]
POINT = ["uaclaims", "ruclaims"]
CAMP = {"dsm": "ua", "uamin": "ua", "uaclaims": "ua", "isw": "tiers", "wiki": "tiers", "boost": "tiers",
        "rumax": "ru", "ruclaims": "ru"}
W = {"dsm": 1.0, "uamin": 0.5, "uaclaims": 1.0, "isw": 1.0, "wiki": 0.5, "boost": 0.5, "rumax": 1.0, "ruclaims": 1.0}
CAMPS = ("ua", "ru", "tiers")
VIEWS = {"equilibre": CAMPS, "ua": ("ua",), "ru": ("ru",), "tiers": ("tiers",)}
CODE = {"": 0, "UA": 1, "RU": 2, "CONTESTED": 3}
SIGN = {1: -1, 2: 1, 3: 0}            # ukrainien, russe, contesté
RANK = {1: 0, 3: 1, 2: 2}

DEFAULT = {
    "cap_km": 60.0,        # au-delà, la maille est « loin du front » : logit saturé à ±lmax
    "lmax": 12.0,          # logit borné à ±12 (p entre 6e-6 et 1 - 6e-6)
    "mc_block_km": 50.0,   # Monte Carlo : décalages tirés par blocs de 50 km (erreurs corrélées localement)
    "alpha": 1.0,          # élargissement de σ par les indices de combats (σ × (1 + α·h))
    "h0": 0.1,             # h = 1 - exp(-densité / h0), densité d'indices lissée sur ~6 km
    "beta": 0.3,           # part de l'a priori (mois précédent diffusé) dans le filtre temporel
    "claim_logit": 1.73,   # une annonce vaut p = 0,85 au centre de la localité
    "claim_days": 90,      # une annonce compte 90 jours
    "near_km": 20.0,       # … si elle porte sur une localité à moins de 20 km d'une zone tenue / contestée
    "sigma_scale": 1.0,    # tests de sensibilité
    "v_scale": 1.0,
    "proxies": True,
    "camp_balance": True,
}
LAG_DAYS = 7
PRE_DATE = "20220223"
CRIMEA_ADM1 = {"11", "20"}
RU_MEDIA = {"ria", "ntv", "kp", "ng"}
UA_MEDIA = {"24tvua", "espreso", "liga", "nv", "pravdaua", "unian", "militarnyy", "interfaxua", "forbesua"}
MC_DRAWS = 12 if FAST else 40
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


# ---------- indices de proximité du front ----------
def load_cues(months):
    def js(name, var):
        path = os.path.join(ROOT, "site", name)
        if not os.path.exists(path):
            return None
        s = open(path, encoding="utf-8").read()
        return json.loads(s[len(f"window.{var}="):].rstrip().rstrip(";"))
    D = js("data.js", "DATA")
    O = js("osint.js", "OSINT")
    midx = {m: i for i, m in enumerate(months)}
    pts = defaultdict(list)
    if D:
        for mi, cells in enumerate(D["cells"]):
            m = D["months"][mi]
            for j in range(0, len(cells), 4):
                pts[midx[m]].append(((cells[j + 1] + 0.5) * D["cell"], (cells[j] + 0.5) * D["cell"],
                                     max(0.0, cells[j + 2] - cells[j + 3]) * 0.1))
    if O:
        p = O["ws"]["pts"]
        for j in range(0, len(p), 5):
            pts[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 4])))
        p = O["ucdp"]["pts"]
        for j in range(0, len(p), 5):
            pts[p[j + 2]].append(((p[j + 1] + 0.5) * 0.1, (p[j] + 0.5) * 0.1, float(p[j + 3])))
    return pts


def cue_field(points, h0):
    if not points:
        return None
    raw = [0.0] * N
    for lon, lat, w in points:
        k = grid_index(lon, lat)
        if k >= 0:
            raw[k] += w
    dens = gauss_blur(raw, 3.0)
    return [1 - math.exp(-v / h0) if v > 1e-6 else 0.0 for v in dens]


# ---------- modèle ----------
class Model:
    def __init__(self, months, snap, status, claims, cell, places):
        self.months, self.snap, self.status, self.claims = months, snap, status, claims
        self.cell, self.places = cell, places
        self.U = [k for k in range(N) if cell[k] >= 0]
        self.inU = [c >= 0 for c in cell]
        self.maskU = [1.0 if u else 0.0 for u in self.inU]
        self.pid = [p["id"] for p in places]
        self.lat = [p["lat"] for p in places]
        self.lon = [p["lon"] for p in places]
        self.ncell = defaultdict(int)
        for c in cell:
            if c >= 0:
                self.ncell[c] += 1
        self.center = [grid_index(p["lon"], p["lat"]) for p in places]
        self.radius = [max(CELL_KM, math.sqrt(self.ncell[k] * CELL_KM ** 2 / math.pi)) for k in range(len(places))]
        self.den_cache = {}

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
        return {s: [per_place[s][c] if c >= 0 else None for c in self.cell] for s in POLY if present[s]}

    def claim_kernels(self, base, day, wstart, P):
        hot = defaultdict(list)
        for k, b in enumerate(base):
            if any(c > 1 for c in b):
                hot[(int(self.lat[k] * 4), int(self.lon[k] * 4))].append(k)

        def near(k):
            a, b = int(self.lat[k] * 4), int(self.lon[k] * 4)
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    for q in hot.get((a + da, b + db), ()):
                        dy = (self.lat[q] - self.lat[k]) * 111.2
                        dx = (self.lon[q] - self.lon[k]) * 111.2 * math.cos(math.radians(self.lat[k]))
                        if dx * dx + dy * dy <= P["near_km"] ** 2:
                            return True
            return False
        ker = {"uaclaims": {}, "ruclaims": {}}
        flags = {}
        for k, i in enumerate(self.pid):
            lst = self.claims.get(i)
            if not lst:
                continue
            side = claim_state(lst, day, wstart)
            if not side or self.center[k] < 0 or not near(k):
                continue
            flags[k] = side
            src = "ruclaims" if side == "ru" else "uaclaims"
            sgn = 1 if side == "ru" else -1
            r = self.radius[k]
            cj, ci = divmod(self.center[k], NX)
            rc = int(math.ceil(2 * r / CELL_KM))
            for dj in range(-rc, rc + 1):
                for di in range(-rc, rc + 1):
                    j2, i2 = cj + dj, ci + di
                    if not (0 <= j2 < NY and 0 <= i2 < NX):
                        continue
                    q = j2 * NX + i2
                    if not self.inU[q]:
                        continue
                    d2 = (dj * dj + di * di) * CELL_KM ** 2
                    if d2 > 4 * r * r:
                        continue
                    v = sgn * P["claim_logit"] * math.exp(-d2 / (2 * r * r))
                    if abs(v) > abs(ker[src].get(q, 0.0)):
                        ker[src][q] = v
        return ker, flags

    def pool(self, dist, sigma, ker, h, P, camps, cells=None, shift=None, pick=None, block=None):
        """logit observé {maille: L}, pool logarithmique normalisé par camp (sources absentes : ne votent pas)."""
        lmax, a = P["lmax"], P["alpha"]
        polys = [s for s in dist if (pick is None or pick.get(s)) and (not P["camp_balance"] or CAMP[s] in camps)]
        pts = [s for s in POINT if ker.get(s) and (pick is None or pick.get(s)) and (not P["camp_balance"] or CAMP[s] in camps)]
        wt = {s: W[s] * (pick[s] if pick else 1) for s in polys + pts}
        camp_of = {s: (CAMP[s] if P["camp_balance"] else "tous") for s in polys + pts}
        out = {}
        for k in (cells if cells is not None else self.U):
            hk = (1 + a * h[k]) if h else 1.0
            num = {}
            den = {}
            for s in polys:
                d = dist[s].get(k)
                if d is None:
                    continue
                if shift:
                    d += shift[s].get(block[k], 0.0)
                lv = d / (sigma[s] * hk)
                lv = lmax if lv > lmax else -lmax if lv < -lmax else lv
                c = camp_of[s]
                num[c] = num.get(c, 0.0) + wt[s] * lv
                den[c] = den.get(c, 0.0) + wt[s]
            for s in pts:
                v = ker[s].get(k)
                if v is None:
                    continue
                c = camp_of[s]
                num[c] = num.get(c, 0.0) + wt[s] * v
                den[c] = den.get(c, 0.0) + wt[s]
            if den:
                out[k] = sum(num[c] / den[c] for c in den) / len(den)
        return out

    def run(self, P, sigma0, v_km, cues, month_list, on_month=None, mc=False):
        """Déroule le modèle ; appelle on_month(m, r) avec r['views'][vue] = p (liste alignée sur U)."""
        prev = {view: None for view in VIEWS}
        prev_key, age = {}, defaultdict(float)
        prev_day = None
        blur_cells = max(0.5, v_km * P["v_scale"] / CELL_KM)
        if blur_cells not in self.den_cache:
            self.den_cache[blur_cells] = gauss_blur(self.maskU, blur_cells)
        den_b = self.den_cache[blur_cells]
        for m in month_list:
            d = self.snap[m]
            day = d.strftime("%Y%m%d")
            wstart = (d - dt.timedelta(days=P["claim_days"])).strftime("%Y%m%d")
            base = self.base_codes(day)
            signs = self.poly_signs(base, P["proxies"])
            ndays = (d - prev_day).days if prev_day else 0
            for s, arr in signs.items():
                key = hash(tuple(arr[k] for k in self.U[::5]))
                age[s] = age[s] + ndays if prev_key.get(s) == key else 0.0
                prev_key[s] = key
            prev_day = d
            dist = {s: signed_distance(arr, self.U, self.inU, P["cap_km"]) for s, arr in signs.items()}
            sigma = {s: sigma0[s] * P["sigma_scale"] + v_km * P["v_scale"] * age[s] / 30.0 for s in dist}
            ker, flags = self.claim_kernels(base, day, wstart, P) if P["proxies"] else ({}, {})
            h = cue_field(cues.get(self.months.index(m), []), P["h0"]) if P["alpha"] > 0 else None
            r = {"day": day, "sigma": sigma, "age": {s: age[s] for s in dist}, "flags": flags, "base": base, "views": {}}
            for view, camps in VIEWS.items():
                obs = self.pool(dist, sigma, ker, h, P, camps)
                L = [0.0] * N
                if prev[view] is not None and P["beta"] > 0:
                    num = gauss_blur(prev[view], blur_cells)
                    for k in self.U:
                        pr = num[k] / den_b[k] if den_b[k] > 1e-9 else 0.0
                        L[k] = (1 - P["beta"]) * obs.get(k, 0.0) + P["beta"] * pr
                else:
                    for k in self.U:
                        L[k] = obs.get(k, 0.0)
                prev[view] = L
                r["views"][view] = [sig(L[k]) for k in self.U]
                if mc and view == "equilibre":
                    r["mc"] = self.monte_carlo(dist, sigma, ker, h, P, obs, L)
            if on_month:
                on_month(m, r)
        return prev

    def monte_carlo(self, dist, sigma, ker, h, P, obs, L):
        """Intervalle à 90 % (périmètre total) : chaque ligne décalée de N(0, σ_s), sources rééchantillonnées par camp."""
        band = [k for k in self.U if -6 < obs.get(k, 0.0) < 6]
        base_area = sum(sig(L[k]) for k in self.U)
        band_area = sum(sig(L[k]) for k in band)
        rng = random.Random(42)
        by_camp = defaultdict(list)
        for s in list(dist) + [s for s in POINT if ker.get(s)]:
            by_camp[CAMP[s]].append(s)
        areas = []
        b1 = 1 - P["beta"]
        bs = max(1, int(P["mc_block_km"] / CELL_KM))
        block = {k: (k // NX // bs) * 1000 + (k % NX) // bs for k in band}
        blocks = set(block.values())
        for _ in range(MC_DRAWS):
            shift = {s: {b: rng.gauss(0, sigma[s]) for b in blocks} for s in dist}
            pick = defaultdict(int)
            for lst in by_camp.values():
                for _ in lst:
                    pick[rng.choice(lst)] += 1
            o = self.pool(dist, sigma, ker, h, P, CAMPS, cells=band, shift=shift, pick=dict(pick), block=block)
            tot = sum(sig(L[k] + b1 * (o.get(k, 0.0) - obs.get(k, 0.0))) for k in band)
            areas.append((base_area - band_area + tot) * CELL_KM ** 2)
        areas.sort()
        return [areas[int(0.05 * (len(areas) - 1))], areas[int(round(0.95 * (len(areas) - 1)))]]

    def areas(self, p, crimea, pre):
        out = {"tout": [0.0, 0.0], "horsCrimee": [0.0, 0.0], "depuis2022": [0.0, 0.0]}
        for idx, k in enumerate(self.U):
            v = p[idx]
            pl = self.cell[k]
            med = 1.0 if v >= 0.5 else 0.0
            out["tout"][0] += v
            out["tout"][1] += med
            if not crimea[pl]:
                out["horsCrimee"][0] += v
                out["horsCrimee"][1] += med
                if not pre[pl]:
                    out["depuis2022"][0] += v
                    out["depuis2022"][1] += med
        return {k: [a * CELL_KM ** 2, b * CELL_KM ** 2] for k, (a, b) in out.items()}


# ---------- calibration ----------
def calibrate(model, months_sample, P):
    """Validation croisée : chaque carte (DeepStateMap, ISW, Wikipédia) est prédite par les autres.
    σ0_s = écart moyen (km) entre sa limite et la ligne médiane des autres / (2 ln 2) — écart absolu
    moyen d'une loi logistique d'échelle σ. Le diagramme de calibration compare p prédite et part de
    mailles que la source retirée classe russe (contesté = 0,5)."""
    held = ["dsm", "isw", "wiki"]
    sig0 = {s: 4.0 for s in POLY}
    mae, bins, slope = {}, [], 1.0
    for it in range(3):
        bins = [[0.0, 0] for _ in range(10)]
        err = defaultdict(list)
        for m in months_sample:
            base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
            signs = model.poly_signs(base, proxies=False)
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
                    if sk is None:
                        continue
                    edge = sk == 0 or any(0 <= q < N and model.inU[q] and arr[q] is not None and arr[q] != sk
                                          for q in (k - 1, k + 1, k - NX, k + NX))
                    if edge and k in dmed:
                        err[s].append(min(abs(dmed[k]), P["cap_km"]))  # au-delà de cap : écart plafonné
                    if it >= 1 and k in L:
                        pv = sig(L[k])
                        if 0.02 < pv < 0.98:
                            b = min(9, int(pv * 10))
                            bins[b][0] += 1.0 if sk == 1 else 0.5 if sk == 0 else 0.0
                            bins[b][1] += 1
        if it == 2:
            break  # dernière passe : diagramme avec les σ0 recalés
        mae = {s: sum(v) / len(v) for s, v in err.items() if v}
        for s in held:
            if s in mae:
                sig0[s] = max(CELL_KM / 2, mae[s] / (2 * math.log(2)))
        if it == 1:
            # recalage : pente b de logit(observé) contre logit(prédit) ; σ0 / b ramène le diagramme sur la diagonale
            num = den = 0.0
            for i, (o, n) in enumerate(bins):
                if not n or i in (0, 9):
                    continue
                f = min(0.99, max(0.01, o / n))
                x = math.log(((i + 0.5) / 10) / (1 - (i + 0.5) / 10))
                yv = math.log(f / (1 - f))
                num += n * x * yv
                den += n * x * x
            slope = num / den if den else 1.0
            for s in held:
                sig0[s] = max(CELL_KM / 2, sig0[s] / slope)
        sig0["boost"] = sig0["wiki"]
        sig0["rumax"] = sig0["uamin"] = 1.5 * max(sig0[s] for s in held)
        print("  calibration passe %d : %s" % (it + 1, ", ".join(f"{s} σ0 = {sig0[s]:.1f} km (écart moyen {mae.get(s, 0):.1f})" for s in held)), flush=True)
    print(f"  pente de calibration avant recalage : {slope:.2f}", flush=True)
    return sig0, {s: round(v, 2) for s, v in mae.items()}, [[round((i + 0.5) / 10, 2), round(o / n, 3) if n else None, n] for i, (o, n) in enumerate(bins)], slope


def estimate_speed(model, months):
    """Vitesse typique du front (km/mois) : surface qui change de camp / longueur du front ; médiane depuis juin 2022."""
    prev, speeds = None, []
    for m in months:
        base = model.base_codes(model.snap[m].strftime("%Y%m%d"))
        occ = []
        for b in base:
            av = [SIGN[c] for c in b if c]
            occ.append(bool(av) and sum(av) / len(av) > 0)
        mask = [occ[c] if c >= 0 else None for c in model.cell]
        edge = sum(1 for k in model.U if any(0 <= q < N and mask[q] is not None and mask[q] != mask[k]
                                             for q in (k - 1, k + 1, k - NX, k + NX)))
        if prev is not None and m >= "2022-06" and edge:
            flipped = sum(1 for k in model.U if mask[k] != prev[k])
            speeds.append(flipped * CELL_KM ** 2 / (edge / 2 * CELL_KM))
        prev = mask
    s = sorted(speeds)
    return (s[len(s) // 2] if s else 1.0), speeds


# ---------- sorties ----------
def encode_field(q, prevq):
    """Champ quantifié 0-63, en différences avec le mois précédent (255 = inchangé) : paires (valeur, longueur varint)."""
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
    if old:  # vérifié avant le calcul (~15 min) pour ne pas le lancer pour rien
        olds = [m for m in old["months"] if m]
        guard.check("build_front.py", [
            ("nombre d'instantanés", len(fm), len(olds), "max"),
            ("dernier instantané", snap[fm[-1]].isoformat(), olds[-1]["date"] if olds else None, "max"),
            ("localités", sum(len(v) for v in status.values()) // max(1, len(status)),
             old.get("nPlaces"), "count"),
        ])
    claims = read_claims()
    cell, places = rasterize(os.path.join(SRC_DIR, "gn_UA_tess.geojson"))
    model = Model(months, snap, status, claims, cell, places)
    crimea = [p["adm1"] in CRIMEA_ADM1 for p in places]
    pre = []
    for b in model.base_codes(PRE_DATE):
        av = [SIGN[c] for c in b if c]
        pre.append(bool(av) and sum(av) / len(av) > 0)
    P = dict(DEFAULT)
    if "--sans-indices" in sys.argv:
        P["alpha"] = 0.0
    cues = load_cues(months)

    v_km, speeds = estimate_speed(model, fm)
    print(f"vitesse typique du front : {v_km:.2f} km/mois", flush=True)
    sig0, mae, calib, slope = calibrate(model, fm[::4] if not FAST else fm[::12], P)

    U = model.U
    months_out = {m: None for m in months}
    fields = {view: {} for view in VIEWS}
    prevq = {view: None for view in VIEWS}
    state = {"codes": {}}

    def on_month(m, r):
        mo = {"date": snap[m].isoformat(), "a": {}, "dis": {}}
        for view in VIEWS:
            p = r["views"][view]
            mo["a"][view] = {k: [round(v[0]), round(v[1])] for k, v in model.areas(p, crimea, pre).items()}
            q = [min(63, int(x * 63 + 0.5)) for x in p]
            fields[view][m] = encode_field(q, prevq[view])
            prevq[view] = q
        lo, hi = r["mc"]
        tot = mo["a"]["equilibre"]["tout"][0]
        mo["mc"] = {per: [round(lo - tot + mo["a"]["equilibre"][per][0]), round(hi - tot + mo["a"]["equilibre"][per][0])]
                    for per in ("tout", "horsCrimee", "depuis2022")}
        pu, pr_ = r["views"]["ua"], r["views"]["ru"]
        dis = {"tout": [0.0, 0.0], "horsCrimee": [0.0, 0.0], "depuis2022": [0.0, 0.0]}
        for idx, k in enumerate(U):
            dl = abs(pr_[idx] - pu[idx])
            big = 1.0 if dl > 0.5 else 0.0
            pl = cell[k]
            for per in ("tout",) + (("horsCrimee",) if not crimea[pl] else ()) + (("depuis2022",) if not crimea[pl] and not pre[pl] else ()):
                dis[per][0] += big
                dis[per][1] += dl
        mo["dis"] = {k: [round(a * CELL_KM ** 2), round(b * CELL_KM ** 2)] for k, (a, b) in dis.items()}
        mo["sigma"] = {s: round(v, 2) for s, v in r["sigma"].items()}
        mo["age"] = {s: round(v) for s, v in r["age"].items()}
        cur = {}
        for k, b in enumerate(r["base"]):
            fl = r["flags"].get(k)
            if any(c > 1 for c in b) or fl:
                cur[k] = b[0] | b[1] << 2 | b[2] << 4 | b[3] << 6 | (256 if fl == "ua" else 0) | (512 if fl == "ru" else 0)
        prev_codes = state["codes"]
        diff = [[k, c] for k, c in cur.items() if prev_codes.get(k) != c] + [[k, 0] for k in prev_codes if k not in cur]
        mo["d"] = [x for kc in sorted(diff) for x in kc]
        state["codes"] = cur
        months_out[m] = mo
        print(f"  {m} : {mo['a']['equilibre']['tout'][0]:,} km² (90 % : {mo['mc']['tout'][0]:,}–{mo['mc']['tout'][1]:,}) · "
              f"UA {mo['a']['ua']['tout'][0]:,} · RU {mo['a']['ru']['tout'][0]:,} · tiers {mo['a']['tiers']['tout'][0]:,} · "
              f"désaccord {mo['dis']['tout'][0]:,} km²", flush=True)

    print("modèle principal…", flush=True)
    model.run(P, sig0, v_km, cues, fm, on_month=on_month, mc=True)

    variants = [
        ("défaut : camps à un tiers chacun", {}),
        ("poids par source, sans équilibre des camps", {"camp_balance": False}),
        ("sans enveloppes ni annonces (cartes seules)", {"proxies": False}),
        ("sans indices de proximité (α = 0)", {"alpha": 0.0}),
        ("sans filtre temporel (β = 0)", {"beta": 0.0}),
        ("σ0 doublés", {"sigma_scale": 2.0}),
        ("vitesse du front doublée", {"v_scale": 2.0}),
    ]
    sens = []
    tail = fm[-13:] if not FAST else fm[-3:]
    for name, ch in variants:
        Pv = dict(P)
        Pv.update(ch)
        out = {}
        model.run(Pv, sig0, v_km, cues, tail, on_month=lambda m, r: out.update({m: r}) if m == tail[-1] else None)
        r = out[tail[-1]]
        row = [name]
        for view in ("equilibre", "ua", "ru", "tiers"):
            a = model.areas(r["views"][view], crimea, pre)
            row += [round(a["tout"][0]), round(a["depuis2022"][0])]
        sens.append(row)
        print(f"  sensibilité · {name} : {row[1]:,} km² (vue équilibrée)", flush=True)

    # ---------- écriture ----------
    runs, last, n = bytearray(), None, 0

    def flush(v, n):
        while n:
            k = min(n, 255)
            runs.extend((v & 0xFF, v >> 8, k))
            n -= k
    for c in cell:
        c = 65535 if c < 0 else c
        if c == last:
            n += 1
        else:
            if last is not None:
                flush(last, n)
            last, n = c, 1
    flush(last, n)
    flags = bytes((1 if crimea[k] else 0) | (2 if pre[k] else 0) for k in range(len(places)))
    mlist = [months_out[m] for m in months]
    meta = {
        "generated": today.isoformat(), "viinaLast": last_viina, "limit": limit.isoformat(),
        "grid": {"nx": NX, "ny": NY, "size": SIZE, "x0": X0, "y1": Y1, "lon0": LON0, "lat0": LAT0, "R": R},
        "viina": VIINA, "poly": POLY, "point": POINT, "camps": CAMP, "weights": W, "views": {k: list(v) for k, v in VIEWS.items()},
        "params": P, "v": round(v_km, 2), "speeds": [round(x, 2) for x in speeds],
        "sigma0": {s: round(v, 2) for s, v in sig0.items()}, "mae": mae, "calibration": calib, "sensitivity": sens,
        "mcDraws": MC_DRAWS, "calibSlope": round(slope, 2), "calibMonths": len(fm[::4] if not FAST else fm[::12]),
        "runs": base64.b64encode(bytes(runs)).decode(), "flags": base64.b64encode(flags).decode(), "nPlaces": len(places),
        "months": mlist,
    }
    js = "window.FRONT=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "front.js"), "w", encoding="utf-8").write(js)
    digests = {}
    for view in VIEWS:
        arr = [fields[view].get(m) for m in months]
        fjs = f"(window.FRONTP=window.FRONTP||{{}})[{json.dumps(view)}]=" + json.dumps(arr, separators=(",", ":")) + ";"
        open(os.path.join(ROOT, "site", f"front_{view}.js"), "w").write(fjs)
        digests[view] = hashlib.sha1(fjs.encode()).hexdigest()[:10]
        print(f"front_{view}.js : {len(fjs) // 1024} Ko")
    with open(os.path.join(ROOT, "data", "front_areas.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mois", "date", "perimetre"] + [f"{v}_{x}_km2" for v in VIEWS for x in ("esperance", "mediane")]
                   + ["mc_p05_km2", "mc_p95_km2", "desaccord_km2", "desaccord_doux_km2"])
        for mo in mlist:
            if not mo:
                continue
            for per in ("tout", "horsCrimee", "depuis2022"):
                w.writerow([mo["date"][:7], mo["date"], per] + [x for v in VIEWS for x in mo["a"][v][per]] + mo["mc"][per] + mo["dis"][per])
    with open(os.path.join(ROOT, "data", "front_calibration.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["p_predite", "part_occupee_selon_source_retiree", "n_mailles"])
        w.writerows(calib)
    with open(os.path.join(ROOT, "data", "front_sensibilite.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variante"] + [f"{v}_{p}_km2" for v in ("equilibre", "ua", "ru", "tiers") for p in ("tout", "depuis2022")])
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
    print(f"front.js : {len(js) // 1024} Ko · {lastm['date']} : {a[0]:,} km² (90 % {lastm['mc']['tout'][0]:,}–{lastm['mc']['tout'][1]:,}) · "
          f"σ0 {meta['sigma0']} · v {meta['v']} km/mois")


if __name__ == "__main__":
    main()
