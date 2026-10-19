"""Outils communs de la couche « signaux Telegram » : collecte de l'aperçu public t.me/s/<canal> (sans compte, sans clé),
mots-clés de combat et de présence, lieux (coordonnées explicites, localités GeoNames non ambiguës d'Ukraine et des
oblasts russes frontaliers), maille de 2 km et tronçon de 24 km de la grille du projet.

Portage des modules de recherche ukraine-recherche/signals2/telegram/tg_activity.py (avec extract.block_of) et
signals3/crawl_sens.py (19/10/2026) ; mêmes règles, pour que les comptes de la CI prolongent l'historique.
Aucun texte n'est publié : les messages restent dans <brut>/telegram (cache Actions), seuls des comptes par tronçon sortent.
"""
import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------- canaux (camp de la source, pas d'échantillonnage en identifiants : 20 = tout, 40 = une page sur deux) ----------
# mêmes canaux et mêmes pas que la collecte historique (signals3/crawl_sens.py), pour que les comptes restent comparables
CHANNELS = [("rybar", "ru", 20), ("dva_majors", "ru", 20), ("wargonzo", "ru", 20), ("voenkorKotenok", "ru", 20),
            ("epoddubny", "ru", 20), ("notes_veterans", "ru", 20), ("boris_rozhin", "ru", 40), ("milinfolive", "ru", 40),
            ("rvvoenkor", "ru", 40),
            ("deepstateua", "ua", 20), ("butusovplus", "ua", 20), ("noel_reports", "ua", 20), ("CITeam", "tiers", 20),
            ("Tsaplienko", "ua", 40), ("operativnoZSU", "ua", 40)]

# ---------- grille du projet (site/front.js : grid) ----------
R = 6371007.2
LON0, LAT0 = 31.5, 48.5
X0, Y1, SIZE, NX, NY = -700000.0, 470000.0, 2000.0, 700, 470
TW = 12                      # tronçon = 12 × 12 mailles de 2 km = 24 km
TC = math.ceil(NX / TW)      # 59 tronçons par rangée


def laea(lon, lat):
    l, p, l0, p0 = map(math.radians, (lon, lat, LON0, LAT0))
    k = math.sqrt(2 / (1 + math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0)))
    return R * k * math.cos(p) * math.sin(l - l0), R * k * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0))


