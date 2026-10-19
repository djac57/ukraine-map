"""Moteur de fusion (production) : constantes et outils communs.

État latent : pour chaque tronçon k (tuile de 24 km traversée par la ligne) et chaque mois t, le DÉCALAGE δ_k(t) (km)
de la vraie ligne par rapport à la ligne 50 % des cartes (champ équilibré), le long de la normale ; δ > 0 : la vraie
ligne est plus avancée côté ukrainien (gain russe par rapport aux cartes). Une maille x de distance signée d(x) à la
ligne des cartes est russe si d(x) + δ > 0. Loi a posteriori discrète de δ sur une grille de −20 à +20 km (pas 0,5 km).
Copie de `ukraine-recherche/analyses/fusion/commun.py` (19/10/2026), socle lu dans les fichiers publiés du site.
"""
import numpy as np
from scipy.special import ndtr

from .socle import DATA, ROOT, SITE, lgt, socle  # noqa: F401

S = socle()

DGRID = np.arange(-20.0, 20.0001, 0.5)          # 81 hypothèses
ND = len(DGRID)
BW = 0.5
BEDGES = np.arange(-60.0, 60.0001, BW)
BCENT = 0.5 * (BEDGES[1:] + BEDGES[:-1])         # 240 classes de distance
NB = len(BCENT)
DMAX_TILE = 60.0                                  # mailles du tronçon prises en compte (|d| ≤ 60 km)
EVAL_FROM = "2023-01"
H = 2                                             # horizon de la vérité (consensus à t+2)


def bin_of(d):
    """Indice de classe de distance (−1 hors de ±60 km)."""
    d = np.asarray(d, np.float64)
    b = np.floor((np.nan_to_num(d, nan=1e9) - BEDGES[0]) / BW)
    b = np.where(np.isfinite(d) & (b >= 0) & (b < NB), b, -1)
    return b.astype(np.int64)


def logistic(X, y, lam=1.0, it=25, w_s=None):
    """Régression logistique ridge (IRLS), constante non pénalisée ; poids d'observation facultatifs."""
    X = np.c_[np.ones(len(X)), X]
    k = X.shape[1]
    w = np.zeros(k)
    ws = np.ones(len(y)) if w_s is None else w_s
    pen = lam * np.ones(k)
    pen[0] = 0.0
    for _ in range(it):
        z = np.clip(X @ w, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = X.T @ (ws * (y - p)) - pen * w
        Hm = (X * (ws * p * (1 - p))[:, None]).T @ X + np.diag(pen) + 1e-6 * np.eye(k)
        st = np.linalg.solve(Hm, g)
        w += st
        if np.abs(st).max() < 1e-6:
            break
    return w


def ridge(X, y, lam=10.0, w_s=None):
    X1 = np.c_[np.ones(len(X)), X]
    ws = np.ones(len(y)) if w_s is None else w_s
    pen = lam * np.eye(X1.shape[1])
    pen[0, 0] = 0
    A = (X1 * ws[:, None]).T @ X1 + pen
    return np.linalg.solve(A, (X1 * ws[:, None]).T @ y)


def norm_cdf(x):
    return ndtr(x)
