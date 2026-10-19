"""Bibliothèque du détecteur de cratères v2 sur le front entier (lecture Sentinel-2 L2A, grille du site, faisceau).

Reprend À L'IDENTIQUE (seuils gelés le 19/10/2026) la lecture et les masques de ukraine-recherche/sar/crateres_v2 :
  - crlib.mosaic (SCL d'abord, bandes B02 B03 B04 B08 à 10 m seulement si ≥ 15 % valide, décalage −1000 des baselines
    ≥ 04.00, SCL 4/5, nuages SCL 3/8/9/10 dilatés de 5 px) ;
  - run_zone.prep (brume HOT, dilatation 120 m, contexte clair 1 km ≥ 80 %) ;
  - detect2.py et rules.py sont des copies octet pour octet (shasum identique).
Différences voulues : une lecture qui échoue (réseau, veille du Mac) lève NetErr au lieu de rendre une date vide en
silence, pour que le pipeline attende le retour du réseau puis relise la même date ; la recherche STAC lève aussi.

Données : Copernicus Sentinel-2 L2A (ESA) via Microsoft Planetary Computer (COG publics, jetons SAS anonymes),
ESA WorldCover 2021 (CC BY 4.0). Même source que l'étalonnage de v2 (pas de changement de fournisseur : les seuils
sont gelés sur ces réflectances).
"""
import base64
import datetime as dt
import json
import os
import threading
import time

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("VSI_CACHE", "TRUE")
import numpy as np
from scipy import ndimage as ndi
from scipy.ndimage import distance_transform_edt

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
RES = 10.0
BANDS = ("B02", "B03", "B04", "B08")
DIL = 12          # run_zone.DIL (120 m)
CLEAR = 0.8       # run_zone.CLEAR (voisinage de 1 km clair à 80 %)
TILE = 20         # tuile de 20 × 20 mailles de 2 km (40 km), comme sar/band.py et scripts/sar_update.py


class NetErr(Exception):
    pass


