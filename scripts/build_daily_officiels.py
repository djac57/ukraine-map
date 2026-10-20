"""Bilans officiels QUOTIDIENS par maille de 2 km (data/daily/, dailylib), depuis le 24/02/2022 (20/10/2026).

Mêmes règles d'extraction que build_official_signals.py (dont on réutilise les fonctions), mais au jour et CAUSALES :
  - état-major ukrainien (t.me/s/GeneralStaffZSU) : bilan du matin (≤ 9 h) = jour précédent (date = jour décrit,
    date_pub = jour de publication) ; assauts de chaque direction répartis entre les localités citées dans son
    paragraphe ; sans localité, répartis selon les localités de la même direction des 30 jours PRÉCÉDENTS (la version
    mensuelle utilisait le mois entier, futur compris) ; le centre de chaque direction (pour lever les homonymes) est la
    médiane des localités non ambiguës de cette direction publiées JUSQU'À ce jour ;
    frappes aériennes : localités citées dans la phrase « завдав… авіаційних ударів … по … » ;
  - MoD RF (t.me/s/mod_russia, puis citations reprises par Interfax et RIA Novosti, fetch_mod_bulletins.py) :
    localités citées dans les bilans des groupements (« в районах населённых пунктов … »), date = date_pub.

    python3 scripts/build_daily_officiels.py <viina> <dossier claims> [<dossier claims>…] [--depuis AAAA-MM-JJ | --tout]

Chaque dossier claims contient GeneralStaffZSU.json / mod_russia.json (format fetch_claims.py : liste de {id, date,
text}) ou leurs variantes .jsonl (collecte historique backfill_telegram.py, phase O) ; les messages sont fusionnés par
identifiant. Par défaut (CI), seuls les 90 derniers jours sont remplacés.
Sorties :
  etatmajor_<année>.csv.gz   date, date_pub, maille, i, j, camp_source (ua), assauts (assauts du jour attribués à la
                             maille, fractionnaires), frappes_aeriennes (citations)
  etatmajor_couverture.csv   date, lu (1 = bilan du jour trouvé : une absence de ligne vaut alors zéro), n_messages
                             (bilans), assauts_total (somme des directions), assauts_localises, source
  modru_<année>.csv.gz       date, date_pub, maille, i, j, camp_source (ru), localites (citations)
  modru_couverture.csv       date, lu (1 = au moins un bilan de groupement avec une localité reconnue), n_messages
                             (bilans de groupement), n_localites, source (tg | agence | tg+agence)
Faits officiels seulement, agrégés ; aucun texte.
"""
import bisect
import datetime as dt
import glob
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_official_signals as O  # noqa: E402
import dailylib as D  # noqa: E402
import tglib as T  # noqa: E402

START = "2022-02-24"
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))
AGENCY_ID = 9_000_000_000
H_GS = ["date", "date_pub", "maille", "i", "j", "camp_source", "assauts", "frappes_aeriennes"]
H_GSC = ["date", "lu", "n_messages", "assauts_total", "assauts_localises", "source"]
H_RU = ["date", "date_pub", "maille", "i", "j", "camp_source", "localites"]
H_RUC = ["date", "lu", "n_messages", "n_localites", "source"]


def load_posts(dirs, name):
    posts = {}
    for d in dirs:
        for p in (os.path.join(d, f"{name}.json"), os.path.join(d, f"{name}.jsonl")):
            if not os.path.exists(p):
                continue
            if p.endswith(".json"):
                items = json.load(open(p, encoding="utf-8"))
            else:
                items = []
                for line in open(p, encoding="utf-8"):
                    try:
                        items.append(json.loads(line))
                    except ValueError:
                        continue
            for x in items:
                if x.get("date", "") >= START:
                    posts[x["id"]] = x
    return list(posts.values())


