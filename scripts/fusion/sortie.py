"""Sorties du moteur de fusion pour le site : `site/front_fusion.js` (champ + aires) et `site/fusion_explain.js`
(explication compacte par tronçon et par instantané). Formats décrits dans `scripts/build_fusion.py`."""
import json
import os

import numpy as np

from .commun import DGRID, S, SITE
from .champ import median_of
from .socle import decode_seq, encode_field, load_seq

# codes compacts des signaux (explication) ; libellés en français, à traduire côté affichage
CODES = {
    "Ds": "DeepState en avance sur les autres cartes", "Is": "ISW en avance", "Wk": "Wikipédia en avance",
    "Me": "biais et retard passés des cartes dans le secteur", "Vi": "vitesse récente de la ligne des cartes",
    "As": "combats derrière la ligne (asymétrie)", "S2": "impacts Sentinel-2 par côté", "To": "Telegram orienté (qui attaque)",
    "Li": "liste officielle des hostilités (pression)", "Ac": "intensité des combats (probabilité de mouvement)",
    "Aa": "assauts de l'état-major (pression)",
    "F": "feux FIRMS en excès", "U": "UCDP", "P": "pertes WarSpotting", "A": "assauts de l'état-major (position)",
    "Fa": "frappes aériennes de l'état-major", "M": "localités des bilans du MoD RF", "Ar": "menaces d'artillerie",
    "K": "alertes KAB", "R": "radar S1 (bâti modifié)", "T": "Telegram (points)", "Te": "terrain dur",
    "Mem": "mémoire du tronçon (mois précédent)",
}
POS_CODE = {"feux": "F", "ucdp": "U", "pertes": "P", "em_assauts": "A", "em_frappes": "Fa", "modru": "M", "artillerie": "Ar",
            "kab": "K", "radar_bati": "R", "tg": "T", "terrain": "Te", "_filtre": "Mem"}
DYN_CODE = {"memoire": "Me", "mouvement": "Vi", "asymetrie": "As", "impacts_s2": "S2", "s2_sens": "S2", "tg_oriente": "To",
            "liste_dyn": "Li", "activite": "Ac", "assauts_dyn": "Aa"}


def _q(P, a):
    return DGRID[np.argmax(np.cumsum(P, -1) >= a, -1)]


def _norm(L):
    L = L - L.max(-1, keepdims=True)
    P = np.exp(L)
    return P / P.sum(-1, keepdims=True)


def contributions(M, R, t):
    """Par ligne du mois t : {code: Δ km} (> 0 = vers le côté ukrainien, gain russe par rapport aux cartes).
    Modèles de position et filtre : médiane avec tous les termes − médiane sans le terme. Modèles dynamiques : part du
    modèle dans le centre μ de la composante « mouvement » (coefficient × écart à la moyenne d'apprentissage) × π ;
    les cartes séparées sont détaillées par carte (DeepState, ISW, Wikipédia)."""
    rec, w = R["rec"][t], R["w"][t]
    n = len(M.C.rows[t])
    L = rec["lp"] + w.get("_filtre", 0) * rec["lf"] + sum(w.get(k, 0) * rec["LL"][k] for k in R["pos"])
    base = median_of(_norm(L))
    out = [dict() for _ in range(n)]
    for k in R["pos"] + ["_filtre"]:
        wn = w.get(k, 0)
        if not wn:
            continue
        term = rec["lf"] if k == "_filtre" else rec["LL"][k]
        d = base - median_of(_norm(L - wn * term))
        for i in np.flatnonzero(np.abs(d) >= 0.05):
            out[i][POS_CODE.get(k, k)] = float(d[i])
    P = M.cartes.params.get(t)
    if P and "mu_m" in P:
        pi = R["comp"][t][0]
        coef = P["wm"][1:] / P["sd_m"]
        j = 0
        for m in R["dyn_models"]:
            X = m.features(t)["mu"]
            nc = X.shape[1]
            if not nc:
                continue
            part = coef[j:j + nc] * (X - P["mu_m"][j:j + nc])          # n × nc
            if m.name == "temoins":
                groups = (("Ds", [0, 3]), ("Is", [1, 4]), ("Wk", [2, 5]))
            else:
                groups = ((DYN_CODE.get(m.name, m.name), list(range(nc))),)
            for code, cols in groups:
                v = part[:, cols].sum(1) * pi
                for i in np.flatnonzero(np.abs(v) >= 0.05):
                    out[i][code] = out[i].get(code, 0.0) + float(v[i])
            j += nc
    return out


