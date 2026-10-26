#!/usr/bin/env python3
"""Contours des RAÏONS d'Ukraine (réforme de 2020, 136 raïons) pour un futur choroplèthe au niveau district,
en plus de l'actuel niveau oblast (site/geo.js → window.GEO.regions).

Source : geoBoundaries, édition « gbHumanitarian » UKR ADM2 (districts) et ADM1 (oblasts), qui reprend la
découpe officielle 2020 de la State Scientific Production Enterprise « Kartographia » diffusée par l'OCHA
(Common Operational Datasets). Licence CC BY 3.0 IGO (attribution, dérivés et agrégats autorisés). Fetch par
URL brute GitHub, sans clé ; commit figé pour la reproductibilité (comme les autres scripts build du dépôt).

Produit, dans le MÊME esprit que build_geo.py (geo.js + data/regions.json) :
  site/raions.js         window.RAIONS = {"raions":[Feature…], "meta":{…}} : géométrie simplifiée pour le web,
                         chaque raïon avec un id stable, un nom n, le code oblast parent o (UA-xx) et le pays c.
  data/raions.json       FeatureCollection complète (géométrie un peu moins simplifiée), pour les jointures.
  data/raions_oblast.csv table de contrôle raïon → oblast (id, nom, oblast, nom oblast, spécial).

Le rattachement raïon → oblast se fait par jointure spatiale contre les oblasts ADM1 de la MÊME source
(donc des frontières qui s'emboîtent exactement), puis mappage du nom ADM1 vers le code UA-xx.

Lancement (le résultat est versionné, à relancer seulement si la découpe change) :
    python3 scripts/build_raions.py                # télécharge depuis geoBoundaries
    python3 scripts/build_raions.py --src <dossier> # réutilise adm1.geojson / adm2.geojson déjà téléchargés
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# geoBoundaries, commit figé (gbHumanitarian = Common Operational Dataset de l'OCHA, découpe Kartographia 2020).
GB_COMMIT = "9469f09"
URL = {
    "adm1": f"https://github.com/wmgeolab/geoBoundaries/raw/{GB_COMMIT}/releaseData/gbHumanitarian/UKR/ADM1/geoBoundaries-UKR-ADM1.geojson",
    "adm2": f"https://github.com/wmgeolab/geoBoundaries/raw/{GB_COMMIT}/releaseData/gbHumanitarian/UKR/ADM2/geoBoundaries-UKR-ADM2.geojson",
}
LICENSE = "geoBoundaries gbHumanitarian (OCHA/Kartographia, 2020), CC BY 3.0 IGO"

# Nom ADM1 (romanisation geoBoundaries) → code ISO 3166-2 des oblasts, aligné sur UA_NAMES de build_geo.py.
# « Kyivska » apparaît DEUX fois (l'oblast, plus étendu, et la ville de Kyiv) : départagés par l'aire (voir plus bas).
ADM1_TO_UA = {
    "Avtonomna Respublika Krym": "UA-43", "Cherkaska": "UA-71", "Chernihivska": "UA-74", "Chernivetska": "UA-77",
    "Dnipropetrovska": "UA-12", "Donetska": "UA-14", "Ivano-Frankivska": "UA-26", "Kharkivska": "UA-63",
    "Khersonska": "UA-65", "Khmelnytska": "UA-68", "Kirovohradska": "UA-35", "Luhanska": "UA-09", "Lvivska": "UA-46",
    "Mykolaivska": "UA-48", "Odeska": "UA-51", "Poltavska": "UA-53", "Rivnenska": "UA-56", "Sevastopilska": "UA-40",
    "Sumska": "UA-59", "Ternopilska": "UA-61", "Vinnytska": "UA-05", "Volynska": "UA-07", "Zakarpatska": "UA-21",
    "Zaporizka": "UA-23", "Zhytomyrska": "UA-18",
}
# Pays (a3) par code oblast, pour la propriété c (comme regions de geo.js). De facto : Crimée/Sébastopol côté russe.
C_OF = {"UA-43": "RUS", "UA-40": "RUS"}
# Unités ADM2 qui ne sont PAS des raïons de la réforme 2020 (villes à statut spécial, zone d'exclusion).
SPECIAL = {"Kyivska", "Sevastopilska", "Chornobylska zona vidchuzhennia"}


# ---------------------------------------------------------------------------- géométrie (style build_geo.py)
def polys(geom):
    t = geom["type"]
    return [geom["coordinates"]] if t == "Polygon" else geom["coordinates"] if t == "MultiPolygon" else []


def area(r):
    return sum(r[i][0] * r[i - 1][1] - r[i - 1][0] * r[i][1] for i in range(len(r))) / 2.0


def dp(pts, tol):
    """Douglas-Peucker itératif (identique à build_geo.py)."""
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
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, idx = d, i
        if best > t2:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def rnd(r, nd):
    out = []
    for x, y in r:
        p = [round(x, nd), round(y, nd)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def simplify(geom, tol, nd, min_area=0.0):
    """MultiPolygon simplifié, anneau extérieur horaire (convention d3, aire positive), comme build_geo.poly_geom."""
    out = []
    for poly in polys(geom):
        rings = []
        for k, ring in enumerate(poly):
            r = [tuple(p[:2]) for p in ring]
            if len(r) < 4:
                if k == 0:
                    break
                continue
            if r[0] != r[-1]:
                r = r + [r[0]]
            r = rnd(dp(r, tol), nd)
            if len(r) < 4 or (k == 0 and abs(area(r)) < min_area):
                if k == 0:
                    break
                continue
            a = area(r)
            if (k == 0 and a < 0) or (k > 0 and a > 0):
                r = r[::-1]
            if r[0] != r[-1]:
                r.append(r[0])
            rings.append(r)
        if rings:
            out.append(rings)
    return {"type": "MultiPolygon", "coordinates": out} if out else None


# ---------------------------------------------------------------------------- jointure spatiale raïon → oblast
def bbox(geom):
    xs, ys = [], []
    for poly in polys(geom):
        for x, y in poly[0]:
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys))


def pip(pt, ring):
    """Point dans un anneau (ray casting)."""
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi):
            inside = not inside
        j = i
    return inside


def in_poly(pt, poly):
    if not pip(pt, poly[0]):
        return False
    return not any(pip(pt, h) for h in poly[1:])


def in_feat(pt, geom):
    return any(in_poly(pt, p) for p in polys(geom))


def sample_points(geom, grid=10):
    """Points intérieurs : centroïde de chaque polygone + grille filtrée à l'intérieur (≈ recouvrement d'aire)."""
    pts = []
    for p in polys(geom):
        outer = p[0]
        xs = [q[0] for q in outer]
        ys = [q[1] for q in outer]
        pts.append((sum(xs) / len(xs), sum(ys) / len(ys)))
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        for gx in range(1, grid):
            for gy in range(1, grid):
                c = (x0 + (x1 - x0) * gx / grid, y0 + (y1 - y0) * gy / grid)
                if in_poly(c, p):
                    pts.append(c)
    return pts


def build_oblasts(adm1):
    """Liste (code, aire, bbox, geom simplifié) ; départage les deux « Kyivska » par l'aire (ville < oblast)."""
    items = []
    for f in adm1:
        nm = f["properties"]["shapeName"]
        g = simplify(f["geometry"], 0.0015, 5)  # fin : la jointure doit suivre les frontières dentelées (réservoirs)
        if not g:
            continue
        a = sum(abs(area(p[0])) for p in polys(g))
        items.append([nm, a, g])
    kyiv = sorted([it for it in items if it[0] == "Kyivska"], key=lambda z: z[1])
    out = []
    for it in items:
        nm, a, g = it
        if nm == "Kyivska":
            code = "UA-30" if it is kyiv[0] else "UA-32"  # la plus petite = ville de Kyiv
        else:
            code = ADM1_TO_UA.get(nm)
        if not code:
            raise SystemExit(f"oblast ADM1 non mappé : {nm!r}")
        out.append((code, a, bbox(g), g))
    return out


