"""UCDP GED et WarSpotting en agrégats QUOTIDIENS par maille de 2 km (data/daily/, dailylib), depuis le 24/02/2022.

    python3 scripts/build_daily_osint.py <brut> [--tout | --depuis AAAA-MM-JJ]

Par défaut (CI), seuls les jours des 90 derniers jours sont remplacés (WarSpotting relit les 60 derniers jours à chaque
passage ; UCDP publie un mois « candidate » par mois) ; --tout réécrit tout l'historique à partir du brut.
Une source dont le brut est absent n'est pas touchée (les fichiers publiés restent).

WarSpotting (pertes RUSSES documentées, ukr.warspotting.net, usage non lucratif avec crédit ; l'API n'a pas de pertes
ukrainiennes) : ni unité, ni modèle, ni numéro de fiche ; seulement les fiches géolocalisées.
  warspotting_<année>.csv.gz : date (jour de la perte), date_pub_min, maille, i, j, camp_source (= camp qui a perdu le
  matériel : ru), n_pertes, n_detruits, n_endommages, n_abandonnes, n_captures, n_chars, n_blindes, n_artillerie,
  n_autres.
  date_pub_min : BORNE INFÉRIEURE de la date de mise en ligne (l'API n'en donne pas) : les fiches sont numérotées dans
  l'ordre d'ajout, une fiche n'a donc pas pu être ajoutée avant la plus récente des pertes des fiches de numéro inférieur
  ou égal (maximum courant, selon l'identifiant, du 90e centile des dates des 200 fiches précédentes). Un lecteur causal au jour d lit date_pub_min ≤ d, en
  sachant que la vraie mise en ligne peut être plus tardive (ajouts tardifs fréquents).
  warspotting_couverture.csv : date, lu (fichier du jour lu), n_messages (fiches), n_geolocalises.
UCDP GED (CC BY 4.0) : GED 26.1 (jusqu'à fin 2025) puis versions « candidate » mensuelles ; Ukraine et Russie
frontalière, précision de lieu ≤ 25 km (where_prec 1 ou 2), comme build_osint.py.
  ucdp_<année>.csv.gz : date (date_start), date_pub (estimée : dernier jour du mois suivant, rythme de publication des
  versions candidate), maille, i, j, camp_source (tiers), n_evenements, n_sol (contact au sol ou artillerie, règle de
  build_osint.py), morts_best, morts_ru, morts_ua, morts_civils, morts_inconnus (dyade Russie-Ukraine : deaths_a = ru,
  deaths_b = ua ; autres dyades : deaths_a / deaths_b comptés « inconnus »), n_multijour (date_end > date_start).
  Les comptes viennent de la version FINALE du GED (révisée après coup) : légère information future sur les comptes,
  pas sur l'existence des événements.
  ucdp_couverture.csv : date, lu (jour couvert par une version publiée), n_messages (événements, toutes précisions).
"""
import csv
import datetime as dt
import glob
import io
import json
import os
import sys
import zipfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dailylib as D  # noqa: E402
import tglib as T  # noqa: E402

START = "2022-02-24"
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))
WS_GROUP = {"Tanks": 0, "Infantry fighting vehicles": 1, "Infantry mobility vehicles": 1, "Armoured personnel carriers": 1,
            "Armoured fighting vehicles": 1, "Self-propelled artillery": 2, "Towed artillery": 2,
            "Rocket and missile artillery": 2, "Mortars": 2}
WS_STATUS = {"Destroyed": 1, "Damaged": 2, "Abandoned": 3, "Captured": 4}
H_WS = ["date", "date_pub_min", "maille", "i", "j", "camp_source", "n_pertes", "n_detruits", "n_endommages", "n_abandonnes",
        "n_captures", "n_chars", "n_blindes", "n_artillerie", "n_autres"]
H_UC = ["date", "date_pub", "maille", "i", "j", "camp_source", "n_evenements", "n_sol", "morts_best", "morts_ru", "morts_ua",
        "morts_civils", "morts_inconnus", "n_multijour"]


def day_range(a, b):
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def month_end_next(d):
    y, m = int(d[:4]), int(d[5:7]) + 1
    y, m = y + (m > 12), (m - 1) % 12 + 1
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()


