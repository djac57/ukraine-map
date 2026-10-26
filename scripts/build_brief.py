#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Brief quotidien « ce qui se passe » : extrait NOS détections (flèches poussée possible/probable) du mois courant via
le diagnostic __uafArrows de la carte (Playwright), les nomme par un repère reconnaissable, et écrit site/brief.js.
DÉTERMINISTE (aucun LLM) → fiable, gratuit, reproductible. i18n : brief.js porte {place, side, kind} ; app.js localise."""
import json, os, sys, threading, http.server, socketserver, functools, math, re, datetime as dt

# garde-fou anti-drift LLM : fr/en doivent être latin pur, uk/ru cyrillique — aucune écriture parasite (arabe/CJK…)
_FOREIGN = re.compile(r"[؀-ۿ֐-׿　-鿿Ͱ-Ͽ]")   # arabe, hébreu, CJK, grec
_CYR = re.compile(r"[Ѐ-ӿ]")


def _ok_trend(t):
    """True si les 4 phrases sont dans la bonne écriture (sinon on jette la tendance de ce lieu, le brief reste)."""
    if not t or not all(t.get(k) for k in ("fr", "en", "uk", "ru")):
        return False
    if _FOREIGN.search(t["fr"]) or _FOREIGN.search(t["en"]):   # pas d'arabe/CJK/grec dans fr/en
        return False
    if _CYR.search(t["fr"]) or _CYR.search(t["en"]):           # pas de cyrillique dans fr/en
        return False
    if not _CYR.search(t["uk"]) or not _CYR.search(t["ru"]):   # uk/ru doivent être cyrilliques
        return False
    return True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
PLACES = json.load(open(os.path.join(SITE, "places.json")))["places"]   # [nom, lat, lon, oblastIdx, pop, keys]
# repères connus du front : préférés quand nos coords tombent dans une poche de hameaux (pop 0) type Komar
NOTABLE = {"Komar", "Velyka Novosilka", "Novopavlivka", "Pokrovsk", "Myrnohrad", "Kupiansk", "Lyman", "Dobropillia",
           "Toretsk", "Siversk", "Vovchansk", "Chasiv Yar", "Kostiantynivka", "Kurakhove", "Vuhledar", "Robotyne",
           "Kreminna", "Bakhmut", "Avdiyivka", "Marinka", "Huliaipole", "Orikhiv", "Kramatorsk", "Sloviansk"}


def name_of(lon, lat):
    cl = math.cos(math.radians(lat))
    near = sorted(((((r[2] - lon) * cl) ** 2 + (r[1] - lat) ** 2, r) for r in PLACES))[:10]
    near = [r for d, r in near if d ** 0.5 < 11 / 111.0] or [near[0][1]]
    for r in near:                       # 1) un repère connu dans le secteur
        if r[0] in NOTABLE:
            return r[0]
    pop = max(near, key=lambda r: r[4])  # 2) sinon la localité la plus peuplée du secteur
    return pop[0] if pop[4] > 0 else near[0][0]   # 3) sinon la plus proche


def extract():
    from playwright.sync_api import sync_playwright

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    with socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=SITE)) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_page(viewport={"width": 1200, "height": 900})
            pg.goto(f"http://127.0.0.1:{port}/index.html", wait_until="domcontentloaded", timeout=90000)
            pg.wait_for_function("() => !!(window.FRONTP && window.FRONTP.mcsd && window.FRONTP.asym && window.__uafArrowsReady && window.__uafArrowsReady() && window.__uafArrows)", timeout=120000)
            pg.wait_for_timeout(2500)
            fi = pg.evaluate("() => window.__uafDraw().front")
            arrows = []
            for _ in range(10):   # retry : les flèches dépendent de FRONT_SENS/mcsd chargés en async
                arrows = []
                for L in ("overview", "10", "20"):   # 'overview' = niveau des détections à zoom par défaut (possible/probable)
                    arrows += pg.evaluate("([fi,L]) => window.__uafArrows(fi, L)", [fi, L])
                if arrows:
                    break
                pg.wait_for_timeout(1500)
            month = pg.evaluate("() => { const i=window.__uafDraw().front; return (window.FRONT && window.FRONT.months && window.FRONT.months[i] && window.FRONT.months[i].date) || null; }")
            # charger le champ « contact » (calque off par défaut) pour que __uafContactAt renvoie une valeur
            pg.evaluate("() => { const c = document.getElementById('lyrContact'); if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event('change')); } }")
            pg.wait_for_timeout(1800)
            sigs, trends = {}, {}   # signaux du mois + trajectoire mensuelle (6 mois) par détection
            for a in arrows:
                key = f"{a['lon']},{a['lat']}"
                if key not in sigs:
                    sigs[key] = pg.evaluate("([fi,lon,lat]) => window.__uafSignalsAt(fi, lon, lat)", [fi, a["lon"], a["lat"]]) or {}
                    trends[key] = pg.evaluate("([lon,lat]) => window.__uafTrendAt(lon, lat, 6)", [a["lon"], a["lat"]]) or []
            b.close()
    print(f"[debug] fi={fi} month={month} arrows={len(arrows)} possible={sum(1 for a in arrows if a.get('possible'))} "
          f"probable={sum(1 for a in arrows if a.get('probable'))}", file=sys.stderr)
    return arrows, month, sigs, trends


def llm_trends(dets):
    """Couche TENDANCE (best-effort) : un seul appel Gemini lit la trajectoire mensuelle de NOS signaux
    (contrôle russe %, P(mouvement) %) par détection et écrit une courte phrase FR « plus ample que le
    déterministe » (tendance longue : ralentit / s'installe / repart). Dégradation propre : clé absente ou
    échec → {} et le brief déterministe s'affiche quand même. N'invente rien : la phrase doit coller aux chiffres."""
    try:
        import gemini_client as gc
    except Exception:
        return {}
    if not gc.have_key():
        print("[tendance] pas de clé Gemini → brief déterministe seul", file=sys.stderr)
        return {}
    payload = []
    for d in dets:
        traj = d.get("traj") or []
        if len(traj) < 3:            # trop court pour parler de tendance
            continue
        payload.append({"place": d["place"], "camp": d["side"], "type": d["kind"],
                        "trajectoire": [{"mois": t["mois"], "controle_russe_pct": t["ctrl"], "p_mouvement_pct": t["pmove"]} for t in traj]})
    if not payload:
        return {}
    prompt = (
        "Tu commentes une carte du front ukrainien. Pour chaque lieu ci-dessous je te donne la TRAJECTOIRE "
        "mensuelle de deux signaux mesurés par nos modèles : `controle_russe_pct` (part du secteur sous contrôle "
        "russe) et `p_mouvement_pct` (probabilité estimée d'un mouvement de ligne ce mois). "
        "Écris pour chaque lieu UNE phrase courte (max ~15 mots) décrivant la TENDANCE DE FOND sur la période "
        "(p. ex. « pression russe qui remonte depuis l'été », « avancée russe qui s'est stabilisée », "
        "« poche reprise par l'Ukraine cet été »), et fournis-la dans les QUATRE langues : français (fr), anglais "
        "(en), ukrainien (uk), russe (ru) — même sens exact dans chaque langue. Reste STRICTEMENT fidèle aux "
        "chiffres : ne cite aucun chiffre, n'invente aucun lieu ni événement, pas de date absolue, pas de pronostic "
        "militaire. Ton neutre et sobre.\n\n" + json.dumps(payload, ensure_ascii=False))
    schema = {"type": "object", "properties": {"tendances": {"type": "array", "items": {
        "type": "object", "properties": {"place": {"type": "string"}, "fr": {"type": "string"},
        "en": {"type": "string"}, "uk": {"type": "string"}, "ru": {"type": "string"}},
        "required": ["place", "fr", "en", "uk", "ru"]}}}, "required": ["tendances"]}
    try:
        raw = gc.generate(prompt, schema=schema, temperature=0.2, max_tokens=1024)
        got = {r["place"]: {k: (r.get(k) or "").strip() for k in ("fr", "en", "uk", "ru")}
               for r in json.loads(raw).get("tendances", []) if r.get("fr")}
        out = {d["place"]: got[d["place"]] for d in dets if _ok_trend(got.get(d["place"]))}
        print(f"[tendance] {len(out)} phrase(s) LLM (4 langues), {len(got) - len(out)} rejetée(s) anti-drift", file=sys.stderr)
        return out
    except Exception as e:
        print(f"[tendance] Gemini ignoré ({e}) → brief déterministe seul", file=sys.stderr)
        return {}


def main():
    arrows, month, sigs, trends = extract()
    seen, det = set(), []
    for a in arrows:
        if a.get("probable"):
            kind, side = "ru_probable", "ru"
        elif a.get("possible"):
            kind, side = "ua_possible", "ua"
        elif abs(a.get("v", 0)) >= 2.5:
            kind, side = ("ru_move" if a["ru"] else "ua_move"), ("ru" if a["ru"] else "ua")
        else:
            continue
        place = name_of(a["lon"], a["lat"])
        if (place, kind) in seen:
            continue
        seen.add((place, kind))
        key = f"{a['lon']},{a['lat']}"
        s = sigs.get(key, {}) or {}
        det.append({"place": place, "side": side, "kind": kind, "notable": place in NOTABLE,
                    "v": abs(round(a.get("v", 0), 1)), "lon": round(a["lon"], 3), "lat": round(a["lat"], 3),
                    "contact": s.get("contact"), "pmove": s.get("pmove"), "traj": trends.get(key, [])})
    # priorité : repères connus d'abord, puis poussées (possible/probable) avant mouvements
    det.sort(key=lambda d: (0 if d["notable"] else 1, 0 if d["kind"] in ("ua_possible", "ru_probable") else 1, -d["v"]))
    top = det[:3]
    trend_txt = llm_trends(top)   # tendance LLM best-effort (dégradation propre si pas de clé / échec)
    items = [{"place": d["place"], "side": d["side"], "kind": d["kind"], "lon": d["lon"], "lat": d["lat"],
              "contact": d["contact"], "pmove": d["pmove"], **({"trend": trend_txt[d["place"]]} if trend_txt.get(d["place"]) else {})}
             for d in top]
    out = {"date": month or dt.date.today().isoformat(), "items": items}
    import hashlib, re
    bp = os.path.join(SITE, "brief.js")
    with open(bp, "w", encoding="utf-8") as f:
        f.write("window.BRIEF=" + json.dumps(out, ensure_ascii=False) + ";\n")
    # anti-cache : stamper le buster de brief.js dans index.html avec son nouveau hash
    h = hashlib.sha1(open(bp, "rb").read()).hexdigest()[:10]
    idx = os.path.join(SITE, "index.html")
    s = open(idx, encoding="utf-8").read()
    s2 = re.sub(r"brief\.js\?v=[0-9a-f]+", "brief.js?v=" + h, s)
    if s2 != s:
        open(idx, "w", encoding="utf-8").write(s2)
    print("brief.js écrit :", json.dumps(out, ensure_ascii=False), "| buster", h)


if __name__ == "__main__":
    main()
