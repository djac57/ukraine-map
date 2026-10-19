"""Construit site/osint.js : couches OSINT agrégées, alignées sur les mois de site/data.js.

    python3 scripts/build_osint.py <dossier_brut>

Règle de prudence : rien de plus récent que le dernier mois complet terminé depuis au moins 7 jours ;
pertes géolocalisées agrégées en mailles de 0,1° par mois (ni unité, ni modèle, ni position exacte).
"""
import csv
import datetime as dt
import glob
import hashlib
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402

RAW = sys.argv[1] if len(sys.argv) > 1 else "raw"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELL = 0.1
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))   # décalage de publication : J-1 depuis le 19/10/2026 (décision de l'utilisateur ; J-7 auparavant)

GROUPS = ["chars", "blindés", "artillerie", "défense aérienne", "logistique", "aéronefs et autres"]
WS_GROUP = {
    "Tanks": 0,
    "Infantry fighting vehicles": 1, "Infantry mobility vehicles": 1, "Armoured personnel carriers": 1,
    "Armoured fighting vehicles": 1,
    "Self-propelled artillery": 2, "Towed artillery": 2, "Rocket and missile artillery": 2, "Mortars": 2,
    "Anti-aircraft systems": 3, "Radars, jammers": 3,
    "Transport": 4, "Engineering": 4, "Command posts, communication": 4, "Ambulances, medical vehicles": 4,
}
ORYX_GROUP = {
    "Tanks": 0,
    "Armoured Fighting Vehicles": 1, "Infantry Fighting Vehicles": 1, "Armoured Personnel Carriers": 1,
    "Infantry Mobility Vehicles": 1, "Mine-Resistant Ambush Protected": 1,
    "Self-Propelled Artillery": 2, "Towed Artillery": 2, "Multiple Rocket Launchers": 2, "Rocket and Missile Artillery": 2,
    "Surface-To-Air Missile Systems": 3, "Self-Propelled Anti-Aircraft Guns": 3, "Anti-Aircraft Guns": 3, "Radars": 3,
    "Trucks, Vehicles, Jeeps, and Trains": 4, "Trucks, Vehicles, and Jeeps": 4, "Engineering Vehicles And Equipment": 4,
    "Command Posts And Communications Stations": 4, "Artillery Support Vehicles And Equipment": 4,
}


def month_list():
    src = open(os.path.join(ROOT, "site", "data.js"), encoding="utf-8").read()
    return json.loads(src[len("window.DATA="):-1])["months"]


def last_complete_month(today):
    limit = today - dt.timedelta(days=LAG_DAYS)
    first = limit.replace(day=1)
    end = first - dt.timedelta(days=1)  # dernier jour du mois précédent : forcément ≤ limite
    return f"{end.year}-{end.month:02d}"


def warspotting(months, cutoff):
    midx = {m: i for i, m in enumerate(months)}
    cells = defaultdict(int)
    per_month = [[0] * len(GROUPS) for _ in months]
    geo_n = tot_n = 0
    first = last = None
    for path in sorted(glob.glob(os.path.join(RAW, "warspotting", "*.json"))):
        for x in json.load(open(path)):
            d = x.get("date") or os.path.basename(path)[:10]
            m = d[:7]
            if m > cutoff or m not in midx:
                continue
            g = WS_GROUP.get(x.get("type"), 5)
            i = midx[m]
            per_month[i][g] += 1
            tot_n += 1
            first, last = min(first or d, d), max(last or d, d)
            geo = x.get("geo")
            if not geo:
                continue
            try:
                lat, lon = (float(v) for v in geo.split(","))
            except ValueError:
                continue
            if not (43 < lat < 56 and 21 < lon < 45):
                continue
            geo_n += 1
            cells[(math.floor(lat / CELL), math.floor(lon / CELL), i, g)] += 1
    pts = []
    for (a, b, i, g), n in sorted(cells.items(), key=lambda kv: kv[0][2]):
        pts += [a, b, i, g, n]
    return {"pts": pts, "perMonth": per_month, "total": tot_n, "geo": geo_n, "first": first, "last": last}


