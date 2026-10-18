"""Faits officiels des bilans quotidiens, par maille de 2 km et par mois, pour P(contact au sol) et le nowcast :

- état-major ukrainien (t.me/s/GeneralStaffZSU) : assauts par direction, répartis entre les localités citées dans le
  paragraphe de la direction ; localités citées pour les frappes aériennes du paragraphe national ;
- MoD RF (t.me/s/mod_russia) : localités citées dans les bilans des groupements de troupes (« в районах населённых
  пунктов … »), contrepartie russe ; couverture plus faible (voir data/officiels_couverture.csv).

    python3 scripts/build_official_signals.py <dossier_brut> <dossier_viina> [--tout]

Entrées : <brut>/claims/GeneralStaffZSU.json, <brut>/claims/mod_russia.json (collecte de fetch_claims.py, jamais
publiée), <viina>/gn_UA_tess.geojson. Sorties (faits agrégés seulement) : data/officiels_cells_month.csv
(maille, mois, source, valeur par jour couvert) et data/officiels_couverture.csv (source, mois, jours couverts).
Mise à jour incrémentale : les mois présents dans le brut sont recalculés, les autres sont repris du fichier versionné.
Méthode reprise de l'analyse hors ligne (scratchpad backtest/v2/gsua.py) ; aucune donnée plus fine n'est publiée.
"""
import csv
import datetime as dt
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# ---------- nombres en toutes lettres ----------
UNITS = {"од": 1, "дв": 2, "дві": 2, "три": 3, "чотир": 4, "п'ят": 5, "шіст": 6, "шест": 6, "сім": 7, "сем": 7,
         "вісім": 8, "восьм": 8, "дев'ят": 9, "десят": 10}
WORDS = [("одинадцят", 11), ("дванадцят", 12), ("тринадцят", 13), ("чотирнадцят", 14), ("п'ятнадцят", 15),
         ("шістнадцят", 16), ("сімнадцят", 17), ("вісімнадцят", 18), ("дев'ятнадцят", 19), ("двадцят", 20),
         ("тридцят", 30), ("сорок", 40), ("п'ятдесят", 50), ("шістдесят", 60), ("сімдесят", 70), ("вісімдесят", 80),
         ("дев'яност", 90), ("сто", 100),
         ("двічі", 2), ("тричі", 3), ("чотири", 4), ("чотирьох", 4), ("п'ять", 5), ("п'яти", 5), ("шість", 6), ("шести", 6),
         ("сім", 7), ("семи", 7), ("вісім", 8), ("восьми", 8), ("дев'ять", 9), ("дев'яти", 9), ("десять", 10), ("десяти", 10),
         ("три", 3), ("трьох", 3), ("два", 2), ("дві", 2), ("двох", 2), ("один", 1), ("одну", 1), ("одна", 1), ("одного", 1),
         ("одній", 1), ("однієї", 1)]


def num_word(w):
    w = w.lower().replace("’", "'").replace("ʼ", "'")
    if w.isdigit():
        return int(w)
    for stem, v in WORDS:
        if w == stem or (len(stem) >= 6 and w.startswith(stem)):
            return v
    return None


TOK = re.compile(r"[\w'’ʼ\-]+")
COUNT_NOUN = re.compile(r"^(атак|штурм|зіткн|бо[їє]|бій|спроб|раз|наступ|дій|дії|боєзіткн|атакува|ворож)", re.I)
KAB = re.compile(r"(керован\w*\s+авіабомб|КАБ)", re.I)
AIR = re.compile(r"(авіауд|авіаційн\w*\s+удар)", re.I)
DRONE = re.compile(r"дрон\w*[-\s]камікадзе", re.I)


def first_count(text, noun=COUNT_NOUN, window=4):
    """Premier nombre suivi (à 4 mots au plus) d'un nom attendu ; « двічі », « тричі » comptent seuls."""
    toks = TOK.findall(text)
    for i, t in enumerate(toks):
        v = num_word(t)
        if v is None:
            continue
        # nombre composé : « двадцять дві »
        if i + 1 < len(toks) and v in (20, 30, 40, 50, 60, 70, 80, 90):
            u = num_word(toks[i + 1])
            if u and u < 10:
                v += u
        if t.lower() in ("двічі", "тричі"):
            return v
        for k in range(1, window + 1):
            if i + k < len(toks) and noun.search(toks[i + k]):
                return v
    return None


