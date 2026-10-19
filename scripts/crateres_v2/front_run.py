"""Détecteur de cratères v2 (seuils GELÉS) sur tout le faisceau du front, pipeline RÉSUMABLE.

    python -I front_run.py --root <dépôt> --work <dossier de travail> --prive <dossier privé>
                           [--start 2024-07-01] [--end AAAA-MM-JJ] [--detect-from AAAA-MM-JJ] [--radius 30]
                           [--workers 3] [--threads 3] [--shard i/n]

Faisceau : mailles de 2 km à ≤ --radius km de la ligne p = 0,5 (champ « equilibre » du site) à au moins un mois de
[start, end] ; tuiles de 20 × 20 mailles (40 km) qui en contiennent ; seules les mailles du faisceau ET de la tuile
sont analysées (pas de double compte entre tuiles voisines).

Par tuile, dates successives (comme run_zone.py --stream) : référence de chaque pixel = sa dernière observation claire
(≤ 30 jours) ; persistance = sa prochaine observation claire (≤ 25 jours, 3 dates au plus). Les dates antérieures à
--detect-from ne servent qu'à construire la référence (mise à jour mensuelle).

Reprise : <prive>/<tuile>/state.npz (référence par pixel, indice de la prochaine date, positions dans les fichiers de
sortie) est réécrit de façon atomique au plus toutes les --ckpt secondes et à la fin ; à la relance, les sorties sont
tronquées au dernier point de reprise et le calcul reprend à cette date. Une lecture qui échoue (réseau coupé, veille)
fait attendre la tuile (pause croissante, jusqu'à 15 min) puis relire la même date : aucune date n'est perdue en silence.
Débit : --workers tuiles en parallèle × --threads lectures simultanées chacune, une recherche STAC par tuile.

Sorties (aucune ne quitte la machine) :
  <prive>/<tuile>/cands.jsonl  candidats C ≥ 20 avec attributs et position pixel (PRIVÉ)
  <prive>/<tuile>/obs.csv      date, maille, km² comparés
  <prive>/<tuile>/done.json    tuile terminée
  <work>/plan.json, <work>/progress.jsonl (journal tuile × date), <work>/run.log, <work>/supervisor.pid

Seuil des candidats : 20 au lieu de 12 dans run_zone.py. Sans effet sur la décision : les deux règles (stricte et
large, rules.py) exigent C ≥ 20, les maxima locaux au-dessus de 20 sont les mêmes, et les attributs utilisés par
rules.keep ne dépendent pas des autres candidats (seuls n500 et nn en dépendent, inutilisés).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

THR = 20.0
BLK, OV = 2048, 24
MAXREF, MAXNXT = 30, 25
PREF = 5          # dates lues en avance


def log(work, msg):
    s = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} [{os.getpid()}] {msg}"
    print(s, flush=True)
    with open(os.path.join(work, "run.log"), "a") as fh:
        fh.write(s + "\n")


def journal(work, **kw):
    kw["t"] = round(time.time(), 1)
    with open(os.path.join(work, "progress.jsonl"), "a") as fh:
        fh.write(json.dumps(kw) + "\n")


# ------------------------------------------------------------------ plan
def make_plan(a):
    import numpy as np
    import s2lib
    path = os.path.join(a.work, "plan.json")
    if os.path.exists(path):
        P = json.load(open(path))
        for k in ("start", "end", "detect_from", "radius"):
            if str(P[k]) != str(getattr(a, k)):
                sys.exit(f"plan.json existant avec {k} = {P[k]} (demandé {getattr(a, k)}) : autre dossier --work ?")
        return P
    fr = s2lib.Front(a.root)
    band = fr.band(a.start[:7], a.end[:7], a.radius)
    tiles = s2lib.tiles_for(fr, band)
    for t in tiles:
        t["G"] = s2lib.tile_utm(fr, t)
        sub = band[t["j0"]:t["j0"] + s2lib.TILE, t["i0"]:t["i0"] + s2lib.TILE]
        jj, ii = np.nonzero(sub)
        t["cells"] = [int((t["j0"] + j) * fr.NX + t["i0"] + i) for j, i in zip(jj, ii)]
    tiles.sort(key=lambda t: -t["ncells"])
    P = dict(start=a.start, end=a.end, detect_from=a.detect_from, radius=a.radius, cmax=a.cmax,
             grid=dict(NX=fr.NX, NY=fr.NY, SIZE=fr.SIZE, X0=fr.X0, Y1=fr.Y1, PROJ4=fr.PROJ4),
             created=dt.datetime.now().isoformat(timespec="seconds"), tiles=tiles)
    json.dump(P, open(path + ".tmp", "w"))
    os.replace(path + ".tmp", path)
    return P


# ------------------------------------------------------------------ une tuile
def run_tile(t, P, cfg):
    import numpy as np
    from pyproj import Transformer
    from scipy import ndimage as ndi
    import detect2 as de
    import s2lib as sl

    work, td = cfg["work"], os.path.join(cfg["prive"], t["id"])
    os.makedirs(td, exist_ok=True)
    if os.path.exists(os.path.join(td, "done.json")):
        return t["id"], "déjà"
    G = t["G"]; gr = P["grid"]
    t0 = time.time()

    def wait_net(what, attempt):
        pause = min(900, 30 * 2 ** min(attempt, 5))
        log(work, f"{t['id']} : {what} ; nouvel essai dans {pause} s (essai {attempt + 1})")
        journal(work, ev="attente", tile=t["id"], what=str(what)[:160], pause=pause)
        time.sleep(pause)

    # recherche STAC (une fois par tuile, gardée sur disque)
    ip = os.path.join(td, "items.json")
    attempt = 0
    while not os.path.exists(ip):
        try:
            its = sl.search(G["bbox"], P["start"], P["end"], P["cmax"])
            json.dump(its, open(ip + ".tmp", "w")); os.replace(ip + ".tmp", ip)
        except sl.NetErr as e:
            wait_net(e, attempt); attempt += 1
    by = sl.by_date(json.load(open(ip)))
    dates = sorted(by)
    wp = os.path.join(td, "worldcover.npy")
    attempt = 0
    while not os.path.exists(wp):
        try:
            np.save(wp + ".tmp.npy", sl.worldcover(G)); os.replace(wp + ".tmp.npy", wp)
        except sl.NetErr as e:
            wait_net(e, attempt); attempt += 1
    wc = np.load(wp)
    H, W = G["H"], G["W"]
    land = ~(ndi.binary_dilation(wc == 50, iterations=3) | np.isin(wc, (80, 90, 0)))   # bâti élargi de 30 m, eau, humide
    tr = Transformer.from_crs(f"EPSG:{G['epsg']}", gr["PROJ4"], always_xy=True)
    yy, xx = np.mgrid[0:H:10, 0:W:10]
    lx, ly = tr.transform(G["x0"] + (xx + 5) * 10.0, G["y1"] - (yy + 5) * 10.0)
    cells = (np.floor((gr["Y1"] - ly) / gr["SIZE"]) * gr["NX"] + np.floor((lx - gr["X0"]) / gr["SIZE"])).astype(np.int64)
    inb = np.isin(cells, np.array(t["cells"], np.int64))
    land &= np.repeat(np.repeat(inb, 10, 0), 10, 1)[:H, :W]
    del wc, yy, xx, lx, ly
    NXc, X0, Y1, SIZE = gr["NX"], gr["X0"], gr["Y1"], gr["SIZE"]
    BANDS = sl.BANDS

    # point de reprise
    sp = os.path.join(td, "state.npz")
    cp_, op_ = os.path.join(td, "cands.jsonl"), os.path.join(td, "obs.csv")
    REF = None; RDATE = np.full((H, W), -10 ** 6, np.int32); k0 = 0; offc = offo = 0
    if os.path.exists(sp):
        z = np.load(sp)
        k0, offc, offo = int(z["k_next"]), int(z["off_c"]), int(z["off_o"])
        RDATE = z["rdate"]
        if bool(z["has_ref"]):
            REF = {b: z[b] for b in BANDS}; REF["valid"] = z["valid"]
        del z
        log(work, f"{t['id']} : reprise à la date {k0 + 1}/{len(dates)}")
    for p_, off in ((cp_, offc), (op_, offo)):
        with open(p_, "ab") as fh:
            fh.truncate(off)
    fc, fo = open(cp_, "ab"), open(op_, "ab")
    if offo == 0:
        fo.write(b"date,cell,km2\n")
    journal(work, ev="tuile_debut", tile=t["id"], n=len(dates), k0=k0)

    import socket
    from concurrent.futures import TimeoutError as FutTimeout
    socket.setdefaulttimeout(120)        # pas de connexion pendante à l'infini après une veille (requests / jetons SAS)
    pool = {"ex": ThreadPoolExecutor(cfg["threads"])}
    futs = {}

    def submit(q, lenient=False):
        if q not in futs and q < len(dates):
            futs[q] = pool["ex"].submit(sl.fetch_one, G, by[dates[q]], lenient)

    def get(q):
        attempt = 0
        while True:
            if attempt >= 3 and sl.net_ok():
                # le service répond mais cette date échoue toujours : fichier abîmé ou absent ; on fait comme
                # crlib (lecture ratée = pixels manquants) pour ne pas bloquer la tuile
                log(work, f"{t['id']} : {dates[q]} lue en mode tolérant (le réseau répond, la date échoue encore)")
                journal(work, ev="tolerant", tile=t["id"], d=dates[q])
                submit(q, lenient=True)
            for qq in range(q, min(len(dates), q + PREF)):
                submit(qq)
            try:
                r = futs[q].result(timeout=cfg.get("date_timeout", 2400))
                futs.pop(q)
                break
            except (sl.NetErr, FutTimeout) as e:
                if isinstance(e, FutTimeout):     # lecture bloquée : on abandonne ces fils et on repart à neuf
                    e = sl.NetErr(f"lecture bloquée plus de {cfg.get('date_timeout', 2400) / 60:.0f} min")
                    pool["ex"].shutdown(wait=False, cancel_futures=True)
                    pool["ex"] = ThreadPoolExecutor(cfg["threads"])
                for qq in list(futs):       # on relira tout après la coupure
                    futs[qq].cancel()
                futs.clear()
                wait_net(e, attempt); attempt += 1
        if r is None:      # date trop nuageuse : masque vide
            r = {b: np.zeros((H, W), np.int16) for b in BANDS}; r["valid"] = np.zeros((H, W), bool)
        return r

    def checkpoint(k_next):
        fc.flush(); fo.flush()
        tmp = sp + ".tmp.npz"
        kw = {b: REF[b] for b in BANDS} if REF is not None else {}
        if REF is not None:
            kw["valid"] = REF["valid"]
        np.savez(tmp, k_next=k_next, off_c=fc.tell(), off_o=fo.tell(), has_ref=REF is not None, rdate=RDATE, **kw)
        os.replace(tmp, sp)

    cache = {}
    last_ck = time.time()
    df = P["detect_from"]
    try:
        for k in range(k0, len(dates)):
            d = dates[k]; ts = time.time()
            for q in list(cache):
                if q < k:
                    cache.pop(q)
            for q in range(k, min(len(dates), k + 4)):
                if q not in cache:
                    cache[q] = get(q)
            for q in range(k + 4, min(len(dates), k + PREF)):
                submit(q)
            cur = cache[k]
            kind, ncand, km2 = "vide", 0, 0.0
            if cur["valid"].mean() >= 0.02:
                od = sl.ordd(d)
                if REF is None:
                    REF = {b: cur[b].copy() for b in BANDS}; REF["valid"] = cur["valid"].copy()
                    RDATE[cur["valid"]] = od
                    kind = "init"
                else:
                    if d >= df:
                        kind = "det"
                        refv = REF["valid"] & (od - RDATE <= MAXREF)
                        NX = {b: np.zeros((H, W), np.int16) for b in BANDS}; NX["valid"] = np.zeros((H, W), bool)
                        for q in range(k + 1, min(len(dates), k + 4)):
                            if sl.ordd(dates[q]) - od > MAXNXT:
                                break
                            zq = cache[q]; new = zq["valid"] & ~NX["valid"]
                            for b in BANDS:
                                NX[b][new] = zq[b][new]
                            NX["valid"] |= new
                        okall = np.zeros((H, W), bool)
                        buf = []
                        for r0 in range(0, H, BLK):
                            for c0 in range(0, W, BLK):
                                R0, C0 = max(0, r0 - OV), max(0, c0 - OV)
                                R1, C1 = min(H, r0 + BLK + OV), min(W, c0 + BLK + OV)
                                s = (slice(R0, R1), slice(C0, C1))
                                if not land[r0:min(H, r0 + BLK), c0:min(W, c0 + BLK)].any():
                                    continue
                                ref = {b: REF[b][s] for b in BANDS}; ref["valid"] = refv[s]; ref["rdate"] = RDATE[s]
                                cu = {b: cur[b][s] for b in BANDS}; cu["valid"] = cur["valid"][s]
                                nx = {b: NX[b][s] for b in BANDS}; nx["valid"] = NX["valid"][s]
                                if (ref["valid"] & cu["valid"]).mean() < 0.05:
                                    continue
                                cands, okl, sds = de.candidates(ref, cu, nx, land[s], thr=THR)
                                okall[r0:min(H, r0 + BLK), c0:min(W, c0 + BLK)] = okl[r0 - R0:r0 - R0 + min(BLK, H - r0), c0 - C0:c0 - C0 + min(BLK, W - c0)]
                                for o in cands:
                                    rr, cc = o["r"] + R0, o["c"] + C0
                                    if not (r0 <= rr < r0 + BLK and c0 <= cc < c0 + BLK):
                                        continue
                                    o["r"], o["c"] = int(rr), int(cc)
                                    o["d1"] = d
                                    o["d0"] = dt.date.fromordinal(int(RDATE[rr, cc])).isoformat()
                                    nd = [dates[q] for q in range(k + 1, min(len(dates), k + 4)) if cache[q]["valid"][rr, cc] and sl.ordd(dates[q]) - od <= MAXNXT]
                                    o["d2"] = nd[0] if nd else None
                                    x, y = tr.transform(G["x0"] + (cc + 0.5) * 10, G["y1"] - (rr + 0.5) * 10)
                                    o["cell"] = int((Y1 - y) // SIZE) * NXc + int((x - X0) // SIZE)
                                    buf.append(json.dumps(o) + "\n")
                        ncand = len(buf)
                        fc.write("".join(buf).encode())
                        ok10 = okall[::10, ::10]
                        cc_, nn_ = np.unique(cells[ok10], return_counts=True)
                        fo.write("".join(f"{d},{int(c)},{round(n * 0.01, 2)}\n" for c, n in zip(cc_, nn_)).encode())
                        km2 = float(okall.sum()) / 1e4
                        del NX, okall, refv
                    else:
                        kind = "ref"
                    v = cur["valid"]
                    for b in BANDS:
                        REF[b][v] = cur[b][v]
                    REF["valid"] |= v
                    RDATE[v] = od
            journal(work, ev="date", tile=t["id"], k=k, n=len(dates), d=d, kind=kind, cand=ncand, km2=round(km2, 1),
                    s=round(time.time() - ts, 1))
            if time.time() - last_ck >= cfg["ckpt"] or k == len(dates) - 1:
                checkpoint(k + 1); last_ck = time.time()
    finally:
        pool["ex"].shutdown(wait=False, cancel_futures=True)
        fc.close(); fo.close()
    json.dump(dict(tile=t["id"], dates=len(dates), first=dates[0] if dates else None, last=dates[-1] if dates else None,
                   start=P["start"], end=P["end"], detect_from=df, s=round(time.time() - t0), finished=dt.datetime.now().isoformat(timespec="seconds")),
              open(os.path.join(td, "done.json"), "w"))
    if os.path.exists(sp):
        os.remove(sp)        # point de reprise devenu inutile (≈ 200 Mo)
    journal(work, ev="tuile_fin", tile=t["id"], n=len(dates), s=round(time.time() - t0))
    return t["id"], f"{len(dates)} dates, {time.time() - t0:.0f} s"


def _worker(args):
    t, P, cfg = args
    try:
        return run_tile(t, P, cfg)
    except Exception:  # noqa: BLE001
        log(cfg["work"], f"{t['id']} : ERREUR\n{traceback.format_exc()}")
        journal(cfg["work"], ev="erreur", tile=t["id"])
        return t["id"], "erreur"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--prive", required=True)
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--detect-from", dest="detect_from", default=None)
    ap.add_argument("--radius", type=float, default=30.0)
    ap.add_argument("--cmax", type=float, default=50.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--ckpt", type=float, default=600.0)
    ap.add_argument("--date-timeout", dest="date_timeout", type=float, default=2400.0,
                    help="au-delà, une lecture de date est réputée bloquée (veille, réseau) et relancée")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args()
    a.detect_from = a.detect_from or a.start
    os.makedirs(a.work, exist_ok=True); os.makedirs(a.prive, exist_ok=True)
    P = make_plan(a)
    si, sn = map(int, a.shard.split("/"))
    tiles = [t for q, t in enumerate(P["tiles"]) if q % sn == si]
    log(a.work, f"plan : {len(P['tiles'])} tuiles ({sum(t['ncells'] for t in P['tiles']) * 4:,} km² de faisceau), lot {a.shard} : {len(tiles)} tuiles ; "
                f"{P['start']} → {P['end']}, détection à partir de {P['detect_from']}, ±{P['radius']:.0f} km")
    if a.plan_only:
        return
    open(os.path.join(a.work, "supervisor.pid"), "w").write(str(os.getpid()))
    cfg = dict(work=a.work, prive=a.prive, threads=a.threads, ckpt=a.ckpt, date_timeout=a.date_timeout)
    fails = {}
    for rnd in range(20):
        todo = [t for t in tiles if not os.path.exists(os.path.join(a.prive, t["id"], "done.json")) and fails.get(t["id"], 0) < 3]
        if not todo:
            break
        try:
            with ProcessPoolExecutor(a.workers) as pool:
                fs = [pool.submit(_worker, (t, P, cfg)) for t in todo]
                for f in as_completed(fs):
                    tid, msg = f.result()
                    if msg == "erreur":
                        fails[tid] = fails.get(tid, 0) + 1
                    log(a.work, f"{tid} : {msg}")
        except BrokenProcessPool:
            log(a.work, "un processus de calcul est mort (mémoire ?) : relance du lot dans 60 s")
            time.sleep(60)
    left = [t["id"] for t in tiles if not os.path.exists(os.path.join(a.prive, t["id"], "done.json"))]
    log(a.work, "TERMINÉ" if not left else f"ARRÊT avec {len(left)} tuiles non terminées : {', '.join(left)}")
    journal(a.work, ev="fin", left=len(left))


if __name__ == "__main__":
    main()
