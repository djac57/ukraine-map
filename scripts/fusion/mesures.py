"""Mesures du moteur de fusion (walk-forward strict), mêmes mailles et mêmes mois que la recherche :

  - Brier ≤ 30 km : mailles ukrainiennes à ≤ 30 km de la ligne PUBLIÉE (nowcast), vérité = consensus à t+2 ;
  - Brier des mailles qui changent de camp (consensus à t ≠ consensus à t+2 ; aussi t+1) ;
  - couverture 5-95 % par secteur de 24 km (points de la ligne vraie à t+2) : médiane, P10, secteurs < 90 % ;
  - KPI « avance sur les cartes » par EMPILEMENT (définition de `scripts/kpi_avance.py`, réimplémentée sur la grille du
    moteur comme `analyses/fusion/kpi_empile.py`) : gain 1 − Brier(empilé) / Brier(consensus du jour), net du placebo
    « sens tiré au hasard » (5 graines), IC par blocs de 3 mois.
"""
import numpy as np

from .commun import EVAL_FROM, H, S
from .champ import covered, eval_cells
from . import donnees as DN


def eval_months():
    return [t for t in S.built if S.months[t] >= EVAL_FROM and (t + H) in S.built and np.isfinite(S.Pnow[t]).any()]


def metriques(fields, months=None):
    ts = months or eval_months()
    acc = {"b": [0.0, 0], "c2": [0.0, 0], "c1": [0.0, 0]}
    cov = {}
    mon = {}
    for t in ts:
        p = fields[t]
        idx = eval_cells(t)
        for h, kc in ((2, "c2"), (1, "c1")):
            if t + h not in S.built:
                continue
            y = S.T[t + h][idx]
            k = y >= 0
            ch = k & (S.T[t][idx] >= 0) & (S.T[t][idx] != y)
            if h == 2:
                sb, nb = float(((p[idx][k] - y[k]) ** 2).sum()), int(k.sum())
                acc["b"][0] += sb; acc["b"][1] += nb
                sc, nc = float(((p[idx][ch] - y[ch]) ** 2).sum()), int(ch.sum())
                mon[t] = (sb, nb, sc, nc)
            acc[kc][0] += float(((p[idx][ch] - y[ch]) ** 2).sum()); acc[kc][1] += int(ch.sum())
        tk, inn = covered(p, t)
        for kk in np.unique(tk):
            m = tk == kk
            z = cov.setdefault(int(kk), [0, 0]); z[0] += int(inn[m].sum()); z[1] += int(m.sum())
    r = lambda a: a[0] / max(a[1], 1)  # noqa: E731
    big = [k for k in cov if cov[k][1] >= 40]
    cv = np.array([cov[k][0] / cov[k][1] for k in big])
    return {"mois": len(ts), "brier_bande": r(acc["b"]), "n_bande": acc["b"][1], "brier_change2": r(acc["c2"]),
            "n_change2": acc["c2"][1], "brier_change1": r(acc["c1"]), "cov_med": float(np.median(cv)),
            "cov_p10": float(np.percentile(cv, 10)), "cov_sous90": int(np.sum(cv < 0.9)), "n_secteurs": len(big),
            "cov_min": float(cv.min()), "_cov": {int(k): cov[k] for k in big}, "_mon": mon}


def boot_diff(ma, mb, nb=1000, seed=0):
    """Erreur type (bootstrap par mois) de l'écart apparié (Brier ≤ 30 km, Brier changeantes)."""
    ts = sorted(set(ma) & set(mb))
    rng = np.random.default_rng(seed)
    A = np.array([ma[t] for t in ts], float); B = np.array([mb[t] for t in ts], float)
    d1, d2 = [], []
    for _ in range(nb):
        i = rng.integers(0, len(ts), len(ts))
        a, b = A[i].sum(0), B[i].sum(0)
        d1.append(a[0] / a[1] - b[0] / b[1]); d2.append(a[2] / a[3] - b[2] / b[3])
    return float(np.std(d1)), float(np.std(d2))


# ---------------------------------------------------------------------------------------------------------------------
# KPI « avance sur les cartes » par empilement
# ---------------------------------------------------------------------------------------------------------------------
def _lg(p):
    p = np.clip(np.nan_to_num(p, nan=0.5), 0.5 / 63, 1 - 0.5 / 63)
    return np.log(p / (1 - p))


