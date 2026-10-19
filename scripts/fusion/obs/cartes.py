"""Modèle d'observation des CARTES : témoin avec biais et retard appris.

Le champ des cartes (vue équilibrée : DeepStateMap, ISW, Wikipédia, annonces, poids à égalité par camp comme en
production) donne une ligne 50 %, la référence δ = 0. Les cartes sont un témoin BIAISÉ et EN RETARD : là où la ligne
bouge, la vraie ligne (consensus à t + 2) est déjà plus loin. Leur vraisemblance pour δ est un mélange :

    p(δ | cartes) = (1 − π) · N(δ ; b₀, σ₀²)  +  π · N(δ ; μ, σ₁²)

  - composante « stable » : les cartes ont raison à b₀ ± σ₀ près (b₀, σ₀ appris sur les tronçons passés qui n'ont
    pas bougé de 1 km) ;
  - composante « mouvement », de probabilité π (logistique ridge sur les variables g des modèles dynamiques) et de
    centre μ (régression ridge sur les variables orientées h, sur les tronçons passés qui ont bougé ≥ 1 km : le
    coefficient de la vitesse des cartes est le RETARD appris, la constante le BIAIS appris) ; σ₁ = écart-type
    résiduel.
Tout est réappris chaque mois t sur les tronçons-mois q ∈ [t − 24, t − 2] (vérité à q + 2 connue à t).
Sans modèle dynamique, π et μ sont des constantes apprises (biais et taux de mouvement moyens).
"""
import numpy as np

from ..commun import DGRID, H, logistic, ridge


