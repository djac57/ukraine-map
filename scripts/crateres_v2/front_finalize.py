"""Agrégats du détecteur v2 (front entier) : tronçon de 20 km × côté × décade, puis couche du site.

    # 1) sommes partielles d'un ou plusieurs dossiers privés (positions lues ici, jamais écrites) :
    python -I front_finalize.py sections --root <dépôt> --prive <dossier privé> --out <partiel.csv> [--from AAAA-MM-JJ]
    # 2) assemblage (plusieurs partiels = lots / shards), fusion éventuelle avec l'historique, z, fichiers du dépôt :
    python -I front_finalize.py build --root <dépôt> --parts <partiel.csv> [...] [--merge data/impacts_troncons_decades_v2.csv]
                                      [--from AAAA-MM-JJ] [--csv <sortie.csv>] [--js <sortie.js>]
    # raccourci (tout l'historique, un seul dossier) :
    python -I front_finalize.py all --root <dépôt> --prive <dossier privé> --tmp <dossier> [--csv …] [--js …]
    # positions publiées (décision du 19/10/2026, décalage J-1) -> site/crateres_points.js :
    python -I front_finalize.py points --root <dépôt> --prive <dossier privé> [--lag 1] [--js <sortie.js>]

Définitions identiques à ukraine-recherche/sar/crateres_v2/agg_decades.py (règle STRICTE gelée, rules.P ; comptage
« tous », CRAT_COMPTE=tous, comme data/impacts_troncons_decades_v2.csv actuel) :
  - amas = détections retenues de la même date (et de la même tuile) à ≤ 300 m ; « en groupe » = amas ≥ 3 ;
  - côté : champ « equilibre » du mois (p ≥ 0,5 = russe) ; seules les mailles à ≤ 15 km de la ligne du mois ;
  - s2_n = s2_n_tous ; s2_km2 = km² comparés ; s2_z = z de Poisson contre les décades 18 à 36 précédentes ;
  - fire_n, fire_z repris de data/impacts_troncons_decades.csv (v1).
Couche du site (format de scripts/build_impacts_layer.py, lu par site/app.js loadImp / drawImp) :
  window.IMPACTS = {"rows": [[mois, gx, gy, z_ru_30j, z_ua_30j, asym_30j], …], "secCells": 10}
  fenêtre de 30 jours finissant à la 3e décade de chaque mois, définitions de sar/impacts_sections.py (A_MIN = 20 km²,
  normale = décades 18 à 36 avant, z seulement à partir de la 37e décade de la série). Agrégats seulement.
Avec --merge et --from : les lignes de l'historique antérieures à la décade de --from sont gardées telles quelles, les
suivantes sont remplacées par les nouvelles ; s2_z est recalculé sur la série fusionnée.
"""
import argparse
import csv
import glob
import json
import math
import os
import shutil
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules  # noqa: E402
import s2lib as sl  # noqa: E402

GRP_D, GRP_MIN = 300.0, 3
A_MIN = 20.0


