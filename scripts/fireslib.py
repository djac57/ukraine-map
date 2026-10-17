"""Fonctions communes : lecture des détections, grille, normale et excès (utilisées par build.py)."""
import csv
import glob
import math
import os
from collections import defaultdict

CELL = 0.1          # maille de la grille (degrés, ≈ 7 × 11 km à 48° N)
STATIC_CELL = 0.01  # maille du masque des sources fixes (≈ 1 km)


def cell_of(lat, lon):
    return (math.floor(lat / CELL), math.floor(lon / CELL))


def read_detections(raw_dir, api_files=(), keep=None):
    """Toutes les détections VIIRS S-NPP de la zone, sans doublon.

    Renvoie (dets, static) : dets = liste de (lat, lon, date 'AAAA-MM-JJ', frp, source) ;
    static = ensemble de cellules de 0,01° classées « source fixe » (type 2).
    Priorité par jour : archives annuelles par pays > API FIRMS en traitement standard (avec type) >
    API FIRMS quasi temps réel > flux NRT 7 jours. Avec le type, on ne garde que le type 0 (feu de
    végétation présumé) ; sans type (NRT), on écarte ce qui tombe sur une source fixe connue.
    keep(lat, lon) : filtre géographique appliqué aux sources hors archives (qui, elles, sont déjà
    limitées à l'Ukraine et à la Russie).
    """
    seen, dets, static = set(), [], defaultdict(int)
    for path in sorted(glob.glob(os.path.join(raw_dir, "*_[12][0-9][0-9][0-9].csv"))):
        with open(path) as fh:
            for r in csv.DictReader(fh):
                lat, lon = float(r["lat"]), float(r["lon"])
                key = (r["lat"], r["lon"], r["date"], r["time"])
                if key in seen:
                    continue
                seen.add(key)
                t = r["type"]
                if t == "2":
                    static[(math.floor(lat / STATIC_CELL), math.floor(lon / STATIC_CELL))] += 1
                if t != "0":
                    continue
                dets.append((lat, lon, r["date"], float(r["frp"] or 0), "a"))
    covered = {d[2] for d in dets}
    api = []
    for path in api_files:
        if os.path.exists(path):
            with open(path) as fh:
                api += [r for r in csv.DictReader(fh) if r["date"] not in covered]
    api = [r for r in api if keep is None or keep(float(r["lat"]), float(r["lon"]))]
    sp_days = set()
    for r in api:
        if r["src"] != "sp":
            continue
        lat, lon = float(r["lat"]), float(r["lon"])
        sp_days.add(r["date"])
        if r["type"] == "2":
            static[(math.floor(lat / STATIC_CELL), math.floor(lon / STATIC_CELL))] += 1
        if r["type"] == "0":
            dets.append((lat, lon, r["date"], float(r["frp"] or 0), "s"))
    fixed = {c for c, n in static.items() if n >= 3}

    def add_nrt(rows, tag, skip_days):
        for r in rows:
            if r["date"] in skip_days:
                continue  # jour déjà couvert par une source définitive
            lat, lon = float(r["lat"]), float(r["lon"])
            key = (r["lat"], r["lon"], r["date"], r["time"])
            if key in seen:
                continue
            seen.add(key)
            if (math.floor(lat / STATIC_CELL), math.floor(lon / STATIC_CELL)) in fixed:
                continue
            dets.append((lat, lon, r["date"], float(r["frp"] or 0), tag))

    api_nrt = [r for r in api if r["src"] == "nrt"]
    add_nrt(api_nrt, "n", covered | sp_days)
    nrt = os.path.join(raw_dir, "nrt.csv")
    if os.path.exists(nrt):
        with open(nrt) as fh:
            rows = [r for r in csv.DictReader(fh) if keep is None or keep(float(r["lat"]), float(r["lon"]))]
        add_nrt(rows, "n", covered | sp_days | {r["date"] for r in api_nrt})
    return dets, fixed


def monthly_counts(dets, start=None):
    """counts[cell][(année, mois)] = nombre de détections (à partir de la date start incluse)."""
    counts = defaultdict(lambda: defaultdict(int))
    for lat, lon, d, frp, src in dets:
        if start and d < start:
            continue
        counts[cell_of(lat, lon)][(int(d[:4]), int(d[5:7]))] += 1
    return counts


def baseline(counts, years):
    """Normale par cellule et mois calendaire : (moyenne, écart-type) sur les années données."""
    base = {}
    n = len(years)
    for c, by in counts.items():
        for m in range(1, 13):
            vals = [by.get((y, m), 0) for y in years]
            s = sum(vals)
            if s == 0:
                continue
            mu = s / n
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / n)
            base[(c, m)] = (mu, sd)
    return base


def is_excess(n, mu, sd, k, floor_sd, nmin):
    """Une cellule-mois est « en excès » si son compte dépasse nettement sa normale."""
    return n >= nmin and n > mu + k * max(sd, floor_sd)


# Paramètres retenus (calibrés sur 2018-2021 : chaque année testée contre une normale qui l'exclut)
K, NMIN, FLOOR_SD, NEIGHBOURS = 4.0, 3, 1.0, 3


def excess_cells(counts, base_years, months, scale=None):
    """Cellules-mois en excès, pour les mois (année, mois) demandés.

    Une cellule est retenue si son compte dépasse sa normale de plus de K écarts-types (plancher
    FLOOR_SD), compte au moins NMIN détections, et si au moins NEIGHBOURS de ses 8 voisines sont
    aussi en excès le même mois : les feux de combat sont groupés le long du front, alors que les
    brûlages agricoles « en trop » d'une année sont isolés.
    scale : {(année, mois): f} pour un mois couvert en partie (f = jours observés / jours du mois) ;
    la normale est alors ramenée à f·μ et l'écart-type à √f·σ (comptes de type Poisson).
    Renvoie {(cellule, année, mois): (n, normale)}.
    """
    base = baseline(counts, base_years)
    months = set(months)
    raw = {}
    for c, by in counts.items():
        for ym, n in by.items():
            if ym not in months:
                continue
            mu, sd = base.get((c, ym[1]), (0.0, 0.0))
            f = (scale or {}).get(ym, 1.0)
            mu, sd = mu * f, sd * math.sqrt(f)
            if is_excess(n, mu, sd, K, FLOOR_SD, NMIN):
                raw[(c, ym)] = (n, mu)
    out = {}
    for (c, ym), v in raw.items():
        nb = sum(1 for di in (-1, 0, 1) for dj in (-1, 0, 1)
                 if (di or dj) and ((c[0] + di, c[1] + dj), ym) in raw)
        if nb >= NEIGHBOURS:
            out[(c, ym[0], ym[1])] = v
    return out
