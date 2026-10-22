"""Refit QUOTIDIEN des POIDS de fusion (« moteur_jour », docs/ETAT.md § 6, 22/10/2026).

Prolonge le CHAMP réancré au jour (`decades.py`, déjà livré) : au lieu de REPORTER la loi a posteriori de δ du mois de
référence t, on la RECALCULE chaque jour d sur la ligne des cartes DU JOUR, avec :

    log post_d(δ | tronçon k) =  log π_t(δ)                     a priori des cartes du mois t (témoin biaisé, réancré)
                               + w_f · log filtre(jour précédent)   mémoire (filtre temporel, pas en JOURS)
                               + Σ_s w_s · log L_s^jour(δ)          signaux de POSITION, lus au pas QUOTIDIEN et causal

  - L_s^jour : vraisemblance de position construite sur la ligne du JOUR d et un champ causal de `donnees_jour.py`
    (MA exponentielle, publication ≤ d) ; forme du profil (m, L±, ε) héritée du fit MENSUEL du mois t ;
  - w_s : poids des signaux RÉAPPRIS jour par jour en walk-forward strict (score logarithmique de δ vrai des
    tronçons-JOURS passés q dont la vérité mensuelle — consensus à q + 2 mois — est connue à d ; décalage J-1),
    demi-vie 365 jours, grille de WGRID ; cadence `FUSION_REFIT` (jours) entre deux réapprentissages (coût) ;
  - signaux quotidiens disponibles : feux (2026), ucdp, pertes, em_frappes, modru, tg (artillerie/kab/radar : pas
    d'agrégat quotidien → poids mensuels, écartés du refit).

Aucune information postérieure à d. Sortie PARALLÈLE dédiée (`site/front_fusion_refit_hist_<année>.js`), jamais le défaut.
"""
import os
import time

import numpy as np

from .commun import BCENT, DGRID, ND, S, bin_of, norm_cdf
from .champ import ZG, median_of, quant, spread_of
from .moteur import KER, WGRID, FKER
from . import decades as DEC
from . import donnees_jour as DJ

HL_W_DAYS = float(os.environ.get("FUSION_W_HL", "365"))   # demi-vie du poids temporel (jours) : 12 mois
WIN_W_DAYS = int(os.environ.get("FUSION_W_WIN", "730"))   # fenêtre d'apprentissage (jours) : 24 mois
BETA_MONTH = 0.3                                           # filtre mensuel β ; converti au pas réel Δt_jours
CAP = 25.0                                                 # N_eff plafonné par tronçon (densités)

# signaux de POSITION quotidiens (intersection de POS_PROD du build et des agrégats data/daily)
SIG_DENS = ["feux", "ucdp", "pertes", "em_frappes", "modru"]
SIG_TG = "tg"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _al(u, m, Lm, Lp, eps):
    return eps + (1 - eps) * np.where(u < m, np.exp((u - m) / Lm), np.exp(-(u - m) / Lp))


