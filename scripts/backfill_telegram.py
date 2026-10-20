"""Collecte historique COMPLÈTE (24/02/2022 → aujourd'hui) des 15 canaux Telegram de la couche « signaux Telegram », en
local et en arrière-plan, reprise possible à tout moment (20/10/2026).

    nohup python3 -u scripts/backfill_telegram.py <dossier interne> [--graine <signals3/internal>] [--phase 0,1,2] \
          > <dossier interne>/backfill.log 2>&1 &

Aperçu public t.me/s/<canal>?before=<id> (sans compte, sans clé ; robots.txt de t.me : 404, aucune règle), une requête
à la fois, pause TG_PAUSE (2 s par défaut, jamais moins de 2 s), User-Agent explicite de tglib. Un refus 403 arrête
TOUT (pas de contournement). 429 : on attend (Retry-After, plafonné à 10 min) puis la pause est doublée.

Grille : une page de l'aperçu couvre ~20 identifiants (mesuré le 20/10/2026 : 20 à 23 identifiants, 9 à 20 messages
selon les albums) ; on lit les pages before = top_graine + 1 - 20 k, ancrées sur le « top » de la collecte historique
de signals3 (fichiers <canal>.done), qui avait lu une page sur deux (k pair) pour les canaux au pas 40.
  phase 0 : pages récentes (au-dessus du 25/09/2026, la graine s'arrête au 30/09/2026), tous les canaux ;
  phase 1 : pages manquantes de la graine (k impair) des 5 canaux au pas 40 : historique complet en messages ;
  phase 2 : toutes les autres pages (relecture complète : géocodage v2, trous éventuels de la graine, textes à jour).
Dans chaque phase, les canaux avancent à tour de rôle, du plus récent au plus ancien ; un canal s'arrête quand une page
est entièrement antérieure au 24/02/2022 ou vide.

Sorties (dossier interne, JAMAIS versionné ni publié) :
  <canal>.jsonl        messages de combat ou de présence (tglib.CONTACT / PRESENCE) AVEC texte : id, d (jour de
                       publication, UTC), camp, t (1 500 caractères au plus), b (page) ;
  <canal>.pages.jsonl  journal des pages : b, ok, n (messages), lo / hi (identifiants), dlo / dhi (jours), j (messages
                       par jour de publication : sert à la couverture), sans texte ;
  backfill.status.json avancement (pages faites / prévues par phase, débit, fin estimée).
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tglib as T  # noqa: E402

START = "2022-02-24"
RECENT_FLOOR = "2026-09-25"      # la graine s'arrête au 30/09/2026
STEP = 20
PAUSE = max(2.0, float(os.environ.get("TG_PAUSE", "2.0")))
STRIDE = {n: s for n, _, s in T.CHANNELS}
CAMP = {n: c for n, c, _ in T.CHANNELS}


class Refus(Exception):
    pass


STATS = Counter()


def get(name, before):
    """Une page de l'aperçu public ; None après 4 essais ; Refus sur 403 (on abandonne tout)."""
    global PAUSE
    url = f"https://t.me/s/{name}?before={before}"
    for attempt in range(4):
        STATS["requetes"] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": T.UA, "Accept-Language": "ru,uk;q=0.8,en;q=0.5"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise Refus(f"403 sur {name}")
            if e.code == 404:
                return None
            if e.code == 429:
                STATS["429"] += 1
                try:
                    ra = int(e.headers.get("Retry-After") or 0)
                except ValueError:
                    ra = 0
                PAUSE = min(PAUSE * 2, 30.0)
                print(f"  429 ({name}) : attente {min(ra or 120, 600)} s, pause portée à {PAUSE:.0f} s", flush=True)
                time.sleep(min(ra or 120, 600))
                continue
            STATS["erreurs"] += 1
            time.sleep(15 * (attempt + 1))
        except Exception:
            STATS["erreurs"] += 1
            time.sleep(10 * (attempt + 1))
    return None


