"""État du pipeline front_run.py : tuiles terminées, tuile × dates faites, vitesse récente, ETA, processus vivant.

    python -I front_status.py --work <dossier de travail> --prive <dossier privé> [--hours 1]
"""
import argparse
import datetime as dt
import json
import os
import time
from collections import defaultdict


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--prive", required=True)
    ap.add_argument("--hours", type=float, default=1.0, help="fenêtre de mesure de la vitesse")
    a = ap.parse_args()
    P = json.load(open(os.path.join(a.work, "plan.json")))
    tiles = [t["id"] for t in P["tiles"]]
    done = {t for t in tiles if os.path.exists(os.path.join(a.prive, t, "done.json"))}
    ndates, kmax, kind = {}, defaultdict(lambda: -1), defaultdict(int)
    events = []
    waits = []
    nerr = 0
    pj = os.path.join(a.work, "progress.jsonl")
    if os.path.exists(pj):
        for line in open(pj):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ev") == "date":
                ndates[e["tile"]] = e["n"]
                if e["k"] > kmax[e["tile"]]:
                    kmax[e["tile"]] = e["k"]
                    kind[e["kind"]] += 1
                events.append((e["t"], e["tile"], e["k"], e.get("s", 0)))
            elif e.get("ev") in ("tuile_debut", "tuile_fin"):
                ndates[e["tile"]] = e["n"]
            elif e.get("ev") == "attente":
                waits.append(e)
            elif e.get("ev") == "erreur":
                nerr += 1
    for t in tiles:
        ip = os.path.join(a.prive, t, "items.json")
        if t not in ndates and os.path.exists(ip):
            try:
                ndates[t] = len({it["properties"]["datetime"][:10] for it in json.load(open(ip))})
            except (ValueError, KeyError):
                pass
    known = [ndates[t] for t in tiles if t in ndates]
    avg = sum(known) / len(known) if known else 0
    total = sum(ndates.get(t, avg) for t in tiles)
    fait = sum(ndates.get(t, 0) if t in done else kmax[t] + 1 for t in tiles)
    now = time.time()
    win = a.hours * 3600
    rec = {(tl, k) for tt, tl, k, _ in events if tt >= now - win}
    first = min((e[0] for e in events), default=now)
    span = min(win, now - first)
    rate = len(rec) / span * 3600 if span > 60 else 0.0
    pidf = os.path.join(a.work, "supervisor.pid")
    pid = int(open(pidf).read()) if os.path.exists(pidf) else None
    last = max((e[0] for e in events), default=None)
    print(f"plan : {len(tiles)} tuiles, {P['start']} → {P['end']} (détection à partir de {P['detect_from']}), ±{P['radius']:.0f} km")
    print(f"processus : PID {pid} {'VIVANT' if pid and alive(pid) else 'ARRÊTÉ'} ; dernière date traitée il y a "
          f"{(now - last) / 60:.1f} min" if last else f"processus : PID {pid} {'VIVANT' if pid and alive(pid) else 'ARRÊTÉ'} ; aucune date encore")
    print(f"tuiles terminées : {len(done)} / {len(tiles)} ; en cours : {sum(1 for t in tiles if t not in done and kmax[t] >= 0)}")
    print(f"tuiles × dates : {fait:.0f} / {total:.0f} ({100 * fait / max(total, 1):.1f} %) ; "
          f"{len([t for t in tiles if t in ndates])} tuiles déjà cherchées (moyenne {avg:.0f} dates)")
    print(f"  dont : {dict(kind)}")
    if rate > 0:
        eta = (total - fait) / rate
        fin = dt.datetime.now() + dt.timedelta(hours=eta)
        print(f"vitesse ({span / 3600:.1f} h récentes) : {rate:.0f} tuiles × dates / h ; reste {total - fait:.0f} → ETA {eta:.1f} h (vers le {fin:%d/%m %H:%M})")
    if waits:
        w = waits[-1]
        print(f"attentes réseau : {len(waits)} (dernière il y a {(now - w['t']) / 60:.0f} min : {w['what'][:100]})")
    if nerr:
        print(f"erreurs de tuile : {nerr} (voir {os.path.join(a.work, 'run.log')})")
    if len(done) == len(tiles):
        print("TOUT EST TRAITÉ : lancer front_finalize.py")


if __name__ == "__main__":
    main()