class MoteurJour:
    """Refit des poids au jour, branché sur le moteur mensuel déjà construit (M, R, CH, choice, lams)."""

    def __init__(self, M, R, CH, choice, lams, cadence=7, signaux=None, refit=True):
        self.M, self.R, self.CH, self.choice, self.lams = M, R, CH, choice, lams
        self.C = M.C
        self.refit_on = refit                         # False : loi RECALCULÉE au jour mais POIDS mensuels (diagnostic)
        self.cadence = max(1, int(cadence))
        self.signaux = signaux if signaux is not None else self._signaux_dispo()
        self._prior = {}          # t -> logprior cartes (nrows_t x 81)
        self._rowmap = {}         # t -> (mailles Z valides, rows) pour les tronçons du mois t
        self._prof = {}           # (t, sig) -> params du profil mensuel
        self._llcache = {}        # (daykey, sig) -> loglik (nrows x 81) au jour (training)
        self._dayidx = DEC.lire()
        self._by_month = {}
        for j, e in enumerate(self._dayidx):
            self._by_month.setdefault(e["m"], []).append(j)

    def _signaux_dispo(self):
        pos = set(self.R["pos"])
        out = [s for s in SIG_DENS if s in pos and s in DJ.GROUPES and DJ.evenements(DJ.GROUPES[s][0]) is not None]
        if SIG_TG in pos:
            out.append(SIG_TG)
        return out

    # ---- a priori des cartes du mois t (réancré : δ relatif à la ligne du jour) ----
    def prior(self, t):
        if t not in self._prior:
            self._prior[t] = self.M.cartes.logprior(t, self.C, self.R["dyn_models"])
        return self._prior[t]

    def _rows_of(self, t):
        """(z valides, row par z) : mailles de Z appartenant à un tronçon du mois t, |d| ≤ 60 testé au jour."""
        if t not in self._rowmap:
            row = self.C.rpos[t][S.tinv]              # row de tronçon par maille Z (−1 hors tronçon)
            self._rowmap[t] = row
        return self._rowmap[t]

    def _prof_dens(self, t, sig):
        key = (t, sig)
        if key not in self._prof:
            self._prof[key] = self.M.models[sig].params.get(t)
        return self._prof[key]

    # ---- vraisemblance de position d'un signal au JOUR (ligne du jour, champ causal) ----
    def loglik_jour(self, t, sdD, d_ord, sig):
        """(nrows_t x 81) log-vraisemblance de δ du signal `sig` au jour d_ord, sur la ligne du jour (sdD sur Z)."""
        n = len(self.C.rows[t])
        row = self._rows_of(t)
        ok = np.isfinite(sdD) & (np.abs(np.nan_to_num(sdD, nan=999)) <= 60) & (row >= 0)
        if not ok.any():
            return np.zeros((n, ND))
        b = bin_of(sdD[ok])
        rr = row[ok]
        valid = b >= 0
        b, rr, zsel = b[valid], rr[valid], np.flatnonzero(ok)[valid]
        if sig == SIG_TG:
            LL = np.zeros((n, ND))
            for sub in self.M.models[SIG_TG].sub:
                p = sub.params.get(t)
                if not p:
                    continue
                w = DJ.champ_causal(sub.name, d_ord)[zsel]
                LL += self._prof_loglik(n, b, rr, w, None, p, cap=None)
            return LL
        p = self._prof_dens(t, sig)
        if not p:
            return np.zeros((n, ND))
        w = DJ.champ_groupe(sig, d_ord)[zsel]
        return self._prof_loglik(n, b, rr, w, None, p, cap=CAP)

    @staticmethod
    def _prof_loglik(n, b, rr, w, win, p, cap):
        He = np.zeros((n, len(BCENT)))
        Hc = np.zeros((n, len(BCENT)))
        np.add.at(He, (rr, b), w)
        np.add.at(Hc, (rr, b), 1.0 if win is None else win)
        if cap:
            N = He.sum(1)
            sc = np.where(N > cap, cap / np.maximum(N, 1e-9), 1.0)
            He = He * sc[:, None]
        F = _al(BCENT[:, None] + DGRID[None, :], p["m"], p["Lm"], p["Lp"], p["eps"])
        N = He.sum(1)
        LL = He @ np.log(F) - N[:, None] * np.log(np.maximum(Hc @ F, 1e-300))
        return LL - LL.max(1, keepdims=True)

    def _ll_day(self, j):
        """{sig: loglik (nrows x 81)} au jour d'indice j (cache ; training)."""
        e = self._dayidx[j]
        key = e["key"]
        out = {}
        for sig in self.signaux:
            ck = (key, sig)
            if ck not in self._llcache:
                self._llcache[ck] = self.loglik_jour(e["t"], e["SDe"].astype(np.float32), e["date"].toordinal(), sig)
            out[sig] = self._llcache[ck]
        return out

    # ---- apprentissage des poids au jour (walk-forward) ----
    def _train_days(self, t):
        """Jours d'apprentissage : jusqu'à 3 par mois passé qm ∈ [t−24, t−2] (vérité connue à t)."""
        out = []
        for qm in range(t - 24, t - 2 + 1):
            if qm not in self._by_month or qm not in S.built:
                continue
            js = self._by_month[qm]
            if not js:
                continue
            for frac in (0.25, 0.55, 0.85):
                out.append(js[min(int(frac * len(js)), len(js) - 1)])
        return sorted(set(out))

    def refit(self, t, d_ord):
        """Poids des signaux appris sur les tronçons-JOURS passés, cible = δ vrai mensuel (consensus à qm+2)."""
        tj = self._train_days(t)
        base, LLs, Ks, dec = [], {s: [] for s in self.signaux}, [], []
        for j in tj:
            e = self._dayidx[j]
            tr, m = e["t"], e["m"]                     # mois de référence du jour / mois civil du jour
            if tr is None or tr not in self.R["post"]:
                continue
            # vraisemblance et a priori sur les tronçons du mois de référence tr (ligne DU JOUR) ; cible = δ vrai
            # du mois civil m (consensus à m+2), par INDICE DE TUILE (C.rows = indices de tuiles, d2 clé (mois, tuile))
            d = np.array([self.C.d2.get((m, k), np.nan) for k in self.C.rows[tr]])
            okr = np.isfinite(d)
            if not okr.any():
                continue
            pr = self.prior(tr)[okr]
            ll = self._ll_day(j)
            base.append(pr)
            for s in self.signaux:
                LLs[s].append(ll[s][okr])
            jt = np.clip(np.round((d[okr] - DGRID[0]) / 0.5).astype(int), 0, ND - 1)
            Ks.append(KER[jt])
            dec.append(np.full(int(okr.sum()), 0.5 ** ((d_ord - e["date"].toordinal()) / HL_W_DAYS)))
        if len(base) < 4:
            return {s: self.R["w"][t].get(s, 0.0) for s in self.signaux}
        base = np.concatenate(base)
        LLc = {s: np.concatenate(LLs[s]) for s in self.signaux}
        K = np.concatenate(Ks)
        dec = np.concatenate(dec)

        def score(w):
            L = base + sum(w[s] * LLc[s] for s in self.signaux if w[s])
            L = L - L.max(1, keepdims=True)
            P = np.exp(L)
            P /= P.sum(1, keepdims=True)
            return float(np.sum(dec * np.log((P * K).sum(1) + 1e-12)))

        w = {s: float(self.R["w"][t].get(s, 0.0)) for s in self.signaux}
        best = score(w)
        for _ in range(2):
            for s in self.signaux:
                for v in WGRID:
                    if v == w[s]:
                        continue
                    w2 = dict(w); w2[s] = v
                    sc = score(w2)
                    if sc > best + 1e-6:
                        best, w = sc, w2
        return w

    # ---- champ de fusion au jour (poids refités) ----
    def _tables(self, t, P, s, lam):
        """Tables (nrows x len(ZG)) : p(russe | distance z à la ligne du jour), pour une loi P personnalisée de δ."""
        s = np.asarray(s, float)
        lam = np.asarray(lam, float)
        m = median_of(P)
        ld = spread_of(lam)
        sh = m[:, None] + ld[:, None] * (DGRID[None, :] - m[:, None])
        arg = (ZG[None, :, None] + sh[:, None, :]) / (lam * s)[:, None, None]
        return np.einsum("izd,id->iz", norm_cdf(arg), P)

    def posterior(self, j, w, prev):
        """Loi a posteriori de δ au jour j (nrows x 81) et dict tile->post pour le filtre."""
        e = self._dayidx[j]
        t = e["t"]
        sdD = e["SDe"].astype(np.float32)
        L = self.prior(t).copy()
        wf = float(self.R["w"][t].get("_filtre", 0.0))
        if wf and prev is not None:
            lf = np.zeros_like(L)
            dt_days = max(1, e["date"].toordinal() - prev["d_ord"])
            bfac = BETA_MONTH ** (dt_days / 30.0)
            pm = prev["post"]
            for i, k in enumerate(self.C.rows[t]):
                code = int(S.tiles[k])
                pv = pm.get(code)
                if pv is None:
                    continue
                pv = FKER @ pv
                lf[i] = np.log(pv / max(pv.sum(), 1e-300) + 1e-6)
            L = L + wf * bfac * lf
        for sig in self.signaux:
            if w.get(sig, 0.0):
                L = L + w[sig] * self.loglik_jour(t, sdD, e["date"].toordinal(), sig)
        L = L - L.max(1, keepdims=True)
        P = np.exp(L)
        P /= P.sum(1, keepdims=True)
        tile_post = {int(S.tiles[k]): P[i] for i, k in enumerate(self.C.rows[t])}
        return P, {"d_ord": e["date"].toordinal(), "post": tile_post}

    def champ(self, decs=None, rho=0.0):
        """{indice du jour: p sur Z (uint8, 6 bits)} — même format que decades.champ, poids refités au jour."""
        CH = self.CH
        out = {}
        prev, w, last_t, last_refit = None, None, None, None
        n_refit = 0
        for j, e in enumerate(self._dayidx):
            if decs is not None and j not in decs:
                continue
            t = e["t"]
            if t not in self.choice or t not in self.lams or t not in self.R["post"]:
                continue
            d_ord = e["date"].toordinal()
            if t != last_t:
                prev, last_refit = None, None            # changement de mois de référence : filtre et cadence remis
            if not self.refit_on:
                w = {s: float(self.R["w"][t].get(s, 0.0)) for s in self.signaux}
            elif w is None or last_t != t or last_refit is None or d_ord - last_refit >= self.cadence:
                w = self.refit(t, d_ord)
                last_refit = d_ord
                n_refit += 1
            last_t = t
            P, prev = self.posterior(j, w, prev)
            b, g, s0, sp, kp = self.choice[t]
            F = self._tables(t, P, CH.s_of(t, s0, sp), self.lams[t])
            pc, pr, pw = CH.pairs(t)
            sdM = np.nan_to_num(S.SDe[t], nan=999).astype(np.float64)
            extra = CH.sdp(t, b, g, kp) - sdM
            sdDz = np.nan_to_num(sdD_of(e), nan=999)
            z = sdDz[pc] + extra[pc]
            x = (np.clip(z, ZG[0], ZG[-1]) - ZG[0]) / (ZG[1] - ZG[0])
            i0 = np.minimum(np.floor(x).astype(np.int64), len(ZG) - 2)
            fr = x - i0
            v = F[pr, i0] * (1 - fr) + F[pr, i0 + 1] * fr
            num = np.bincount(pc, weights=pw * v, minlength=S.nZ)
            den = np.bincount(pc, weights=pw, minlength=S.nZ)
            p = np.where(den > 1e-9, num / np.maximum(den, 1e-12), e["Peq"] / 63.0)
            out[j] = np.round(quant(p) * 63).astype(np.uint8)
        self.n_refit = n_refit
        return out


