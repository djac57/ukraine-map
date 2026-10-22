"""Rafraîchit le signal de DÉTECTION « impacts Sentinel-2 10 m » : régénère
`data/impacts_troncons_decades.csv` (tronçon de 20 km × côté × décade : comptes d'impacts 10 m, surface
observée, z de Poisson local) à partir du cache 10 m déjà sur disque.

Chaîne du signal (reco de l'agent I : « le S2 10 m est le signal 10 m principal de DÉTECTION » ; validé Vivaldi,
+~120 j d'avance sur la bascule des cartes) :
  1. (lourd, DIFFÉRÉ en CI / nohup local) `ukraine-recherche/sar/impacts_front.py` lit les scènes S2 L2A 10 m
     (Planetary Computer, sans compte) et écrit le cache `sar/work_impacts/front/t*.csv` (impacts par maille × décade).
  2. (léger, CE script) `ukraine-recherche/sar/impacts_decades.py` agrège ce cache par tronçon × côté × décade
     et calcule le z de Poisson local ; on recopie le CSV dans `ukraine-feux/data/`.

Ce script NE fait AUCUN fetch réseau : il s'appuie uniquement sur le cache 10 m présent (avertissement de la mission :
un fetch S2 local frais peut être superficiel — vérité = CI). Un garde-fou de sanité empêche d'écrire un CSV dégénéré :
si l'agrégat est vide/trop court, le CSV figé actuel est CONSERVÉ et le script sort en erreur.

    python3 -I scripts/refresh_impacts_decades.py            # régénère depuis le cache puis met à jour data/
    python3 -I scripts/refresh_impacts_decades.py --check    # n'écrit rien : compare au CSV en place (fraîcheur)

Gaté : standalone, jamais appelé par le build par défaut. N'altère que `data/impacts_troncons_decades.csv`.
"""
import csv
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEST = os.path.join(DATA, "impacts_troncons_decades.csv")
RECH = os.environ.get("UKRAINE_RECHERCHE", os.path.join(os.path.dirname(os.path.dirname(ROOT)), "perso", "ukraine-recherche"))
if not os.path.isdir(RECH):
    RECH = os.path.join(os.path.dirname(ROOT), "ukraine-recherche")
SAR = os.path.join(RECH, "sar")
SRC = os.path.join(SAR, "out", "impacts_troncons_decades.csv")
CACHE = os.path.join(SAR, "work_impacts", "front")

MIN_ROWS = 5000          # l'agrégat complet du front ≈ 14 500 lignes ; en dessous = dégénéré
MIN_NONZERO_S2 = 500     # au moins ~500 tronçon-décades avec au moins un impact 10 m


def sanity(path):
    """(ok, n_rows, n_nonzero_s2, derniere_decade) — un CSV plausible, pas une sortie superficielle."""
    if not os.path.exists(path):
        return False, 0, 0, None
    n = nz = 0
    last = ""
    for r in csv.DictReader(open(path, encoding="utf-8")):
        n += 1
        try:
            if float(r["s2_n"] or 0) > 0:
                nz += 1
        except (KeyError, ValueError):
            pass
        if r.get("decade", "") > last:
            last = r["decade"]
    return (n >= MIN_ROWS and nz >= MIN_NONZERO_S2), n, nz, last


def main():
    check = "--check" in sys.argv
    if not os.path.isdir(CACHE) or not os.listdir(CACHE):
        sys.exit(f"cache 10 m absent : {CACHE} — lancer d'abord impacts_front.py (fetch, différé CI/nohup). Abandon.")
    # agrégation (léger, lit le cache, aucun réseau)
    cmd = [sys.executable, "-I", os.path.join(SAR, "impacts_decades.py")]
    print("agrégation du cache 10 m :", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=SAR)
    if r.returncode != 0:
        sys.exit(f"impacts_decades.py a échoué (code {r.returncode}) ; CSV en place conservé.")
    ok, n, nz, last = sanity(SRC)
    print(f"agrégat : {n} lignes, {nz} tronçon-décades avec impact, dernière décade {last}", flush=True)
    if not ok:
        sys.exit(f"agrégat DÉGÉNÉRÉ ({n} lignes, {nz} non nuls) — CSV figé conservé (vérité = CI). Abandon.")
    ok0, n0, nz0, last0 = sanity(DEST)
    print(f"en place : {n0} lignes, {nz0} non nuls, dernière décade {last0}", flush=True)
    if check:
        same = ok0 and (n, nz, last) == (n0, nz0, last0)
        print("IDENTIQUE au CSV en place." if same else "DIFFÈRE du CSV en place (rafraîchissement utile).")
        return
    shutil.copyfile(SRC, DEST)
    print(f"-> écrit {DEST}  ({n} lignes, dernière décade {last})")


if __name__ == "__main__":
    main()