def load_pages(out, name):
    done, fin = {}, None
    p = os.path.join(out, f"{name}.pages.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("fin"):
                fin = max(fin or 0, r["b"])
            elif r.get("ok") or r["b"] not in done:
                done[r["b"]] = r
    return done, fin


def fetch_page(out, name, b):
    page = get(name, b)
    time.sleep(PAUSE)
    rec = {"b": b, "ok": 0, "n": 0, "t": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    if page is not None:
        msgs = [m for m in T.parse(page, name) if m[0] < b]
        rec["ok"] = 1
        rec["n"] = len(msgs)
        if msgs:
            rec.update(lo=min(m[0] for m in msgs), hi=max(m[0] for m in msgs), dlo=min(m[1] for m in msgs),
                       dhi=max(m[1] for m in msgs), j=dict(Counter(m[1] for m in msgs)))
            with open(os.path.join(out, f"{name}.jsonl"), "a", encoding="utf-8") as fh:
                for mid, day, txt in msgs:
                    if day >= START and (T.CONTACT.search(txt) or T.PRESENCE.search(txt)):
                        fh.write(json.dumps({"id": mid, "d": day, "camp": CAMP[name], "t": txt[:1500], "b": b},
                                            ensure_ascii=False) + "\n")
    with open(os.path.join(out, f"{name}.pages.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


# ---------- bilans officiels (phase O) ----------
# Les bruts de fetch_claims.py (cache CI) n'ont les bilans quotidiens qu'à partir de 2024 (mots-clés « напрямку »,
# « группировк » ajoutés tard) : on relit les deux canaux officiels de la fin de l'historique disponible jusqu'au
# 24/02/2022, page par page (before = plus petit identifiant de la page précédente), mêmes mots-clés que fetch_claims.
OFFICIELS = {"GeneralStaffZSU": "2024-06-01", "mod_russia": "2024-02-01"}


def crawl_officiels(out):
    import fetch_claims as FC
    od = os.path.join(out, "claims")
    os.makedirs(od, exist_ok=True)
    for ch, until in OFFICIELS.items():
        keys = FC.CHANNELS[ch]
        path = os.path.join(od, f"{ch}.jsonl")
        spath = os.path.join(od, f"{ch}.state.json")
        st = json.load(open(spath)) if os.path.exists(spath) else {}
        if st.get("fin"):
            print(f"  {ch} : déjà complet ({st})", flush=True)
            continue
        before = st.get("before")
        if before is None:
            # recherche par dichotomie du premier identifiant publié après `until`
            page = get(ch, 10 ** 7)
            time.sleep(PAUSE)
            ms = FC.parse(page or "", ch)
            lo, hi = 1, max([m["id"] for m in ms] + [1])
            while hi - lo > 20:
                mid = (lo + hi) // 2
                ms = FC.parse(get(ch, mid) or "", ch)
                time.sleep(PAUSE)
                if ms and max(m["date"] for m in ms) >= until:
                    hi = mid
                else:
                    lo = mid
            before = hi + 20
        pages, fails = st.get("pages", 0), 0
        while fails < 10:
            page = get(ch, before)
            time.sleep(PAUSE)
            ms = [m for m in FC.parse(page or "", ch) if m["id"] < before]
            if page is None:
                print(f"  {ch} : page {before} illisible, nouvel essai dans 60 s", flush=True)
                fails += 1
                time.sleep(60)
                continue
            pages += 1
            with open(path, "a", encoding="utf-8") as fh:
                for m in ms:
                    if m["date"] >= START and any(k in m["text"].lower() for k in keys):
                        fh.write(json.dumps(m, ensure_ascii=False) + "\n")
            fin = not ms or min(m["date"] for m in ms) < START
            before = min([m["id"] for m in ms] + [before])
            json.dump({"before": before, "pages": pages, "fin": fin, "jour": (min(m["date"] for m in ms) if ms else "-")},
                      open(spath, "w"))
            if pages % 200 == 0 or fin:
                print(f"  {ch} : {pages} pages, jusqu'au {(min(m['date'] for m in ms) if ms else '-')}", flush=True)
            if fin:
                break


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d  # noqa: E731
    graine = opt("--graine", os.path.join(os.path.dirname(T.ROOT), "ukraine-recherche", "signals3", "internal"))
    phases = [x if x == "O" else int(x) for x in opt("--phase", "O,0,1,2").split(",")]
    args = [a for a in args if a not in (graine, opt("--phase", ""))]
    out = args[0]
    os.makedirs(out, exist_ok=True)
    top0 = {}
    for name in STRIDE:
        p = os.path.join(graine, f"{name}.done")
        top0[name] = json.load(open(p))["top"] if os.path.exists(p) else None
    # top actuel (une requête par canal) : ancrage de la phase 0
    topnow = {}
    for name in STRIDE:
        page = None
        try:
            req = urllib.request.Request(f"https://t.me/s/{name}", headers={"User-Agent": T.UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                page = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"403 sur {name} : arrêt", flush=True)
                return
        except Exception:
            pass
        time.sleep(PAUSE)
        ms = T.parse(page, name) if page else []
        topnow[name] = max([m[0] for m in ms] + [top0[name] or 0])
        if top0[name] is None:
            top0[name] = topnow[name]
    print(f"départ {dt.datetime.now().isoformat(timespec='seconds')} ; pause {PAUSE} s ; phases {phases}", flush=True)
    state = {n: load_pages(out, n) for n in STRIDE}
    t0, n0 = time.time(), 0

    last = [time.time()]

    def iterate(name, phase):
        a = top0[name] + 1
        if phase == 0:
            k = -((topnow[name] - top0[name]) // STEP + 1)
            while True:
                yield k, a - STEP * k
                k += 1
        elif phase == 1:
            if STRIDE[name] != 40:
                return
            k = 1
            while a - STEP * k > 0:
                yield k, a - STEP * k
                k += 2
        else:
            k = 0
            while a - STEP * k > 0:
                yield k, a - STEP * k
                k += 1

    if "O" in phases:
        try:
            crawl_officiels(out)
        except Refus as e:
            print(f"REFUS : {e} — arrêt complet de la collecte (pas de contournement)", flush=True)
            return
    for phase in [x for x in phases if x != "O"]:
        gens = {n: iterate(n, phase) for n in STRIDE}
        active = list(STRIDE)
        print(f"=== phase {phase} : {len(active)} canaux ===", flush=True)
        while active:
            for name in list(active):
                done, fin = state[name]
                rec = None
                for k, b in gens[name]:
                    if fin is not None and b <= fin:
                        rec = "fin"
                        break
                    if b in done and done[b].get("ok"):
                        r = done[b]
                        if phase == 0 and r.get("n") and r.get("dhi", "9") < RECENT_FLOOR:
                            rec = "fin"
                            break
                        if phase > 0 and (not r.get("n") or r.get("dhi", "9") < START):
                            rec = "fin"
                            break
                        continue
                    if b > topnow[name] + STEP:
                        continue
                    try:
                        rec = fetch_page(out, name, b)
                    except Refus as e:
                        print(f"REFUS : {e} — arrêt complet de la collecte (pas de contournement)", flush=True)
                        return
                    done[b] = rec
                    n0 += 1
                    if rec["ok"] and (not rec["n"] or rec.get("dhi", "9") < START):
                        if phase > 0:
                            with open(os.path.join(out, f"{name}.pages.jsonl"), "a", encoding="utf-8") as fh:
                                fh.write(json.dumps({"b": b, "fin": 1}) + "\n")
                            state[name] = (done, b)
                            rec = "fin"
                    elif phase == 0 and rec["ok"] and rec.get("dhi", "9") < RECENT_FLOOR:
                        rec = "fin"
                    break
                else:
                    rec = "fin"
                if rec == "fin":
                    active.remove(name)
            if n0 and time.time() - last[0] > 300:
                last[0] = time.time()
                el = time.time() - t0
                st = {"phase": phase, "pages_lues": n0, "s_par_page": round(el / n0, 2), "actifs": active,
                      "stats": dict(STATS), "maj": dt.datetime.now().isoformat(timespec="seconds"),
                      "position": {n: min([b for b, r in state[n][0].items() if r.get("ok")] or [0]) for n in STRIDE}}
                json.dump(st, open(os.path.join(out, "backfill.status.json"), "w"), indent=1)
                print(f"  {n0} pages, {el / n0:.2f} s/page, {len(active)} canaux actifs, {dict(STATS)}", flush=True)
        # nouvel essai des pages en échec de la phase
        for name in STRIDE:
            done, fin = state[name]
            for b in sorted([b for b, r in done.items() if not r.get("ok")], reverse=True):
                try:
                    done[b] = fetch_page(out, name, b)
                except Refus as e:
                    print(f"REFUS : {e} — arrêt", flush=True)
                    return
        print(f"=== phase {phase} terminée ({dt.datetime.now().isoformat(timespec='seconds')}) ===", flush=True)
    print(f"FIN : {n0} pages en {time.time() - t0:.0f} s, {dict(STATS)}", flush=True)


if __name__ == "__main__":
    main()
