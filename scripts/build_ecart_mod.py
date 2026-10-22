"""Écart entre la surface de contrôle REVENDIQUÉE par le MoD RF / RIA et NOTRE ligne fusionnée.

    python3 scripts/build_ecart_mod.py

Idée (différenciateur) : le ministère russe de la Défense (repris Interfax / RIA / Telegram) annonce des
localités « prises ». On cumule ces localités (faits géocodés, déjà filtrés par build_claims.py), on en
déduit la ZONE que le MoD revendique, et on la compare maille à maille à NOTRE champ de contrôle fusionné
(moteur v3, site/front_fusion.js). On NE compare PAS au DeepState : c'est « MoD vs NOUS ».

Sorties :
  data/ecart_mod.csv   une ligne par localité revendiquée (date première/dernière annonce, p_RU de NOTRE
                       ligne, statut accord/sur-revendiquée, profondeur derrière notre ligne, drapeau de
                       fiabilité du géocodage).
  site/ecart_mod.js    window.ECART_MOD pour une future couche (chiffres + points sur-revendiqués).

Entrées : data/claims_modru.csv et data/claims_gsua.csv (FAITS géocodés, build_claims.py), site/front.js
(grille LAEA + indices de mailles), site/front_fusion.js (NOTRE champ fusionné, dernier mois).

Méthode de l'aire : la REGION revendiquée par le MoD = fermeture morphologique (rayon r) de l'union des
mailles des localités revendiquées et de notre propre région russe, puis remplissage des trous ; cela relie
les annonces à l'arrière russe et comble les concavités sans recopier aucune carte. L'ÉCART = mailles de
cette région que NOTRE ligne attribue à l'Ukraine (p_RU < 0,5). Sensibilité donnée pour r ∈ {6, 10, 14} km.

Prudence : agrégé, pas de suivi d'unités, aucune donnée nominative ; géocodage = faits seulement, aucun
texte repris. Une annonce très en arrière de notre ligne (> 30 km) ne peut pas être une prise récente :
elle est signalée (reprise ancienne de 2022 ou homonyme / axe d'oblast mal géocodé).
"""
import csv
import json
import base64
import math
import os
import sys
from collections import deque, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---------- grille LAEA (identique à build_front.py) ----------
R = 6371007.2
LON0, LAT0 = 31.5, 48.5
SIZE = 2000.0
X0, Y1 = -700e3, 470e3
CELL_KM2 = (SIZE / 1000) ** 2
HULL_RADII_KM = (6, 10, 14)
HULL_MAIN_KM = 10
DEEP_KM = 30.0            # au-delà, une sur-revendication n'est pas une prise récente crédible


def laea(lon, lat):
    l, p, l0, p0 = map(math.radians, (lon, lat, LON0, LAT0))
    k = math.sqrt(2 / (1 + math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0)))
    return R * k * math.cos(p) * math.sin(l - l0), R * k * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0))


# ---------- lecture des sorties publiées ----------
def read_front():
    s = open(os.path.join(ROOT, "site", "front.js")).read()
    return json.loads(s.split("=", 1)[1].rstrip().rstrip(";"))


def read_frontp(path, key):
    """Décode le tableau run-length window.FRONTP["key"] (un base64 par mois)."""
    s = open(path).read()
    i = s.index("=", s.index(key)) + 1
    seg = s[i:].strip()
    depth = 0
    for k, ch in enumerate(seg):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(seg[:k + 1])
    raise ValueError("tableau FRONTP introuvable dans " + path)


def read_fusion_meta(path):
    s = open(path).read()
    i = s.index("window.FRONT_FUSION=") + len("window.FRONT_FUSION=")
    seg = s[i:]
    depth = 0
    for k, ch in enumerate(seg):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(seg[:k + 1])
    raise ValueError("objet FRONT_FUSION introuvable")


def decode_month(enc, ncells, prev):
    b = base64.b64decode(enc)
    q = np.empty(ncells, np.uint8)
    o = i = 0
    while i < len(b):
        v = b[i]; i += 1
        n = 0; sh = 0
        while True:
            c = b[i]; i += 1
            n |= (c & 127) << sh; sh += 7
            if not c & 128:
                break
        q[o:o + n] = prev[o:o + n] if v == 255 else v
        o += n
    return q


# ---------- morphologie binaire numpy-only (BFS 4-voisins) ----------
def _bfs_dist(mask, allowed, NX, NY):
    """Distance de Manhattan (en mailles) à la source mask, propagée sur les mailles allowed."""
    N = NX * NY
    dist = np.full(N, 1 << 30, np.int32)
    allowed = allowed.reshape(-1)
    src = np.nonzero(mask.reshape(-1))[0]
    dq = deque(int(c) for c in src)
    dist[src] = 0
    while dq:
        c = dq.popleft()
        d1 = dist[c] + 1
        j, i = divmod(c, NX)
        for nc in ((c - NX if j else -1), (c + NX if j + 1 < NY else -1),
                   (c - 1 if i else -1), (c + 1 if i + 1 < NX else -1)):
            if nc >= 0 and allowed[nc] and dist[nc] > d1:
                dist[nc] = d1
                dq.append(nc)
    return dist.reshape(NY, NX)


