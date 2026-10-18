"""Bande d'incertitude locale de la ligne 50 % : intervalles conformes par secteur, en walk-forward.

Erreur signée e (km) d'un mois t : position de la ligne de « vérité » (consensus des cartes à t + H, les cartes ayant du
retard) par rapport à la ligne 50 % du champ publié au mois t, mesurée le long de la distance signée D (positive côté
russe). Une maille x est russe si e < D(x). Pour chaque secteur (tuile de 24 km), la bande du mois t est faite des
quantiles 2,5 % et 97,5 % des erreurs PASSÉES (mois s ≤ t − H, dont la vérité est connue au mois t) :
  - oubli : poids 0,5^((t − s) / HL) ;
  - emprunt aux secteurs voisins : poids ω · exp(−d / R) (d entre centres de tuiles, jusqu'à 3R) ;
  - conformal adaptatif : le niveau α du secteur suit ses échecs passés, α ← α + γ (0,05 − taux d'échec) ;
  - dé-quantification : la ligne vraie est n'importe où dans sa maille de 2 km (bruit uniforme ±1 km, graine fixe).
Le champ p est ensuite reconstruit autour de la ligne 50 % (inchangée) : p = σ(ln 19 · D / s), s = −q2,5 du côté
ukrainien et q97,5 du côté russe, de sorte que les isolignes 5 % et 95 % tombent sur les bornes locales.
"""
import math

import numpy as np
from scipy import ndimage

LN19 = math.log(19.0)


