"""Modèle d'observation « PRÉSENCE FILMÉE » (vidéos de combat géolocalisées) : EMPLACEMENT, DÉSACTIVÉ (20/10/2026).

Source prévue : GeoConfirmed (évaluation : ukraine-recherche/analyses/videos/RAPPORT.md). **Non utilisée** tant que
l'autorisation écrite n'est pas obtenue : le modèle n'est dans aucune liste de `build_fusion.py` (POS_PROD) et, sans le
fichier `data/videos_presence.csv`, il est vide (couverture nulle, vraisemblance plate). Règles du projet : aucune unité,
maille ≥ 2 km, J−7 minimum, agrégats seulement.

Fichier attendu : data/videos_presence.csv, colonnes `mois` (AAAA-MM, date de première publication ≤ instantané − 7 j),
`maille` (indice de la grille complète), `camp` (ua | ru : camp PRÉSENT au point filmé — la cible d'une frappe, pas son
auteur), `n` (fiches).

Modèle : indice PONCTUEL, rare et fort. Une présence du camp c au point x, ligne décalée de δ (u = d(x) + δ, > 0 côté
russe ; s = +1 pour une présence ukrainienne, −1 pour une présence russe) :

    P(fiche en x | δ) ∝ ε + (1 − ε) · Φ(−s · u / L)

(une présence ukrainienne est d'autant plus probable que le point est du côté ukrainien de la vraie ligne ; ε = part de
fiches fausses ou mal classées : 2,8 à 35 % selon le secteur dans le rapport ; L = imprécision de géolocalisation).
Poids d'une fiche croissant au-delà de 2 mailles de la ligne des cartes, w(d) = clip((|d| − 4 km) / 4 km, 0, 1) : près
de la ligne, une vidéo ne dit rien que les cartes ne disent déjà ; loin derrière la ligne adverse, elle contredit la
carte. log L(δ) = Σ_x w(d(x)) · n(x) · log[ε + (1 − ε) Φ(−s u / L)] − N · log Σ_W (…) (normalisation sur les mailles
où une fiche peut tomber, comme `densite.py`) ; ε et L appris en walk-forward (q ≤ t − 2, vérité à q + 2), poids
dans la fusion appris comme les autres modèles de position.
"""
import csv
import os

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtr

from ..commun import BCENT, DATA, DGRID, H, S


class PresenceFilmee:
    KIND = "position"
    ACTIF = False          # interrupteur : à passer à True SEULEMENT après autorisation de la source
    FICHIER = "videos_presence.csv"

    def __init__(self, name="videos", label="Présence filmée (vidéos géolocalisées) — désactivé"):
        self.name, self.label = name, label
        self.params = {}

    def prepare(self, C):
        n = len(S.months)
        self.cover = np.zeros(n, bool)
        self.E = {}
        path = os.path.join(DATA, self.FICHIER)
        if not (self.ACTIF and os.path.exists(path)):
            return
        A = {c: np.zeros((S.NM, S.nZ), np.float32) for c in ("ua", "ru")}
        for r in csv.DictReader(open(path, encoding="utf-8")):
            t = S.midx.get(r["mois"][:7])
            u = S.pos[int(r["maille"])] if 0 <= int(r["maille"]) < S.NX * S.NY else -1
            if t is None or u < 0 or S.zpos[u] < 0 or r["camp"] not in A:
                continue
            A[r["camp"]][t, S.zpos[u]] += float(r.get("n") or 1)
        for t in C.months:
            out = []
            for c, s in (("ua", 1.0), ("ru", -1.0)):
                sd = np.nan_to_num(S.SDe[t], nan=999)
                w = np.clip((np.abs(sd) - 4.0) / 4.0, 0, 1) * A[c][t]
                He, Hc, Het, Hct = C.hist(t, w)
                out.append((s, He, Hc, Het, Hct))
            self.E[t] = out
            self.cover[t] = any(o[1].sum() > 0 for o in out)

    @staticmethod
    def _f(u, s, L, eps):
        return eps + (1 - eps) * ndtr(-s * u / L)

    def fit(self, t, C):
        past = [q for q in C.months if t - 24 <= q <= t - H and q in self.E and self.E[q][0][3] is not None]
        if len(past) < 4:
            return None

        def nll(v):
            L, eps = np.exp(v[0]), 1 / (1 + np.exp(-v[1]))
            tot = 0.0
            for q in past:
                for s, _, _, Het, Hct in self.E[q]:
                    f = self._f(BCENT, s, L, eps)
                    n = Het.sum(1)
                    tot -= float((Het @ np.log(f) - n * np.log(np.maximum(Hct @ f, 1e-300))).sum())
            return tot
        r = minimize(nll, np.array([np.log(3.0), -2.0]), method="L-BFGS-B", bounds=[(np.log(0.3), np.log(30)), (-9, 3)])
        self.params[t] = {"L": float(np.exp(r.x[0])), "eps": float(1 / (1 + np.exp(-r.x[1])))}
        return self.params[t]

    def loglik(self, t, C):
        n = len(C.rows[t])
        p = self.params.get(t)
        if p is None or t not in self.E:
            return np.zeros((n, len(DGRID)))
        LL = np.zeros((n, len(DGRID)))
        for s, He, Hc, _, _ in self.E[t]:
            F = self._f(BCENT[:, None] + DGRID[None, :], s, p["L"], p["eps"])
            LL += He @ np.log(F) - He.sum(1)[:, None] * np.log(np.maximum(Hc @ F, 1e-300))
        return LL - LL.max(1, keepdims=True)

    def formula(self, t=None):
        return "présence filmée : DÉSACTIVÉ (autorisation de la source non obtenue)" if not self.ACTIF else \
            f"présence filmée : P ∝ ε + (1 − ε)·Φ(−s·u/L), {self.params.get(t)}"
