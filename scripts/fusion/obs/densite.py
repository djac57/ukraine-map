"""Modèle d'observation « densité d'événements selon la distance signée à la ligne ».

Pour un signal s, au mois t, dans le tronçon k : chaque maille x a une distance signée d(x) à la ligne des cartes ;
si la vraie ligne est décalée de δ, sa distance à la vraie ligne est d(x) + δ. Les événements (comptés e(x), comptes
écrasés par log(1 + n) : une maille active vaut un témoin, pas cent) tombent selon une intensité

    λ_s(x) = A_{k,t} · f_s(d(x) + δ),     f_s(u) = ε_s + (1 − ε_s) · exp(−|u − m_s| / L_s^±)

(Laplace asymétrique : pic en m_s, décroissance L_s^− côté ukrainien (u < m_s) et L_s^+ côté russe, plancher ε_s
pour les événements sans lien avec la ligne). En conditionnant sur le nombre total d'événements du tronçon
(A_{k,t} s'élimine), la log-vraisemblance de δ est multinomiale :

    log L_s(δ) = Σ_x e(x) · log f_s(d(x) + δ)  −  N · log Σ_{x ∈ W} f_s(d(x) + δ)

W = mailles où la source PEUT exister (fenêtre : côté ukrainien pour les alertes, ≤ 15 km de la ligne pour le radar).
N est plafonné (N_eff ≤ 25 par tronçon) pour ne pas traiter des événements groupés comme indépendants.
(m, L^−, L^+, ε) sont appris chaque mois t par maximum de vraisemblance sur les mois q ≤ t − 2 (distances à la vraie
ligne, consensus à q + 2), demi-vie 12 mois, fenêtre 24 mois.
"""
import numpy as np
from scipy.optimize import minimize

from ..commun import BCENT, DGRID, H, S
from .. import donnees as DN


def al(u, m, Lm, Lp, eps):
    return eps + (1 - eps) * np.where(u < m, np.exp((u - m) / Lm), np.exp(-(u - m) / Lp))


class ProfilDistance:
    KIND = "position"

    def __init__(self, name, label, sources, transform=np.log1p, window=None, cap=25.0, win_months=24, half_life=12.0):
        self.name, self.label, self.sources = name, label, sources
        self.transform, self.window, self.cap = transform, window, cap
        self.win_months, self.half_life = win_months, half_life
        self.params = {}

    # ----- données -----
    def _values(self):
        A, cover = None, None
        for s in self.sources:
            a, c = DN.brut(s)
            A = a if A is None else A + a
            cover = c if cover is None else (cover | c)
        return A, cover

    def _win(self, C, t):
        if self.window == "ua":
            return C.isUAz
        if self.window == "radar":
            return np.nan_to_num(np.abs(S.SDe[t]), nan=999) <= 15
        return None

    def prepare(self, C):
        A, self.cover = self._values()
        self.Hm, self.Cm, self.Ht, self.Ct = {}, {}, {}, {}
        for t in C.months:
            if not self.cover[t]:
                continue
            w = A[t] if self.transform is None else self.transform(np.maximum(A[t], 0))
            He, Hc, Het, Hct = C.hist(t, w, self._win(C, t))
            n = He.sum(1)
            sc = np.where(n > self.cap, self.cap / np.maximum(n, 1e-9), 1.0) if self.cap else np.ones(len(n))
            self.Hm[t], self.Cm[t] = He * sc[:, None], Hc
            if Het is not None:
                nt = Het.sum(1)
                sct = np.where(nt > self.cap, self.cap / np.maximum(nt, 1e-9), 1.0) if self.cap else np.ones(len(nt))
                self.Ht[t], self.Ct[t] = Het * sct[:, None], Hct

    # ----- apprentissage -----
    @staticmethod
    def _unpack(v):
        return v[0], np.exp(v[1]), np.exp(v[2]), 1 / (1 + np.exp(-v[3]))

    def _nll(self, v, He, Hc, w):
        m, Lm, Lp, eps = self._unpack(v)
        f = al(BCENT, m, Lm, Lp, eps)
        if isinstance(He, tuple):          # forme agrégée (somme pondérée des He, w·n par ligne)
            heS, wn = He
            return -(float(heS @ np.log(f)) - float(wn @ np.log(np.maximum(Hc @ f, 1e-300)))) / max(float(wn.sum()), 1e-9)
        n = He.sum(1)
        ll = He @ np.log(f) - n * np.log(np.maximum(Hc @ f, 1e-300))
        return -float(np.sum(w * ll)) / max(float(np.sum(w * n)), 1e-9)

    def fit(self, t, C):
        past = [q for q in C.months if t - self.win_months <= q <= t - H and q in self.Ht]
        if len(past) < 4:
            return None
        He = np.concatenate([self.Ht[q] for q in past])
        Hc = np.concatenate([self.Ct[q] for q in past])
        w = np.concatenate([np.full(len(self.Ht[q]), 0.5 ** ((t - q) / self.half_life)) for q in past])
        keep = He.sum(1) > 0
        if keep.sum() < 10:
            return None
        He, Hc, w = He[keep], Hc[keep], w[keep]
        agg = ((w[:, None] * He).sum(0), w * He.sum(1))
        prev = self.params.get(t - 1)
        if prev:
            starts = [np.array([prev["m"], np.log(prev["Lm"]), np.log(prev["Lp"]), np.log(prev["eps"] / (1 - prev["eps"]))])]
        else:
            starts = [np.array([0.0, np.log(5.0), np.log(5.0), -2.0]), np.array([0.0, np.log(15.0), np.log(15.0), -4.0])]
        best = None
        bounds = [(-20, 20), (np.log(0.3), np.log(80)), (np.log(0.3), np.log(80)), (-9, 3)]
        for x0 in starts:
            r = minimize(self._nll, x0, args=(agg, Hc, w), method="L-BFGS-B", bounds=bounds)
            if best is None or r.fun < best.fun:
                best = r
        m, Lm, Lp, eps = self._unpack(best.x)
        # gain d'information par événement par rapport au profil plat (nats), mesure de netteté
        flat = self._nll(np.array([0.0, np.log(80), np.log(80), 3.0]), agg, Hc, w)
        self.params[t] = {"m": float(m), "Lm": float(Lm), "Lp": float(Lp), "eps": float(eps), "gain_nat": float(flat - best.fun),
                          "n_evt": float(He.sum())}
        return self.params[t]

    # ----- vraisemblance de δ -----
    def loglik(self, t, C):
        n = len(C.rows[t])
        p = self.params.get(t)
        if p is None or t not in self.Hm:
            return np.zeros((n, len(DGRID)))
        F = al(BCENT[:, None] + DGRID[None, :], p["m"], p["Lm"], p["Lp"], p["eps"])
        He, Hc = self.Hm[t], self.Cm[t]
        N = He.sum(1)
        LL = He @ np.log(F) - N[:, None] * np.log(np.maximum(Hc @ F, 1e-300))
        return LL - LL.max(1, keepdims=True)

    def formula(self, t=None):
        ts = sorted(self.params)
        p = self.params[t if t in self.params else ts[-1]] if ts else None
        if not p:
            return f"{self.label} : pas assez de données"
        return (f"{self.label} : f(u) = {p['eps']:.3f} + {1 - p['eps']:.3f}·exp(−|u − {p['m']:+.1f}|/L), "
                f"L⁻ = {p['Lm']:.1f} km (côté ukrainien), L⁺ = {p['Lp']:.1f} km (côté russe) ; gain {p['gain_nat'] * 1000:.0f} mnat/évt")
