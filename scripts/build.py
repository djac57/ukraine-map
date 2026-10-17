"""Construit site/data.js : feux en excès depuis le 24 février 2022, normale, référence 2021, régions.

    python3 scripts/build.py <dossier_brut>

Méthode (détaillée sur la page) : grille de 0,1° ; normale par maille et mois calendaire sur 2012-2021 ;
une maille-mois est « en excès » si son compte dépasse la normale de plus de 4 écarts-types (plancher 1),
compte au moins 3 détections et a au moins 3 voisines en excès le même mois.
"""
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fireslib as F  # noqa: E402
import guard  # noqa: E402

RAW = sys.argv[1] if len(sys.argv) > 1 else "raw"
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "site", "data.js")
BASE_YEARS = list(range(2012, 2022))
WAR_START = "2022-02-24"


def point_in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def load_regions():
    with open(os.path.join(ROOT, "data", "regions.json"), encoding="utf-8") as fh:
        feats = json.load(fh)["features"]
    regs = []
    for f in feats:
        polys = f["geometry"]["coordinates"]
        xs = [p[0] for poly in polys for p in poly[0]]
        ys = [p[1] for poly in polys for p in poly[0]]
        regs.append((f["properties"], polys, (min(xs), min(ys), max(xs), max(ys))))
    return regs


def region_of(lon, lat, regs, cache):
    key = (round(lon, 2), round(lat, 2))
    if key in cache:
        return cache[key]
    found = None
    for props, polys, (x0, y0, x1, y1) in regs:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        for poly in polys:
            if point_in_ring(lon, lat, poly[0]) and not any(point_in_ring(lon, lat, h) for h in poly[1:]):
                found = props["id"]
                break
        if found:
            break
    cache[key] = found
    return found


def country_filter():
    """Ukraine + Russie (polygones Natural Earth de site/geo.js), pour les sources hors archives par pays."""
    src = open(os.path.join(ROOT, "site", "geo.js"), encoding="utf-8").read()
    geo = json.loads(src[len("window.GEO="):].rstrip().rstrip(";"))
    polys = []
    for f in geo["countries"]:
        if f["properties"]["iso"] in ("UA", "RU"):
            for poly in f["geometry"]["coordinates"]:
                xs = [p[0] for p in poly[0]]
                ys = [p[1] for p in poly[0]]
                polys.append((poly, (min(xs), min(ys), max(xs), max(ys))))
    cache = {}

    def keep(lat, lon):
        key = (round(lat, 2), round(lon, 2))
        if key not in cache:
            cache[key] = any(x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_ring(lon, lat, poly[0])
                             and not any(point_in_ring(lon, lat, h) for h in poly[1:])
                             for poly, (x0, y0, x1, y1) in polys)
        return cache[key]
    return keep


def calibration(counts):
    """Faux excès sur les années d'avant-guerre (chaque année testée contre une normale qui l'exclut)."""
    out = {}
    for y in (2018, 2019, 2020, 2021):
        ex = F.excess_cells(counts, [b for b in BASE_YEARS if b != y], [(y, m) for m in range(1, 13)])
        tot = sum(n for by in counts.values() for (yy, m), n in by.items() if yy == y)
        out[y] = round(sum(n - mu for n, mu in ex.values()) / tot * 100, 1) if tot else 0
    return out


