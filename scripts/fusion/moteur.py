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

from .commun import DGRID, H, ND, S
from .contexte import contexte
from . import obs

WGRID = (0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
KGRID = (0.0, 0.5, 1.0, 2.0, 4.0)
KER = np.exp(-0.5 * ((DGRID[:, None] - DGRID[None, :]) / 0.5) ** 2)   # noyau de score (0,5 km)
FILT_Q = 1.0
FKER = np.exp(-0.5 * ((DGRID[:, None] - DGRID[None, :]) / FILT_Q) ** 2)

# version RETENUE (recherche, « complet revu ») : la liste officielle passe en modèle de pression
POSITION = ["feux", "ucdp", "pertes", "em_assauts", "em_frappes", "modru", "artillerie", "kab", "radar_bati", "tg"]
DYNAMIQUE = ["temoins", "memoire", "mouvement", "asymetrie", "impacts_s2", "tg_oriente", "activite", "liste_dyn"]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Moteur:
    def __init__(self, names=None):
        self.C = contexte()
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

    # ------------------------------------------------------------------------------------------------------------------
    def run(self, position, dynamique, filtre=True, terrain=True, placebo=None, seed=0):
        """position : noms des modèles de position ; dynamique : noms des modèles dynamiques ; placebo : None ou
        liste de noms dont les vraisemblances / variables sont PERMUTÉES entre tronçons du même mois."""
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
            rec[t] = {"lp": lp, "lf": lf, "LL": LLs, "d2": d2}
            w = self._learn(t, rec, pos, filtre)
            wts[t] = w
            L = lp + w.get("_filtre", 0.0) * lf + sum(w.get(n, 0.0) * LLs[n] for n in pos)
            L -= L.max(1, keepdims=True)
            P = np.exp(L)
            post[t] = P / P.sum(1, keepdims=True)
        self.cartes = cartes
        return {"post": post, "w": wts, "comp": comp, "pos": pos, "dyn": [m.name for m in dyn], "rec": rec, "dyn_models": dyn_eff}

    def _learn(self, t, rec, pos, filtre):
        past = [q for q in rec if t - 24 <= q <= t - H]
        w = {n: 0.0 for n in pos}
        if filtre:
            w["_filtre"] = 0.0
        if len(past) < 6:
            return w
        ok = [np.isfinite(rec[q]["d2"]) for q in past]
        lp = np.concatenate([rec[q]["lp"][o] for q, o in zip(past, ok)])
        lf = np.concatenate([rec[q]["lf"][o] for q, o in zip(past, ok)])
        LL = {n: np.concatenate([rec[q]["LL"][n][o] for q, o in zip(past, ok)]) for n in pos}
        d2 = np.concatenate([rec[q]["d2"][o] for q, o in zip(past, ok)])
        jt = np.clip(np.round((d2 - DGRID[0]) / 0.5).astype(int), 0, ND - 1)
        K = KER[jt]
        dec = np.concatenate([np.full(o.sum(), 0.5 ** ((t - q) / 12)) for q, o in zip(past, ok)])

        def score(ww):
            L = lp + ww.get("_filtre", 0.0) * lf + sum(ww[n] * LL[n] for n in pos if ww[n])
            L = L - L.max(1, keepdims=True)
            P = np.exp(L)
            P /= P.sum(1, keepdims=True)
            return float(np.sum(dec * np.log((P * K).sum(1) + 1e-12)))
        best = score(w)
        keys = (["_filtre"] if filtre else []) + pos
        for _ in range(2):
            for n in keys:
                for v in (KGRID if n == "terrain" else WGRID):
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


class _Perm:
    """Modèle dynamique dont les variables sont permutées entre tronçons (placebo)."""

    def __init__(self, m, perm):
        self.m, self.perm = m, perm
        self.name, self.mu_names, self.pi_names = m.name, m.mu_names, m.pi_names

    def features(self, t):
        F = self.m.features(t)
        return {k: v[self.perm[t]] for k, v in F.items()}