def oryx(months):
    path = os.path.join(RAW, "oryx_daily_count.csv")
    series = defaultdict(list)
    for r in csv.DictReader(open(path)):
        series[(r["country"], r["equipment_type"].strip())].append((r["date_recorded"], int(float(r["type_total"]))))
    for v in series.values():
        v.sort()
    last_day = max(d for v in series.values() for d, _ in v)
    out = {"lastDay": last_day, "total": [], "snapshot": {}}
    # Cumul mensuel : une entrée par preuve (photo / vidéo) dans les fichiers d'événements, par date
    # de relevé. Le compteur « All Types » quotidien a un trou de juin 2022 à octobre 2024.
    per = defaultdict(int)
    for kind in ("destroyed", "abandoned", "captured", "damaged"):
        path = os.path.join(RAW, f"oryx_event_{kind}.csv")
        for r in csv.DictReader(open(path)):
            per[(r["country"], r["date_recorded"][:7])] += 1
    cum = {"Russia": 0, "Ukraine": 0}
    for m in months:
        if m > last_day[:7]:
            out["total"].append(None)
            continue
        for c in cum:
            cum[c] += per.get((c, m), 0)
        out["total"].append([cum["Russia"], cum["Ukraine"]])
    # Répartition par type : dernier relevé de chaque catégorie (la série par type ne remonte pas à 2022)
    snap = [[0, 0] for _ in GROUPS]
    for (c, t), v in series.items():
        g = ORYX_GROUP.get(t)
        if g is None or c not in ("Russia", "Ukraine"):
            continue
        # catégories renommées : on ne garde que la variante la plus récente
        if t == "Trucks, Vehicles, and Jeeps" and (c, "Trucks, Vehicles, Jeeps, and Trains") in series:
            continue
        if t == "Multiple Rocket Launchers" and (c, "Rocket and Missile Artillery") in series:
            continue
        snap[g][0 if c == "Russia" else 1] += v[-1][1]
    allt = [series[(c, "All Types")][-1][1] for c in ("Russia", "Ukraine")]
    snap[5] = [allt[k] - sum(s[k] for s in snap[:5]) for k in (0, 1)]
    out["snapshot"] = snap
    return out


def missiles(months, cutoff):
    cat = {r["model"]: r["category"] for r in csv.DictReader(open(os.path.join(RAW, "missiles_and_uavs.csv")))}
    midx = {m: i for i, m in enumerate(months)}
    per = [[0, 0, 0, 0] for _ in months]  # missiles lancés, abattus ; drones lancés, abattus

    def num(v):
        try:
            f = float(v)
            return 0 if math.isnan(f) else int(round(f))
        except (TypeError, ValueError):
            return 0

    first = last = None
    for r in csv.DictReader(open(os.path.join(RAW, "missile_attacks_daily.csv"))):
        d = (r["time_end"] or r["time_start"])[:10]
        m = d[:7]
        if m > cutoff or m not in midx:
            continue
        model = r["model"]
        drone = cat.get(model) == "UAV" or bool(re.search(r"Shahed|Geran|Gerbera|UAV|drone|Lancet|Orlan|Merlin|Supercam|Zala|Italmas|Parodiya|Harpia", model, re.I))
        if cat.get(model) and cat[model] != "UAV":
            drone = False
        k = 2 if drone else 0
        per[midx[m]][k] += num(r["launched"])
        per[midx[m]][k + 1] += num(r["destroyed"])
        first, last = min(first or d, d), max(last or d, d)
    return {"perMonth": per, "first": first, "last": last}