def cell_of(lon, lat):
    """Maille de 2 km (j * NX + i), ou -1 hors grille (équivalent de extract.block_of(...)[1])."""
    x, y = laea(lon, lat)
    i, j = int((x - X0) // SIZE), int((Y1 - y) // SIZE)
    return j * NX + i if 0 <= i < NX and 0 <= j < NY else -1


def tr_of_cell(c):
    return ((c // NX) // TW) * TC + (c % NX) // TW


def tr_origin(tr):
    return (tr % TC) * TW, (tr // TC) * TW


# ---------- mots-clés ----------
CONTACT = re.compile(r"штурм|\bбо[їй]|\bбоя[хм]|\bбо[еє]в|бойов\w* зіткн|боестолкн|наступ|контрнаступ|контратак|зачист|просун|продвин|"
                     r"вклинил|окруж|оточ|освобо|звільнил|взяли под контроль|позиці|позици|ДРГ|assault|fighting|clash|advance|"
                     r"captur|liberat|encircl|offensive", re.I)
# présence signalée sans assaut explicite : ДРГ, infiltration, « замечен », « помічено »…
PRESENCE = re.compile(r"ДРГ|диверс|инфильтр|інфільтр|просачива|просочува|замечен|обнаружен|помічен|виявлен|присутстви|присутн|"
                      r"разведгрупп|розвідгруп|infiltrat|sabotage group", re.I)
LOCPREP = re.compile(r"(?:^|\s)(?:в|у|во|біля|поблизу|під|под|неподалік|около|возле|районі|районе|напрямку|направлении|"
                     r"бік|сторону|на|по|до|из|з|із|від|от|обстріл|обстрел|обстріли|обстрелы|атака|атаки|штурм|штурми|удар|удары|удари|"
                     r"near|in|at|around|outside|towards|of)\s*$", re.I)
# ajouts du 19/10/2026 (attribution à la phrase) : nom de lieu après un nom générique (« населенный пункт Крейдянка »,
# « село », « н. п. ») ou dans une énumération qui suit un lieu reconnu (« Нестерное, Лукашово и Грачевка »)
LOCNOUN = re.compile(r"(?:^|\s)(?:пункт\w*|сел[оаеиі]|селом|селищ\w*|поселк\w*|посел[её]к|смт|пгт|н\.\s?п\.|с\.|м\.|г\.|"
                     r"деревн\w*|хутор\w*|город\w*|місто|міста|містом|village|town)\s*$", re.I)
REGION_NEXT = re.compile(r"(?:\s*(?:,|и\b|і\b|та\b|and\b)\s*[А-ЯІЇЄҐЁ][\w-]*){0,5}\s*(?:област\w*|обл\b|обл\.|район\w*|р-н|направлен\w*|напрям\w*|"
                         r"округ\w*|губерн\w*|приграничь\w*|прикордонн\w*|участк\w*|ділянц\w*|фронт\w*|групп?ировк\w*|"
                         r"oblast|region|direction|axis|front)", re.I)
LISTSEP = re.compile(r"^\s*(?:,|,?\s*(?:и|і|та|й|and)\s)\s*$", re.I)
CAPW = re.compile(r"(?<![\w'’])([А-ЯІЇЄҐЁA-Z][а-яіїєґёa-z'’ʼ]+)")
ENDINGS = sorted(["ого", "ому", "ої", "ій", "ів", "ах", "ями", "ами", "ям", "ою", "ею", "ий", "ом", "ем", "ой", "ым", "ых",
                  "ей", "ая", "ое", "ые", "а", "у", "і", "и", "е", "я", "ю", "ь", "о", "ї", "й", "ы"], key=len, reverse=True)


def norm(s):
    return s.lower().replace("’", "'").replace("ʼ", "'").replace("ё", "е").strip()


def stem(s):
    out = []
    for p in re.split(r"[\s\-]+", norm(s)):
        for e in ENDINGS:
            if p.endswith(e) and len(p) - len(e) >= 4:
                p = p[:-len(e)]
                break
        out.append(p)
    return " ".join(out)


STOP = {stem(w) for w in ("Україна", "Украина", "Росія", "Россия", "Київ", "Москва", "Крим", "Крым", "Донбас", "Донбасс",
                          "Зеленський", "Путін", "Путин", "Слава", "Мир", "Победа", "Перемога", "Сили", "Силы", "ЗСУ", "ВСУ",
                          "Армія", "Армия", "Генштаб", "Telegram", "Ukraine", "Russia", "Kyiv", "Moscow", "Сумщина",
                          "Харківщина", "Донеччина", "Запоріжжя область", "Херсонщина", "Дніпропетровщина")}


# ---------- dictionnaire de lieux ----------
def load_gaz(tess_path):
    """Localités d'Ukraine (GeoNames, gn_UA_tess.geojson) dont le nom (radical) est non ambigu."""
    gj = json.load(open(tess_path, encoding="utf-8"))
    idx = defaultdict(set)
    for f in gj["features"]:
        pr = f["properties"]
        p = (round(float(pr["longitude"]), 4), round(float(pr["latitude"]), 4), int(pr.get("population") or 0))
        names = {n.strip() for n in (pr.get("alternatenames") or "").split(",")} | {pr["name"]}
        for n in names:
            if len(n) < 4:
                continue
            k = stem(n)
            if len(k.replace(" ", "")) >= 4 and k not in STOP:
                idx[k].add(p)
    uniq = {k: next(iter(v))[:2] for k, v in idx.items() if len(v) == 1}
    for k, v in idx.items():
        if len(v) < 2:
            continue
        v = sorted(v, key=lambda p: -p[2])
        # un nom partagé par des lieux distants de < 5 km reste non ambigu (ville et quartier)
        if len(v) <= 3 and max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in v for b in v) < 0.07:
            uniq[k] = v[0][:2]
        # ville d'au moins 20 000 habitants face à des homonymes de moins de 2 000 : la ville
        elif v[0][2] >= 20000 and v[1][2] * 5 <= v[0][2]:
            uniq[k] = v[0][:2]
    return uniq


def load_gaz_all(tess_path, ru_path=os.path.join(ROOT, "data", "ru_border.tsv")):
    """Ukraine + localités des oblasts de Koursk, Belgorod et Briansk (GeoNames RU, CC BY 4.0, data/ru_border.tsv) ;
    un homonyme d'une localité ukrainienne est écarté (ambigu)."""
    gaz = load_gaz(tess_path)
    idx = defaultdict(set)
    for line in open(ru_path, encoding="utf-8"):
        gid, name, alts, lat, lon, pop = line.rstrip("\n").split("\t")
        for n in {x.strip() for x in alts.split(",") if re.search(r"[а-яё]", x.lower())} | {name}:
            if len(n) < 4:
                continue
            k = stem(n)
            if len(k.replace(" ", "")) >= 4 and k not in STOP:
                idx[k].add((round(float(lon), 4), round(float(lat), 4), int(pop or 0)))
    for k, v in idx.items():
        if k in gaz:
            continue
        v = sorted(v, key=lambda p: -p[2])
        if len(v) == 1 or (v[0][2] >= 20000 and v[1][2] * 5 <= v[0][2]):
            gaz[k] = v[0][:2]
    return gaz


# ---------- coordonnées explicites ----------
DEC = re.compile(r"(?<![\d.])(4[4-9]|5[0-3])[.,](\d{3,7})\s*[°]?\s*[NnСс]?\s*[,;/\s]\s*(2[2-9]|3\d|4[01])[.,](\d{3,7})(?![\d])")
DMS = re.compile(r"(4[4-9]|5[0-3])\s*°\s*(\d{1,2})\s*['′’]\s*(\d{1,2}(?:[.,]\d+)?)?\s*[\"″”]?\s*[NnСс]?\s*[,;\s]+\s*"
                 r"(2[2-9]|3\d|4[01])\s*°\s*(\d{1,2})\s*['′’]\s*(\d{1,2}(?:[.,]\d+)?)?")
MGRS = re.compile(r"\b(3[5-8])\s?([TU])\s?([A-HJ-NP-Z]{2})\s?(\d{4,5})\s?(\d{4,5})\b")


def utm_to_ll(zone, easting, northing):
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    k0 = 0.9996
    x = easting - 500000.0
    m = northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p1 = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu) \
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
    ep2 = e2 / (1 - e2)
    n1 = a / math.sqrt(1 - e2 * math.sin(p1) ** 2)
    t1 = math.tan(p1) ** 2
    c1 = ep2 * math.cos(p1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = p1 - (n1 * math.tan(p1) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                           + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(p1)
    return math.degrees(lon) + (zone - 1) * 6 - 180 + 3, math.degrees(lat)


def mgrs_to_ll(zone, band, sq, e, n):
    zone = int(zone)
    cols = ["ABCDEFGH", "JKLMNPQR", "STUVWXYZ"][(zone - 1) % 3]
    rows = "ABCDEFGHJKLMNPQRSTUV"
    ci, ri = cols.find(sq[0]), rows.find(sq[1])
    if ci < 0 or ri < 0:
        return None
    if zone % 2 == 0:
        ri = (ri - 5) % 20
    easting = (ci + 1) * 100000 + int(e.ljust(5, "0"))
    northing = ri * 100000 + int(n.ljust(5, "0"))
    base = 4400000 if band == "T" else 5300000
    while northing < base:
        northing += 2000000
    while northing > base + 1000000:
        northing -= 2000000
    lon, lat = utm_to_ll(zone, easting, northing)
    return (lon, lat) if 43 < lat < 54 and 21 < lon < 42 else None


def coords_in(text):
    out = []
    for m in DEC.finditer(text):
        out.append((float(m.group(3) + "." + m.group(4)), float(m.group(1) + "." + m.group(2))))
    for m in DMS.finditer(text):
        lat = int(m.group(1)) + int(m.group(2)) / 60 + float((m.group(3) or "0").replace(",", ".")) / 3600
        lon = int(m.group(4)) + int(m.group(5)) / 60 + float((m.group(6) or "0").replace(",", ".")) / 3600
        out.append((lon, lat))
    for m in MGRS.finditer(text):
        r = mgrs_to_ll(*m.groups())
        if r:
            out.append(r)
    return out


def places_in(text, gaz):
    """Mots capitalisés précédés d'une préposition de lieu (ou d'un nom de frappe/combat) ; deux mots consécutifs
    (« Часів Яр ») puis un mot."""
    out = []
    ws = list(CAPW.finditer(text))
    k = 0
    prev_end = -1      # fin du dernier lieu reconnu (énumérations)
    while k < len(ws):
        m = ws[k]
        pre = text[max(0, m.start() - 25):m.start()]
        if not (LOCPREP.search(pre) or LOCNOUN.search(pre) or (prev_end >= 0 and LISTSEP.match(text[prev_end:m.start()]))):
            k += 1
            continue
        hit = None
        # adjectif de région ou d'axe (« Брянской области », « Херсонском направлении », « Курском приграничье ») : ce
        # n'est pas une localité (sinon un village homonyme, souvent en Crimée, est pris à tort ; correctif du 19/10/2026)
        if REGION_NEXT.match(text, m.end()):
            k += 1
            continue
        if k + 1 < len(ws) and text[m.end():ws[k + 1].start()] in (" ", "-"):
            two = stem(m.group(1) + " " + ws[k + 1].group(1))
            if two in gaz:
                hit, k = gaz[two], k + 1
        if hit is None:
            one = stem(m.group(1))
            if one in gaz and one not in STOP:
                hit = gaz[one]
        if hit:
            out.append(hit)
            prev_end = ws[k].end()
        k += 1
    return out


def cells_list(text, gaz):
    """Mailles de 2 km citées (avec répétitions, dans l'ordre : coordonnées explicites puis localités non ambiguës)."""
    return [c for c in (cell_of(lo, la) for lo, la in coords_in(text) + places_in(text, gaz)) if c >= 0]


def cells_of(text, gaz):
    """Mailles de 2 km citées par le message entier (ancienne attribution, gardée pour les mesures)."""
    return sorted(set(cells_list(text, gaz)))


# ---------- attribution à la phrase (19/10/2026) ----------
# Avant : un message comptait pour TOUTES les localités qu'il cite (bulletins récapitulatifs étalés jusqu'à 760 km).
# Maintenant : une attaque ou une présence ne compte que pour les localités de la MÊME phrase que l'indice ; une phrase
# dont les localités s'étalent sur plus de SPREAD_KM n'en garde que le tronçon le plus cité (SPREAD_MODE="top") ou est
# écartée (SPREAD_MODE="drop").
SPREAD_KM = 30.0
SPREAD_MODE = os.environ.get("TG_SPREAD", "top")
_ABBR = re.compile(r"(?:^|[\s(])(?:[а-яіїєґё]{1,2}|н\.\s?п|смт|пгт|р-н|обл|ул|вул|просп)\.$")


def sentences(text):
    """Découpe en phrases (. ! ? … ; ou retour à la ligne suivis d'un blanc), sans couper après une abréviation de lieu
    (« с. », « м. », « н.п. », « смт. »…)."""
    out, last = [], 0
    for m in re.finditer(r"[.!?…;\n]\s+", text):
        if text[m.start()] == "." and _ABBR.search(text[max(0, m.start() - 8):m.start() + 1]):
            continue
        out.append(text[last:m.end()])
        last = m.end()
    out.append(text[last:])
    res = []
    for s in out:
        # une ligne qui commence par des coordonnées (« 49.81414,37.50836 ») précise la phrase précédente
        if res and re.match(r"\s*(?:4[4-9]|5[0-3])[.,]\d{3,}", s):
            res[-1] += s
        elif s.strip():
            res.append(s)
    return res


def spread_km(cells):
    """Plus grande distance entre deux mailles citées (km, maille de 2 km)."""
    pts = [(c % NX, c // NX) for c in set(cells)]
    return max((math.hypot(a[0] - b[0], a[1] - b[1]) * SIZE / 1000 for a in pts for b in pts), default=0.0)


def restrict(cells, mode=None):
    """Garde-fou d'étalement : None si la phrase est écartée, sinon les mailles retenues (ensemble)."""
    mode = mode or SPREAD_MODE
    if spread_km(cells) <= SPREAD_KM:
        return set(cells)
    if mode == "drop":
        return None
    n = defaultdict(int)
    for c in cells:
        n[tr_of_cell(c)] += 1
    best = max(n.values())
    top = next(tr_of_cell(c) for c in cells if n[tr_of_cell(c)] == best)   # à égalité : le premier cité
    return {c for c in cells if tr_of_cell(c) == top}


def events_message(text, camp, gaz):
    """Attribution au MESSAGE (règle historique de signals3, pour le MODÈLE) : les indices du message entier
    (classify_strict, presence) comptent pour toutes les localités qu'il cite. Faux comme « présence à tel point »
    (bulletins récapitulatifs), mais porteur d'activité par secteur : le modèle de sens y perd avec la phrase (AUC sens
    0,738 → 0,707, mesure du 19/10/2026). Géocodage corrigé (adjectifs de région, « село / н. п. », énumérations)."""
    import tg_sens as S
    if not (CONTACT.search(text) or PRESENCE.search(text)):
        return {}
    cells = set(cells_list(text, gaz))
    if not cells:
        return {}
    kinds = {"loc"}
    cls, pres = S.classify_strict(text, camp), S.presence(text, camp)
    if cls in ("ua", "ru", "sq"):
        kinds.add(cls)
    if pres in ("ua", "ru"):
        kinds.add("pres_" + pres)
    return {c: kinds for c in cells}


def events(text, camp, gaz, mode=None):
    """{maille: ensemble d'indices} pour un message : « loc » (phrase de combat ou de présence localisée), « ua » / « ru » /
    « sq » (qui attaque, tg_sens.classify_strict sur la phrase), « pres_ua » / « pres_ru » (tg_sens.presence sur la phrase).
    Chaque indice ne compte que pour les localités de sa phrase."""
    import tg_sens as S
    out = defaultdict(set)
    for s in sentences(text):
        cls, pres = S.classify_strict(s, camp), S.presence(s, camp)
        if not (CONTACT.search(s) or PRESENCE.search(s) or cls in ("ua", "ru", "sq") or pres in ("ua", "ru")):
            continue
        cl = cells_list(s, gaz)
        if not cl:
            continue
        keep = restrict(cl, mode)
        if not keep:
            continue
        kinds = {"loc"}
        if cls in ("ua", "ru", "sq"):
            kinds.add(cls)
        if pres in ("ua", "ru"):
            kinds.add("pres_" + pres)
        for c in keep:
            out[c] |= kinds
    return out


# ---------- collecte ----------
UA = "ukraine-map/1.0 (+https://djac57.github.io/ukraine-map/; non-commercial research, aggregate counts only)"
PAUSE = float(os.environ.get("TG_PAUSE", "0.8"))    # secondes entre deux requêtes (une seule à la fois)


STATS = {"requests": 0, "429": 0, "errors": 0, "wait_s": 0.0}


def _wait(s):
    STATS["wait_s"] += s
    time.sleep(s)


def get(name, before=None):
    url = f"https://t.me/s/{name}" + (f"?before={before}" if before else "")
    for attempt in range(5):
        STATS["requests"] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,uk;q=0.8,en;q=0.5"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return None
            STATS["429" if e.code == 429 else "errors"] += 1
            try:
                ra = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                ra = 0
            # Retry-After plafonné (on a vu des attentes interminables) : on préfère abandonner la page
            _wait(min(ra or 15 * (attempt + 1), 90))
        except Exception:
            STATS["errors"] += 1
            _wait(5 * (attempt + 1))
    return None


def parse(page, name):
    """[(identifiant, jour AAAA-MM-JJ, texte)] des messages d'une page de l'aperçu public."""
    out = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', page)[1:]:
        i = re.search(rf'data-post="{re.escape(name)}/(\d+)"', block, flags=re.I)
        d = re.search(r'datetime="([^"]+)"', block)
        t = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, flags=re.S)
        if i and d:
            txt = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", t.group(1)))) if t else ""
            out.append((int(i.group(1)), d.group(1)[:10], txt))
    return out
