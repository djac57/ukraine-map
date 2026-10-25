"""Impacts Sentinel-2 (taches de 10 à 30 m, nouvelles et persistantes) sur le faisceau du front, par maille de 2 km
et par décade. FETCH 100 % Planetary Computer (STAC public, pc.sign) — aucun compte, aucun Earthdata, aucun cache local
obligatoire : le cache de tuiles produit ici est transitoire (reconstruit à chaque run CI).

Porté FIDÈLEMENT de ukraine-recherche/sar/impacts_front.py (E3b, 25/10/2026). Seuls changements : tiles.json et le
dossier de cache sont relatifs à ce dossier (ou à $IMPACTS_V1_CACHE) ; frontlib local ; détecteur inchangé.

    python -I fetch.py <lot> <nlots> [début AAAA-MM-JJ] [fin AAAA-MM-JJ]   -> <cache>/<tuile>.csv
"""
import csv
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from rasterio.windows import from_bounds, Window
from scipy import ndimage as ndi

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import frontlib as fl  # noqa: E402

TILES = os.path.join(HERE, "tiles.json")
OUTD = os.environ.get("IMPACTS_V1_CACHE", os.path.join(HERE, "work_impacts", "front"))
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
RES = 10.0
T_NBR, T_NIR = 0.12, np.log(0.75)


def tile_utm(t):
    x0 = fl.X0 + t["i0"] * fl.SIZE; y1 = fl.Y1 - t["j0"] * fl.SIZE
    ncx = min(20, fl.NX - t["i0"]); ncy = min(20, fl.NY - t["j0"])
    xs = np.array([x0, x0 + ncx * fl.SIZE, x0, x0 + ncx * fl.SIZE]); ys = np.array([y1, y1, y1 - ncy * fl.SIZE, y1 - ncy * fl.SIZE])
    lon, lat = Transformer.from_crs(fl.PROJ4, "EPSG:4326", always_xy=True).transform(xs, ys)
    zone = int((np.mean(lon) + 180) // 6) + 1
    epsg = 32600 + zone
    ux, uy = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    gx0, gx1 = np.floor(min(ux) / 20) * 20, np.ceil(max(ux) / 20) * 20
    gy0, gy1 = np.floor(min(uy) / 20) * 20, np.ceil(max(uy) / 20) * 20
    return dict(epsg=epsg, x0=gx0, y1=gy1, W=int((gx1 - gx0) / RES), H=int((gy1 - gy0) / RES),
                bbox=[float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))])


