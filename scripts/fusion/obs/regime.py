"""v4 (20/10/2026) : RÉGIME du tronçon, mélange DOUX (« gating ») entre deux jeux de poids : calme (figé ou frémissant) et
OFFENSIVE EN COURS. g_k(t) ∈ [0 ; 1] est calculé à la date t avec les seules informations connues à t :

  mvt   mouvement SOUTENU du consensus des cartes dans le tronçon et ses voisins :
            O_k(t) = Σ_{j=0..5} ρ^j · |Y_{t−j,k} − Y_{t−j−1,k}|  (+ 0,5 × moyenne des voisins),   g = 1 − exp(−O / O₀)
        (Y = profondeur de la surface russe du consensus du tronçon, km ; ρ = 0,6 ; O₀ fixé sur 2023-2024) ;
  pm    P(mouvement) PUBLIÉE (`FRONT.months[t].pm`, tuile de 24 km, apprise en walk-forward par `build_front.py`) ;
  mix   « ou » doux des deux : g = 1 − (1 − g_mvt)(1 − pm).

Le secteur FIGÉ (aucune maille n'a changé depuis 6 mois) est déjà traité à part (`figes.py`) ; « frémissant » est la
zone intermédiaire de g. Le régime ne dit rien du SENS : il ne change que la façon de lire les témoins (poids des modèles
de position, du filtre, et coefficients de la composante « mouvement » des cartes).
"""
import numpy as np

from ..commun import S, TILE


class Regime:
    def __init__(self, mode="mvt", O0=2.0, rho=0.6, nlag=6, w_nb=0.5):
        self.mode, self.O0, self.rho, self.nlag, self.w_nb = mode, O0, rho, nlag, w_nb
        self.g = {}

    def _pm(self, t, rows):
        mo = S.FR["months"][t] if t < len(S.FR["months"]) else None
        out = np.zeros(len(rows))
        if not mo or "pm" not in mo or TILE != 12:
            return out
        d = {(int(j0) // 12) * 1000 + int(i0) // 12: p / 100.0 for i0, j0, p in mo["pm"]}
        return np.array([d.get(int(S.tiles[k]), 0.0) for k in rows])

    def _mvt(self, t, rows):
        code = {int(c): i for i, c in enumerate(S.tiles)}

        def O(k):
            s = 0.0
            for j in range(self.nlag):
                a, b = S.Y.get((t - j, k)), S.Y.get((t - j - 1, k))
                if a is not None and b is not None:
                    s += self.rho ** j * abs(a - b)
            return s
        out = []
        for k in rows:
            c = int(S.tiles[k])
            a, b = c // 1000, c % 1000
            nb = [code[(a + x) * 1000 + b + y] for x in (-1, 0, 1) for y in (-1, 0, 1)
                  if (x, y) != (0, 0) and (a + x) * 1000 + b + y in code]
            o = O(k) + (self.w_nb * float(np.mean([O(kk) for kk in nb])) if nb else 0.0)
            out.append(o)
        return 1.0 - np.exp(-np.array(out) / self.O0)

    def prepare(self, C):
        for t in C.months:
            rows = C.rows[t]
            if self.mode == "pm":
                g = self._pm(t, rows)
            elif self.mode == "mix":
                g = 1 - (1 - self._mvt(t, rows)) * (1 - self._pm(t, rows))
            else:
                g = self._mvt(t, rows)
            self.g[t] = np.clip(g, 0.0, 1.0)
        return self

    def __call__(self, t, perm=None):
        g = self.g[t]
        return g if perm is None else g[perm]
