"""Moteur de fusion unique : grille bayésienne 1D par tronçon, filtrée dans le temps (copie de
`ukraine-recherche/analyses/fusion/moteur.py`, 19/10/2026).

Pour chaque tronçon k et chaque mois t (walk-forward strict : tout ce qui est appris à t ne voit que q ≤ t − 2) :

    log π_t(δ) = log p(δ | cartes, dynamique)                           a priori des cartes (témoin biaisé, en retard)
               + w_f · log [π_{t−1} décalé de −ΔY_cartes ⊗ N(0, 1 km²)]  filtre temporel (mémoire du tronçon)
               + Σ_s w_s · log L_s(δ)                                   modèles d'observation de position
               + κ · log L_terrain(δ)                                   terrain dur
               − constante

Les poids (w_f, w_s, κ) sont appris chaque mois par montée de coordonnées sur le score logarithmique de δ vrai
(consensus à q + 2) des tronçons-mois q ∈ [t − 24, t − 2], grille {0 ; 0,1 ; 0,2 ; 0,35 ; 0,5 ; 0,75 ; 1}.
"""
import time

import numpy as np
from scipy import sparse

from .commun import DGRID, H, ND, S
from .contexte import contexte
from . import obs

WGRID = (0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
KGRID = (0.0, 0.5, 1.0, 2.0, 4.0)
KER = np.exp(-0.5 * ((DGRID[:, None] - DGRID[None, :]) / 0.5) ** 2)   # noyau de score (0,5 km)
FILT_Q = 1.0
CGRID = (0.0, 0.1, 0.2, 0.35, 0.5)                                    # couplage entre tronçons voisins (v2)
FKER = np.exp(-0.5 * ((DGRID[:, None] - DGRID[None, :]) / FILT_Q) ** 2)

# version RETENUE (recherche, « complet revu ») : la liste officielle passe en modèle de pression
POSITION = ["feux", "ucdp", "pertes", "em_assauts", "em_frappes", "modru", "artillerie", "kab", "radar_bati", "tg"]
DYNAMIQUE = ["temoins", "memoire", "mouvement", "asymetrie", "impacts_s2", "tg_oriente", "activite", "liste_dyn"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Moteur:
    def __init__(self, names=None):
        self.C = contexte()
        self._NB = {}
        cat = obs.catalogue()
        self.models = {m.name: m for m in cat if names is None or m.name in names or m.name == "terrain"}
        self.LL = {}
        t0 = time.time()
        for m in self.models.values():
            m.prepare(self.C)
        log(f"modèles préparés ({time.time() - t0:.0f} s)")
        t0 = time.time()
        for n, m in self.models.items():
            if m.KIND != "position":
                continue
            self.LL[n] = {}
            for t in self.C.months:
                m.fit(t, self.C)
                self.LL[n][t] = m.loglik(t, self.C)
        log(f"profils appris en walk-forward ({time.time() - t0:.0f} s)")

    def poids_figes(self, **kw):
        """Poids des cartes durablement contredites dans les secteurs figés (`obs/figes.PoidsCartesFiges`), pour le champ."""
        F = obs.PoidsCartesFiges(**kw)
        F.prepare(self.C, self.models["temoins"])
        return F

    def figes(self, **kw):
        """Biais des secteurs figés (`obs/figes.py`), préparé sur les décalages des cartes séparées (modèle « temoins »)."""
        F = obs.SecteursFiges(**kw)
        F.prepare(self.C, self.models["temoins"])
        return F

    # ------------------------------------------------------------------------------------------------------------------
    def neighbours(self, t):
        """Matrice creuse (lignes du mois t × lignes du mois t) de moyenne sur les tronçons VOISINS (8-voisinage,
        diagonales 0,5, sans le tronçon lui-même), normalisée par ligne ; lignes sans voisin : nulles."""
        if t not in self._NB:
            rows = self.C.rows[t]
            code = {int(S.tiles[k]): i for i, k in enumerate(rows)}
            I, J, V = [], [], []
            for i, k in enumerate(rows):
                a, b = int(S.tiles[k]) // 1000, int(S.tiles[k]) % 1000
                nb = [(code[(a + x) * 1000 + b + y], 1.0 if x == 0 or y == 0 else 0.5)
                      for x in (-1, 0, 1) for y in (-1, 0, 1) if (x, y) != (0, 0) and (a + x) * 1000 + b + y in code]
                tot = sum(w for _, w in nb)
                for j, w in nb:
                    I.append(i); J.append(j); V.append(w / tot)
            n = len(rows)
            self._NB[t] = sparse.csr_matrix((V, (I, J)), shape=(n, n))
        return self._NB[t]

    def run(self, position, dynamique, filtre=True, terrain=True, placebo=None, seed=0, figes=None, couplage=False):
        """position : noms des modèles de position ; dynamique : noms des modèles dynamiques ; placebo : None ou
        liste de noms dont les vraisemblances / variables sont PERMUTÉES entre tronçons du même mois ("figes" : biais
        des secteurs figés permutés) ; figes : None ou `obs.SecteursFiges` préparé (centre de la composante stable
        décalé de ρ·B_k dans les secteurs figés, ρ appris) ; couplage : log π_k ← log π_k + c · moyenne des voisins
        (c appris), pour des tronçons courts (v2)."""
        C = self.C
        rng = np.random.default_rng(seed)
        pos = [n for n in position if n in self.LL and n != "terrain"]
        if terrain and "terrain" in self.LL:
            pos = pos + ["terrain"]
        dyn = [self.models[n] for n in dynamique]
        perm = {t: rng.permutation(len(C.rows[t])) for t in C.months} if placebo else {}
        dyn_eff = [_Perm(m, perm) if placebo and m.name in placebo else m for m in dyn]
        rec, post, wts, comp = {}, {}, {}, {}
        cartes = obs.TemoinCartes()
        for t in C.months:
            cartes.fit(t, C, dyn_eff)
            lp = cartes.logprior(t, C, dyn_eff)
            lpr = None
            if figes is not None:
                sh = figes.shift(t, perm[t] if placebo and "figes" in placebo else None)
                lpr = np.stack([lp] + [cartes.logprior(t, C, dyn_eff, shift=r * sh) for r in obs.FIGES_RGRID[1:]])
            comp[t] = cartes.components(t, C, dyn_eff)[:2]
            LLs = {}
            for n in pos:
                L = self.LL[n][t]
                LLs[n] = L[perm[t]] if placebo and n in placebo else L
            lf = np.zeros_like(lp)
            if filtre and (t - 1) in post:
                prev_rows = {k: i for i, k in enumerate(C.rows[t - 1])}
                for i, k in enumerate(C.rows[t]):
                    j = prev_rows.get(k)
                    if j is None:
                        continue
                    sh = C.dmap.get((t, k), 0.0)
                    pv = np.interp(DGRID + sh, DGRID, post[t - 1][j], left=0, right=0)
                    pv = FKER @ pv
                    lf[i] = np.log(pv / max(pv.sum(), 1e-300) + 1e-6)
            d2 = np.array([C.d2.get((t, k), np.nan) for k in C.rows[t]])
            rec[t] = {"lp": lp, "lpr": lpr, "lf": lf, "LL": LLs, "d2": d2, "NB": self.neighbours(t) if couplage else None}
            w = self._learn(t, rec, pos, filtre, figes is not None, couplage)
            wts[t] = w
            post[t] = _norm(self.logpost(rec[t], w, pos))
        self.cartes = cartes
        return {"post": post, "w": wts, "comp": comp, "pos": pos, "dyn": [m.name for m in dyn], "rec": rec, "dyn_models": dyn_eff}

    @staticmethod
    def logpost(r, w, pos, drop=None):
        """Log-loi a posteriori (non normalisée) d'un mois (r = rec[t]) pour les poids w ; drop : terme retiré
        (explication : nom d'un modèle de position, "_filtre", "_figes" ou "_couplage")."""
        ww = dict(w)
        if drop:
            ww[drop] = 0.0
        lp = r["lp"] if r.get("lpr") is None else r["lpr"][int(round(ww.get("_figes", 0.0) / 0.25))]
        L = lp + ww.get("_filtre", 0.0) * r["lf"] + sum(ww.get(n, 0.0) * r["LL"][n] for n in pos if ww.get(n, 0.0))
        c = ww.get("_couplage", 0.0)
        if c and r.get("NB") is not None:      # mélange géométrique avec la moyenne des voisins (netteté conservée)
            L = _lnorm(L)
            has = np.asarray(r["NB"].sum(1)).ravel() > 0
            L = np.where(has[:, None], (1 - c) * L + c * (r["NB"] @ L), L)
        return L

    def _learn(self, t, rec, pos, filtre, figes=False, couplage=False):
        past = [q for q in rec if t - 24 <= q <= t - H]
        w = {n: 0.0 for n in pos}
        if filtre:
            w["_filtre"] = 0.0
        if figes:
            w["_figes"] = 0.0
        if couplage:
            w["_couplage"] = 0.0
        if len(past) < 6:
            return w
        ok = np.concatenate([np.isfinite(rec[q]["d2"]) for q in past])
        lp = np.concatenate([rec[q]["lp"] for q in past])
        lpr = np.concatenate([rec[q]["lpr"] for q in past], 1) if figes else None
        lf = np.concatenate([rec[q]["lf"] for q in past])
        LL = {n: np.concatenate([rec[q]["LL"][n] for q in past]) for n in pos}
        NB = sparse.block_diag([rec[q]["NB"] for q in past], format="csr") if couplage else None
        d2 = np.concatenate([rec[q]["d2"] for q in past])[ok]
        jt = np.clip(np.round((d2 - DGRID[0]) / 0.5).astype(int), 0, ND - 1)
        K = KER[jt]
        dec = np.concatenate([np.full(len(rec[q]["d2"]), 0.5 ** ((t - q) / 12)) for q in past])[ok]
        big = {"lp": lp, "lpr": lpr, "lf": lf, "LL": LL, "NB": NB}
        if not couplage:      # sans couplage, seules les lignes de vérité connue comptent (comme v1)
            big = {"lp": lp[ok], "lpr": None if lpr is None else lpr[:, ok], "lf": lf[ok], "LL": {n: v[ok] for n, v in LL.items()}, "NB": None}

        def score(ww):
            L = self.logpost(big, ww, pos)
            if couplage:
                L = L[ok]
            L = L - L.max(1, keepdims=True)
            P = np.exp(L)
            P /= P.sum(1, keepdims=True)
            return float(np.sum(dec * np.log((P * K).sum(1) + 1e-12)))
        best = score(w)
        keys = (["_filtre"] if filtre else []) + (["_figes"] if figes else []) + (["_couplage"] if couplage else []) + pos
        for _ in range(2):
            for n in keys:
                grid = KGRID if n == "terrain" else obs.FIGES_RGRID if n == "_figes" else CGRID if n == "_couplage" else WGRID
                for v in grid:
                    if v == w[n]:
                        continue
                    w2 = dict(w); w2[n] = v
                    s = score(w2)
                    if s > best + 1e-6:
                        best, w = s, w2
        return w

    # ------------------------------------------------------------------------------------------------------------------
    _VL = {}

    def vloc(self, t):
        """Vitesse LOCALE de la ligne des cartes par maille (km/mois, > 0 = gain russe) : (d_t − d_{t−2}) / 2, à ≤ 20 km
        de la ligne, bornée à ±10 km, lissée (σ = 2 mailles)."""
        if t not in self._VL:
            v = np.zeros(S.nZ, np.float32)
            if (t - 2) in S.built:
                a, b = np.nan_to_num(S.SDe[t], nan=0), np.nan_to_num(S.SDe[t - 2], nan=0)
                v = np.clip((a - b) / 2, -10, 10) * (np.abs(a) <= 20)
                v = S.smooth(S.full(v), 2.0)[S.Zi]
            self._VL[t] = v.astype(np.float32)
        return self._VL[t]


def _lnorm(L):
    m = L.max(1, keepdims=True)
    return L - (m + np.log(np.exp(L - m).sum(1, keepdims=True)))


def _norm(L):
    P = np.exp(L - L.max(1, keepdims=True))
    return P / P.sum(1, keepdims=True)


class _Perm:
    """Modèle dynamique dont les variables sont permutées entre tronçons (placebo)."""

    def __init__(self, m, perm):
        self.m, self.perm = m, perm
        self.name, self.mu_names, self.pi_names = m.name, m.mu_names, m.pi_names

    def features(self, t):
        F = self.m.features(t)
        return {k: v[self.perm[t]] for k, v in F.items()}

    def avail(self, t):
        return self.m.avail(t)
