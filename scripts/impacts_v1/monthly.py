"""Refresh mensuel CLOUD du signal S2 v1 du DÉTECTEUR (data/impacts_troncons_decades.csv).

Fenêtre (exécution le jour J du mois M, J ≥ 11) — mêmes principes que crateres_v2/front_monthly :
  end         = J − 1 ;
  detect_from = 11 du mois M − 2 (les décades récentes n'avaient pas encore de « prochaine observation claire » pour la
                persistance) : on recalcule à partir de là, le reste de l'historique committé est gardé ;
  start       = detect_from − 31 jours (triplet de référence pour la détection).

    python -I monthly.py window [--today AAAA-MM-JJ]                          -> start detect_from end
    python -I monthly.py run   --root . --tmp <dossier> [--shard i/n] [--today …]   -> <tmp>/cache/t*.csv
    python -I monthly.py build --root . --tmp <dossier> [--today …]                 -> data/impacts_troncons_decades.csv
"""
import argparse
import datetime as dt
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
    ap.add_argument("--tmp", default="/tmp/impacts_v1")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--today")
    a = ap.parse_args()
    start, df, end = window(a.today)
    if a.cmd == "window":
        print(start, df, end)
        return
    py = sys.executable
    cache = os.path.join(a.tmp, "cache")
    env = dict(os.environ, IMPACTS_V1_CACHE=cache, SAR_FRONT_ROOT=os.path.abspath(a.root))
    if a.cmd == "run":
        sh, ns = a.shard.split("/")
        os.makedirs(cache, exist_ok=True)
        subprocess.run([py, "-I", os.path.join(HERE, "fetch.py"), sh, ns, start, end], check=True, env=env)
    else:  # build
        subprocess.run([py, "-I", os.path.join(HERE, "aggregate.py"), "--root", a.root, "--cache", cache,
                        "--from", df, "--merge", os.path.join(a.root, "data", "impacts_troncons_decades.csv")],
                       check=True, env=env)


if __name__ == "__main__":
    main()
