"""Nowcast : les signaux de combat déplacent p (scripts/build_front.py l'appelle après le modèle principal).

Les cartes ont du retard, les combats sont immédiats. Validation hors ligne (walk-forward, 2022-10 → 2026-07, vérité =
consensus des cartes deux mois plus tard) de trois formes :
- « zone contestée » (logit p ← (1 − λ s) logit p, la maille tirée vers 0,5) : λ = 0 toujours retenu ; elle aide les
  mailles qui vont changer de camp mais dégrade bien davantage toutes celles qui ne changent pas ;
- sens donné par le côté de la ligne où l'on se bat : aucun gain (β = 0 toujours retenu) ; les combats ne disent pas
  qui avance ;
- retenue : l'intensité des combats module la poussée locale récente (la ligne avance dans le sens des deux derniers
  mois, d'autant plus que l'on s'y bat) :

    logit p' = logit p + β · s(x) · g(d) · tanh(tendance(x) / 0,03)

  s = score de combat : deuxième plus fort des indicateurs normalisés (feux en excès, combats UCDP, pertes WarSpotting),
      soit la cohérence d'au moins deux indicateurs ; chacun normalisé par 1 − exp(−x / h), h = 90e centile de
      l'indicateur dans la bande de contact (tous mois), fixé par les données et non par le résultat ;
  g = max(0, 1 − d / 30 km), d = distance à la ligne 50 % du mois ;
  tendance = variation mensuelle de p sur deux mois, lissée sur ~5 km (> 0 : gain russe).
  β est choisi chaque mois sur les seuls mois dont la vérité était connue (t' ≤ t − 2) : walk-forward, y compris pour
  l'affichage historique.
Le nowcast reste au mois écoulé : il corrige le retard des cartes, il n'anticipe pas.
"""
import math

import numpy as np
from scipy import ndimage

BGRID = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
WIDTH_KM = 30.0
R0 = 0.03
NAMES = ["feux", "ucdp", "pertes"]
CASES = [
    ("Vivaldi (nord de Lyman, 2026)", ["2026-05", "2026-06", "2026-07"], [37.55, 38.15, 48.98, 49.30]),
    ("Kharkiv (contre-offensive, 2022)", ["2022-08", "2022-09"], [36.5, 38.5, 49.0, 50.4]),
    ("Kherson (2022)", ["2022-09", "2022-10"], [32.0, 34.5, 46.3, 47.6]),
    ("Robotyne (2023)", ["2023-06", "2023-07", "2023-08"], [35.6, 36.2, 47.3, 47.7]),
    ("Avdiïvka (2023-2024)", ["2023-10", "2023-11", "2023-12", "2024-01", "2024-02"], [37.5, 37.95, 48.0, 48.3]),
    ("Pokrovsk (2024-2025)", ["2024-07", "2024-08", "2024-09", "2024-10", "2024-11", "2024-12", "2025-01", "2025-02", "2025-03"], [36.8, 37.5, 48.05, 48.5]),
    ("Dnipro à Kherson (calme)", None, [32.30, 33.90, 46.40, 47.00]),
]


def lgt(p):
    p = np.clip(p, 0.5 / 63, 62.5 / 63)
    return np.log(p / (1 - p))