def main():
    # normale 2012-2021 et années de guerre closes : leurs archives sont indispensables
    guard.require_files("build.py", [os.path.join(RAW, f"{c}_{y}.csv") for y in range(2012, 2026)
                                     for c in ("Ukraine", "Russian_Federation")])
    api_files = sorted(glob.glob(os.path.join(ROOT, "data", "viirs_api_*.csv")))
    dets, fixed = F.read_detections(RAW, api_files, country_filter())
    counts = F.monthly_counts(dets)
    last_day = max(d[2] for d in dets)
    archive_last = max(d[2] for d in dets if d[4] == "a")
    src_n = Counter(d[4] for d in dets)
    sp_days = [d[2] for d in dets if d[4] == "s"]
    nrt_days = [d[2] for d in dets if d[4] == "n"]
    nrt_first = min(nrt_days) if nrt_days else None
    # Mois de guerre : de février 2022 au mois de la dernière détection
    months = []
    y, m = 2022, 2
    ly, lm = int(last_day[:4]), int(last_day[5:7])
    while (y, m) <= (ly, lm):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    # Couverture : jours réellement observés par S-NPP (archives : années complètes ; 2026 : jours
    # présents dans l'API ou le flux NRT, toutes détections confondues). Le satellite a eu des
    # interruptions en 2026 (fin avril-début juin notamment) : la normale est ramenée au prorata.
    obs_days = set()
    for path in api_files + [os.path.join(RAW, "nrt.csv")]:
        if os.path.exists(path):
            with open(path) as fh:
                obs_days |= {r["date"] for r in csv.DictReader(fh)}
    obs_days |= {d[2] for d in dets}
    cover = {}
    for y, m in months:
        ndays = (dt.date(y + m // 12, m % 12 + 1, 1) - dt.date(y, m, 1)).days
        if y <= int(archive_last[:4]):
            cover[(y, m)] = 1.0
        else:
            cover[(y, m)] = sum(1 for k in range(1, ndays + 1) if f"{y}-{m:02d}-{k:02d}" in obs_days) / ndays
    covered = {ym for ym, f in cover.items() if f >= 0.5}
    gaps = sorted(d for d in (dt.date(int(archive_last[:4]) + 1, 1, 1) + dt.timedelta(days=i) for i in range(400))
                  if d.isoformat() <= last_day and d.isoformat() not in obs_days)
    ex = F.excess_cells(counts, BASE_YEARS, [ym for ym in months if ym in covered], cover)
    # Février 2022 : seuls les jours à partir du 24 comptent (on recalcule la maille sur ces jours)
    feb = Counter(F.cell_of(d[0], d[1]) for d in dets if WAR_START <= d[2] <= "2022-02-28")
    ex = {k: v for k, v in ex.items() if (k[1], k[2]) != (2022, 2) or feb.get(k[0], 0) >= F.NMIN}
    midx = {ym: i for i, ym in enumerate(months)}
    regs = load_regions()
    cache = {}
    pts = []
    reg_tot = defaultdict(lambda: [0.0] * len(months))
    for lat, lon, d, frp, src in dets:
        if d < WAR_START:
            continue
        ym = (int(d[:4]), int(d[5:7]))
        if (F.cell_of(lat, lon), ym[0], ym[1]) not in ex:
            continue
        pts.extend([round(lat * 100), round(lon * 100), midx[ym], int(d[8:10]), round(frp)])
    for (c, y, m), (n, mu) in ex.items():
        lat, lon = (c[0] + 0.5) * F.CELL, (c[1] + 0.5) * F.CELL
        r = region_of(lon, lat, regs, cache) or "autre"
        reg_tot[r][midx[(y, m)]] += n - mu
    cells = [[] for _ in months]
    for (c, y, m), (n, mu) in ex.items():
        cells[midx[(y, m)]] += [c[0], c[1], n, round(mu, 1)]
    # Série mensuelle (zone entière) : toutes les détections, normale du mois, excès
    base_tot = defaultdict(float)
    for c, by in counts.items():
        for (yy, mm), n in by.items():
            if yy in BASE_YEARS:
                base_tot[mm] += n / len(BASE_YEARS)
    series = []
    for ym in months:
        tot = sum(by.get(ym, 0) for by in counts.values())
        exc = sum(n - mu for (c, y, m), (n, mu) in ex.items() if (y, m) == ym)
        f = cover[ym]
        series.append([tot, round(base_tot[ym[1]] * f), round(exc), round(f, 2) if ym in covered else 0])
    # Référence avant-guerre 2021 : détections par maille et par mois
    ref = [[] for _ in range(12)]
    for c, by in counts.items():
        for mm in range(1, 13):
            n = by.get((2021, mm), 0)
            if n:
                ref[mm - 1] += [c[0], c[1], n]
    # Historique annuel 2012→ (contexte)
    yearly = Counter(int(d[2][:4]) for d in dets)
    with open(os.path.join(ROOT, "data", "regions.json"), encoding="utf-8") as fh:
        reg_names = {f["properties"]["id"]: [f["properties"]["n"], f["properties"]["c"]] for f in json.load(fh)["features"]}
    calib = calibration(counts)
    data = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "lastDay": last_day, "archiveLast": archive_last, "nrtFirst": nrt_first, "gapDays": [d.isoformat() for d in gaps], "spLast": max(sp_days) if sp_days else None,
        "months": [f"{y}-{m:02d}" for y, m in months],
        "cell": F.CELL, "params": {"k": F.K, "nmin": F.NMIN, "floorSd": F.FLOOR_SD, "neighbours": F.NEIGHBOURS,
                                    "baseYears": [BASE_YEARS[0], BASE_YEARS[-1]]},
        "calibration": calib,
        "pts": pts, "cells": cells, "series": series, "ref2021": ref,
        "regions": {k: [round(x) for x in v] for k, v in reg_tot.items()}, "regionNames": reg_names,
        "yearly": {str(k): v for k, v in sorted(yearly.items())},
        "fixedSources": len(fixed),
    }
    old = guard.published("data.js", "DATA")
    if old:
        guard.check("build.py", [
            ("détections 2012-2025", sum(v for y, v in data["yearly"].items() if y < "2026"),
             sum(v for y, v in old["yearly"].items() if y < "2026"), "count"),
            ("nombre de mois", len(data["months"]), len(old["months"]), "max"),
            ("dernière détection", data["lastDay"], old["lastDay"], "max"),
            ("détections en excès", len(data["pts"]) // 5, len(old["pts"]) // 5, "count"),
        ])
    js = "window.DATA=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(js)
    digest = hashlib.sha1(js.encode()).hexdigest()[:10]
    page = os.path.join(ROOT, "site", "index.html")
    if os.path.exists(page):
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="data\.js(\?v=[\w-]+)?"', f'src="data.js?v={digest}"', src)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    print(f"sources : archives {src_n['a']} · API standard {src_n['s']} · quasi temps réel {src_n['n']} "
          f"(standard jusqu'au {data['spLast']}, quasi temps réel à partir du {nrt_first})")
    print(f"data.js : {len(js) // 1024} Ko · {len(pts) // 5} détections en excès · {len(ex)} mailles-mois · "
          f"{len(months)} mois ({months[0]} → {months[-1]}) · dernière détection {last_day} · faux excès {calib}")


if __name__ == "__main__":
    main()