def assign_oblast(geom, oblasts):
    """Oblast au recouvrement d'aire majoritaire (vote de points) ; départage par la plus petite aire (enclaves) ;
    repli sur l'oblast au sommet le plus proche si aucun point ne tombe dans un oblast."""
    pts = sample_points(geom)
    votes, amap = {}, {}
    for pt in pts:
        for (code, a, bb, g) in oblasts:
            if bb[0] <= pt[0] <= bb[2] and bb[1] <= pt[1] <= bb[3] and in_feat(pt, g):
                votes[code] = votes.get(code, 0) + 1
                amap[code] = a
    if votes:
        mx = max(votes.values())
        return sorted([(amap[c], c) for c, v in votes.items() if v == mx])[0][1], "vote"
    ref = pts[0]
    best, bd = None, 1e18
    for (code, a, bb, g) in oblasts:
        for p in polys(g):
            for x, y in p[0]:
                dd = (x - ref[0]) ** 2 + (y - ref[1]) ** 2
                if dd < bd:
                    bd, best = dd, code
    return best, "near"


# ---------------------------------------------------------------------------- id stable
def slug(name):
    s = name.lower()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# ---------------------------------------------------------------------------- entrées / sorties
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ukraine-map/build_raions (contact: djac57)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def load_source(kind, src_dir):
    if src_dir:
        path = os.path.join(src_dir, f"{kind}.geojson")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["features"]
    return fetch(URL[kind])["features"]