class Nowcast:
    def __init__(self, U, cellU, NX, NY, km, lonlat, months):
        self.U, self.cellU, self.NX, self.NY, self.km = np.asarray(U), np.asarray(cellU), NX, NY, km
        self.jj, self.ii = self.U // NX, self.U % NX
        self.isUA = self.cellU >= 0
        self.lon, self.lat = lonlat
        self.months = months
        self.P, self.T, self.IND = {}, {}, {}
        self._dist, self._trend = {}, {}

    # ---------- données ----------
    def grid(self, vals):
        g = np.zeros((self.NY, self.NX), np.float32)
        g[self.jj, self.ii] = vals
        return g

    def add_month(self, t, p, truth, indicators):
        """p : champ de base sur U ; truth : consensus des cartes sur U (1, 0, −1) ; indicators : 3 grilles complètes."""
        self.P[t] = np.asarray(p, np.float32)
        self.T[t] = np.asarray(truth, np.int8)
        self.IND[t] = [ndimage.gaussian_filter(np.asarray(x, np.float32).reshape(self.NY, self.NX), 3.0)[self.jj, self.ii]
                       for x in indicators]

    def dist_line(self, t):
        if t not in self._dist:
            g = np.full((self.NY, self.NX), np.nan, np.float32)
            g[self.jj, self.ii] = self.P[t]
            ru, ua = g > 0.5, g < 0.5
            edge = np.zeros_like(ru)
            for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                edge |= ru & np.roll(ua, sh, axis=(0, 1))
            self._dist[t] = (ndimage.distance_transform_edt(~edge) * self.km)[self.jj, self.ii].astype(np.float32)
        return self._dist[t]

    def trend(self, t, k=2, sig=2.5):
        if t not in self._trend:
            if (t - k) not in self.P:
                self._trend[t] = np.zeros(len(self.U), np.float32)
            else:
                d = (self.P[t] - self.P[t - k]) / k
                gs = ndimage.gaussian_filter(self.grid(d), sig)
                ws = ndimage.gaussian_filter(self.grid(np.ones(len(self.U), np.float32)), sig)
                self._trend[t] = (gs / np.maximum(ws, 1e-6))[self.jj, self.ii]
        return self._trend[t]

    def scales(self):
        """h par indicateur : 90e centile des valeurs non nulles à moins de 30 km de la ligne, sur tous les mois."""
        self.H = []
        for n in range(len(NAMES)):
            vals = [self.IND[t][n][(self.dist_line(t) <= WIDTH_KM) & (self.IND[t][n] > 0)] for t in self.P]
            v = np.concatenate([x for x in vals if len(x)]) if any(len(x) for x in vals) else np.array([1.0])
            self.H.append(float(np.percentile(v, 90)) or 1.0)
        return self.H

    def score(self, t):
        a = np.sort(np.stack([1 - np.exp(-self.IND[t][n] / self.H[n]) for n in range(len(NAMES))]), axis=0)
        return a[-2]

    def apply(self, t, beta):
        p = self.P[t]
        if beta <= 0:
            return p.copy()
        g = np.clip(1 - self.dist_line(t) / WIDTH_KM, 0, 1)
        return (1 / (1 + np.exp(-(lgt(p) + beta * self.score(t) * g * np.tanh(self.trend(t) / R0))))).astype(np.float32)

    def apply_arrays(self, p, indicators, p_back, beta, k=2):
        """Nowcast d'un pas récent (hors calibration) : champ p, indicateurs bruts de la fenêtre, champ k mois plus tôt.
        Renvoie (p nowcast, score de combat, distance à la ligne)."""
        key = -1 - len(self.P)
        self.P[key] = np.asarray(p, np.float32)
        self.P[key - k] = np.asarray(p_back, np.float32)
        self.IND[key] = [ndimage.gaussian_filter(np.asarray(x, np.float32).reshape(self.NY, self.NX), 3.0)[self.jj, self.ii]
                         for x in indicators]
        try:
            return self.apply(key, beta), self.score(key), self.dist_line(key)
        finally:
            for kk in (key, key - k):
                self.P.pop(kk, None)
                self._dist.pop(kk, None)
                self._trend.pop(kk, None)
            self.IND.pop(key, None)

    # ---------- validation ----------
    def brier(self, t, p, sel=None):
        if (t + 2) not in self.T or not (self.T[t + 2] >= 0).any():
            return None
        m = self.isUA & (self.dist_line(t) <= WIDTH_KM) & (self.T[t + 2] >= 0)
        if sel is not None:
            m &= sel
        ch = m & (self.T[t] >= 0) & (self.T[t] != self.T[t + 2])
        return (float(((p[m] - self.T[t + 2][m]) ** 2).sum()), int(m.sum()),
                float(((p[ch] - self.T[t + 2][ch]) ** 2).sum()), int(ch.sum()))

    def calibrate(self, start="2022-10", min_hist=4):
        """β par mois (walk-forward) et bilan de validation."""
        self.scales()
        ts = sorted(self.P)
        ev = [t for t in ts if self.brier(t, self.P[t]) is not None and self.months[t] >= "2022-04"]
        E = {(t, b): self.brier(t, self.apply(t, b)) for t in ev for b in BGRID}
        beta = {}
        for t in ts:
            hist = [s for s in ev if s <= t - 2]
            beta[t] = min(BGRID, key=lambda b: sum(E[(s, b)][0] for s in hist)) if len(hist) >= min_hist else 0.0
        self.beta = beta
        final = min(BGRID, key=lambda b: sum(E[(s, b)][0] for s in ev)) if ev else 0.0

        def acc(ts_, sel=None, fixed=None):
            tot = {"base": [0.0, 0, 0.0, 0], "now": [0.0, 0, 0.0, 0]}
            for t in ts_:
                b = fixed if fixed is not None else beta[t]
                r0 = self.brier(t, self.P[t], sel) if sel is not None else E[(t, 0.0)]
                r1 = self.brier(t, self.apply(t, b), sel) if sel is not None else E[(t, b)]
                for i in range(4):
                    tot["base"][i] += r0[i]
                    tot["now"][i] += r1[i]
            return tot

        def summary(tot):
            b, n = tot["base"], tot["now"]
            return {"band": [round(b[0] / max(b[1], 1), 4), round(n[0] / max(n[1], 1), 4)],
                    "changing": [round(b[2] / max(b[3], 1), 4), round(n[2] / max(n[3], 1), 4)], "nChanging": b[3], "nBand": b[1]}
        wf = [t for t in ev if self.months[t] >= start and sum(1 for s in ev if s <= t - 2) >= min_hist]
        out = {"beta_final": final, "overall": summary(acc(wf)), "years": {}, "cases": {}, "scales": dict(zip(NAMES, [round(h, 4) for h in self.H])),
               "betas": {self.months[t]: beta[t] for t in wf}, "period": [self.months[wf[0]], self.months[wf[-1]]] if wf else None}
        for y in sorted({self.months[t][:4] for t in wf}):
            out["years"][y] = summary(acc([t for t in wf if self.months[t].startswith(y)]))
        for name, ms, bx in CASES:
            sel = (self.lon >= bx[0]) & (self.lon <= bx[1]) & (self.lat >= bx[2]) & (self.lat <= bx[3])
            tt = [t for t in ev if (ms is None and "2023-04" <= self.months[t] <= "2025-12") or (ms and self.months[t] in ms)]
            if not tt:
                continue
            s = summary(acc(tt, sel, fixed=final))
            s["dp"] = round(float(np.mean([np.abs(self.apply(t, final) - self.P[t])[sel & self.isUA].mean() for t in tt])), 4)
            out["cases"][name] = s
        self.report = out
        return out
