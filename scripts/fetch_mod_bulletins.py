"""Bilans quotidiens et annonces de prise du MoD RF depuis l'abandon de MAX (en CI : --sources interfax,ria).

    python3 -I fetch_mod_bulletins.py <dossier_brut> [--since 2026-03-01] [--until AAAA-MM-JJ]
                                       [--sources tg,interfax,ria] [--html <dossier_cache_html>] [--budget <minutes>]

--budget : arrêt propre après N minutes (état sauvegardé, le passage suivant reprend) ; le premier rattrapage
de mars à aujourd'hui prend plusieurs heures (RIA surtout), réparti sur plusieurs passages de la CI.

Sources (toutes publiques, sans compte, robots.txt respecté, User-Agent honnête, >= 2 s entre deux requêtes par hôte) :
- tg       : aperçu web public t.me/s/mod_russia, MÊMES mots-clés que scripts/fetch_claims.py (annonces de prise,
             amorces des bilans). En CI, fetch_claims.py continue de faire ce travail : ne lancer ici que
             --sources interfax,ria.
- interfax : archives par jour https://www.interfax.ru/news/AAAA/MM/JJ/[page_N] (autorisées par robots.txt) ; titres
             filtrés (MoD RF + front), puis article ; on ne garde que les passages ENTRE GUILLEMETS attribués au MoD.
- ria      : plan du site mensuel https://ria.ru/sitemap_article.xml?date_start=…&date_end=… (déclaré dans
             robots.txt) ; articles dont le mot de l'URL est lié au MoD ou aux groupements ; mêmes règles de citation.
- TASS (robots.txt et articles en 403), mil.ru (injoignable hors de Russie), MAX (coquille JS, noindex), VK (compte) :
  exclus.

Sortie : <dossier_brut>/claims/mod_russia.json au format de fetch_claims.py (liste de {"id", "date", "text"}) : les
messages Telegram existants sont conservés tels quels ; chaque citation d'agence reçoit un identifiant synthétique
>= 9 000 000 000 (Telegram : < 10^6) tiré de l'URL ; « text » = citation verbatim du MoD seulement (faits, jamais
publiée). Dédoublonnage : une seule citation par (jour, groupement) pour les bilans (la plus longue), une seule par
(jour, ensemble de localités) pour les prises ; les prises déjà publiées sur Telegram le même jour sont écartées.
État incrémental : <dossier_brut>/modsrc/ (items.json, state.json). Les jours J-2 → J sont relus à chaque passage.
"""
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "ukraine-feux-research/1.0 (non-commercial research; https://djac57.github.io/ukraine-map/)"
PAUSE = 2.0
_last = {}
DEADLINE = None


class Budget(Exception):
    pass


def get(url, timeout=60, tries=3):
    if DEADLINE and time.time() > DEADLINE:
        raise Budget()
    host = urllib.parse.urlsplit(url).hostname
    for i in range(tries):
        wait = PAUSE - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.5"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _last[host] = time.time()
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            _last[host] = time.time()
            if e.code in (403, 404, 410):
                return e.code, b""        # 403 = protection : on n'insiste pas
            time.sleep(10 * (i + 1))
        except Exception:  # noqa: BLE001
            _last[host] = time.time()
            time.sleep(8 * (i + 1))
    return -1, b""


