"""Frappes ukrainiennes SIGNALÉES sur la Russie et les territoires occupés, par région et par mois (faits agrégés, aucune
cible) → site/strikes_ru.js, pour la couche « missiles et drones » (sens Ukraine → Russie).

Trois sources, chacune comptée à part :
  - mod : drones ukrainiens « interceptés » selon les bilans du MoD RF (annonces d'une partie ; fil public t.me/s/mod_russia,
    <brut>/drones/claims/mod_russia.json, fetch_mod_drones.py), par région citée ; mer Noire, mer d'Azov à part ;
  - gov : mentions de frappes dans les canaux des autorités régionales de Belgorod, Koursk et Briansk
    (data/gouverneurs_region_month.csv, agrégats) ;
  - fires : feux anormaux FIRMS (site/data.js) à moins de 1,5 km d'un site industriel ou énergétique d'infra.js en Russie
    ou en zone occupée, comptés par région et par mois (aucun site publié).
Mois alignés sur site/data.js (index = mois de la couche feux), coupure J-7 de data.js respectée.
    python3 scripts/build_strikes_ru.py <brut>
"""
import csv
import json
import math
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")


def load_js(name, prefix):
    s = open(os.path.join(SITE, name), encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].rstrip().rstrip(";")) if prefix is None else json.loads(s[len(prefix):].rstrip().rstrip(";"))


# territoires occupés et mers, en plus des régions russes de regions_ru.js (radicaux au génitif)
EXTRA = {"UA-43": ["крым"], "UA-40": ["севастопол"], "UA-23": ["запорожск"], "UA-65": ["херсонск"], "UA-14": ["донецк", "днр"],
         "UA-09": ["луганск", "лнр"], "sea:noire": ["черного моря", "чёрного моря"], "sea:azov": ["азовского моря"]}
