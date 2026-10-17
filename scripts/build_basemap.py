"""Fond de carte détaillé, chargé en différé : grands axes routiers et localités.

    python3 scripts/build_basemap.py <osm_roads.json> <cities1000.zip>

- Routes : OpenStreetMap (© contributeurs OpenStreetMap, ODbL), voies motorway / trunk / primary de la
  zone d'étude (export Overpass), simplifiées (Douglas-Peucker, ~400 m), avec leur numéro (M-, H-, P-…).
- Localités : GeoNames cities1000 (CC BY 4.0), Ukraine et Russie dans la zone d'étude, avec leur population ;
  noms en translittération française de la forme officielle latine.
Sortie : site/basemap.js (window.BASEMAP = {roads, labels, places}).
"""
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAT, LON = (44.0, 53.6), (22.0, 41.5)
TOL = 0.004  # tolérance de simplification (degrés, ~400 m)


def dp(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1e-12
    imax, dmax = 0, 0.0
    for i in range(1, len(pts) - 1):
        d = abs(dy * (pts[i][0] - a[0]) - dx * (pts[i][1] - a[1])) / n
        if d > dmax:
            imax, dmax = i, d
    if dmax <= tol:
        return [a, b]
    return dp(pts[: imax + 1], tol)[:-1] + dp(pts[imax:], tol)


def norm_ref(ref):
    """« М-05;E95 » -> « M-05 » (on garde le numéro national, lettres latines)."""
    for part in re.split(r"[;,/]", ref or ""):
        p = part.strip().upper().translate(str.maketrans("МНРТАЕ", "MHPTAE")).replace(" ", "-")
        m = re.match(r"^([MHPTA])-?(\d+)", p)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(2)}"
    return None


def fr_translit(name):
    """Translittération officielle latine (ukrainienne ou russe) -> usage français courant."""
    rules = {"Shch": "Chtch", "shch": "chtch", "Zh": "J", "zh": "j", "Ch": "Tch", "ch": "tch", "Sh": "Ch", "sh": "ch",
             "Yu": "Iou", "yu": "iou", "iu": "iou", "Ya": "Ia", "ya": "ia", "Ye": "Ie", "ye": "ie", "Kh": "Kh", "kh": "kh",
             "ou": "ou", "u": "ou", "U": "Ou", "ʹ": "", "’": "", "'": ""}
    pat = re.compile("|".join(sorted(map(re.escape, rules), key=len, reverse=True)))
    s = pat.sub(lambda m: rules[m.group(0)], name)  # une seule passe : pas de double substitution
    s = s.replace("oou", "ou").replace("Oou", "Ou").replace("ctch", "tch")
    s = re.sub(r"yy\b", "y", s)                      # Kryvyy -> Kryvy
    s = re.sub(r"([aeo])yi", r"\1ï", s)               # Mykolayiv -> Mykolaïv
    s = s.replace("-on-Don", "-sur-le-Don")
    return s


def main():
    roads_path = sys.argv[1]
    cities_zip = sys.argv[2]
    data = json.load(open(roads_path))
    lines = defaultdict(list)  # (classe, ref) -> segments
    for el in data["elements"]:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        hw = el["tags"].get("highway")
        cls = 1 if hw in ("motorway", "trunk") else 2
        ref = norm_ref(el["tags"].get("ref"))
        if cls == 2 and not ref:
            continue  # routes principales sans numéro : on les laisse de côté
        pts = [(g["lon"], g["lat"]) for g in el["geometry"]]
        lines[(cls, ref or "")].append(pts)
    roads, labels = [], []
    for (cls, ref), segs in lines.items():
        # assemble les tronçons bout à bout quand ils se touchent
        segs = [s for s in segs if len(s) >= 2]
        merged = []
        while segs:
            cur = segs.pop()
            changed = True
            while changed:
                changed = False
                for i, s in enumerate(segs):
                    if s[0] == cur[-1]:
                        cur = cur + s[1:]
                    elif s[-1] == cur[0]:
                        cur = s + cur[1:]
                    elif s[-1] == cur[-1]:
                        cur = cur + s[::-1][1:]
                    elif s[0] == cur[0]:
                        cur = s[::-1] + cur[1:]
                    else:
                        continue
                    segs.pop(i)
                    changed = True
                    break
            merged.append(cur)
        total = 0.0
        for line in merged:
            simp = dp(line, TOL)
            if len(simp) < 2:
                continue
            length = sum(math.hypot(simp[i + 1][0] - simp[i][0], simp[i + 1][1] - simp[i][1]) for i in range(len(simp) - 1))
            if length < 0.02:
                continue
            total += length
            flat = [round(v, 3) for p in simp for v in p]
            roads.append([cls, ref, flat])
            if ref and length > 0.35:  # un numéro par tronçon long
                mid = simp[len(simp) // 2]
                labels.append([round(mid[0], 3), round(mid[1], 3), ref, cls])
    # localités
    places = []
    with zipfile.ZipFile(cities_zip) as z:
        with z.open("cities1000.txt") as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"):
                cc, lat, lon = row[8], float(row[4]), float(row[5])
                if cc not in ("UA", "RU") or not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
                    continue
                pop = int(row[14] or 0)
                if pop < 1000 or row[7] == "PPLX":  # PPLX : quartier d'une ville
                    continue
                places.append([round(lon, 3), round(lat, 3), fr_translit(row[1]), pop])
    places.sort(key=lambda p: -p[3])
    out = {"roads": roads, "labels": labels, "places": places,
           "credit": "Routes © contributeurs OpenStreetMap (ODbL) ; localités GeoNames (CC BY 4.0)"}
    js = "window.BASEMAP=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";"
    path = os.path.join(ROOT, "site", "basemap.js")
    open(path, "w", encoding="utf-8").write(js)
    digest = hashlib.sha1(js.encode()).hexdigest()[:10]
    page = os.path.join(ROOT, "site", "index.html")
    s = open(page, encoding="utf-8").read()
    s2 = re.sub(r'const BASEMAP_V = "[\w-]*"', f'const BASEMAP_V = "{digest}"', s)
    if s2 != s:
        open(page, "w", encoding="utf-8").write(s2)
    print(f"basemap.js : {len(js) // 1024} Ko · {len(roads)} tronçons ({sum(1 for r in roads if r[0] == 1)} M/H principaux), "
          f"{len(labels)} numéros, {len(places)} localités ≥ 1 000 hab.")


if __name__ == "__main__":
    main()