def _sigm(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def _feat(x):
    x = np.clip(np.nan_to_num(x, nan=0.0), -30, 30) / 10
    return np.stack([np.ones_like(x), x, np.maximum(x, 0)], 1)


def _fit(X, y, lam=1.0, it=12):
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = _sigm(X @ w)
        g = X.T @ (y - p) - lam * np.r_[0, w[1:]]
        Hm = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(X.shape[1]) + 1e-6 * np.eye(X.shape[1])
        w += np.linalg.solve(Hm, g)
    return w


class KPI:
    def __init__(self):
        self.SDT = DN.sd_truth()
        self.isua = S.isUA[S.Zi]
        self._smp, self._dom = {}, {}

    def domain(self, t, h):
        if (t, h) not in self._dom:
            y = S.T[t + h]
            self._dom[(t, h)] = self.isua & (y >= 0) & ((np.abs(np.nan_to_num(self.SDT[t], nan=99)) <= 30)
                                                        | (np.abs(np.nan_to_num(self.SDT[t + h], nan=99)) <= 30))
        return self._dom[(t, h)]

    def sample(self, q, h):
        if (q, h) not in self._smp:
            m = np.flatnonzero(self.domain(q, h))
            self._smp[(q, h)] = np.random.default_rng(q * 31 + h).choice(m, size=min(8000, len(m)), replace=False)
        return self._smp[(q, h)]

    def X_of(self, p, t, idx=None):
        x = self.SDT[t] if idx is None else self.SDT[t][idx]
        pp = p if idx is None else p[idx]
        g = np.maximum(0, 1 - np.abs(np.nan_to_num(x, nan=99)) / 30)
        L = np.clip(_lg(pp), -4, 4) / 4
        return np.c_[_feat(x), L, L * g]

    @staticmethod
    def placebo(src, pool, t, seed):
        r = np.where(np.random.default_rng(1000 * seed + 7 + t).random(S.nT) < 0.5, -1.0, 1.0)[S.tinv]
        return _sigm(_lg(pool) + r * (_lg(src) - _lg(pool)))

    def gains(self, fields, h, ts):
        out = {}
        for t in ts:
            tr = [q for q in S.built if q + h <= t and q + h in S.built and q in fields][-12:]
            if len(tr) < 6 or t not in fields:
                continue
            Xs = np.concatenate([self.X_of(fields[q], q, self.sample(q, h)) for q in tr])
            Ys = np.concatenate([(S.T[q + h][self.sample(q, h)] == 1).astype(float) for q in tr])
            w, w0 = _fit(Xs, Ys), _fit(Xs[:, :3], Ys)
            dom = self.domain(t, h)
            y = (S.T[t + h] == 1).astype(float)
            Xd = self.X_of(fields[t], t)
            ps, pb = _sigm(Xd @ w), _sigm(Xd[:, :3] @ w0)
            ch = dom & (S.T[t] >= 0) & (S.T[t] != S.T[t + h])
            out[t] = (((ps - y) ** 2)[dom].sum(), ((pb - y) ** 2)[dom].sum(), dom.sum(), ((ps - y) ** 2)[ch].sum(),
                      ((pb - y) ** 2)[ch].sum(), ch.sum())
        return out

    @staticmethod
    def agg(G, ts):
        A = np.array([G[t] for t in ts if t in G], float).sum(0)
        return 1 - A[0] / A[1], 1 - A[3] / A[4]

    def boot(self, Ga, Gp_list, ts, nb=1000):
        ts = [t for t in ts if t in Ga and all(t in g for g in Gp_list)]
        rng = np.random.default_rng(0)
        nblk = max(1, len(ts) - 2)
        v1, v2 = [], []
        for _ in range(nb):
            st = rng.integers(0, nblk, int(np.ceil(len(ts) / 3)))
            pick = [ts[min(s + j, len(ts) - 1)] for s in st for j in range(3)][:len(ts)]
            a = self.agg(Ga, pick)
            p = np.mean([self.agg(g, pick) for g in Gp_list], 0)
            v1.append(a[0] - p[0]); v2.append(a[1] - p[1])
        return np.percentile(v1, [2.5, 97.5]).tolist(), np.percentile(v2, [2.5, 97.5]).tolist()

    def net(self, src, h, seeds=5):
        """KPI net (points) d'une source {t: p sur Z} à l'horizon h, sur toute la période et sur 12 mois."""
        pool = {t: np.nan_to_num(S.Peq[t], nan=0.5) for t in S.built}
        ts = [t for t in S.built if S.months[t] >= EVAL_FROM and t + h in S.built]
        G = self.gains(src, h, ts)
        plc = [self.gains({t: self.placebo(src[t], pool[t], t, s) for t in src if t in pool}, h, ts) for s in range(seeds)]
        a = self.agg(G, ts)
        p = np.mean([self.agg(g, ts) for g in plc], 0)
        l12 = ts[-12:]
        a12 = self.agg(G, l12)
        p12 = np.mean([self.agg(g, l12) for g in plc], 0)
        ci1, ci2 = self.boot(G, plc, ts)
        return {"brut_d30": a[0], "brut_chg": a[1], "placebo_d30": p[0], "placebo_chg": p[1], "net_d30": a[0] - p[0],
                "net_chg": a[1] - p[1], "ic_net_d30": ci1, "ic_net_chg": ci2, "net_d30_12m": a12[0] - p12[0],
                "net_chg_12m": a12[1] - p12[1]}