# Lieux de lancement cités par l'armée de l'air ukrainienne (lon, lat) ; directions vagues ignorées
LAUNCH = {
    "Primorsko-Akhtarsk": (38.17, 46.05), "Kursk oblast": (36.19, 51.73), "Кursk oblast": (36.19, 51.73),
    "Oryol oblast": (36.08, 52.97), "Crimea": (34.10, 45.30), "Millerovo": (40.40, 48.92),
    "Bryansk oblast": (34.36, 53.25), "Hvardiiske": (34.02, 45.11), "Shatalovo": (32.47, 54.33),
    "Chauda": (35.84, 45.00), "Donetsk oblast": (37.80, 48.00), "Yeysk": (38.27, 46.71), "Black Sea": (32.50, 44.00),
    "Bleak Sea": (32.50, 44.00), "Kacha": (33.55, 44.78), "Caspian Sea": (49.50, 42.50), "Sea of Azov": (36.50, 46.00),
    "Zaporizhzhia oblast": (35.37, 46.84), "Saratov oblast": (46.21, 51.48), "Engels-2": (46.21, 51.48),
    "Prymorsk": (36.35, 46.73), "Vologda oblast": (39.90, 59.20), "Belgorod oblast": (36.59, 50.60),
    "Belgorod": (36.59, 50.60), "Voronezh oblast": (39.20, 51.66), "Rostov oblast": (39.70, 47.20),
    "Balaklava": (33.60, 44.50), "Volgodonsk": (42.15, 47.51), "eastern coast of Sea of Azov": (38.20, 46.30),
    "Volgograd oblast": (44.50, 48.70), "Berdiansk": (36.78, 46.75), "Olenya": (33.45, 68.15), "Olenegorsk": (33.45, 68.15),
    "Seshcha": (33.33, 53.73), "Novorossiysk": (37.77, 44.72), "Krasnodar Krai": (38.98, 45.04),
    "Tambov oblast": (41.45, 52.72), "Samara oblast": (50.10, 53.20), "Ryazan oblast": (39.74, 54.63),
    "Astrakhan oblast": (48.03, 46.35), "Tokmak": (35.71, 47.25), "Lipetsk oblast": (39.60, 52.60),
    "Taganrog": (38.90, 47.21), "Kherson oblast": (33.60, 46.60), "Belarus": (27.56, 53.90), "Tula oblast": (37.62, 54.19),
    "Belbek": (33.58, 44.69), "Dzhankoi": (34.40, 45.71), "Yalta": (34.16, 44.50), "Cape Tarkhankut": (32.50, 45.35),
    "Luhansk oblast": (39.30, 48.57), "Anapa": (37.32, 44.89), "Nizhny Novgorod oblast": (44.00, 56.30),
    "Sevastopol": (33.52, 44.60), "Soltsy-2": (30.30, 58.12), "Shaykovka": (34.36, 54.23), "Kapustin Yar": (45.77, 48.58),
    "Savasleyka": (42.30, 55.43), "Feodosia": (35.38, 45.03), "Morozovsk": (41.82, 48.35), "Melitopol": (35.37, 46.84),
}
# Cibles : oblast (code ISO 3166-2:UA, comme les régions du fond de carte)
TARGET = {
    "Kyiv oblast": "UA-32", "Kyiv oblast (south)": "UA-32", "Kyiv": "UA-30", "Odesa oblast": "UA-51", "Odesa": "UA-51",
    "Snake Island": "UA-51", "Dnipropetrovsk oblast": "UA-12", "Dnipropetrovsk oblas": "UA-12", "Dnipro": "UA-12",
    "Dnipro Raion": "UA-12", "Kryvyi Rih": "UA-12", "Kryvyi Rih Raion": "UA-12", "Synelnykove Raion": "UA-12",
    "Pavlohrad Raion": "UA-12", "Novomoskovsk Raion": "UA-12", "Kamianske Raion": "UA-12", "Kharkiv oblast": "UA-63",
    "Kharkiv": "UA-63", "Poltava oblast": "UA-53", "Kremenchuk": "UA-53", "Myrhorod Raion": "UA-53", "Lviv oblast": "UA-46",
    "Zaporizhzhia oblast": "UA-23", "Zaporizhzhia": "UA-23", "Vinnytsia oblast": "UA-05", "Vinnytsia": "UA-05",
    "Mykolaiv oblast": "UA-48", "Ochakiv": "UA-48", "Khmelnytskyi oblast": "UA-68", "Starokostiantyniv": "UA-68",
    "Kirovohrad oblast": "UA-35", "Rivne oblast": "UA-56", "Ternopil oblast": "UA-61", "Sumy oblast": "UA-59",
    "Sumy": "UA-59", "Zhytomyr oblast": "UA-18", "Chernivtsi oblast": "UA-77", "Ivano-Frankivsk oblast": "UA-26",
    "Kolomyia": "UA-26", "Kherson oblast": "UA-65", "Kherson": "UA-65", "Donetsk oblast": "UA-14", "Kramatorsk": "UA-14",
    "Lutsk oblast": "UA-07", "Lutsk": "UA-07", "Volyn oblast": "UA-07", "Chernihiv oblast": "UA-74",
    "Cherkasy oblast": "UA-71",
}


