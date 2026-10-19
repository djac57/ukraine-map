"""Collecte incrémentale des 15 canaux Telegram de la couche « signaux Telegram » : aperçu public t.me/s/<canal> (sans
compte, sans clé), une requête à la fois avec une pause (TG_PAUSE, 0,8 s par défaut), User-Agent explicite.

    python3 scripts/fetch_telegram.py <brut> [--days 45] [--budget 50] [--full]

On ne relit que les `--days` derniers jours (45 par défaut), avec le même pas d'échantillonnage que la collecte historique
(tglib.CHANNELS) ; quand la couverture précédente d'un canal est complète, seulement depuis sa fin moins 3 jours (sauf
--full). Budget de temps (--budget minutes) : les canaux les moins à jour passent d'abord, les autres au passage suivant.
Mesure du 19/10/2026 (local, premier passage complet) : t.me ralentit l'aperçu à ~3 à 4 s par page. Seuls les messages contenant un mot de combat ou de présence sont gardés, AVEC leur texte, dans
<brut>/telegram/<canal>.jsonl : ce dossier vit dans le cache Actions (raw/, jamais versionné) ou, en local, dans un dossier
interne de recherche ; il n'est jamais publié. Les messages de plus de KEEP_DAYS jours sont oubliés.
<brut>/telegram/<canal>.state.json : période couverte sans trou (from, to), messages lus, pages lues.
La construction (build_telegram_layer.py) en tire des comptes par tronçon de 24 km, sans aucun texte.
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tglib as T  # noqa: E402

KEEP_DAYS = 120
MAX_PAGES = 600          # garde-fou par canal
OVERLAP_DAYS = 3
FULL = "--full" in sys.argv


def crawl(out, name, camp, stride, start, today):
    t_start = time.time()
    path = os.path.join(out, f"{name}.jsonl")
    spath = os.path.join(out, f"{name}.state.json")
    old = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                p = json.loads(line)
                old[p["id"]] = p
            except ValueError:
                continue
    state = json.load(open(spath, encoding="utf-8")) if os.path.exists(spath) else {}
    # reprise : si la couverture précédente est complète et récente, on ne relit que depuis sa fin moins OVERLAP_DAYS
    # (messages édités ou supprimés), sans jamais remonter avant la fenêtre de 45 jours
    if not FULL and state.get("complet") and state.get("to", "") >= start:
        start = max(start, (dt.date.fromisoformat(state["to"]) - dt.timedelta(days=OVERLAP_DAYS)).isoformat())
    first = T.get(name)
    time.sleep(T.PAUSE)
    msgs = T.parse(first, name) if first else []
    if not msgs:
        print(f"  {name:16s} inaccessible, état précédent conservé", flush=True)
        return None
    top = max(m[0] for m in msgs)
    seen, kept, pages, oldest = set(), 0, 1, today
    new = {}

    def take(lst):
        nonlocal kept, oldest
        for mid, day, txt in lst:
            if mid in seen:
                continue
            seen.add(mid)
            oldest = min(oldest, day)
            if day < start or day > today:
                continue
            if T.CONTACT.search(txt) or T.PRESENCE.search(txt):
                new[mid] = {"id": mid, "d": day, "camp": camp, "t": txt[:1500]}
                kept += 1
    take(msgs)
    b = top + 1 - stride
    while b > 0 and oldest >= start and pages < MAX_PAGES:
        page = T.get(name, b)
        time.sleep(T.PAUSE)
        pages += 1
        if page:
            take(T.parse(page, name))
        b -= stride
    complete = oldest < start or b <= 0
    # fusion : les messages relus remplacent les anciens (texte édité, message supprimé) dans la fenêtre relue
    lo_keep = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    cov_from = start if complete else oldest
    merged = {k: v for k, v in old.items() if lo_keep <= v["d"] < cov_from}
    merged.update(new)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for k in sorted(merged):
            fh.write(json.dumps(merged[k], ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    # couverture sans trou : on prolonge l'ancienne si elle rejoint la nouvelle fenêtre
    if state.get("from") and state.get("to", "") >= cov_from:
        f = max(state["from"], lo_keep)
    else:
        f = cov_from
    state = {"from": f, "to": today, "camp": camp, "stride": stride, "top": top, "pages": pages, "messages": len(seen),
             "gardes": kept, "complet": complete}
    json.dump(state, open(spath, "w", encoding="utf-8"))
    print(f"  {name:16s} {camp:5s} pas {stride} : {pages:4d} pages, {len(seen):5d} messages lus, {kept:4d} gardés "
          f"(combat ou présence), couverture {state['from']} → {state['to']}{'' if complete else ' (INCOMPLÈTE)'}, "
          f"{time.time() - t_start:.0f} s", flush=True)
    return pages


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = lambda k, d: int(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d  # noqa: E731
    days, budget = opt("--days", 45), opt("--budget", 50)
    args = [a for a in args if a not in {str(days), str(budget)}]
    out = os.path.join(args[0] if args else "raw", "telegram")
    os.makedirs(out, exist_ok=True)
    today = dt.datetime.now(dt.timezone.utc).date()
    start = (today - dt.timedelta(days=days)).isoformat()
    only = os.environ.get("TG_ONLY")
    t0 = time.time()
    tot = 0
    print(f"Telegram : relecture du {start} au {today.isoformat()} ({len(T.CHANNELS)} canaux, pause {T.PAUSE} s)", flush=True)
    def last_to(name):
        p = os.path.join(out, f"{name}.state.json")
        return json.load(open(p, encoding="utf-8")).get("to", "") if os.path.exists(p) else ""
    # les canaux les moins à jour d'abord : si le budget de temps est atteint, les suivants passent au prochain jour
    for name, camp, stride in sorted(T.CHANNELS, key=lambda x: last_to(x[0])):
        if only and name not in only.split(","):
            continue
        if time.time() - t0 > budget * 60:
            print(f"  budget de {budget} min atteint : {name} et suivants au prochain passage", flush=True)
            break
        try:
            tot += crawl(out, name, camp, stride, start, today.isoformat()) or 0
        except Exception as e:     # un canal en échec ne bloque pas les autres
            print(f"  {name:16s} ERREUR {type(e).__name__}: {e}", flush=True)
    print(f"Telegram : {tot} pages en {time.time() - t0:.0f} s ({T.STATS['requests']} requêtes, {T.STATS['429']} refus 429, "
          f"{T.STATS['errors']} erreurs, {T.STATS['wait_s']:.0f} s d'attente)", flush=True)


if __name__ == "__main__":
    main()
