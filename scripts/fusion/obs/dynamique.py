"""Modèles d'observation de l'ÉTAT DYNAMIQUE du tronçon (pression, sens, vitesse) : ils ne disent pas OÙ est la ligne
mais vers où elle va. Ils entrent dans la composante « mouvement » de l'a priori des cartes (`cartes.py`) :

    δ ~ (1 − π) · N(b₀, σ₀²) + π · t₄(μ, σ₁)
    logit π = a₀ + Σ_j a_j · g_j      (g : intensité / probabilité de mouvement)
    μ       = c₀ + Σ_j c_j · h_j      (h : variables ORIENTÉES, > 0 = poussée russe)

Chaque modèle fournit ses variables g (sans sens) et h (orientées) par tronçon et par mois, toutes connues au mois t :

  mouvement   vitesse des cartes : ΔY sur 1 mois et pente sur 3 mois (km de profondeur, > 0 = gain russe) ; |·| dans π.
  asymetrie   combats derrière la ligne (formules de `sectors.asym`) : a, z ; |a|, |z| dans π.
  impacts_s2  impacts Sentinel-2 par côté (data/impacts_troncons_decades.csv) : z_RU − z_UA et indicatrice « S2
              disponible » dans μ ; max z et disponibilité dans π (version de recherche).
  s2_sens     variante revue : z_RU − z_UA dans μ SEULEMENT (sens), rien dans π.
  tg_oriente  Telegram orienté : (activité UA signalée − activité RU) / (total + 2) à ≤ 20 km ; log(1 + total) dans π.
  activite    intensité des combats près de la ligne (feux, UCDP, pertes, assauts) dans π seulement.
  memoire     biais et mouvement passés du secteur (δ vrais passés, demi-vie 6 mois, voisins 0,3).
  temoins     chaque carte (DeepState corrigé, ISW tant qu'elle paraît, Wikipédia) comme témoin séparé : décalage δ_m de
              sa ligne dans le tronçon (états par localité publiés dans `FRONT.months[].d`).
  liste_dyn   liste officielle comme PRESSION : part côté UA à ≤ 15 km et hausse sur un mois.
  assauts_dyn variante revue des assauts de l'état-major UA comme PRESSION : log(1 + assauts) côté UA à ≤ 10 km et sa
              hausse sur un mois (μ), log(1 + assauts) (π).
"""
import csv
import os

import numpy as np

from ..commun import DATA, DGRID, S
from .. import donnees as DN