def arcs(months, cutoff):
    """Arcs agrégés lieu de lancement -> oblast visé, par mois (lancés, répartis à parts égales)."""
    midx = {m: i for i, m in enumerate(months)}
    names = sorted(set(LAUNCH))
    lidx = {n: i for i, n in enumerate(names)}
    agg = defaultdict(float)
    tot = defaultdict(float)
    spec = defaultdict(float)
    for r in csv.DictReader(open(os.path.join(RAW, "missile_attacks_daily.csv"))):
        m = (r["time_end"] or r["time_start"])[:7]
        if m > cutoff or m not in midx:
            continue
        try:
            n = float(r["launched"])
        except ValueError:
            continue
        if math.isnan(n) or n <= 0:
            continue
        tot[m] += n
        lp = [x.strip() for x in re.split(r" and |, ", r["launch_place"] or "") if x.strip() in LAUNCH]
        tg = [TARGET[x.strip()] for x in re.split(r" and |, ", r["target_main"] or "") if x.strip() in TARGET]
        if not lp or not tg:
            continue
        spec[m] += n
        share = n / (len(lp) * len(tg))
        for a in lp:
            for b in set(tg):
                agg[(midx[m], lidx[a], b)] += share
    out = [[i, l, t, round(v)] for (i, l, t), v in sorted(agg.items()) if v >= 1]
    share = round(sum(spec.values()) / max(1, sum(tot.values())) * 100)
    return {"places": [[n, *LAUNCH[n]] for n in names], "arcs": out, "specificShare": share}


def ucdp(months, cutoff):
    """Événements UCDP (GED 26.1 jusqu'en 2025, puis « candidate » mensuels), Ukraine et Russie frontalière, précision ≤ 25 km."""
    midx = {m: i for i, m in enumerate(months)}
    rows = []
    z = os.path.join(RAW, "ucdp", "ged261-csv.zip")
    if os.path.exists(z):
        with zipfile.ZipFile(z) as zf:
            rows += list(csv.DictReader(io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="utf-8")))
    seen = {r["id"] for r in rows}
    for f in sorted(glob.glob(os.path.join(RAW, "ucdp", "GEDEvent_v*.csv"))):
        rows += [r for r in csv.DictReader(open(f, encoding="utf-8")) if r["id"] not in seen]
    cells = defaultdict(lambda: [0, 0])
    ground = defaultdict(lambda: [0, 0])
    per = [0] * len(months)
    last = None
    remote_n = 0
    for r in rows:
        if r["country"] not in ("Ukraine", "Russia", "Russia (Soviet Union)") or r["where_prec"] not in ("1", "2"):
            continue
        if r["country"] != "Ukraine" and not (44 <= float(r["latitude"]) <= 56 and 30 <= float(r["longitude"]) <= 42):
            continue  # Russie : seulement les régions frontalières (Briansk, Koursk, Belgorod…)
        m = r["date_start"][:7]
        if m > cutoff or m not in midx:
            continue
        i = midx[m]
        lat, lon = float(r["latitude"]), float(r["longitude"])
        key = (math.floor(lat / CELL), math.floor(lon / CELL), i)
        c = cells[key]
        c[0] += 1
        c[1] += int(float(r["best"] or 0))
        per[i] += 1
        last = max(last or m, m)
        # contact ou frappe à distance : mots du titre de la dépêche, sinon victimes (civils seulement = frappe)
        txt = (r.get("source_headline") or "") + " " + (r.get("source_article") or "")
        civ_only = float(r.get("deaths_civilians") or 0) > 0 and float(r.get("deaths_a") or 0) + float(r.get("deaths_b") or 0) == 0
        remote = not UCDP_GROUND.search(txt) and (bool(UCDP_REMOTE.search(txt)) or (civ_only and not UCDP_ART.search(txt)))
        if remote:
            remote_n += 1
        else:
            g = ground[key]
            g[0] += 1
            g[1] += int(float(r["best"] or 0))
    pts = [x for (a, b, i), (n, d) in sorted(cells.items(), key=lambda kv: kv[0][2]) for x in (a, b, i, n, d)]
    gpts = [x for (a, b, i), (n, d) in sorted(ground.items(), key=lambda kv: kv[0][2]) for x in (a, b, i, n, d)]
    print(f"UCDP : {sum(per)} événements, dont {remote_n} frappes à distance écartées du contact (missiles, drones, aviation, victimes civiles seules)", flush=True)
    return {"pts": pts, "ground": gpts, "perMonth": per, "last": last}