def count_before(text, pat):
    """Nombre placé juste avant un motif (KAB, frappes) dans le texte."""
    tot = None
    for m in pat.finditer(text):
        pre = TOK.findall(text[max(0, m.start() - 40):m.start()])
        for t in reversed(pre[-3:]):
            v = num_word(t)
            if v is not None:
                tot = (tot or 0) + v
                break
    return tot


# ---------- géocodage ----------
FRONT_ADM1 = {"Donets'k", "Kharkiv", "Luhans'k", "Zaporizhzhya", "Dnipropetrovs'k", "Sumy", "Kherson", "Mykolayiv", "Chernihiv"}
ENDINGS = sorted(["ого", "ому", "ої", "ій", "ів", "ах", "ями", "ами", "ям", "ям", "ою", "ею", "ий", "ий", "а", "у", "і", "и", "е", "я", "ю", "ь", "о", "ї", "й"],
                 key=len, reverse=True)


def norm(s):
    return s.lower().replace("’", "'").replace("ʼ", "'").replace("ё", "е").strip()


def stem(s):
    parts = re.split(r"[\s\-]+", norm(s))
    out = []
    for p in parts:
        for e in ENDINGS:
            if p.endswith(e) and len(p) - len(e) >= 3:
                p = p[:-len(e)]
                break
        out.append(p)
    return " ".join(out)



def load_gaz(tess):
    gj = json.load(open(tess))
    idx = defaultdict(list)
    for f in gj["features"]:
        pr = f["properties"]
        if pr.get("ADM1_NAME") not in FRONT_ADM1:
            continue
        p = (float(pr["longitude"]), float(pr["latitude"]), pr["name"])
        for n in {n.strip() for n in (pr.get("alternatenames") or "").split(",") if re.search(r"[а-яіїєґ]", n.lower())}:
            k = stem(n)
            if len(k.replace(" ", "")) >= 4:
                idx[k].append(p)
    return idx


CAPW = re.compile(r"(?<![\w'’])([А-ЯІЇЄҐ][а-яіїєґ'’ʼ]+(?:[\s\-][А-ЯІЇЄҐ][а-яіїєґ'’ʼ]+)?)")
NOT_PLACE = {"сили", "україн", "збройн", "агресор", "окупант", "ворог", "противник", "наш", "росі", "рф", "на", "слава",
             "загальн", "крім", "також", "зокрема", "протягом", "внаслідок", "біля", "поблизу", "вчора", "сьогодні", "минул",
             "станом", "оперативн", "розпочал", "агрес", "північн", "південн", "сумськ", "харківськ", "донецьк", "запорізьк",
             "херсонськ", "дніпропетровськ", "курськ", "бєлгородськ", "брянськ", "луганськ", "уточнен", "із", "за", "до", "у", "в"}


def places_in(text, idx):
    out = []
    for m in CAPW.finditer(text):
        w = m.group(1)
        if norm(w).split()[0][:7] in NOT_PLACE or any(norm(w).startswith(x) for x in NOT_PLACE):
            continue
        k = stem(w)
        if k in idx:
            out.append((w, idx[k]))
        elif " " in k or "-" in w:
            k1 = stem(w.split()[0].split("-")[0])
            if k1 in idx:
                out.append((w, idx[k1]))
    return out


HOUR = re.compile(r"станом на (\d{1,2})[.:](\d\d)")
DIRP2 = re.compile(r"[Нн]а\s+([А-ЯІЇЄҐ][^\n.;:]{0,80}?)\s+напрям(?:ку|ках)")
NATIONAL = re.compile(r"(Загалом|протягом минулої доби зафіксовано|Розпочал|Найгаряч|зафіксовано\s+\d+\s+бойов|станом на)", re.I)
DIRP = re.compile(r"(?:^|\n|\.\s)\s*На\s+([А-ЯІЇЄҐ][^\n]{0,80}?)\s+напрям(?:ку|ках|і)", re.M)


def direction_keys(s):
    names = re.split(r"\s*(?:,|\sі\s|\sта\s|\sй\s)\s*", s)
    out = []
    for n in names:
        n = norm(n).replace("ому", "").replace("-", "_")
        n = re.sub(r"[^a-zа-яіїєґ'_]", "", n)
        if n:
            out.append(n)
    return "+".join(out)



