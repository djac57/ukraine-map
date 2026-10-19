"""« Dans quel sens » : P(bouge) × P(sens | bouge) par tronçon de 24 km et par DÉCADE, flèche « poussée russe probable »
et règle « poussée possible » améliorée (côté ukrainien). → site/front_sens.js

    python3 scripts/build_direction.py            (après build_front.py : lit les fichiers publiés du site)

Étude et validation walk-forward : ukraine-recherche/analyses/sens/RAPPORT.md (décision de l'utilisateur du 19/10/2026).

Entrées (toutes disponibles en CI ; chacune peut manquer, le script se dégrade proprement) :
- site/front.js : instantanés mensuels, P(mouvement) publiée par tronçon (months[].pm), grille et mailles ;
- site/front_equilibre.js : champ des cartes (p > 0,5 → profondeur russe du tronçon, mouvement récent, vérité
  d'apprentissage) ; site/front_asym.js : « combats derrière la ligne » publié (|a| là où l'asymétrie est anormale) ;
- Telegram, agrégats par tronçon (aucun texte) : data/telegram_troncons.csv (collecte CI) ; à défaut les agrégats figés
  de recherche (ukraine-recherche/analyses/sens/tg_daily.csv, puis signals3/sens_troncons_month.csv), à défaut
  site/telegram_troncons.js (version réduite, mensuelle) ; rien du tout → étage sans Telegram ;
- impacts Sentinel-2 par côté : data/impacts_troncons_decades.csv (figé, 2024-07 → 2026-10) ; UTILISÉS QUAND ILS SONT
  DISPONIBLES dans l'indice additif de la « poussée possible » et comme indice concordant ; sans S2, le terme est
  absent (aucune pénalité). Dans P(sens) : option testée, non retenue (voir S2_IN_SENS).

Modèle (walk-forward strict : à chaque pas, n'apprend que sur des tronçons-pas dont l'issue, persistance comprise, était
connue ; fenêtre glissante de 12 mois) :
  P(bouge)        = P(mouvement) publiée (dernier instantané mensuel ≤ la décade) ;
  P(UA | bouge)   = régression logistique ridge (λ = 3) apprise sur les tronçons-pas passés qui ont bougé d'au moins 1 km
                    (champ des cartes, 3 mois, persistance 1 mois) : asymétrie publiée (côté russe, côté ukrainien,
                    différence), mouvement récent signé (1 et 3 mois), Telegram sur 30 jours (différence brute
                    UA − RU en log, z du tronçon sur 12 mois de la différence, des attaques/présences de chaque camp) et
                    sur 10 jours ; option SENS_S2_DANS_SENS=1 : + β · S2 quand S2 est mesurable (testé, non retenu) ;
  P(sens RU)      = 1 − P(UA | bouge).
Drapeaux (seuils choisis en walk-forward, voir le rapport) :
  1  « poussée russe probable » : P(bouge) ≥ 97e centile des P(bouge) des 12 mois précédents ET P(sens RU) ≥ 0,8 ;
  2  « poussée possible » (UA, améliorée) : P(UA | bouge) ≥ 0,3 ET [règle actuelle (≥ 6 mailles côté russe à
     |a| ≥ 0,4 deux mois de suite) OU (indice additif D_UA ≥ 1,5 ET P(bouge) ≥ 80e centile des 12 mois précédents)],
     D_UA = Σ min(max(anomalie, −1), 5) / 2 sur : 5 × asymétrie publiée, Telegram z(UA − RU), S2 (si disponible) ;
  4  ancienne règle « poussée possible » seule (pour comparaison, non affichée).
Agrégats seulement (tronçons de 24 km), aucun texte Telegram, aucun suivi d'unités ; pas = décades (10, 20, fin de
mois) jusqu'à la date limite J-7 du build.
"""
import base64
import csv
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA = os.path.join(ROOT, "data")
RECH = os.environ.get("UKRAINE_RECHERCHE", os.path.join(os.path.dirname(os.path.dirname(ROOT)), "perso", "ukraine-recherche"))
if not os.path.isdir(RECH):
    RECH = os.path.join(os.path.dirname(ROOT), "ukraine-recherche")