# classement des événements UCDP (contact au sol et artillerie / frappe à distance)
UCDP_REMOTE = re.compile(r"missile|drone|uav|shahed|air ?strike|airstrike|guided bomb|glide bomb|\bkab\b|bomb|rocket|ballistic|cruise", re.I)
UCDP_GROUND = re.compile(r"clash|assault|fight|battle|storm|offensive|infantry|skirmish|counter-?attack|ground", re.I)
UCDP_ART = re.compile(r"shell|artillery|mortar|mlrs|grad|howitzer", re.I)


def main():
    guard.require_files("build_osint.py", [os.path.join(RAW, f) for f in (
        "oryx_daily_count.csv", "oryx_event_destroyed.csv", "oryx_event_abandoned.csv", "oryx_event_captured.csv",
        "oryx_event_damaged.csv", "missile_attacks_daily.csv", "missiles_and_uavs.csv", "ucdp/ged261-csv.zip")]
        + [os.path.join(RAW, "warspotting")])
    months = month_list()
    today = dt.date.today()
    cutoff = last_complete_month(today)
    data = {"generated": today.isoformat(), "cutoff": cutoff, "groups": GROUPS,
            "ws": warspotting(months, cutoff), "oryx": oryx(months), "missiles": missiles(months, cutoff),
            "launch": arcs(months, cutoff), "ucdp": ucdp(months, cutoff)}
    old = guard.published("osint.js", "OSINT")
    if old:
        guard.check("build_osint.py", [
            ("pertes WarSpotting", data["ws"]["total"], old["ws"]["total"], "count"),
            ("pertes géolocalisées", data["ws"]["geo"], old["ws"]["geo"], "count"),
            ("dernière perte", data["ws"]["last"], old["ws"]["last"], "max"),
            ("événements UCDP", sum(data["ucdp"]["perMonth"]), sum(old["ucdp"]["perMonth"]), "count"),
            ("dernier mois UCDP", data["ucdp"]["last"], old["ucdp"]["last"], "max"),
            ("mois de missiles renseignés", sum(1 for v in data["missiles"]["perMonth"] if any(v)),
             sum(1 for v in old["missiles"]["perMonth"] if any(v)), "max"),
            ("dernier relevé Oryx", data["oryx"]["lastDay"], old["oryx"]["lastDay"], "max"),
            ("mois de coupure", data["cutoff"], old["cutoff"], "max"),
        ])
    js = "window.OSINT=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";"
    out = os.path.join(ROOT, "site", "osint.js")
    open(out, "w", encoding="utf-8").write(js)
    digest = hashlib.sha1(js.encode()).hexdigest()[:10]
    page = os.path.join(ROOT, "site", "index.html")
    src = open(page, encoding="utf-8").read()
    new = re.sub(r'src="osint\.js(\?v=[\w-]+)?"', f'src="osint.js?v={digest}"', src)
    if new != src:
        open(page, "w", encoding="utf-8").write(new)
    ws = data["ws"]
    print(f"osint.js : {len(js) // 1024} Ko · WarSpotting {ws['total']} pertes ({ws['geo']} géolocalisées, "
          f"{ws['first']} → {ws['last']}) · Oryx au {data['oryx']['lastDay']} · missiles {data['missiles']['first']} → "
          f"{data['missiles']['last']} · UCDP jusqu'à {data['ucdp']['last']} ({sum(data['ucdp']['perMonth'])} événements) · "
          f"{len(data['launch']['arcs'])} arcs (cibles précisées : {data['launch']['specificShare']} % des lancements) · coupure {cutoff}")


if __name__ == "__main__":
    main()