# ---------------------------------------------------------------- champs du site (grille LAEA 2 km)
def _load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def _load_frontp(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


class Front:
    """Grille, mailles du domaine et champ « equilibre » par mois (copie de sar/frontlib.py + sar/band.py)."""

    def __init__(self, root, view="equilibre"):
        F = _load_js(os.path.join(root, "site", "front.js"))
        g = F["grid"]
        self.NX, self.NY, self.SIZE, self.X0, self.Y1 = g["nx"], g["ny"], g["size"], g["x0"], g["y1"]
        self.PROJ4 = f"+proj=laea +lat_0={g['lat0']} +lon_0={g['lon0']} +x_0=0 +y_0=0 +R={g['R']} +units=m +no_defs"
        months = _load_js(os.path.join(root, "site", "data.js"))["months"]
        cp = np.zeros(self.NX * self.NY, dtype=np.int32)
        b = base64.b64decode(F["runs"])
        o = 0
        for k in range(0, len(b), 3):
            v = b[k] | b[k + 1] << 8
            n = b[k + 2]
            cp[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
            o += n
        self.cp = cp.reshape(self.NY, self.NX)
        self.dom = self.cp >= 0
        ucells = np.nonzero(cp != -1)[0]
        enc = _load_frontp(os.path.join(root, "site", f"front_{view}.js"))
        self.F, prev = {}, np.zeros(len(ucells), dtype=np.uint8)
        for t, e in enumerate(enc):
            if not e:
                continue
            bb = base64.b64decode(e)
            q = np.zeros(len(ucells), dtype=np.uint8)
            o = i = 0
            while i < len(bb):
                v = bb[i]
                i += 1
                n = sh = 0
                while True:
                    c = bb[i]
                    i += 1
                    n |= (c & 127) << sh
                    sh += 7
                    if not c & 128:
                        break
                q[o:o + n] = prev[o:o + n] if v == 255 else v
                o += n
            prev = q
            gg = np.full(self.NX * self.NY, np.nan, dtype=np.float32)
            gg[ucells] = q / 63.0
            self.F[months[t] if t < len(months) else t] = gg.reshape(self.NY, self.NX)
        self.FM = sorted(k for k in self.F if isinstance(k, str))
        self._D = {}

    def line_of(self, g):
        dom = self.dom
        ru = (g >= 0.5) & dom
        ua = (g < 0.5) & dom
        line = np.zeros_like(dom)
        line[:, :-1] |= (ru[:, :-1] & ua[:, 1:]) | (ua[:, :-1] & ru[:, 1:])
        line[:, 1:] |= (ru[:, 1:] & ua[:, :-1]) | (ua[:, 1:] & ru[:, :-1])
        line[:-1, :] |= (ru[:-1, :] & ua[1:, :]) | (ua[:-1, :] & ru[1:, :])
        line[1:, :] |= (ru[1:, :] & ua[:-1, :]) | (ua[1:, :] & ru[:-1, :])
        return line

    def fld(self, m):
        ks = [k for k in self.FM if k <= m]
        return ks[-1] if ks else self.FM[0]

    def D(self, m):
        """Distance (km) à la ligne p = 0,5 du champ du mois m (ou du dernier champ antérieur)."""
        fm = self.fld(m)
        if fm not in self._D:
            self._D[fm] = distance_transform_edt(~self.line_of(self.F[fm])) * self.SIZE / 1000.0
        return self._D[fm]

    def band(self, m0, m1, radius):
        """Mailles du domaine (hors mer) à ≤ radius km de la ligne à au moins un mois de [m0, m1]."""
        ms = [m for m in self.FM if m0 <= m <= m1] or [self.fld(m0)]
        b = np.zeros((self.NY, self.NX), bool)
        for m in ms:
            b |= self.D(m) <= radius
        return b & (self.cp != -1)

    def sec_side(self, cell, month, rmax=15.0):
        """(j_tronçon, i_tronçon, côté) d'une maille au mois donné ; None hors domaine ou à > 15 km (agg_decades)."""
        j, i = cell // self.NX, cell % self.NX
        if not (0 <= j < self.NY) or not self.dom[j, i]:
            return None
        fm = self.fld(month)
        if self.D(month)[j, i] > rmax:
            return None
        return (j // 10, i // 10, "RU" if self.F[fm][j, i] >= 0.5 else "UA")


def tiles_for(front, band):
    out = []
    for tj in range(0, front.NY, TILE):
        for ti in range(0, front.NX, TILE):
            n = int(band[tj:tj + TILE, ti:ti + TILE].sum())
            if n:
                out.append(dict(id=f"t{tj:03d}_{ti:03d}", j0=tj, i0=ti, ncells=n))
    return out


def tile_utm(front, t):
    """Grille UTM à 10 m (alignée sur 20 m) couvrant la tuile de 40 km (copie de sar/impacts_front.tile_utm)."""
    from pyproj import Transformer
    x0 = front.X0 + t["i0"] * front.SIZE; y1 = front.Y1 - t["j0"] * front.SIZE
    ncx = min(TILE, front.NX - t["i0"]); ncy = min(TILE, front.NY - t["j0"])
    xs = np.array([x0, x0 + ncx * front.SIZE, x0, x0 + ncx * front.SIZE])
    ys = np.array([y1, y1, y1 - ncy * front.SIZE, y1 - ncy * front.SIZE])
    lon, lat = Transformer.from_crs(front.PROJ4, "EPSG:4326", always_xy=True).transform(xs, ys)
    zone = int((np.mean(lon) + 180) // 6) + 1
    epsg = 32600 + zone
    ux, uy = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    gx0, gx1 = np.floor(min(ux) / 20) * 20, np.ceil(max(ux) / 20) * 20
    gy0, gy1 = np.floor(min(uy) / 20) * 20, np.ceil(max(uy) / 20) * 20
    return dict(epsg=int(epsg), x0=float(gx0), y1=float(gy1), W=int((gx1 - gx0) / RES), H=int((gy1 - gy0) / RES),
                bbox=[float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))])


def cell_map(front, G):
    """Maille du site de chaque pixel de 100 m (1 pixel sur 10), comme run_zone.cell_map."""
    from pyproj import Transformer
    tr = Transformer.from_crs(f"EPSG:{G['epsg']}", front.PROJ4, always_xy=True)
    yy, xx = np.mgrid[0:G["H"]:10, 0:G["W"]:10]
    lx, ly = tr.transform(G["x0"] + (xx + 5) * 10.0, G["y1"] - (yy + 5) * 10.0)
    return (np.floor((front.Y1 - ly) / front.SIZE) * front.NX + np.floor((lx - front.X0) / front.SIZE)).astype(np.int64), tr


# ---------------------------------------------------------------- STAC (poli : une recherche par tuile, pauses)
_stac_lock = threading.Lock()


def search(bbox, start, end, cmax=50, tries=6):
    import pystac_client
    last = None
    for attempt in range(tries):
        try:
            with _stac_lock:
                cat = pystac_client.Client.open(STAC)
                items = list(cat.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=f"{start}/{end}",
                                        query={"eo:cloud_cover": {"lt": cmax}}).items())
                time.sleep(1.0)
            return [it.to_dict(transform_hrefs=False) for it in items]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(600, 20 * 2 ** attempt))
    raise NetErr(f"recherche STAC : {type(last).__name__}")