TILE = 12
WIN_DIR = 12           # fenêtre d'apprentissage du sens (mois)
P_RU, Q_RU = 0.8, 97   # flèche « poussée russe probable »
P_UA, Q_GATE, D_UA = 0.3, 80, 1.5   # « poussée possible » améliorée
# S2 dans P(sens) : testé en walk-forward (β appris sur les seuls cas où S2 était mesurable) : AUC du sens 0,854 → 0,847
# (2023+, ≥ 2 km), 0,731 → 0,706 (2025+) ; donc NON par défaut. S2 reste utilisé, quand il est disponible, dans l'indice
# additif de la « poussée possible » (où il apporte : 6 → 9 départs ukrainiens précédés sur 75) et comme indice concordant.
S2_IN_SENS = os.environ.get("SENS_S2_DANS_SENS", "0") == "1"


def say(*a):
    print(*a, flush=True)


def load_js(name):
    s = open(os.path.join(SITE, name), encoding="utf-8").read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def decode_field(name, nU):
    s = open(os.path.join(SITE, name), encoding="utf-8").read()
    arr = json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))
    out, prev = [], np.zeros(nU, np.uint8)
    for e in arr:
        if not e:
            out.append(None)
            continue
        b, q, o, i = base64.b64decode(e), np.zeros(nU, np.uint8), 0, 0
        while i < len(b):
            v = b[i]; i += 1
            n = sh = 0
            while True:
                c = b[i]; i += 1
                n |= (c & 127) << sh; sh += 7
                if not c & 128:
                    break
            q[o:o + n] = prev[o:o + n] if v == 255 else v
            o += n
        out.append(q)
        prev = q
    return out


def month_add(m, n):
    y, mo = int(m[:4]), int(m[5:]) + n
    while mo > 12:
        y, mo = y + 1, mo - 12
    while mo < 1:
        y, mo = y - 1, mo + 12
    return f"{y}-{mo:02d}"


# ---------------------------------------------------------------- front publié
FR = load_js("front.js")
GX, GY = FR["grid"]["nx"], FR["grid"]["ny"]
b = base64.b64decode(FR["runs"])
cellPlace = np.zeros(GX * GY, np.int32)
o = 0
for i in range(0, len(b), 3):
    v, n = b[i] | b[i + 1] << 8, b[i + 2]
    cellPlace[o:o + n] = -1 if v == 65535 else (-2 if v == 65534 else v)
    o += n