def decode(b):
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    for enc in ("utf-8", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def clean(h):
    h = re.sub(r"<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(h)).strip()


# ---------- citations verbatim (repris de ukraine-recherche/modfill/extract.py) ----------
VERBS = r"(?:говорится|сообщ|сказа|заяв|отмеча|подчерк|уточн|добав|пишет|информир|доложил|указ|приводятся|следует)"
OPEN = r'(?:^|(?<=[\.:!?]\s)|(?<=:\s)|(?<=—\s))["«„“]'
Q1 = re.compile(OPEN + r'(?=[А-ЯЁA-Z0-9«"])(.+?)["»“”]\s*,\s*[-–—]\s*' + VERBS, re.S)
Q2 = re.compile(r':\s*["«„“](?=[А-ЯЁA-Z0-9«"])(.+)["»“”]\s*\.?\s*$', re.S)
GREEDY1 = re.compile(OPEN + r'(?=[А-ЯЁA-Z0-9«"])(.+)["»“”]\s*,\s*[-–—]\s*' + VERBS, re.S)


def split_quotes(par):
    m = GREEDY1.search(par)
    if m:
        q = m.group(1).strip()
        if re.search(r'["»]\s*,\s*[-–—]\s*' + VERBS, q):
            return [x.strip() for x in Q1.findall(par)]
        return [q]
    m2 = Q2.search(par)
    return [m2.group(1).strip()] if m2 else []


# ---------- filtres ----------
MOD = re.compile(r"Минобороны|министерств\w* обороны|МО РФ|военное ведомство|ведомств", re.I)
CAPTURE = re.compile(r"освободил|освобождени[ея] населенн|установил[аи]? контроль|взял[аи]? под (?:свой )?контроль|"
                     r"взят[оаы]? под контроль|завершил[аи]? освобождение|освобожден[ыо]? населенн", re.I)
SUMMARY = re.compile(r"группировк\w* войск|в район(?:е|ах) населенн", re.I)
GROUP = re.compile(r"группировк\w* (?:войск )?[«\"]?(Север|Запад|Юг|Южн|Центр|Восток|Днепр)", re.I)
IFX_ITEM = re.compile(r'<div data-id="(\d+)">\s*<span>(\d\d:\d\d)</span>\s*<a href="(/[a-z]+/\d+)"[^>]*>\s*<h3>(.*?)</h3>', re.S)
IFX_WHO = re.compile(r"Минобороны|ВС РФ|ВС России|российские (?:войска|военные|силы)|войска РФ|военные РФ|группировк|"
                     r"армия России|российская армия|ВСУ", re.I)
IFX_WHAT = re.compile(r"контрол|освобо|группировк|позици|продвиж|поражени|населенн|рубеж|положени|наступ|вглубь|"
                      r"в глубину|потерял|потери|[Сс]водк|сел[ао]? |поселок|поселк|город")
IFX_NOT = re.compile(r"Минобороны (?:Украины|США|Британии|Израиля|Ирана|Китая|Белоруссии|КНДР)|арест|суд |обмен|"
                     r"военнопленн|ПВО|беспилотник|дрон|БПЛА")
# mot de l'URL : rendement mesuré sur 2026-03 (articles avec citation utile / lus) : groupements 30-90 %,
# minoborony 11 %, spetsoperatsiya 11 %, svo 7 %, voennye 6 %, vs 0 % ; on garde les plus rentables
RIA_SLUGS = {"minoborony", "vostok", "zapad", "sever", "yug", "tsentr", "dnepr", "gruppirovka", "osvobozhdenie"}
TG_KEYS = ["освобож", "освободил", "контроль над", "взят под контроль", "взяли под контроль",
           "группировк", "в районах населенных пунктов", "в районе населенного пункта"]   # = fetch_claims.py


def keep_quotes(quotes):
    return [q for q in quotes if CAPTURE.search(q) or SUMMARY.search(q)]


def synth_id(url):
    return 9_000_000_000 + int(hashlib.sha1(url.encode()).hexdigest()[:12], 16) % 900_000_000


# ---------- sources ----------
def cache_get(url, cache, name):
    if cache:
        fn = os.path.join(cache, name)
        if os.path.exists(fn):
            return 200, open(fn, "rb").read()
    st, data = get(url)
    if cache and st == 200:
        os.makedirs(os.path.dirname(os.path.join(cache, name)), exist_ok=True)
        open(os.path.join(cache, name), "wb").write(data)
    return st, data


def interfax(days, state, items, cache, recent):
    done = set(state.setdefault("interfax_days", []))
    seen = set(state.setdefault("interfax_art", []))
    try:
        _interfax(days, items, cache, recent, done, seen)
    finally:
        state["interfax_days"], state["interfax_art"] = sorted(done), sorted(seen)


def _interfax(days, items, cache, recent, done, seen):
    for day in days:
        d = day.isoformat()
        if d in done and day not in recent:
            continue
        heads, ids = [], set()
        for p in range(1, 9):
            url = f"https://www.interfax.ru/news/{day:%Y/%m/%d}/" + ("" if p == 1 else f"page_{p}/")
            c = cache if day not in recent else None
            st, data = cache_get(url, c, f"interfax/days/{d}_p{p}.html")
            if st != 200:
                break
            t = decode(data)
            new = [x for x in IFX_ITEM.findall(t) if x[0] not in ids]
            if not new:
                break
            for did, hm, href, title in new:
                ids.add(did)
                heads.append((href, clean(title)))
            if f"page_{p + 1}" not in t:
                break
        for href, title in heads:
            if not href.startswith("/russia/") or href in seen:
                continue
            if not (IFX_WHO.search(title) and IFX_WHAT.search(title)) or (IFX_NOT.search(title) and not CAPTURE.search(title)):
                continue
            url = "https://www.interfax.ru" + href
            st, data = cache_get(url, cache, f"interfax/art/{href.rsplit('/', 1)[1]}.html")
            if st != 200:
                continue
            seen.add(href)
            t = decode(data)
            m = re.search(r'<article[^>]*itemprop="articleBody"[^>]*>(.*?)</article>', t, re.S)
            body = m.group(1) if m else ""
            pars = [clean(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)]
            if not MOD.search(" ".join(pars) + " " + title):
                continue
            qs = keep_quotes([q for x in pars if x for q in split_quotes(x)])
            if qs:
                items.append({"src": "interfax", "url": url, "date": d, "quotes": qs})
        if day not in recent and heads:
            done.add(d)
        print(f"interfax {d} : {len(heads)} titres", flush=True)


def ria(months, state, items, cache, recent_months):
    seen = set(state.setdefault("ria_art", []))
    try:
        _ria(months, items, cache, seen)
    finally:
        state["ria_art"] = sorted(seen)


def _ria(months, items, cache, seen):
    for m0, m1 in months:
        url = f"https://ria.ru/sitemap_article.xml?date_start={m0:%Y%m%d}&date_end={m1:%Y%m%d}"
        st, data = get(url, timeout=120)
        if st != 200:
            print("ria : plan du site illisible", m0, st, flush=True)
            continue
        locs = re.findall(r"<loc>(https://ria\.ru/(\d{8})/([a-z0-9\-]+)-(\d+)\.html)</loc>", decode(data))
        todo = [x for x in locs if x[2] in RIA_SLUGS and x[3] not in seen]
        print(f"ria {m0:%Y-%m} : {len(locs)} articles, {len(todo)} à lire", flush=True)
        for n, (u, ymd, slug, aid) in enumerate(sorted(todo, key=lambda x: x[1])):
            st, data = cache_get(u, cache, f"ria/art/{aid}.html")
            if n % 50 == 49:
                print(f"  ria {ymd} : {n + 1}/{len(todo)}", flush=True)
            if st != 200:
                print(f"  ria {u} : {st}", flush=True)
                continue
            seen.add(aid)
            t = decode(data)
            ti = re.search(r"<title>(.*?)</title>", t, re.S)
            title = clean(ti.group(1)) if ti else ""
            pars = [clean(p) for p in re.findall(r'<div class="article__text"[^>]*>(.*?)</div>', t, re.S)]
            pars += [clean(p) for p in re.findall(r'<div class="article__quote-text"[^>]*>(.*?)</div>', t, re.S)]
            if not MOD.search(" ".join(pars) + " " + title):
                continue
            qs = keep_quotes([q for x in pars if x for q in split_quotes(x)])
            if qs:
                items.append({"src": "ria", "url": u, "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}", "quotes": qs})


def telegram(since, posts):
    """Comme fetch_claims.py (mêmes mots-clés, date UTC), du plus récent vers --since, arrêt au premier déjà vu."""
    known = {p["id"] for p in posts if p["id"] < 10 ** 9}
    before, pages = None, 0
    while True:
        st, data = get("https://t.me/s/mod_russia" + (f"?before={before}" if before else ""))
        pages += 1
        if st != 200:
            break
        batch = []
        for block in re.split(r'<div class="tgme_widget_message_wrap', decode(data))[1:]:
            m = re.search(r'data-post="mod_russia/(\d+)"', block)
            d = re.search(r'datetime="([^"]+)"', block)
            x = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, flags=re.S)
            if m and d and x:
                text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", x.group(1))))
                batch.append({"id": int(m.group(1)), "date": d.group(1)[:10], "text": text})
        if not batch:
            break
        stop = False
        for p in batch:
            if p["id"] in known and before is not None:
                stop = True
            if p["date"] >= since and any(k in p["text"].lower() for k in TG_KEYS) and p["id"] not in known:
                posts.append(p)
                known.add(p["id"])
        oldest = min(batch, key=lambda p: p["id"])
        if stop or oldest["date"] < since or (before and oldest["id"] >= before):
            break
        before = oldest["id"]
        if pages % 50 == 0:
            print(f"tg : {pages} pages, {oldest['date']}", flush=True)
    print(f"tg : {pages} pages lues", flush=True)


# ---------- fusion ----------
def stems(text):
    return {w[:5].lower() for w in re.findall(r"\b[А-ЯЁ][а-яё]{3,}", text)}


def merge(posts, items):
    tg = [p for p in posts if p["id"] < 10 ** 9]
    tg_caps = {}
    for p in tg:
        if CAPTURE.search(p["text"]):
            tg_caps.setdefault(p["date"], set()).update(stems(p["text"]))
    # un message synthétique par (jour, groupement) : réunion des citations distinctes de toutes les agences ;
    # une citation sans groupement hérite de celui de l'article quand il est unique
    groups, caps = {}, {}
    for it in items:
        art_g = {GROUP.search(q).group(1).lower()[:3] for q in it["quotes"] if GROUP.search(q)}
        for q in it["quotes"]:
            if CAPTURE.search(q):
                st = stems(q)
                if st and st <= tg_caps.get(it["date"], set()):
                    continue                      # prise déjà publiée sur Telegram ce jour-là
                caps.setdefault((it["date"], ",".join(sorted(st))), q)
                continue
            g = GROUP.search(q)
            g = g.group(1).lower()[:3] if g else (next(iter(art_g)) if len(art_g) == 1 else "x")
            groups.setdefault((it["date"], g), {}).setdefault(re.sub(r"\W+", "", q.lower())[:200], q)
    out = list(tg)
    for (d, g), qs in sorted(groups.items()):
        txt = "\n".join(qs.values())
        if g != "x" and "группировк" not in txt.lower():
            txt = f"[группировка войск {g}]\n" + txt   # repère pour build_official_signals (exige « группировк… войск »)
        out.append({"id": synth_id(f"{d}#g:{g}"), "date": d, "text": txt})
    for (d, st), q in sorted(caps.items()):
        out.append({"id": synth_id(f"{d}#c:{st}"), "date": d, "text": q})
    seen, final = set(), []
    for p in sorted(out, key=lambda p: p["id"]):
        if p["id"] not in seen:
            seen.add(p["id"])
            final.append(p)
    return final


def main():
    a = sys.argv[1:]
    raw = a[0]
    opt = {a[i]: a[i + 1] for i in range(1, len(a) - 1, 2)}
    since = dt.date.fromisoformat(opt.get("--since", "2026-03-01"))
    until = dt.date.fromisoformat(opt.get("--until", dt.date.today().isoformat()))
    sources = opt.get("--sources", "tg,interfax,ria").split(",")
    cache = opt.get("--html")
    global DEADLINE
    if opt.get("--budget"):
        DEADLINE = time.time() + 60 * float(opt["--budget"])
    sd = os.path.join(raw, "modsrc")
    os.makedirs(sd, exist_ok=True)
    os.makedirs(os.path.join(raw, "claims"), exist_ok=True)
    spath, ipath = os.path.join(sd, "state.json"), os.path.join(sd, "items.json")
    state = json.load(open(spath)) if os.path.exists(spath) else {}
    items = json.load(open(ipath)) if os.path.exists(ipath) else []
    cpath = os.path.join(raw, "claims", "mod_russia.json")
    posts = json.load(open(cpath)) if os.path.exists(cpath) else []
    today = dt.date.today()
    recent = {today - dt.timedelta(days=k) for k in range(3)}
    days = [since + dt.timedelta(days=k) for k in range((until - since).days + 1)]
    try:
        if "tg" in sources:
            telegram(since.isoformat(), posts)
        if "interfax" in sources:
            interfax(days, state, items, cache, recent)
        if "ria" in sources:
            months, m = [], since.replace(day=1)
            while m <= until:
                nxt = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
                months.append((m, min(nxt - dt.timedelta(days=1), until)))
                m = nxt
            ria(months, state, items, cache, {months[-1][0]})
    except Budget:
        print("budget de temps atteint : arrêt propre, reprise au prochain passage", flush=True)
    finally:
        uniq = {}
        for it in items:
            uniq[it["url"]] = it
        items = sorted(uniq.values(), key=lambda x: (x["date"], x["url"]))
        json.dump(items, open(ipath, "w"), ensure_ascii=False)
        json.dump(state, open(spath, "w"))
        merged = merge(posts, items)
        json.dump(merged, open(cpath, "w"), ensure_ascii=False)
        print(f"mod_russia.json : {len(merged)} messages ({sum(p['id'] >= 10 ** 9 for p in merged)} citations d'agences)")


if __name__ == "__main__":
    main()