def by_date(item_dicts):
    import pystac
    out = {}
    for d in item_dicts:
        it = pystac.Item.from_dict(d)
        out.setdefault(it.datetime.strftime("%Y-%m-%d"), []).append(it)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- lecture fenêtrée des COG
def _read(href, G, scale):
    import planetary_computer as pc
    import rasterio
    from rasterio.windows import from_bounds, Window
    with rasterio.open(pc.sign(href)) as ds:
        # produits sans CRS (vus en 2026, p. ex. T37UCR du 07/04/2026) : crlib levait AttributeError, réessayait puis
        # rendait None ; on rend None tout de suite (même résultat, sans attente)
        if ds.crs is None or ds.crs.to_epsg() != G["epsg"]:
            return None
        bx0, by1 = G["x0"], G["y1"]
        bx1, by0 = bx0 + G["W"] * RES, by1 - G["H"] * RES
        try:
            wi = from_bounds(bx0, by0, bx1, by1, ds.transform).intersection(Window(0, 0, ds.width, ds.height))
        except Exception:  # noqa: BLE001  (fenêtre hors image : légitime)
            return None
        wi = Window(int(round(wi.col_off)), int(round(wi.row_off)), int(round(wi.width)), int(round(wi.height)))
        if wi.width <= 0 or wi.height <= 0:
            return None
        a = ds.read(1, window=wi)
        tr = ds.window_transform(wi)
    if scale > 1:
        a = np.repeat(np.repeat(a, scale, 0), scale, 1)
    c0 = int(round((tr.c - G["x0"]) / RES)); r0 = int(round((G["y1"] - tr.f) / RES))
    out = np.zeros((G["H"], G["W"]), a.dtype)
    rr0, cc0 = max(0, r0), max(0, c0)
    rr1, cc1 = min(G["H"], r0 + a.shape[0]), min(G["W"], c0 + a.shape[1])
    if rr1 <= rr0 or cc1 <= cc0:
        return None
    out[rr0:rr1, cc0:cc1] = a[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
    return out


_tl = threading.local()


def read(href, G, scale, tries=4):
    """Comme crlib.read (4 essais). En mode strict (défaut) l'échec lève NetErr ; en mode « tolérant » (fetch_one(…,
    lenient=True), utilisé quand le réseau répond mais que la même date échoue encore : fichier abîmé ou absent) il
    rend None, exactement comme crlib.read."""
    last = None
    for attempt in range(tries):
        try:
            return _read(href, G, scale)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(4 + 6 * attempt)
    if getattr(_tl, "lenient", False):
        return None
    raise NetErr(f"lecture COG : {type(last).__name__}: {str(last)[:120]}")


def net_ok(timeout=20):
    """Le service répond-il ? (distingue une coupure réseau d'un fichier abîmé)."""
    import urllib.request
    try:
        with urllib.request.urlopen(STAC, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def mosaic(items, G, bands=BANDS, minvalid=0.15):
    """Copie de crlib.mosaic (bandes à 10 m seulement), qui lève NetErr au lieu d'ignorer une lecture ratée."""
    H, W = G["H"], G["W"]
    scl_m = np.zeros((H, W), np.uint8)
    used = []
    for it in items:
        if not it.properties.get("s2:mgrs_tile", str(G["epsg"] - 32600)).startswith(str(G["epsg"] - 32600)):
            continue
        s = read(it.assets["SCL"].href, G, 2)
        if s is None:
            continue
        new = (scl_m == 0) & (s > 0)
        if new.any():
            scl_m[new] = s[new]
            used.append((it, new))
    bad = np.isin(scl_m, (3, 8, 9, 10))
    bad = ndi.binary_dilation(bad, iterations=5)
    valid = np.isin(scl_m, (4, 5)) & ~bad
    if valid.mean() < minvalid:
        return None, valid, scl_m
    m = {b: np.zeros((H, W), np.float32) for b in bands}
    for it, new in used:
        tk = new & valid
        if not tk.any():
            continue
        off = 0.1 if float(it.properties.get("s2:processing_baseline", "0") or 0) >= 4.0 else 0.0
        for b in bands:
            a = read(it.assets[b].href, G, 1)
            if a is None:
                valid[tk] = False
                continue
            m[b][tk] = a[tk].astype(np.float32) / 1e4 - off
    for b in bands:
        valid &= m[b] > -0.05
    return m, valid, scl_m


def prep(o):
    """Copie de run_zone.prep (brume HOT, dilatation 120 m, contexte clair de 1 km ≥ 80 %)."""
    hot = (o["B02"].astype(np.float32) - 0.5 * o["B04"].astype(np.float32)) / 1e4
    v = o["valid"]
    med = float(np.median(hot[v][::97])) if v.any() else 0.0
    bad = np.isin(o["scl"], (2, 3, 8, 9, 10)) | (v & (hot > max(0.035, med + 0.02)))
    o["valid"] = v & (ndi.maximum_filter(bad.astype(np.uint8), 2 * DIL + 1) == 0)
    frac = ndi.uniform_filter(o["valid"].astype(np.float32), 101, mode="constant")
    o["valid"] &= frac >= CLEAR
    o.pop("scl", None)
    return o


def fetch_one(G, items, lenient=False):
    """Mosaïque d'une date lue à distance (copie de run_zone.fetch_one) ; None si trop nuageuse."""
    _tl.lenient = lenient
    m, v, scl = mosaic(items, G, bands=BANDS)
    if m is None:
        return None
    o = {b: np.clip(m[b] * 1e4, -1000, 32000).astype(np.int16) for b in BANDS}
    o["valid"] = v; o["scl"] = scl
    return prep(o)


def worldcover(G, tries=6):
    import planetary_computer as pc
    import pystac_client
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, Resampling
    last = None
    for attempt in range(tries):
        try:
            cat = pystac_client.Client.open(STAC, modifier=pc.sign_inplace)
            out = np.zeros((G["H"], G["W"]), np.uint8)
            for it in cat.search(collections=["esa-worldcover"], bbox=G["bbox"]).items():
                if "2021" not in it.id:
                    continue
                with rasterio.open(it.assets["map"].href) as ds:
                    dst = np.zeros_like(out)
                    reproject(rasterio.band(ds, 1), dst, dst_transform=from_origin(G["x0"], G["y1"], RES, RES),
                              dst_crs=f"EPSG:{G['epsg']}", resampling=Resampling.nearest)
                    out = np.where(out == 0, dst, out)
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(600, 20 * 2 ** attempt))
    raise NetErr(f"WorldCover : {type(last).__name__}")


def ordd(d):
    return dt.date.fromisoformat(d).toordinal()


def decade(d):
    return f"{d[:7]}-{min(3, (int(d[8:10]) - 1) // 10 + 1)}"
