"""Lecture (seule) des champs de contrôle du projet : grille LAEA 2 km, p = probabilité de contrôle russe.

Porté de ukraine-recherche/sar/frontlib.py (E3b, 25/10/2026) pour le refresh CLOUD du signal S2 v1 du détecteur.
Aucune écriture dans le projet. Lit site/front.js, site/data.js, site/front_equilibre.js du DÉPÔT (in-repo, versionné).
"""
import base64
import json
import os

import numpy as np

# Dépôt ukraine-feux (scripts/impacts_v1/ -> scripts/ -> dépôt). Surcharge possible par SAR_FRONT_ROOT.
ROOT = os.environ.get("SAR_FRONT_ROOT",
                      os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def _load_frontp(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


F = _load_js(os.path.join(ROOT, "site", "front.js"))
G = F["grid"]
NX, NY, SIZE, X0, Y1 = G["nx"], G["ny"], G["size"], G["x0"], G["y1"]
LON0, LAT0, R = G["lon0"], G["lat0"], G["R"]
MONTHS = _load_js(os.path.join(ROOT, "site", "data.js"))["months"]

# proj4 équivalent (sphère de rayon R)
PROJ4 = f"+proj=laea +lat_0={LAT0} +lon_0={LON0} +x_0=0 +y_0=0 +R={R} +units=m +no_defs"


def cell_place():
    cp = np.zeros(NX * NY, dtype=np.int32)
    b = base64.b64decode(F["runs"])
    o = 0
    for k in range(0, len(b), 3):
        v = b[k] | b[k + 1] << 8
        n = b[k + 2]
        cp[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
        o += n
    return cp.reshape(NY, NX)


def fields(view="equilibre"):
    """Renvoie {mois: tableau NY×NX de p (NaN hors domaine)}."""
    cp = cell_place().ravel()
    ucells = np.nonzero(cp != -1)[0]
    enc = _load_frontp(os.path.join(ROOT, "site", f"front_{view}.js"))
    out, prev = {}, np.zeros(len(ucells), dtype=np.uint8)
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
        g = np.full(NX * NY, np.nan, dtype=np.float32)
        g[ucells] = q / 63.0
        out[MONTHS[t] if t < len(MONTHS) else t] = g.reshape(NY, NX)
    return out, len(enc)


def laea(lon, lat):
    l, p, l0, p0 = map(np.radians, (lon, lat, LON0, LAT0))
    k = np.sqrt(2 / (1 + np.sin(p0) * np.sin(p) + np.cos(p0) * np.cos(p) * np.cos(l - l0)))
    return R * k * np.cos(p) * np.sin(l - l0), R * k * (np.cos(p0) * np.sin(p) - np.sin(p0) * np.cos(p) * np.cos(l - l0))


def cell_of(lon, lat):
    x, y = laea(lon, lat)
    return int((Y1 - y) // SIZE), int((x - X0) // SIZE)   # (j, i)
