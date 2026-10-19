"""SECTEURS FIGÉS (v2, 19-20/10/2026) : biais durable des cartes appris par secteur, appliqué SEULEMENT là où la ligne est
figée (aucune avance à protéger).

Problème : là où aucune carte ne bouge depuis longtemps, une carte durablement contredite par les deux autres (ex. rive
droite du Dnipro, Antonivka–Kozatske : Wikipédia code russes des localités de la rive droite depuis 2023-12, contre
DeepState et l'ISW) tire la ligne des cartes (vue équilibrée) de plusieurs km, et le moteur la suit : sa composante
« stable » N(b₀, σ₀²) est centrée sur le biais MOYEN de tous les tronçons. Le poids dynamique par carte du 19/10
(`source_consensus.py`, non retenu) touchait aussi les secteurs actifs et cassait l'avance de DeepState à Lyman.

Modèle : pour le tronçon k au mois t,
    figé_k(t) = la ligne des cartes (vue équilibrée) ET la ligne de chaque carte présente (DeepState, ISW, Wikipédia)
                n'ont pas bougé de plus de `thr` km (profondeur moyenne du tronçon) entre t − W et t ;
    B_k(t)    = Σ_q w_q · δ_vrai(q, k) / (Σ_q w_q + n₀),  q ∈ [t − 24, t − 2] tels que figé_k(q) ET figé_k(q + 2)
                (vérité à q + 2 connue à t ; w_q = 0,5^((t − q)/hl)) : biais durable des cartes dans le secteur figé,
                rétréci vers 0 ;
    composante stable de l'a priori : N(b₀ + ρ · B_k(t) · figé_k(t), σ₀²)
ρ est APPRIS chaque mois dans le moteur (montée de coordonnées sur le score de δ vrai, q ≤ t − 2, comme les autres
poids). Hors secteurs figés, rien ne change (B n'est pas appliqué). Walk-forward strict : tout ce qui sert à t est
connu à t. Placebo : (B, figé) permutés entre tronçons du même mois.
(W, thr, hl, n₀) : choisis sur 2023-2024 seulement, puis gelés (docs/ETAT.md § 6, 19-20/10/2026).
"""
import numpy as np

from ..commun import H, S, TILE

RGRID = (0.0, 0.25, 0.5, 0.75, 1.0)


class SecteursFiges:
    KIND = "figes"
    name, label = "figes", "Secteur figé : biais durable des cartes appris dans le secteur"

    def __init__(self, W=6, thr=0.5, hl=12.0, n0=2.0, win=24):
        self.W, self.thr, self.hl, self.n0, self.win = W, thr, hl, n0, win
        self.B, self.frz = {}, {}

    def prepare(self, C, temoins):
        """temoins : modèle dynamique « temoins » préparé (décalage δ_m de chaque carte par tronçon et par mois)."""
        a2 = S.km ** 2 / (TILE * S.km)
        pool = {}
        for t in C.months:
            pool[t] = np.bincount(S.tinv, weights=(np.nan_to_num(S.Peq[t]) > 0.5), minlength=S.nT) * a2
        # position (km de profondeur) de la ligne de chaque carte : ligne des cartes + δ_m ; NaN si la carte manque
        pos = {}
        for t in C.months:
            mu = temoins.features(t)["mu"]
            dl, av = mu[:, :3], mu[:, 3:6] > 0
            P = np.full((S.nT, 4), np.nan)
            rows = np.asarray(C.rows[t])
            P[rows, 0] = pool[t][rows]
            P[rows, 1:] = np.where(av, pool[t][rows][:, None] + dl, np.nan)
            pos[t] = P
        self.frz_all = {}
        for t in C.months:
            rows = np.asarray(C.rows[t])
            ok = np.ones(len(rows), bool)
            ref = pos[t][rows]
            for q in range(t - self.W, t):
                if q not in pos:
                    ok[:] = False
                    break
                d = np.abs(pos[q][rows] - ref)
                seen = np.isfinite(pos[q][rows][:, 0])
                ok &= seen & np.all(np.nan_to_num(d, nan=0.0) <= self.thr, 1)
            self.frz_all[t] = dict(zip(rows.tolist(), ok.tolist()))
        for t in C.months:
            rows = C.rows[t]
            B = np.zeros(len(rows))
            past = [q for q in C.months if t - self.win <= q <= t - H]
            for i, k in enumerate(rows):
                num = den = 0.0
                for q in past:
                    if not (self.frz_all[q].get(k) and self.frz_all.get(q + H, {}).get(k)):
                        continue
                    d = C.d2.get((q, k))
                    if d is None:
                        continue
                    w = 0.5 ** ((t - q) / self.hl)
                    num += w * d; den += w
                B[i] = num / (den + self.n0) if den else 0.0
            self.B[t] = B
            self.frz[t] = np.array([bool(self.frz_all[t].get(k)) for k in rows])

    def shift(self, t, perm=None):
        """Décalage du centre de la composante stable, par ligne du mois t (avant le facteur ρ appris)."""
        v = self.B[t] * self.frz[t]
        return v[perm] if perm is not None else v

    def formula(self, t=None):
        return (f"secteurs figés : W = {self.W} mois, seuil {self.thr} km, demi-vie {self.hl} mois, n₀ = {self.n0} ; "
                f"centre stable b₀ + ρ·B_k si figé")