def cache_bust(js, filename):
    """Met à jour le ?v= de <script src=filename> dans index.html (comme build_geo.py pour geo.js)."""
    page = os.path.join(ROOT, "site", "index.html")
    if not os.path.exists(page):
        return
    src = open(page, encoding="utf-8").read()
    pat = re.compile(r'src="' + re.escape(filename) + r'(\?v=[\w-]+)?"')
    if pat.search(src):
        new = pat.sub(f'src="{filename}?v={hashlib.sha1(js.encode()).hexdigest()[:10]}"', src)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="dossier contenant adm1.geojson et adm2.geojson déjà téléchargés")
    ap.add_argument("--tol", type=float, default=0.004, help="tolérance Douglas-Peucker (degrés) pour site/raions.js")
    ap.add_argument("--nd", type=int, default=3, help="décimales conservées pour site/raions.js")
    args = ap.parse_args()

    adm1 = load_source("adm1", args.src)
    adm2 = load_source("adm2", args.src)
    oblasts = build_oblasts(adm1)

    web, full, table, seen, dist = [], [], [], {}, {}
    for f in sorted(adm2, key=lambda z: z["properties"]["shapeName"]):
        p = f["properties"]
        name = p["shapeName"]
        code, mode = assign_oblast(f["geometry"], oblasts)
        sp = 1 if name in SPECIAL else 0
        rid = f"{code}-{slug(name)}"
        if rid in seen:  # garde-fou anti-collision (stable : suffixe déterministe)
            seen[rid] += 1
            rid = f"{rid}-{seen[rid]}"
        else:
            seen[rid] = 1
        props = {"id": rid, "n": name, "o": code, "c": C_OF.get(code, "UKR")}
        if sp:
            props["sp"] = 1
        gw = simplify(f["geometry"], args.tol, args.nd)
        gf = simplify(f["geometry"], 0.0015, 4)  # version data/ un peu plus fine
        web.append({"type": "Feature", "properties": props, "geometry": gw})
        full.append({"type": "Feature", "properties": dict(props, gid=p.get("shapeID")), "geometry": gf})
        table.append((rid, name, code, sp, mode))
        if not sp:
            dist[code] = dist.get(code, 0) + 1

    meta = {"source": LICENSE, "url": URL["adm2"], "commit": GB_COMMIT,
            "n_units": len(web), "n_raions": sum(1 for _, _, _, sp, _ in table if not sp)}
    js = "window.RAIONS=" + json.dumps({"raions": web, "meta": meta}, ensure_ascii=False, separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "raions.js"), "w", encoding="utf-8").write(js)
    cache_bust(js, "raions.js")

    with open(os.path.join(ROOT, "data", "raions.json"), "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": full}, fh, ensure_ascii=False, separators=(",", ":"))

    lines = ["raion_id,raion_name,oblast,oblast_name,special,mode"]
    onames = {"UA-43": "Crimée", "UA-40": "Sébastopol", "UA-30": "Kyiv (ville)", "UA-32": "Kyiv (oblast)"}
    # noms oblast lisibles depuis ADM1_TO_UA inversé + overrides
    inv = {}
    for nm, cc in ADM1_TO_UA.items():
        inv.setdefault(cc, nm)
    for rid, name, code, sp, mode in table:
        on = onames.get(code, inv.get(code, ""))
        lines.append(f"{rid},{name},{code},{on},{sp},{mode}")
    open(os.path.join(ROOT, "data", "raions_oblast.csv"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    near = [t[0] for t in table if t[4] == "near"]
    print(f"site/raions.js : {len(js) // 1024} Ko ; {len(web)} unités ADM2 "
          f"({meta['n_raions']} raïons + {len(web) - meta['n_raions']} unités spéciales)")
    print(f"data/raions.json + data/raions_oblast.csv écrits. Rattachement par repli 'near' : {len(near)} {near}")
    print("Répartition raïons par oblast (hors unités spéciales) :")
    for k in sorted(dist):
        print(f"  {k} {inv.get(k, onames.get(k, ''))}: {dist[k]}")
    print(f"  Total raïons : {sum(dist.values())} sur {len(dist)} oblasts")


if __name__ == "__main__":
    main()