def dilate(mask, r_cells, allowed, NX, NY):
    d = _bfs_dist(mask, allowed, NX, NY)
    return d <= r_cells


def closing(seed, allowed, r_cells, NX, NY):
    """Fermeture morphologique (dilatation de rayon r puis érosion), sur les mailles allowed.
    Élément structurant = disque de Manhattan (BFS 4-voisins). La fermeture est extensive :
    le résultat contient toujours la graine, et comble les concavités / trous de largeur < 2r."""
    dil = dilate(seed, r_cells, allowed, NX, NY) & allowed
    # érosion de dil = complément de la dilatation du complément (dans allowed)
    comp = allowed & ~dil
    comp_dil = dilate(comp, r_cells, allowed, NX, NY) & allowed
    return (allowed & ~comp_dil) | (seed & allowed)


def main():
    fr = read_front()
    g = fr["grid"]
    NX, NY = g["nx"], g["ny"]
    N = NX * NY

    # mailles -> indice de localité (-1 hors domaine, -2 bande russe frontalière)
    b = base64.b64decode(fr["runs"])
    cellPlace = np.empty(N, np.int32)
    o = 0
    for i in range(0, len(b), 3):
        v = b[i] | (b[i + 1] << 8)
        n = b[i + 2]
        cellPlace[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
        o += n
    UCELLS = np.nonzero(cellPlace != -1)[0]

    # NOTRE champ fusionné (dernier mois)
    fus = read_frontp(os.path.join(ROOT, "site", "front_fusion.js"), '["fusion"]')
    meta = read_fusion_meta(os.path.join(ROOT, "site", "front_fusion.js"))
    prev = np.zeros(len(UCELLS), np.uint8)
    last_idx = -1
    for idx, e in enumerate(fus):
        if e:
            prev = decode_month(e, len(UCELLS), prev)
            last_idx = idx
    our_date = meta["months"][last_idx]["date"] if last_idx < len(meta["months"]) else fr.get("limit")

    pgrid = np.full(N, np.nan)
    pgrid[UCELLS] = prev.astype(float) / 63.0
    pgrid[cellPlace == -2] = 1.0            # bande russe = russe
    P2 = pgrid.reshape(NY, NX)
    cp2 = cellPlace.reshape(NY, NX)
    ru = np.nan_to_num(P2, nan=0.0) >= 0.5   # NOTRE région russe (Crimée + bande comprises)
    domain = cp2 != -1
    ua_domain = (cp2 >= 0) & ~ru             # tenu UA selon nous, en Ukraine (hors bande russe)

    def gidx(lon, lat):
        x, y = laea(lon, lat)
        i = int((x - X0) // SIZE)
        j = int((Y1 - y) // SIZE)
        return j * NX + i if 0 <= i < NX and 0 <= j < NY else -1

    # distance (mailles) derrière NOTRE ligne, dans le domaine
    dist_front = _bfs_dist(ru, domain & ~np.isnan(P2) | (cp2 == -2), NX, NY)

    # ---------- localités revendiquées par le MoD (faits géocodés) ----------
    mod = list(csv.DictReader(open(os.path.join(ROOT, "data", "claims_modru.csv"))))
    first, last = {}, {}
    rec = {}
    for r in mod:
        k = r["geonameid"]
        first[k] = min(first.get(k, r["date"]), r["date"])
        last[k] = max(last.get(k, r["date"]), r["date"])
        rec[k] = r
    # reprises documentées par l'état-major UA (libérations), indépendant de notre ligne
    gsua = defaultdict(list)
    for r in csv.DictReader(open(os.path.join(ROOT, "data", "claims_gsua.csv"))):
        gsua[r["geonameid"]].append(r["date"])

    locs = []
    for k, r in rec.items():
        lon, lat = float(r["lon"]), float(r["lat"])
        c = gidx(lon, lat)
        p = float(pgrid[c]) if c >= 0 and not np.isnan(pgrid[c]) else None
        depth = float(dist_front[c // NX, c % NX] * (SIZE / 1000)) if (c >= 0 and dist_front[c // NX, c % NX] < (1 << 20)) else None
        reprise = any(d >= first[k] for d in gsua.get(k, []))
        statut = "hors_grille" if p is None else ("accord_RU" if p >= 0.5 else "surrevendiquee_UA")
        # drapeau de fiabilité du géocodage : une « prise » à > 30 km derrière notre ligne n'est pas crédible
        # comme prise récente (reprise ancienne de 2022, ou homonyme / axe d'oblast mal géocodé)
        suspect = bool(statut == "surrevendiquee_UA" and depth is not None and depth > DEEP_KM)
        locs.append(dict(geonameid=k, nom=r["nom"], lat=lat, lon=lon, cell=c,
                         date_premiere=first[k], date_derniere=last[k],
                         p_ru_nous=(round(p, 3) if p is not None else None),
                         statut=statut, profondeur_km=(round(depth, 1) if depth is not None else None),
                         reprise_ua_documentee=reprise, geocodage_suspect=suspect))

    nR = sum(1 for L in locs if L["statut"] == "accord_RU")
    nU = sum(1 for L in locs if L["statut"] == "surrevendiquee_UA")
    over = [L for L in locs if L["statut"] == "surrevendiquee_UA"]
    over_near = [L for L in over if L["profondeur_km"] is not None and L["profondeur_km"] <= DEEP_KM]
    over_recent = [L for L in over if L["date_derniere"] >= "2025-01-01"]

    # ---------- aire revendiquée vs notre ligne ----------
    seed = np.zeros((NY, NX), bool)
    for L in locs:
        if L["cell"] >= 0:
            seed[L["cell"] // NX, L["cell"] % NX] = True
    our_ru_km2 = float((ru & domain).sum() * CELL_KM2)
    overclaim_km2 = {}
    for rkm in HULL_RADII_KM:
        hull = closing(seed | ru, domain, rkm / (SIZE / 1000), NX, NY)
        oc = hull & ua_domain
        near = oc & (dist_front <= DEEP_KM / (SIZE / 1000))
        overclaim_km2[rkm] = dict(total=round(float(oc.sum() * CELL_KM2)),
                                  pres_du_front=round(float(near.sum() * CELL_KM2)))
    head = overclaim_km2[HULL_MAIN_KM]["total"]

    # ---------- écritures ----------
    cols = ["geonameid", "nom", "lat", "lon", "date_premiere", "date_derniere",
            "p_ru_nous", "statut", "profondeur_km", "reprise_ua_documentee", "geocodage_suspect"]
    with open(os.path.join(ROOT, "data", "ecart_mod.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for L in sorted(locs, key=lambda x: x["date_derniere"], reverse=True):
            w.writerow(L)

    payload = {
        "generated": __import__("datetime").date.today().isoformat(),
        "notre_date": our_date,
        "source": "MoD RF (Interfax / RIA / Telegram), faits géocodés ; comparé à NOTRE champ fusionné v3",
        "n_localites_revendiquees": len(locs),
        "n_accord_ru": nR,
        "n_surrevendiquees_ua": nU,
        "n_surrevendiquees_recentes": len(over_recent),
        "ecart_km2_r10": head,
        "ecart_km2_sensibilite": overclaim_km2,
        "notre_region_ru_km2": round(our_ru_km2),
        "profondeur_mediane_km": (round(float(np.median([L["profondeur_km"] for L in over if L["profondeur_km"] is not None])), 1) if over else None),
        "localites_surrevendiquees": [
            {"nom": L["nom"], "lat": L["lat"], "lon": L["lon"], "p": L["p_ru_nous"],
             "dd": L["date_derniere"], "prof": L["profondeur_km"], "suspect": L["geocodage_suspect"]}
            for L in sorted(over, key=lambda x: x["date_derniere"], reverse=True)
        ],
    }
    with open(os.path.join(ROOT, "site", "ecart_mod.js"), "w") as f:
        f.write("window.ECART_MOD=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")

    # ---------- résumé ----------
    print(f"NOTRE ligne (champ fusionné v3) : mois {our_date}")
    print(f"Localités revendiquées par le MoD (faits géocodés, cumul) : {len(locs)}")
    print(f"  d'accord avec notre ligne (RU) : {nR}")
    print(f"  SUR-REVENDIQUÉES (notre ligne = UA) : {nU}  dont dernière annonce >= 2025 : {len(over_recent)}")
    print(f"  dont près du front (<= {DEEP_KM:.0f} km derrière notre ligne) : {len(over_near)}")
    print(f"  profondeur médiane derrière notre ligne : {payload['profondeur_mediane_km']} km")
    print(f"ÉCART de surface (r={HULL_MAIN_KM} km) : {head} km²  (sensibilité r=6..14 km : "
          f"{overclaim_km2[6]['total']}..{overclaim_km2[14]['total']} km²)")
    print(f"  dont près du front : {overclaim_km2[HULL_MAIN_KM]['pres_du_front']} km²")
    print(f"Notre région russe (réf.) : {round(our_ru_km2)} km²")
    print("Écrit : data/ecart_mod.csv, site/ecart_mod.js")


if __name__ == "__main__":
    main()
