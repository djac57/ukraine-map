"""Extrait les FAITS « localité X prise / reprise le jour J » des annonces officielles et les géocode.

    python3 scripts/build_claims.py <dossier_brut> <dossier_viina>

Entrées : <dossier_brut>/claims/mod_russia.json et GeneralStaffZSU.json (fetch_claims.py), pavage VIINA
(noms GeoNames, dont les formes cyrilliques russes et ukrainiennes), statuts VIINA (pour départager les
homonymes par la proximité du front). Sorties publiées (faits seulement, aucun texte repris) :
data/claims_modru.csv et data/claims_gsua.csv (date, geonameid, nom, lat, lon, source), et
data/claims_stats.json (taux de rejet).

Règles :
- on ne lit que les phrases contenant un verbe de prise ou de reprise ;
- les candidats sont les groupes de mots à majuscule (déclinaisons ramenées à un radical commun aux
  graphies russe et ukrainienne) ;
- recherche limitée aux oblasts du front, et à l'oblast cité dans la phrase quand il y en a un ;
- seuls les candidats à moins de 30 km d'une zone tenue ou contestée selon au moins une carte ce mois-là
  sont gardés ; s'il en reste plusieurs (homonymes), on prend celui qui est à moins de 60 km des autres
  localités sans ambiguïté de la même phrase, sinon la mention est rejetée comme ambiguë ;
- localités en Russie (Koursk, Belgorod…) : hors du pavage, comptées à part.
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
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

RAW = sys.argv[1] if len(sys.argv) > 1 else "raw"
VIINA_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(RAW, "viina")
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))   # décalage de publication : J-1 depuis le 19/10/2026 (décision de l'utilisateur ; J-7 auparavant)

OBL = r"\w*\s+(?:област|обл\.)"
REGION = [  # (motif, nom ADM1 du pavage GeoNames) : adjectif + « область », forme en -щина, ou ДНР / ЛНР
    (r"\bднр\b|донецк(?:ой|ая) народн|донецьк" + OBL + r"|донецк" + OBL + r"|донеччин", "Donets'k"),
    (r"\bлнр\b|луганск(?:ой|ая) народн|луганськ" + OBL + r"|луганск" + OBL + r"|луганщин", "Luhans'k"),
    (r"харьковск" + OBL + r"|харківськ" + OBL + r"|харківщин", "Kharkiv"),
    (r"запорожск" + OBL + r"|запорізьк" + OBL + r"|запоріжжі|запорізьк(?:ий|ому) напрям", "Zaporizhzhya"),
    (r"херсонск" + OBL + r"|херсонськ" + OBL + r"|херсонщин", "Kherson"),
    (r"днепропетровск" + OBL + r"|дніпропетровськ" + OBL + r"|дніпропетровщин", "Dnipropetrovs'k"),
    (r"сумск" + OBL + r"|сумськ" + OBL + r"|сумщин", "Sumy"),
    (r"николаевск" + OBL + r"|миколаївськ" + OBL + r"|миколаївщин", "Mykolayiv"),
    (r"киевск" + OBL + r"|київськ" + OBL + r"|київщин", "Kiev"),
    (r"черниговск" + OBL + r"|чернігівськ" + OBL + r"|чернігівщин", "Chernihiv"),
]
FRONT = {r for _, r in REGION}
RUSSIA = r"курск|белгород|брянск|курськ|бєлгород"
RU_REGION = [(r"курск" + r"\w*\s+(?:област|обл\.)" + r"|курськ\w*\s+(?:област|обл\.)|курщин", "41"),
             (r"белгородск\w*\s+(?:област|обл\.)|бєлгородськ\w*\s+(?:област|обл\.)|бєлгородщин", "09"),
             (r"брянск\w*\s+(?:област|обл\.)|брянськ\w*\s+(?:област|обл\.)|брянщин", "10")]
VERBS = {
    "modru": re.compile(r"освобож|освободил|контроль над|взят[ыо]? под контроль|взяли под контроль", re.I),
    "gsua": re.compile(r"звільн|повернули під контроль|встановили контроль|контроль над", re.I),
}
NOISE = re.compile(r"полон|плен|служб|посад|звільнити небо|освободить небо|заручник|заложник", re.I)
NEG = re.compile(r"фейк|неправда|ложь|не соответствует|не відповідає|спростов", re.I)
STOP = set("""
днр лнр донецкой донецкая народной народная республики республика луганской харьковской запорожской херсонской
днепропетровской сумской николаевской курской белгородской брянской курская курскую курск белгород брянск
суджанском суджанского глушковском кореневском беловском большесолдатском рыльском льговском области область вооруженных вооружённых сил силы всу украины україни россии
российской федерации министерство минобороны обороны группировки группировка войск запад юг центр восток север днепр
героя герой российской армии армия президент владимир путин сырский зеленский генштаб генеральний штаб сили оборони
зсу збройних сил україна донеччині харківщині запоріжжі луганщині сумщині херсонщині області напрямку напрямок
""".split())
VERB_PREFIX = ("освобо", "освобожд", "установ", "подразделени", "взят", "звільн", "підрозділ", "правда", "фейк")
UA_LET = str.maketrans({"ё": "и", "е": "и", "є": "и", "і": "и", "ї": "и", "ы": "и", "й": "и", "ґ": "г", "э": "и", "ъ": "", "ь": "", "'": "", "’": "", "ʼ": ""})
ENDINGS = sorted(["ов", "ев", "ый", "ий", "ій", "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ой", "ей", "ою", "ом", "ем", "ым", "им",
                  "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ых", "их", "ів", "ах", "ях", "а", "я", "о", "е",
                  "ы", "и", "у", "ю", "і", "ї"], key=len, reverse=True)


def stem(word):
    w = word.lower().replace("ё", "е")
    for e in ENDINGS:
        if len(w) - len(e) >= 3 and w.endswith(e):
            w = w[: -len(e)]
            break
    return w.translate(UA_LET)


def key(name):
    return " ".join(stem(w) for w in re.split(r"[\s\-]+", name) if w)


STOP_KEYS = None  # rempli après la définition de STOP


def lev(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


STOP_KEYS = {key(w) for w in STOP} | {key(w) for w in ("Республика", "Республики", "Республике", "Республіка", "Народной", "Федерации")}
CAP = re.compile(r"(?<![\w«])([А-ЯЁІЇЄҐ][а-яёіїєґ'’ʼ]+(?:[\s\-](?:[А-ЯЁІЇЄҐ][а-яёіїєґ'’ʼ]+|на|над|у|в)){0,3})")


def candidates(sentence):
    out = []
    for m in CAP.finditer(sentence):
        words = [w for w in re.split(r"\s+", m.group(1))]
        # coupe sur les mots vides (« Харьковской области », « Вооруженных Сил »)
        cur = []
        for w in words:
            if w.lower().strip("-") in STOP or len(w) < 3 and w not in ("на", "над", "у", "в"):
                if cur:
                    out.append(" ".join(cur))
                cur = []
            else:
                cur.append(w)
        if cur and cur[0][0].isupper():
            out.append(" ".join(cur))
    res = []
    split = []
    for c in out:  # coupe sur « в » / « у » (« Гуево в Суджанском районе »)
        split += [x.strip() for x in re.split(r"\s(?:в|у|во)\s", c) if x.strip()]
    for c in split:
        ws = c.split(" ")
        while ws and (ws[0].lower().startswith(VERB_PREFIX) or ws[0].lower() in STOP):
            ws = ws[1:]
        while ws and not ws[-1][0].isupper():
            ws = ws[:-1]
        if ws:
            res.append(" ".join(ws))
    return res


def load_gazetteer(tess):
    gj = json.load(open(tess))
    places, index = [], defaultdict(list)
    for f in gj["features"]:
        pr = f["properties"]
        if not f["geometry"] or pr.get("ADM1_NAME") not in FRONT:
            continue
        k = len(places)
        places.append({"id": str(int(pr["geonameid"])), "name": pr["name"], "lat": float(pr["latitude"]),
                       "lon": float(pr["longitude"]), "adm1": pr["ADM1_NAME"], "pop": int(pr.get("population") or 0)})
        names = {n.strip() for n in (pr.get("alternatenames") or "").split(",") if re.search(r"[а-яёіїєґ]", n.lower())}
        for n in names:
            index[key(n)].append(k)
    return places, index


def load_russian_gazetteer(path):
    """Localités des oblasts de Koursk, Belgorod et Briansk (GeoNames RU, CC BY 4.0)."""
    places, index = [], defaultdict(list)
    if not path or not os.path.exists(path):
        return places, index
    import zipfile
    import io
    with zipfile.ZipFile(path) as z:
        for r in csv.reader(io.TextIOWrapper(z.open("RU.txt"), encoding="utf-8"), delimiter="\t"):
            if r[6] != "P" or r[10] not in ("41", "09", "10"):
                continue
            k = len(places)
            places.append({"id": r[0], "name": r[1], "lat": float(r[4]), "lon": float(r[5]), "adm1": r[10], "pop": int(r[14] or 0)})
            for n in {n.strip() for n in r[3].split(",") if re.search(r"[а-яё]", n.lower())}:
                index[key(n)].append(k)
    return places, index


def front_places(status_by_day, places):
    """Pour chaque jour d'instantané : ensemble des localités tenues ou contestées selon au moins une carte."""
    out = {}
    for day, st in status_by_day.items():
        out[day] = {p["id"] for p in places if any(c > 1 for c in st.get(p["id"], ()))}
    return out