class Dynamique:
    KIND = "dynamique"

    def __init__(self, name, label):
        self.name, self.label = name, label
        self.F = {}
        self.params = {}

    def prepare(self, C):
        getattr(self, "_prep_" + self.name)(C)

    # --- variables par mois : {"mu": (n, a), "pi": (n, b)} ---
    def _prep_mouvement(self, C):
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        for t in C.months:
            v1 = np.array([C.dmap.get((t, k), 0.0) for k in C.rows[t]])
            v3 = np.array([(S.Y[(t, k)] - S.Y[(t - 3, k)]) / 3 if (t - 3, k) in S.Y else C.dmap.get((t, k), 0.0) for k in C.rows[t]])
            self.F[t] = {"mu": np.c_[v1, v3], "pi": np.c_[np.abs(v1), np.abs(v3)]}
        self.mu_names, self.pi_names = ["vitesse 1 mois", "vitesse 3 mois"], ["|vitesse 1 mois|", "|vitesse 3 mois|"]

    def _prep_asymetrie(self, C):
        a, z = DN.asymetrie()
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        for t in C.months:
            ma, _ = C.tile_mean(t, a[t], maxd=10.0)
            mz, _ = C.tile_mean(t, z[t], maxd=10.0)
            self.F[t] = {"mu": np.c_[ma, mz], "pi": np.c_[np.abs(ma), np.abs(mz)]}
        self.mu_names, self.pi_names = ["asymétrie a", "asymétrie z"], ["|a|", "|z|"]

    def _s2(self, C):
        """Par mois et tronçon : (z_RU, z_UA) moyens des décades du mois (secteurs de 20 km répartis au prorata des
        mailles sur les tuiles de 24 km), None si un côté manque."""
        zs = {}
        for r in csv.DictReader(open(os.path.join(DATA, "impacts_troncons_decades.csv"), encoding="utf-8")):
            if r["s2_z"] in ("", None):
                continue
            t = S.midx.get(r["decade"][:7])
            if t is None:
                continue
            a, b = map(int, r["sec"].split("_"))
            zs.setdefault((t, a, b, r["side"]), []).append(float(r["s2_z"]))
        sec = (S.jj // 10) * 1000 + S.ii // 10
        tl = (S.jj // 12) * 1000 + S.ii // 12
        pairs = {}
        for s_, k_ in zip(sec, tl):
            d = pairs.setdefault(int(k_), {})
            d[int(s_)] = d.get(int(s_), 0) + 1
        out = {}
        for t in C.months:
            row = []
            for k in C.rows[t]:
                code = int(S.tiles[k])
                num = {"RU": 0.0, "UA": 0.0}; den = {"RU": 0.0, "UA": 0.0}
                for s_, n in pairs.get(code, {}).items():
                    for side in ("RU", "UA"):
                        v = zs.get((t, s_ // 1000, s_ % 1000, side))
                        if v:
                            num[side] += n * float(np.mean(v)); den[side] += n
                row.append((num["RU"] / den["RU"], num["UA"] / den["UA"]) if den["RU"] > 0 and den["UA"] > 0 else None)
            out[t] = row
        return out

    def _prep_impacts_s2(self, C):
        Z = self._s2(C)
        self.cover = np.zeros(S.NM, bool)
        for t in C.months:
            mu, pi = [], []
            for v in Z[t]:
                if v is not None:
                    zr, zu = v
                    mu.append([np.clip(zr - zu, -10, 10), 1.0]); pi.append([np.clip(max(zr, zu), -3, 10), 1.0])
                    self.cover[t] = True
                else:
                    mu.append([0.0, 0.0]); pi.append([0.0, 0.0])
            self.F[t] = {"mu": np.array(mu), "pi": np.array(pi)}
        self.mu_names, self.pi_names = ["z impacts RU − UA", "S2 disponible"], ["max z impacts", "S2 disponible"]

    def _prep_s2_sens(self, C):
        Z = self._s2(C)
        self.cover = np.zeros(S.NM, bool)
        for t in C.months:
            mu = []
            for v in Z[t]:
                mu.append([np.clip(v[0] - v[1], -10, 10) if v is not None else 0.0])
                self.cover[t] |= v is not None
            self.F[t] = {"mu": np.array(mu), "pi": np.zeros((len(mu), 0))}
        self.mu_names, self.pi_names = ["z impacts RU − UA (sens)"], []

    def _prep_tg_oriente(self, C):
        A = {c: DN.brut("tg_" + c)[0] for c in ("att_ua", "att_ru", "pres_ua", "pres_ru")}
        self.cover = DN.brut("tg_att_ua")[1]
        for t in C.months:
            if not self.cover[t]:
                n = len(C.rows[t]); self.F[t] = {"mu": np.zeros((n, 1)), "pi": np.zeros((n, 1))}; continue
            ua = A["att_ua"][t] + A["pres_ua"][t]
            ru = A["att_ru"][t] + A["pres_ru"][t]
            mu_, n_ = C.tile_mean(t, ua, maxd=20.0)
            mr_, _ = C.tile_mean(t, ru, maxd=20.0)
            u, r = mu_ * n_, mr_ * n_
            self.F[t] = {"mu": ((u - r) / (u + r + 2))[:, None], "pi": np.log1p(u + r)[:, None]}
        self.mu_names, self.pi_names = ["Telegram (UA − RU)/(total + 2)"], ["log(1 + mentions)"]

    def _prep_activite(self, C):
        srcs = ["feux", "ucdp", "pertes", "em_assauts"]
        arr = {s: DN.brut(s) for s in srcs}
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        for t in C.months:
            cols = []
            for s in srcs:
                A, cv = arr[s]
                if cv[t]:
                    m, n = C.tile_mean(t, A[t], maxd=10.0)
                    cols.append(np.log1p(m * n))
                else:
                    cols.append(np.zeros(len(C.rows[t])))
            self.F[t] = {"mu": np.zeros((len(C.rows[t]), 0)), "pi": np.c_[tuple(cols)]}
        self.mu_names, self.pi_names = [], [f"log(1 + {s} à ≤ 10 km)" for s in srcs]

    def _prep_assauts_dyn(self, C):
        A, cv = DN.brut("em_assauts")
        self.cover = cv
        for t in C.months:
            n = len(C.rows[t])
            if not cv[t]:
                self.F[t] = {"mu": np.zeros((n, 2)), "pi": np.zeros((n, 1))}
                continue
            m, nn = C.tile_mean(t, A[t], side="ua", maxd=10.0)
            cur = np.log1p(m * nn)
            if (t - 1) in C.rows and cv[t - 1]:
                m0, n0 = C.tile_mean(t, A[t - 1], side="ua", maxd=10.0)
                prev = np.log1p(m0 * n0)
            else:
                prev = cur
            self.F[t] = {"mu": np.c_[cur, cur - prev], "pi": cur[:, None]}
        self.mu_names, self.pi_names = ["assauts côté UA (log)", "assauts : hausse 1 mois"], ["assauts côté UA (log)"]

    def _prep_memoire(self, C):
        """Biais et retard des cartes PAR SECTEUR : moyenne pondérée des δ vrais passés du tronçon (q ≤ t − 2,
        demi-vie 6 mois ; voisins à ≤ 36 km, poids 0,3), rétrécie × n/(n + 3) ; et moyenne de |δ| (mouvement passé)."""
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        code = {int(c): i for i, c in enumerate(S.tiles)}
        nb = {}
        for i, c in enumerate(S.tiles):
            a, b = int(c) // 1000, int(c) % 1000
            nb[i] = [(code[(a + x) * 1000 + b + y], 1.0 if (x, y) == (0, 0) else 0.3)
                     for x in (-1, 0, 1) for y in (-1, 0, 1) if (a + x) * 1000 + b + y in code]
        for t in C.months:
            mu, pi = [], []
            past = [q for q in C.months if t - 24 <= q <= t - 2]
            for k in C.rows[t]:
                num = den = numa = 0.0
                for q in past:
                    w0 = 0.5 ** ((t - q) / 6)
                    for kk, wn in nb[k]:
                        d = C.d2.get((q, kk))
                        if d is None:
                            continue
                        w = w0 * wn
                        num += w * d; numa += w * abs(d); den += w
                sh = den / (den + 3.0)
                mu.append([sh * num / den if den else 0.0]); pi.append([sh * numa / den if den else 0.0])
            self.F[t] = {"mu": np.array(mu), "pi": np.array(pi)}
        self.mu_names, self.pi_names = ["biais passé du secteur (δ moyen)"], ["mouvement passé du secteur (|δ| moyen)"]

    def _prep_temoins(self, C):
        """Chaque carte comme TÉMOIN séparé (DeepState corrigé par son journal daté, ISW tant qu'elle paraît, Wikipédia ;
        états par localité de l'instantané publié) : décalage δ_m de la ligne de la carte m par rapport à la ligne du
        champ équilibré dans le tronçon (même calcul que δ vrai). Dans μ : δ_DS, δ_ISW, δ_Wiki (0 si absente) et leurs
        indicatrices ; dans π : |δ_m| et l'écart entre cartes (désaccord). `self.off[t]` : avance LOCALE pondérée des
        cartes (a = 0,6 DeepState, 0,25 ISW, 0,15 Wikipédia), maille par maille, pour le champ."""
        cellZ = S.cellU[S.Zi]
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        AW = (0.6, 0.25, 0.15)
        self.off = {}
        for t in C.months:
            sd = np.nan_to_num(S.SDe[t], nan=999)
            n = len(C.rows[t])
            dl = np.zeros((n, 3)); av = np.zeros((n, 3))
            onum = np.zeros(S.nZ, np.float32); oden = 0.0
            codes = S.codes.get(t)
            if codes is not None:
                # localité absente des différences publiées (tous codes nuls) : ukrainienne pour toute carte présente
                omitted = (codes == 0).all(1)
                for mi in range(3):
                    if mi == 1 and not S.isw_live[t]:
                        continue
                    col = codes[:, mi]
                    if not (col > 0).any():     # carte absente ce mois-là
                        continue
                    col = np.where(omitted, 1, col)
                    st = np.where(cellZ >= 0, col[np.maximum(cellZ, 0)], 0)
                    v = np.where(st == 2, 1.0, np.where(st == 1, 0.0, np.nan_to_num(S.Peq[t], nan=0.5)))
                    v = np.where(C.isUAz, v, np.nan_to_num(S.Peq[t], nan=1.0)).astype(np.float32)
                    dm = S.sd_of(S.full(v, fill=np.nan))[S.Zi]
                    o = np.clip(np.nan_to_num(dm - sd, nan=0.0), -15, 15) * (np.abs(sd) <= 20)
                    onum += AW[mi] * S.smooth(S.full(o), 1.5)[S.Zi]; oden += AW[mi]
                    for r, k in enumerate(C.rows[t]):
                        z = S.tile_cells[k]
                        mk = (np.abs(sd[z]) <= 30) & ((st[z] == 1) | (st[z] == 2))
                        if mk.sum() < 10:
                            continue
                        x = sd[z][mk][:, None] + DGRID[None, :]
                        ru = (st[z][mk] == 2)[:, None]
                        miss = (ru & (x <= 0)).sum(0) + (~ru & (x > 0)).sum(0) + 1e-3 * np.abs(DGRID)
                        dl[r, mi] = DGRID[int(np.argmin(miss))]; av[r, mi] = 1.0
            with np.errstate(invalid="ignore"):
                spread = np.where(av.sum(1) >= 2, np.nanmax(np.where(av > 0, dl, np.nan), 1) - np.nanmin(np.where(av > 0, dl, np.nan), 1), 0.0)
            spread = np.nan_to_num(spread)
            self.off[t] = (onum / oden if oden else onum).astype(np.float32)
            self.F[t] = {"mu": np.c_[dl, av], "pi": np.c_[np.abs(dl), spread]}
        self.mu_names = ["δ DeepState", "δ ISW", "δ Wikipédia", "DeepState présent", "ISW présent", "Wikipédia présent"]
        self.pi_names = ["|δ DeepState|", "|δ ISW|", "|δ Wikipédia|", "désaccord des cartes"]

    def _prep_liste_dyn(self, C):
        """Liste officielle rejouée comme indicateur de PRESSION : part des localités en hostilités actives côté
        ukrainien à ≤ 15 km de la ligne, et sa hausse sur un mois (nouvelles hromadas inscrites)."""
        A, cover = DN.brut("liste")
        self.cover = cover
        for t in C.months:
            ua, _ = C.tile_mean(t, A[t], side="ua", maxd=15.0)
            prev = A[t - 1] if t - 1 in C.rows else A[t]
            ua0, _ = C.tile_mean(t, prev, side="ua", maxd=15.0)
            self.F[t] = {"mu": np.c_[ua, ua - ua0], "pi": np.c_[ua, np.abs(ua - ua0)]}
        self.mu_names, self.pi_names = ["liste : part côté UA", "liste : hausse 1 mois"], ["liste : part côté UA", "liste : |hausse|"]

    def features(self, t):
        return self.F[t]

    def formula(self, t=None):
        return f"{self.label} : μ ← {', '.join(self.mu_names) or '—'} ; π ← {', '.join(self.pi_names) or '—'}"
