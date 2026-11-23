"""Couche « Fortifications (@Playfra) » : fossés antichars, dents de dragon et barbelés tracés par Playfra
(X @Playfra0, Telegram t.me/PlayfraOSINT ; dépôt https://github.com/playframap/playframap.github.io).

Accord écrit de Playfra (message privé X, 22-23/11/2026) : réutilisation du GeoJSON autorisée, crédit = lien vers son X
et/ou sa chaîne Telegram. Les objets ne sont PAS datés (instantané du jour du téléchargement).

Sortie : site/fortifs_playfra.js (window.FORTIFS) et data/fortifs_playfra.json (même contenu, lu par sectors.py).
Lignes simplifiées (Douglas-Peucker, 15 m), coordonnées en 1e-5 degré, codées en différences successives :
  types[t] = [[x0, y0, dx1, dy1, dx2, dy2, ...], ...]   (x = lon·1e5, y = lat·1e5)

Usage : python scripts/build_fortifs.py [--src DOSSIER_LOCAL]   (sans --src : téléchargement depuis GitHub)
"""
import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "https://raw.githubusercontent.com/playframap/playframap.github.io/main/{}"
FILES = {"ditch": "ditches.geojson", "teeth": "teeth.geojson", "wire": "wire.geojson"}
TOL_M = 15.0
KLAT = 111_320.0


def load(src, name):
    if src:
        with open(os.path.join(src, name), encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(RAW.format(name), headers={"User-Agent": "ukraine-front-map"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def dp(pts, tol):
    """Douglas-Peucker itératif sur des points (x, y) en mètres ; renvoie les indices gardés."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L
            if d > best:
                best, bi = d, i
        if best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [i for i in range(n) if keep[i]]


def lines_of(fc):
    for f in fc.get("features", []):
        g = f.get("geometry")
        if not g:
            continue
        if g["type"] == "LineString":
            yield g["coordinates"]
        elif g["type"] == "MultiLineString":
            yield from g["coordinates"]
        elif g["type"] == "Polygon":
            yield from g["coordinates"]
        elif g["type"] == "MultiPolygon":
            for p in g["coordinates"]:
                yield from p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="dossier local contenant ditches/teeth/wire.geojson (sinon GitHub)")
    a = ap.parse_args()
    out = {"src": "Playfra", "x": "https://x.com/Playfra0", "tg": "https://t.me/PlayfraOSINT",
           "repo": "https://github.com/playframap/playframap.github.io",
           "fetched": dt.date.today().isoformat(), "scale": 1e5, "types": {}, "n": {}, "km": {}, "bbox": None}
    bb = [180, 90, -180, -90]
    for t, name in FILES.items():
        fc = load(a.src, name)
        enc, km = [], 0.0
        for ln in lines_of(fc):
            ln = [(float(p[0]), float(p[1])) for p in ln if p and len(p) >= 2]
            if len(ln) < 2:
                continue
            c = math.cos(math.radians(ln[0][1]))
            m = [(x * KLAT * c, y * KLAT) for x, y in ln]
            km += sum(math.hypot(m[i + 1][0] - m[i][0], m[i + 1][1] - m[i][1]) for i in range(len(m) - 1)) / 1000
            idx = dp(m, TOL_M)
            q = [(round(ln[i][0] * 1e5), round(ln[i][1] * 1e5)) for i in idx]
            arr = [q[0][0], q[0][1]]
            for i in range(1, len(q)):
                arr += [q[i][0] - q[i - 1][0], q[i][1] - q[i - 1][1]]
            enc.append(arr)
            for x, y in ln:
                bb = [min(bb[0], x), min(bb[1], y), max(bb[2], x), max(bb[3], y)]
        out["types"][t] = enc
        out["n"][t] = len(enc)
        out["km"][t] = round(km)
        print(f"{t}: {len(enc)} lignes, {km:.0f} km", file=sys.stderr)
    out["bbox"] = [round(v, 3) for v in bb]
    js = json.dumps(out, separators=(",", ":"))
    with open(os.path.join(ROOT, "data", "fortifs_playfra.json"), "w", encoding="utf-8") as f:
        f.write(js)
    with open(os.path.join(ROOT, "site", "fortifs_playfra.js"), "w", encoding="utf-8") as f:
        f.write("window.FORTIFS=" + js + ";\n")
    print(f"site/fortifs_playfra.js : {len(js) / 1e6:.2f} Mo", file=sys.stderr)


if __name__ == "__main__":
    main()
