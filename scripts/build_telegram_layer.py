"""Couche d'indicateur « signaux Telegram (30 j) » et agrégats réutilisables par le modèle.

DEUX USAGES, DEUX ATTRIBUTIONS (décision du 19/10/2026, docs/ETAT.md § 6) :
  - MODÈLE (data/telegram_troncons.csv, lu par build_direction.py) : attribution au MESSAGE (tglib.events_message,
    règle historique de signals3) : les bulletins multi-lieux portent une information d'activité par secteur utile au
    modèle de sens (avec la phrase : AUC sens 0,738 → 0,707) ; géocodage corrigé (adjectifs de région écartés, lieux
    après « село / н. п. », énumérations) ;
  - AFFICHAGE (data/telegram_cells.csv, site/telegram_cells.js, site/telegram_troncons.js) : attribution à la PHRASE
    (tglib.events : une attaque ou une présence ne compte que pour les localités de sa phrase ; phrase étalée sur plus de
    30 km ramenée à son tronçon le plus cité), pour qu'un signal repéré à un endroit s'affiche à cet endroit.
Messages de combat ou de présence des 15 canaux, selon le camp de la source
(ru, ua, tiers), classés « qui attaque » (tg_sens.classify_strict : attaque ukrainienne, attaque russe, statu quo) et
« présence signalée » (tg_sens.presence). Un message compte au plus une fois par indice et par maille (ou tronçon).
Agrégats seulement : aucun texte, aucun nom de canal, aucune unité ni personne.

    python3 scripts/build_telegram_layer.py <brut> [<viina>] [--seed <dossier jsonl historique>]

  <brut>/telegram/*.jsonl, *.state.json  collecte incrémentale (fetch_telegram.py, ~45 derniers jours) ;
  <viina>/gn_UA_tess.geojson              localités d'Ukraine (défaut <brut>/viina), plus data/ru_border.tsv ;
  --seed                                  messages historiques (ukraine-recherche/signals3/internal/*.jsonl, champs d, camp,
                                          t) : reconstruit tout l'historique qu'ils couvrent (sans garde-fou de recul,
                                          puisque la règle d'attribution peut changer les comptes).

Fusion incrémentale (comme build_strikes_ru.py) : les périodes (mois, décades) entièrement couvertes par la collecte
récente remplacent les comptes publiés, sauf si la nouvelle version est plus pauvre (moins de 95 % des messages localisés
publiés pour la période) ; les autres périodes gardent les comptes publiés. Rien après J-7 (UTC). Sorties :
  data/telegram_troncons.csv   tronçon de 24 km × (mois | décade) : pas, debut, troncon, i0, j0, camp_source, n_localises,
                               n_combat, n_att_ua, n_att_ru, n_sq, n_presence_ua, n_presence_ru, partiel
  data/telegram_cells.csv      maille de 2 km × (mois | 30j = 30 derniers jours affichables) : pas, debut, maille, i, j,
                               camp_source, n_localises, att_ua, att_ru, pres_ua, pres_ru, partiel
  site/telegram_troncons.js    mois : sources russes → [mois, tronçon, attaques UA, présence UA] ; sources ukrainiennes →
                               [mois, tronçon, attaques RU, présence RU] ; « upto » = données complètes jusqu'au
  site/telegram_cells.js       TG_CELLS = {nx, camps, months, upto, m: [[i_mois, maille, i_camp, att_ua, att_ru, pres_ua,
                               pres_ru]…], recent: {from, to, rows: [[maille, i_camp, att_ua, att_ru, pres_ua, pres_ru]…]}}
Code 3 (guard) si la nouvelle version est plus pauvre que celle publiée.
"""
import csv
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402
import tglib as T  # noqa: E402

