"""Mise à jour mensuelle du détecteur v2 sur le front entier (GitHub Actions, le 9 du mois ; aucun état lourd gardé).

    python front_monthly.py window [--today AAAA-MM-JJ]                       -> affiche start, detect_from, end
    python front_monthly.py run   --root . --tmp <dossier> --shard i/n [--workers 3] [--today …]
        -> <tmp>/sections_<i>.csv  (sommes tronçon × côté × décade du lot ; les positions restent dans <tmp>/prive)
    python front_monthly.py build --root . --parts <tmp>/sections_*.csv [--today …]
        -> data/impacts_troncons_decades_v2.csv (fusionné avec l'historique) et site/impacts_troncons.js

Fenêtre (exécution le jour J du mois M, J ≥ 9) :
  end         = J − 1 ;
  detect_from = 11 du mois M − 2 (2e décade) : les dates de fin de fenêtre du mois précédent n'avaient pas encore de
                « prochaine observation claire » (persistance, ≤ 25 jours) ; elles sont recalculées ici ;
  start       = detect_from − 31 jours (construction de la référence, dernière observation claire ≤ 30 jours).
Les décades ≥ detect_from sont remplacées dans l'historique, les autres sont gardées ; s2_z est recalculé.
Résultats identiques au calcul continu (la référence ne remonte jamais à plus de 30 jours).
"""
import argparse
import datetime as dt
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def window(today):
    t = dt.date.fromisoformat(today) if today else dt.date.today()
    y, m = t.year, t.month - 2
    if m <= 0:
        y, m = y - 1, m + 12
    df = dt.date(y, m, 11)
    return (df - dt.timedelta(days=31)).isoformat(), df.isoformat(), (t - dt.timedelta(days=1)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("window", "run", "build"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--tmp", default="/tmp/crateres_v2")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--radius", type=float, default=30.0)
    ap.add_argument("--parts", nargs="*", default=[])
    ap.add_argument("--today")
    a = ap.parse_args()
    start, df, end = window(a.today)
    if a.cmd == "window":
        print(start, df, end)
        return
    py = sys.executable
    if a.cmd == "run":
        si = a.shard.split("/")[0]
        prive = os.path.join(a.tmp, "prive"); work = os.path.join(a.tmp, "work")
        subprocess.run([py, "-I", os.path.join(HERE, "front_run.py"), "--root", a.root, "--work", work, "--prive", prive,
                        "--start", start, "--end", end, "--detect-from", df, "--radius", str(a.radius),
                        "--workers", str(a.workers), "--threads", str(a.threads), "--shard", a.shard], check=True)
        subprocess.run([py, "-I", os.path.join(HERE, "front_finalize.py"), "sections", "--root", a.root, "--prive", prive,
                        "--from", df, "--out", os.path.join(a.tmp, f"sections_{si}.csv")], check=True)
    else:
        parts = [p for g in a.parts for p in (glob.glob(g) or [g])]
        subprocess.run([py, "-I", os.path.join(HERE, "front_finalize.py"), "build", "--root", a.root, "--parts", *parts,
                        "--merge", os.path.join(a.root, "data", "impacts_troncons_decades_v2.csv"), "--from", df], check=True)


if __name__ == "__main__":
    main()