def read_asset(href, G, scale):
    with rasterio.open(pc.sign(href)) as ds:
        if ds.crs.to_epsg() != G["epsg"]:
            return None
        bx0, by1 = G["x0"], G["y1"]; bx1, by0 = bx0 + G["W"] * RES, by1 - G["H"] * RES
        try:
            wi = from_bounds(bx0, by0, bx1, by1, ds.transform).intersection(Window(0, 0, ds.width, ds.height))
        except Exception:
            return None
        wi = Window(int(round(wi.col_off)), int(round(wi.row_off)), int(round(wi.width)), int(round(wi.height)))
        if wi.width <= 0 or wi.height <= 0:
            return None
        a = ds.read(1, window=wi); tr = ds.window_transform(wi)
    if scale > 1:
        a = np.repeat(np.repeat(a, scale, 0), scale, 1)
    c0 = int(round((tr.c - G["x0"]) / RES)); r0 = int(round((G["y1"] - tr.f) / RES))
    out = np.zeros((G["H"], G["W"]), a.dtype)
    rr0, cc0 = max(0, r0), max(0, c0); rr1, cc1 = min(G["H"], r0 + a.shape[0]), min(G["W"], c0 + a.shape[1])
    if rr1 <= rr0 or cc1 <= cc0:
        return None
    out[rr0:rr1, cc0:cc1] = a[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
    return out


def mosaic(items, G):
    b8 = np.zeros((G["H"], G["W"]), np.uint16); b12 = b8.copy()
    valid = np.zeros((G["H"], G["W"]), bool); bad = np.zeros_like(valid)
    for it in items:
        for attempt in range(3):
            try:
                scl = read_asset(it.assets["SCL"].href, G, 2)
                break
            except Exception:
                time.sleep(5); scl = None
        if scl is None:
            continue
        ok = np.isin(scl, (4, 5)) & ~valid
        bad |= np.isin(scl, (3, 8, 9, 10, 11))
        if not ok.any():
            continue
        try:
            a8 = read_asset(it.assets["B08"].href, G, 1); a12 = read_asset(it.assets["B12"].href, G, 2)
        except Exception:
            continue
        if a8 is None or a12 is None:
            continue
        b8[ok] = a8[ok]; b12[ok] = a12[ok]; valid |= ok
    bad = ndi.binary_dilation(bad, iterations=5)
    valid &= ~bad & (b8 > 0) & (b12 > 0)
    return dict(B08=b8, B12=b12), valid


def landmask(G):
    cat = pystac_client.Client.open(STAC, modifier=pc.sign_inplace)
    out = np.zeros((G["H"], G["W"]), np.uint8)
    for it in cat.search(collections=["esa-worldcover"], bbox=G["bbox"]).items():
        if "2021" not in it.id:
            continue
        with rasterio.open(it.assets["map"].href) as ds:
            dst = np.zeros_like(out)
            reproject(rasterio.band(ds, 1), dst, dst_transform=from_origin(G["x0"], G["y1"], RES, RES), dst_crs=f"EPSG:{G['epsg']}",
                      resampling=Resampling.nearest)
            out = np.where(out == 0, dst, out)
    return np.isin(out, (50, 80, 90)) | (out == 0)


def nbr(m):
    b8, b12 = m["B08"].astype(np.float32), m["B12"].astype(np.float32)
    return (b8 - b12) / np.maximum(b8 + b12, 1)


def local(x):
    return x - ndi.uniform_filter(x, 9)


def detect(m0, v0, m1, v1, m2, v2, excl):
    ok = v0 & v1 & ~excl
    n0, n1 = nbr(m0), nbr(m1)
    dn = np.where(ok, n0 - n1, 0).astype(np.float32)
    lr = np.where(ok, np.log(np.maximum(m1["B08"], 1) / np.maximum(m0["B08"], 1)), 0).astype(np.float32)
    cand = ok & (local(dn) > T_NBR) & (local(lr) < T_NIR)
    raw = ok & ((dn > 0.08) | (lr < np.log(0.85)))
    labr, nr = ndi.label(raw, structure=np.ones((3, 3)))
    if nr:
        sz = ndi.sum(raw, labr, index=np.arange(1, nr + 1))
        large = np.zeros(nr + 1, bool); large[1:] = sz > 9
        cand &= ~ndi.binary_dilation(large[labr], iterations=10)
    b8 = m0["B08"].astype(np.float32)
    mu = ndi.uniform_filter(b8, 9); sd = np.sqrt(np.maximum(ndi.uniform_filter(b8 * b8, 9) - mu * mu, 0))
    cand &= sd < 0.12 * np.maximum(mu, 1)
    lab, n = ndi.label(cand, structure=np.ones((3, 3)))
    if not n:
        return [], ok
    sizes = ndi.sum(cand, lab, index=np.arange(1, n + 1))
    n2 = nbr(m2)
    persist = np.where(v0 & v2, n0 - n2, np.nan)
    pm = ndi.mean(np.nan_to_num(persist, nan=-1), lab, index=np.arange(1, n + 1))
    com = ndi.center_of_mass(cand, lab, index=np.arange(1, n + 1))
    return [c for c, s, p in zip(com, sizes, pm) if s <= 9 and p >= T_NBR * 0.6], ok


def decade(d):
    return f"{d[:7]}-{min(3, (int(d[8:10]) - 1) // 10 + 1)}"


def run_tile(t, start, end):
    out = os.path.join(OUTD, f"{t['id']}.csv")
    if os.path.exists(out):
        return "déjà"
    G = tile_utm(t)
    cat = pystac_client.Client.open(STAC)
    items = []
    for attempt in range(4):
        try:
            items = list(cat.search(collections=["sentinel-2-l2a"], bbox=G["bbox"], datetime=f"{start}/{end}",
                                    query={"eo:cloud_cover": {"lt": 40}}).items()); break
        except Exception:
            time.sleep(20)
    by_date = defaultdict(list)
    for it in items:
        by_date[it.datetime.strftime("%Y-%m-%d")].append(it)
    dates = sorted(by_date)
    excl = landmask(G)
    tr = Transformer.from_crs(f"EPSG:{G['epsg']}", fl.PROJ4, always_xy=True)
    yy, xx = np.mgrid[0:G["H"]:10, 0:G["W"]:10]
    lx, ly = tr.transform(G["x0"] + (xx + 5) * RES, G["y1"] - (yy + 5) * RES)
    cell_sub = (np.floor((fl.Y1 - ly) / fl.SIZE) * fl.NX + np.floor((lx - fl.X0) / fl.SIZE)).astype(np.int64)
    det = defaultdict(int); obs = defaultdict(float)
    win = []
    with ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(mosaic, by_date[d], G) for d in dates[:2]]
        for k, d in enumerate(dates):
            m, v = futs[k].result()
            if k + 2 < len(dates):
                futs.append(ex.submit(mosaic, by_date[dates[k + 2]], G))
            futs[k] = None
            if v.mean() < 0.2:
                continue
            win.append((d, m, v))
            if len(win) == 3:
                (d0, m0, v0), (d1, m1, v1), (d2, m2, v2) = win
                pts, ok = detect(m0, v0, m1, v1, m2, v2, excl)
                dec = decade(d1)
                for r, c in pts:
                    x, y = tr.transform(G["x0"] + (c + 0.5) * RES, G["y1"] - (r + 0.5) * RES)
                    det[(int((fl.Y1 - y) // fl.SIZE) * fl.NX + int((x - fl.X0) // fl.SIZE), dec)] += 1
                okc = ok[::10, ::10]
                for cell, n in zip(*np.unique(cell_sub[okc], return_counts=True)):
                    obs[(int(cell), dec)] += n * 0.01
                win.pop(0)
    os.makedirs(OUTD, exist_ok=True)
    with open(out + ".part", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell", "decade", "s2_impacts", "s2_obs_km2"])
        for (cell, dec), a in sorted(obs.items()):
            w.writerow([cell, dec, det.get((cell, dec), 0), round(a, 2)])
    os.replace(out + ".part", out)
    return f"{len(dates)} dates, {sum(det.values())} détections"


def main():
    shard, nsh = int(sys.argv[1]), int(sys.argv[2])
    start = sys.argv[3] if len(sys.argv) > 3 else "2024-07-01"
    end = sys.argv[4] if len(sys.argv) > 4 else "2026-10-10"
    tiles = [t for t in json.load(open(TILES)) if any("2024-07" <= m <= "2026-09" for m in t["act15"])]
    # tuiles du nord de Lyman d'abord (ordre d'origine, pour un diagnostic lisible)
    tiles.sort(key=lambda t: (abs(t["j0"] - 180) + abs(t["i0"] - 580), t["id"]))
    mine = tiles[shard::nsh]
    print(f"lot {shard}/{nsh} : {len(mine)} tuiles sur {len(tiles)} ; fenêtre {start} → {end} ; cache {OUTD}", flush=True)
    t0 = time.time()
    for t in mine:
        r = run_tile(t, start, end)
        print(f"  {t['id']} : {r} ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
