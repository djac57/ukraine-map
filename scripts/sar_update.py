"""Mise à jour mensuelle des indices radar Sentinel-1 par maille de 2 km (pour GitHub Actions).

    python sar_update.py --root <dépôt> [--month AAAA-MM] [--radius 15] [--workers 8] [--out data/sar_cells.csv]

Aucun compte, aucun état lourd : tout est relu à distance à chaque exécution, puis jeté.

Données et licences :
  - Copernicus Sentinel-1 GRD (ESA), via Microsoft Planetary Computer (collection « sentinel-1-grd », jetons SAS
    anonymes). Mention obligatoire : « Contient des données Copernicus Sentinel modifiées [2021-AAAA] ».
  - ESA WorldCover 2021 (CC BY 4.0, © ESA WorldCover project 2021, contient des données Copernicus Sentinel modifiées).
  - ERA5 via l'API historique Open-Meteo (CC BY 4.0, Copernicus Climate Change Service), sans clé, usage non commercial.

Indices (mois M = dernier mois terminé depuis au moins 7 jours, ou --partial : mois en cours jusqu'à J−7) :
  built_damage_since_2021 : part des sous-pixels bâtis (50 m) dont la rétrodiffusion du mois M s'écarte de celle des
      mêmes mois calendaires de 2021 (M−1, M, M+1 ; même orbite) : t = |Δ| / (σ_2021 · sqrt(1/min(n,2) + 1/n_2021)),
      moyenne VV/VH, max des orbites, seuil 3,3 (façon PWTT). Au moins 2 passages valides dans le mois et par orbite.
  built_damage_excess : même chose moins la médiane des mailles calculées ce mois.
  agri_amp_db : abandon agricole, médiane sur les pixels de 200 m cultivés en 2021 de l'écart-type des 12 moyennes
      mensuelles (M−11 … M) ; faible = friche. Lu sur l'aperçu ×8 (≈ 80 m), plus léger.
  Bâti : aperçu ×4 (≈ 40 m, moyenne des puissances), comme l'historique.
  Passages exclus : neige ≥ 1 cm ou T2m ≤ 0 °C à l'heure du passage (ERA5).
Fenêtre : tuiles de 40 km couvrant les mailles à moins de --radius km de la ligne p = 0,5 du dernier champ publié.
Sortie : une ligne par maille de 2 km et par mois (aucune donnée plus fine n'est écrite).
"""
import argparse
import base64
import csv
import datetime as dt
import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.windows import Window, from_bounds
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
METEO = "https://archive-api.open-meteo.com/v1/archive"
PIX, K, TILE = 50.0, 40, 20          # sous-pixel 50 m, 40 sous-pixels par maille de 2 km, tuile de 20 mailles
A = 4                                # 200 m = 4 × 50 m (abandon agricole)
T_DMG, FLOOR, NT_CAP, MIN_NT, MIN_NREF = 3.3, 0.5, 2, 2, 6
SNOW_M, T_MIN = 0.01, 0.0