def decs(a, b):
    out = []
    y, m = int(a[:4]), int(a[5:7])
    while f"{y}-{m:02d}" <= b:
        for d in (1, 2, 3):
            out.append(f"{y}-{m:02d}-{d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def groups(L):
    from scipy.spatial import cKDTree
    P = np.array([[o["r"], o["c"]] for o in L], float) * 10.0
    parent = list(range(len(L)))

    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in cKDTree(P).query_pairs(GRP_D):
        a, b = f(i), f(j)
        if a != b:
            parent[a] = b
    roots = [f(i) for i in range(len(L))]
    size = defaultdict(int)
    for r in roots:
        size[r] += 1
    return [(size[r] >= GRP_MIN, r) for r in roots]


def sections(a):
    fr = sl.Front(a.root)
    NT, NG, NGR, A = defaultdict(int), defaultdict(int), defaultdict(set), defaultdict(float)
    fdec = sl.decade(a.from_) if a.from_ else None
    ntile = nkept = 0
    for td in sorted(glob.glob(os.path.join(a.prive, "t*_*"))):
        if not os.path.exists(os.path.join(td, "done.json")) and not a.partial:
            continue
        tid = os.path.basename(td)
        ntile += 1
        byd = defaultdict(list)
        cp = os.path.join(td, "cands.jsonl")
        if os.path.exists(cp):
            for line in open(cp):
                if not line.endswith("\n"):
                    continue        # ligne en cours d'écriture (lecture d'une tuile non terminée)
                o = json.loads(line)
                if rules.keep(o, rules.P):
                    byd[o["d1"]].append(o)
        for d, L in byd.items():
            if fdec and sl.decade(d) < fdec:
                continue
            for o, (ing, gid) in zip(L, groups(L)):
                k = fr.sec_side(o["cell"], d[:7])
                if k is None:
                    continue
                key = (k, sl.decade(d))
                NT[key] += 1; nkept += 1
                if ing:
                    NG[key] += 1; NGR[key].add((tid, d, gid))
        op = os.path.join(td, "obs.csv")
        if os.path.exists(op):
            with open(op) as fh:
                for r in csv.DictReader(fh):
                    if not r.get("km2"):
                        continue
                    dec = sl.decade(r["date"])
                    if fdec and dec < fdec:
                        continue
                    k = fr.sec_side(int(r["cell"]), r["date"][:7])
                    if k is not None:
                        A[(k, dec)] += float(r["km2"])
    keys = sorted(set(A) | set(NT))
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sec", "side", "decade", "n_tous", "n_grp", "ngrp", "km2"])
        for (k, dec) in keys:
            w.writerow([f"{k[0]}_{k[1]}", k[2], dec, NT.get((k, dec), 0), NG.get((k, dec), 0), len(NGR.get((k, dec), ())),
                        round(A.get((k, dec), 0.0), 2)])
    print(f"{a.out} : {ntile} tuiles, {nkept} détections retenues à ≤ 15 km, {len(keys)} tronçon × côté × décade")


def build(a):
    NT, NG, NGR, A = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(float)
    for p in a.parts:
        for r in csv.DictReader(open(p)):
            key = ((r["sec"], r["side"]), r["decade"])
            NT[key] += int(r["n_tous"]); NG[key] += int(r["n_grp"]); NGR[key] += int(r["ngrp"]); A[key] += float(r["km2"])
    if a.merge and os.path.exists(a.merge):
        fdec = sl.decade(a.from_) if a.from_ else "9999"
        kept = 0
        for r in csv.DictReader(open(a.merge)):
            if r["decade"] >= fdec:
                continue
            key = ((r["sec"], r["side"]), r["decade"])
            NT[key] = int(r["s2_n_tous"]); NG[key] = int(r["s2_grp"]); NGR[key] = int(r["s2_ngrp"]); A[key] = float(r["s2_km2"])
            kept += 1
        print(f"historique {a.merge} : {kept} lignes gardées (décades < {fdec})")
    fire = {}
    v1 = os.path.join(a.root, "data", "impacts_troncons_decades.csv")
    if os.path.exists(v1):
        for r in csv.DictReader(open(v1)):
            fire[(r["sec"], r["side"], r["decade"])] = (r["fire_n"], r["fire_z"])
    alld = sorted({d for _, d in A})
    DEC = decs(alld[0][:7], alld[-1][:7]) if alld else []
    IDX = {d: i for i, d in enumerate(DEC)}
    N = NT       # CRAT_COMPTE = tous

    def series_z(key, d):            # agg_decades.series_z
        k = IDX[d]
        if k < 18:
            return np.nan
        nn = sum(N.get((key, DEC[q]), 0) for q in range(max(0, k - 36), k - 17))
        aa = sum(A.get((key, DEC[q]), 0) for q in range(max(0, k - 36), k - 17))
        ar = A.get((key, d), 0)
        if aa <= 0 or ar <= 0:
            return np.nan
        e = (nn + 0.5) / aa * ar
        return (N.get((key, d), 0) - e) / math.sqrt(e)

    rows = []
    for key in sorted({k for k, _ in A}, key=lambda k: (tuple(map(int, k[0].split("_"))), k[1])):
        for d in DEC:
            if (key, d) not in A:
                continue
            z = series_z(key, d)
            fn, fz = fire.get((key[0], key[1], d), ("", ""))
            rows.append(dict(sec=key[0], side=key[1], decade=d, s2_n=int(N.get((key, d), 0)), s2_km2=round(A[(key, d)], 1),
                             s2_z="" if np.isnan(z) else round(float(z), 3), fire_n=fn, fire_z=fz,
                             s2_n_tous=int(NT.get((key, d), 0)), s2_grp=int(NG.get((key, d), 0)), s2_ngrp=int(NGR.get((key, d), 0))))
    csv_out = a.csv or os.path.join(a.root, "data", "impacts_troncons_decades_v2.csv")
    with open(csv_out + ".tmp", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    os.replace(csv_out + ".tmp", csv_out)
    print(f"{csv_out} : {len(rows)} lignes, {len({r['sec'] for r in rows})} tronçons, {DEC[0]} → {DEC[-1]}, "
          f"détections retenues {sum(r['s2_n_tous'] for r in rows)}")

    # ---- couche du site : impacts_sections.py, fenêtre de 3 décades, z contre décades 18 à 36 avant
    def wsum(D_, s, sd, k, w=3):
        return sum(D_.get(((s, sd), DEC[q]), 0) for q in range(k - w + 1, k + 1) if q >= 0)

    def pz(n, ar, nn, aa):
        if ar < A_MIN or aa < A_MIN * 3:
            return None
        e = (nn + 0.5) / aa * ar
        return (n - e) / math.sqrt(e)

    def asym(nr, ar, nu, au):
        if ar < A_MIN or au < A_MIN:
            return None
        return math.log((nr + 0.5) / ar) - math.log((nu + 0.5) / au)

    def r1(x):
        return None if x is None else round(float(x), 1)
    secs = sorted({k[0] for k, _ in A}, key=lambda s: tuple(map(int, s.split("_"))))
    js_rows = []
    for s in secs:
        j, i = map(int, s.split("_"))
        for k, d in enumerate(DEC):
            if not d.endswith("-3") or k < 36:
                continue
            nr, ar, nu, au = wsum(N, s, "RU", k), wsum(A, s, "RU", k), wsum(N, s, "UA", k), wsum(A, s, "UA", k)
            nrr = sum(N.get(((s, "RU"), DEC[q]), 0) for q in range(k - 36, k - 17)); arr = sum(A.get(((s, "RU"), DEC[q]), 0) for q in range(k - 36, k - 17))
            nuu = sum(N.get(((s, "UA"), DEC[q]), 0) for q in range(k - 36, k - 17)); auu = sum(A.get(((s, "UA"), DEC[q]), 0) for q in range(k - 36, k - 17))
            zr, zu = r1(pz(nr, ar, nrr, arr)), r1(pz(nu, au, nuu, auu))
            if zr is None and zu is None:
                continue
            js_rows.append([d[:7], i * 10 + 5, j * 10 + 5, zr, zu, r1(asym(nr, ar, nu, au))])
    js_out = a.js or os.path.join(a.root, "site", "impacts_troncons.js")
    if os.path.exists(js_out) and a.backup:
        os.makedirs(a.backup, exist_ok=True)
        bk = os.path.join(a.backup, "impacts_troncons_avant_v2.js")
        if not os.path.exists(bk):
            shutil.copy2(js_out, bk)
            print(f"ancienne couche copiée dans {bk}")
    js = "window.IMPACTS=" + json.dumps({"rows": js_rows, "secCells": 10}, separators=(",", ":")) + ";"
    open(js_out + ".tmp", "w", encoding="utf-8").write(js)
    os.replace(js_out + ".tmp", js_out)
    if js_rows:
        print(f"{js_out} : {len(js) // 1024} Ko, {len(js_rows)} tronçons-mois, {min(r[0] for r in js_rows)} → {max(r[0] for r in js_rows)}")
    else:
        print(f"{js_out} : aucune ligne (moins de 37 décades de série ?)")


D0_PTS = "2024-07-01"     # origine des jours de site/crateres_points.js
# Niveau 2 (« détection élargie », décision du 20/10/2026) : la règle stricte avec C ≥ 20 (au lieu de 30) et une
# élongation ≤ 3 (au lieu de 2) ; tous les autres critères inchangés (persistance, contraste à d1, fond végétal, aire,
# texture, données manquantes). Défini ici et non dans rules.py (copie octet pour octet de la recherche). Les agrégats
# (sections / build) restent sur la règle stricte.
P_NIV2 = dict(rules.P, Cmin=20.0, elong_max=3.0)
# Précisions mesurées à l'aveugle le 20/10/2026 sur le front entier (bande ≤ 15 km, 07/2024 → 10/2026 ; 334 vignettes
# tirées au hasard dans chaque niveau, douteux = ½) : stricte 0,47 (n = 79, IC 95 % 0,37-0,58), niveau 2 seul 0,43
# (n = 143, IC 0,35-0,51). Sur les zones d'évaluation de l'été 2026 (PK, nord de Lyman, Soumy), la stricte était à 0,73.
PREC_N1, PREC_N2 = 0.47, 0.43


def points(a):
    """Positions des détections v2 à ≤ 15 km de la ligne du mois (mêmes que les agrégats), dates d'observation
    ≤ aujourd'hui − a.lag jours (décalage de publication) :
      window.CRATERES = {generated, upto, d0, precision, precision2, cols,
                         pts: [[lon×1e4, lat×1e4, jour depuis d0, côté, niveau], …]}
    côté = 1 si russe (champ « equilibre » du mois, p ≥ 0,5), 0 sinon ; niveau = 1 si retenue par la règle stricte
    (rules.P), 2 si retenue seulement par la règle élargie (P_NIV2) ; points triés par jour. Pas de score par point
    (règles à seuils : seule la précision d'ensemble de chaque niveau est mesurée)."""
    import datetime as dt
    from pyproj import Transformer
    fr = sl.Front(a.root)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    upto = (today - dt.timedelta(days=a.lag)).isoformat()
    o0 = dt.date.fromisoformat(D0_PTS).toordinal()
    pts, ntile, nfar = [], 0, 0
    TR = {}
    for td in sorted(glob.glob(os.path.join(a.prive, "t*_*"))):
        if not os.path.exists(os.path.join(td, "done.json")) and not a.partial:
            continue
        tid = os.path.basename(td)
        cp = os.path.join(td, "cands.jsonl")
        if not os.path.exists(cp):
            continue
        ntile += 1
        tj, ti = int(tid[1:4]), int(tid[5:8])
        G = sl.tile_utm(fr, dict(j0=tj, i0=ti))
        if G["epsg"] not in TR:
            TR[G["epsg"]] = Transformer.from_crs(f"EPSG:{G['epsg']}", "EPSG:4326", always_xy=True)
        R, C, D, S, NV = [], [], [], [], []
        for line in open(cp):
            if not line.endswith("\n") or '"d2": null' in line:
                continue
            o = json.loads(line)
            if o["d1"] < D0_PTS or o["d1"] > upto:
                continue
            nv = 1 if rules.keep(o, rules.P) else 2 if rules.keep(o, P_NIV2) else 0
            if not nv:
                continue
            k = fr.sec_side(o["cell"], o["d1"][:7])
            if k is None:
                nfar += 1
                continue
            R.append(o["r"]); C.append(o["c"]); D.append(dt.date.fromisoformat(o["d1"]).toordinal() - o0)
            S.append(1 if k[2] == "RU" else 0); NV.append(nv)
        if not R:
            continue
        lon, lat = TR[G["epsg"]].transform(G["x0"] + (np.array(C) + 0.5) * 10.0, G["y1"] - (np.array(R) + 0.5) * 10.0)
        pts += [[int(round(x * 1e4)), int(round(y * 1e4)), d, s, nv] for x, y, d, s, nv in zip(lon, lat, D, S, NV)]
    pts.sort(key=lambda p: (p[2], p[0], p[1]))
    out = a.js or os.path.join(a.root, "site", "crateres_points.js")
    obj = dict(generated=dt.date.today().isoformat(), upto=upto, d0=D0_PTS, precision=PREC_N1, precision2=PREC_N2,
               cols=["lon_e4", "lat_e4", "jour", "ru", "niveau"], pts=pts)
    js = "window.CRATERES=" + json.dumps(obj, separators=(",", ":")) + ";"
    open(out + ".tmp", "w", encoding="utf-8").write(js)
    os.replace(out + ".tmp", out)
    last = dt.date.fromordinal(o0 + pts[-1][2]).isoformat() if pts else "—"
    n1 = sum(1 for p in pts if p[4] == 1)
    print(f"{out} : {len(js) // 1024} Ko, {len(pts)} points (stricts {n1}, élargis seulement {len(pts) - n1} ; "
          f"{sum(p[3] for p in pts)} côté russe), {ntile} tuiles, {nfar} retenues hors bande de 15 km écartées, "
          f"dernière date {last} (≤ {upto})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("sections", "build", "all", "points"))
    ap.add_argument("--lag", type=int, default=1, help="points : décalage de publication en jours (J-1 par défaut)")
    ap.add_argument("--today", help="points : date du jour (AAAA-MM-JJ), défaut = date système")
    ap.add_argument("--root", required=True)
    ap.add_argument("--prive")
    ap.add_argument("--out")
    ap.add_argument("--parts", nargs="*", default=[])
    ap.add_argument("--merge")
    ap.add_argument("--from", dest="from_")
    ap.add_argument("--csv")
    ap.add_argument("--js")
    ap.add_argument("--tmp")
    ap.add_argument("--backup", help="dossier où copier l'ancienne couche du site avant de l'écraser")
    ap.add_argument("--partial", action="store_true", help="inclure les tuiles non terminées (essai)")
    a = ap.parse_args()
    if a.cmd == "sections":
        sections(a)
    elif a.cmd == "build":
        build(a)
    elif a.cmd == "points":
        points(a)
    else:
        os.makedirs(a.tmp, exist_ok=True)
        a.out = os.path.join(a.tmp, "sections_front.csv")
        sections(a)
        a.parts = [a.out]
        build(a)


if __name__ == "__main__":
    main()
