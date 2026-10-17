"""Couche « infrastructures » : site/infra.js, à partir des fichiers de fetch_infra.py.

    python3 scripts/build_infra.py <dossier_brut>

Infrastructures statiques et publiques uniquement (voies ferrées, gares principales, centrales électriques, grands sites
industriels, cokeries et mines, aéroports et aérodromes), sans aucune information de frappe ni de dommages.
Filtres pour ne pas surcharger : gares des localités d'au moins 20 000 habitants (GeoNames, fond de carte) ; centrales
d'au moins 50 MW (nucléaire et hydraulique toujours) ; zones industrielles de plus de 1 km² ; aéroports OurAirports
(grands et moyens) et aérodromes OpenStreetMap nommés, dédoublonnés à 3 km.
"""
import csv
import io
import json
import math
import os
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S, W, N, E = 44.0, 30.0, 52.5, 41.5


def inside(lon, lat):
    return W <= lon <= E and S <= lat <= N


def dp(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1e-12
    k, dmax = 0, -1.0
    for i in range(1, len(pts) - 1):
        d = abs(dy * (pts[i][0] - a[0]) - dx * (pts[i][1] - a[1])) / L
        if d > dmax:
            k, dmax = i, d
    if dmax <= tol:
        return [a, b]
    return dp(pts[:k + 1], tol)[:-1] + dp(pts[k:], tol)


def shp_lines(zpath):
    """Lecteur minimal de shapefile (polylignes) dans une archive zip."""
    with zipfile.ZipFile(zpath) as z:
        name = [n for n in z.namelist() if n.endswith(".shp")][0]
        b = z.read(name)
    out, pos = [], 100
    while pos < len(b):
        rec_len = struct.unpack(">i", b[pos + 4:pos + 8])[0] * 2
        rec = b[pos + 8:pos + 8 + rec_len]
        pos += 8 + rec_len
        if len(rec) < 44 or struct.unpack("<i", rec[:4])[0] not in (3, 13, 23):
            continue
        nparts, npts = struct.unpack("<ii", rec[36:44])
        parts = list(struct.unpack(f"<{nparts}i", rec[44:44 + 4 * nparts]))
        p0 = 44 + 4 * nparts
        pts = [struct.unpack("<dd", rec[p0 + 16 * i:p0 + 16 * i + 16]) for i in range(npts)]
        for a, c in zip(parts, parts[1:] + [npts]):
            line = [(round(x, 3), round(y, 3)) for x, y in pts[a:c]]
            if any(inside(x, y) for x, y in line):
                out.append(line)
    return out


def area_km2(ring):
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        s += x0 * kx * y1 * ky - x1 * kx * y0 * ky
    return abs(s) / 2


def osm(path):
    return json.load(open(path, encoding="utf-8")).get("elements", []) if os.path.exists(path) else []


def centre(el):
    if "lat" in el:
        return el["lon"], el["lat"]
    if "center" in el:
        return el["center"]["lon"], el["center"]["lat"]
    g = el.get("geometry") or []
    if g:
        return sum(p["lon"] for p in g) / len(g), sum(p["lat"] for p in g) / len(g)
    b = el.get("bounds")
    return ((b["minlon"] + b["maxlon"]) / 2, (b["minlat"] + b["maxlat"]) / 2) if b else (None, None)


def name_of(tags):
    return tags.get("name:fr") or tags.get("name:en") or tags.get("name:uk") or tags.get("name") or ""


def main():
    raw = os.path.join(sys.argv[1] if len(sys.argv) > 1 else "raw", "infra")
    BM = json.loads(open(os.path.join(ROOT, "site", "basemap.js"), encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))
    towns = [p for p in BM.get("places", []) if len(p) > 3 and (p[3] or 0) >= 20000]
    out = {"sources": {
        "rail": "Natural Earth 10m railroads (domaine public) ; OpenStreetMap usage=main (ODbL)",
        "plants": "WRI Global Power Plant Database v1.3.0, 2021 (CC BY 4.0)",
        "airports": "OurAirports (domaine public) ; OpenStreetMap (ODbL)",
        "osm": "© contributeurs OpenStreetMap (ODbL) : gares, zones industrielles, cokeries, mines, aérodromes"}}
    # voies ferrées
    rail = []
    zp = os.path.join(raw, "ne_10m_railroads.zip")
    if os.path.exists(zp):
        rail += [dp(l, 0.004) for l in shp_lines(zp)]
    ne_n = len(rail)
    for el in osm(os.path.join(raw, "osm_rail_main.json")):
        g = el.get("geometry") or []
        line = [(round(p["lon"], 3), round(p["lat"], 3)) for p in g]
        if len(line) >= 2:
            rail.append(dp(line, 0.004))
    out["rail"] = [[c for p in l for c in p] for l in rail if len(l) >= 2]
    # gares des localités d'au moins 20 000 habitants (une par localité, la plus proche du centre)
    best = {}
    for el in osm(os.path.join(raw, "osm_stations.json")):
        lon, lat = centre(el)
        if lon is None:
            continue
        for t in towns:
            d = math.hypot((t[0] - lon) * 68, (t[1] - lat) * 111)
            if d <= 4 and (t[2] not in best or d < best[t[2]][0]):
                best[t[2]] = (d, round(lon, 3), round(lat, 3), name_of(el.get("tags", {})) or t[2], t[3])
    out["stations"] = [[v[1], v[2], v[3]] for v in sorted(best.values(), key=lambda v: -v[4])]
    # centrales électriques
    plants = []
    zp = os.path.join(raw, "gppd.zip")
    if os.path.exists(zp):
        with zipfile.ZipFile(zp) as z:
            name = [n for n in z.namelist() if n.endswith(".csv") and "global_power_plant_database" in n][0]
            for r in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8")):
                if r["country"] not in ("UKR", "RUS"):
                    continue
                lon, lat, mw = float(r["longitude"]), float(r["latitude"]), float(r["capacity_mw"] or 0)
                if inside(lon, lat) and (mw >= 50 or r["primary_fuel"] in ("Nuclear", "Hydro")):
                    plants.append([round(lon, 3), round(lat, 3), r["name"], r["primary_fuel"], round(mw)])
    out["plants"] = plants
    # zones industrielles de plus de 1 km² (contour simplifié)
    ind = []
    for el in osm(os.path.join(raw, "osm_industrial.json")):
        rings = []
        if el["type"] == "way" and el.get("geometry"):
            rings.append([(p["lon"], p["lat"]) for p in el["geometry"]])
        elif el["type"] == "relation":
            for mem in el.get("members", []):
                if mem.get("role") == "outer" and mem.get("geometry"):
                    rings.append([(p["lon"], p["lat"]) for p in mem["geometry"]])
        for ring in rings:
            if len(ring) < 4:
                continue
            a = area_km2(ring)
            if a >= 1.0:
                r2 = dp([(round(x, 4), round(y, 4)) for x, y in ring], 0.002)
                ind.append([round(a, 1), name_of(el.get("tags", {})), [c for p in r2 for c in p]])
    out["industrial"] = ind
    heavy = []
    for el in osm(os.path.join(raw, "osm_heavy.json")):
        lon, lat = centre(el)
        nm = name_of(el.get("tags", {}))
        if lon is not None and nm:
            tg = el.get("tags", {})
            kind = tg.get("industrial") or tg.get("product") or tg.get("resource") or "industrie"
            heavy.append([round(lon, 3), round(lat, 3), nm, kind])
    out["heavy"] = heavy
    # aéroports et aérodromes (statiques, publics), dédoublonnés à 3 km
    air = []
    p = os.path.join(raw, "airports.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r["type"] in ("large_airport", "medium_airport") and r["iso_country"] in ("UA", "RU"):
                lon, lat = float(r["longitude_deg"]), float(r["latitude_deg"])
                if inside(lon, lat):
                    air.append([round(lon, 3), round(lat, 3), r["name"], "aéroport"])
    for el in osm(os.path.join(raw, "osm_aero.json")):
        lon, lat = centre(el)
        tg = el.get("tags", {})
        nm = name_of(tg)
        if lon is None or not nm:
            continue
        if any(math.hypot((a[0] - lon) * 68, (a[1] - lat) * 111) < 3 for a in air):
            continue
        air.append([round(lon, 3), round(lat, 3), nm, "base aérienne" if tg.get("military") == "airfield" or tg.get("aerodrome:type") == "military" else "aérodrome"])
    out["airports"] = air
    js = "window.INFRA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "infra.js"), "w", encoding="utf-8").write(js)
    print(f"infra.js : {len(js) // 1024} Ko · voies ferrées {len(out['rail'])} tronçons (dont Natural Earth {ne_n}) · gares {len(out['stations'])} · "
          f"centrales {len(plants)} · zones industrielles > 1 km² {len(ind)} · sites lourds {len(heavy)} · aéroports et aérodromes {len(air)}")


if __name__ == "__main__":
    main()