# ---------------------------------------------------------------- grille et champs du projet
def load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def load_frontp(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


class Grid:
    def __init__(self, root):
        F = load_js(os.path.join(root, "site", "front.js"))
        g = F["grid"]
        self.NX, self.NY, self.SIZE, self.X0, self.Y1 = g["nx"], g["ny"], g["size"], g["x0"], g["y1"]
        self.LON0, self.LAT0, self.R = g["lon0"], g["lat0"], float(g["R"])
        cp = np.zeros(self.NX * self.NY, np.int32)
        b = base64.b64decode(F["runs"]); o = 0
        for k in range(0, len(b), 3):
            v = b[k] | b[k + 1] << 8; n = b[k + 2]
            cp[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v; o += n
        self.cp = cp.reshape(self.NY, self.NX)
        self.months = load_js(os.path.join(root, "site", "data.js"))["months"]
        enc = load_frontp(os.path.join(root, "site", "front_equilibre.js"))
        ucells = np.nonzero(cp != -1)[0]
        prev = np.zeros(len(ucells), np.uint8); last = None
        for t, e in enumerate(enc):
            if not e:
                continue
            bb = base64.b64decode(e); q = np.zeros(len(ucells), np.uint8); o = i = 0
            while i < len(bb):
                v = bb[i]; i += 1; n = sh = 0
                while True:
                    c = bb[i]; i += 1; n |= (c & 127) << sh; sh += 7
                    if not c & 128:
                        break
                q[o:o + n] = prev[o:o + n] if v == 255 else v; o += n
            prev = q; last = t
        p = np.full(self.NX * self.NY, np.nan, np.float32); p[ucells] = prev / 63.0
        self.p, self.p_month = p.reshape(self.NY, self.NX), self.months[last]

    def laea(self, lon, lat):
        l, p, l0, p0 = map(np.radians, (lon, lat, self.LON0, self.LAT0))
        k = np.sqrt(2 / (1 + np.sin(p0) * np.sin(p) + np.cos(p0) * np.cos(p) * np.cos(l - l0)))
        return self.R * k * np.cos(p) * np.sin(l - l0), self.R * k * (np.cos(p0) * np.sin(p) - np.sin(p0) * np.cos(p) * np.cos(l - l0))

    def inv(self, x, y):
        rho = np.hypot(x, y); c = 2 * np.arcsin(rho / (2 * self.R))
        p0, l0 = math.radians(self.LAT0), math.radians(self.LON0)
        lat = np.arcsin(np.cos(c) * math.sin(p0) + y * np.sin(c) * math.cos(p0) / rho)
        lon = l0 + np.arctan2(x * np.sin(c), rho * math.cos(p0) * np.cos(c) - y * math.sin(p0) * np.sin(c))
        return np.degrees(lon), np.degrees(lat)

    def distance_km(self):
        dom = self.cp >= 0; ru = (self.p >= 0.5) & dom; ua = (self.p < 0.5) & dom
        line = np.zeros_like(dom)
        line[:, :-1] |= (ru[:, :-1] & ua[:, 1:]) | (ua[:, :-1] & ru[:, 1:])
        line[:, 1:] |= (ru[:, 1:] & ua[:, :-1]) | (ua[:, 1:] & ru[:, :-1])
        line[:-1, :] |= (ru[:-1, :] & ua[1:, :]) | (ua[:-1, :] & ru[1:, :])
        line[1:, :] |= (ru[1:, :] & ua[:-1, :]) | (ua[1:, :] & ru[:-1, :])
        return distance_transform_edt(~line) * self.SIZE / 1000.0


def tile_geom(gr, j0, i0):
    ncx, ncy = min(TILE, gr.NX - i0), min(TILE, gr.NY - j0)
    G = dict(j0=j0, i0=i0, ncx=ncx, ncy=ncy, W=ncx * K, H=ncy * K, tx0=gr.X0 + i0 * gr.SIZE, ty1=gr.Y1 - j0 * gr.SIZE)
    xs = np.array([G["tx0"], G["tx0"] + G["W"] * PIX] * 2); ys = np.array([G["ty1"]] * 2 + [G["ty1"] - G["H"] * PIX] * 2)
    lon, lat = gr.inv(xs, ys)
    G["bbox"] = [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
    clon, clat = gr.inv(np.array([G["tx0"] + G["W"] * PIX / 2]), np.array([G["ty1"] - G["H"] * PIX / 2]))
    G["center"] = (float(clon[0]), float(clat[0]))
    return G


# ---------------------------------------------------------------- lecture Sentinel-1
def read_pass(href, G, gr, dec, pix):
    """Somme des puissances et effectifs par pixel cible (pas pix) pour un fichier GRD lu à la décimation dec."""
    W, H = int(G["W"] * PIX / pix), int(G["H"] * PIX / pix)
    with rasterio.open(pc.sign(href)) as ds:
        gcps, _ = ds.gcps
        rows = np.array(sorted({g.row for g in gcps})); cols = np.array(sorted({g.col for g in gcps}))
        lon = np.full((len(rows), len(cols)), np.nan); lat = lon.copy()
        ri = {r: k for k, r in enumerate(rows)}; ci = {c: k for k, c in enumerate(cols)}
        for g in gcps:
            lon[ri[g.row], ci[g.col]] = g.x; lat[ri[g.row], ci[g.col]] = g.y
        if np.isnan(lon).any():
            return None
        gx, gy = gr.laea(lon, lat)
        fx = RegularGridInterpolator((rows, cols), gx, bounds_error=False, fill_value=None)
        fy = RegularGridInterpolator((rows, cols), gy, bounds_error=False, fill_value=None)
        rr, cc = np.meshgrid(np.arange(0, ds.height, 64), np.arange(0, ds.width, 64), indexing="ij")
        pts = np.stack([rr.ravel(), cc.ravel()], 1); x, y = fx(pts), fy(pts)
        m = 1000.0
        ins = (x > G["tx0"] - m) & (x < G["tx0"] + G["W"] * PIX + m) & (y < G["ty1"] + m) & (y > G["ty1"] - G["H"] * PIX - m)
        if not ins.any():
            return None
        r0 = max(0, rr.ravel()[ins].min() - 64); r1 = min(ds.height, rr.ravel()[ins].max() + 64)
        c0 = max(0, cc.ravel()[ins].min() - 64); c1 = min(ds.width, cc.ravel()[ins].max() + 64)
        h, w = (r1 - r0) // dec, (c1 - c0) // dec
        if h < 4 or w < 4:
            return None
        a = ds.read(1, window=Window(c0, r0, w * dec, h * dec), out_shape=(h, w)).astype(np.float64)
    R_, C_ = np.meshgrid(r0 + (np.arange(h) + 0.5) * dec, c0 + (np.arange(w) + 0.5) * dec, indexing="ij")
    pts = np.stack([R_.ravel(), C_.ravel()], 1); x, y = fx(pts), fy(pts)
    ti = np.floor((x - G["tx0"]) / pix).astype(np.int64); tj = np.floor((G["ty1"] - y) / pix).astype(np.int64)
    v = a.ravel()
    ok = (ti >= 0) & (ti < W) & (tj >= 0) & (tj < H) & (v > 0)
    if ok.sum() < 200:
        return None
    idx = tj[ok] * W + ti[ok]
    return np.bincount(idx, weights=v[ok] ** 2, minlength=W * H), np.bincount(idx, minlength=W * H)


def pass_db(group, G, gr, dec, pix):
    """dB normalisés (médiane de la tuile retirée) d'un passage, pour vv et vh, ou None."""
    W, H = int(G["W"] * PIX / pix), int(G["H"] * PIX / pix)
    out = {}
    for pol in ("vv", "vh"):
        s = np.zeros(W * H); c = np.zeros(W * H)
        for it in group:
            for attempt in range(3):
                try:
                    r = read_pass(it[pol], G, gr, dec, pix); break
                except Exception as e:
                    print("   réessai", it["id"], pol, type(e).__name__, str(e)[:100], flush=True)
                    time.sleep(5 * (attempt + 1)); r = None
            if r is not None:
                s += r[0]; c += r[1]
        ok = c >= 1
        if ok.sum() < 0.05 * W * H:
            return None
        db = np.full(W * H, np.nan, np.float32); db[ok] = 10 * np.log10(s[ok] / c[ok]); db -= np.nanmedian(db)
        out[pol] = db.reshape(H, W)
    return out


def composite(groups, keys, G, gr, dec, pix, workers, want_var=False):
    """Moyenne (et variance) mensuelle par orbite, à partir des passages retenus."""
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda k: (k, pass_db(groups[k], G, gr, dec, pix)), keys))
    acc = {}
    for (_, orb, _), r in res:
        if r is None:
            continue
        a = acc.setdefault(orb, {p: [0.0, 0.0, 0] for p in ("vv", "vh")})
        for p in ("vv", "vh"):
            v = r[p]; ok = ~np.isnan(v)
            s, ss, n = a[p]
            if isinstance(n, int):
                s, ss, n = np.zeros(v.shape), np.zeros(v.shape), np.zeros(v.shape)
            s[ok] += v[ok]; ss[ok] += v[ok] ** 2; n[ok] += 1
            a[p] = [s, ss, n]
    out = {}
    for orb, a in acc.items():
        d = {}
        for p in ("vv", "vh"):
            s, ss, n = a[p]
            with np.errstate(all="ignore"):
                mu = np.where(n > 0, s / np.maximum(n, 1), np.nan)
                d[p] = mu
                if want_var:
                    d[p + "_var"] = np.maximum(np.where(n > 0, ss / np.maximum(n, 1) - mu ** 2, np.nan), 0)
            d["n"] = n
        out[orb] = d
    return out


# ---------------------------------------------------------------- météo (masque d'hiver)
def weather(points, start, end):
    """{(lat, lon): {heure ISO: froid ?}} via l'API historique Open-Meteo (ERA5), par lots de 5 points."""
    out = {}
    pts = sorted(points)
    for k in range(0, len(pts), 5):
        b = pts[k:k + 5]
        q = urllib.parse.urlencode(dict(latitude=",".join(str(a) for a, _ in b), longitude=",".join(str(o) for _, o in b),
                                        start_date=start, end_date=end, hourly="temperature_2m,snow_depth", timezone="UTC"))
        for attempt in range(5):
            try:
                with urllib.request.urlopen(f"{METEO}?{q}", timeout=120) as r:
                    data = json.loads(r.read()); break
            except Exception as e:
                print("  météo réessai", type(e).__name__, flush=True); time.sleep(30 * (attempt + 1))
        else:
            raise SystemExit("échec Open-Meteo")
        data = data if isinstance(data, list) else [data]
        for p, d in zip(b, data):
            h = d["hourly"]
            out[p] = {t: ((s is not None and s >= SNOW_M) or (tt is not None and tt <= T_MIN))
                      for t, tt, s in zip(h["time"], h["temperature_2m"], h["snow_depth"])}
        time.sleep(5)
    return out


def is_cold(wx, iso):
    t = dt.datetime.fromisoformat(iso[:19]) + dt.timedelta(minutes=30)
    return wx.get(t.strftime("%Y-%m-%dT%H:00"), False)


# ---------------------------------------------------------------- occupation du sol
def landcover(G, gr, cat):
    lo0, la0, lo1, la1 = G["bbox"]; m = 0.02
    items = [it for it in cat.search(collections=["esa-worldcover"], bbox=[lo0 - m, la0 - m, lo1 + m, la1 + m]).items() if "2021" in it.id]
    n = G["W"] * G["H"]; c = np.zeros(n); built = np.zeros(n); crop = np.zeros(n)
    for it in items:
        with rasterio.open(pc.sign(it.assets["map"].href)) as ds:
            w = from_bounds(lo0 - m, la0 - m, lo1 + m, la1 + m, ds.transform).round_offsets().round_lengths()
            w = w.intersection(Window(0, 0, ds.width, ds.height))
            a = ds.read(1, window=w); tr = ds.window_transform(w)
        rr, cc = np.meshgrid(np.arange(a.shape[0]) + 0.5, np.arange(a.shape[1]) + 0.5, indexing="ij")
        x, y = gr.laea(tr.c + cc * tr.a, tr.f + rr * tr.e)
        ti = np.floor((x - G["tx0"]) / PIX).astype(np.int64); tj = np.floor((G["ty1"] - y) / PIX).astype(np.int64)
        ok = (ti >= 0) & (ti < G["W"]) & (tj >= 0) & (tj < G["H"]) & (a > 0)
        idx = (tj * G["W"] + ti)[ok]; av = a[ok]
        c += np.bincount(idx, minlength=n)
        built += np.bincount(idx, weights=(av == 50).astype(float), minlength=n)
        crop += np.bincount(idx, weights=(av == 40).astype(float), minlength=n)
    b = (built / np.maximum(c, 1)).reshape(G["H"], G["W"]); cr = (crop / np.maximum(c, 1)).reshape(G["H"], G["W"])
    return b, cr


# ---------------------------------------------------------------- calcul d'une tuile
def month_add(m, k):
    n = int(m[:4]) * 12 + int(m[5:]) - 1 + k
    return f"{n // 12}-{n % 12 + 1:02d}"


def run_tile(G, gr, month, cutoff, wx, cat, workers):
    agri_months = [month_add(month, -k) for k in range(11, -1, -1)]
    cm = int(month[5:])
    ref_months = [f"2021-{((cm - 2) % 12) + 1:02d}", f"2021-{cm:02d}", f"2021-{(cm % 12) + 1:02d}"]
    wanted = set(agri_months) | set(ref_months)
    items = []
    for rng in ((f"{agri_months[0]}-01", cutoff), ("2021-01-01", "2021-12-31")):
        for it in cat.search(collections=["sentinel-1-grd"], bbox=G["bbox"], datetime=f"{rng[0]}T00:00:00Z/{rng[1]}T23:59:59Z",
                             query={"sar:instrument_mode": {"eq": "IW"}}).items():
            p = it.properties
            if it.datetime.strftime("%Y-%m") in wanted and {"VV", "VH"} <= set(p.get("sar:polarizations", [])):
                items.append(dict(id=it.id, dt=it.datetime.isoformat(), orbit=p["sat:relative_orbit"],
                                  vv=it.assets["vv"].href, vh=it.assets["vh"].href))
    groups = defaultdict(list)
    for it in items:
        groups[(it["dt"][:7], it["orbit"], it["dt"][:10])].append(it)
    keep = {k for k, g in groups.items() if not is_cold(wx, g[0]["dt"])}
    by_month = lambda m: sorted(k for k in keep if k[0] == m)  # noqa: E731
    built, crop = landcover(G, gr, cat)
    ncy, ncx = G["ncy"], G["ncx"]
    # bâti modifié : mois courant et référence 2021 (aperçu ×4 ≈ 40 m, ramené à 50 m, comme l'historique)
    cur = composite(groups, by_month(month), G, gr, 4, PIX, workers)
    refs = [composite(groups, by_month(m), G, gr, 4, PIX, workers, want_var=True) for m in ref_months]
    T = np.full((G["H"], G["W"]), -1.0)
    for o, C in cur.items():
        rs = [r[o] for r in refs if o in r]
        if not rs:
            continue
        ts = []
        for p in ("vv", "vh"):
            N = sum(r["n"] for r in rs)
            with np.errstate(all="ignore"):
                M = sum(np.nan_to_num(r[p]) * r["n"] for r in rs) / np.maximum(N, 1)
                V = sum((np.nan_to_num(r[p + "_var"]) + (np.nan_to_num(r[p]) - M) ** 2) * r["n"] for r in rs) / np.maximum(N, 1)
                sd = np.maximum(np.sqrt(V), FLOOR)
                t = np.abs(C[p] - M) / (sd * np.sqrt(1 / np.clip(C["n"], 1, NT_CAP) + 1 / np.maximum(N, 1)))
            ts.append(np.where((C["n"] >= MIN_NT) & (N >= MIN_NREF), t, np.nan))
        To = (ts[0] + ts[1]) / 2; v = ~np.isnan(To)
        T[v] = np.maximum(T[v], To[v])
    has = T >= 0; bmask = built > 0.3
    blk = lambda a, k: a.reshape(ncy, k, ncx, k).sum(axis=(1, 3))  # noqa: E731
    nb = blk((has & bmask).astype(float), K); db = blk((has & bmask & (T > T_DMG)).astype(float), K)
    # abandon agricole (aperçu ×8, pixels de 200 m)
    stacks = defaultdict(list)
    for m in agri_months:
        for o, C in composite(groups, by_month(m), G, gr, 8, PIX * A, workers).items():
            for p in ("vv", "vh"):
                stacks[(o, p)].append(np.where(C["n"] > 0, C[p], np.nan))
    acc = np.zeros((G["H"] // A, G["W"] // A)); cnt = np.zeros_like(acc)
    for st in stacks.values():
        if len(st) < 8:
            continue
        st = np.stack(st); nv = np.sum(~np.isnan(st), axis=0)
        with np.errstate(all="ignore"):
            sd = np.nanstd(st, axis=0)
        ok = nv >= 8; acc[ok] += sd[ok]; cnt[ok] += 1
    crop200 = crop.reshape(G["H"] // A, A, G["W"] // A, A).mean(axis=(1, 3)) > 0.6
    amp = np.where((cnt > 0) & crop200, acc / np.maximum(cnt, 1), np.nan)
    ab = amp.reshape(ncy, K // A, ncx, K // A).transpose(0, 2, 1, 3).reshape(ncy, ncx, -1)
    nval = np.sum(~np.isnan(ab), axis=2)
    with np.errstate(all="ignore"):
        med = np.nanmedian(ab, axis=2)
    bshare = built.reshape(ncy, K, ncx, K).mean(axis=(1, 3))
    rows = []
    for cj in range(ncy):
        for ci in range(ncx):
            j, i = G["j0"] + cj, G["i0"] + ci
            rows.append(dict(cell=j * gr.NX + i, i=i, j=j, month=month,
                             built_damage_since_2021=float(db[cj, ci] / nb[cj, ci]) if nb[cj, ci] >= 40 else None,
                             agri_amp_db=float(med[cj, ci]) if nval[cj, ci] >= 10 else None, built_share=float(bshare[cj, ci])))
    return rows, len(items), len(groups) - len(keep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--month", default=None)
    ap.add_argument("--radius", type=float, default=15.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="data/sar_cells.csv")
    ap.add_argument("--max-tiles", type=int, default=0, help="limite (tests)")
    ap.add_argument("--partial", action="store_true", help="calculer aussi le mois en cours (jusqu'à J−7)")
    a = ap.parse_args()
    cutoff_d = dt.date.today() - dt.timedelta(days=int(os.environ.get("LAG_DAYS", "1")))   # décalage de publication (J-1 depuis le 19/10/2026)
    if a.month:
        month = a.month
    elif a.partial or (cutoff_d + dt.timedelta(days=1)).day == 1:
        month = cutoff_d.strftime("%Y-%m")                          # mois en cours, lu jusqu'à J−7
    else:
        month = month_add(cutoff_d.strftime("%Y-%m"), -1)           # dernier mois complet terminé depuis ≥ 7 jours
    cutoff = min(cutoff_d, dt.date(int(month[:4]) + (month[5:] == "12"), int(month[5:]) % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
    gr = Grid(a.root)
    dist = gr.distance_km()
    win = (dist <= a.radius) & (gr.cp >= 0)
    tiles = sorted({(j // TILE * TILE, i // TILE * TILE) for j, i in zip(*np.nonzero(win))})
    if a.max_tiles:
        tiles = tiles[:a.max_tiles]
    print(f"mois {month} (jusqu'au {cutoff}), ligne du champ {gr.p_month}, {win.sum()} mailles à ≤ {a.radius} km, {len(tiles)} tuiles", flush=True)
    geoms = [tile_geom(gr, j0, i0) for j0, i0 in tiles]
    pts = {(round(G["center"][1] * 2) / 2, round(G["center"][0] * 2) / 2) for G in geoms}
    cm = int(month[5:])
    wx_cur = weather(pts, f"{month_add(month, -11)}-01", cutoff)
    wx_ref = weather(pts, "2021-01-01", "2021-12-31")
    cat = pystac_client.Client.open(STAC)
    rows = []
    t0 = time.time()
    for k, G in enumerate(geoms):
        p = (round(G["center"][1] * 2) / 2, round(G["center"][0] * 2) / 2)
        wx = {**wx_ref[p], **wx_cur[p]}
        r, nit, ncold = run_tile(G, gr, month, cutoff, wx, cat, a.workers)
        keep = {(j, i) for j, i in zip(*np.nonzero(win))}
        rows += [x for x in r if (x["j"], x["i"]) in keep]
        print(f"  tuile {k + 1}/{len(geoms)} : {nit} fichiers, {ncold} passages froids exclus, {time.time() - t0:.0f}s", flush=True)
    vals = [x["built_damage_since_2021"] for x in rows if x["built_damage_since_2021"] is not None]
    med = float(np.median(vals)) if vals else 0.0
    out = os.path.join(a.root, a.out) if not os.path.isabs(a.out) else a.out
    old = []
    if os.path.exists(out):
        old = [r for r in csv.DictReader(open(out)) if r["month"] != month]
    fields = ["cell", "i", "j", "month", "built_damage_since_2021", "built_damage_excess", "agri_amp_db", "built_share"]
    f4 = lambda v: "" if v is None else round(v, 4)  # noqa: E731
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(old)
        for x in rows:
            d = x["built_damage_since_2021"]
            w.writerow(dict(cell=x["cell"], i=x["i"], j=x["j"], month=month, built_damage_since_2021=f4(d),
                            built_damage_excess=f4(None if d is None else d - med), agri_amp_db=f4(x["agri_amp_db"]),
                            built_share=round(x["built_share"], 3)))
    print(f"écrit {out} : {len(rows)} mailles pour {month} ; contient des données Copernicus Sentinel modifiées ({month[:4]})")


if __name__ == "__main__":
    main()
