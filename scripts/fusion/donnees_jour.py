"""Données QUOTIDIENNES causales du moteur de fusion (refit des poids au jour, docs/ETAT.md § 6, 22/10/2026).

Lit les agrégats quotidiens `data/daily/<source>_<année>.csv.gz` (maille de 2 km, par jour, par source ; colonnes
`date, [date_pub,] maille, …`) et les feux `data/viirs_api_<année>.csv` (FIRMS, par jour), SANS réseau et SANS relire
le brut. Chaque signal de POSITION produit, pour un jour d, un champ CAUSAL sur Z :

    champ_d(z) = Σ_{évt : date_pub ≤ d, d − W ≤ date ≤ d}  c · 0,5^((d − date) / hl)

  - causalité stricte : un événement n'entre que s'il est PUBLIÉ à d (date_pub ≤ d ; UCDP arrive ~2 mois après,
    WarSpotting en retard : au moment où on le voit, d − date est grand, donc son poids est faible — le retard est
    porté par la donnée, pas forcé) ;
  - moyenne mobile EXPONENTIELLE causale (demi-vie hl jours, conversion du pas mensuel : ~21 j ≈ 0,7 mois ; FUSION_MA_HL
    la règle) : remplace l'agrégat mensuel par un lissage glissant qui met en avant les jours récents.

Signaux disponibles au pas quotidien : feux (FIRMS ; viirs, 2026 seulement en local), ucdp (n_evenements), pertes
(WarSpotting n_pertes), em_assauts, em_frappes (état-major), modru (bilans MoD RF), tg_att_ua/att_ru/pres_ua/pres_ru
(Telegram, indicatrice par maille). Artillerie, KAB, radar S1 : pas d'agrégat quotidien → écartés du refit au jour
(contribution mensuelle modeste ; voir le rapport).
"""
import csv
import datetime as dt
import glob
import gzip
import os

import numpy as np

from .commun import S
from .socle import DATA

HL = float(os.environ.get("FUSION_MA_HL", "21.0"))   # demi-vie de la MA causale (jours) ; conversion du pas mensuel
WIN_MA = int(os.environ.get("FUSION_MA_WIN", "90"))  # fenêtre de la MA (jours) : au-delà, poids < 2^-4,3

# (fichier daily, colonne de compte, colonne date_pub | None, transform) par signal de position quotidien
DAILY = {
    "ucdp": ("ucdp", "n_evenements", "date_pub", "log1p"),
    "pertes": ("warspotting", "n_pertes", "date_pub_min", "log1p"),
    "em_assauts": ("etatmajor", "assauts", "date_pub", "log1p"),
    "em_frappes": ("etatmajor", "frappes_aeriennes", "date_pub", "log1p"),
    "modru": ("modru", "localites", "date_pub", "log1p"),
    "tg_att_ua": ("telegram", "n_att_ua", None, "ind"),
    "tg_att_ru": ("telegram", "n_att_ru", None, "ind"),
    "tg_pres_ua": ("telegram", "n_presence_ua", None, "ind"),
    "tg_pres_ru": ("telegram", "n_presence_ru", None, "ind"),
}
# regroupement en signaux du moteur (mêmes noms que POS_PROD / PointsTelegram.sub)
GROUPES = {
    "feux": ["feux"], "ucdp": ["ucdp"], "pertes": ["pertes"], "em_assauts": ["em_assauts"],
    "em_frappes": ["em_frappes"], "modru": ["modru"],
    "tg": ["tg_att_ua", "tg_att_ru", "tg_pres_ua", "tg_pres_ru"],
}

_EV = {}


def _ord(s):
    return dt.date.fromisoformat(s[:10]).toordinal()


def _maille_to_z(maille):
    """maille = j·700 + i (= indice de grille complète) → indice Z (−1 hors zone)."""
    u = S.u_of_cell(np.asarray(maille, np.int64))
    z = np.full(len(u), -1, np.int64)
    ok = u >= 0
    z[ok] = S.zpos[u[ok]]
    return z


def _charger_daily(sig):
    """(date_ord, pub_ord, z, c) float/int, triés par date_ord, pour un signal quotidien élémentaire."""
    src, col, pubcol, _ = DAILY[sig]
    dates, pubs, mailles, cs = [], [], [], []
    for path in sorted(glob.glob(os.path.join(DATA, "daily", f"{src}_[12]*.csv.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                v = r.get(col)
                if not v or float(v) == 0:
                    continue
                dates.append(_ord(r["date"]))
                pubs.append(_ord(r[pubcol]) if pubcol else _ord(r["date"]))
                mailles.append(int(r["maille"]))
                cs.append(float(v))
    if not dates:
        return None
    da = np.array(dates, np.int32)
    o = np.argsort(da, kind="stable")
    z = _maille_to_z(np.array(mailles, np.int64)[o])
    keep = z >= 0
    return (da[o][keep], np.array(pubs, np.int32)[o][keep], z[keep], np.array(cs, np.float32)[o][keep])


def _charger_feux():
    """Feux FIRMS quotidiens (data/viirs_api_<année>.csv ; date = date_pub, publication quasi temps réel)."""
    dates, mailles = [], []
    for path in sorted(glob.glob(os.path.join(DATA, "viirs_api_[12]*.csv"))):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = r.get("date") or r.get("acq_date")
                if not d:
                    continue
                c = S.cell_of(np.array([float(r["lon"])]), np.array([float(r["lat"])]))[0]
                dates.append(_ord(d))
                mailles.append(int(c))
    if not dates:
        return None
    da = np.array(dates, np.int32)
    o = np.argsort(da, kind="stable")
    z = _maille_to_z(np.array(mailles, np.int64)[o])
    keep = z >= 0
    c = np.ones(len(z), np.float32)
    return (da[o][keep], da[o][keep], z[keep], c[keep])


def evenements(sig):
    """Événements d'un signal élémentaire (cache)."""
    if sig not in _EV:
        _EV[sig] = _charger_feux() if sig == "feux" else _charger_daily(sig)
    return _EV[sig]


def champ_causal(sig, d_ord, hl=None, win=None):
    """Champ causal sur Z (float32) du signal élémentaire `sig` au jour d_ord. 0 si source absente."""
    hl = HL if hl is None else hl
    win = WIN_MA if win is None else win
    ev = evenements(sig)
    out = np.zeros(S.nZ, np.float32)
    if ev is None:
        return out
    da, pub, z, c = ev
    lo = np.searchsorted(da, d_ord - win)
    hi = np.searchsorted(da, d_ord, side="right")
    if hi <= lo:
        return out
    dd, pp, zz, cc = da[lo:hi], pub[lo:hi], z[lo:hi], c[lo:hi]
    m = pp <= d_ord                                        # causalité : publié à d
    if not m.any():
        return out
    w = cc[m] * np.exp2(-(d_ord - dd[m]) / hl)
    np.add.at(out, zz[m], w)
    return out


def _transform(sig):
    return "log1p" if sig == "feux" else DAILY[sig][3]


def champ_groupe(nom, d_ord):
    """Poids causal d'un signal du moteur (somme des élémentaires) sur Z : log1p de la MA pour les densités (un amas
    actif ≈ un témoin, pas cent), MA brute pour Telegram (indicatrices décroissantes = fraîcheur des mentions)."""
    out = np.zeros(S.nZ, np.float32)
    for sig in GROUPES[nom]:
        a = champ_causal(sig, d_ord)
        if _transform(sig) == "log1p":
            a = np.log1p(np.maximum(a, 0))
        out = out + a.astype(np.float32)
    return out


def disponible_feux():
    return evenements("feux") is not None
