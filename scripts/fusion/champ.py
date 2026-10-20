"""Du décalage δ au champ de probabilité et à la bande (copie vectorisée de `analyses/fusion/moteur.field` et
`evaluer.champs`, 19/10/2026, plus la recalibration de la bande par secteur de production).

    p(x) = Σ_k ω_k(x) · Σ_δ π_k(δ) · Φ( (d(x) + β·v_loc(x) + γ·o(x) + m_k + λ_k·(δ − m_k)) / (λ_k · s_k) )

  - ω_k : poids gaussiens (10 km) des tronçons voisins à ≤ 30 km ; au-delà, champ des cartes ;
  - v_loc(x) : vitesse locale de la ligne des cartes ; o(x) : avance locale des cartes séparées ;
  - s_k = s₀ + s_π · π_k : flou de forme, élargi quand la probabilité de mouvement π_k du tronçon est forte ;
  - m_k = médiane a posteriori de δ ; λ_k = exp(c_k) : facteur d'échelle de la bande du secteur, appliqué au flou ET
    à l'étalement de la loi de δ autour de sa médiane (la ligne 50 % ne bouge presque pas). En recherche, c_k ne
    touchait que s_k : la bande restait trop large (98,6 % de couverture médiane au lieu de 95 %) parce que sa largeur
    vient surtout de l'étalement de la loi de δ.
  - (β, γ, s₀, s_π) choisis chaque mois en walk-forward sur une grille de 72 points (règle de l'utilisateur : Brier
    ≤ 30 km passé ≤ 0,97 × production, puis le meilleur Brier des mailles qui changent) ;
  - c_k : conformal adaptatif PAR SECTEUR sur la couverture constatée avec deux mois de retard (voir `calibrer`).
"""
import numpy as np

from .commun import DGRID, H, OFF, S, TILE, lgt, norm_cdf

ZG = np.arange(-60, 60.001, 0.25)
FGRID = [(b, g, s0, sp) for b in (1.0, 1.5, 2.0, 2.5) for g in (0.35, 0.7, 1.0) for s0 in (1.0, 1.5) for sp in (4.0, 6.0, 10.0)]
DEFAULT = (2.0, 0.7, 1.5, 6.0)
TILEZ = S.tinv
SECZ = S.sinv          # secteur de 24 km de chaque maille de Z (évaluation et calibration de la bande)
# portée et largeur des poids des tronçons voisins dans le champ : 30 km et 10 km pour des tronçons de 24 km (v1),
# au prorata de la taille du tronçon sinon (FUSION_OMEGA = facteur multiplicatif, choisi sur 2023-2024)
import os as _os
OMEGA = float(_os.environ.get("FUSION_OMEGA", "1.0"))
R_NB, SIG_NB = 30.0 * TILE / 12 * OMEGA, 10.0 * TILE / 12 * OMEGA


def quant(p):
    return np.clip(np.round(np.asarray(p) * 63), 0, 63) / 63


def eval_cells(t):
    """Mailles évaluées : ukrainiennes, à ≤ 30 km de la ligne PUBLIÉE (nowcast)."""
    sd = np.nan_to_num(S.SDn[t], nan=999)
    return np.flatnonzero(S.isUA[S.Zi] & (np.abs(sd) <= 30))


SPREAD = {"mode": "both"}
# v3 (20/10/2026) : profil du décalage le long de la ligne (None : δ uniforme par tronçon, v2) ; contrainte de km² dans le
# choix des paramètres du champ (None : v2)
PROFIL = {"mode": None, "alpha": 0.0}
CHOIX = {"km_tol": None}


def spread_of(lam):
    """Facteur appliqué à l'étalement de la loi de δ autour de sa médiane pour un facteur de bande λ :
    "both" λ ; "widen" max(λ, 1) (resserrer ne touche que le flou de forme s)."""
    lam = np.asarray(lam, float)
    return np.maximum(lam, 1.0) if SPREAD["mode"] == "widen" else lam


