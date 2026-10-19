"""AMPLEUR de l'avancée à venir, par tronçon de 24 km : de combien de km la ligne bougera d'ici t+1 et t+2 mois, signé
(> 0 : avancée russe, < 0 : avancée ukrainienne), avec intervalles 80 % et 95 % calibrés (conformal local), et km²
agrégés. Étude : ukraine-recherche/analyses/ampleur/RAPPORT.md.

    python3 scripts/build_advance.py [--root .]         ->  site/front_advance.js

Entrées (produites par la CI avant ce script ; dégradation propre si une entrée manque) :
  site/front.js            grille, maillage (runs), mois, P(mouvement) publié par tronçon (months[].pm) ;
  site/front_equilibre.js  champ des CARTES par mois (position de la ligne, zone grise) — indispensable ;
  site/front_nowcast.js    champ publié (décalage imposé par le nowcast) — facultatif ;
  data/telegram_troncons.csv  Telegram par tronçon et par mois (agrégats) — facultatif ; ignoré si le dernier mois manque.

Position Y(t, k) = surface où p_cartes > 0,5 dans la tuile k (12 × 12 mailles de 2 km) / 24 km (km de profondeur russe),
si ≥ 20 mailles de la tuile sont à ≤ 20 km de la ligne. Cible Δ_h = Y(t+h) − Y(t).
Modèle « à deux étages » (walk-forward strict : au mois t, apprentissage sur les q ≤ t − h des 24 mois précédents) :
  1. P(|Δ_h| ≥ 0,5 km) : logistique ridge (mouvement récent 1, 3, 6 mois et voisins, P(mouvement) publié, décalage du
     nowcast, zone grise, volume Telegram) ;
  2. P(sens russe | bouge) : logistique ridge (mouvements récents signés, voisins, décalage du nowcast, place restante,
     orientation Telegram) ;
  3. ampleur | bouge : régression de log|Δ| + résidus empiriques, bornée par la place restante dans la tuile ;
  le reste de la masse : Δ des tronçons immobiles du passé. Distribution prédictive = mélange (39 quantiles).
Intervalles : conformal local (CQR) par tronçon (voisins à ≤ 120 km, poids 0,6·e^(−d/40), oubli 6 mois, correction d'échantillon fini) ET
par classe de risque P(mouvement) (< 0,15 ; 0,15-0,35 ; ≥ 0,35), α adaptatif par tronçon et par classe. km² agrégés : somme des espérances × 24,
intervalle = quantiles des résidus agrégés passés.
PUBLICATION CONDITIONNELLE : chaque horizon porte un drapeau `show.troncons`, vrai seulement si, en walk-forward
(2023+), le CRPS bat la persistance et la couverture est tenue par classe de risque (≥ nominal − 3 points) et par
secteur (≥ nominal − 5 points, secteurs d'au moins 100 tronçons-mois) ; `show.km2` si l'intervalle agrégé couvre
(≥ 70 % / 88 %) ET si l'erreur bat la persistance (0 km²) et la tendance (gain des h derniers mois reconduit). Sinon le fichier est écrit mais rien ne doit s'afficher.
Agrégats seulement (tuiles de 24 km, instantanés mensuels à J-7) ; aucun texte Telegram.
"""
import base64
import csv
import datetime as dt
import json
import math
import os
import sys

import numpy as np
from scipy import ndimage

TILE = 12
QL = np.arange(1, 40) / 40.0
WIN = 24
EVAL_FROM = "2023-01"
PM_BINS = (0.15, 0.35)
HL, OM, RR, CROSS, GA = 6.0, 0.6, 40.0, 0.0, 0.05
THR = 0.5
SECTORS = [("Koursk-Soumy", 35.0, 51.2), ("Kharkiv nord", 36.9, 50.2), ("Koupiansk-Oskil", 37.8, 49.7), ("Lyman", 38.1, 49.1),
           ("Siversk-Bakhmout", 38.1, 48.65), ("Toretsk-Kostiantynivka", 37.75, 48.4), ("Pokrovsk-Avdiïvka", 37.3, 48.2),
           ("Kourakhove-Vouhledar", 37.15, 47.85), ("Velyka Novosilka", 36.6, 47.7), ("Zaporijjia", 35.8, 47.45),
           ("Dnipro-Kherson", 33.5, 46.8), ("Louhansk-Donetsk (arrière)", 39.2, 48.3)]


