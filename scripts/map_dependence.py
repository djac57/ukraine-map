"""Indicateur INTERNE de « dépendance aux cartes » (non affiché sur le site) : à quel point le champ publié (nowcast,
« avec signaux de combat ») s'écarte du champ des cartes seules (équilibré), dans la bande de ±30 km autour de la ligne
50 % publiée, sur les mailles ukrainiennes. Plus les mesures montent, moins le résultat dépend des cartes.

  (a) part de l'information hors cartes : moyenne |logit p_final − logit p_cartes| / moyenne |logit p_final|
  (b) part des mailles dont l'état (russe p > 0,95 / gris / ukrainien p < 0,05) diffère entre cartes et final
  (c) écart moyen de position entre les lignes 50 % des cartes et du final (km, moyenne symétrique des distances)

Calculé sur les 12 derniers instantanés mensuels et sur le dernier.
    python3 scripts/map_dependence.py [dossier_site]          (défaut : site/)
"""
import base64
import json
import os
import sys

import numpy as np
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_meta(site):
    s = open(os.path.join(site, "front.js"), encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))


def load_seq(site, view):
    s = open(os.path.join(site, f"front_{view}.js"), encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].rstrip().rstrip(";"))


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


def measures(site=os.path.join(ROOT, "site"), last=12):
    FR = load_meta(site)
    nx, ny, km = FR["grid"]["nx"], FR["grid"]["ny"], FR["grid"]["size"] / 1000
    place = np.zeros(nx * ny, np.int32)
    b, o = base64.b64decode(FR["runs"]), 0
    for i in range(0, len(b), 3):
        v, n = b[i] | b[i + 1] << 8, b[i + 2]
        place[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
        o += n
    U = np.flatnonzero(place != -1)
    ua = place[U] >= 0
    E = decode_seq(load_seq(site, "equilibre"), len(U))
    N = decode_seq(load_seq(site, "nowcast"), len(U))
    frames = [k for k, m in enumerate(FR["months"]) if m and len(m.get("date", "")) >= 7 and E[k] is not None and N[k] is not None]
    sel = frames[-last:]

    inb = np.zeros(nx * ny, bool)
    inb[U[ua]] = True
    inb = inb.reshape(ny, nx)

    def grid(v):
        g = np.full(nx * ny, np.nan, np.float32)
        g[U] = v
        return g.reshape(ny, nx)

    def edge(g):
        ru, uaa = g > 0.5, g < 0.5
        e = np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            e |= ru & np.roll(uaa, sh, axis=(0, 1))
        return e
    rows = []
    for k in sel:
        pc, pf = E[k] / 63.0, N[k] / 63.0
        gf, gc = grid(pf), grid(pc)
        ef, ec = edge(gf), edge(gc)
        df = (ndimage.distance_transform_edt(~ef) * km).ravel()[U]
        band = ua & (df <= 30)
        lg = lambda p: np.log(np.clip(p, 0.01, 0.99) / (1 - np.clip(p, 0.01, 0.99)))  # noqa: E731
        a = float(np.abs(lg(pf) - lg(pc))[band].mean() / max(np.abs(lg(pf))[band].mean(), 1e-9))
        st = lambda p: np.where(p > 0.95, 2, np.where(p < 0.05, 0, 1))  # noqa: E731
        bb = float((st(pf) != st(pc))[band].mean())
        dc = ndimage.distance_transform_edt(~ec) * km
        dfg = ndimage.distance_transform_edt(~ef) * km
        c = float(np.mean([dc[ef & inb].mean() if (ef & inb).any() else np.nan, dfg[ec & inb].mean() if (ec & inb).any() else np.nan]))
        rows.append((FR["months"][k]["date"], a, bb, c))
    return rows


def summary(rows):
    A = np.array([r[1:] for r in rows])
    return {"months": [rows[0][0], rows[-1][0]], "a_hors_cartes": round(float(A[:, 0].mean()), 4), "b_etat_change": round(float(A[:, 1].mean()), 4),
            "c_ligne_km": round(float(np.nanmean(A[:, 2])), 3), "dernier": {"date": rows[-1][0], "a": round(rows[-1][1], 4), "b": round(rows[-1][2], 4), "c_km": round(rows[-1][3], 3)}}


if __name__ == "__main__":
    site = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "site")
    r = measures(site)
    print("dépendance aux cartes (interne) : " + json.dumps(summary(r), ensure_ascii=False))
