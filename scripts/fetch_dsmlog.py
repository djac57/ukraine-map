"""Journal public de DeepStateMap : localités annoncées « libérées » ou « occupées », par date.

    python3 scripts/fetch_dsmlog.py <dossier_viina>

Pourquoi : la colonne DeepState de VIINA n'a pas suivi certaines mises à jour de la carte (contre-offensive
au nord de Lyman, 2026 : Serednie est « libérée » le 14 septembre selon DeepState, mais codée russe par VIINA
un mois plus tard). Le journal de DeepState (deepstatemap.live/api/history/public) date et localise chaque
changement ; il sert à corriger la colonne DeepState de VIINA, localité par localité.

On ne garde que des FAITS : date, localité (géocodée par les coordonnées du lien vers la carte, rattachée à la
localité VIINA la plus proche, à moins de 2 km) et sens du changement. Le texte n'est ni conservé ni publié.
Seuls les verbes de prise comptent : « liberated » → ukrainien ; « occupied », « captured » → russe. Les
avancées « près de » (« advanced near », « pushed back near », « regained control of areas near ») ne disent
pas qui tient la localité : ignorées.
Sortie : data/dsm_log.csv (date, geonameid, lon, lat, camp).
"""
import csv
import json
import math
import os
import re
import sys
import urllib.request

import guard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (compatible; ukraine-feux/1.0; non-commercial research)"
URL = "https://deepstatemap.live/api/history/public"
LINK = re.compile(r'<a [^>]*href="[^"]*#\d+/(-?[\d.]+)/(-?[\d.]+)"[^>]*>.*?</a>', re.S)
# verbes (anglais, sinon ukrainien) : le dernier vu avant un lien fixe le sens ; « near / поблизу » l'annule
VERB = re.compile(r"\b(liberated|occupied|captured|seized|took control of|near|in the area of)\b|"
                  r"(звільнили|звільнено|окупував|окупували|захопив|захопили|поблизу|біля|в районі)", re.I)
SIDE = {"liberated": "ua", "звільнили": "ua", "звільнено": "ua", "occupied": "ru", "captured": "ru", "seized": "ru",
        "took control of": "ru", "окупував": "ru", "окупували": "ru", "захопив": "ru", "захопили": "ru"}


def parse(text):
    """Liste (lat, lon, camp) des liens précédés d'un verbe de prise, dans l'ordre du texte."""
    out, side, pos = [], None, 0
    toks = sorted([(m.start(), "v", (m.group(1) or m.group(2)).lower()) for m in VERB.finditer(text)]
                  + [(m.start(), "l", (float(m.group(1)), float(m.group(2)))) for m in LINK.finditer(text)])
    for _, kind, val in toks:
        if kind == "v":
            side = SIDE.get(val)
        elif side:
            out.append((val[0], val[1], side))
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "viina"
    tess = os.path.join(src, "gn_UA_tess.geojson")
    guard.require_files("fetch_dsmlog.py", [tess])
    places = [(int(f["properties"]["geonameid"]), f["properties"]["longitude"], f["properties"]["latitude"])
              for f in json.load(open(tess, encoding="utf-8"))["features"]]
    grid = {}
    for p in places:
        grid.setdefault((round(p[1] * 10), round(p[2] * 10)), []).append(p)
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    hist = json.loads(urllib.request.urlopen(req, timeout=60).read())
    rows = []
    for h in hist:
        text = h.get("descriptionEn") or h.get("description") or ""
        day = h["createdAt"][:10]
        for lat, lon, side in parse(text):
            best, bd = None, 2.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for g, x, y in grid.get((round(lon * 10) + dx, round(lat * 10) + dy), []):
                        d = math.hypot((x - lon) * 111.3 * math.cos(math.radians(lat)), (y - lat) * 111.3)
                        if d < bd:
                            best, bd = (g, x, y), d
            if best:
                rows.append((day, best[0], round(best[1], 4), round(best[2], 4), side))
    rows = sorted(set(rows))
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    out = os.path.join(ROOT, "data", "dsm_log.csv")
    old = sum(1 for _ in open(out, encoding="utf-8")) - 1 if os.path.exists(out) else None
    guard.check("fetch_dsmlog.py", [("localités datées", len(rows), old, "count")])
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "geonameid", "lon", "lat", "camp"])
        w.writerows(rows)
    n = {s: sum(1 for r in rows if r[4] == s) for s in ("ua", "ru")}
    print(f"dsm_log.csv : {len(hist)} mises à jour, {len(rows)} localités datées (libérées {n['ua']}, occupées {n['ru']})")


if __name__ == "__main__":
    main()