def main():
    import build_front as BF  # lecture des statuts VIINA
    BF.SRC_DIR = VIINA_DIR
    tess = os.path.join(VIINA_DIR, "gn_UA_tess.geojson")
    places, index = load_gazetteer(tess)
    ru_places, ru_index = load_russian_gazetteer(sys.argv[3] if len(sys.argv) > 3 else os.path.join(RAW, "geonames", "RU.zip"))
    ua_border = defaultdict(list)
    for p in places:
        if p["adm1"] in ("Sumy", "Kharkiv", "Chernihiv", "Luhans'k"):
            ua_border[(int(p["lat"] * 2), int(p["lon"] * 2))].append(p)

    def near_border(p, km=80):
        a, b = int(p["lat"] * 2), int(p["lon"] * 2)
        for da in (-2, -1, 0, 1, 2):
            for db in (-2, -1, 0, 1, 2):
                for q in ua_border.get((a + da, b + db), ()):
                    if math.hypot((q["lat"] - p["lat"]) * 111.2, (q["lon"] - p["lon"]) * 111.2 * math.cos(math.radians(p["lat"]))) <= km:
                        return True
        return False
    keys = list(index)
    pid_idx = {p["id"]: i for i, p in enumerate(places)}
    today = dt.date.today()
    limit = (today - dt.timedelta(days=LAG_DAYS)).isoformat()
    # instantanés de fin de mois pour la proximité du front
    months = []
    d = dt.date(2022, 2, 1)
    while d <= today:
        months.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    ends = [(m.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1) for m in months]
    status, _ = BF.read_status([e for e in ends if e.isoformat() <= limit])
    hot = front_places(status, places)
    hot_days = sorted(hot)
    grid = defaultdict(list)
    for i, p in enumerate(places):
        grid[(int(p["lat"] * 4), int(p["lon"] * 4))].append(i)

    def near_front(i, day):
        prior = [x for x in hot_days if x >= day] or hot_days[-1:]
        h = hot[prior[0]]
        p = places[i]
        a, b = int(p["lat"] * 4), int(p["lon"] * 4)
        for da in (-2, -1, 0, 1, 2):
            for db in (-2, -1, 0, 1, 2):
                for q in grid.get((a + da, b + db), ()):
                    if places[q]["id"] in h:
                        dy = (places[q]["lat"] - p["lat"]) * 111.2
                        dx = (places[q]["lon"] - p["lon"]) * 111.2 * math.cos(math.radians(p["lat"]))
                        if dx * dx + dy * dy <= 30 ** 2:
                            return True
        return False

    stats = {}
    for src, channel in (("modru", "mod_russia"), ("gsua", "GeneralStaffZSU")):
        path = os.path.join(RAW, "claims", f"{channel}.json")
        rows, st = [], Counter()
        if not os.path.exists(path):
            print(f"{channel} : pas de données")
            continue
        for post in json.load(open(path)):
            day = post["date"]
            if day < "2022-02-24" or day > limit or NEG.search(post["text"]):
                continue
            post_regions = [r for pat, r in REGION if re.search(pat, post["text"].lower())]
            prev = ""
            for sent in re.split(r"(?<=[.!?…])\s+|\n+", post["text"]):
                # une liste sans verbe ne compte que si elle suit directement une phrase de prise terminée par « : »
                listed = prev.rstrip().endswith(":") and bool(VERBS[src].search(prev))
                prev = sent if sent.strip() else prev
                if not VERBS[src].search(sent) and not listed:
                    continue
                if NOISE.search(sent):  # « libéré de captivité », « libéré du service », etc.
                    continue
                low = sent.lower()
                regions = [r for pat, r in REGION if re.search(pat, low)] or post_regions
                russia = bool(re.search(RUSSIA, low))
                ru_regions = [r for pat, r in RU_REGION if re.search(pat, low)]
                resolved, pending = [], []
                for cand in candidates(sent):
                    k = key(cand)
                    if k in STOP_KEYS:
                        continue
                    st["mentions"] += 1
                    if ru_regions and not regions:
                        # localité russe de la bande frontalière (Koursk, Belgorod, Briansk)
                        hits = [i for i in ru_index.get(k, []) if ru_places[i]["adm1"] in ru_regions and near_border(ru_places[i])]
                        if len(hits) > 1:
                            # homonymes : pendant l'incursion de Koursk, le plus proche de Soudja (à moins de 45 km) ;
                            # sinon le plus proche de la frontière s'il l'est nettement
                            def km_to(q, lat, lon):
                                return math.hypot((q["lat"] - lat) * 111.2, (q["lon"] - lon) * 111.2 * math.cos(math.radians(lat)))
                            ranked = sorted(hits, key=lambda i: km_to(ru_places[i], 51.19, 35.27))
                            if "2024-08" <= day[:7] <= "2025-05" and km_to(ru_places[ranked[0]], 51.19, 35.27) <= 45 and \
                                    (len(ranked) == 1 or km_to(ru_places[ranked[1]], 51.19, 35.27) - km_to(ru_places[ranked[0]], 51.19, 35.27) > 10):
                                hits = ranked[:1]
                            else:
                                near30 = [i for i in hits if near_border(ru_places[i], 30)]
                                hits = near30 if len(near30) == 1 else hits
                        if len(hits) != 1:
                            st["Russie : ambigu ou introuvable"] += 1
                            continue
                        q = ru_places[hits[0]]
                        rows.append((day, "ru" + q["id"], q["name"], q["lat"], q["lon"], "MoD RF" if src == "modru" else "État-major ukrainien"))
                        st["géocodé (Russie)"] += 1
                        st["géocodé"] += 1
                        continue
                    hits = index.get(k)
                    if not hits and len(k) >= 6:
                        best = sorted((lev(k, kk), kk) for kk in keys if abs(len(kk) - len(k)) <= 2 and kk[:2] == k[:2])
                        if best and best[0][0] <= max(1, len(k) // 7) and (len(best) == 1 or best[1][0] > best[0][0]):
                            hits = index[best[0][1]]
                    if not hits:
                        st["hors Russie" if russia and not regions else "introuvable"] += 1
                        continue
                    hits = sorted(set(hits))
                    if regions:
                        hits = [i for i in hits if places[i]["adm1"] in regions]
                    if not hits:
                        st["hors oblast cité"] += 1
                        continue
                    hits = [i for i in hits if near_front(i, day.replace("-", ""))]
                    if not hits:
                        st["loin du front"] += 1
                        continue
                    (resolved if len(hits) == 1 else pending).append(hits)
                # homonymes : d'abord la cohérence spatiale avec les localités sans ambiguïté de la même phrase,
                # sinon la proximité du front
                anchor = None
                if resolved:
                    la = sorted(places[h[0]]["lat"] for h in resolved)[len(resolved) // 2]
                    lo = sorted(places[h[0]]["lon"] for h in resolved)[len(resolved) // 2]
                    anchor = (la, lo)

                def km(i, a):
                    return math.hypot((places[i]["lat"] - a[0]) * 111.2, (places[i]["lon"] - a[1]) * 111.2 * math.cos(math.radians(a[0])))
                chosen = []
                for h in resolved:
                    if anchor and len(resolved) >= 3 and km(h[0], anchor) > 150:
                        st["incohérent"] += 1
                        continue
                    chosen.append(h[0])
                for h in pending:
                    pick = None
                    if anchor:
                        near = [i for i in h if km(i, anchor) <= 60]
                        if len(near) == 1:
                            pick = near[0]
                    if pick is None:
                        st["ambigu"] += 1
                        continue
                    chosen.append(pick)
                for i in chosen:
                    p = places[i]
                    rows.append((day, p["id"], p["name"], p["lat"], p["lon"], "MoD RF" if src == "modru" else "État-major ukrainien"))
                    st["géocodé"] += 1
        # une annonce par localité et par jour
        rows = sorted(set(rows))
        out = os.path.join(ROOT, "data", f"claims_{src}.csv")
        if os.path.exists(out):  # garde-fou : jamais moins d'annonces que la version versionnée
            with open(out, encoding="utf-8") as fh:
                old_n = sum(1 for _ in fh) - 1
            if len(rows) < old_n * 0.95:
                print(f"build_claims.py : {src} en recul ({len(rows)} contre {old_n}), fichier conservé")
                continue
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "geonameid", "nom", "lat", "lon", "source"])
            w.writerows(rows)
        tot = st["mentions"]
        stats[src] = {k: v for k, v in st.items()}
        stats[src]["annonces"] = len(rows)
        stats[src]["taux_rejet"] = round(1 - st["géocodé"] / tot, 3) if tot else None
        # parmi les noms reconnus comme localités ukrainiennes (hors mots courants et localités russes)
        known = st["géocodé"] + st["ambigu"] + st["loin du front"] + st["hors oblast cité"] + st["incohérent"]
        stats[src]["taux_rejet_localites"] = round(1 - st["géocodé"] / known, 3) if known else None
        print(f"{src} : {tot} mentions, {st['géocodé']} géocodées ({len(rows)} annonces uniques), "
              f"rejet {stats[src]['taux_rejet']:.0%} (parmi les localités reconnues : {stats[src]['taux_rejet_localites'] or 0:.0%}) — " + ", ".join(f"{k} {v}" for k, v in st.items() if k not in ('mentions', 'géocodé')))
    json.dump(stats, open(os.path.join(ROOT, "data", "claims_stats.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
