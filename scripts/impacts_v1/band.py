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


def distances():
    F, _ = fl.fields("equilibre")
    cp = fl.cell_place()
    dom = cp >= 0
    D = {m: distance_transform_edt(~line_of(g, dom)) * fl.SIZE / 1000.0 for m, g in F.items()}
    return F, cp, D