class PoidsCartesFiges:
    """POIDS D'UNE CARTE DURABLEMENT CONTREDITE, réduit SEULEMENT dans les secteurs figés (v2, 19-20/10/2026).

    Le décalage uniforme δ du tronçon ne voit pas une bosse locale (rive droite du Dnipro : Wikipédia code russes des
    localités jusqu'à 20 km du fleuve sur une partie du tronçon, δ vrai du tronçon ≈ 0). La correction porte donc sur
    l'avance LOCALE des cartes séparées dans le champ, o(x) = Σ_m a_m·(d_m(x) − d(x)) / Σ_m a_m (a = 0,6 DeepState,
    0,25 ISW, 0,15 Wikipédia), qui devient

        o_κ(x) = Σ_m a_m·r_m,k·(d_m(x) − d(x)) / Σ_m a_m·r_m,k ,   r_m,k = 1 − min(1, κ·x_m,k)  si le tronçon k est figé,
                                                                  r_m,k = 1                  sinon ;

      - figé_k(t) : entre t − W et t, aucune maille du tronçon à ≤ 30 km de la ligne n'a changé d'état dans AUCUNE
        carte présente ni dans le consensus, à `nthr` mailles près (une avance de DeepState, comme à Lyman en 2026,
        rend le secteur non figé : rien n'y change) ;
      - e_m,k(t) : part des mailles connues à ≤ 15 km de la ligne, dans les mois passés q ∈ [t − 24, t − 2] où le
        tronçon était figé (au mois q ET à q + 2), où la carte m contredisait le consensus de q + 2 (vérité connue à t),
        rétrécie (n₀ mailles) ; x_m,k = e_m,k − min des autres cartes (excès d'erreur, ≥ 0) ;
      - κ ∈ KGRID choisi chaque mois en walk-forward avec les autres paramètres du champ (`champ.table_choix`, même
        règle « ne rien dégrader » : Brier ≤ 30 km passé ≤ 0,97 × production, puis meilleur Brier des mailles qui
        changent). Placebo : r permutés entre tronçons du même mois.
    """
    KGRID = (0.0, 4.0, 12.0)

    def __init__(self, W=6, nthr=2, n0=200.0, hl=12.0, win=24, near=15.0):
        self.W, self.nthr, self.n0, self.hl, self.win, self.near = W, nthr, n0, hl, win, near
        self.x, self.frz = {}, {}

    def prepare(self, C, temoins):
        T = S.T
        frz_all = {}
        for t in C.months:
            st = temoins.stm[t]
            sd = np.abs(np.nan_to_num(S.SDe[t], nan=999)) <= 30
            ok = {}
            for k in C.rows[t]:
                z = S.tile_cells[k]
                z = z[sd[z]]
                ch = 0
                good = True
                for q in range(t - self.W, t):
                    if q not in temoins.stm:
                        good = False
                        break
                    sq = temoins.stm[q]
                    pres = (st[:, z] > 0).any(1) & (sq[:, z] > 0).any(1)
                    diff = ((sq[:, z] != st[:, z]) & pres[:, None]).any(0) | (T[q][z] != T[t][z])
                    ch = max(ch, int(diff.sum()))
                    if ch > self.nthr:
                        good = False
                        break
                ok[k] = good
            frz_all[t] = ok
        self.frz_all = frz_all
        # erreurs des cartes dans les mois figés passés (vérité à q + 2)
        err = {}
        for q in C.months:
            if (q + H) not in S.built or (q + H) not in frz_all:
                continue
            st = temoins.stm[q]
            yv = T[q + H]
            near = np.abs(np.nan_to_num(S.SDe[q], nan=999)) <= self.near
            e = {}
            for k in C.rows[q]:
                if not (frz_all[q].get(k) and frz_all[q + H].get(k)):
                    continue
                z = S.tile_cells[k]
                z = z[near[z] & (yv[z] >= 0)]
                v = np.zeros((3, 2))
                for mi in range(3):
                    s_ = st[mi, z]
                    m = (s_ == 1) | (s_ == 2)
                    v[mi] = [float(((s_[m] == 2) != (yv[z][m] == 1)).sum()), float(m.sum())]
                e[k] = v
            err[q] = e
        for t in C.months:
            rows = C.rows[t]
            X = np.zeros((len(rows), 3))
            past = [q for q in err if t - self.win <= q <= t - H]
            for i, k in enumerate(rows):
                acc = np.zeros((3, 2))
                for q in past:
                    v = err[q].get(k)
                    if v is not None:
                        acc += 0.5 ** ((t - q) / self.hl) * v
                if acc[:, 1].max() <= 0:
                    continue
                gm = acc[:, 0].sum() / max(acc[:, 1].sum(), 1)            # erreur commune du tronçon (rétrécissement)
                e = (acc[:, 0] + self.n0 * gm) / (acc[:, 1] + self.n0)
                e = np.where(acc[:, 1] > 0, e, np.nan)
                for mi in range(3):
                    oth = [e[j] for j in range(3) if j != mi and np.isfinite(e[j])]
                    if np.isfinite(e[mi]) and oth:
                        X[i, mi] = max(0.0, e[mi] - min(oth))
            self.x[t] = X
            self.frz[t] = np.array([bool(frz_all[t].get(k)) for k in rows])

    def r(self, t, kappa, perm=None):
        """r (lignes du mois t × 3 cartes) pour κ."""
        R = np.where(self.frz[t][:, None], 1 - np.minimum(1.0, kappa * self.x[t]), 1.0)
        return R[perm] if perm is not None else R
