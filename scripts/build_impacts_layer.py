"""Couche d'indicateur « impacts détectés (Sentinel-2) », données FIGÉES : par tronçon de 20 km (10 × 10 mailles) et par
mois (fenêtre de 30 jours finissant à la 3e décade), z local des impacts de chaque côté de la ligne et asymétrie
(sar/out/impacts_troncons.csv, agrégats). → site/impacts_troncons.js
    python3 scripts/build_impacts_layer.py <sar/out/impacts_troncons.csv>
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
f = lambda x: None if x in ("", None) else round(float(x), 1)  # noqa: E731
rows = []
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    if not r["decade"].endswith("-3"):
        continue
    zr, zu = f(r["z_ru_30j"]), f(r["z_ua_30j"])
    if zr is None and zu is None:
        continue
    j, i = map(int, r["sec"].split("_"))
    rows.append([r["decade"][:7], i * 10 + 5, j * 10 + 5, zr, zu, f(r["asym_30j"])])
js = "window.IMPACTS=" + json.dumps({"rows": rows, "secCells": 10}, separators=(",", ":")) + ";"
open(os.path.join(ROOT, "site", "impacts_troncons.js"), "w", encoding="utf-8").write(js)
print(f"impacts_troncons.js : {len(js) // 1024} Ko, {len(rows)} tronçons-mois, {min(r[0] for r in rows)} → {max(r[0] for r in rows)}")
