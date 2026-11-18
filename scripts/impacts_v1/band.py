"""Faisceau du front : distance de chaque maille à la ligne p = 0,5 (vue equilibre), par mois.

Porté de ukraine-recherche/sar/band.py (E3b) — SEULE la fonction distances() est nécessaire à l'agrégation v1
(la génération de tiles.json, qui importait `zones`, est écartée : tiles.json est committé dans ce dossier).
"""
import os
import sys

import numpy as np
from scipy.ndimage import distance_transform_edt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import frontlib as fl  # noqa: E402

TILE = 20


def line_of(g, dom):
    ru = (g >= 0.5) & dom
    ua = (g < 0.5) & dom
    line = np.zeros_like(dom)
    line[:, :-1] |= (ru[:, :-1] & ua[:, 1:]) | (ua[:, :-1] & ru[:, 1:])
    line[:, 1:] |= (ru[:, 1:] & ua[:, :-1]) | (ua[:, 1:] & ru[:, :-1])
    line[:-1, :] |= (ru[:-1, :] & ua[1:, :]) | (ua[:-1, :] & ru[1:, :])
    line[1:, :] |= (ru[1:, :] & ua[:-1, :]) | (ua[1:, :] & ru[:-1, :])
    return line


BORDER_BBOX = (33.5, 50.4, 35.6, 51.6)   # copie de crateres_v2/s2lib.BORDER_BBOX (lon0, lat0, lon1, lat1)
_BORDER = {}


def border_line(cp, g, bbox=BORDER_BBOX):
    """Mailles UA (domaine, p < 0,5) voisines d'une maille russe hors domaine (cp == -2), dans la bbox."""
    dom = cp >= 0
    rus = cp == -2
    nb = np.zeros_like(dom)
    nb[:, :-1] |= rus[:, 1:]; nb[:, 1:] |= rus[:, :-1]; nb[:-1, :] |= rus[1:, :]; nb[1:, :] |= rus[:-1, :]
    if bbox not in _BORDER:
        from pyproj import Transformer
        jj, ii = np.nonzero(dom & nb)
        lon, lat = Transformer.from_crs(fl.PROJ4, "EPSG:4326", always_xy=True).transform(
            fl.X0 + (ii + 0.5) * fl.SIZE, fl.Y1 - (jj + 0.5) * fl.SIZE)
        keep = (lon >= bbox[0]) & (lon <= bbox[2]) & (lat >= bbox[1]) & (lat <= bbox[3])
        m = np.zeros_like(dom)
        m[jj[keep], ii[keep]] = True
        _BORDER[bbox] = m
    return _BORDER[bbox] & (g < 0.5)


def distances():
    F, _ = fl.fields("equilibre")
    cp = fl.cell_place()
    dom = cp >= 0
    # + frontière Soumy–Koursk (18/11/2026), même règle que scripts/crateres_v2/s2lib.border_line
    D = {m: distance_transform_edt(~(line_of(g, dom) | border_line(cp, g))) * fl.SIZE / 1000.0 for m, g in F.items()}
    return F, cp, D
