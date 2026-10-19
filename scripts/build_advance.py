"""AMPLEUR de l'avancée à venir, par tronçon de 24 km : de combien de km la ligne bougera d'ici t+1 et t+2 mois, signé
(> 0 : avancée russe, < 0 : avancée ukrainienne), avec intervalles 80 % et 95 % calibrés (conformal local), et km²
agrégés. Étude : ukraine-recherche/analyses/ampleur/RAPPORT.md (v1, puis « Ampleur v2 », même rapport).

    python3 scripts/build_advance.py [--root .]         ->  site/front_advance.js

Entrées (produites par la CI avant ce script ; dégradation propre si une entrée manque) :
  site/front.js            grille, maillage (runs), instantanés MENSUELS (les pas récents de 3 jours, kind = "d", sont
                           ignorés : la vérité d'apprentissage reste mensuelle), P(mouvement) publié (months[].pm) ;
  site/front_equilibre.js  champ des CARTES par mois (position de la ligne, zone grise) — indispensable ;
  site/front_nowcast.js    champ publié (décalage imposé par le nowcast) — facultatif ;
  site/front_sens.js       modèle de sens à deux étages (build_direction.py), P(sens RU | bouge) par tronçon et par
                           décade ; on lit la décade de fin de mois — facultatif (v2 seulement) ;
  data/telegram_troncons.csv  Telegram par tronçon et par mois (agrégats) — facultatif ; ignoré si le dernier mois manque.

Position Y(t, k) = surface où p_cartes > 0,5 dans la tuile k (12 × 12 mailles de 2 km) / 24 km (km de profondeur russe),
si ≥ 20 mailles de la tuile sont à ≤ 20 km de la ligne. Cible Δ_h = Y(t+h) − Y(t).
v1 « à deux étages » (walk-forward strict : au mois t, apprentissage sur les q ≤ t − h des 24 mois précédents) :
  1. P(|Δ_h| ≥ 0,5 km) : logistique ridge (mouvement récent 1, 3, 6 mois et voisins, P(mouvement) publié, décalage du
     nowcast, zone grise, volume Telegram) ;
  2. P(sens russe | bouge) : logistique ridge (mouvements récents signés, voisins, décalage du nowcast, place restante,
     orientation Telegram) ;
  3. ampleur | bouge : régression de log|Δ| + résidus empiriques, bornée par la place restante dans la tuile ;
  le reste de la masse : Δ des tronçons immobiles du passé. Distribution prédictive = mélange (39 quantiles).
v2 = v1 + deux termes (paramètres choisis sur les origines 2023-2024, GELÉS ensuite ; évaluation hors échantillon 2025+) :
  a. RÉGIME ÉTABLI : un tronçon est « en régime » s'il a bougé dans le même sens au moins L mois de suite (≥ 0,15 km
     par mois) à une vitesse moyenne v ≥ VMIN km/mois. Sa distribution prédictive est le mélange (1 − λ) · v1 + λ · R,
     R = h · v · r, où r parcourt les rapports Δ_h / (h · v) des tronçons en régime du passé (fenêtre de 24 mois) :
     « la vitesse soutenue se poursuit, ralentit ou s'arrête » appris sur le passé. λ est réappris à chaque mois sur
     les tronçons en régime passés dont l'issue est connue (CRPS minimal sur la grille 0 … 1), donc 0 tant que le
     régime n'a jamais payé ;
  b. SENS : P(sens RU | bouge) de front_sens.js (AUC ≈ 0,81 au pas décadaire) entre comme variable (logit, plus
     indicatrice de présence) dans le deuxième étage ; c'est elle qui fixe l'asymétrie de la fourchette.
Intervalles : conformal local (CQR) par tronçon (voisins à ≤ 120 km, poids 0,6·e^(−d/40), oubli 6 mois, correction d'échantillon fini) ET
par classe de risque P(mouvement) (< 0,15 ; 0,15-0,35 ; ≥ 0,35), α adaptatif par tronçon et par classe. km² agrégés : somme des espérances × 24,
intervalle = quantiles des résidus agrégés passés.
CHOIX DU MODÈLE (à chaque build, par horizon) : v2 seulement si son CRPS bat celui de v1 sur la période gelée (2025+)
ET si sa couverture tient (même règle que l'affichage) ; sinon v1.
PUBLICATION CONDITIONNELLE : chaque horizon porte un drapeau `show.troncons`, vrai seulement si, en walk-forward
(2023+), le CRPS bat la persistance et la couverture est tenue par classe de risque (≥ nominal − 3 points) et par
secteur (≥ nominal − 5 points, secteurs d'au moins 100 tronçons-mois) ; `show.km2` si l'intervalle agrégé couvre
(≥ 70 % / 88 %) ET si l'erreur bat la persistance (0 km²) et la tendance (gain des h derniers mois reconduit). Sinon le fichier est écrit mais rien ne doit s'afficher.
Pas de 10 jours : non (la vérité d'apprentissage publiée est mensuelle ; les pas de 3 jours ne couvrent que 90 jours).
Agrégats seulement (tuiles de 24 km, instantanés mensuels à J-7) ; aucun texte Telegram.
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "2")

import base64  # noqa: E402
import csv  # noqa: E402
import datetime as dt  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402
from scipy import ndimage  # noqa: E402

TILE = 12
QL = np.arange(1, 40) / 40.0
WIN = 24
EVAL_FROM = "2023-01"
FROZEN_FROM = "2025-01"      # paramètres v2 choisis sur 2023-01 → 2024-12, évaluation hors échantillon à partir d'ici
PM_BINS = (0.15, 0.35)
HL, OM, RR, CROSS, GA = 6.0, 0.6, 40.0, 0.0, 0.05
THR = 0.5
EPS_REG = 0.15               # mouvement mensuel minimal (km) pour compter un mois « dans le même sens »
LAMS = (0.0, 0.25, 0.5, 0.75, 1.0)
V1 = {"sens": "own", "reg_L": None}
# v2 GELÉ : choisi sur les origines 2023-2024 (ukraine-recherche/analyses/ampleur/v2_etude.py → v2_etude.txt)
# (19/10/2026, 3e passage : régime « 2 mois de suite », v ≥ 0,25 km/mois, rapports par sens, oubli 6 mois ; P(sens) de
# front_sens.js non retenue sur 2023-2024). Hors échantillon 2025+ : jeu égal avec v1, légèrement moins bien → v1 publiée.
V2 = {h: {"sens": "own", "reg_v": "run", "reg_L": 2, "reg_vmin": 0.25, "reg_pm": 0.0, "reg_dir": True, "reg_hl": 6, "reg_feat": False}
      for h in (1, 2)}
SECTORS = [("Koursk-Soumy", 35.0, 51.2), ("Kharkiv nord", 36.9, 50.2), ("Koupiansk-Oskil", 37.8, 49.7), ("Lyman", 38.1, 49.1),
           ("Siversk-Bakhmout", 38.1, 48.65), ("Toretsk-Kostiantynivka", 37.75, 48.4), ("Pokrovsk-Avdiïvka", 37.3, 48.2),
           ("Kourakhove-Vouhledar", 37.15, 47.85), ("Velyka Novosilka", 36.6, 47.7), ("Zaporijjia", 35.8, 47.45),
           ("Dnipro-Kherson", 33.5, 46.8), ("Louhansk-Donetsk (arrière)", 39.2, 48.3)]
# boîtes (lon0, lon1, lat0, lat1) et mois de début / fin, reprises de ukraine-recherche/analyses/ampleur/analyse.py
EVENTS = [
    ("Avdiïvka, oct. 2023 - fév. 2024", (37.4, 38.0, 47.95, 48.35), "2023-10", "2024-02"),
    ("Vovtchansk, mai 2024", (36.2, 37.5, 50.0, 50.5), "2024-05", "2024-06"),
    ("Pokrovsk, été 2024", (36.9, 37.6, 47.95, 48.5), "2024-07", "2024-09"),
    ("Koursk, août 2024", (34.4, 36.0, 50.9, 51.8), "2024-08", "2024-09"),
    ("Vouhledar, sept.-oct. 2024", (36.9, 37.5, 47.6, 47.95), "2024-09", "2024-10"),
    ("Kourakhove, oct.-déc. 2024", (36.9, 37.4, 47.9, 48.1), "2024-10", "2024-12"),
    ("Pokrovsk, 2025", (36.9, 37.6, 47.95, 48.5), "2025-01", "2025-12"),
    ("Vivaldi / nord de Lyman, 2026", (37.5, 38.6, 48.8, 49.5), "2026-05", "2026-09"),
]


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


def mixq(qa, qb, lam):
    """Quantiles du mélange (1 − λ) · A + λ · B, A et B donnés par leurs 39 quantiles (atomes équiprobables)."""
    if lam <= 0:
        return qa
    if lam >= 1:
        return qb
    x = np.r_[qa, qb]
    w = np.r_[np.full(len(qa), (1 - lam) / len(qa)), np.full(len(qb), lam / len(qb))]
    return wquant(x, w, QL)


def month_end(m):
    y, mo = int(m[:4]), int(m[5:])
    return (dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()


# ---------- données ----------
def prepare(root):
    S = SimpleNamespace(root=root)
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
    nmon = FR.get("nMonths", len(frames))
    # instantanés MENSUELS seulement (les pas récents de 3 jours, après nMonths, ont des dates du même mois)
    mlab = [m["date"][:7] if (m and i < nmon and m.get("kind", "m") == "m") else None for i, m in enumerate(frames)]
    EQ = decode_seq(load_seq(os.path.join(site, "front_equilibre.js")), nU)
    p_now = os.path.join(site, "front_nowcast.js")
    NOW = decode_seq(load_seq(p_now), nU) if os.path.exists(p_now) else [None] * len(frames)
    months = [m for s, m in enumerate(mlab) if m and s < len(EQ) and EQ[s] is not None]
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
    # P(mouvement) publié (front.js, instantanés mensuels)
    PM = {}
    for i, m in enumerate(frames):
        if m and i < nmon and m.get("pm") and m["date"][:7] in mi:
            for c, r, p in m["pm"]:
                PM[(mi[m["date"][:7]], (r // TILE) * 1000 + c // TILE)] = p / 100.0
    use_pm = any(key[0] == NM - 1 for key in PM)
    if use_pm:
        used.append("P(mouvement) publié")
    else:
        missing.append("P(mouvement) du dernier mois")
    # P(sens RU | bouge) du modèle de sens (front_sens.js, décade de fin de mois)
    PS = {}
    p_sens = os.path.join(site, "front_sens.js")
    if os.path.exists(p_sens):
        try:
            SE = load_js(p_sens)
            for d, rows in zip(SE.get("steps", []), SE.get("rows", [])):
                if d[:7] in mi and d == month_end(d[:7]):
                    for r in rows:
                        if r[3] is not None:
                            PS[(mi[d[:7]], (r[1] // TILE) * 1000 + r[0] // TILE)] = r[3] / 100.0
        except (OSError, ValueError, KeyError, IndexError):
            PS = {}
    use_ps = any(key[0] == NM - 1 for key in PS)
    if use_ps:
        used.append("P(sens RU | bouge) du modèle de sens (front_sens.js)")
    else:
        PS = {}
        missing.append("front_sens.js (ou sa décade de fin du dernier mois)")
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

    FA, FS, REG = {}, {}, {}
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
        # régime établi : mois consécutifs (les plus récents) dans le même sens, vitesse moyenne sur ces mois
        mm = []
        for i in range(1, 7):
            a_, b_ = Y.get((t - i + 1, k)), Y.get((t - i, k))
            if a_ is None or b_ is None:
                break
            mm.append(a_ - b_)
        sg = float(np.sign(sum(mm[:3]))) if mm else 0.0
        run = 0
        while run < len(mm) and sg != 0 and sg * mm[run] >= EPS_REG:
            run += 1
        s6 = float(np.sign(sum(mm))) if mm else 0.0
        cnt6 = sum(1 for x in mm if s6 != 0 and s6 * x >= EPS_REG)
        REG[(t, k)] = (run, float(np.mean(mm[:run])) if run else 0.0, cnt6, float(sum(mm)) / 6.0 if len(mm) == 6 else 0.0)

    def target(t, k, h):
        a, b_ = Y.get((t + h, k)), Y.get((t, k))
        return None if a is None or b_ is None else a - b_

    # voisinage des tuiles (conformal local)
    TPOS = {int(k): i for i, k in enumerate(tiles)}
    cen = np.array([[(k // 1000 + 0.5) * wkm, (k % 1000 + 0.5) * wkm] for k in tiles])
    D = np.hypot(cen[:, None, 0] - cen[None, :, 0], cen[:, None, 1] - cen[None, :, 1])
    WT = np.where(D == 0, 1.0, np.where(D <= 3 * RR, OM * np.exp(-D / RR), 0.0))
    WT0 = np.where(D == 0, 1.0, np.where(D <= 75, 0.3 * np.exp(-D / 25), 0.0))
    LL = {int(k): inv_laea(G, G["x0"] + ((k % 1000) + 0.5) * TILE * G["size"], G["y1"] - ((k // 1000) + 0.5) * TILE * G["size"]) for k in tiles}
    SOF = {k: min(SECTORS, key=lambda s: (s[1] - LL[k][0]) ** 2 * 0.44 + (s[2] - LL[k][1]) ** 2)[0] for k in LL}
    keys_by_t = {}
    for key in FA:
        keys_by_t.setdefault(key[0], []).append(key)
    S.__dict__.update(dict(site=site, months=months, mi=mi, NM=NM, Y=Y, FA=FA, FS=FS, REG=REG, PS=PS, PM=PM, target=target,
                           TPOS=TPOS, WT=WT, WT0=WT0, LL=LL, SOF=SOF, wkm=wkm, used=used, missing=missing, keys_by_t=keys_by_t))
    return S


# ---------- modèles ----------
def hurdle(S, h, cfg=V1):
    """Distribution prédictive (39 quantiles), espérance et P(mouvement) par (t, k), walk-forward.
    cfg : sens = "own" (v1) | "feature" (P(sens) de front_sens.js en variable du 2e étage) | "replace" (P(sens) telle quelle) ;
    reg_L, reg_vmin : terme « régime établi » (None : absent)."""
    target, Y, REG = S.target, S.Y, S.REG
    if cfg.get("reg_feat"):     # variables de régime dans les trois étages (mois de suite, vitesses 1 et 6 mois)
        FA = {k: np.r_[x, REG[k][0], abs(REG[k][1]), abs(REG[k][3])] for k, x in S.FA.items()}
        FS0 = {k: np.r_[x, REG[k][1], REG[k][3]] for k, x in S.FS.items()}
    else:
        FA, FS0 = S.FA, S.FS
    sens = cfg.get("sens", "own") if S.PS else "own"
    L, vmin = cfg.get("reg_L"), cfg.get("reg_vmin", 0.25)
    six, pmin = cfg.get("reg_v") == "6m", cfg.get("reg_pm", 0.0)
    rdir, rhl = cfg.get("reg_dir", False), cfg.get("reg_hl")   # pool des rapports par sens ; oubli (mois, None : aucun)

    def speed(key):
        r = REG[key]
        return r[3] if six else r[1]

    def fs(key):
        x = FS0[key]
        if sens == "feature":
            p = S.PS.get(key)
            x = np.r_[x, math.log(min(max(p, 0.02), 0.98) / (1 - min(max(p, 0.02), 0.98))) if p is not None else 0.0, float(p is not None)]
        return x

    def in_reg(key, p):
        """régime établi : L mois de suite (ou L mois sur 6 avec la vitesse sur 6 mois) dans le même sens, vitesse ≥ vmin,
        et pression continue : P(mouvement) du premier étage ≥ pmin."""
        if L is None or p < pmin:
            return False
        r = REG[key]
        return (r[2] if six else r[0]) >= L and abs(speed(key)) >= vmin

    Q, MEAN, PMV, LAM = {}, {}, {}, {}
    hist = []      # tronçons en régime déjà prévus : (t, quantiles v1, quantiles régime, issue)
    for t in range(S.NM):
        tr = [key for q in range(max(0, t - WIN), t - h + 1) for key in S.keys_by_t.get(q, []) if target(*key, h) is not None]
        te = S.keys_by_t.get(t, [])
        if len(tr) < 300 or not te:
            continue
        yv = np.array([target(*x, h) for x in tr])
        mv = np.abs(yv) >= THR
        if mv.sum() < 30 or (yv[mv] > 0).sum() < 10 or (yv[mv] < 0).sum() < 5:
            continue
        XA = np.array([FA[x] for x in tr]); XS = np.array([fs(x) for x in tr])
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
        # régime : rapports Δ_h / (h · v) des tronçons en régime du passé ; λ réappris sur les régimes passés prévus
        ratios, lam = None, 0.0
        if L is not None:
            ptr = plogi(w1, za)
            rr = [(y_ / (h * speed(x)), x[0], np.sign(speed(x))) for x, y_, p_ in zip(tr, yv, ptr) if in_reg(x, p_)]
            if len(rr) >= 20:
                ratios = (np.clip(np.array([r[0] for r in rr]), -3.0, 4.0), np.array([r[1] for r in rr]), np.array([r[2] for r in rr]))
            past = [x for x in hist if t - WIN - h <= x[0] <= t - h]
            if len(past) >= 20:
                sc = [np.mean([crps_q(mixq(qa, qb, la), y) for _, qa, qb, y in past]) for la in LAMS]
                lam = LAMS[int(np.argmin(sc))]
        LAM[t] = lam
        for key in te:
            a = (FA[key] - mua) / sda
            s = (fs(key) - mus) / sds
            p = float(plogi(w1, a[None])[0])
            q = float(plogi(w2, s[None])[0])
            if sens == "replace" and key in S.PS:
                q = S.PS[key]
            y0 = Y[key]
            mr = np.clip(np.exp(b3[0] + np.r_[a, 1.0] @ b3[1:] + res), THR, max(24 - y0, THR))
            mu_ = np.clip(np.exp(b3[0] + np.r_[a, 0.0] @ b3[1:] + res), THR, max(y0, THR))
            atoms = np.r_[v0, mr, -mu_]
            wts = np.r_[(1 - p) * p0, np.full(len(mr), p * q / len(mr)), np.full(len(mu_), p * (1 - q) / len(mu_))]
            o_ = np.argsort(atoms, kind="stable")
            c_ = np.cumsum(wts[o_]); c_ /= c_[-1]
            qh = atoms[o_][np.clip(np.searchsorted(c_, QL - 1e-12), 0, len(atoms) - 1)]
            mh = float((atoms * wts).sum() / wts.sum())
            PMV[key] = p
            if ratios is not None and in_reg(key, p):
                rv, rq, rs = ratios
                sel = rs == np.sign(speed(key)) if rdir else np.ones(len(rv), bool)
                if sel.sum() < 10:
                    sel = np.ones(len(rv), bool)
                rw = 0.5 ** ((t - rq[sel]) / rhl) if rhl else np.ones(sel.sum())
                ra = np.clip(h * speed(key) * rv[sel], -y0, 24 - y0)
                qr = wquant(ra, rw, QL)
                ra = np.array([float((ra * rw).sum() / rw.sum())])
                yk = target(*key, h)
                if yk is not None:
                    hist.append((t, qh, qr, yk))
                Q[key] = mixq(qh, qr, lam)
                MEAN[key] = (1 - lam) * mh + lam * float(ra.mean())
            else:
                Q[key], MEAN[key] = qh, mh
    return Q, MEAN, PMV, LAM


def persistence(S, h, WT):
    """Référence : 0 km, distribution = Δ passés locaux (deux voisinages essayés, on garde le meilleur)."""
    Q = {}
    pool = [(key, S.target(*key, h)) for key in S.FA if S.target(*key, h) is not None]
    pk = np.array([S.TPOS[k] for (_, k), _ in pool]); pq = np.array([t for (t, _), _ in pool]); pv = np.array([v for _, v in pool])
    for t in range(S.NM):
        sel = pq <= t - h
        if sel.sum() < 300:
            continue
        for key in S.keys_by_t.get(t, []):
            w = WT[S.TPOS[key[1]]][pk[sel]] * 0.5 ** ((t - pq[sel]) / HL)
            if w.sum() < 3:
                w = 0.5 ** ((t - pq[sel]) / HL)
            Q[key] = wquant(pv[sel], w, QL)
    return Q


def cqr(S, Q, h, alpha, cls):
    target, TPOS, WT = S.target, S.TPOS, S.WT
    il, ih = (3, 35) if alpha == 0.2 else (0, 38)
    sc = {key: max(q[il] - target(*key, h), target(*key, h) - q[ih]) for key, q in Q.items() if target(*key, h) is not None}
    sk = np.array([TPOS[k] for (_, k) in sc], int); sq = np.array([t for (t, _) in sc]); sv = np.array(list(sc.values()))
    scl = np.array([cls.get(x, -1) for x in sc])
    by_t = {}
    for x in sc:
        by_t.setdefault(x[0], []).append(x)
    qby_t = {}
    for x in Q:
        qby_t.setdefault(x[0], []).append(x)
    al, out = {}, {}
    for t in sorted(qby_t):
        for (q, k) in by_t.get(t - h, []):
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
        for key in qby_t[t]:
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


def run_model(S, h, cfg):
    Q, MEAN, PMV, LAM = hurdle(S, h, cfg)
    cls = {key: int(np.searchsorted(PM_BINS, p)) for key, p in PMV.items()}
    C = {lab: cqr(S, Q, h, al, cls) for lab, al in (("80", 0.2), ("95", 0.05))}
    return SimpleNamespace(Q=Q, MEAN=MEAN, PMV=PMV, LAM=LAM, cls=cls, C=C)


# ---------- évaluation ----------
def score(S, h, M, ev, Q0=None):
    """CRPS, couverture 80 / 95 % globale, par classe de risque et par secteur, sur les tronçons-mois ev ; ok = règle d'affichage."""
    target = S.target
    yv = np.array([target(*k_, h) for k_ in ev])
    mvm = np.abs(yv) >= THR
    crps = np.array([crps_q(M.Q[k_], y) for k_, y in zip(ev, yv)])
    V = {"n": len(ev), "crps": round(float(crps.mean()), 4), "crps_bougent": round(float(crps[mvm].mean()), 3) if mvm.any() else None,
         "mae": round(float(np.mean(np.abs(np.array([M.Q[k_][19] for k_ in ev]) - yv))), 4),
         "mae_bougent": round(float(np.mean(np.abs(np.array([M.Q[k_][19] for k_ in ev]) - yv)[mvm])), 3) if mvm.any() else None}
    if Q0 is not None:
        crps0 = min((np.array([crps_q(Q0_[k_], y) for k_, y in zip(ev, yv)]) for Q0_ in Q0), key=lambda a: a.mean())
        V["crps_persistance"] = round(float(crps0.mean()), 4)
        V["crps_bougent_persistance"] = round(float(crps0[mvm].mean()), 3) if mvm.any() else None
    ok = Q0 is None or V["crps"] < V["crps_persistance"]
    cl = np.array([M.cls.get(k_, -1) for k_ in ev])
    for lab, nom in (("80", 0.80), ("95", 0.95)):
        cov = np.array([M.C[lab][k_][0] - 1e-9 <= y <= M.C[lab][k_][1] + 1e-9 for k_, y in zip(ev, yv)])
        V["couverture" + lab] = round(float(cov.mean()), 3)
        V["couverture" + lab + "_bougent"] = round(float(cov[mvm].mean()), 3) if mvm.any() else None
        V["largeur" + lab] = round(float(np.mean([M.C[lab][k_][1] - M.C[lab][k_][0] for k_ in ev])), 2)
        V["classes" + lab] = {}
        for c in range(len(PM_BINS) + 1):
            s_ = cl == c
            if s_.sum():
                V["classes" + lab][str(c)] = [round(float(cov[s_].mean()), 3), int(s_.sum()), round(float(mvm[s_].mean()), 3)]
                if s_.sum() >= 100 and cov[s_].mean() < nom - 0.03:
                    ok = False
        V["secteurs" + lab] = {}
        for sname, _, _ in SECTORS:
            s_ = np.array([S.SOF[k_[1]] == sname for k_ in ev])
            if s_.sum():
                V["secteurs" + lab][sname] = [round(float(cov[s_].mean()), 3), int(s_.sum())]
                if s_.sum() >= 100 and cov[s_].mean() < nom - 0.05:
                    ok = False
    V["ok"] = bool(ok)
    return V