def day_range(a, b):
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def gs_daily(posts, idx, last):
    best = {}
    for p in posts:
        t = p["text"]
        if "напрям" not in t or "доб" not in t and "станом" not in t:
            continue
        h = O.HOUR.search(t)
        if not h:
            continue
        hour = int(h.group(1))
        d = dt.date.fromisoformat(p["date"])
        day = (d - dt.timedelta(days=1) if hour <= 9 else d).isoformat()
        prio = 2 if hour <= 9 else 1
        cur = best.get(day)
        if not cur or (prio, hour) > (cur[0], cur[1]):
            best[day] = (prio, hour, p)
    rows = defaultdict(lambda: [0.0, 0])
    cov = {}
    xs, ys = defaultdict(list), defaultdict(list)     # positions publiées jusqu'ici, par direction (médiane causale)
    hist = defaultdict(list)                          # (jour, maille) par direction (répartition de repli, 30 j)
    for day in sorted(best):
        if day > last or day < START:
            continue
        prio, hour, p = best[day]
        pub = p["date"]
        paras = []
        for para in re.split(r"\n+", p["text"]):
            m = O.DIRP2.search(para)
            if not m or O.NATIONAL.search(para):
                continue
            dk = O.direction_keys(m.group(1))
            if re.search(r"активних\s+(?:наступальних\s+)?дій\s+не\s+(?:проводив|вів|веде|було)|не\s+(?:виявлено|зафіксовано)", para) and not O.first_count(para):
                n = 0
            else:
                n = O.first_count(para)
            paras.append((dk, n, O.places_in(para, idx)))
        # les positions non ambiguës du jour entrent dans la médiane AVANT de lever les homonymes du même bilan
        for dk, n, pl in paras:
            for w, cands in pl:
                if len(cands) == 1:
                    bisect.insort(xs[dk], cands[0][0])
                    bisect.insort(ys[dk], cands[0][1])
        tot = loc = 0.0
        for dk, n, pl in paras:
            cells = []
            if xs[dk]:
                c0 = (xs[dk][len(xs[dk]) // 2], ys[dk][len(ys[dk]) // 2])
                for w, cands in pl:
                    c = min(cands, key=lambda q: (q[0] - c0[0]) ** 2 + (q[1] - c0[1]) ** 2)
                    if math.hypot((c[0] - c0[0]) * 74, (c[1] - c0[1]) * 111) <= 120:
                        k = T.cell_of(c[0], c[1])
                        if k >= 0:
                            cells.append(k)
            lo30 = (dt.date.fromisoformat(day) - dt.timedelta(days=30)).isoformat()
            if n:
                tot += n
                share = Counter(cells) if cells else Counter(k for d0, k in hist[dk] if lo30 <= d0 < day)
                s = sum(share.values())
                if s:
                    loc += n
                    for k, c in share.items():
                        rows[(day, pub, k)][0] += n * c / s
            hist[dk] += [(day, k) for k in cells]
            hist[dk] = [x for x in hist[dk] if x[0] >= lo30]
        # frappes aériennes du paragraphe national
        m = re.search(r"(?:завдав\w*|завдавав)\s+авіа\w*[^.]*?(?:район\w*|по)\s+([^.]+)\.", p["text"])
        if m:
            allpos = [(v[len(v) // 2], ys[k][len(ys[k]) // 2]) for k, v in xs.items() if v]
            for w, cands in O.places_in(m.group(1), idx):
                c = min(cands, key=lambda q: min((q[0] - a) ** 2 + (q[1] - b) ** 2 for a, b in allpos)) if allpos else cands[0]
                k = T.cell_of(c[0], c[1])
                if k >= 0:
                    rows[(day, pub, k)][1] += 1
        cov[day] = [day, 1, 1, round(tot, 3), round(loc, 3), "tg"]
    out = [[d, dp, k, k % T.NX, k // T.NX, "ua", f"{v[0]:.3f}".rstrip("0").rstrip("."), v[1]] for (d, dp, k), v in rows.items()]
    for d in day_range(START, last):
        cov.setdefault(d, [d, 0, 0, 0, 0, "-"])
    return out, cov


def ru_daily(posts, idx, last):
    rows = defaultdict(int)
    nb, nloc, src = Counter(), Counter(), defaultdict(set)
    for p in posts:
        t = p["text"]
        d = p["date"]
        if d > last or not re.search(r"группировк\w* войск", t):
            continue
        hits = O.ru_post_cells(p, idx, T.cell_of)   # découpage par segment, oblast cité, homonymes levés ou rejetés
        nb[d] += 1
        if hits:
            src[d].add("agence" if not isinstance(p["id"], int) or p["id"] >= AGENCY_ID else "tg")
            nloc[d] += len(hits)
            for g in hits:
                rows[(d, d, g)] += 1
    out = [[d, dp, g, g % T.NX, g // T.NX, "ru", n] for (d, dp, g), n in rows.items()]
    cov = {d: [d, int(nloc[d] > 0), nb[d], nloc[d], "+".join(sorted(src[d])) or "-"] for d in day_range(START, last)}
    return out, cov


def publish(source, header, rows, cov, cov_header, since):
    if since is None:
        w = D.write(source, header, rows)
        D.write_cov(source, cov_header, cov)
    else:
        days = {d for d in cov if d >= since}
        h, oc = D.read_cov(source)
        n_old = sum(1 for d in days if d in oc and oc[d][1] == "1")
        n_new = sum(1 for d in days if cov[d][1] == 1)
        if n_old and n_new < 0.9 * n_old:
            print(f"{source} : nouvelle version plus pauvre ({n_new} jours lus < 90 % de {n_old}) : jours publiés gardés", flush=True)
            return
        w = D.replace_days(source, header, rows, days)
        oc.update({d: v for d, v in cov.items() if d in days})
        D.write_cov(source, h or cov_header, oc)
    n = sum(1 for v in cov.values() if str(v[1]) == "1")
    by = Counter(d[:4] for d, v in cov.items() if str(v[1]) == "1")
    print(f"{source} : {len(rows)} lignes maille-jour, années écrites {w}, {n} jours lus sur {len(cov)} {dict(sorted(by.items()))}", flush=True)


def main():
    a = sys.argv[1:]
    since = a[a.index("--depuis") + 1] if "--depuis" in a else None
    args = [x for x in a if not x.startswith("--") and x != since]
    viina, dirs = args[0], args[1:]
    today = dt.datetime.now(dt.timezone.utc).date()
    last = (today - dt.timedelta(days=LAG_DAYS)).isoformat()
    if "--tout" in a:
        since = None
    elif since is None:
        since = (today - dt.timedelta(days=90)).isoformat()
    tess = os.path.join(viina, "gn_UA_tess.geojson")
    gs = load_posts(dirs, "GeneralStaffZSU")
    if gs:
        rows, cov = gs_daily(gs, O.load_gaz(tess), last)
        publish("etatmajor", H_GS, rows, cov, H_GSC, since)
    ru = load_posts(dirs, "mod_russia")
    if ru:
        rows, cov = ru_daily(ru, O.load_gaz_ru(tess), last)
        publish("modru", H_RU, rows, cov, H_RUC, since)


if __name__ == "__main__":
    main()