def warspotting(raw, last):
    files = sorted(glob.glob(os.path.join(raw, "warspotting", "*.json")))
    if not files:
        return None
    entries, cov = [], {}
    for p in files:
        day = os.path.basename(p)[:10]
        if not (START <= day <= last):
            continue
        xs = json.load(open(p))
        cov[day] = [day, 1, len(xs), sum(1 for x in xs if x.get("geo"))]
        entries += [x for x in xs if x.get("id") is not None]
    # borne inférieure de la mise en ligne : maximum courant de la date de perte selon l'identifiant
    # (robuste aux dates erronées : 90e centile des 200 fiches précédentes, puis maximum courant ; une seule fiche mal
    # datée dans le futur ne repousse donc pas tout)
    pub, run, win = {}, "", []
    for x in sorted(entries, key=lambda x: x["id"]):
        win.append(x.get("date") or "")
        win = win[-200:]
        q = sorted(win)[int(0.9 * (len(win) - 1))]
        run = max(run, q)
        pub[x["id"]] = run
    agg = defaultdict(lambda: [0] * 9)
    for x in entries:
        d = x.get("date")
        if not d or not (START <= d <= last) or not x.get("geo"):
            continue
        try:
            lat, lon = (float(v) for v in x["geo"].split(","))
        except ValueError:
            continue
        c = T.cell_of(lon, lat)
        if c < 0:
            continue
        v = agg[(d, max(d, pub[x["id"]]), c)]
        v[0] += 1
        s = WS_STATUS.get(x.get("status"))
        if s:
            v[s] += 1
        v[5 + WS_GROUP.get(x.get("type"), 3)] += 1
    rows = [[d, dp, c, c % T.NX, c // T.NX, "ru"] + v for (d, dp, c), v in agg.items()]
    for d in day_range(START, last):
        cov.setdefault(d, [d, 0, 0, 0])
    return rows, cov


UCDP_REMOTE = UCDP_GROUND = UCDP_ART = None


def ucdp(raw, last):
    import re
    global UCDP_REMOTE, UCDP_GROUND, UCDP_ART
    UCDP_REMOTE = re.compile(r"missile|drone|uav|shahed|air ?strike|airstrike|guided bomb|glide bomb|\bkab\b|bomb|rocket|ballistic|cruise", re.I)
    UCDP_GROUND = re.compile(r"clash|assault|fight|battle|storm|offensive|infantry|skirmish|counter-?attack|ground", re.I)
    UCDP_ART = re.compile(r"shell|artillery|mortar|mlrs|grad|howitzer", re.I)
    z = os.path.join(raw, "ucdp", "ged261-csv.zip")
    if not os.path.exists(z):
        return None
    with zipfile.ZipFile(z) as zf:
        rows = list(csv.DictReader(io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="utf-8")))
    seen = {r["id"] for r in rows}
    covered_until = "2025-12-31"
    for f in sorted(glob.glob(os.path.join(raw, "ucdp", "GEDEvent_v*.csv"))):
        new = [r for r in csv.DictReader(open(f, encoding="utf-8")) if r["id"] not in seen]
        rows += new
        if new:
            covered_until = max(covered_until, max(r["date_start"][:10] for r in new)[:7] + "-31")
    agg = defaultdict(lambda: [0] * 8)
    tot = defaultdict(int)
    for r in rows:
        d = r["date_start"][:10]
        if not (START <= d <= last) or r["country"] not in ("Ukraine", "Russia", "Russia (Soviet Union)"):
            continue
        lat, lon = float(r["latitude"]), float(r["longitude"])
        if r["country"] != "Ukraine" and not (44 <= lat <= 56 and 30 <= lon <= 42):
            continue
        tot[d] += 1
        if r["where_prec"] not in ("1", "2"):
            continue
        c = T.cell_of(lon, lat)
        if c < 0:
            continue
        txt = (r.get("source_headline") or "") + " " + (r.get("source_article") or "")
        da, db = float(r.get("deaths_a") or 0), float(r.get("deaths_b") or 0)
        civ, unk = float(r.get("deaths_civilians") or 0), float(r.get("deaths_unknown") or 0)
        civ_only = civ > 0 and da + db == 0
        remote = not UCDP_GROUND.search(txt) and (bool(UCDP_REMOTE.search(txt)) or (civ_only and not UCDP_ART.search(txt)))
        ruua = r["side_a"].startswith("Government of Russia") and r["side_b"] == "Government of Ukraine"
        v = agg[(d, month_end_next(d), c)]
        v[0] += 1
        v[1] += int(not remote)
        v[2] += int(float(r["best"] or 0))
        v[3] += int(da) if ruua else 0
        v[4] += int(db) if ruua else 0
        v[5] += int(civ)
        v[6] += int(unk) + (0 if ruua else int(da + db))
        v[7] += int(r["date_end"][:10] > d)
    out = [[d, dp, c, c % T.NX, c // T.NX, "tiers"] + v for (d, dp, c), v in agg.items()]
    cov = {d: [d, int(d <= covered_until), tot.get(d, 0)] for d in day_range(START, last)}
    return out, cov


def publish(source, header, rows, cov, cov_header, since):
    if since is None:
        w = D.write(source, header, rows)
        D.write_cov(source, cov_header, cov)
    else:
        days = {d for d in cov if d >= since}
        # garde-fou : pas de remplacement par une version plus pauvre (lignes des jours concernés)
        _, old = D.read(source)
        n_old = sum(1 for r in old if r[0] in days)
        n_new = sum(1 for r in rows if r[0] in days)
        if n_old and n_new < 0.9 * n_old:
            print(f"{source} : nouvelle version plus pauvre ({n_new} < 90 % de {n_old} lignes) : jours publiés gardés", flush=True)
            return
        w = D.replace_days(source, header, rows, days)
        h, oc = D.read_cov(source)
        oc.update({d: v for d, v in cov.items() if d in days})
        D.write_cov(source, h or cov_header, oc)
    n = sum(1 for v in cov.values() if str(v[1]) == "1")
    print(f"{source} : {len(rows)} lignes maille-jour, années écrites {w}, {n} jours lus sur {len(cov)}", flush=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    since = sys.argv[sys.argv.index("--depuis") + 1] if "--depuis" in sys.argv else None
    args = [a for a in args if a != since]
    raw = args[0] if args else "raw"
    today = dt.datetime.now(dt.timezone.utc).date()
    last = (today - dt.timedelta(days=LAG_DAYS)).isoformat()
    if "--tout" in sys.argv:
        since = None
    elif since is None:
        since = (today - dt.timedelta(days=90)).isoformat()
    ws = warspotting(raw, last)
    if ws:
        publish("warspotting", H_WS, ws[0], ws[1], ["date", "lu", "n_messages", "n_geolocalises"], since)
    else:
        print("WarSpotting : brut absent, fichiers publiés gardés", flush=True)
    uc = ucdp(raw, last)
    if uc:
        publish("ucdp", H_UC, uc[0], uc[1], ["date", "lu", "n_messages"], since)
    else:
        print("UCDP : brut absent, fichiers publiés gardés", flush=True)


if __name__ == "__main__":
    main()