def explain(M, R, top=6):
    """{t: [[colonne, ligne, δ médian ×10, δ 5 % ×10, δ 95 % ×10, π %, [[code, Δ ×10], …]], …]}."""
    res = {}
    for t in M.C.months:
        P = R["post"][t]
        med, lo, hi = median_of(P), _q(P, 0.05), _q(P, 0.95)
        pi = R["comp"][t][0]
        ctr = contributions(M, R, t)
        rows = []
        for i, k in enumerate(M.C.rows[t]):
            code = int(S.tiles[k])
            cs = sorted(ctr[i].items(), key=lambda x: -abs(x[1]))[:top]
            rows.append([(code % 1000) * 12, (code // 1000) * 12, int(round(10 * med[i])), int(round(10 * lo[i])),
                         int(round(10 * hi[i])), int(round(100 * pi[i])),
                         [[c, int(round(10 * v))] for c, v in cs if abs(v) >= 0.05]])
        res[t] = rows
    return res


# ---------------------------------------------------------------------------------------------------------------------
def full_fields(F):
    """{t: p sur U} : champ du moteur sur Z, champ des cartes (vue équilibrée publiée) ailleurs."""
    EQ = decode_seq(load_seq(os.path.join(SITE, "front_equilibre.js")), S.nU)
    out = {}
    for t, pz in F.items():
        if t >= len(EQ) or EQ[t] is None:
            continue
        p = EQ[t].astype(np.float32) / 63.0
        p[S.Zi] = pz
        out[t] = p
    return out


def areas(p):
    """Mêmes surfaces que `build_front.metrics` : [espérance, médiane (p ≥ 0,5), zone grise 0,05-0,95, p > 0,95] en km²
    par périmètre (tout, horsCrimee, depuis2022), et « russie » [espérance, médiane] de la surface sous contrôle
    ukrainien dans la bande russe."""
    a2 = S.km ** 2
    pl = S.cellU
    ua = pl >= 0
    band = pl == -2
    rows = np.stack([p, (p >= 0.5), (p >= 0.05) & (p <= 0.95), (p > 0.95)], 1).astype(np.float64)
    cr = np.zeros(S.nU, bool); pr = np.zeros(S.nU, bool)
    cr[ua] = S.crimea[pl[ua]]; pr[ua] = S.pre[pl[ua]]
    out = {}
    for per, m in (("tout", ua), ("horsCrimee", ua & ~cr), ("depuis2022", ua & ~cr & ~pr)):
        out[per] = [int(round(x * a2)) for x in rows[m].sum(0)]
    pb = p[band]
    out["russie"] = [int(round(float(np.where(pb < 0.95, 1 - pb, 0).sum()) * a2)), int(round(float((pb < 0.5).sum()) * a2))]
    return out


def mc_interval(M, R, t, a, ndraw=200, block=2, seed=0):
    """Intervalle à 95 % de la surface attendue : tirages de δ par tronçon dans sa loi a posteriori, avec le même quantile
    pour les tronçons d'un même bloc de 48 km (2 × 2 tuiles, comme les blocs de 50 km du Monte Carlo publié) ; la surface
    varie de (δ − E δ) × longueur de ligne du tronçon (mailles à 0-2 km côté russe × 2 km)."""
    P = R["post"][t]
    rows = M.C.rows[t]
    cdf = np.cumsum(P, 1)
    Ed = P @ DGRID
    sd = np.nan_to_num(S.SDe[t], nan=999)
    cellZ = S.cellU[S.Zi]
    isua = cellZ >= 0
    cr = np.zeros(S.nZ, bool); pr = np.zeros(S.nZ, bool)
    cr[isua] = S.crimea[cellZ[isua]]; pr[isua] = S.pre[cellZ[isua]]
    edge = (sd > 0) & (sd <= 2.0) & isua
    blk = np.array([(int(S.tiles[k]) // 1000 // block) * 1000 + (int(S.tiles[k]) % 1000) // block for k in rows])
    ub, inv = np.unique(blk, return_inverse=True)
    rng = np.random.default_rng(seed + t)
    U_ = rng.uniform(0, 1, (ndraw, len(ub)))[:, inv]                       # ndraw × lignes
    D = np.empty_like(U_)
    for i in range(len(rows)):
        D[:, i] = DGRID[np.minimum(np.searchsorted(cdf[i], U_[:, i]), len(DGRID) - 1)]
    out = {}
    for per, m in (("tout", edge), ("horsCrimee", edge & ~cr), ("depuis2022", edge & ~cr & ~pr)):
        Lk = np.array([float(m[S.tile_cells[k]].sum()) * S.km for k in rows])
        dA = (D - Ed[None, :]) @ Lk
        lo, hi = np.percentile(dA, [2.5, 97.5])
        out[per] = [int(round(a[per][0] + lo)), int(round(a[per][0] + hi))]
    return out


def write_front(path, Fu, months_meta, meta):
    """`window.FRONT_FUSION = {...}` (méta, aires) PUIS `(window.FRONTP=window.FRONTP||{})["fusion"]=[...]` (en dernier :
    les lecteurs de séquences coupent au premier « ]= »)."""
    nO = S.nOrder
    arr, prev = [], None
    last = None
    for i in range(nO):
        if i < S.NM:
            q = Fu.get(i)
            if q is not None:
                last = q
        else:                                   # pas récents (kind « d ») : dernier mois calculé reporté
            q = last if S.FR["months"][i] else None
        if q is None:
            arr.append(None)
            continue
        qq = np.minimum(63, (np.asarray(q) * 63 + 0.5).astype(np.int64)).astype(np.uint8)
        arr.append(encode_field(qq, prev))
        prev = qq
    head = json.dumps(dict(meta, months=months_meta), ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.FRONT_FUSION=" + head + ";\n")
        f.write('(window.FRONTP=window.FRONTP||{})["fusion"]=' + json.dumps(arr, separators=(",", ":")) + ";\n")


def write_explain(path, ex, meta):
    months = [ex.get(i) for i in range(S.NM)]
    D = dict(meta, months=months)
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.FUSION_EXPLAIN=" + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";\n")
