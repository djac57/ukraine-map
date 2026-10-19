"""Socle du moteur de fusion en PRODUCTION : tout est lu dans les fichiers PUBLIÉS du site (`site/front.js`,
`site/front_equilibre.js`, `site/front_nowcast.js`) ; aucune donnée brute (VIINA, Telegram, etc.) n'est relue.

Conventions du projet : p = probabilité de contrôle russe ; distances signées POSITIVES côté russe ; mailles de 2 km ;
mois = instantané mensuel à J-7 ; vérité = consensus des cartes à t+2.

  - grille, maillage (mailles du modèle U, localité de chaque maille) : `FRONT.grid`, `FRONT.runs` ;
  - états par localité et par carte (DeepState corrigé par son journal daté, ISW prolongée après son arrêt, Wikipédia) :
    `FRONT.months[].d` (différences de codes b0 | b1 << 2 | b2 << 4 | b3 << 6 | drapeaux ; 0 absente, 1 ukrainienne,
    2 russe, 3 contestée ; une localité absente du fichier n'a que des codes 0 ou 1) ;
  - consensus des cartes T (1 russe, 0 ukrainien, −1 inconnu) par localité, comme `build_front.py` (majorité des cartes
    qui donnent 1 ou 2 parmi DeepState, ISW, Wikipédia ; égalité : inconnu), puis par maille ;
  - Peq = champ des CARTES (vue équilibrée), Pnow = champ PUBLIÉ (nowcast) ; SDe, SDn : distances signées à leurs lignes 50 % ;
  - zone Z = mailles à ≤ 62 km de l'une des deux lignes à un mois quelconque ; tronçon = tuile de 24 km (12 × 12 mailles),
    position Y_{t,k} = surface russe du consensus / 24 km si ≥ 20 mailles à ≤ 20 km de la ligne et ≥ 20 mailles connues.
"""
import base64
import datetime as dt
import json
import math
import os

import numpy as np
from scipy import ndimage

