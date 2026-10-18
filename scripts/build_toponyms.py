"""Toponymes de la carte en quatre langues : site/toponyms.js (window.TOPO).

    python3 scripts/build_toponyms.py <places_i18n.json>

Entrée : table GeoNames (CC BY 4.0) construite hors dépôt (ukraine-recherche/i18n/places_i18n.json : localités du fond de
carte, dans l'ordre de BASEMAP.places, et étiquettes de geo.js). Noms anglais, ukrainiens et russes des « alternate
names » GeoNames, repli sur la forme latine ; le français reste celui du fond de carte.
Sortie : TOPO.p = [[en, uk, ru], …] aligné sur BASEMAP.places ; TOPO.c = {nom français : [en, uk, ru]} pour les villes,
régions et pays de geo.js. À relancer seulement si basemap.js ou geo.js changent.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = json.load(open(sys.argv[1], encoding="utf-8"))
import re
CYR = re.compile("[а-яіїєґё]", re.I)


def pick(v, fr):
    """[en, uk, ru] ; en ukrainien et en russe, une forme latine de repli est remplacée par la forme cyrillique de
    l'autre langue quand GeoNames n'en a qu'une (villes russes sans nom ukrainien, villages sans nom russe)."""
    en, uk, ru = v.get("en") or fr, v.get("uk") or fr, v.get("ru") or fr
    if not CYR.search(uk) and CYR.search(ru):
        uk = ru
    if not CYR.search(ru) and CYR.search(uk):
        ru = uk
    return [en, uk, ru]


p = [pick(x, x["fr"]) for x in src["places"]]
c = {}
for part in ("cities", "countries", "regions"):   # villes d'abord : « Kharkiv » est aussi un nom d'oblast
    for fr, v in src["geo"].get(part, {}).items():
        c.setdefault(fr, pick(v, fr))
js = "window.TOPO=" + json.dumps({"p": p, "c": c}, ensure_ascii=False, separators=(",", ":")) + ";"
open(os.path.join(ROOT, "site", "toponyms.js"), "w", encoding="utf-8").write(js)
print(f"toponyms.js : {len(js) // 1024} Ko · {len(p)} localités · {len(c)} étiquettes de geo.js (GeoNames, CC BY 4.0)")
