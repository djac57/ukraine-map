"""A priori de terrain dur sur les mouvements : franchir un fleuve (≥ 40 m³/s ou plan d'eau), un escarpement ou un canal
nommé (`site/barrieres_dures.js`, agent terrain) est peu probable en deux mois.

Pour un décalage δ, la bande balayée par la ligne entre d = 0 et d = −δ (côté ukrainien si δ > 0, russe si δ < 0)
contient n_k(δ) mailles barrières du tronçon :

    log L_terrain(δ) = −min(1, n_k(δ) / 3)          (pondéré par κ appris dans la fusion ; κ = 0 : sans effet)

Une rivière qui coupe le tronçon sur ≥ 6 km compte pleinement. Statique : aucune variable du futur.
"""
import base64
import json
import os

import numpy as np

from ..commun import DGRID, SITE, S


class Barrieres:
    KIND = "position"

    def __init__(self, name, label):
        self.name, self.label = name, label
        self.params = {}

    def prepare(self, C):
        s = open(os.path.join(SITE, "barrieres_dures.js"), encoding="utf-8").read()
        Hh = json.loads(s[s.index("=") + 1:].strip().rstrip(";"))
        v = np.frombuffer(base64.b64decode(Hh["packed"]), "<u4")
        g = np.zeros(S.NX * S.NY, bool)
        g[(v >> 3)[(v & 7) > 0]] = True
        self.bar = g[S.U][S.Zi]
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        self.LL = {}
        for t in C.months:
            z, r = C.cell_row[t]
            sd = np.nan_to_num(S.SDe[t], nan=999)[z]
            b = self.bar[z]
            n = len(C.rows[t])
            cnt = np.zeros((n, len(DGRID)))
            for j, dl in enumerate(DGRID):
                if dl > 0:
                    m = b & (sd <= 0) & (sd > -dl)
                elif dl < 0:
                    m = b & (sd > 0) & (sd <= -dl)
                else:
                    continue
                cnt[:, j] = np.bincount(r[m], minlength=n)
            self.LL[t] = -np.minimum(1.0, cnt / 3.0)

    def fit(self, t, C):
        return None

    def loglik(self, t, C):
        return self.LL[t]

    def formula(self, t=None):
        return "terrain : log L(δ) = −min(1, n(δ)/3), n = mailles barrières balayées par le décalage"