# ---------- MoD RF : localités des bilans des groupements ----------
RU_ENDINGS = sorted(["ого", "ому", "ой", "ий", "ая", "ое", "ые", "ых", "ым", "ами", "ах", "ям", "а", "у", "е", "и", "ы", "ь", "о", "й", "я"], key=len, reverse=True)
RU_PLACES = re.compile(r"в район(?:е|ах) населенн\w+ пункт\w*\s+([^.;:\n]+)", re.I)
RU_CAP = re.compile(r"(?<![\w'’])([А-ЯЁ][а-яё'’\-]+(?:[\s\-][А-ЯЁ][а-яё'’]+)?)")
RU_NOT = {"донецк", "луганск", "запорожск", "херсонск", "харьковск", "днепропетровск", "сумск", "курск", "белгородск",
          "днр", "лнр", "народн", "республик", "област", "украин", "всу"}


def ru_stem(s):
    out = []
    for p in re.split(r"[\s\-]+", norm(s)):
        for e in RU_ENDINGS:
            if p.endswith(e) and len(p) - len(e) >= 3:
                p = p[:-len(e)]
                break
        out.append(p)
    return " ".join(out)


def load_gaz_ru(tess):
    gj = json.load(open(tess))
    idx = defaultdict(list)
    for f in gj["features"]:
        pr = f["properties"]
        if pr.get("ADM1_NAME") not in FRONT_ADM1:
            continue
        p = (float(pr["longitude"]), float(pr["latitude"]), pr["name"])
        for n in {n.strip() for n in (pr.get("alternatenames") or "").split(",") if re.search(r"[а-яё]", n.lower())}:
            k = ru_stem(n)
            if len(k.replace(" ", "")) >= 4:
                idx[k].append(p)
    return idx


