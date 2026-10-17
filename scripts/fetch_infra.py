"""Infrastructures statiques et publiques pour la couche « infrastructures » (contexte des combats intenses).

    python3 scripts/fetch_infra.py <dossier_brut>

Sources (licences vérifiées) :
- Natural Earth 10m railroads : domaine public.
- WRI Global Power Plant Database v1.3.0 (juin 2021) : CC BY 4.0 (World Resources Institute).
- OurAirports (airports.csv) : domaine public.
- OpenStreetMap (Overpass) : gares (railway=station), voies ferrées principales (usage=main), zones industrielles
  (landuse=industrial), cokeries et mines, aérodromes (aeroway=aerodrome, military=airfield) ; ODbL, © contributeurs
  OpenStreetMap.
Infrastructures statiques uniquement : aucune liste de frappes ni d'état de dommages.
Écrit dans <dossier_brut>/infra/ ; build_infra.py en tire site/infra.js. À relancer de loin en loin (données stables).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

UA = "ukraine-feux/1.0 (non-commercial research)"   # Overpass refuse (406) les identifiants qui imitent un navigateur
BBOX = (44.0, 30.0, 52.5, 41.5)   # sud, ouest, nord, est : la moitié est de l'Ukraine et la bande russe
FILES = {
    "ne_10m_railroads.zip": "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_railroads.zip",
    "gppd.zip": "https://wri-dataportal-prod.s3.amazonaws.com/manual/global_power_plant_database_v_1_3.zip",
    "airports.csv": "https://davidmegginson.github.io/ourairports-data/airports.csv",
}
OVERPASS = "https://overpass-api.de/api/interpreter"
QUERIES = {
    "osm_stations.json": 'node["railway"="station"]["name"]({b});',
    "osm_rail_main.json": 'way["railway"="rail"]["usage"="main"]({b});',
    "osm_industrial.json": 'way["landuse"="industrial"]({b});relation["landuse"="industrial"]({b});',
    "osm_heavy.json": 'nwr["industrial"~"coke|mine|steel|chemical"]({b});nwr["man_made"="works"]["product"~"coke|steel|coal"]({b});nwr["landuse"="quarry"]["resource"~"coal"]({b});',
    "osm_aero.json": 'nwr["aeroway"="aerodrome"]({b});nwr["military"="airfield"]({b});',
}


def get(url, data=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "*/*",
                                                          **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    out = os.path.join(sys.argv[1] if len(sys.argv) > 1 else "raw", "infra")
    os.makedirs(out, exist_ok=True)
    for name, url in FILES.items():
        path = os.path.join(out, name)
        if not os.path.exists(path):
            open(path, "wb").write(get(url))
            print(f"{name} : {os.path.getsize(path) // 1024} Ko", flush=True)
    # requêtes découpées en tuiles de 2° × 2° (une requête sur toute la zone dépasse les limites du serveur)
    s0, w0, n0, e0 = BBOX
    tiles = [(la, lo, min(la + 2, n0), min(lo + 2, e0)) for la in range(int(s0), int(n0), 2) for lo in range(int(w0), int(e0), 2)]
    for name, q in QUERIES.items():
        path = os.path.join(out, name)
        if os.path.exists(path):
            continue
        elems, seen, failed = [], set(), 0
        for t in tiles:
            body = f"[out:json][timeout:300];({q.format(b=','.join(str(x) for x in t))});out geom;"
            for attempt in range(4):
                try:
                    data = json.loads(get(OVERPASS, urllib.parse.urlencode({"data": body}).encode(), timeout=600))
                    for el in data.get("elements", []):
                        key = (el["type"], el["id"])
                        if key not in seen:
                            seen.add(key)
                            elems.append(el)
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"{name} {t} : nouvel essai ({e})", flush=True)
                    time.sleep(30 * (attempt + 1))
            else:
                failed += 1
            time.sleep(3)
        if failed:
            print(f"{name} : {failed} tuile(s) en échec, fichier non écrit", flush=True)
            continue
        json.dump({"elements": elems}, open(path, "w", encoding="utf-8"))
        print(f"{name} : {len(elems)} objets", flush=True)


if __name__ == "__main__":
    main()