def km2(S, h, M, keys, frm=EVAL_FROM):
    """km² agrégés (gain russe net, somme des tronçons × 24 km²) sur les tronçons-mois keys ; intervalles = résidus passés."""
    target, months, wkm = S.target, S.months, S.wkm
    ts = sorted({k_[0] for k_ in keys})
    agg = {}
    for t in ts:
        ks_ = [k_ for k_ in keys if k_[0] == t]
        pred = sum(M.MEAN[k_] for k_ in ks_) * wkm
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
        if t in res and months[t] >= frm:
            ac8 += lo8 <= res[t] <= hi8; ac9 += lo9 <= res[t] <= hi9; an += 1
    trend = {}
    for t in res:
        ks_ = [k_ for k_ in keys if k_[0] == t]
        if all((t - h, k_[1]) in S.Y for k_ in ks_):
            trend[t] = sum(S.Y[k_] - S.Y[(t - h, k_[1])] for k_ in ks_) * wkm
    sel = [t for t in res if months[t] >= frm]
    K = {"n": an, "couverture80": round(ac8 / max(an, 1), 3), "couverture95": round(ac9 / max(an, 1), 3),
         "mae": round(float(np.mean([abs(res[t]) for t in sel]))) if sel else None,
         "biais": round(float(np.mean([res[t] for t in sel]))) if sel else None,
         "mae_persistance": round(float(np.mean([abs(agg[t][1]) for t in sel]))) if sel else None,
         "mae_tendance": round(float(np.mean([abs(agg[t][1] - trend[t]) for t in sel if t in trend]))) if any(t in trend for t in sel) else None}
    K["ok"] = bool(an >= 12 and K["couverture80"] >= 0.70 and K["couverture95"] >= 0.88 and K["mae"] is not None
                   and K["mae"] < min(x for x in (K["mae_persistance"], K["mae_tendance"]) if x is not None))
    return K, agg, AGI