ROOT = T.ROOT
TR_CSV = os.path.join(ROOT, "data", "telegram_troncons.csv")
CELL_CSV = os.path.join(ROOT, "data", "telegram_cells.csv")
TR_JS = os.path.join(ROOT, "site", "telegram_troncons.js")
CELL_JS = os.path.join(ROOT, "site", "telegram_cells.js")
LAG_DAYS = 7
RECENT_DAYS = 30
KINDS = ["loc", "combat", "ua", "ru", "sq", "pres_ua", "pres_ru"]
TR_COLS = ["n_localises", "n_combat", "n_att_ua", "n_att_ru", "n_sq", "n_presence_ua", "n_presence_ru"]
CELL_COLS = ["n_localises", "att_ua", "att_ru", "pres_ua", "pres_ru"]
CELL_IDX = [0, 2, 3, 5, 6]          # position des colonnes de maille dans le vecteur KINDS
CAMPS = ["ru", "ua", "tiers"]


def decade(d):
    return d[:8] + ("01" if int(d[8:]) <= 10 else "11" if int(d[8:]) <= 20 else "21")


def period_end(pas, debut):
    if pas == "mois":
        y, m = int(debut[:4]), int(debut[5:7])
        return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
    if pas == "30j":
        return (dt.date.fromisoformat(debut) + dt.timedelta(days=RECENT_DAYS - 1)).isoformat()
    if debut[8:] in ("01", "11"):
        return debut[:8] + ("10" if debut[8:] == "01" else "20")
    return period_end("mois", debut[:7])


def first_full(pas, d):
    """Début de la première période (mois ou décade) entièrement postérieure ou égale à la date d."""
    start = d[:7] if pas == "mois" else decade(d)
    if (pas == "mois" and d[8:] == "01") or (pas == "decade" and d == start):
        return start
    nxt = (dt.date.fromisoformat(period_end(pas, start)) + dt.timedelta(days=1)).isoformat()
    return nxt[:7] if pas == "mois" else nxt


def vec(kinds):
    v = [int(k in kinds) for k in KINDS]
    v[1] = int(bool(kinds & {"ua", "ru", "sq"}))
    return v


def _add(dd, key, v):
    w = dd[key]
    for k in range(len(KINDS)):
        w[k] += v[k]


def _bytr(ev):
    out = defaultdict(set)
    for c, kinds in ev.items():
        out[T.tr_of_cell(c)] |= kinds
    return out


def aggregate(msgs, recent=None):
    """msgs : [(jour, camp, attribution au message, attribution à la phrase)] → trois tables
    {(pas, debut, unité, camp): vecteur KINDS} :
      tr  (MODÈLE, data/telegram_troncons.csv) : tronçons × mois et décade, attribution au message ;
      trd (AFFICHAGE, site/telegram_troncons.js) : tronçons × mois, attribution à la phrase ;
      ce  (AFFICHAGE, mailles) : mailles × mois (+ « 30j » si recent = (début, fin)), attribution à la phrase."""
    new = lambda: defaultdict(lambda: [0] * len(KINDS))  # noqa: E731
    tr, trd, ce = new(), new(), new()
    links = defaultdict(list)   # (maille, camp) → [(jour, canal, numéro)] sur les 30 derniers jours (attribution à la phrase)
    for d, camp, ev_m, ev_s, *ref in msgs:
        for t, kinds in _bytr(ev_m).items():
            for pas, deb in (("mois", d[:7]), ("decade", decade(d))):
                _add(tr, (pas, deb, t, camp), vec(kinds))
        for t, kinds in _bytr(ev_s).items():
            _add(trd, ("mois", d[:7], t, camp), vec(kinds))
        for c, kinds in ev_s.items():
            _add(ce, ("mois", d[:7], c, camp), vec(kinds))
            if recent and recent[0] <= d <= recent[1]:
                _add(ce, ("30j", recent[0], c, camp), vec(kinds))
                if len(ref) == 2 and ref[1] is not None:
                    links[(c, camp)].append((d, ref[0], ref[1]))
    aggregate.links = links
    return tr, trd, ce


def load_csv(path, cols, idx):
    rows = {}
    if os.path.exists(path):
        unit = "troncon" if "troncon" in open(path, encoding="utf-8").readline() else "maille"
        for r in csv.DictReader(open(path, encoding="utf-8")):
            v = [0] * len(KINDS)
            for c, k in zip(cols, idx):
                v[k] = int(r[c])
            rows[(r["pas"], r["debut"], int(r[unit]), r["camp_source"])] = v
    return rows