def median_of(P):
    return DGRID[np.argmax(np.cumsum(P, 1) >= 0.5, 1)]


class Champ:
    """Champs du moteur pour un résultat R de `Moteur.run`."""

    def __init__(self, M, R, R_=None, sig=None, figes=None, placebo=False, seed=0):
        """figes : None ou `obs.PoidsCartesFiges` préparé (poids d'une carte durablement contredite réduit dans les
        secteurs figés, κ choisi dans la table du champ) ; placebo : r permutés entre tronçons du même mois."""
        self.M, self.R, self.C = M, R, M.C
        self.figes = figes
        self.kgrid = figes.KGRID if figes is not None else (0.0,)
        rng = np.random.default_rng(seed)
        self._perm = {t: rng.permutation(len(M.C.rows[t])) for t in M.C.months} if placebo else {}
        self._off = {}
        self.Rk, self.sig = (R_ or R_NB), (sig or SIG_NB)
        self._pairs = {}

    def pairs(self, t):
        """(maille Z, ligne du tronçon, poids) des mailles à ≤ 60 km de la ligne et ≤ 30 km du centre d'un tronçon."""
        if t in self._pairs:
            return self._pairs[t]
        sd = np.nan_to_num(S.SDe[t], nan=999)
        cand = np.flatnonzero(np.abs(sd) <= 60)
        jz, iz = S.jj[S.Zi][cand], S.ii[S.Zi][cand]
        rc = self.Rk / S.km
        pc, pr, pw = [], [], []
        for i, k in enumerate(self.C.rows[t]):
            cj, ci = (S.tiles[k] // 1000 + 0.5) * TILE - OFF, (S.tiles[k] % 1000 + 0.5) * TILE - OFF
            m = (np.abs(jz - cj) <= rc) & (np.abs(iz - ci) <= rc)
            c = np.flatnonzero(m)
            r2 = ((jz[c] - cj) ** 2 + (iz[c] - ci) ** 2) * S.km ** 2
            ok = r2 <= self.Rk ** 2
            pc.append(cand[c[ok]]); pr.append(np.full(ok.sum(), i)); pw.append(np.exp(-0.5 * r2[ok] / self.sig ** 2))
        out = (np.concatenate(pc), np.concatenate(pr), np.concatenate(pw))
        self._pairs[t] = out
        return out

    def tables(self, t, s, lam=None):
        """F (lignes × ZG) : probabilité russe d'une maille à distance z de la ligne des cartes, par tronçon."""
        P = self.R["post"][t]
        s = np.asarray(s, float)
        if lam is None:
            arg = (ZG[None, :, None] + DGRID[None, None, :]) / s[:, None, None]
        else:
            lam = np.asarray(lam, float)
            m = median_of(P)
            ld = spread_of(lam)
            sh = m[:, None] + ld[:, None] * (DGRID[None, :] - m[:, None])           # lignes × δ
            arg = (ZG[None, :, None] + sh[:, None, :]) / (lam * s)[:, None, None]
        return np.einsum("izd,id->iz", norm_cdf(arg), P)

    def offset(self, t, kappa):
        """Avance locale des cartes séparées o(x) ; κ > 0 : poids réduits des cartes durablement contredites dans les
        secteurs figés (`obs/figes.PoidsCartesFiges`)."""
        tm = self.M.models["temoins"]
        if not kappa or self.figes is None:
            return tm.off[t]
        if (t, kappa) not in self._off:
            Rr = self.figes.r(t, kappa, self._perm.get(t))                   # lignes × 3
            rz = np.ones((3, S.nZ), np.float32)
            rp = self.C.rpos[t][S.tinv]
            okz = rp >= 0
            rz[:, okz] = Rr[rp[okz]].T
            om = tm.offm[t]
            a = np.array(tm.AW, np.float32)[:, None] * rz * np.isfinite(om)
            den = a.sum(0)
            self._off[(t, kappa)] = np.where(den > 0, (a * np.nan_to_num(om)).sum(0) / np.maximum(den, 1e-9), 0.0).astype(np.float32)
        return self._off[(t, kappa)]

    def sdp(self, t, beta, gamma, kappa=0.0):
        sd = np.nan_to_num(S.SDe[t], nan=999).astype(np.float64)
        if beta:
            sd = sd + beta * self.M.vloc(t)
        if gamma:
            sd = sd + gamma * self.offset(t, kappa)
        if PROFIL["mode"] and PROFIL["alpha"]:
            sd = sd + PROFIL["alpha"] * self.profil(t)
        return sd

    def profil(self, t):
        """v3 (avancée PARTIELLE d'un tronçon) : le décalage médian m_k du tronçon n'est plus uniforme le long de la ligne,
        il est réparti selon un profil r(x) de moyenne 1 sur les mailles proches de la ligne du tronçon :
            terme = (r(x) − 1) · m(x),   m(x) = Σ_k ω_k(x) m_k (même voisinage que le champ),
        r(x) = (ε + a(x)) / (ε + moyenne du tronçon de a), borné à [0 ; 3], a = |vitesse locale des cartes| ("vit") ou
        intensité locale des combats ("act", log(1 + feux + UCDP + assauts) lissé). Aucun paramètre appris par tronçon :
        α (poids du profil) est fixé sur 2023-2024. Une partie du tronçon bouge (là où la ligne bougeait / où l'on se
        bat), le reste non ; la médiane du tronçon est conservée en moyenne."""
        key = (t, PROFIL["mode"])
        cache = self.__dict__.setdefault("_pf", {})
        if key in cache:
            return cache[key]
        sd = np.nan_to_num(S.SDe[t], nan=999)
        near = np.abs(sd) <= 6.0
        if PROFIL["mode"] == "vit":
            a, eps = np.abs(self.M.vloc(t)).astype(np.float64), 0.25
        else:
            from . import donnees as DN
            a = np.zeros(S.nZ)
            for src in ("feux", "ucdp", "em_assauts"):
                A, cv = DN.brut(src)
                if cv[t]:
                    a = a + A[t]
            a = S.smooth(S.full(np.log1p(a).astype(np.float32)), 2.0)[S.Zi].astype(np.float64)
            eps = 0.1
        num = np.bincount(S.tinv, weights=a * near, minlength=S.nT)
        den = np.bincount(S.tinv, weights=near.astype(float), minlength=S.nT)
        mt = num / np.maximum(den, 1)
        r = np.clip((eps + a) / (eps + mt[S.tinv]), 0, 3.0)
        med = median_of(self.R["post"][t])
        pc, pr, pw = self.pairs(t)
        mn = np.bincount(pc, weights=pw * med[pr], minlength=S.nZ)
        md = np.bincount(pc, weights=pw, minlength=S.nZ)
        m = np.where(md > 1e-9, mn / np.maximum(md, 1e-12), 0.0)
        out = ((r - 1.0) * m * (np.abs(sd) <= 30)).astype(np.float64)
        cache[key] = out
        return out

    def field(self, t, F, beta, gamma, sel=None, kappa=0.0):
        """Champ sur Z (champ des cartes là où aucun tronçon n'est voisin) ; sel : masque des paires à calculer."""
        pc, pr, pw = self.pairs(t)
        if sel is not None:
            pc, pr, pw = pc[sel], pr[sel], pw[sel]
        x = (np.clip(self.sdp(t, beta, gamma, kappa)[pc], ZG[0], ZG[-1]) - ZG[0]) / (ZG[1] - ZG[0])
        i0 = np.minimum(np.floor(x).astype(np.int64), len(ZG) - 2)
        fr = x - i0
        v = F[pr, i0] * (1 - fr) + F[pr, i0 + 1] * fr
        num = np.bincount(pc, weights=pw * v, minlength=S.nZ)
        den = np.bincount(pc, weights=pw, minlength=S.nZ)
        p = np.where(den > 1e-9, num / np.maximum(den, 1e-12), np.nan_to_num(S.Peq[t], nan=0.5))
        return p.astype(np.float32)

    def s_of(self, t, s0, sp):
        return s0 + sp * self.R["comp"][t][0]


# ---------------------------------------------------------------------------------------------------------------------
# couverture : points de la ligne vraie (t + 2), interpolés entre paires de mailles voisines (une russe, une ukrainienne)
# ---------------------------------------------------------------------------------------------------------------------
_PC = {}


def _pairs_truth(t):
    if t in _PC:
        return _PC[t]
    Tf = np.full(S.nU, -1, np.int8)
    Tf[S.Zi] = S.T[t]
    pos = np.full((S.NY, S.NX), -1, np.int64)
    pos[S.jj, S.ii] = np.arange(S.nU)
    g = np.full((S.NY, S.NX), -1, np.int8)
    g[S.jj, S.ii] = Tf
    A, B = [], []
    for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb = np.roll(g, sh, axis=(0, 1)); npos = np.roll(pos, sh, axis=(0, 1))
        m = (g == 1) & (nb == 0)
        A.append(pos[m]); B.append(npos[m])
    a, b = np.concatenate(A), np.concatenate(B)
    ok = (a >= 0) & (b >= 0)
    a, b = a[ok], b[ok]
    ok = S.isUA[a] & S.isUA[b]
    a, b = S.zpos[a[ok]], S.zpos[b[ok]]
    ok = (a >= 0) & (b >= 0)
    _PC[t] = (a[ok], b[ok])
    return _PC[t]


def covered(pZ, t):
    """(tuile, couvert) des points de la ligne vraie à t+2 pour le champ pZ du mois t (bande 5-95 %)."""
    a, b = _pairs_truth(t + H)
    u = np.random.default_rng(t).uniform(0, 1, len(a))
    x = 1 / (1 + np.exp(-((1 - u) * lgt(pZ[a]) + u * lgt(pZ[b]))))
    return SECZ[a], (x >= 0.05) & (x <= 0.95)


# ---------------------------------------------------------------------------------------------------------------------
# choix des paramètres du champ (walk-forward) et calibration de la bande par secteur
# ---------------------------------------------------------------------------------------------------------------------
FGRID_LARGE = [(b, g, s0, sp) for b in (1.0, 1.5, 2.0, 2.5, 3.5) for g in (0.35, 0.7, 1.0) for s0 in (1.0, 1.5)
               for sp in (4.0, 6.0, 10.0, 16.0, 25.0)]


def table_choix(CH, fgrid=None):
    """Pour chaque mois t dont la vérité t+2 est connue : sommes (Brier ≤ 30 km, n, Brier changeantes, n) de chaque
    point de FGRID, et celles de la production (nowcast publié), sur les mailles évaluées. Pas de calibration ici."""
    use_g = "temoins" in CH.R["dyn"]
    use_b = "mouvement" in CH.R["dyn"]
    grid = sorted({(b if use_b else 0.0, g if use_g else 0.0, s0, sp, kp) for b, g, s0, sp in (fgrid or FGRID) for kp in CH.kgrid})
    ll = {}
    for t in CH.C.months:
        if t + H not in S.built or not np.isfinite(S.Pnow[t]).any():
            continue
        idx = eval_cells(t)
        y = S.T[t + H][idx]
        k = y >= 0
        ch = k & (S.T[t][idx] >= 0) & (S.T[t][idx] != y)
        pp = S.Pnow[t][idx]
        prod = np.array([((pp[k] - y[k]) ** 2).sum(), k.sum(), ((pp[ch] - y[ch]) ** 2).sum(), ch.sum(),
                         float((pp[k] >= 0.5).sum() - (y[k] == 1).sum())], float)
        pc = CH.pairs(t)[0]
        inidx = np.zeros(S.nZ, bool); inidx[idx] = True
        sel = inidx[pc]
        Ftab = {}
        row = []
        for b, g, s0, sp, kp in grid:
            if (s0, sp) not in Ftab:
                Ftab[(s0, sp)] = CH.tables(t, CH.s_of(t, s0, sp))
            p = quant(CH.field(t, Ftab[(s0, sp)], b, g, sel=sel, kappa=kp)[idx])
            row.append([((p[k] - y[k]) ** 2).sum(), k.sum(), ((p[ch] - y[ch]) ** 2).sum(), ch.sum(),
                        float((p[k] >= 0.5).sum() - (y[k] == 1).sum())])
        ll[t] = (np.array(row, float), prod)
    return grid, ll


def choisir(grid, ll, t, use_b=True, use_g=True):
    past = [q for q in ll if t - 24 <= q <= t - H]
    if len(past) < 3:
        return ((DEFAULT[0] if use_b else 0.0), (DEFAULT[1] if use_g else 0.0), DEFAULT[2], DEFAULT[3], 0.0)
    dec = {q: 0.5 ** ((t - q) / 12) for q in past}
    A = sum(dec[q] * ll[q][0] for q in past)
    Pp = sum(dec[q] * ll[q][1] for q in past)
    rb = (A[:, 0] / A[:, 1]) / (Pp[0] / Pp[1])
    rc = (A[:, 2] / np.maximum(A[:, 3], 1)) / (Pp[2] / max(Pp[3], 1))
    ok = rb <= 0.97
    if CHOIX["km_tol"] is not None and A.shape[1] > 4:
        # v3 : ERREUR DE NIVEAU de la surface p ≥ 0,5 contre le consensus à q + 2 (mailles évaluées, km², moyenne pondérée
        # des mois passés) au plus celle du nowcast publié (ou km_tol) : la ligne ne doit pas s'emballer en surface.
        wsum = sum(dec.values())
        eg = 4.0 * A[:, 4] / wsum
        ep = 4.0 * Pp[4] / wsum
        okk = np.abs(eg) <= max(abs(ep), CHOIX["km_tol"])
        if (ok & okk).any():
            ok = ok & okk
        elif okk.any():
            ok = okk
    sc = np.where(ok, rc, 10 + rb + rc) if ok.any() else rb + rc
    best = grid[int(np.argmin(sc))]
    if len(grid[0]) < 5 or len({g[4] for g in grid}) == 1:
        return best
    # κ (poids des cartes contredites dans les secteurs figés) ne touche que des secteurs immobiles, donc presque pas les
    # mailles qui changent : choisi APRÈS les autres paramètres, au meilleur Brier ≤ 30 km passé, à condition de ne pas
    # dégrader le Brier passé des mailles qui changent par rapport à κ = 0 (règle « ne rien dégrader »)
    same = [i for i, g in enumerate(grid) if g[:4] == best[:4]]
    i0 = next(i for i in same if grid[i][4] == 0.0)
    cand = [i for i in same if rc[i] <= rc[i0] + 1e-9]
    return grid[min(cand, key=lambda i: (rb[i], grid[i][4]))]


LGRID = np.exp(np.linspace(-1.6, 2.4, 21))     # facteurs d'échelle λ essayés pour la bande (0,20 à 11)


def besoin(CH, t, s, b, g, kp=0.0):
    """Conformal LOCAL : pour chaque point de la ligne vraie à t+2 (mêmes points et même tirage que `covered`), le plus
    petit facteur λ de LGRID (appliqué à tous les tronçons) qui met le point dans la bande 5-95 % du champ du mois t.
    Renvoie (tuile, indice de λ ; len(LGRID) si aucun ne suffit)."""
    a, bb = _pairs_truth(t + H)
    u = np.random.default_rng(t).uniform(0, 1, len(a))
    pc = CH.pairs(t)[0]
    need = np.zeros(S.nZ, bool); need[a] = True; need[bb] = True
    sel = need[pc]
    first = np.full(len(a), len(LGRID), np.int64)
    n = len(CH.C.rows[t])
    for j, lam in enumerate(LGRID):
        p = quant(CH.field(t, CH.tables(t, s, np.full(n, lam)), b, g, sel=sel, kappa=kp))
        x = 1 / (1 + np.exp(-((1 - u) * lgt(p[a]) + u * lgt(p[bb]))))
        ok = (x >= 0.05) & (x <= 0.95) & (first == len(LGRID))
        first[ok] = j
    return SECZ[a], first


def besoins(CH, grid, ll):
    """{q: comptes (tronçons × (len(LGRID) + 1)) des λ nécessaires} pour les mois dont la vérité q + 2 est connue. Ne
    dépend que des paramètres du champ choisis à q (pas de la calibration) : calculé une fois par build."""
    use_g = "temoins" in CH.R["dyn"]
    use_b = "mouvement" in CH.R["dyn"]
    out = {}
    for q in CH.C.months:
        if (q + H) not in S.built:
            continue
        b, g, s0, sp, kp = choisir(grid, ll, q, use_b, use_g)
        tk, j = besoin(CH, q, CH.s_of(q, s0, sp), b, g, kp)
        Hn = np.zeros((S.nS, len(LGRID) + 1))
        np.add.at(Hn, (tk, j), 1.0)
        out[q] = Hn
    return out


def _neighbours():
    """Secteurs de 24 km voisins (8-voisinage, poids 0,3) de chaque secteur."""
    code = {int(c): i for i, c in enumerate(S.secs)}
    nb = {}
    for i, c in enumerate(S.secs):
        a, b = int(c) // 1000, int(c) % 1000
        nb[i] = [(code[(a + x) * 1000 + b + y], 1.0 if (x, y) == (0, 0) else 0.3)
                 for x in (-1, 0, 1) for y in (-1, 0, 1) if (a + x) * 1000 + b + y in code]
    return nb


def _wquant(v, w, q):
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return float(v[o][min(np.searchsorted(cw, q * cw[-1]), len(v) - 1)])


def champs(CH, grid, ll, calib="conformal", eta=1.0, target=0.05, clip=(-2.5, 2.7), prior_w=0.0, init_mean=True,
           level=0.95, hl=12.0, n0=30.0, win=24, lam_max=None, lam_min=None, need=None, gamma_a=0.0, a_clip=(0.005, 0.3), cls_bounds=None, cls_mode="shrink"):
    """Champs du moteur, mois par mois, avec la bande recalibrée par secteur.

    calib :
      "conformal" (PRODUCTION) : conformal LOCAL par secteur. Pour chaque point de la ligne vraie d'un mois passé q
          (q ≤ t − 2, vérité à q + 2 connue à t), `besoin` donne le plus petit facteur λ qui l'aurait mis dans la bande
          5-95 %. Au mois t, log λ_k = quantile `level` pondéré des log λ des points du secteur k et de ses 8 voisins
          (poids 0,3), des `win` derniers mois (demi-vie `hl` mois), rétréci vers le quantile de tous les secteurs
          (n_eff / (n_eff + n0)). λ_k s'applique au flou ET à l'étalement de la loi de δ autour de sa médiane ; il peut
          RESSERRER la bande (λ < 1) là où elle couvrait trop.
      "s" (recherche) / "spread" : conformal adaptatif c_k ← clip(c_k + η · (échec_k − 0,05)) avec deux mois de retard,
          sur le flou seul / sur le flou et l'étalement (trop lent à redescendre après une rafale d'échecs : 98,6 %) ;
      None : pas de calibration.
    Renvoie ({t: p sur Z quantifié}, choix, c final par tronçon, {t: λ par ligne du mois})."""
    use_g = "temoins" in CH.R["dyn"]
    use_b = "mouvement" in CH.R["dyn"]
    c, out, hist, choice, lams = {}, {}, {}, {}, {}
    NL = len(LGRID)
    lv = np.r_[np.log(LGRID), np.log(LGRID[-1]) + 0.25]       # valeur de la classe « aucun λ ne suffit »
    nb = _neighbours()
    alpha = {}
    if cls_bounds:
        raise NotImplementedError("classes de risque : retirées en v2 (non utilisées en production)")
    if calib == "conformal" and need is None:
        need = besoins(CH, grid, ll)                           # q -> (nT × (NL + 1)) comptes des λ nécessaires
    for t in CH.C.months:
        b, g, s0, sp, kp = choisir(grid, ll, t, use_b, use_g)
        choice[t] = (b, g, s0, sp, kp)
        s = CH.s_of(t, s0, sp)
        if calib == "conformal":
            # niveau adaptatif par secteur (comme conformal.py) : α_k ← clip(α_k + γ·(0,05 − échec_k)), échec constaté
            # sur le champ publié deux mois plus tôt
            if gamma_a and (t - H) in hist:
                tk, inn = hist.pop(t - H)
                for k in np.unique(tk):
                    m = tk == k
                    if m.sum() >= 5:
                        a0 = alpha.get(int(k), 1 - level)
                        alpha[int(k)] = float(np.clip(a0 + gamma_a * (target - (1 - inn[m].mean())), *a_clip))
            past = [q for q in need if t - win <= q <= t - H]
            lam = np.ones(len(CH.C.rows[t]))
            if past:
                # λ par SECTEUR de 24 km (le tronçon prend le λ de son secteur : calibration inchangée quand TILE < 12)
                Hq = sum(0.5 ** ((t - q) / hl) * need[q] for q in past)             # nS × (NL + 1)
                tot = Hq.sum(0)
                lam_s = {}
                for sk in np.unique(S.sec_of_tile[CH.C.rows[t]]):
                    sk = int(sk)
                    lev = 1 - alpha.get(sk, 1 - level)
                    glob = _wquant(lv, tot, lev) if tot.sum() > 0 else 0.0
                    hk = sum(wn * Hq[kk] for kk, wn in nb[sk])
                    ne = float(hk.sum())
                    loc = _wquant(lv, hk, lev) if ne > 0 else glob
                    lam_s[sk] = (ne * loc + n0 * glob) / (ne + n0)
                cv = np.array([lam_s[int(S.sec_of_tile[k])] for k in CH.C.rows[t]])
                lam = np.clip(np.exp(cv), lam_min or 0.0, lam_max or np.inf)
                c = {int(k): float(np.log(v)) for k, v in zip(CH.C.rows[t], lam)}
            F = CH.tables(t, s, lam)
            lams[t] = lam
            out[t] = quant(CH.field(t, F, b, g, kappa=kp))
            if gamma_a and (t + H) in S.built:
                hist[t] = covered(out[t], t)
            continue
        if calib and (t - H) in hist:
            tk, inn = hist.pop(t - H)
            for k in np.unique(tk):
                m = tk == k
                if m.sum() >= 5:
                    c0 = c.get(int(k), float(np.mean(list(c.values()))) if (c and init_mean) else 0.0)
                    c[int(k)] = float(np.clip(c0 + eta * ((1 - inn[m].mean()) - target), *clip))
            if prior_w and c:
                mu = float(np.mean(list(c.values())))
                c = {k: (1 - prior_w) * v + prior_w * mu for k, v in c.items()}
        cm = float(np.mean(list(c.values()))) if (c and init_mean) else 0.0
        cv = np.array([c.get(int(k), cm) for k in CH.C.rows[t]])
        if calib == "s":
            F = CH.tables(t, s * np.exp(cv))
            lam = np.ones(len(cv))
        elif calib == "spread":
            lam = np.exp(cv)
            F = CH.tables(t, s, lam)
        else:
            lam = np.ones(len(cv))
            F = CH.tables(t, s)
        lams[t] = lam
        p = quant(CH.field(t, F, b, g, kappa=kp))
        out[t] = p
        if calib and (t + H) in S.built:
            hist[t] = covered(p, t)
    return out, choice, c, lams