def gs_month_cells(posts, idx, grid_index):
    """État-major : {mois: {maille: (assauts par jour couvert, frappes citées par jour couvert)}}, jours couverts."""
    best = {}
    for p in posts:
        t = p["text"]
        if "напрям" not in t or "доб" not in t and "станом" not in t:
            continue
        h = HOUR.search(t)
        if not h:
            continue
        hour = int(h.group(1))
        d = dt.date.fromisoformat(p["date"])
        day = (d - dt.timedelta(days=1) if hour <= 9 else d).isoformat()
        prio = 2 if hour <= 9 else 1
        cur = best.get(day)
        if not cur or (prio, hour) > (cur[0], cur[1]):
            best[day] = (prio, hour, p)
    paras, raw_pos = [], defaultdict(list)
    for day, (prio, hour, p) in sorted(best.items()):
        for para in re.split(r"\n+", p["text"]):
            m = DIRP2.search(para)
            if not m or NATIONAL.search(para):
                continue
            dk = direction_keys(m.group(1))
            if re.search(r"активних\s+(?:наступальних\s+)?дій\s+не\s+(?:проводив|вів|веде|було)|не\s+(?:виявлено|зафіксовано)", para) and not first_count(para):
                n = 0
            else:
                n = first_count(para)
            pl = places_in(para, idx)
            paras.append((day, dk, n, pl))
            for w, cands in pl:
                if len(cands) == 1:
                    raw_pos[dk].append(cands[0][:2])
    med = {}
    for dk, ps in raw_pos.items():
        xs, ys = sorted(x for x, _ in ps), sorted(y for _, y in ps)
        med[dk] = (xs[len(xs) // 2], ys[len(ys) // 2])
    covered = Counter(d[:7] for d in best)
    daily = []
    for day, dk, n, pl in paras:
        cells = []
        c0 = med.get(dk)
        for w, cands in pl:
            if c0 is None:
                continue
            c = min(cands, key=lambda q: (q[0] - c0[0]) ** 2 + (q[1] - c0[1]) ** 2)
            if math.hypot((c[0] - c0[0]) * 74, (c[1] - c0[1]) * 111) <= 120:
                k = grid_index(c[0], c[1])
                if k >= 0:
                    cells.append(k)
        daily.append((day, dk, n, cells))
    month_share = defaultdict(Counter)
    for day, dk, n, cells in daily:
        month_share[(dk, day[:7])].update(cells)
    ass = defaultdict(lambda: defaultdict(float))
    for day, dk, n, cells in daily:
        if not n:
            continue
        share = Counter(cells) if cells else month_share.get((dk, day[:7]), Counter())
        tot = sum(share.values())
        for k, c in share.items():
            ass[day[:7]][k] += n * c / tot
    air = defaultdict(lambda: defaultdict(float))
    allpos = list(med.values())
    for day, (prio, hour, p) in best.items():
        m = re.search(r"(?:завдав\w*|завдавав)\s+авіа\w*[^.]*?(?:район\w*|по)\s+([^.]+)\.", p["text"])
        if not m:
            continue
        for w, cands in places_in(m.group(1), idx):
            c = min(cands, key=lambda q: min((q[0] - a) ** 2 + (q[1] - b) ** 2 for a, b in allpos)) if allpos else cands[0]
            k = grid_index(c[0], c[1])
            if k >= 0:
                air[day[:7]][k] += 1
    return ({m: {k: v / covered[m] for k, v in d.items()} for m, d in ass.items()},
            {m: {k: v / covered[m] for k, v in d.items()} for m, d in air.items()}, covered)


def ru_month_cells(posts, idx, grid_index):
    """MoD RF : {mois: {maille: localités citées par jour couvert}}, jours couverts (jours avec un bilan de groupement)."""
    cnt = defaultdict(lambda: defaultdict(float))
    days = set()
    for p in posts:
        t = p["text"]
        if not re.search(r"группировк\w* войск", t):
            continue
        hits = []
        for m in RU_PLACES.finditer(t):
            for w in RU_CAP.findall(m.group(1)):
                k = ru_stem(w)
                if any(k.startswith(x) for x in RU_NOT):
                    continue
                cands = idx.get(k)
                if cands and len(cands) <= 3:
                    c = cands[0]
                    g = grid_index(c[0], c[1])
                    if g >= 0:
                        hits.append(g)
        if hits:
            days.add(p["date"])
            for g in hits:
                cnt[p["date"][:7]][g] += 1
    covered = Counter(d[:7] for d in days)
    return {m: {k: v / covered[m] for k, v in d.items()} for m, d in cnt.items()}, covered


def main():
    raw, viina = sys.argv[1], sys.argv[2]
    import build_front as B
    tess = os.path.join(viina, "gn_UA_tess.geojson")
    out = os.path.join(ROOT, "data", "officiels_cells_month.csv")
    cov_out = os.path.join(ROOT, "data", "officiels_couverture.csv")
    new, cov = {}, {}
    gs = os.path.join(raw, "claims", "GeneralStaffZSU.json")
    if os.path.exists(gs):
        a, f, c = gs_month_cells(json.load(open(gs)), load_gaz(tess), B.grid_index)
        new["etatmajor_assauts"], new["etatmajor_frappes"], cov["etatmajor"] = a, f, c
    ru = os.path.join(raw, "claims", "mod_russia.json")
    if os.path.exists(ru):
        r, c = ru_month_cells(json.load(open(ru)), load_gaz_ru(tess), B.grid_index)
        new["modru_localites"], cov["modru"] = r, c
    # mois recalculés : les 3 derniers mois du brut seulement (le cache de CI ne garde, pour les mois anciens, que les
    # messages filtrés par les anciens mots-clés ; l'historique versionné, plus complet, n'est pas réécrit) ; avec
    # --tout, tous les mois du brut (reconstruction locale à partir d'une collecte complète)
    full = "--tout" in sys.argv
    for src in list(new):
        ms = sorted(new[src])
        if ms and not full:
            last = ms[-1]
            y, mo = int(last[:4]), int(last[5:])
            cut = f"{y + (mo - 3) // 12:04d}-{(mo - 3) % 12 + 1:02d}"
            new[src] = {m: v for m, v in new[src].items() if m >= cut}
    for k in list(cov):
        if not full and cov[k]:
            last = max(cov[k])
            y, mo = int(last[:4]), int(last[5:])
            cut = f"{y + (mo - 3) // 12:04d}-{(mo - 3) % 12 + 1:02d}"
            cov[k] = Counter({m: n for m, n in cov[k].items() if m >= cut})
    rows = {}
    if os.path.exists(out):
        for r in csv.DictReader(open(out)):
            rows[(r["source"], r["mois"], r["maille"])] = r["par_jour"]
    for src, d in new.items():
        for m in d:
            for key in [k for k in rows if k[0] == src and k[1] == m]:
                del rows[key]
            for k, v in d[m].items():
                rows[(src, m, str(k))] = f"{v:.4f}"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "mois", "maille", "par_jour"])
        for (s, m, k), v in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0], int(kv[0][2]))):
            w.writerow([s, m, k, v])
    crow = {}
    if os.path.exists(cov_out):
        for r in csv.DictReader(open(cov_out)):
            crow[(r["source"], r["mois"])] = r["jours_couverts"]
    for src, c in cov.items():
        for m, n in c.items():
            crow[(src, m)] = str(n)
    with open(cov_out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "mois", "jours_couverts"])
        for (s, m), n in sorted(crow.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            w.writerow([s, m, n])
    print(f"officiels : {len(rows)} lignes maille-mois ; mois couverts : " + ", ".join(f"{s} {len(c)}" for s, c in cov.items()))


if __name__ == "__main__":
    main()