UC = np.flatnonzero(cellPlace != -1)
nU = len(UC)
jj, ii = UC // GX, UC % GX
tile = (jj // TILE) * 1000 + ii // TILE
TID = {int(k): np.flatnonzero(tile == k) for k in np.unique(tile)}
frames = [(n, mo["date"]) for n, mo in enumerate(FR["months"]) if mo and mo.get("date")]
PEQ = decode_field("front_equilibre.js", nU)
try:
    PAS = decode_field("front_asym.js", nU)
except (OSError, ValueError):
    PAS = [None] * len(PEQ)
    say("front_asym.js absent : asymétrie mise à zéro")

MON, MONTHS = {}, []
for n, d in frames:
    if n >= len(PEQ) or PEQ[n] is None:
        continue
    m = d[:7]
    MONTHS.append(m)
    peq = PEQ[n]
    a1 = PAS[n] / 63.0 if n < len(PAS) and PAS[n] is not None else np.zeros(nU)
    a0 = PAS[n - 1] / 63.0 if n > 0 and n - 1 < len(PAS) and PAS[n - 1] is not None else np.zeros(nU)
    pm = {((r[1] - 6) // TILE) * 1000 + (r[0] - 6) // TILE: r[2] / 100 for r in (FR["months"][n].get("pm") or [])}
    ru = peq / 63.0 > 0.5
    for k, us in TID.items():
        r_, u_ = us[ru[us]], us[~ru[us]]
        MON[(m, k)] = dict(pm=pm.get(k), y=float(ru[us].sum()) * 4.0 / (TILE * 2.0),
                           fa_ru=float(a1[r_].mean()) if len(r_) else 0.0, fa_ua=float(a1[u_].mean()) if len(u_) else 0.0,
                           r_ru=int(((a1[r_] >= 0.4) & (a0[r_] >= 0.4)).sum()))
MONTHS = sorted(set(MONTHS))
LASTM = MONTHS[-1]
limit = dt.date.fromisoformat(FR.get("limit") or frames[-1][1])

# ---------------------------------------------------------------- Telegram (agrégats par tronçon)
COLS = ("ru_combat", "ru_attUA", "ru_attRU", "ru_presUA", "ua_combat", "ua_attUA", "ua_attRU", "ua_presRU")
TG_DAY = defaultdict(dict)    # k -> {date: vecteur 8} (résolution jour/décade)
TG_MON = defaultdict(dict)    # k -> {mois: vecteur 8} (résolution mensuelle)
TG_SRC, TG_LAST, TG_RES = "aucune", None, None


def tg_add(store, k, key, camp, vals):
    v = store[k].setdefault(key, np.zeros(8))
    c, a_ua, a_ru, p_ua, p_ru = vals
    if camp == "ru":
        v[[0, 1, 2, 3]] += [c, a_ua, a_ru, p_ua]
    elif camp == "ua":
        v[[4, 5, 6, 7]] += [c, a_ua, a_ru, p_ru]


def read_tg_csv(path):
    """Format souple : tronçon (k, ou troncon + i0/j0), temps (date/jour/decade → jour ; mois → mois), camp de la source,
    comptes (n_combat, n_ua, n_ru, n_presence_ua|n_pres_ua, n_presence_ru|n_pres_ru)."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        return None
    h = rows[0].keys()
    g = lambda r, *names: next((float(r[x] or 0) for x in names if x in r), 0.0)  # noqa: E731
    if "pas" in h and "debut" in h:            # format de la collecte CI : lignes « decade » et « mois » mêlées, on garde les décades
        rows = [r for r in rows if r["pas"] == "decade"]
        for r in rows:
            d0 = dt.date.fromisoformat(r["debut"][:10])
            r["jour"] = (d0.replace(day=10) if d0.day <= 10 else d0.replace(day=20) if d0.day <= 20 else
                         dt.date(d0.year + d0.month // 12, d0.month % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
        if not rows:
            return None
        h = rows[0].keys()
    tcol = next((c for c in ("date", "jour", "decade", "fin", "mois", "month") if c in h), None)
    ccol = next((c for c in ("camp_source", "camp", "source_camp") if c in h), None)
    if tcol is None or ccol is None or not any(c in h for c in ("k", "troncon", "i0")):
        return None
    res = "mois" if tcol in ("mois", "month") else "jour"
    last = None
    for r in rows:
        if "i0" in r and "j0" in r and r["i0"] != "":
            k = (int(r["j0"]) // TILE) * 1000 + int(r["i0"]) // TILE
        elif "k" in r:
            k = int(r["k"])
        else:
            continue
        t = r[tcol]
        if res == "jour" and len(t) == 9:      # décade « AAAA-MM-d » → dernier jour de la décade
            y, mo, dd = int(t[:4]), int(t[5:7]), int(t[8])
            t = (dt.date(y, mo, 10) if dd == 1 else dt.date(y, mo, 20) if dd == 2 else
                 dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
        vals = (g(r, "n_combat"), g(r, "n_ua", "n_att_ua"), g(r, "n_ru", "n_att_ru"), g(r, "n_presence_ua", "n_pres_ua"), g(r, "n_presence_ru", "n_pres_ru"))
        tg_add(TG_DAY if res == "jour" else TG_MON, k, t[:10] if res == "jour" else t[:7], r[ccol], vals)
        last = max(last or t, t)
    return res, last


TG_MODE = os.environ.get("SENS_TG", "")          # tests : « site » (couche publiée seule) ou « aucun »
for path, lab in (() if TG_MODE else ((os.path.join(DATA, "telegram_troncons.csv"), "data/telegram_troncons.csv (CI)"),
                  (os.path.join(RECH, "analyses", "sens", "tg_daily.csv"), "agrégats figés de recherche (jour)"),
                  (os.path.join(RECH, "signals3", "sens_troncons_month.csv"), "agrégats figés de recherche (mois)"))):
    if os.path.exists(path):
        try:
            r = read_tg_csv(path)
        except (OSError, ValueError, KeyError) as e:
            say(f"Telegram : {path} illisible ({e.__class__.__name__}), source suivante")
            r = None
        if r:
            TG_RES, TG_LAST = r
            TG_SRC = lab
            break
if TG_RES is None and TG_MODE != "aucun" and os.path.exists(os.path.join(SITE, "telegram_troncons.js")):
    T = load_js("telegram_troncons.js")
    tiles_ = {int(t): v for t, v in T["tiles"].items()}
    for side in ("ru", "ua"):
        for m, t, a, p in T.get(side, []):
            i0, j0 = tiles_[int(t)]
            k = (j0 // TILE) * 1000 + i0 // TILE
            # version réduite : sources russes → attaques/présence UA ; sources ukrainiennes → attaques/présence RU
            tg_add(TG_MON, k, m, side, (a + p, a, 0, p, 0) if side == "ru" else (a + p, 0, a, 0, p))
    TG_RES, TG_LAST, TG_SRC = "mois", T.get("last"), "site/telegram_troncons.js (réduit)"
say(f"Telegram : {TG_SRC}, résolution {TG_RES}, jusqu'à {TG_LAST}")
TGC = {}
if TG_RES == "jour":
    D0 = dt.date(2022, 2, 1)
    ND = (limit - D0).days + 2
    for k, dd in TG_DAY.items():
        a = np.zeros((ND, 8))
        for d, v in dd.items():
            i = (dt.date.fromisoformat(d) - D0).days
            if 0 <= i < ND:
                a[i] += v
        TGC[k] = np.vstack([np.zeros((1, 8)), np.cumsum(a, 0)])


def tg_window(k, d, days):
    if TG_RES == "jour":
        c = TGC.get(k)
        if c is None:
            return np.zeros(8)
        b_ = min((d - dt.date(2022, 2, 1)).days + 1, len(c) - 1)
        return c[b_] - c[max(0, b_ - days)]
    if TG_RES == "mois":     # mois civil achevé le plus récent (le mois de d si d en est le dernier jour)
        m = d.strftime("%Y-%m") if (d + dt.timedelta(days=1)).day == 1 else month_add(d.strftime("%Y-%m"), -1)
        return TG_MON.get(k, {}).get(m, np.zeros(8))
    return np.zeros(8)


def tg_ok(d):
    if TG_RES is None or TG_LAST is None:
        return False
    return d.isoformat()[:len(TG_LAST)] <= TG_LAST


# ---------------------------------------------------------------- Sentinel-2 (tronçons de 20 km → tuiles de 24 km)
S2 = defaultdict(lambda: [0.0, 0.0])
S2DECS = []
s2path = os.path.join(DATA, "impacts_troncons_decades.csv")
if os.path.exists(s2path) and os.environ.get("SENS_S2", "1") != "0":
    decs = set()
    ov = lambda a0, a1, b0, b1: max(0, min(a1, b1) - max(a0, b0))  # noqa: E731
    for r in csv.DictReader(open(s2path, encoding="utf-8")):
        js_, is_ = map(int, r["sec"].split("_"))
        n, a = float(r["s2_n"] or 0), float(r["s2_km2"] or 0)
        decs.add(r["decade"])
        for jt in range(js_ * 10 // TILE, (js_ * 10 + 9) // TILE + 1):
            for it in range(is_ * 10 // TILE, (is_ * 10 + 9) // TILE + 1):
                f = ov(js_ * 10, js_ * 10 + 10, jt * TILE, jt * TILE + TILE) * ov(is_ * 10, is_ * 10 + 10, it * TILE, it * TILE + TILE) / 100.0
                if f > 0:
                    s_ = S2[(r["side"], r["decade"], jt * 1000 + it)]
                    s_[0] += f * n
                    s_[1] += f * a
    S2DECS = sorted(decs)
say(f"Sentinel-2 : {'data/impacts_troncons_decades.csv, ' + S2DECS[0] + ' → ' + S2DECS[-1] if S2DECS else 'absent'}")
S2I = {d: i for i, d in enumerate(S2DECS)}


def s2z(d, k, side, nd, amin):
    lab = f"{d.year}-{d.month:02d}-{1 if d.day <= 10 else 2 if d.day <= 20 else 3}"
    if lab not in S2I:
        return None
    last = S2I[lab]
    n = sum(S2[(side, S2DECS[q], k)][0] for q in range(last - nd + 1, last + 1) if q >= 0)
    a = sum(S2[(side, S2DECS[q], k)][1] for q in range(last - nd + 1, last + 1) if q >= 0)
    nn = aa = 0.0
    for q in range(last - 36, last - 17):
        if q >= 0:
            v = S2[(side, S2DECS[q], k)]
            nn += v[0]; aa += v[1]
    if a < amin or aa < 50:
        return None
    e = (nn + 0.5) / aa * a
    return (n - e) / math.sqrt(max(e, 1.0))


# ---------------------------------------------------------------- pas (décades) et variables
steps = []
m = next(m for m in MONTHS if any(MON[(m, k)]["pm"] is not None for k in TID))
while True:
    y, mo = int(m[:4]), int(m[5:])
    end = dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)
    for d in (dt.date(y, mo, 10), dt.date(y, mo, 20), end):
        if d <= limit:
            steps.append(d)
    if end >= limit:
        break
    m = month_add(m, 1)
mfs = [d.strftime("%Y-%m") if (d + dt.timedelta(days=1)).day == 1 else month_add(d.strftime("%Y-%m"), -1) for d in steps]
for s in range(len(steps)):          # dernier instantané mensuel publié à la date du pas
    while mfs[s] not in MONTHS and mfs[s] > MONTHS[0]:
        mfs[s] = month_add(mfs[s], -1)
NS = len(steps)
SPM = 3
NAMES = ["fa", "fa_ru", "fa_ua", "dy1", "dy3", "tgd30", "z_tgd30", "z_tgu30", "z_tgr30", "z_tgd10"]
ser = defaultdict(dict)
ROWS = {}
for s, d in enumerate(steps):
    mf = mfs[s]
    okt = tg_ok(d)
    for k in TID:
        r = MON.get((mf, k))
        if r is None or r["pm"] is None:
            continue
        ROWS[(s, k)] = r
        if okt:
            for w in (30, 10):
                v = tg_window(k, d, w)
                u = v[1] + v[3] + v[5]
                ru = v[6] + v[7] + v[2]
                ser[(k, f"d{w}")][s] = math.log1p(u) - math.log1p(ru)
                ser[(k, f"u{w}")][s] = math.log1p(u)
                ser[(k, f"r{w}")][s] = math.log1p(ru)
ZGAP = 3 if TG_RES == "jour" else 1


def zpast(series, s, gap, n=12 * SPM, minn=6, floor=0.5):
    if s not in series:
        return 0.0
    past = [series[q] for q in range(s - n - gap + 1, s - gap + 1) if q in series]
    if len(past) < minn:
        return 0.0
    return float((series[s] - np.mean(past)) / max(np.std(past), floor))


F, S2V = {}, {}
for (s, k), r in ROWS.items():
    mf = mfs[s]
    y1 = MON.get((month_add(mf, -1), k), r)["y"]
    y3 = MON.get((month_add(mf, -3), k), r)["y"]
    gap = ZGAP if TG_RES == "jour" else SPM      # au pas mensuel, la valeur se répète 3 fois : passé strict
    F[(s, k)] = np.array([r["fa_ru"] - r["fa_ua"], r["fa_ru"], r["fa_ua"], r["y"] - y1, r["y"] - y3,
                          ser[(k, "d30")].get(s, 0.0), zpast(ser[(k, "d30")], s, gap), zpast(ser[(k, "u30")], s, gap),
                          zpast(ser[(k, "r30")], s, gap), zpast(ser[(k, "d10")], s, gap)])
    zr, zu = s2z(steps[s], k, "RU", 3, 20), s2z(steps[s], k, "UA", 3, 20)
    if zr is not None and zu is not None:
        S2V[(s, k)] = (float(np.clip(zr, -5, 10)) - float(np.clip(zu, -5, 10))) / math.sqrt(2)


# ---------------------------------------------------------------- vérité d'apprentissage (champ des cartes, mensuel)
def target(mf, k, X, sgn):
    """Mouvement ≥ X km au-delà de l'extrême des 2 mois précédents, à 1-3 mois, qui tient 1 mois ; None si inconnu."""
    if month_add(mf, 4) > LASTM or (mf, k) not in MON:
        return None
    ref = [MON[(month_add(mf, -q), k)]["y"] for q in (2, 1, 0) if (month_add(mf, -q), k) in MON]
    y0 = min(ref) if sgn < 0 else max(ref)
    for h in (1, 2, 3):
        a, b_ = MON.get((month_add(mf, h), k)), MON.get((month_add(mf, h + 1), k))
        if a is None:
            continue
        bb = b_["y"] if b_ else a["y"]
        if sgn * (a["y"] - y0) >= X and sgn * (bb - y0) >= X:
            return 1
    return 0


TGT = {}
for (s, k) in F:
    for X in (1.0, 2.0):
        u, r_ = target(mfs[s], k, X, -1), target(mfs[s], k, X, 1)
        if u is not None:
            TGT[("UA", X, s, k)], TGT[("RU", X, s, k)] = u, r_


def logi(X, y, lam=3.0):
    X = np.c_[np.ones(len(X)), X]
    w = np.zeros(X.shape[1])
    for _ in range(30):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        Hm = (X * (p * (1 - p))[:, None]).T @ X + lam * np.diag(np.r_[0, np.ones(X.shape[1] - 1)]) + 1e-8 * np.eye(X.shape[1])
        w += np.linalg.solve(Hm, X.T @ (y - p) - lam * np.r_[0, w[1:]])
    return w


def sig(v):
    return 1 / (1 + math.exp(-max(-30, min(30, v))))


PUA, LOGIT0, BETA = {}, {}, {}
use = [i for i, n in enumerate(NAMES) if TG_RES is not None or not n.startswith(("tg", "z_tg"))]
bystep = defaultdict(list)
for key in F:
    bystep[key[0]].append(key)
for s in range(NS):
    mf = mfs[s]
    lo = month_add(mf, -4 - WIN_DIR)
    tr = [(q, k) for (q, k) in F if lo < mfs[q] <= month_add(mf, -4) and (TGT.get(("UA", 1.0, q, k)) or TGT.get(("RU", 1.0, q, k)))]
    if len(tr) < 30:
        continue
    yr = np.array([TGT[("UA", 1.0, q, k)] for q, k in tr], float)
    if yr.sum() < 5 or len(yr) - yr.sum() < 5:
        continue
    Xr = np.array([F[x][use] for x in tr])
    mu, sd = Xr.mean(0), Xr.std(0) + 1e-9
    w = logi((Xr - mu) / sd, yr)
    lg = lambda key: w[0] + float(((F[key][use] - mu) / sd) @ w[1:])  # noqa: E731
    # S2 quand il est disponible : β sur les cas passés où il était mesurable (offset = logit du modèle)
    t2 = [x for x in tr if x in S2V]
    beta = 0.0
    if len(t2) >= 30:
        y2 = np.array([TGT[("UA", 1.0, q, k)] for q, k in t2], float)
        if 5 <= y2.sum() <= len(y2) - 5:
            off, xs = np.array([lg(x) for x in t2]), np.array([S2V[x] for x in t2])
            for _ in range(30):
                p = 1 / (1 + np.exp(-np.clip(off + beta * xs, -30, 30)))
                beta += ((xs * (y2 - p)).sum() - 3.0 * beta) / ((xs * xs * p * (1 - p)).sum() + 3.0)
    BETA[s] = beta
    for key in bystep[s]:
        PUA[key] = sig(lg(key) + (beta * S2V[key] if (S2_IN_SENS and key in S2V) else 0.0))


# ---------------------------------------------------------------- drapeaux
def pct_threshold(q):
    """Seuil de P(bouge) au pas s : centile q des P(bouge) des 12 mois d'instantanés précédant le mois du pas."""
    pmv = defaultdict(list)
    for (m, k), r in MON.items():
        if r["pm"] is not None:
            pmv[m].append(r["pm"])
    out = {}
    for s in range(NS):
        past = [v for n in range(1, 13) for v in pmv.get(month_add(mfs[s], -n), [])]
        if len([n for n in range(1, 13) if month_add(mfs[s], -n) in pmv]) >= 2:
            out[s] = float(np.percentile(past, q))
    return out


THR_RU, THR_GATE = pct_threshold(Q_RU), pct_threshold(Q_GATE)


def clipz(v):
    return float(np.clip(v, -1, 5)) / 2


FLAG, CUE, DUA = {}, {}, {}
for key, x in F.items():
    s, k = key
    r = ROWS[key]
    pu = PUA.get(key)
    pm = r["pm"]
    s2 = S2V.get(key)
    du = clipz(5 * x[0]) + clipz(x[6]) + (clipz(s2) if s2 is not None else 0.0)
    DUA[key] = du
    old = r["r_ru"] >= 6
    f = 0
    if pu is not None and s in THR_RU and pm >= THR_RU[s] and (1 - pu) >= P_RU:
        f |= 1
    # les deux branches exigent P(UA | bouge) ≥ 0,3 : jamais de « poussée possible » là où le sens russe est probable
    # (ni drapeau contradictoire avec la flèche russe, qui exige P(sens RU) ≥ 0,8)
    if pu is not None and pu >= P_UA and (old or (s in THR_GATE and pm >= THR_GATE[s] and du >= D_UA)):
        f |= 2
    if old:
        f |= 4
    FLAG[key] = f
    c = 0
    c |= 1 if x[6] <= -1 else 0                       # Telegram : activité russe signalée en excès (z ≤ −1)
    c |= 2 if x[6] >= 1 else 0                        # Telegram : activité ukrainienne signalée en excès (z ≥ 1)
    c |= 4 if s2 is not None and s2 <= -2 else 0      # S2 : impacts plus denses côté ukrainien (z ≥ 2 d'écart)
    c |= 8 if s2 is not None and s2 >= 2 else 0       # S2 : impacts plus denses côté russe
    c |= 16 if r["fa_ua"] >= 0.1 else 0               # combats derrière la ligne ukrainienne (asymétrie publiée)
    c |= 32 if r["fa_ru"] >= 0.1 else 0               # combats derrière la ligne russe
    c |= 64 if x[4] >= 0.5 else 0                     # gain russe récent (≥ 0,5 km sur 3 mois)
    c |= 128 if x[4] <= -0.5 else 0                   # reprise ukrainienne récente
    c |= 256 if s2 is not None else 0                 # S2 disponible
    c |= 512 if tg_ok(steps[s]) else 0                # Telegram disponible
    CUE[key] = c


# ---------------------------------------------------------------- validation interne (vérité : champ des cartes)
def auc(sc, y):
    sc, y = np.asarray(sc, float), np.asarray(y, float)
    if y.sum() == 0 or y.sum() == len(y):
        return None
    o = np.argsort(sc, kind="mergesort")
    rk = np.empty(len(sc)); rk[o] = np.arange(1, len(sc) + 1)
    n1 = y.sum()
    return round(float((rk[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1))), 3)


VAL = {}
for X in (1.0, 2.0):
    mv = [key for key in PUA if TGT.get(("UA", X) + key) is not None and TGT[("UA", X) + key] + TGT[("RU", X) + key] == 1 and steps[key[0]].isoformat() >= "2023-03"]
    VAL[f"auc_sens_{X:g}km"] = auc([PUA[x] for x in mv], [TGT[("UA", X) + x] for x in mv])
    VAL[f"n_sens_{X:g}km"] = len(mv)
    for side, bit in (("RU", 1), ("UA", 2), ("UA", 4)):
        keys = [key for key in F if (side, X) + key in TGT and steps[key[0]].isoformat() >= "2023-03"]
        y = np.array([TGT[(side, X) + x] for x in keys])
        a = np.array([bool(FLAG[x] & bit) for x in keys])
        nm = len({steps[s].strftime("%Y-%m") for s, _ in keys}) or 1
        tm = {(steps[s].strftime("%Y-%m"), k) for (s, k), aa in zip(keys, a) if aa}
        tf = {(steps[s].strftime("%Y-%m"), k) for (s, k), aa, yy in zip(keys, a, y) if aa and not yy}
        lab = {1: "fleche_ru", 2: "poussee_possible", 4: "ancienne_regle"}[bit]
        VAL[f"{lab}_{X:g}km"] = dict(precision=round(float((a & (y == 1)).sum() / max(a.sum(), 1)), 3),
                                     rappel=round(float((a & (y == 1)).sum() / max(y.sum(), 1)), 3),
                                     base=round(float(y.mean()), 4) if len(y) else None,
                                     alertes_mois=round(len(tm) / nm, 2), fausses_mois=round(len(tf) / nm, 2))
say("validation interne (vérité : champ des cartes, 2023-03+) : " + json.dumps(VAL, ensure_ascii=False))

# ---------------------------------------------------------------- sortie
out_steps, rows = [], []
for s, d in enumerate(steps):
    lst = []
    for k in sorted(TID):
        key = (s, k)
        if key not in F:
            continue
        pm = ROWS[key]["pm"]
        if pm < 0.10 and not FLAG[key] & 3:
            continue
        pu = PUA.get(key)
        lst.append([(k % 1000) * TILE + TILE // 2, (k // 1000) * TILE + TILE // 2, round(pm * 100),
                    None if pu is None else round((1 - pu) * 100), FLAG[key], CUE[key]])
    out_steps.append(d.isoformat())
    rows.append(lst)
meta = {
    "generated": dt.date.today().isoformat(), "tileCells": TILE, "stepDays": 10,
    "columns": ["colonne", "ligne", "P(bouge) %", "P(sens RU | bouge) % (null : pas encore appris)", "drapeaux", "indices"],
    "flags": {"1": "poussée russe probable", "2": "poussée possible (UA, règle améliorée)", "4": "ancienne règle « poussée possible » (comparaison, non affichée)"},
    "cues": {"1": "Telegram : activité russe signalée en excès (z ≤ −1)", "2": "Telegram : activité ukrainienne signalée en excès (z ≥ 1)",
             "4": "impacts S2 plus denses côté ukrainien", "8": "impacts S2 plus denses côté russe",
             "16": "combats derrière la ligne ukrainienne", "32": "combats derrière la ligne russe",
             "64": "gain russe récent (3 mois)", "128": "reprise ukrainienne récente (3 mois)",
             "256": "Sentinel-2 disponible", "512": "Telegram disponible"},
    "rules": {"fleche_ru": f"P(bouge) ≥ {Q_RU}e centile des 12 mois précédents ET P(sens RU) ≥ {P_RU}",
              "poussee_possible": f"P(UA | bouge) ≥ {P_UA} ET (ancienne règle OU (indice additif D_UA ≥ {D_UA} ET P(bouge) ≥ {Q_GATE}e centile))"},
    "sources": {"telegram": TG_SRC, "telegram_jusqua": TG_LAST, "sentinel2": (S2DECS[0] + " → " + S2DECS[-1]) if S2DECS else None},
    # validation walk-forward de l'étude (vérité : consensus des cartes à la décade ; ukraine-recherche/analyses/sens/RAPPORT.md)
    "validationEtude": {"verite": "consensus des cartes à la décade, 2023-03 → 2026-06 (eval_prod.py)",
                        "auc_sens_1km": 0.78, "auc_sens_2km": 0.85,
                        "fleche_ru_2km": {"precision": 0.43, "rappel": 0.16, "alertes_mois": 2.6, "fausses_mois": 1.6, "reference_pbouge_seule_precision": 0.28},
                        "fleche_ru_1km": {"precision": 0.59, "rappel": 0.12, "reference_pbouge_seule_precision": 0.44},
                        "poussee_possible_1km": {"precision": 0.11, "rappel": 0.03, "alertes_mois": 1.1, "base": 0.018, "ancienne_regle_precision": 0.06},
                        "poussee_possible_2km": {"precision": 0.08, "rappel": 0.04, "base": 0.009, "ancienne_regle_precision": 0.03}},
    "validationBuild": VAL,
    "steps": out_steps, "rows": rows,
}
js = "window.FRONT_SENS=" + json.dumps(meta, separators=(",", ":"), ensure_ascii=False) + ";"
open(os.environ.get("SENS_OUT") or os.path.join(SITE, "front_sens.js"), "w", encoding="utf-8").write(js)
nfl = sum(1 for v in FLAG.values() if v & 1)
npp = sum(1 for v in FLAG.values() if v & 2)
say(f"front_sens.js : {len(js) // 1024} Ko, {NS} décades ({out_steps[0]} → {out_steps[-1]}), flèches russes {nfl}, poussées possibles {npp}")