WORDS = {"один": 1, "одного": 1, "одна": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3, "четыре": 4, "пять": 5,
         "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10}
NUM = r"(\d+|" + "|".join(WORDS) + r")"


def num(x):
    return int(x) if x.isdigit() else WORDS.get(x.lower(), 0)


def parse_mod(texts, keys):
    """[(date, région, n ou None)] : une entrée par message et par région citée (« над территорией Брянской области »,
    « над территориями Белгородской и Курской областей », « над акваторией Черного моря »). n = nombre de drones
    seulement quand il est donné pour cette région seule (« 12 – над территорией Брянской области ») ; sinon None."""
    out = []
    for date, t in texts:
        tl = t.lower().replace("ё", "е")
        if not re.search(r"беспилот|бпла", tl) or not re.search(r"перехвач|уничтож|сбит|подавлен", tl):
            continue
        seen = {}
        for m in re.finditer(r"(?:(?P<n1>" + NUM + r")\s*[–—-]\s*)?над\s+(?:территори\w+|акватори\w+)\s+(?P<reg>[^,;:.\n–—]+)(?:\s*[–—-]\s*(?P<n2>" + NUM + r")\b)?", tl):
            reg = m.group("reg")
            rids = [k for k, st in keys.items() if any(s_ in reg for s_ in st)]
            n = m.group("n1") or m.group("n2")
            for rid in rids:
                v = num(n) if (n and len(rids) == 1) else None
                if rid not in seen or (seen[rid] is None and v):
                    seen[rid] = v
        out += [(date, rid, v) for rid, v in seen.items()]
    return out


def in_ring(x, y, ring):
    ins = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            ins = not ins
    return ins


def region_of(lon, lat, feats):
    for f in feats:
        g = f["geometry"]
        if not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            if in_ring(lon, lat, poly[0]):
                return f["properties"]["id"]
    return None


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "raw"
    D = load_js("data.js", "window.DATA=")
    months = D["months"]
    midx = {m: i for i, m in enumerate(months)}
    RR = load_js("regions_ru.js", None)
    G = load_js("geo.js", None)
    occ = [f for f in G["regions"] if f["properties"].get("id") in ("UA-43", "UA-40", "UA-23", "UA-65", "UA-14", "UA-09")]
    keys = {f["properties"]["id"]: f["properties"]["k"] for f in RR["features"]}
    keys.update(EXTRA)
    # 1. bilans du MoD RF
    mod, modn = defaultdict(float), defaultdict(float)
    path = os.path.join(raw, "drones", "claims", "mod_russia.json")
    texts = [(p["date"], p["text"]) for p in json.load(open(path, encoding="utf-8"))] if os.path.exists(path) else []
    rows = parse_mod(texts, keys)
    for d, rid, n in rows:
        if d[:7] in midx:
            mod[(midx[d[:7]], rid)] += 1          # jours où la région est citée dans un bilan d'interceptions
            if n:
                modn[(midx[d[:7]], rid)] += n     # drones annoncés quand le nombre est donné pour la région
    # 2. autorités régionales (Belgorod, Koursk, Briansk)
    gov = defaultdict(float)
    gp = os.path.join(ROOT, "data", "gouverneurs_region_month.csv")
    if os.path.exists(gp):
        for r in csv.DictReader(open(gp, encoding="utf-8")):
            if r["mois"] in midx:
                gov[(midx[r["mois"]], r["region"])] += float(r["mentions"])
    # 3. feux anormaux près des sites industriels et énergétiques (Russie et zones occupées)
    I = load_js("infra.js", None)
    sites = [(x[0], x[1]) for x in I.get("heavy", [])] + [(x[0], x[1]) for x in I.get("plants", [])] + \
            [((x[2][0] + x[2][2]) / 2, (x[2][1] + x[2][3]) / 2) for x in I.get("industrial", [])]
    feats = RR["features"] + occ
    srid = {}
    for lon, lat in sites:
        rid = region_of(lon, lat, feats)
        if rid:
            srid[(round(lon, 3), round(lat, 3))] = rid
    grid = defaultdict(list)
    for (lon, lat), rid in srid.items():
        grid[(int(lon * 20), int(lat * 20))].append((lon, lat, rid))
    fires = defaultdict(float)
    P = D["pts"]
    for j in range(0, len(P), 5):
        lat, lon, mi = P[j] / 100, P[j + 1] / 100, P[j + 2]
        cx, cy = int(lon * 20), int(lat * 20)
        best = None
        for a in (cx - 1, cx, cx + 1):
            for b in (cy - 1, cy, cy + 1):
                for slon, slat, rid in grid.get((a, b), ()):
                    dkm = math.hypot((slon - lon) * 111 * math.cos(math.radians(lat)), (slat - lat) * 111)
                    if dkm <= 1.5:
                        best = rid
        if best:
            fires[(mi, best)] += 1
    names = {f["properties"]["id"]: f["properties"]["n"] for f in RR["features"]}
    names.update({f["properties"]["id"]: f["properties"]["n"] for f in occ})
    names.update({"sea:noire": "mer Noire", "sea:azov": "mer d'Azov"})
    pack = lambda dd: [[m, r, round(v)] for (m, r), v in sorted(dd.items()) if v >= 1]  # noqa: E731
    # la collecte en CI ne relit que les derniers mois : les mois hors de la période relue gardent les comptes déjà
    # publiés (aucune collecte : tout est gardé)
    old = load_js("strikes_ru.js", None) if os.path.exists(os.path.join(SITE, "strikes_ru.js")) else {}
    lo, hi = (min(d for d, *_ in rows), max(d for d, *_ in rows)[:7]) if rows else ("9999", "0000")
    if rows and lo[8:] != "01":   # premier mois relu en partie : on garde l'ancien compte
        y, m = int(lo[:4]), int(lo[5:7]) + 1
        lo = f"{y + (m > 12)}-{(m - 1) % 12 + 1:02d}"
    lo = lo[:7]
    keep = lambda key, new: sorted([x for x in old.get(key, []) if not (lo <= months[x[0]] <= hi)] + new)  # noqa: E731
    if not rows:
        print("strikes_ru.js : pas de bilans du MoD RF collectés, comptes précédents conservés", flush=True)
    cov_old = (old.get("coverage") or {}).get("mod") or [None, None]
    cov = [min(x for x in (cov_old[0], rows and min(d for d, *_ in rows)) if x) if (cov_old[0] or rows) else None,
           max(x for x in (cov_old[1], rows and max(d for d, *_ in rows)) if x) if (cov_old[1] or rows) else None]
    inwin = lambda lst: [x for x in lst if lo <= months[x[0]] <= hi] if old else lst  # noqa: E731
    out = {"names": names, "mod": keep("mod", inwin(pack(mod))), "modN": keep("modN", inwin(pack(modn))), "gov": pack(gov), "fires": pack(fires),
           "coverage": {"mod": cov,
                        "gov": "Belgorod, Koursk, Briansk", "sites": len(srid)}}
    open(os.path.join(SITE, "strikes_ru.js"), "w", encoding="utf-8").write("window.STRIKES_RU=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";")
    tot = lambda dd: round(sum(dd.values()))  # noqa: E731
    print(f"strikes_ru.js : MoD RF {len(rows)} mentions régionales ({tot(modn)} drones annoncés quand le nombre est donné), autorités régionales {tot(gov)} mentions, "
          f"feux près de {len(srid)} sites : {tot(fires)} ; couverture MoD {out['coverage']['mod']}")


if __name__ == "__main__":
    main()
