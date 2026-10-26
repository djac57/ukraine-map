#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Brief quotidien « ce qui se passe » : extrait NOS détections (flèches poussée possible/probable) du mois courant via
le diagnostic __uafArrows de la carte (Playwright), les nomme par un repère reconnaissable, et écrit site/brief.js.
DÉTERMINISTE (aucun LLM) → fiable, gratuit, reproductible. i18n : brief.js porte {place, side, kind} ; app.js localise."""
import json, os, sys, threading, http.server, socketserver, functools, math, datetime as dt

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
            b.close()
    print(f"[debug] fi={fi} month={month} arrows={len(arrows)} possible={sum(1 for a in arrows if a.get('possible'))} "
          f"probable={sum(1 for a in arrows if a.get('probable'))} uncertain={sum(1 for a in arrows if a.get('uncertain'))} "
          f"sample_keys={list(arrows[0].keys()) if arrows else '—'}", file=sys.stderr)
    return arrows, month


def main():
    arrows, month = extract()
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
        det.append({"place": place, "side": side, "kind": kind, "notable": place in NOTABLE, "v": abs(round(a.get("v", 0), 1))})
    # priorité : repères connus d'abord, puis poussées (possible/probable) avant mouvements
    det.sort(key=lambda d: (0 if d["notable"] else 1, 0 if d["kind"] in ("ua_possible", "ru_probable") else 1, -d["v"]))
    items = [{"place": d["place"], "side": d["side"], "kind": d["kind"]} for d in det[:3]]
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
