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

Modulation par P(mouvement) (décision du 19/10/2026, banc d'essai rôle B) : l'échelle du secteur k au mois t est
multipliée par exp(γ · a_k(t)), a = P(mouvement) du tronçon standardisée sur les mois PASSÉS (bornée à ±3, 0 sans
valeur) ; les erreurs passées sont divisées par leur propre échelle avant les quantiles, les bornes multipliées par
celle du mois. γ ∈ GAMMAS est choisi chaque mois, parmi les mois évalués q ≤ t − H des 12 derniers, par le score
d'intervalle de Winkler à 95 % ; γ = 0 redonne exactement la bande sans modulation (chaque γ garde son propre α adaptatif).
"""
import math

import numpy as np
from scipy import ndimage

LN19 = math.log(19.0)
GAMMAS = (-0.6, -0.3, 0.0, 0.3, 0.6, 1.0)


def standardize_activity(raw, months_idx):
    """raw : {t: {tuile: valeur}} → {t: {tuile: z}}, z par rapport à toutes les valeurs des mois antérieurs (< t, au
    moins 3 mois), bornée à ±3 ; rien (a = 0) tant que le passé est trop court."""
    out = {}
    hist = []
    for t in sorted(raw):
        if len(hist) >= 3:
            P_ = np.concatenate(hist)
            mu, sd = float(P_.mean()), max(float(P_.std()), 1e-9)
            out[t] = {k: float(np.clip((v - mu) / sd, -3, 3)) for k, v in raw[t].items()}
        hist.append(np.array(list(raw[t].values()), float))
    return out


class LocalBand:
    def __init__(self, sect, months, T, H=2, HL=6.0, R=25.0, OM=0.3, GA=0.05, TS=12, alpha=0.05, eval_from="2023-01", name="",
                 act=None, gammas=GAMMAS, sel_win=12):
        self.s, self.months, self.T = sect, months, T
        self.H, self.HL, self.R, self.OM, self.GA, self.TS, self.alpha0 = H, HL, R, OM, GA, TS, alpha
        self.act = act or {}
        self.gammas = tuple(gammas) if act else (0.0,)
        self.sel_win = sel_win
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
        self.glob = {}
        a_of = lambda t, ks: np.array([self.act.get(t, {}).get(int(k), 0.0) for k in ks])  # noqa: E731
        runs = {}
        for gam in self.gammas:
            alpha, pending = {}, []
            band, score, ev = {}, {}, {}
            for t in sorted(P):
                for rec in [r for r in pending if r[0] <= t - H]:
                    alpha[rec[1]] = float(np.clip(alpha.get(rec[1], self.alpha0) + self.GA * (self.alpha0 - rec[2]), 0.005, 0.3))
                    pending.remove(rec)
                past = [q for q in ERR if q <= t - H]
                if len(past) < 6:
                    continue
                pk = np.concatenate([ERR[q][0] for q in past])
                pe0 = np.concatenate([ERR[q][1] for q in past])
                # erreurs passées ramenées à l'échelle 1 (γ = 0 : inchangées)
                pe = pe0 / np.exp(gam * np.concatenate([a_of(q, ERR[q][0]) for q in past])) if gam else pe0
                pw = np.concatenate([np.full(len(ERR[q][1]), 0.5 ** ((t - q) / self.HL)) for q in past])
                if t not in self.glob:
                    self.glob[t] = self._wq(pe0, pw, [self.alpha0 / 2, 0.5, 1 - self.alpha0 / 2])
                qg = self.glob[t]
                bt = {}
                for k in tiles:
                    wt = {q: (1.0 if q == k else self.OM * math.exp(-d / self.R)) for q, d in near[k]}
                    sel = np.isin(pk, list(wt))
                    if sel.sum() < 20:
                        continue
                    al = alpha.get(k, self.alpha0)
                    ww = pw[sel] * np.array([wt[q] for q in pk[sel]])
                    sk = math.exp(gam * self.act.get(t, {}).get(int(k), 0.0)) if gam else 1.0
                    bt[k] = [x * sk for x in self._wq(pe[sel], ww, [al / 2, 0.5, 1 - al / 2])]
                band[t] = bt
                if t in ERR:  # évaluation (vérité connue plus tard) : couverture par tuile, score de Winkler du mois
                    kt, et = ERR[t]
                    rec, wsum, wn = [], 0.0, 0
                    for k in np.unique(kt):
                        if k not in bt:
                            continue
                        lo, _, hi = bt[k]
                        e = et[kt == k]
                        inn = int(((e >= lo) & (e <= hi)).sum())
                        ing = int(((e >= qg[0]) & (e <= qg[2])).sum())
                        pending.append((t, k, 1 - inn / len(e)))
                        wsum += float(np.sum((hi - lo) + (2 / self.alpha0) * (np.maximum(lo - e, 0) + np.maximum(e - hi, 0)))); wn += len(e)
                        rec.append((int(k), inn, ing, len(e), hi - lo))
                    ev[t] = rec
                    score[t] = wsum / wn if wn else np.nan
            runs[gam] = (band, score, ev)
        # choix de γ en walk-forward : score moyen des mois évalués q ≤ t − H des sel_win derniers mois (0 par défaut)
        g0 = 0.0 if 0.0 in self.gammas else self.gammas[0]
        self.chosen = {}
        for t in sorted(P):
            cand = [q for q in runs[g0][1] if t - H - self.sel_win < q <= t - H and all(q in runs[g][1] for g in self.gammas)]
            self.chosen[t] = g0 if len(cand) < 3 else min(self.gammas, key=lambda g: np.nanmean([runs[g][1][q] for q in cand]))
        self.band = {t: runs[self.chosen[t]][0][t] for t in runs[g0][0]}
        self.runs = runs

        def tally(pick):
            cov, covg, cst, wid = {}, {}, {}, {}
            for t, rec in runs[g0][2].items():
                if self.months[t] < self.eval_from:
                    continue
                for k, inn, ing, n, w in runs[pick(t)][2].get(t, []):
                    c = cov.setdefault(k, [0, 0]); c[0] += inn; c[1] += n
                    g = covg.setdefault(k, [0, 0]); g[0] += ing; g[1] += n
                    wid.setdefault(k, []).append(w)
                    wid.setdefault(("pts", k), []).append(w * n)
                    st = (strata or {}).get(t, {}).get(k, "stable")
                    x = cst.setdefault(st, [0, 0, 0]); x[0] += inn; x[1] += ing; x[2] += n
            return cov, covg, cst, wid
        self.cov, self.covg, self.cst, self.wid = tally(lambda t: self.chosen[t])
        # sans modulation (γ = 0), pour le rapport avant / après
        self.base = tally(lambda t: g0) if len(self.gammas) > 1 else None
        return self

    def coverage_stats(self, which=None, min_n=40):
        """Couverture par secteur (médiane, P10, part < 90 %) et largeur moyenne par point de vérité (km), 2023+ ;
        which = None : bande publiée, « base » : sans modulation."""
        cov, _, _, wid = self.cov, None, None, self.wid
        if which == "base" and self.base:
            cov, _, _, wid = self.base
        big = [k for k in cov if cov[k][1] >= min_n]
        c = np.array([cov[k][0] / cov[k][1] for k in big]) if big else np.array([np.nan])
        npts = sum(v[1] for v in cov.values())
        w = sum(sum(v) for k, v in wid.items() if isinstance(k, tuple)) / max(npts, 1)
        return {"median": round(float(np.median(c)), 3), "p10": round(float(np.percentile(c, 10)), 3),
                "below90": round(float(np.mean(c < 0.90)), 3), "width_km": round(float(w), 2), "tilesN": len(big)}

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
        mod = None
        if len(self.gammas) > 1:
            ev = [t for t in self.chosen if self.months[t] >= self.eval_from and t in self.ERR]
            g = [self.chosen[t] for t in ev]
            mod = {"signal": "P(mouvement) du tronçon, standardisée sur le passé", "gammas": list(self.gammas),
                   "gammaLast": self.chosen[max(self.chosen)] if self.chosen else None,
                   "shareNonZero": round(float(np.mean([x != 0 for x in g])), 2) if g else None,
                   "meanGamma": round(float(np.mean(g)), 2) if g else None,
                   "after": self.coverage_stats(min_n=min_n), "before": self.coverage_stats("base", min_n=min_n)}
        return {"tilesN": len(big), "local": f(cl), "global": f(cg), "modulation": mod,
                "strata": {st: {"local": round(a / n, 3), "global": round(g / n, 3), "n": n} for st, (a, g, n) in self.cst.items()},
                "tiles": tiles, "params": {"H": self.H, "HL": self.HL, "R": self.R, "omega": self.OM, "gamma": self.GA, "tile_km": self.TS * self.s.km}}
