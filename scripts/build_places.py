#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Génère site/places.json : index de recherche des localités (GeoNames, gn_UA_tess.geojson).
Chaque entrée = [nom affiché, lat, lon, index_oblast, population, clés_de_recherche].
Les clés incluent les variantes latines (diacritiques retirés) ET cyrilliques (UK/RU)
pour qu'on puisse taper « Pokrovsk », « Покровськ » ou « Покровск ».
Gazetier statique : à régénérer à la main seulement, hors pipeline quotidien.
  python3 -I scripts/build_places.py
"""
import json, os, re, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "raw", "viina", "gn_UA_tess.geojson")
OUT = os.path.join(ROOT, "site", "places.json")

CYR = re.compile(r"[а-яёіїєґ]", re.I)
LATN = re.compile(r"[a-z]", re.I)
APOS = re.compile(r"[''`’\-–—/.]+")
WS = re.compile(r"\s+")


def norm(s):
    """Minuscule, diacritiques latins retirés, apostrophes/tirets -> espace ; cyrillique conservé."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = APOS.sub(" ", s.lower())
    return WS.sub(" ", s).strip()


def keep_alias(a):
    """On garde les alias en écriture latine ou cyrillique ; on écarte les autres scripts (bruit)."""
    return bool(LATN.search(a) or CYR.search(a))


def main():
    if not os.path.exists(SRC):
        sys.exit("manque " + SRC)
    gj = json.load(open(SRC, encoding="utf-8"))
    oblasts, oidx = [], {}
    rows = []
    for f in gj["features"]:
        p = f["properties"]
        try:
            lat = round(float(p["latitude"]), 4)
            lon = round(float(p["longitude"]), 4)
        except (TypeError, ValueError):
            continue
        name = (p.get("name") or p.get("asciiname") or "").strip()
        if not name:
            continue
        pop = int(p.get("population") or 0)
        ob = (p.get("ADM1_NAME") or "").strip()
        if ob not in oidx:
            oidx[ob] = len(oblasts); oblasts.append(ob)
        # clés de recherche : nom + asciiname + alternatenames (latin/cyrillique), normalisés, dédupliqués
        cand = [name, p.get("asciiname") or ""]
        cand += [a.strip() for a in (p.get("alternatenames") or "").split(",")]
        # plus d'alias pour les villes peuplées : elles portent les exonymes (Kiev, Odessa, Kharkov…) loin dans la liste
        cap = 24 if pop >= 20000 else 8
        seen, keys = set(), []
        for a in cand:
            if not a or not keep_alias(a):
                continue
            k = norm(a)
            if len(k) < 3 or k in seen:
                continue
            seen.add(k); keys.append(k)
            if len(keys) >= cap:
                break
        rows.append([name, lat, lon, oidx[ob], pop, " ".join(keys)])
    # tri par population décroissante : le front-end garde l'ordre pour départager les égalités
    rows.sort(key=lambda r: -r[4])
    out = {"oblasts": oblasts, "places": rows}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(OUT)
    print(f"{len(rows)} localités -> {OUT} ({sz/1e6:.2f} Mo, {len(oblasts)} oblasts)")
    # sanity : quelques villes connues
    bykey = {}
    for r in rows:
        for k in r[5].split():
            bykey.setdefault(k, r)
    for q in ("pokrovsk", "kupiansk", "bakhmut", "avdiivka", "pokrovsk".encode().decode()):
        r = bykey.get(q)
        print(f"  {q:12s} -> {r[0] if r else 'ABSENT'}" + (f"  ({r[1]},{r[2]}) pop={r[4]} {oblasts[r[3]]}" if r else ""))


if __name__ == "__main__":
    main()