def sdD_of(e):
    return e["SDe"].astype(np.float64)


def ecrire(dF, cadence, suffixe="refit"):
    """Écrit la sortie PARALLÈLE dédiée `site/front_fusion_{suffixe}_hist_<année>.js` (même format que decades.ecrire)."""
    import json
    from .socle import SITE, encode_field, decode_seq
    from .sortie import areas
    D = DEC.lire()
    pos = {e["key"]: j for j, e in enumerate(D)}
    band = S.cellU == -2
    index = {}
    for Y in sorted(S.FR["hist"]["years"]):
        H = DEC._after_eq(os.path.join(SITE, f"front_hist_{Y}.js"))
        seq = {f: decode_seq(DEC._after_eq(os.path.join(SITE, f"front_hist_{Y}_{f}.js")), S.nU) for f in ("equilibre", "nowcast")}
        arr, metas, prev = [], [], None
        for j, key in enumerate(H["keys"]):
            q = seq["equilibre"][j]
            jj = pos.get(key)
            if q is None or jj is None or jj not in dF:
                arr.append(None); metas.append(None); continue
            p = q.astype(np.float32) / 63.0
            p[S.Zi] = dF[jj].astype(np.float32) / 63.0
            if seq["nowcast"][j] is not None:
                p[band] = seq["nowcast"][j][band].astype(np.float32) / 63.0
            qq = np.minimum(63, (p * 63 + 0.5).astype(np.int64)).astype(np.uint8)
            arr.append(encode_field(qq, prev)); prev = qq
            metas.append({"date": key, "a": areas(p)})
        head = json.dumps({"keys": H["keys"], "cadence": cadence, "months": metas}, ensure_ascii=False, separators=(",", ":"))
        path = os.path.join(SITE, f"front_fusion_{suffixe}_hist_{Y}.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'(window.FRONT_FUSION_REFIT_HIST=window.FRONT_FUSION_REFIT_HIST||{{}})["{Y}"]=' + head + ";\n")
            f.write(f'(window.FRONTH=window.FRONTH||{{}})["{Y}/fusion_{suffixe}"]=' + json.dumps(arr, separators=(",", ":")) + ";\n")
        index[Y] = {"n": sum(1 for x in arr if x), "ko": os.path.getsize(path) // 1024}
    return index