class TemoinCartes:
    KIND = "cartes"
    name, label = "cartes", "Cartes (DeepState, ISW, Wikipédia ; témoin avec biais et retard)"

    def __init__(self, win=24, thr=1.0, lam_pi=3.0, lam_mu=10.0, nu=4.0):
        self.win, self.thr, self.lam_pi, self.lam_mu, self.nu = win, thr, lam_pi, lam_mu, nu
        self.params = {}

    @staticmethod
    def _X(dyn, t, part, mask=None):
        cols = [m.features(t)[part] for m in dyn]
        cols = [c for c in cols if c.shape[1]]
        n = len(next(iter(dyn)).features(t)[part]) if dyn else 0
        X = np.concatenate(cols, 1) if cols else np.zeros((n, 0))
        return X if mask is None else X[:, mask]

    @staticmethod
    def colmask(dyn, t, part):
        """Colonnes (variables) dont la SOURCE existe au mois t (v2, 19/10/2026) : une source absente à t (ISW après son
        arrêt, Telegram ou Sentinel-2 hors couverture…) est retirée du modèle appris pour t, au lieu d'entrer comme une
        indicatrice « absente » ou un zéro qui déplacerait μ ou π. Le modèle de t est appris sur le passé avec les seules
        variables disponibles à t."""
        ms = [np.asarray(m.avail(t)[0 if part == "mu" else 1], bool) for m in dyn]
        ms = [x for x in ms if len(x)]
        return np.concatenate(ms) if ms else np.zeros(0, bool)

    def fit(self, t, C, dyn):
        past = [q for q in C.months if t - self.win <= q <= t - H]
        cm_mu = self.colmask(dyn, t, "mu") if dyn else None
        cm_pi = self.colmask(dyn, t, "pi") if dyn else None
        y, Xp, Xm = [], [], []
        for q in past:
            ks = C.rows[q]
            d = np.array([C.d2.get((q, k), np.nan) for k in ks])
            ok = np.isfinite(d)
            if not ok.any():
                continue
            y.append(d[ok])
            if dyn:
                Xp.append(self._X(dyn, q, "pi", cm_pi)[ok]); Xm.append(self._X(dyn, q, "mu", cm_mu)[ok])
        if not y or sum(len(a) for a in y) < 200:
            self.params[t] = None
            return None
        y = np.concatenate(y)
        n = len(y)
        Xp = np.concatenate(Xp) if dyn else np.zeros((n, 0))
        Xm = np.concatenate(Xm) if dyn else np.zeros((n, 0))
        mv = np.abs(y) >= self.thr
        st = ~mv
        b0 = float(np.mean(y[st])); s0 = max(float(np.std(y[st])), 0.25)
        P = {"b0": b0, "s0": s0, "n": n, "taux_mvt": float(mv.mean()), "cm_mu": cm_mu, "cm_pi": cm_pi}
        # π
        if Xp.shape[1]:
            mu_p, sd_p = Xp.mean(0), np.where(Xp.std(0) < 1e-9, 1, Xp.std(0))
            wp = logistic((Xp - mu_p) / sd_p, mv.astype(float), lam=self.lam_pi)
            P.update(mu_p=mu_p, sd_p=sd_p, wp=wp)
        else:
            P.update(wp=np.array([np.log(mv.mean() / (1 - mv.mean()))]))
        # μ, σ₁
        if Xm.shape[1] and mv.sum() >= 30:
            mu_m, sd_m = Xm[mv].mean(0), np.where(Xm[mv].std(0) < 1e-9, 1, Xm[mv].std(0))
            wm = ridge((Xm[mv] - mu_m) / sd_m, y[mv], lam=self.lam_mu)
            res = y[mv] - (wm[0] + ((Xm[mv] - mu_m) / sd_m) @ wm[1:])
            P.update(mu_m=mu_m, sd_m=sd_m, wm=wm)
        else:
            wm = np.array([float(np.mean(y[mv])) if mv.any() else 0.0])
            res = y[mv] - wm[0]
            P.update(wm=wm)
        P["s1"] = max(float(np.sqrt(np.mean(res ** 2))) if mv.any() else 3.0, 0.8)
        # échelle hétéroscédastique : log(résidu²) ~ variables g (intensité, mouvement passé du secteur)
        if Xp.shape[1] and mv.sum() >= 30:
            Zp = (Xp[mv] - P["mu_p"]) / P["sd_p"]
            ws = ridge(Zp, np.log(res ** 2 + 0.25), lam=self.lam_mu)
            off = float(np.log(np.mean(res ** 2 + 0.25)) - np.mean(ws[0] + Zp @ ws[1:]))
            P.update(ws=ws, ws_off=off)
        self.params[t] = P
        return P

    def scale(self, t, C, dyn):
        P = self.params.get(t)
        n = len(C.rows[t])
        if P is None:
            return np.full(n, 3.0)
        if "ws" not in P:
            return np.full(n, P["s1"])
        Xp = self._X(dyn, t, "pi", P["cm_pi"])
        lv = P["ws"][0] + ((Xp - P["mu_p"]) / P["sd_p"]) @ P["ws"][1:] + P["ws_off"]
        return np.clip(np.sqrt(np.exp(lv)), 0.8, 12.0)

    def components(self, t, C, dyn):
        """(π, μ) par tronçon du mois t."""
        P = self.params.get(t)
        n = len(C.rows[t])
        if P is None:
            return np.full(n, 0.15), np.zeros(n), None
        if "mu_p" in P:
            Xp = self._X(dyn, t, "pi", P["cm_pi"])
            z = P["wp"][0] + ((Xp - P["mu_p"]) / P["sd_p"]) @ P["wp"][1:]
        else:
            z = np.full(n, P["wp"][0])
        pi = 1 / (1 + np.exp(-np.clip(z, -20, 20)))
        if "mu_m" in P:
            Xm = self._X(dyn, t, "mu", P["cm_mu"])
            mu = P["wm"][0] + ((Xm - P["mu_m"]) / P["sd_m"]) @ P["wm"][1:]
        else:
            mu = np.full(n, P["wm"][0])
        return pi, np.clip(mu, -15, 15), P

    def logprior(self, t, C, dyn, shift=None):
        """shift : décalage (km) du centre de la composante STABLE par ligne (secteurs figés, `figes.py`) ; None : 0."""
        pi, mu, P = self.components(t, C, dyn)
        b0, s0 = (0.0, 0.4) if P is None else (P["b0"], P["s0"])
        s1 = self.scale(t, C, dyn)[:, None]
        c0 = b0 + (np.zeros(len(pi)) if shift is None else np.asarray(shift, float))
        g0 = np.exp(-0.5 * ((DGRID[None, :] - c0[:, None]) / s0) ** 2) / s0
        nu = self.nu
        g1 = (1 + ((DGRID[None, :] - mu[:, None]) / s1) ** 2 / nu) ** (-(nu + 1) / 2) / s1   # Student, queues lourdes
        pr = (1 - pi)[:, None] * g0 + pi[:, None] * g1 + 1e-12
        pr /= pr.sum(1, keepdims=True)
        return np.log(pr)

    def formula(self, t=None, dyn=()):
        ts = sorted(k for k, v in self.params.items() if v)
        if not ts:
            return "cartes : pas assez de passé"
        P = self.params[t if t in ts else ts[-1]]
        s = (f"cartes : δ ~ (1 − π)·N({P['b0']:+.2f}, {P['s0']:.2f}²) + π·N(μ, {P['s1']:.2f}²) ; "
             f"taux de mouvement ≥ 1 km {P['taux_mvt']:.1%}")
        if "mu_m" in P:
            names = [n for m in dyn for n in m.mu_names]
            if P.get("cm_mu") is not None:
                names = [n for n, k in zip(names, P["cm_mu"]) if k]
            coef = P["wm"][1:] / P["sd_m"]
            s += " ; μ = " + f"{P['wm'][0] - np.sum(coef * P['mu_m']):+.2f}" + "".join(f" {c:+.2f}·[{n}]" for c, n in zip(coef, names))
        return s