# ---------- lecture ----------
def load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def load_seq(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


def decode_seq(enc, n):
    out, prev = [], np.zeros(n, np.uint8)
    for e in enc:
        if not e:
            out.append(None)
            continue
        b, q, o, i = base64.b64decode(e), np.zeros(n, np.uint8), 0, 0
        while i < len(b):
            v = b[i]; i += 1
            k = sh = 0
            while True:
                c = b[i]; i += 1
                k |= (c & 127) << sh; sh += 7
                if not c & 128:
                    break
            q[o:o + k] = prev[o:o + k] if v == 255 else v
            o += k
        out.append(q)
        prev = q
    return out


def inv_laea(G, x, y):
    R, l0, p0 = G["R"], math.radians(G["lon0"]), math.radians(G["lat0"])
    rho = math.hypot(x, y)
    if rho < 1e-9:
        return G["lon0"], G["lat0"]
    c = 2 * math.asin(rho / (2 * R))
    lat = math.asin(math.cos(c) * math.sin(p0) + y * math.sin(c) * math.cos(p0) / rho)
    lon = l0 + math.atan2(x * math.sin(c), rho * math.cos(p0) * math.cos(c) - y * math.sin(p0) * math.sin(c))
    return math.degrees(lon), math.degrees(lat)


# ---------- outils statistiques ----------
def logi(X, y, lam=3.0, it=25):
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        w += np.linalg.solve((X * (p * (1 - p))[:, None]).T @ X + lam * np.diag(np.r_[0, np.ones(X.shape[1] - 1)]) + 1e-8 * np.eye(X.shape[1]),
                             X.T @ (y - p) - lam * np.r_[0, w[1:]])
    return w


def plogi(w, X):
    return 1 / (1 + np.exp(-np.clip(w[0] + X @ w[1:], -30, 30)))


def ridge(X, y, lam):
    Xc = np.c_[np.ones(len(X)), X]
    R = lam * np.eye(Xc.shape[1]); R[0, 0] = 0
    return np.linalg.solve(Xc.T @ Xc + R, Xc.T @ y)


def wquant(x, w, qs):
    o = np.argsort(x, kind="stable")
    x, c = x[o], np.cumsum(w[o])
    c = c / c[-1]
    return x[np.clip(np.searchsorted(c, np.asarray(qs) - 1e-12), 0, len(x) - 1)]


def crps_q(q, y):
    return 2 * float(np.mean((y - q) * (QL - (y < q))))


def main():
    root = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    site = os.path.join(root, "site")
    FR = load_js(os.path.join(site, "front.js"))
    G = FR["grid"]
    NX, NY, KM = G["nx"], G["ny"], G["size"] / 1000.0
    place = np.zeros(NX * NY, np.int32)
    b, o = base64.b64decode(FR["runs"]), 0
    for i in range(0, len(b), 3):
        v, n = b[i] | b[i + 1] << 8, b[i + 2]
        place[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
        o += n
    U = np.flatnonzero(place != -1)
    nU = len(U)
    jj, ii = U // NX, U % NX
    frames = FR["months"]
    mlab = [m["date"][:7] if m else None for m in frames]
    EQ = decode_seq(load_seq(os.path.join(site, "front_equilibre.js")), nU)
    p_now = os.path.join(site, "front_nowcast.js")
    NOW = decode_seq(load_seq(p_now), nU) if os.path.exists(p_now) else [None] * len(frames)
    months = [m for s, m in enumerate(mlab) if m and EQ[s] is not None]
    sidx = {m: s for s, m in enumerate(mlab) if m}
    mi = {m: i for i, m in enumerate(months)}
    NM = len(months)
    tile = (jj // TILE) * 1000 + ii // TILE
    tiles, tinv = np.unique(tile, return_inverse=True)
    nT = len(tiles)
    a2, wkm = KM * KM, TILE * KM

    def bc(w):
        return np.bincount(tinv, weights=np.asarray(w, np.float64), minlength=nT)

    def grid(v, fill):
        g = np.full((NY, NX), fill, np.float32)
        g[jj, ii] = v
        return g

    Y, F = {}, {}
    for t, m in enumerate(months):
        peq = EQ[sidx[m]].astype(np.float32) / 63.0
        g = grid(peq, np.nan)
        ru, ua = g > 0.5, g < 0.5
        nb = np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb |= np.roll(ua, sh, axis=(0, 1))
        edge = ru & nb
        if not edge.any():
            continue
        d = ndimage.distance_transform_edt(~edge) * KM
        sd = np.where(ru, d, -d)[jj, ii]
        near = np.abs(sd) <= 20
        ok = bc(near) >= 20
        own = bc(peq > 0.5)
        gray = bc(near & (peq >= 0.05) & (peq <= 0.95)) * a2 / wkm
        nq = NOW[sidx[m]] if sidx[m] < len(NOW) else None
        ynow = (bc(nq.astype(np.float32) / 63.0 > 0.5) - own) * a2 / wkm if nq is not None else np.zeros(nT)
        for j in np.flatnonzero(ok):
            k = int(tiles[j])
            Y[(t, k)] = float(own[j]) * a2 / wkm
            F[(t, k)] = {"gray": float(min(gray[j], 24)), "y_now": float(np.clip(ynow[j], -5, 5))}
    used = ["mouvement récent (cartes)", "voisins", "zone grise"] + (["décalage du nowcast"] if os.path.exists(p_now) else [])
    missing = [] if os.path.exists(p_now) else ["front_nowcast.js"]
    # P(mouvement) publié (front.js)
    PM = {}
    for m in frames:
        if m and m.get("pm") and m["date"][:7] in mi:
            for c, r, p in m["pm"]:
                PM[(mi[m["date"][:7]], (r // TILE) * 1000 + c // TILE)] = p / 100.0
    use_pm = any(key[0] == NM - 1 for key in PM)
    if use_pm:
        used.append("P(mouvement) publié")
    else:
        missing.append("P(mouvement) du dernier mois")
    # Telegram (CI)
    TGT, TGO = {}, {}
    p_tg = os.path.join(root, "data", "telegram_troncons.csv")
    use_tg = False
    if os.path.exists(p_tg):
        acc = {}
        for r in csv.DictReader(open(p_tg, encoding="utf-8")):
            if r.get("pas") != "mois" or r["debut"][:7] not in mi:
                continue
            key = (mi[r["debut"][:7]], (int(r["j0"]) // TILE) * 1000 + int(r["i0"]) // TILE)
            a = acc.setdefault(key, [0.0, 0.0, 0.0])
            f = {c: float(r.get(c) or 0) for c in ("n_combat", "n_att_ua", "n_att_ru", "n_presence_ua", "n_presence_ru")}
            a[0] += f["n_combat"]
            if r["camp_source"] == "ru":
                a[1] += f["n_att_ru"]; a[2] += f["n_att_ua"] + f["n_presence_ua"]
            elif r["camp_source"] == "ua":
                a[1] += f["n_att_ru"] + f["n_presence_ru"]; a[2] += f["n_att_ua"]
        tg_months = {key[0] for key in acc}
        use_tg = (NM - 1) in tg_months
        for key, (c, r_, u_) in acc.items():
            TGT[key] = math.log1p(c + r_ + u_)
            TGO[key] = math.log1p(r_) - math.log1p(u_)
    if use_tg:
        used.append("Telegram (volume et orientation, agrégats)")
    else:
        missing.append("Telegram (data/telegram_troncons.csv absent ou sans le dernier mois)")

    def dY(t, k, n):
        a, b_ = Y.get((t, k)), Y.get((t - n, k))
        return 0.0 if a is None or b_ is None else a - b_

    def nbrs(k):
        return [k + a * 1000 + b_ for a in (-1, 0, 1) for b_ in (-1, 0, 1) if (a or b_)]

    FA, FS = {}, {}
    for (t, k), f in F.items():
        d1, d3, d6 = dY(t, k, 1), dY(t, k, 3), dY(t, k, 6)
        nb = [dY(t, q, 3) for q in nbrs(k) if (t, q) in Y]
        a = [abs(d1), abs(d3), abs(d6), float(np.mean(np.abs(nb))) if nb else 0.0, abs(f["y_now"]), f["gray"]]
        s = [d1, d3, d6, float(np.mean(nb)) if nb else 0.0, f["y_now"], Y[(t, k)] / 24.0]
        if use_pm:
            a.append(PM.get((t, k), 0.0))
        if use_tg:
            a.append(TGT.get((t, k), 0.0)); s.append(TGO.get((t, k), 0.0))
        FA[(t, k)], FS[(t, k)] = np.array(a), np.array(s)

    def target(t, k, h):
        a, b_ = Y.get((t + h, k)), Y.get((t, k))
        return None if a is None or b_ is None else a - b_

    # voisinage des tuiles (conformal local)
    TPOS = {int(k): i for i, k in enumerate(tiles)}
    cen = np.array([[(k // 1000 + 0.5) * wkm, (k % 1000 + 0.5) * wkm] for k in tiles])
    D = np.hypot(cen[:, None, 0] - cen[None, :, 0], cen[:, None, 1] - cen[None, :, 1])
    WT = np.where(D == 0, 1.0, np.where(D <= 3 * RR, OM * np.exp(-D / RR), 0.0))
    LL = {int(k): inv_laea(G, G["x0"] + ((k % 1000) + 0.5) * TILE * G["size"], G["y1"] - ((k // 1000) + 0.5) * TILE * G["size"]) for k in tiles}
    SOF = {k: min(SECTORS, key=lambda s: (s[1] - LL[k][0]) ** 2 * 0.44 + (s[2] - LL[k][1]) ** 2)[0] for k in LL}

    def hurdle(h):
        """Distribution prédictive (39 quantiles), espérance et P(mouvement) par (t, k), walk-forward."""
        Q, MEAN, PMV = {}, {}, {}
        keys_by_t = {}
        for key in FA:
            keys_by_t.setdefault(key[0], []).append(key)
        for t in range(NM):
            tr = [key for q in range(max(0, t - WIN), t - h + 1) for key in keys_by_t.get(q, []) if target(*key, h) is not None]
            te = keys_by_t.get(t, [])
            if len(tr) < 300 or not te:
                continue
            yv = np.array([target(*x, h) for x in tr])
            mv = np.abs(yv) >= THR
            if mv.sum() < 30 or (yv[mv] > 0).sum() < 10 or (yv[mv] < 0).sum() < 5:
                continue
            XA = np.array([FA[x] for x in tr]); XS = np.array([FS[x] for x in tr])
            mua, sda = XA.mean(0), XA.std(0) + 1e-9
            mus, sds = XS.mean(0), XS.std(0) + 1e-9
            za, zs = (XA - mua) / sda, (XS - mus) / sds
            w1 = logi(za, mv.astype(float))
            w2 = logi(zs[mv], (yv[mv] > 0).astype(float), lam=10.0)
            Xm = np.c_[za[mv], (yv[mv] > 0).astype(float)]
            b3 = ridge(Xm, np.log(np.abs(yv[mv])), 20.0)
            res = np.log(np.abs(yv[mv])) - (b3[0] + Xm @ b3[1:])
            v0, n0 = np.unique(np.round(yv[~mv] * 6) / 6, return_counts=True)
            p0 = n0 / n0.sum()
            for key in te:
                a = (FA[key] - mua) / sda
                s = (FS[key] - mus) / sds
                p = float(plogi(w1, a[None])[0])
                q = float(plogi(w2, s[None])[0])
                y0 = Y[key]
                mr = np.clip(np.exp(b3[0] + np.r_[a, 1.0] @ b3[1:] + res), THR, max(24 - y0, THR))
                mu_ = np.clip(np.exp(b3[0] + np.r_[a, 0.0] @ b3[1:] + res), THR, max(y0, THR))
                atoms = np.r_[v0, mr, -mu_]
                wts = np.r_[(1 - p) * p0, np.full(len(mr), p * q / len(mr)), np.full(len(mu_), p * (1 - q) / len(mu_))]
                o_ = np.argsort(atoms, kind="stable")
                c_ = np.cumsum(wts[o_]); c_ /= c_[-1]
                Q[key] = atoms[o_][np.clip(np.searchsorted(c_, QL - 1e-12), 0, len(atoms) - 1)]
                MEAN[key] = float((atoms * wts).sum() / wts.sum())
                PMV[key] = p
        return Q, MEAN, PMV

    WT0 = np.where(D == 0, 1.0, np.where(D <= 75, 0.3 * np.exp(-D / 25), 0.0))

    def persistence(h, WT=WT):
        """Référence : 0 km, distribution = Δ passés locaux (deux voisinages essayés, on garde le meilleur)."""
        Q = {}
        pool = [(key, target(*key, h)) for key in FA if target(*key, h) is not None]
        pk = np.array([TPOS[k] for (_, k), _ in pool]); pq = np.array([t for (t, _), _ in pool]); pv = np.array([v for _, v in pool])
        for t in range(NM):
            sel = pq <= t - h
            if sel.sum() < 300:
                continue
            for key in [x for x in FA if x[0] == t]:
                w = WT[TPOS[key[1]]][pk[sel]] * 0.5 ** ((t - pq[sel]) / HL)
                if w.sum() < 3:
                    w = 0.5 ** ((t - pq[sel]) / HL)
                Q[key] = wquant(pv[sel], w, QL)
        return Q

    def cqr(Q, h, alpha, cls):
        il, ih = (3, 35) if alpha == 0.2 else (0, 38)
        sc = {key: max(q[il] - target(*key, h), target(*key, h) - q[ih]) for key, q in Q.items() if target(*key, h) is not None}
        sk = np.array([TPOS[k] for (_, k) in sc], int); sq = np.array([t for (t, _) in sc]); sv = np.array(list(sc.values()))
        scl = np.array([cls.get(x, -1) for x in sc])
        al, out = {}, {}
        for t in sorted({t for t, _ in Q}):
            for (q, k) in [x for x in sc if x[0] == t - h]:
                if (q, k) in out:
                    lo, hi = out[(q, k)]
                    miss = float(not (lo - 1e-9 <= target(q, k, h) <= hi + 1e-9))
                    ak = (k, cls.get((q, k), -1))
                    al[ak] = float(np.clip(al.get(ak, alpha) + GA * (alpha - miss), alpha / 5, min(0.5, 3 * alpha)))
            sel = sq <= t - h
            if sel.sum() < 200:
                continue
            ks_, qs_, vs_, cl_ = sk[sel], sq[sel], sv[sel], scl[sel]
            rec = 0.5 ** ((t - qs_) / HL)
            for key in [x for x in Q if x[0] == t]:
                w = WT[TPOS[key[1]]][ks_] * rec
                if w.sum() < 3:
                    w = rec
                c = cls.get(key, -1)
                same = cl_ == c
                if (w * same).sum() < 3:
                    w = rec
                w = w * np.where(same, 1.0, CROSS)
                neff = w.sum() ** 2 / max((w * w).sum(), 1e-12)   # effectif équivalent : correction d'échantillon fini
                qq = float(wquant(vs_, w, [min((1 - al.get((key[1], c), alpha)) * (1 + 1 / max(neff, 1.0)), 0.999)])[0])
                lo, hi = Q[key][il] - qq, Q[key][ih] + qq
                if lo > hi:
                    lo = hi = (lo + hi) / 2
                out[key] = (lo, hi)
        return out

    out_h, valid, show = {}, {}, {}
    for h in (1, 2):
        Q, MEAN, PMV = hurdle(h)
        cls = {key: int(np.searchsorted(PM_BINS, p)) for key, p in PMV.items()}
        Q0a, Q0b = persistence(h), persistence(h, WT0)
        C = {lab: cqr(Q, h, al, cls) for lab, al in (("80", 0.2), ("95", 0.05))}
        ev = [key for key in Q if key in Q0a and key in Q0b and months[key[0]] >= EVAL_FROM and target(*key, h) is not None and key in C["80"] and key in C["95"]]
        yv = np.array([target(*k_, h) for k_ in ev])
        mvm = np.abs(yv) >= THR
        crps = np.array([crps_q(Q[k_], y) for k_, y in zip(ev, yv)])
        crps0 = min((np.array([crps_q(Q0_[k_], y) for k_, y in zip(ev, yv)]) for Q0_ in (Q0a, Q0b)), key=lambda a: a.mean())
        V = {"n": len(ev), "crps": round(float(crps.mean()), 4), "crps_persistance": round(float(crps0.mean()), 4),
             "crps_bougent": round(float(crps[mvm].mean()), 3), "crps_bougent_persistance": round(float(crps0[mvm].mean()), 3),
             "mae": round(float(np.mean(np.abs([Q[k_][19] for k_ in ev] - yv))), 4), "mae_bougent": round(float(np.mean(np.abs(np.array([Q[k_][19] for k_ in ev]) - yv)[mvm])), 3)}
        ok = V["crps"] < V["crps_persistance"]
        for lab, nom in (("80", 0.80), ("95", 0.95)):
            cov = np.array([C[lab][k_][0] - 1e-9 <= y <= C[lab][k_][1] + 1e-9 for k_, y in zip(ev, yv)])
            cl = np.array([cls.get(k_, -1) for k_ in ev])
            V["couverture" + lab] = round(float(cov.mean()), 3)
            V["couverture" + lab + "_bougent"] = round(float(cov[mvm].mean()), 3)
            V["largeur" + lab] = round(float(np.mean([C[lab][k_][1] - C[lab][k_][0] for k_ in ev])), 2)
            V["classes" + lab] = {}
            for c in range(len(PM_BINS) + 1):
                s_ = cl == c
                if s_.sum():
                    V["classes" + lab][str(c)] = [round(float(cov[s_].mean()), 3), int(s_.sum()), round(float(mvm[s_].mean()), 3)]
                    if s_.sum() >= 100 and cov[s_].mean() < nom - 0.03:
                        ok = False
            V["secteurs" + lab] = {}
            for sname, _, _ in SECTORS:
                s_ = np.array([SOF[k_[1]] == sname for k_ in ev])
                if s_.sum():
                    V["secteurs" + lab][sname] = [round(float(cov[s_].mean()), 3), int(s_.sum())]
                    if s_.sum() >= 100 and cov[s_].mean() < nom - 0.05:
                        ok = False
        # km² agrégés (gain russe net, somme des tronçons × 24 km²)
        ts = sorted({k_[0] for k_ in Q})
        agg = {}
        for t in ts:
            ks_ = [k_ for k_ in Q if k_[0] == t]
            pred = sum(MEAN[k_] for k_ in ks_) * wkm
            real = sum(target(*k_, h) for k_ in ks_) * wkm if all(target(*k_, h) is not None for k_ in ks_) else None
            agg[t] = (pred, real)
        res = {t: agg[t][1] - agg[t][0] for t in ts if agg[t][1] is not None}
        AGI, ac8, ac9, an = {}, 0, 0, 0
        for t in ts:
            past = [q for q in res if q <= t - h]
            if len(past) < 8:
                continue
            wts = 0.5 ** ((t - np.array(past)) / 12.0)
            lo8, hi8, lo9, hi9 = wquant(np.array([res[q] for q in past]), wts, [0.1, 0.9, 0.025, 0.975])
            AGI[t] = [round(agg[t][0]), round(agg[t][0] + lo8), round(agg[t][0] + hi8), round(agg[t][0] + lo9), round(agg[t][0] + hi9)]
            if t in res and months[t] >= EVAL_FROM:
                ac8 += lo8 <= res[t] <= hi8; ac9 += lo9 <= res[t] <= hi9; an += 1
        # référence « tendance » : le gain net des h derniers mois, reconduit (mêmes tronçons)
        trend = {}
        for t in res:
            ks_ = [k_ for k_ in Q if k_[0] == t]
            if all((t - h, k_[1]) in Y for k_ in ks_):
                trend[t] = sum(Y[k_] - Y[(t - h, k_[1])] for k_ in ks_) * wkm
        V["km2_tendance_mae"] = round(float(np.mean([abs(agg[t][1] - trend[t]) for t in trend if months[t] >= EVAL_FROM]))) if trend else None
        V["km2"] = {"n": an, "couverture80": round(ac8 / max(an, 1), 3), "couverture95": round(ac9 / max(an, 1), 3),
                    "mae": round(float(np.mean([abs(res[t]) for t in res if months[t] >= EVAL_FROM]))) if res else None,
                    "mae_persistance": round(float(np.mean([abs(agg[t][1]) for t in res if months[t] >= EVAL_FROM]))) if res else None}
        V["km2"]["mae_tendance"] = V.pop("km2_tendance_mae")
        km2_ok = (an >= 12 and V["km2"]["couverture80"] >= 0.70 and V["km2"]["couverture95"] >= 0.88
                  and V["km2"]["mae"] is not None and V["km2"]["mae"] < min(x for x in (V["km2"]["mae_persistance"], V["km2"]["mae_tendance"]) if x is not None))
        valid[h], show[h] = V, {"troncons": bool(ok), "km2": bool(km2_ok)}
        tl = NM - 1
        rows = []
        for k_ in sorted([x for x in Q if x[0] == tl]):
            if k_ not in C["80"] or k_ not in C["95"]:
                continue
            k = k_[1]
            rows.append([int((k % 1000) * TILE + TILE // 2), int((k // 1000) * TILE + TILE // 2), round(float(Q[k_][19]), 2),
                         round(C["80"][k_][0], 2), round(C["80"][k_][1], 2), round(C["95"][k_][0], 2), round(C["95"][k_][1], 2),
                         round(PMV[k_] * 100), round(MEAN[k_], 2)])
        hist = [[months[t], months[t + h] if t + h < NM else None, *AGI[t], round(agg[t][1]) if agg[t][1] is not None else None] for t in ts if t in AGI]
        out_h[str(h)] = {"base": months[tl], "cible": (dt.date(int(months[tl][:4]), int(months[tl][5:]), 1) + dt.timedelta(days=31 * h)).strftime("%Y-%m"),
                         "show": show[h], "tiles": rows, "km2": AGI.get(tl), "km2_hist": hist[-24:], "validation": V}
        print(f"ampleur t+{h} : CRPS {V['crps']:.4f} (persistance {V['crps_persistance']:.4f}) ; qui bougent {V['crps_bougent']:.3f} "
              f"({V['crps_bougent_persistance']:.3f}) ; couverture 80 % {V['couverture80']:.1%} (qui bougent {V['couverture80_bougent']:.1%}), "
              f"95 % {V['couverture95']:.1%} ({V['couverture95_bougent']:.1%}) ; par classe 80 % {V['classes80']} 95 % {V['classes95']} ; "
              f"km² : MAE {V['km2']['mae']} (persistance {V['km2']['mae_persistance']}, tendance {V['km2']['mae_tendance']}), couverture {V['km2']['couverture80']:.0%}/{V['km2']['couverture95']:.0%} ; "
              f"affichage tronçons {'OUI' if ok else 'NON'}, km² {'OUI' if km2_ok else 'NON'}", flush=True)
        bad = {lab: {s: v for s, v in V["secteurs" + lab].items() if v[1] >= 100 and v[0] < (0.75 if lab == "80" else 0.90)} for lab in ("80", "95")}
        if any(bad.values()):
            print(f"  secteurs sous le seuil : {bad}", flush=True)
    doc = {
        "generated": dt.date.today().isoformat(), "tile_km": 24, "unit": "km de profondeur (surface / 24 km) ; km² = km × 24",
        "sign": "> 0 : avancée russe (vers le côté ukrainien) ; < 0 : avancée ukrainienne",
        "columns": ["col", "row", "median_km", "lo80", "hi80", "lo95", "hi95", "pmove_pct", "mean_km"],
        "km2_columns": ["moyenne", "lo80", "hi80", "lo95", "hi95"],
        "km2_hist_columns": ["origine", "cible", "moyenne", "lo80", "hi80", "lo95", "hi95", "realise"],
        "inputs": used, "missing": missing, "horizons": out_h,
        "method": "deux étages : P(mouvement ≥ 0,5 km) × P(sens | mouvement) × ampleur | mouvement ; conformal local par tronçon et par classe de risque ; walk-forward 2023+",
    }
    path = os.path.join(site, "front_advance.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.FRONT_ADVANCE=" + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"écrit {path} ({os.path.getsize(path) // 1024} Ko) ; entrées : {', '.join(used)}" + (f" ; manquantes : {', '.join(missing)}" if missing else ""), flush=True)


if __name__ == "__main__":
    main()
