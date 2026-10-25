"""Agrégation du signal S2 v1 du DÉTECTEUR : tronçon de 20 km × côté × décade (comptes d'impacts, surface observée,
z de Poisson local), + feux VIIRS. Produit data/impacts_troncons_decades.csv (lu par le détecteur s2_sens,
fusion/obs/dynamique.py:92). Porté de ukraine-recherche/sar/impacts_decades.py (E3b) — PARTIE 1 SEULEMENT (production
du CSV). Les diagnostics Vivaldi et le test P(mouvement) (qui importaient scikit-learn) sont ÉCARTÉS : aucune dépendance
scikit-learn. Deps : numpy, scipy (via band), + frontlib (site/front.js in-repo).

Incrémental (comme crateres_v2/front_monthly) :
    python -I aggregate.py --root <dépôt> --cache <cache fetch> --from AAAA-MM-JJ [--merge data/impacts_troncons_decades.csv] [--out ...]
      -> les décades < --from sont GARDÉES telles qu'elles sont dans --merge ; les décades ≥ --from sont recalculées
         depuis le cache (impacts) et viirs_api_2026.csv (feux), z recomputé sur la série fusionnée (normale = décades
         18 à 36 avant, lues dans la série fusionnée committée).
Plein (backfill initial, cache complet requis) :
    python -I aggregate.py --root <dépôt> --cache <cache fetch> --full [--out ...]
"""
import argparse
import csv
import glob
import math
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import frontlib as fl  # noqa: E402
from band import distances  # noqa: E402

F, cp, D = distances()
FM = sorted(k for k in F if isinstance(k, str))
dom = cp >= 0


def fld(m):
    ks = [k for k in FM if k <= m]
    return ks[-1] if ks else FM[0]


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


def dec_label(date_or_dec):
    """AAAA-MM-JJ -> AAAA-MM-D (D=1/2/3) ; AAAA-MM-D laissé tel quel."""
    s = date_or_dec
    if len(s) == 10:          # AAAA-MM-JJ
        return f"{s[:7]}-{min(3, (int(s[8:10]) - 1) // 10 + 1)}"
    return s


