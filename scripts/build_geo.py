#!/usr/bin/env python3
"""Fond de carte du projet « Ukraine : la guerre vue du ciel », d'après Natural Earth (domaine public).

À lancer une fois (le résultat est versionné) :
    python scripts/build_efa_geo.py <dossier des geojson Natural Earth>

Fichiers attendus dans le dossier (https://github.com/nvkelso/natural-earth-vector/tree/master/geojson) :
ne_50m_admin_0_countries, ne_10m_land, ne_10m_admin_0_boundary_lines_land, ne_10m_admin_1_states_provinces,
ne_10m_populated_places, ne_10m_rivers_lake_centerlines, ne_10m_lakes (.geojson).

Produit :
  site/geo.js          fond de carte (pays, terres, frontières, régions, fleuves, lacs, villes)
  data/regions.json    oblasts d'Ukraine et régions russes frontalières, pour rattacher les détections
"""
import hashlib
import json
import re
import os
import sys

SRC = sys.argv[1]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOX = (14.0, 39.5, 49.0, 58.5)  # lon min, lat min, lon max, lat max : Ukraine et alentours
ADM1 = {"UKR", "RUS", "BLR", "POL", "ROU", "MDA", "SVK", "HUN"}  # Ukraine et voisins
UA_NAMES = {"UA-74": "Tchernihiv", "UA-07": "Volhynie", "UA-56": "Rivne", "UA-18": "Jytomyr", "UA-32": "Kyiv (oblast)",
            "UA-21": "Transcarpatie", "UA-77": "Tchernivtsi", "UA-26": "Ivano-Frankivsk", "UA-51": "Odesa",
            "UA-05": "Vinnytsia", "UA-46": "Lviv", "UA-59": "Soumy", "UA-63": "Kharkiv", "UA-09": "Louhansk",
            "UA-14": "Donetsk", "UA-65": "Kherson", "UA-23": "Zaporijjia", "UA-48": "Mykolaïv", "UA-53": "Poltava",
            "UA-68": "Khmelnytskyï", "UA-61": "Ternopil", "UA-12": "Dnipropetrovsk", "UA-71": "Tcherkassy",
            "UA-35": "Kirovohrad", "UA-30": "Kyiv (ville)", "UA-43": "Crimée", "UA-40": "Sébastopol"}
# Régions russes frontalières (zone d'étude)
RU_NAMES = {"RU-BRY": "Briansk", "RU-KRS": "Koursk", "RU-BEL": "Belgorod", "RU-VOR": "Voronej", "RU-ROS": "Rostov",
            "RU-KDA": "Krasnodar"}


def load(name):
    with open(os.path.join(SRC, name + ".geojson"), encoding="utf-8") as fh:
        return json.load(fh)["features"]


def clip_ring(ring, box=BOX):
    """Sutherland-Hodgman sur le rectangle de la zone."""
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
             (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))]
    pts = [tuple(p[:2]) for p in ring]
    for inside, cut in edges:
        if not pts:
            break
        out, prev = [], pts[-1]
        for cur in pts:
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
            prev = cur
        pts = out
    return pts


def dp(pts, tol):
    """Douglas-Peucker itératif."""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    t2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        best, idx = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, idx = d, i
        if best > t2:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def area(r):
    return sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r))) / 2


