"""Signaux Telegram QUOTIDIENS par maille de 2 km (et par tronçon de 24 km), du 24/02/2022 à J-1 (20/10/2026).

Mêmes canaux, même géocodage et mêmes attributions que build_telegram_layer.py (tglib) : au MESSAGE pour le modèle
(tglib.events_message), à la PHRASE pour l'affichage (tglib.events). Le jour est celui de PUBLICATION du message (UTC) :
une ligne du jour d n'utilise que des messages publiés le jour d (date = date_pub).

    # local, historique complet (graine signals3 + collecte backfill_telegram.py + copie locale de la collecte CI)
    python3 scripts/build_daily_telegram.py --full --graine <signals3/internal> --hist <internal/hist> \
            [--ci <dossier contenant telegram/>] --gaz <gn_UA_tess.geojson>
    # CI : ne recalcule que les jours entièrement couverts par la collecte récente (raw/telegram, 45 jours)
    python3 scripts/build_daily_telegram.py raw raw/viina

Sorties (data/daily/, dailylib) :
  telegram_<année>.csv.gz           MODÈLE, attribution au message : date, maille, i, j, camp_source, n_localises,
                                    n_combat, n_att_ua, n_att_ru, n_sq, n_presence_ua, n_presence_ru
  telegram_troncons_<année>.csv.gz  idem par tronçon de 24 km (troncon, i0, j0 à la place de maille, i, j)
  telegram_phrase_<année>.csv.gz    AFFICHAGE, attribution à la phrase : date, maille, i, j, camp_source, n_localises,
                                    att_ua, att_ru, pres_ua, pres_ru
  telegram_couverture.csv           date, lu (1 = tous les canaux actifs ce jour-là entièrement lus), n_canaux,
                                    n_canaux_lus, n_graine_seule (canaux lus seulement par la graine : géocodage v1 au
                                    filtre, ~8 % de messages localisés en moins), n_messages (messages de combat ou de
                                    présence lus), n_localises (messages localisés, attribution au message), source
Un message compte au plus une fois par indice et par maille (ou tronçon). Aucun texte ni nom de canal.
"""
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dailylib as D  # noqa: E402
import tglib as T  # noqa: E402

START = "2022-02-24"
SEED_END = "2026-09-30"          # la graine signals3 s'arrête au 30/09/2026
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))
KINDS = ["loc", "combat", "ua", "ru", "sq", "pres_ua", "pres_ru"]
COLS = ["n_localises", "n_combat", "n_att_ua", "n_att_ru", "n_sq", "n_presence_ua", "n_presence_ru"]
PCOLS = ["n_localises", "att_ua", "att_ru", "pres_ua", "pres_ru"]
PIDX = [0, 2, 3, 5, 6]
H_CELL = ["date", "maille", "i", "j", "camp_source"] + COLS
H_TR = ["date", "troncon", "i0", "j0", "camp_source"] + COLS
H_PH = ["date", "maille", "i", "j", "camp_source"] + PCOLS
H_COV = ["date", "lu", "n_canaux", "n_canaux_lus", "n_graine_seule", "n_messages", "n_localises", "source"]


def vec(kinds):
    v = [int(k in kinds) for k in KINDS]
    v[1] = int(bool(kinds & {"ua", "ru", "sq"}))
    return v


def day_range(a, b):
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    while d <= e:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def aggregate(msgs, gaz):
    """msgs : [(jour, camp, texte)] → (cellules, tronçons, phrase, {jour: [n_messages, n_localises]})."""
    ce, tr, ph = (defaultdict(lambda: [0] * len(KINDS)) for _ in range(3))
    per = defaultdict(lambda: [0, 0])
    for d, camp, txt in msgs:
        per[d][0] += 1
        ev_m = T.events_message(txt, camp, gaz)
        if ev_m:
            per[d][1] += 1
        bytr = defaultdict(set)
        for c, kinds in ev_m.items():
            v = vec(kinds)
            w = ce[(d, c, camp)]
            for k in range(len(KINDS)):
                w[k] += v[k]
            bytr[T.tr_of_cell(c)] |= kinds
        for t, kinds in bytr.items():
            v = vec(kinds)
            w = tr[(d, t, camp)]
            for k in range(len(KINDS)):
                w[k] += v[k]
        for c, kinds in T.events(txt, camp, gaz).items():
            v = vec(kinds)
            w = ph[(d, c, camp)]
            for k in range(len(KINDS)):
                w[k] += v[k]
    return ce, tr, ph, per