def sec_side(c, month):
    j, i = c // fl.NX, c % fl.NX
    fm = fld(month)
    if not dom[j, i] or D[fm][j, i] > 15:
        return None
    return (j // 10, i // 10, "RU" if F[fm][j, i] >= 0.5 else "UA")


def poisson_z(n, a, nn, aa):
    if aa <= 0 or a <= 0:
        return np.nan
    lam = (nn + 0.5) / aa
    e = lam * a
    return (n - e) / math.sqrt(e) if e > 0 else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="dépôt ukraine-feux (viirs_api_2026.csv, CSV de sortie)")
    ap.add_argument("--cache", help="dossier cache du fetch (t*.csv) ; défaut scripts/impacts_v1/work_impacts/front")
    ap.add_argument("--merge", help="CSV historique à fusionner (défaut data/impacts_troncons_decades.csv)")
    ap.add_argument("--from", dest="from_", help="décade (AAAA-MM-JJ ou AAAA-MM-D) à partir de laquelle on recalcule")
    ap.add_argument("--full", action="store_true", help="recalcul complet depuis le cache (ignore --merge/--from)")
    ap.add_argument("--out", help="CSV de sortie (défaut = --merge, ou data/impacts_troncons_decades.csv)")
    a = ap.parse_args()
    cache = a.cache or os.path.join(HERE, "work_impacts", "front")
    merge = a.merge or os.path.join(a.root, "data", "impacts_troncons_decades.csv")
    out = a.out or merge
    api = os.path.join(a.root, "data", "viirs_api_2026.csv")
    from_dec = None if a.full else dec_label(a.from_) if a.from_ else None
    if not a.full and from_dec is None:
        sys.exit("préciser --from AAAA-MM-JJ (incrémental) ou --full (recalcul complet).")

    # ---- série fusionnée : s2_n, s2_km2, fire_n par (sec,side,décade) ----
    N = defaultdict(float); A = defaultdict(float); FN = defaultdict(float)
    committed = {}            # (sec,side,dec) -> row dict (garde l'historique < from_dec verbatim)
    last_month = "2024-07"
    if not a.full and os.path.exists(merge):
        for r in csv.DictReader(open(merge, encoding="utf-8")):
            key = (r["sec"], r["side"], r["decade"])
            committed[key] = r
            last_month = max(last_month, r["decade"][:7])
            # série (pour la normale des décades recalculées) : on garde les comptes committés
            N[(r["sec"], r["side"], r["decade"])] = float(r["s2_n"] or 0)
            A[(r["sec"], r["side"], r["decade"])] = float(r["s2_km2"] or 0)
            FN[(r["sec"], r["side"], r["decade"])] = float(r["fire_n"] or 0)

    # ---- impacts du cache (décades fraîches) : écrasent la série pour dec >= from_dec (ou tout, si --full) ----
    fresh_decs = set()
    rn = defaultdict(float); ra = defaultdict(float)
    for f in glob.glob(os.path.join(cache, "t*.csv")):
        for r in csv.DictReader(open(f)):
            d = r["decade"]
            if not a.full and d < from_dec:
                continue
            k = sec_side(int(r["cell"]), d[:7])
            if k is None:
                continue
            key = (f"{k[0]}_{k[1]}", k[2], d)
            rn[key] += int(r["s2_impacts"]); ra[key] += float(r["s2_obs_km2"])
            fresh_decs.add(d)
            last_month = max(last_month, d[:7])
    # applique le frais : pour dec >= from_dec, on REMPLACE n/a par le frais (0 si pas vu)
    if a.full:
        N = defaultdict(float, rn); A = defaultdict(float, ra); FN = defaultdict(float)
    else:
        # tout (sec,side,dec>=from_dec) committé est effacé puis réécrit par le frais
        for key in list(N):
            if key[2] >= from_dec:
                N[key] = 0.0; A[key] = 0.0
        for key, v in rn.items():
            N[key] = v
        for key, v in ra.items():
            A[key] = v

    # ---- feux VIIRS (2026, depuis le dépôt) pour les décades fraîches ----
    if not a.full:
        for key in list(FN):
            if key[2] >= from_dec:
                FN[key] = 0.0
    if os.path.exists(api):
        for r in csv.DictReader(open(api)):
            if not ((r.get("type") == "0" and r.get("src") == "sp") or r.get("src") == "nrt"):
                continue
            date = r["date"]
            if date[:7] < "2024-07":
                continue
            d = f"{date[:7]}-{min(3, (int(date[8:10]) - 1) // 10 + 1)}"
            if not a.full and d < from_dec:
                continue
            x, y = fl.laea(float(r["lon"]), float(r["lat"]))
            j, i = int((fl.Y1 - y) // fl.SIZE), int((x - fl.X0) // fl.SIZE)
            if not (0 <= j < fl.NY and 0 <= i < fl.NX):
                continue
            k = sec_side(j * fl.NX + i, date[:7])
            if k is not None:
                FN[(f"{k[0]}_{k[1]}", k[2], d)] += 1
                last_month = max(last_month, d[:7])

    DEC = decs("2024-07", last_month)
    IDX = {d: k for k, d in enumerate(DEC)}

    # surface « exposée » (km²) par (sec,side,décade) pour la normale des feux
    AREA = defaultdict(float)
    for m in sorted({d[:7] for d in DEC}):
        fm = fld(m)
        for j, i in zip(*np.nonzero(dom & (D[fm] <= 15))):
            k = (j // 10, i // 10, "RU" if F[fm][j, i] >= 0.5 else "UA")
            for dd in (1, 2, 3):
                AREA[(f"{k[0]}_{k[1]}", k[2], f"{m}-{dd}")] += 4.0

    def series_z(Nd, Ad, sec, side, d):
        k = IDX.get(d)
        if k is None or k < 36:
            return np.nan
        nn = sum(Nd.get((sec, side, DEC[q]), 0) for q in range(k - 36, k - 17))
        aa = sum(Ad.get((sec, side, DEC[q]), 0) for q in range(k - 36, k - 17))
        return poisson_z(Nd.get((sec, side, d), 0), Ad.get((sec, side, d), 0), nn, aa)

    # ---- garde-fou : fetch vide/échoué -> ne PAS écraser l'historique committé ----
    if not a.full and committed and sum(rn.values()) == 0:
        sys.exit("garde-fou : aucun impact frais dans le cache (fetch vide/échoué) — CSV committé conservé, rien d'écrit.")

    # ---- assemblage ----
    COLS = ["sec", "side", "decade", "s2_n", "s2_km2", "s2_z", "fire_n", "fire_z"]

    def fmt_km2(v):
        return "0" if v == 0 else (f"{round(v, 1):g}")

    def fmt_z(v):
        return "" if (v is None or (isinstance(v, float) and np.isnan(v))) else ("0" if v == 0 else f"{round(v, 3):g}")

    out_rows = []            # listes de chaînes prêtes à écrire (ordre COLS)
    recompute = set(k for k in list(N) + list(FN) + list(AREA) if a.full or k[2] >= from_dec)
    nrecomp = 0
    for key in recompute:
        sec, side, d = key
        s2n = int(round(N.get(key, 0))); s2a = round(A.get(key, 0), 1)
        fn = int(round(FN.get(key, 0)))
        if s2n == 0 and s2a == 0 and fn == 0 and key not in AREA:
            continue
        out_rows.append([sec, side, d, str(s2n), fmt_km2(s2a),
                         fmt_z(series_z(N, A, sec, side, d)), str(fn), fmt_z(series_z(FN, AREA, sec, side, d))])
        nrecomp += 1
    # historique committé < from_dec : VERBATIM (byte-identique)
    kept = 0
    if not a.full:
        for key, r in committed.items():
            if key[2] < from_dec:
                out_rows.append([r[c] for c in COLS]); kept += 1
    out_rows.sort(key=lambda x: (x[0], x[1], x[2]))

    tmp = out + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(out_rows)
    os.replace(tmp, out)
    nz = sum(1 for x in out_rows if x[3] not in ("0", "", None))
    print(f"{out} : {len(out_rows)} lignes ({kept} historiques gardées, {nrecomp} recalculées), "
          f"{nz} tronçon-décades avec impact, décades {out_rows[0][2]} → {max(x[2] for x in out_rows)}", flush=True)


if __name__ == "__main__":
    main()