class LocalBand:
    def __init__(self, sect, months, T, H=2, HL=6.0, R=25.0, OM=0.3, GA=0.05, TS=12, alpha=0.05, eval_from="2023-01", name=""):
        self.s, self.months, self.T = sect, months, T
        self.H, self.HL, self.R, self.OM, self.GA, self.TS, self.alpha0 = H, HL, R, OM, GA, TS, alpha
        s = sect
        self.tile = (s.jj // TS) * 1000 + s.ii // TS
        self.NTy, self.NTx = s.NY // TS + 1, s.NX // TS + 1
        self.IDX = {}
        self.uaGrid = sect.grid(sect.isUA.astype(np.float32), 0) > 0.5
        self.eval_from, self.name = eval_from, name

    def sd(self, p):
        """Distance signée (km, positive côté russe) à la ligne 50 % CONTESTÉE : bords dont la maille russe est en
        Ukraine. La frontière d'État avec la bande russe (a priori russe) et la ligne en Russie (Koursk), où la vérité
        des cartes manque, ne sont pas recalibrées."""
        s = self.s
        g = s.grid(p, np.nan)
        ru, ua = g > 0.5, g < 0.5
        nb = np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb |= np.roll(ua, sh, axis=(0, 1))
        edge = ru & nb & self.uaGrid
        if not edge.any():
            return None
        d = ndimage.distance_transform_edt(~edge) * s.km
        return np.where(ru, d, -d)[s.jj, s.ii].astype(np.float32)

    # ---------- erreurs ----------
    def _truth_edges(self, t):
        T = self.T[t]
        g = self.s.grid(T.astype(np.float32), -1)
        ru, ua = g == 1, g == 0
        adj_ua, adj_ru = np.zeros_like(ru), np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            adj_ua |= np.roll(ua, sh, axis=(0, 1))
            adj_ru |= np.roll(ru, sh, axis=(0, 1))
        return (np.flatnonzero((ru & adj_ua)[self.s.jj, self.s.ii]), np.flatnonzero((ua & adj_ru)[self.s.jj, self.s.ii]))

    def errors(self, t, sd):
        """Erreurs du mois t (vérité à t + H) : (tuiles, e en km)."""
        a, b = self._truth_edges(t + self.H)
        idx = np.r_[a, b]
        # sd = 0 sur la maille russe du bord : la ligne 50 % passe à +1 km d'elle ; ligne vraie à −1 km d'une maille
        # russe de bord de vérité, à +1 km d'une maille ukrainienne
        e = np.r_[sd[a], sd[b] + 2.0] + np.random.default_rng(t).uniform(-1.0, 1.0, len(idx))
        keep = np.abs(e) <= 60
        self.IDX[t] = idx[keep]
        return self.tile[idx[keep]], e[keep].astype(np.float32)

    @staticmethod
    def _wq(x, w, qs):
        o = np.argsort(x)
        c = np.cumsum(w[o])
        c /= c[-1]
        return [float(np.interp(q, c, x[o])) for q in qs]

    def _center(self, k):
        return np.array([(k // 1000 + 0.5) * self.TS * self.s.km, (k % 1000 + 0.5) * self.TS * self.s.km])

    # ---------- calibration walk-forward ----------
    def fit(self, P, strata=None):
        """P : {t: champ p (U)}. Calcule, pour chaque mois, les bornes par tuile à partir des seuls mois passés, et la
        couverture obtenue là où la vérité est connue. strata : {t: {tuile: strate}} (rapport)."""
        s, H = self.s, self.H
        self.SD = {t: self.sd(P[t]) for t in P}
        ERR = {t: self.errors(t, self.SD[t]) for t in P if self.SD[t] is not None and t + H < len(self.months)
               and (self.T.get(t + H) is not None) and (self.T[t + H] >= 0).any()}
        self.ERR = ERR
        tiles = np.unique(np.concatenate([v[0] for v in ERR.values()])) if ERR else np.array([], int)
        C = {k: self._center(k) for k in tiles}
        near = {k: [(q, float(np.hypot(*(C[k] - C[q])))) for q in tiles if np.hypot(*(C[k] - C[q])) <= 3 * self.R] for k in tiles}
        self.band, self.glob = {}, {}
        alpha, pending = {}, []
        cov, covg, cst, wid = {}, {}, {}, {}
        for t in sorted(P):
            for rec in [r for r in pending if r[0] <= t - H]:
                alpha[rec[1]] = float(np.clip(alpha.get(rec[1], self.alpha0) + self.GA * (self.alpha0 - rec[2]), 0.005, 0.3))
                pending.remove(rec)
            past = [q for q in ERR if q <= t - H]
            if len(past) < 6:
                continue
            pk = np.concatenate([ERR[q][0] for q in past])
            pe = np.concatenate([ERR[q][1] for q in past])
            pw = np.concatenate([np.full(len(ERR[q][1]), 0.5 ** ((t - q) / self.HL)) for q in past])
            qg = self._wq(pe, pw, [self.alpha0 / 2, 0.5, 1 - self.alpha0 / 2])
            self.glob[t] = qg
            bt = {}
            for k in tiles:
                wt = {q: (1.0 if q == k else self.OM * math.exp(-d / self.R)) for q, d in near[k]}
                sel = np.isin(pk, list(wt))
                if sel.sum() < 20:
                    continue
                al = alpha.get(k, self.alpha0)
                ww = pw[sel] * np.array([wt[q] for q in pk[sel]])
                bt[k] = self._wq(pe[sel], ww, [al / 2, 0.5, 1 - al / 2])
            self.band[t] = bt
            if t in ERR:  # évaluation (vérité connue plus tard) : couverture par tuile et par strate
                kt, et = ERR[t]
                for k in np.unique(kt):
                    if k not in bt:
                        continue
                    lo, _, hi = bt[k]
                    e = et[kt == k]
                    inn = int(((e >= lo) & (e <= hi)).sum())
                    ing = int(((e >= qg[0]) & (e <= qg[2])).sum())
                    pending.append((t, k, 1 - inn / len(e)))
                    if self.months[t] < self.eval_from:
                        continue
                    c = cov.setdefault(int(k), [0, 0]); c[0] += inn; c[1] += len(e)
                    g = covg.setdefault(int(k), [0, 0]); g[0] += ing; g[1] += len(e)
                    wid.setdefault(int(k), []).append(hi - lo)
                    st = (strata or {}).get(t, {}).get(int(k), "stable")
                    x = cst.setdefault(st, [0, 0, 0]); x[0] += inn; x[1] += ing; x[2] += len(e)
        self.cov, self.covg, self.cst, self.wid = cov, covg, cst, wid
        return self

    # ---------- reconstruction du champ ----------
    def bounds(self, t):
        """Bornes (lo < 0 < hi, km) par maille pour le mois t : grille des tuiles, interpolée."""
        tb = [q for q in self.band if q <= t]
        if not tb:
            return None
        t0 = tb[-1]
        qg = self.glob[t0]
        LO = np.full((self.NTy, self.NTx), min(-1.0, qg[0]), np.float32)
        HI = np.full((self.NTy, self.NTx), max(1.0, qg[2]), np.float32)
        for k, (lo, _, hi) in self.band[t0].items():
            LO[k // 1000, k % 1000] = min(-1.0, lo)
            HI[k // 1000, k % 1000] = max(1.0, hi)
        cy = self.s.jj / self.TS - 0.5
        cx = self.s.ii / self.TS - 0.5
        lo = ndimage.map_coordinates(LO, [cy, cx], order=1, mode="nearest")
        hi = ndimage.map_coordinates(HI, [cy, cx], order=1, mode="nearest")
        return lo, hi

    def remap(self, p, t, key):
        b = self.bounds(t)
        if b is None:
            return p
        lo, hi = b
        sd = self.sd(p)
        if sd is None:
            return p
        D = sd + 1.0
        sc = np.where(D < 0, -lo, hi)
        q = (1.0 / (1.0 + np.exp(-np.clip(LN19 * D / sc, -40, 40)))).astype(np.float32)
        # bande russe (a priori russe, ligne de Koursk sans vérité de cartes) : champ inchangé
        return np.where(self.s.isUA, q, p).astype(np.float32)

    def raw_coverage(self, P):
        """Couverture de la bande 0,05-0,95 du champ brut (avant reconstruction), mêmes points de vérité."""
        a = n = 0
        for t in self.ERR:
            if self.months[t] < "2023-01":
                continue
            x = P[t][self.IDX[t]]
            a += int(((x >= 0.05) & (x <= 0.95)).sum()); n += len(x)
        return round(a / n, 3) if n else None

    # ---------- rapport ----------
    def report(self, lonlat, min_n=40):
        big = [k for k in self.cov if self.cov[k][1] >= min_n]
        cl = np.array([self.cov[k][0] / self.cov[k][1] for k in big]) if big else np.array([np.nan])
        cg = np.array([self.covg[k][0] / self.covg[k][1] for k in big]) if big else np.array([np.nan])
        f = lambda c: {"median": round(float(np.median(c)), 3), "p10": round(float(np.percentile(c, 10)), 3), "min": round(float(c.min()), 3),  # noqa: E731
                       "in90_99": round(float(np.mean((c >= 0.90) & (c <= 0.99))), 2), "below90": round(float(np.mean(c < 0.90)), 2)}
        tiles = []
        last = max(self.band) if self.band else None
        for k in big:
            lon, lat = lonlat(k)
            lo, _, hi = self.band[last].get(k, (None, None, None)) if last is not None else (None, None, None)
            tiles.append([round(lon, 2), round(lat, 2), round(-lo, 1) if lo is not None else None, round(hi, 1) if hi is not None else None,
                          round(float(np.median(self.wid[k])), 1), round(self.cov[k][0] / self.cov[k][1], 3), self.cov[k][1]])
        return {"tilesN": len(big), "local": f(cl), "global": f(cg),
                "strata": {st: {"local": round(a / n, 3), "global": round(g / n, 3), "n": n} for st, (a, g, n) in self.cst.items()},
                "tiles": tiles, "params": {"H": self.H, "HL": self.HL, "R": self.R, "omega": self.OM, "gamma": self.GA, "tile_km": self.TS * self.s.km}}