def events(S, h, M, keys):
    """Par événement : km² réalisés (Σ Δ_h × 24 sur les tronçons de la boîte, origines de début − h à fin − 1) contre prévus
    (Σ espérances), et part des tronçons qui bougent couverts par l'intervalle 80 %."""
    out = {}
    ks = set(keys)
    for name, (lo0, lo1, la0, la1), m0, m1 in EVENTS:
        tl = [k for k in S.LL if lo0 <= S.LL[k][0] <= lo1 and la0 <= S.LL[k][1] <= la1]
        if m0 not in S.mi or m1 not in S.mi:
            continue
        t0, t1 = S.mi[m0], S.mi[m1]
        rows = [(t, k) for t in range(max(0, t0 - h), t1) for k in tl if (t, k) in ks]
        if not rows:
            continue
        real = sum(S.target(*x, h) for x in rows) * S.wkm
        pred = sum(M.MEAN[x] for x in rows) * S.wkm
        mvr = [x for x in rows if abs(S.target(*x, h)) >= THR]
        cov = float(np.mean([M.C["80"][x][0] - 1e-9 <= S.target(*x, h) <= M.C["80"][x][1] + 1e-9 for x in mvr])) if mvr else None
        out[name] = {"realise": round(real), "prevu": round(pred), "bougent": len(mvr), "couverts80": None if cov is None else round(cov, 2)}
    return out


