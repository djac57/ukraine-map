"""Contexte partagé par les modèles d'observation : pour chaque mois t, les tronçons traversés par la ligne (lignes du
moteur), les mailles de chaque tronçon à ≤ 60 km de la ligne des cartes, leurs classes de distance à la ligne des
CARTES du mois (application) et à la ligne VRAIE (consensus à t+2, apprentissage seulement), et les cibles δ vrai.

    C = contexte()
    C.rows[t]             -> liste des tronçons k du mois t
    C.cell_row[t]         -> (indices Z, ligne du tronçon) des mailles retenues
    C.bmap[t], C.btrue[t] -> classes de distance (cartes du mois / consensus à t+2 ; −1 sinon)
    C.hist(t, w, window)  -> (He_map, Hc_map, He_true, Hc_true) histogrammes par tronçon (lignes × classes)
"""
import numpy as np

from .commun import DMAX_TILE, H, NB, S, bin_of
from . import donnees as DN


class Contexte:
    def __init__(self):
        self.S = S
        self.SDT = DN.sd_truth()
        self.d2, self.d1 = DN.delta_true()
        self.dmap = DN.map_shift()
        self.rows, self.cell_row, self.bmap, self.btrue, self.rpos = {}, {}, {}, {}, {}
        for t in S.built:
            ks = sorted(k for (q, k) in S.Y if q == t)
            if not ks:
                continue
            rp = np.full(S.nT, -1, np.int64)
            rp[ks] = np.arange(len(ks))
            sd = np.nan_to_num(S.SDe[t], nan=999)
            z = np.flatnonzero((rp[S.tinv] >= 0) & (np.abs(sd) <= DMAX_TILE))
            self.rows[t], self.rpos[t] = ks, rp
            self.cell_row[t] = (z, rp[S.tinv[z]])
            self.bmap[t] = bin_of(sd[z])
            if t + H in S.built:
                self.btrue[t] = bin_of(self.SDT[t + H][z])
        self.months = sorted(self.rows)
        self.isUAz = S.isUA[S.Zi]

    def hist(self, t, w, window=None):
        """w : valeurs par maille de Z au mois t (poids des événements) ; window : masque Z des mailles où la source
        peut exister (normalisation). Histogrammes (lignes × NB) sur les distances aux cartes et, si la vérité est
        connue, à la ligne vraie."""
        z, r = self.cell_row[t]
        n = len(self.rows[t])
        ww = np.asarray(w)[z]
        win = np.ones(len(z), bool) if window is None else np.asarray(window)[z]
        out = []
        for b in (self.bmap[t], self.btrue.get(t)):
            if b is None:
                out += [None, None]
                continue
            ok = b >= 0
            He = np.zeros((n, NB)); Hc = np.zeros((n, NB))
            np.add.at(He, (r[ok], b[ok]), ww[ok] * win[ok])
            np.add.at(Hc, (r[ok & win], b[ok & win]), 1.0)
            out += [He, Hc]
        return tuple(out)

    def tile_mean(self, t, xZ, side=None, maxd=20.0):
        """Moyenne par tronçon du mois (ordre de self.rows[t]) à ≤ maxd km de la ligne des cartes."""
        m, n = S.tile_mean(t, xZ, side, maxd)
        return m[self.rows[t]], n[self.rows[t]]


_C = None


def contexte():
    global _C
    if _C is None:
        _C = Contexte()
    return _C