def rows_of(ce, tr, ph):
    rc = [[d, c, c % T.NX, c // T.NX, camp] + v for (d, c, camp), v in ce.items() if any(v)]
    rt = [[d, t, *T.tr_origin(t), camp] + v for (d, t, camp), v in tr.items() if any(v)]
    rp = [[d, c, c % T.NX, c // T.NX, camp] + [v[k] for k in PIDX] for (d, c, camp), v in ph.items() if any(v[k] for k in PIDX)]
    return rc, rt, rp


# ---------------------------------------------------------------- historique complet (local)
def load_jsonl(p):
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                m = json.loads(line)
            except ValueError:
                continue
            out[m["id"]] = m
    return out


def intervals_cover(iv, x):
    import bisect
    k = bisect.bisect_right(iv, (x, float("inf"))) - 1
    return k >= 0 and iv[k][0] <= x <= iv[k][1]


def merge_iv(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


def channel_history(name, stride, graine, hist, ci, last_day):
    """Messages retenus du canal et jours entièrement lus : (messages {id: msg}, {jour: 'hist'|'graine'|'ci'}).
    Priorité : collecte backfill (pages lues) > collecte CI (fenêtre couverte) > graine (hors pages relues)."""
    H = load_jsonl(os.path.join(hist, f"{name}.jsonl")) if hist else {}
    pages = []
    p = os.path.join(hist, f"{name}.pages.jsonl") if hist else ""
    if p and os.path.exists(p):
        best = {}
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("fin"):
                continue
            if r.get("ok") or r["b"] not in best:
                best[r["b"]] = r
        pages = [r for r in best.values() if r.get("ok")]
    hist_iv = merge_iv([(r["lo"], r["b"] - 1) for r in pages if r.get("n")])
    G = load_jsonl(os.path.join(graine, f"{name}.jsonl")) if graine else {}
    done = json.load(open(os.path.join(graine, f"{name}.done"))) if graine and os.path.exists(os.path.join(graine, f"{name}.done")) else None
    C, cst = {}, None
    if ci and os.path.exists(os.path.join(ci, f"{name}.state.json")):
        cst = json.load(open(os.path.join(ci, f"{name}.state.json"), encoding="utf-8"))
        C = {k: v for k, v in load_jsonl(os.path.join(ci, f"{name}.jsonl")).items() if cst["from"] <= v["d"] <= cst["to"]}
    msgs = {k: v for k, v in H.items() if v["d"] >= START}
    for k, v in C.items():
        if k not in msgs and not intervals_cover(hist_iv, k):
            msgs[k] = v
    for k, v in G.items():
        if k not in msgs and not intervals_cover(hist_iv, k) and v["d"] <= SEED_END and not (cst and cst["from"] <= v["d"]):
            msgs[k] = {"id": k, "d": v["d"], "camp": v["camp"], "t": v["t"]}
    # --- jours entièrement lus ---
    # ancres identifiant → jour (pages relues, messages de la graine) ; un identifiant non couvert rend incertains les jours
    # entre l'ancre inférieure et l'ancre supérieure
    anchors = sorted({(r["lo"], r["dlo"]) for r in pages if r.get("n")} | {(r["hi"], r["dhi"]) for r in pages if r.get("n")}
                     | {(k, v["d"]) for k, v in G.items()} | {(k, v["d"]) for k, v in H.items()})
    cov = {}
    first = min([a[1] for a in anchors] + ["9999"])
    first = max(first, START)
    # graine : grille complète au pas 20 jusqu'au 30/09/2026 ; au pas 40, une page sur deux (couverture par la seule graine
    # = « demi », complétée par les pages impaires relues)
    seed_iv = []
    if done:
        top0 = done["top"]
        a = top0 + 1
        if stride == 20:
            seed_iv = [(1, top0)]
        else:
            seed_iv = merge_iv([(a - 40 * m - 20, a - 40 * m - 1) for m in range(0, a // 40 + 1)])
    ids = [x[0] for x in anchors]
    top_known = max([r["hi"] for r in pages if r.get("n")] + list(G) + [0])
    lo_id = min(ids + [top_known])

    def gap_days(known):
        """Jours incertains : ceux qu'un intervalle d'identifiants non lu peut contenir (entre ses ancres)."""
        import bisect
        gaps, cur = [], lo_id
        for a, b in merge_iv(known):
            if b < lo_id:
                continue
            if a > cur:
                gaps.append((cur, a - 1))
            cur = max(cur, b + 1)
        if cur <= top_known:
            gaps.append((cur, top_known))
        out = set()
        for ga, gb in gaps:
            k1 = bisect.bisect_left(ids, ga) - 1
            k2 = bisect.bisect_right(ids, gb)
            d1 = anchors[k1][1] if k1 >= 0 else START
            d2 = anchors[k2][1] if k2 < len(anchors) else last_day
            out.update(day_range(max(d1, START), min(d2, last_day)))
        return out

    unsure_all, unsure_hist = gap_days(hist_iv + seed_iv), gap_days(hist_iv)
    hist_days = set()
    for r in pages:
        if r.get("n"):
            hist_days.update(day_range(max(r["dlo"], START), min(r["dhi"], last_day)))
    # la page la plus haute relue : les jours après son dernier message ne sont lus que si elle est la page du top actuel
    for d in day_range(first, last_day):
        if cst and cst["from"] <= d < cst["to"] and d not in hist_days:
            cov[d] = "ci"
        elif d in hist_days and d not in unsure_hist:
            cov[d] = "hist"
        elif d <= SEED_END and done and d not in unsure_all:
            cov[d] = "graine" if d not in hist_days else "graine+hist"
    return msgs, cov, first


def full(args):
    opt = lambda k: args[args.index(k) + 1] if k in args else None  # noqa: E731
    graine, hist, ci, gazp = opt("--graine"), opt("--hist"), opt("--ci"), opt("--gaz")
    ci_dir = os.path.join(ci, "telegram") if ci else None
    today = dt.datetime.now(dt.timezone.utc).date()
    last_day = (today - dt.timedelta(days=max(LAG_DAYS, 1))).isoformat()
    gaz = T.load_gaz_all(gazp)
    print(f"localités non ambiguës : {len(gaz)} ; jours {START} → {last_day}", flush=True)
    allmsgs, covs, firsts = [], {}, {}
    for name, camp, stride in T.CHANNELS:
        msgs, cov, first = channel_history(name, stride, graine, hist, ci_dir, last_day)
        covs[name], firsts[name] = cov, first
        allmsgs.extend((m["d"], m["camp"], m["t"]) for m in msgs.values() if START <= m["d"] <= last_day)
        n = defaultdict(int)
        for v in cov.values():
            n[v] += 1
        print(f"  {name:16s} {len(msgs):6d} messages, depuis {first}, jours lus : {dict(n)}", flush=True)
    ce, tr, ph, per = aggregate(allmsgs, gaz)
    rc, rt, rp = rows_of(ce, tr, ph)
    w1 = D.write("telegram", H_CELL, rc)
    w2 = D.write("telegram_troncons", H_TR, rt)
    w3 = D.write("telegram_phrase", H_PH, rp)
    cov_rows = {}
    for d in day_range(START, last_day):
        act = [n for n in covs if firsts[n] <= d]
        lus = [n for n in act if d in covs[n]]
        g = sum(1 for n in lus if covs[n][d].startswith("graine"))
        src = "+".join(sorted({covs[n][d] for n in lus})) or "-"
        cov_rows[d] = [d, int(len(lus) == len(act) and bool(act)), len(act), len(lus), g, per[d][0], per[d][1], src]
    D.write_cov("telegram", H_COV, cov_rows)
    nl = sum(1 for r in cov_rows.values() if r[1])
    print(f"écrit : mailles {w1}, tronçons {w2}, phrase {w3} ; couverture : {nl} jours lus sur {len(cov_rows)}", flush=True)


# ---------------------------------------------------------------- CI : jours récents seulement
def ci(args):
    raw = args[0] if args else "raw"
    viina = args[1] if len(args) > 1 else os.path.join(raw, "viina")
    tdir = os.path.join(raw, "telegram")
    states = {os.path.basename(p)[:-11]: json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(tdir, "*.state.json"))}
    missing = [n for n, _, _ in T.CHANNELS if n not in states]
    if missing or not states:
        print("collecte récente incomplète (canaux sans état : " + ", ".join(missing) + ") : aucun jour remplacé", flush=True)
        return
    today = dt.datetime.now(dt.timezone.utc).date()
    cutoff = (today - dt.timedelta(days=LAG_DAYS)).isoformat()
    lo = max(st["from"] for st in states.values())
    # le jour de la collecte n'est pas complet : on s'arrête la veille du plus ancien « to »
    hi = min((dt.date.fromisoformat(min(st["to"] for st in states.values())) - dt.timedelta(days=1)).isoformat(), cutoff)
    if hi < lo:
        print(f"fenêtre commune vide ({lo} → {hi}) : rien à remplacer", flush=True)
        return
    days = set(day_range(lo, hi))
    gaz = T.load_gaz_all(os.path.join(viina, "gn_UA_tess.geojson"))
    msgs = []
    for n in states:
        for m in load_jsonl(os.path.join(tdir, f"{n}.jsonl")).values():
            if m["d"] in days:
                msgs.append((m["d"], m["camp"], m["t"]))
    ce, tr, ph, per = aggregate(msgs, gaz)
    rc, rt, rp = rows_of(ce, tr, ph)
    # garde-fou : la nouvelle version ne doit pas être plus pauvre (messages localisés) que la publiée sur les mêmes jours
    _, cov = D.read_cov("telegram")
    old = sum(int(cov[d][6]) for d in days if d in cov and cov[d][1] == "1")
    new = sum(per[d][1] for d in days)
    if old and new < 0.95 * old:
        print(f"nouvelle version plus pauvre ({new} messages localisés < 95 % de {old}) : jours publiés gardés", flush=True)
        return
    w1 = D.replace_days("telegram", H_CELL, rc, days)
    w2 = D.replace_days("telegram_troncons", H_TR, rt, days)
    w3 = D.replace_days("telegram_phrase", H_PH, rp, days)
    h, cov = D.read_cov("telegram")
    for d in days:
        cov[d] = [d, 1, len(T.CHANNELS), len(T.CHANNELS), 0, per[d][0], per[d][1], "ci"]
    D.write_cov("telegram", h or H_COV, cov)
    print(f"Telegram quotidien : {len(days)} jours remplacés ({lo} → {hi}), {len(msgs)} messages, {new} localisés ; "
          f"années réécrites : mailles {w1}, tronçons {w2}, phrase {w3}", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    full(a) if "--full" in a else ci([x for x in a if not x.startswith("--")])