def common_keys(S, h, models, frm, to=None):
    ks = set.intersection(*[set(M.Q) & set(M.C["80"]) & set(M.C["95"]) for M in models])
    return sorted(k_ for k_ in ks if S.months[k_[0]] >= frm and (to is None or S.months[k_[0]] < to) and S.target(*k_, h) is not None)


def main():
    root = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    S = prepare(root)
    months, NM, wkm = S.months, S.NM, S.wkm
    out_h = {}
    for h in (1, 2):
        Q0 = (persistence(S, h, S.WT), persistence(S, h, S.WT0))
        M1, M2 = run_model(S, h, V1), run_model(S, h, V2[h])
        # comparaison v1 / v2 sur la période gelée (2025+) puis sur 2023+, mêmes tronçons-mois
        ev_all = [k_ for k_ in common_keys(S, h, (M1, M2), EVAL_FROM) if k_ in Q0[0] and k_ in Q0[1]]
        ev_oos = [k_ for k_ in ev_all if months[k_[0]] >= FROZEN_FROM]
        ev_dev = [k_ for k_ in ev_all if months[k_[0]] < FROZEN_FROM]
        cmp_ = {}
        for lab, ev in (("2023-2024 (réglage)", ev_dev), ("2025+ (gelé)", ev_oos)):
            if not ev:
                continue
            s1, s2 = score(S, h, M1, ev, Q0), score(S, h, M2, ev, Q0)
            k1, k2 = km2(S, h, M1, ev, months[ev[0][0]])[0], km2(S, h, M2, ev, months[ev[0][0]])[0]
            cmp_[lab] = {"n": len(ev), "crps_persistance": s1["crps_persistance"], "crps_v1": s1["crps"], "crps_v2": s2["crps"],
                         "crps_bougent_v1": s1["crps_bougent"], "crps_bougent_v2": s2["crps_bougent"],
                         "couverture80_v1": s1["couverture80"], "couverture80_v2": s2["couverture80"],
                         "couverture95_v1": s1["couverture95"], "couverture95_v2": s2["couverture95"],
                         "km2_biais_v1": k1["biais"], "km2_biais_v2": k2["biais"], "km2_mae_v1": k1["mae"], "km2_mae_v2": k2["mae"]}
        V1s, V2s = score(S, h, M1, ev_all, Q0), score(S, h, M2, ev_all, Q0)
        oos = cmp_.get("2025+ (gelé)")
        use_v2 = bool(oos and oos["crps_v2"] < oos["crps_v1"] and V2s["ok"])
        M, cfg = (M2, V2[h]) if use_v2 else (M1, V1)
        ev = sorted(set(k_ for k_ in M.Q if k_ in Q0[0] and k_ in Q0[1] and months[k_[0]] >= EVAL_FROM and S.target(*k_, h) is not None
                        and k_ in M.C["80"] and k_ in M.C["95"]))
        V = score(S, h, M, ev, Q0)
        ok = V.pop("ok")
        K, agg, AGI = km2(S, h, M, list(M.Q))
        km2_ok = K.pop("ok")
        V["km2"] = K
        V["modele"] = "v2" if use_v2 else "v1"
        V["v1_v2"] = cmp_
        V["evenements"] = events(S, h, M, ev)
        V["evenements_v1"] = events(S, h, M1, ev) if use_v2 else None
        V["lambda_regime"] = M2.LAM.get(NM - 1)
        tl = NM - 1
        rows = []
        for k_ in sorted([x for x in M.Q if x[0] == tl]):
            if k_ not in M.C["80"] or k_ not in M.C["95"]:
                continue
            k = k_[1]
            rows.append([int((k % 1000) * TILE + TILE // 2), int((k // 1000) * TILE + TILE // 2), round(float(M.Q[k_][19]), 2),
                         round(M.C["80"][k_][0], 2), round(M.C["80"][k_][1], 2), round(M.C["95"][k_][0], 2), round(M.C["95"][k_][1], 2),
                         round(M.PMV[k_] * 100), round(M.MEAN[k_], 2)])
        ts = sorted(agg)
        hist = [[months[t], months[t + h] if t + h < NM else None, *AGI[t], round(agg[t][1]) if agg[t][1] is not None else None] for t in ts if t in AGI]
        out_h[str(h)] = {"base": months[tl], "cible": (dt.date(int(months[tl][:4]), int(months[tl][5:]), 1) + dt.timedelta(days=31 * h)).strftime("%Y-%m"),
                         "show": {"troncons": bool(ok), "km2": bool(km2_ok)}, "modele": V["modele"], "params": cfg,
                         "tiles": rows, "km2": AGI.get(tl), "km2_hist": hist[-24:], "validation": V}
        for lab, c in cmp_.items():
            print(f"  t+{h} {lab} (n={c['n']}) : CRPS persistance {c['crps_persistance']:.4f}, v1 {c['crps_v1']:.4f}, v2 {c['crps_v2']:.4f} ; "
                  f"qui bougent v1 {c['crps_bougent_v1']}, v2 {c['crps_bougent_v2']} ; couverture 80 % {c['couverture80_v1']:.1%} / {c['couverture80_v2']:.1%}, "
                  f"95 % {c['couverture95_v1']:.1%} / {c['couverture95_v2']:.1%} ; km² biais {c['km2_biais_v1']} / {c['km2_biais_v2']}", flush=True)
        print(f"ampleur t+{h} [{V['modele']}] : CRPS {V['crps']:.4f} (persistance {V['crps_persistance']:.4f}) ; qui bougent {V['crps_bougent']:.3f} "
              f"({V['crps_bougent_persistance']:.3f}) ; couverture 80 % {V['couverture80']:.1%} (qui bougent {V['couverture80_bougent']:.1%}), "
              f"95 % {V['couverture95']:.1%} ({V['couverture95_bougent']:.1%}) ; par classe 80 % {V['classes80']} 95 % {V['classes95']} ; "
              f"km² : MAE {K['mae']} (persistance {K['mae_persistance']}, tendance {K['mae_tendance']}), biais {K['biais']}, couverture {K['couverture80']:.0%}/{K['couverture95']:.0%} ; "
              f"affichage tronçons {'OUI' if ok else 'NON'}, km² {'OUI' if km2_ok else 'NON'}", flush=True)
        print("  événements : " + " ; ".join(f"{n} {e['realise']}/{e['prevu']} km² ({e['couverts80']})" for n, e in V["evenements"].items()), flush=True)
        bad = {lab: {s: v for s, v in V["secteurs" + lab].items() if v[1] >= 100 and v[0] < (0.75 if lab == "80" else 0.90)} for lab in ("80", "95")}
        if any(bad.values()):
            print(f"  secteurs sous le seuil : {bad}", flush=True)
    meth2 = ("v2 : v1 + régime établi (vitesse soutenue L mois de suite, mélange appris en walk-forward) + P(sens RU | bouge) "
             "du modèle de sens dans le deuxième étage ; paramètres choisis sur 2023-2024, gelés sur 2025+")
    doc = {
        "generated": dt.date.today().isoformat(), "tile_km": 24, "unit": "km de profondeur (surface / 24 km) ; km² = km × 24",
        "sign": "> 0 : avancée russe (vers le côté ukrainien) ; < 0 : avancée ukrainienne",
        "columns": ["col", "row", "median_km", "lo80", "hi80", "lo95", "hi95", "pmove_pct", "mean_km"],
        "km2_columns": ["moyenne", "lo80", "hi80", "lo95", "hi95"],
        "km2_hist_columns": ["origine", "cible", "moyenne", "lo80", "hi80", "lo95", "hi95", "realise"],
        "inputs": [u for u in S.used if not u.startswith("P(sens") or any(v["modele"] == "v2" and v["params"].get("sens") != "own" for v in out_h.values())],
        "missing": S.missing, "horizons": out_h,
        "method": "deux étages : P(mouvement ≥ 0,5 km) × P(sens | mouvement) × ampleur | mouvement ; conformal local par tronçon et par classe de risque ; walk-forward 2023+"
                  + (" ; " + meth2 if any(v["modele"] == "v2" for v in out_h.values()) else " ; v1 (v2 testée, non retenue : " + meth2 + ")"),
    }
    path = os.path.join(S.site, "front_advance.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.FRONT_ADVANCE=" + json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"écrit {path} ({os.path.getsize(path) // 1024} Ko) ; modèles {'/'.join(v['modele'] for v in out_h.values())} ; entrées : {', '.join(doc['inputs'])}" + (f" ; manquantes : {', '.join(S.missing)}" if S.missing else ""), flush=True)


if __name__ == "__main__":
    main()