ROOT = os.environ.get("FUSION_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(ROOT, "data")
TILE = int(os.environ.get("FUSION_TILE", "12"))  # côté du tronçon du moteur, en mailles de 2 km (12 = 24 km ; 6 et 8 testés le
                                                 # 19/10/2026 : mailles qui changent dégradées, non retenus)
SEC = 12                                         # secteur d'ÉVALUATION et de calibration de la bande : 24 km, quel que soit TILE
OFF = int(os.environ.get("FUSION_OFFSET", "0"))   # décalage du pavage des tronçons (mailles, sur les deux axes)
TILE_N = TILE * TILE / 144.0                     # surface du tronçon rapportée à celle de 24 km (seuils d'effectifs)
ZONE_KM = 62.0
ISW_STALE_DAYS = 45     # ISW tenue pour arrêtée (témoin absent) quand son âge dépasse 45 jours


def load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def load_seq(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


def decode_seq(enc, n):
    out, prev = [], np.zeros(n, np.uint8)
    for e in enc:
        if not e:
            out.append(None)
            continue
        b, q, o, i = base64.b64decode(e), np.zeros(n, np.uint8), 0, 0
        while i < len(b):
            v = b[i]; i += 1
            k = sh = 0
            while True:
                c = b[i]; i += 1
                k |= (c & 127) << sh; sh += 7
                if not c & 128:
                    break
            q[o:o + k] = prev[o:o + k] if v == 255 else v
            o += k
        out.append(q)
        prev = q
    return out


def encode_field(q, prevq):
    """Même encodage que `build_front.encode_field` (valeurs 0-63, 255 = inchangé, longueurs en varint)."""
    q = np.asarray(q, np.uint8)
    v = q.astype(np.int16)
    if prevq is not None:
        v = np.where(np.asarray(prevq, np.uint8) == q, 255, v)
    out = bytearray()
    if len(v) == 0:
        return ""
    cut = np.flatnonzero(np.diff(v)) + 1
    starts = np.r_[0, cut]
    ends = np.r_[cut, len(v)]
    for a, b in zip(starts, ends):
        out.append(int(v[a]))
        n = int(b - a)
        while True:
            c = n & 0x7F
            n >>= 7
            out.append(c | 0x80 if n else c)
            if not n:
                break
    return base64.b64encode(bytes(out)).decode()


def lgt(p, eps=0.5 / 63):
    p = np.clip(p, eps, 1 - eps)
    return np.log(p / (1 - p))


class Socle:
    def __init__(self):
        FR = load_js(os.path.join(SITE, "front.js"))
        self.FR = FR
        G = FR["grid"]
        self.NX, self.NY, self.km = G["nx"], G["ny"], G["size"] / 1000
        self.SIZE, self.X0, self.Y1 = G["size"], G["x0"], G["y1"]
        self.LON0, self.LAT0, self.R = G["lon0"], G["lat0"], G["R"]
        place = np.zeros(self.NX * self.NY, np.int32)
        b, o = base64.b64decode(FR["runs"]), 0
        for i in range(0, len(b), 3):
            v, n = b[i] | b[i + 1] << 8, b[i + 2]
            place[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
            o += n
        self.U = np.flatnonzero(place != -1).astype(np.int64)
        self.cellU = place[self.U].astype(np.int64)
        self.nU = len(self.U)
        self.nPlaces = int(FR["nPlaces"])
        fl = base64.b64decode(FR["flags"])
        self.crimea = np.frombuffer(fl, np.uint8)[:self.nPlaces] & 1 > 0
        self.pre = np.frombuffer(fl, np.uint8)[:self.nPlaces] & 2 > 0
        # mois : les FRONT.nMonths premières entrées de FRONT.months (null : mois non construit) ; pas récents ensuite
        self.NM = int(FR["nMonths"])
        first = next(m for m in FR["months"] if m)["date"]
        k0 = next(i for i, m in enumerate(FR["months"]) if m)
        y0, m0 = int(first[:4]), int(first[5:7])
        self.months = []
        for i in range(self.NM):
            q = (y0 * 12 + m0 - 1) + (i - k0)
            self.months.append(f"{q // 12:04d}-{q % 12 + 1:02d}")
        self.nOrder = len(FR["months"])
        self.mdate = [(FR["months"][i]["date"] if i < len(FR["months"]) and FR["months"][i] else None) for i in range(self.NM)]
        self.midx = {m: i for i, m in enumerate(self.months)}
        self.jj, self.ii = self.U // self.NX, self.U % self.NX
        self.isUA = self.cellU >= 0
        self.pos = np.full(self.NX * self.NY, -1, np.int64)
        self.pos[self.U] = np.arange(self.nU)
        self._build()
        self.nZ = len(self.Zi)
        self.zpos = np.full(self.nU, -1, np.int64)
        self.zpos[self.Zi] = np.arange(self.nZ)
        self.tileU = ((self.jj + OFF) // TILE) * 1000 + (self.ii + OFF) // TILE
        self.tileZ = self.tileU[self.Zi]
        self.tiles, self.tinv = np.unique(self.tileZ, return_inverse=True)
        self.nT = len(self.tiles)
        order = np.argsort(self.tinv, kind="stable")
        bounds = np.searchsorted(self.tinv[order], np.arange(self.nT + 1))
        self.tile_cells = [order[bounds[k]:bounds[k + 1]] for k in range(self.nT)]   # indices Z par tronçon
        self.tile_km = TILE * self.km
        # secteurs de 24 km (évaluation, calibration de la bande, placebo du KPI) ; secteur d'un tronçon = celui de son centre
        secZ = (self.jj[self.Zi] // SEC) * 1000 + self.ii[self.Zi] // SEC
        self.secs, self.sinv = np.unique(secZ, return_inverse=True)
        self.nS = len(self.secs)
        sidx = {int(c): i for i, c in enumerate(self.secs)}
        cen = [((int(c) // 1000) * TILE + TILE // 2 - OFF, (int(c) % 1000) * TILE + TILE // 2 - OFF) for c in self.tiles]
        self.sec_of_tile = np.array([sidx.get((a // SEC) * 1000 + b // SEC, int(self.sinv[self.tile_cells[k][0]]))
                                     for k, (a, b) in enumerate(cen)], np.int64)
        self.built = [t for t in range(self.NM) if np.isfinite(self.Peq[t]).any() and (self.T[t] >= 0).any()]
        self._tiles_Y()

    # ---------- géométrie ----------
    def laea(self, lon, lat):
        lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
        l0, p0 = math.radians(self.LON0), math.radians(self.LAT0)
        k = np.sqrt(2 / (1 + math.sin(p0) * np.sin(lat) + math.cos(p0) * np.cos(lat) * np.cos(lon - l0)))
        return self.R * k * np.cos(lat) * np.sin(lon - l0), self.R * k * (math.cos(p0) * np.sin(lat) - math.sin(p0) * np.cos(lat) * np.cos(lon - l0))

    def cell_of(self, lon, lat):
        """Indices de maille (grille complète) ; −1 hors grille."""
        x, y = self.laea(lon, lat)
        i, j = np.floor((x - self.X0) / self.SIZE).astype(int), np.floor((self.Y1 - y) / self.SIZE).astype(int)
        ok = (i >= 0) & (i < self.NX) & (j >= 0) & (j < self.NY)
        return np.where(ok, j * self.NX + i, -1)

    def u_of_cell(self, cells):
        cells = np.asarray(cells, np.int64)
        out = np.full(len(cells), -1, np.int64)
        ok = (cells >= 0) & (cells < self.NX * self.NY)
        out[ok] = self.pos[cells[ok]]
        return out

    def grid(self, v, fill=0.0):
        g = np.full((self.NY, self.NX), fill, np.float32)
        g[self.jj, self.ii] = v
        return g

    def smooth(self, v, sig):
        """Lissage gaussien (σ en mailles) d'un vecteur sur U ; renvoie un vecteur sur U."""
        v = np.nan_to_num(np.asarray(v, np.float32))
        return ndimage.gaussian_filter(self.grid(v), sig)[self.jj, self.ii] if sig > 0 else v

    def sd_of(self, p):
        g = self.grid(p, np.nan)
        ru, ua = g > 0.5, g < 0.5
        nb = np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb |= np.roll(ua, sh, axis=(0, 1))
        edge = ru & nb
        if not edge.any():
            return np.full(self.nU, np.nan, np.float32)
        d = ndimage.distance_transform_edt(~edge) * self.km
        return np.where(ru, d, -d)[self.jj, self.ii].astype(np.float32)

    def full(self, vZ, fill=0.0):
        """Vecteur sur Z → vecteur sur U (fill ailleurs)."""
        out = np.full(self.nU, fill, np.float32)
        out[self.Zi] = vZ
        return out

    # ---------- construction ----------
    def _codes(self):
        """États par localité (DeepState, ISW, Wikipédia) de chaque mois construit, à partir des différences publiées."""
        FR = self.FR
        cur = np.zeros(self.nPlaces, np.int64)
        codes = {}
        for i in range(self.NM):
            mo = FR["months"][i] if i < len(FR["months"]) else None
            if not mo or "d" not in mo:
                continue
            d = np.asarray(mo["d"], np.int64).reshape(-1, 2)
            if len(d):
                cur[d[:, 0]] = d[:, 1]
            codes[i] = np.stack([(cur >> (2 * s)) & 3 for s in range(3)], 1).astype(np.uint8)
        return codes

    def _build(self):
        NOW = decode_seq(load_seq(os.path.join(SITE, "front_nowcast.js")), self.nU)
        EQ = decode_seq(load_seq(os.path.join(SITE, "front_equilibre.js")), self.nU)
        Peq = np.full((self.NM, self.nU), np.nan, np.float32)
        Pnow = np.full((self.NM, self.nU), np.nan, np.float32)
        for t in range(self.NM):
            if t < len(NOW) and NOW[t] is not None:
                Pnow[t] = NOW[t] / 63.0
            if t < len(EQ) and EQ[t] is not None:
                Peq[t] = EQ[t] / 63.0
        self.codes = self._codes()
        # ISW arrêtée (témoin absent, comme `kpi_avance.py`) : âge publié (jours depuis son dernier changement alors
        # qu'une autre carte a changé) > 45 jours sans interruption jusqu'au dernier mois construit
        age = {t: mo["age"].get("isw") for t, mo in enumerate(self.FR["months"][:self.NM]) if mo and "age" in mo}
        self.isw_live = np.array([t in age and age[t] is not None for t in range(self.NM)])
        for t in sorted(age, reverse=True):
            if age[t] is None or age[t] <= ISW_STALE_DAYS:
                break
            self.isw_live[t] = False
        T = np.full((self.NM, self.nU), -1, np.int8)
        ua = self.cellU >= 0
        for t, c in self.codes.items():
            ru_v = (c == 2).sum(1)
            ua_v = (c == 1).sum(1)
            # localité absente des différences publiées : codes 0 ou 1 seulement, donc ukrainienne pour toute carte
            # qui la couvre (vérifié : 99,996 % d'accord avec le consensus de recherche, 3 mailles sur 80 360 diffèrent)
            cons = np.where(ru_v > ua_v, 1, np.where((ua_v > ru_v) | (c == 0).all(1), 0, -1)).astype(np.int8)
            T[t, ua] = cons[self.cellU[ua]]
        SDe = np.full((self.NM, self.nU), np.nan, np.float32)
        SDn = np.full((self.NM, self.nU), np.nan, np.float32)
        for t in range(self.NM):
            if np.isfinite(Peq[t]).any():
                SDe[t] = self.sd_of(Peq[t])
            if np.isfinite(Pnow[t]).any():
                SDn[t] = self.sd_of(Pnow[t])
        near = (np.nan_to_num(np.abs(SDe), nan=999) <= ZONE_KM).any(0) | (np.nan_to_num(np.abs(SDn), nan=999) <= ZONE_KM).any(0)
        self.Zi = np.flatnonzero(near)
        self.Peq, self.Pnow, self.T = Peq[:, self.Zi], Pnow[:, self.Zi], T[:, self.Zi]
        self.SDe, self.SDn = SDe[:, self.Zi], SDn[:, self.Zi]

    # ---------- tronçons ----------
    def _tiles_Y(self):
        self.Y = {}
        for t in self.built:
            sd = np.nan_to_num(self.SDe[t], nan=999)
            nr = np.abs(sd) <= 20
            known = self.T[t] >= 0
            cn = np.bincount(self.tinv, weights=nr, minlength=self.nT)
            ck = np.bincount(self.tinv, weights=known, minlength=self.nT)
            ry = np.bincount(self.tinv, weights=(self.T[t] == 1), minlength=self.nT)
            nmin = max(5.0, 20.0 * TILE_N)          # 20 mailles pour un tronçon de 24 km (v1), au prorata sinon
            ok = (cn >= nmin) & (ck >= nmin)
            for j in np.flatnonzero(ok):
                self.Y[(t, int(j))] = float(ry[j]) * self.km ** 2 / (TILE * self.km)

    def tile_mean(self, t, xZ, side=None, maxd=20.0):
        """Moyenne de xZ (vecteur sur Z) par tronçon sur les mailles à ≤ maxd km de la ligne des cartes ;
        side = 'ru' / 'ua' pour un seul côté. Renvoie (moyenne par tronçon, effectif)."""
        sd = np.nan_to_num(self.SDe[t], nan=999)
        m = np.abs(sd) <= maxd
        if side == "ru":
            m &= sd > 0
        elif side == "ua":
            m &= sd <= 0
        w = m.astype(np.float64)
        s = np.bincount(self.tinv, weights=w * np.nan_to_num(xZ), minlength=self.nT)
        n = np.bincount(self.tinv, weights=w, minlength=self.nT)
        return s / np.maximum(n, 1), n

    def month_end(self, t):
        y, m = map(int, self.months[t].split("-"))
        return (dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()


_S = None


def socle():
    global _S
    if _S is None:
        _S = Socle()
    return _S
