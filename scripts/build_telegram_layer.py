"""Couche d'indicateur « signaux Telegram (30 j) », version FIGÉE (collecte CI à venir) : par tronçon de 24 km et par mois,
nombre de mentions d'attaques et de présence ukrainiennes signalées par les sources RUSSES, et l'inverse (russes signalées
par les sources UKRAINIENNES). Agrégats seulement : aucun texte, aucun nom de canal (fichier de recherche
signals3/sens_troncons_month.csv). → site/telegram_troncons.js
    python3 scripts/build_telegram_layer.py <signals3/sens_troncons_month.csv>
"""
import csv
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    tiles, ru, ua = {}, defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
        t = int(r["troncon"])
        tiles[t] = [int(r["i0"]), int(r["j0"])]
        m = r["mois"]
        if r["camp_source"] == "ru":      # sources russes : attaques ou présence UKRAINIENNES signalées
            ru[(m, t)][0] += int(r["n_ua"] or 0)
            ru[(m, t)][1] += int(r["n_presence_ua"] or 0)
        elif r["camp_source"] == "ua":    # sources ukrainiennes : attaques ou présence RUSSES signalées
            ua[(m, t)][0] += int(r["n_ru"] or 0)
            ua[(m, t)][1] += int(r["n_presence_ru"] or 0)
    pack = lambda dd: [[m, t, a, p] for (m, t), (a, p) in sorted(dd.items()) if a + p > 0]  # noqa: E731
    out = {"tiles": {str(k): v for k, v in sorted(tiles.items())}, "tileCells": 12, "ru": pack(ru), "ua": pack(ua),
           "last": max(m for m, _ in list(ru) + list(ua))}
    js = "window.TG_TRONCONS=" + json.dumps(out, separators=(",", ":")) + ";"
    open(os.path.join(ROOT, "site", "telegram_troncons.js"), "w", encoding="utf-8").write(js)
    print(f"telegram_troncons.js : {len(js) // 1024} Ko, {len(out['ru'])} tronçons-mois (sources russes), {len(out['ua'])} (sources ukrainiennes), jusqu'à {out['last']}")


if __name__ == "__main__":
    main()