def rnd(r, nd):
    out = []
    for x, y in r:
        p = [round(x, nd), round(y, nd)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def polys(geom):
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"] if geom["type"] == "MultiPolygon" else []


def lines(geom):
    return [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"] if geom["type"] == "MultiLineString" else []


def poly_geom(geom, tol, nd, clip=True, min_area=0.0):
    """Polygones découpés, simplifiés, anneaux extérieurs dans le sens horaire (convention d3)."""
    out = []
    for poly in polys(geom):
        rings = []
        for k, ring in enumerate(poly):
            r = clip_ring(ring) if clip else [tuple(p[:2]) for p in ring]
            if len(r) < 4:
                if k == 0:
                    break
                continue
            r = dp(r + [r[0]] if r[0] != r[-1] else r, tol)
            r = rnd(r, nd)
            if len(r) < 4 or (k == 0 and abs(area(r)) < min_area):
                if k == 0:
                    break
                continue
            a = area(r)  # avec cette formule, sens horaire (attendu par d3 pour l'anneau extérieur) ⇔ aire positive
            if (k == 0 and a < 0) or (k > 0 and a > 0):
                r = r[::-1]
            if r[0] != r[-1]:
                r.append(r[0])
            rings.append(r)
        if rings:
            out.append(rings)
    return {"type": "MultiPolygon", "coordinates": out} if out else None


def line_geom(geom, tol, nd):
    x0, y0, x1, y1 = BOX
    out = []
    for ln in lines(geom):
        seg = []
        for p in ln + [None]:
            if p is not None and x0 - 1 <= p[0] <= x1 + 1 and y0 - 1 <= p[1] <= y1 + 1:
                seg.append(tuple(p[:2]))
                continue
            if len(seg) >= 2:
                s = rnd(dp(seg, tol), nd)
                if len(s) >= 2:
                    out.append(s)
            seg = []
    return {"type": "MultiLineString", "coordinates": out} if out else None


def bbox_hit(geom):
    if not geom:
        return False
    x0, y0, x1, y1 = BOX
    xs, ys = [], []

    def walk(c):
        if not c:
            return
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(geom["coordinates"])
    return xs and min(xs) <= x1 and max(xs) >= x0 and min(ys) <= y1 and max(ys) >= y0


def feat(geom, props):
    return {"type": "Feature", "properties": props, "geometry": geom}


def main():
    countries = []
    for f in load("ne_50m_admin_0_countries"):
        p = f["properties"]
        if bbox_hit(f["geometry"]):
            g = poly_geom(f["geometry"], 0.02, 3, min_area=0.002)
            if g:
                countries.append(feat(g, {"iso": p.get("ISO_A2_EH") or p.get("ISO_A2"), "n": p.get("NAME_FR") or p.get("NAME")}))
    land = [feat(g, {}) for g in (poly_geom(f["geometry"], 0.004, 3, min_area=0.0005) for f in load("ne_10m_land")
                                  if bbox_hit(f["geometry"])) if g]
    borders = [feat(g, {}) for g in (line_geom(f["geometry"], 0.003, 3) for f in load("ne_10m_admin_0_boundary_lines_land")
                                     if bbox_hit(f["geometry"])) if g]
    regions, assign = [], []
    for f in load("ne_10m_admin_1_states_provinces"):
        p = f["properties"]
        code = p.get("iso_3166_2")
        name = UA_NAMES.get(code) or RU_NAMES.get(code)
        if p.get("adm0_a3") not in ADM1 or not bbox_hit(f["geometry"]):
            continue
        g = poly_geom(f["geometry"], 0.006, 3)
        if g:
            regions.append(feat(g, {"c": p["adm0_a3"], "id": code if name else None, "n": name}))
        if name:
            ga = poly_geom(f["geometry"], 0.004, 3, clip=False)
            assign.append(feat(ga, {"id": code, "n": name, "c": p["adm0_a3"]}))
    rivers = [feat(g, {"r": f["properties"]["scalerank"]}) for f in load("ne_10m_rivers_lake_centerlines")
              if (f["properties"].get("scalerank") or 99) <= 8 and f["properties"].get("featurecla") != "Lake Centerline"
              and bbox_hit(f["geometry"]) for g in [line_geom(f["geometry"], 0.005, 3)] if g]
    lakes = [feat(g, {}) for f in load("ne_10m_lakes") if (f["properties"].get("scalerank") or 99) <= 7 and bbox_hit(f["geometry"])
             for g in [poly_geom(f["geometry"], 0.004, 3, min_area=0.001)] if g]
    x0, y0, x1, y1 = BOX
    cities = []
    for f in load("ne_10m_populated_places"):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        if x0 <= lon <= x1 and y0 <= lat <= y1 and p.get("SCALERANK", 99) <= 9:
            name = p.get("NAME_FR") or p.get("NAME")
            if p.get("ADM0_A3") == "UKR" and name in ("Kiev", "Kyiv"):
                name = "Kyiv"
            cities.append([round(lon, 3), round(lat, 3), name, p["SCALERANK"], 1 if p.get("FEATURECLA", "").startswith("Admin-0 capital") else 0])
    cities.sort(key=lambda c: (c[3], -c[4]))
    geo = {"countries": countries, "land": land, "borders": borders, "regions": regions, "rivers": rivers, "lakes": lakes, "cities": cities}
    js = "window.GEO=" + json.dumps(geo, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(os.path.join(ROOT, "site", "geo.js"), "w", encoding="utf-8") as fh:
        fh.write(js)
    page = os.path.join(ROOT, "site", "index.html")
    if os.path.exists(page):
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="geo\.js(\?v=[\w-]+)?"', f'src="geo.js?v={hashlib.sha1(js.encode()).hexdigest()[:10]}"', src)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    with open(os.path.join(ROOT, "data", "regions.json"), "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": assign}, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"geo.js : {len(js) // 1024} Ko ({len(countries)} pays, {len(regions)} régions dont {len(assign)} nommées, "
          f"{len(rivers)} fleuves, {len(lakes)} lacs, {len(cities)} villes)")


if __name__ == "__main__":
    main()