def load_published():
    """Agrégats publiés : data/telegram_troncons.csv (modèle), data/telegram_cells.csv et les mois de
    site/telegram_troncons.js (affichage)."""
    tr = load_csv(TR_CSV, TR_COLS, range(len(KINDS)))
    ce = load_csv(CELL_CSV, CELL_COLS, CELL_IDX)
    trd = {}
    old = guard.published("telegram_troncons.js", "TG_TRONCONS")
    for key, ia, ip in (("ru", 2, 5), ("ua", 3, 6)):
        for m, t, a, p in (old or {}).get(key, []):
            v = [0] * len(KINDS)
            v[ia], v[ip], v[0] = a, p, a + p
            trd[("mois", m, int(t), key)] = v
    return tr, trd, ce


def totals(rows):
    tot = defaultdict(int)
    for (pas, deb, _, _), v in rows.items():
        tot[(pas, deb)] += v[0]
    return tot


def read_posts(paths, cutoff, gaz):
    msgs, stat = [], defaultdict(int)
    for path in paths:
        for line in open(path, encoding="utf-8"):
            p = json.loads(line)
            if p["d"] > cutoff or "t" not in p:
                continue
            stat["messages"] += 1
            ev_m, ev_s = T.events_message(p["t"], p["camp"], gaz), T.events(p["t"], p["camp"], gaz)
            stat["localises_message"] += bool(ev_m)
            stat["localises"] += bool(ev_s)
            if ev_m or ev_s:
                # référence publique du message (canal, numéro) pour le lien t.me/<canal>/<n> au survol (30 derniers jours)
                msgs.append((p["d"], p["camp"], ev_m, ev_s, os.path.basename(path)[:-6], p.get("id")))
    return msgs, stat


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    seed = sys.argv[sys.argv.index("--seed") + 1] if "--seed" in sys.argv else None
    args = [a for a in args if a != seed]
    raw = args[0] if args else "raw"
    viina = args[1] if len(args) > 1 else os.path.join(raw, "viina")
    cutoff = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=LAG_DAYS)).isoformat()
    old_js = guard.published("telegram_troncons.js", "TG_TRONCONS")
    rows_tr, rows_trd, rows_ce = ({}, {}, {}) if seed else load_published()
    print(f"agrégats publiés : {len(rows_tr)} lignes (tronçons, modèle), {len(rows_trd)} (tronçons, affichage), {len(rows_ce)} "
          f"(mailles) ; coupure {cutoff} (J-{LAG_DAYS}) ; modèle : attribution au message ; affichage : à la phrase, étalement > "
          f"{T.SPREAD_KM:.0f} km : {T.SPREAD_MODE}", flush=True)
    tess = os.path.join(viina, "gn_UA_tess.geojson")
    tdir = os.path.join(raw, "telegram")
    states = {os.path.basename(p)[:-11]: json.load(open(p, encoding="utf-8")) for p in glob.glob(os.path.join(tdir, "*.state.json"))}
    if not os.path.exists(tess):
        guard.abort("build_telegram_layer.py", f"{tess} absent (dictionnaire de lieux)")
    gaz = T.load_gaz_all(tess)
    print(f"localités non ambiguës : {len(gaz)}", flush=True)
    windows = []      # (messages, début de couverture, fin de couverture, libellé)
    missing = []      # canaux sans collecte récente : les 30 derniers jours ne sont alors pas remplacés
    if seed:
        msgs, stat = read_posts(sorted(glob.glob(os.path.join(seed, "*.jsonl"))), cutoff, gaz)
        if msgs:
            lo, hi = min(x[0] for x in msgs), max(x[0] for x in msgs)
            windows.append((msgs, "2022-01-01" if lo <= "2022-02-24" else lo, min(hi, cutoff), "historique"))
            print(f"historique : {stat['messages']} messages, {stat['localises_message']} localisés (message), "
                  f"{stat['localises']} (phrase)", flush=True)
    if states:
        missing = [n for n, _, _ in T.CHANNELS if n not in states]
        if missing:
            print("canaux sans collecte récente (ignorés pour la couverture) : " + ", ".join(missing), flush=True)
        paths = [os.path.join(tdir, f"{n}.jsonl") for n in states if os.path.exists(os.path.join(tdir, f"{n}.jsonl"))]
        msgs, stat = read_posts(paths, cutoff, gaz)
        lo = max(st["from"] for st in states.values())
        hi = min(min(st["to"] for st in states.values()), cutoff)
        print(f"collecte récente : {stat['messages']} messages, {stat['localises_message']} localisés (message), "
              f"{stat['localises']} (phrase), couverture "
              f"commune {lo} → {hi}", flush=True)
        windows.append((msgs, lo, hi, "collecte récente"))

    recent, recent_links = None, None
    for msgs, lo, hi, label in windows:
        rwin = ((dt.date.fromisoformat(hi) - dt.timedelta(days=RECENT_DAYS - 1)).isoformat(), hi)
        new_tr, new_trd, new_ce = aggregate(msgs, rwin if rwin[0] >= lo else None)
        ntot, otot = totals(new_tr), totals(rows_tr)
        replaced, kept = [], []
        for pas in ("mois", "decade"):
            start = first_full(pas, lo)
            for deb in sorted({deb for (p_, deb) in set(ntot) | set(otot) if p_ == pas and start <= deb <= hi}):
                if otot.get((pas, deb), 0) and ntot.get((pas, deb), 0) < 0.95 * otot[(pas, deb)]:
                    kept.append(f"{pas} {deb} ({ntot.get((pas, deb), 0)} < {otot[(pas, deb)]})")
                    continue
                for rows, new in ((rows_tr, new_tr), (rows_trd, new_trd), (rows_ce, new_ce)):
                    for k in [k for k in rows if k[0] == pas and k[1] == deb]:
                        del rows[k]
                    rows.update({k: v for k, v in new.items() if k[0] == pas and k[1] == deb})
                replaced.append((pas, deb))
        if rwin[0] >= lo and label == "collecte récente" and missing:
            print(f"30 derniers jours non remplacés : {len(missing)} canaux sans collecte récente", flush=True)
        elif rwin[0] >= lo:
            for k in [k for k in rows_ce if k[0] == "30j"]:
                del rows_ce[k]
            rows_ce.update({k: v for k, v in new_ce.items() if k[0] == "30j"})
            recent = rwin
            recent_links = getattr(aggregate, "links", {})
        rm = [d for p_, d in replaced if p_ == "mois"]
        print(f"{label} : {len(rm)} mois remplacés ({rm[0] if rm else '-'} → {rm[-1] if rm else '-'}), "
              f"{sum(1 for p_, _ in replaced if p_ == 'decade')} décades{', 30 derniers jours ' + str(rwin) if rwin[0] >= lo else ''}", flush=True)
        if kept:
            print(f"{label} : périodes gardées telles que publiées (nouvelle version plus pauvre) : " + "; ".join(kept), flush=True)

    rows_tr = {k: v for k, v in rows_tr.items() if k[1] <= cutoff and any(v)}
    rows_ce = {k: v for k, v in rows_ce.items() if k[1] <= cutoff and any(v)}
    rows_trd = {k: v for k, v in rows_trd.items() if k[1] <= cutoff and any(v)}
    upto = max([hi for _, _, hi, _ in windows] + [(old_js or {}).get("upto") or ""])
    if not upto:
        upto = min(cutoff, period_end("mois", max(d for p, d, _, _ in rows_tr if p == "mois")))
    if recent is None:
        r30 = sorted({k[1] for k in rows_ce if k[0] == "30j"})
        recent = (r30[-1], period_end("30j", r30[-1])) if r30 else None

    # --- tronçons pour l'affichage (format inchangé pour app.js ; attribution à la phrase) ---
    ru, ua, tiles = [], [], {}
    for (pas, deb, tr, camp), v in sorted(rows_trd.items()):
        if pas != "mois":
            continue
        if camp == "ru" and v[2] + v[5] > 0:
            ru.append([deb, tr, v[2], v[5]])
        elif camp == "ua" and v[3] + v[6] > 0:
            ua.append([deb, tr, v[3], v[6]])
        else:
            continue
        tiles[tr] = list(T.tr_origin(tr))
    out = {"tiles": {str(k): v for k, v in sorted(tiles.items())}, "tileCells": T.TW, "ru": ru, "ua": ua,
           "last": max(m for m, *_ in ru + ua), "upto": upto}
    # --- mailles ---
    months = sorted({deb for (pas, deb, _, _) in rows_ce if pas == "mois"})
    mi = {m: i for i, m in enumerate(months)}
    sel = lambda v: [v[k] for k in CELL_IDX[1:]]  # noqa: E731
    cm = [[mi[deb], c, CAMPS.index(camp)] + sel(v) for (pas, deb, c, camp), v in sorted(rows_ce.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][3]))
          if pas == "mois" and any(sel(v))]
    cr = [[c, CAMPS.index(camp)] + sel(v) for (pas, deb, c, camp), v in sorted(rows_ce.items(), key=lambda kv: (kv[0][2], kv[0][3]))
          if pas == "30j" and any(sel(v))]
    cells_out = {"nx": T.NX, "camps": CAMPS, "months": months, "upto": upto, "m": cm,
                 "recent": {"from": recent[0], "to": recent[1], "rows": cr} if recent else None}
    # liens vers les messages publics (au plus 3 par maille et par camp, les plus récents), décalage J-7 respecté ;
    # sans nouvelle collecte, on garde ceux déjà publiés
    if recent and recent_links is not None:
        cells_out["recent"]["links"] = {f"{c},{CAMPS.index(camp)}": [f"{ch}/{mid}" for _, ch, mid in sorted(v, reverse=True)[:3]]
                                        for (c, camp), v in sorted(recent_links.items()) if v}
    elif recent and old_js is not None:
        prev = (guard.published("telegram_cells.js", "TG_CELLS") or {}).get("recent") or {}
        if prev.get("links") and prev.get("from") == recent[0]:
            cells_out["recent"]["links"] = prev["links"]
    if old_js and not seed:
        old_cells = guard.published("telegram_cells.js", "TG_CELLS") or {}
        guard.check("build_telegram_layer.py", [("tronçons-mois (sources russes)", len(ru), len(old_js.get("ru", [])), "count"),
                                                ("tronçons-mois (sources ukrainiennes)", len(ua), len(old_js.get("ua", [])), "count"),
                                                ("mailles-mois", len(cm), len(old_cells.get("m", [])) or None, "count"),
                                                ("dernier mois", out["last"], old_js.get("last"), "max")])
    with open(TR_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pas", "debut", "troncon", "i0", "j0", "camp_source"] + TR_COLS + ["partiel"])
        for (pas, deb, tr, camp), v in sorted(rows_tr.items()):
            w.writerow([pas, deb, tr, *T.tr_origin(tr), camp] + v + [int(period_end(pas, deb) > upto)])
    with open(CELL_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pas", "debut", "maille", "i", "j", "camp_source"] + CELL_COLS + ["partiel"])
        for (pas, deb, c, camp), v in sorted(rows_ce.items()):
            w.writerow([pas, deb, c, c % T.NX, c // T.NX, camp] + [v[k] for k in CELL_IDX] + [int(period_end(pas, deb) > upto)])
    js = "window.TG_TRONCONS=" + json.dumps(out, separators=(",", ":")) + ";"
    open(TR_JS, "w", encoding="utf-8").write(js)
    jc = "window.TG_CELLS=" + json.dumps(cells_out, separators=(",", ":")) + ";"
    open(CELL_JS, "w", encoding="utf-8").write(jc)
    print(f"telegram_troncons.js : {len(js) // 1024} Ko, {len(ru)} tronçons-mois (sources russes), {len(ua)} (sources "
          f"ukrainiennes), jusqu'au {upto} ; telegram_cells.js : {len(jc) // 1024} Ko, {len(cm)} mailles-mois, "
          f"{len(cr)} mailles sur les 30 derniers jours {recent} ; CSV : {len(rows_tr)} lignes (tronçons), "
          f"{len(rows_ce)} (mailles)", flush=True)


if __name__ == "__main__":
    main()
