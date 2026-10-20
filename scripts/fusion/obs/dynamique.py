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
  v3 (20/10/2026) :
  kab_dyn     alertes KAB comme PRESSION (côté UA à ≤ 20 km, niveau et hausse) — RETENU ;
  ookla_dyn   baisse des tests Ookla côté UA à ≤ 15 km (niveau dans π, hausse sur 3 mois dans μ) — RETENU ;
  nowcast     décalage implicite du nowcast publié (témoin du DÉCALAGE, pas une carte de plus) — RETENU ;
  s2v2        cratères Sentinel-2 v2 par côté (z RU − UA dans μ, max z dans π) — testé, non retenu ;
  tg_msg      Telegram orienté par message (tuiles de 24 km) — testé, non retenu.
"""
import csv
import os

import numpy as np

from ..commun import DATA, DGRID, NMIN_SHIFT, OFF, S, TILE
from .. import donnees as DN


def tile_neighbours(r=None, w_nb=0.3):
    """Tronçons voisins de chaque tronçon : rayon r tronçons (défaut : ±24 km, soit 1 tronçon de 24 km en v1, 2 de 12 km),
    poids 1 pour le tronçon lui-même, w_nb pour les voisins."""
    r = r or max(1, int(round(12 / TILE)))
    code = {int(c): i for i, c in enumerate(S.tiles)}
    nb = {}
    for i, c in enumerate(S.tiles):
        a, b = int(c) // 1000, int(c) % 1000
        nb[i] = [(code[(a + x) * 1000 + b + y], 1.0 if (x, y) == (0, 0) else w_nb)
                 for x in range(-r, r + 1) for y in range(-r, r + 1) if (a + x) * 1000 + b + y in code]
    return nb


_TEMOINS = {}      # v4 : variables du modèle « temoins » (préparé avant « perime » dans le catalogue)


class Dynamique:
    KIND = "dynamique"

    def __init__(self, name, label):
        self.name, self.label = name, label
        self.F = {}
        self.params = {}
        self.colm = {}          # {t: (masque μ, masque π)} : variables dont la source existe au mois t (sinon : cover[t])

    def avail(self, t):
        """(masque des variables μ, masque des variables π) disponibles au mois t : une source absente ne contribue pas."""
        if t in self.colm:
            return self.colm[t]
        on = bool(self.cover[t]) if hasattr(self, "cover") else True
        return np.full(len(self.mu_names), on), np.full(len(self.pi_names), on)

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

    def _s2(self, C, fname="impacts_troncons_decades.csv", col="s2_z"):
        """Par mois et tronçon : (z_RU, z_UA) moyens des décades du mois (secteurs de 20 km répartis au prorata des
        mailles sur les tuiles de 24 km), None si un côté manque. fname : v1 (5 zones) ou v2 (cratères, tout le front)."""
        zs = {}
        path = os.path.join(DATA, fname)
        if not os.path.exists(path):
            return {t: [None] * len(C.rows[t]) for t in C.months}
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r.get(col) in ("", None):
                continue
            t = S.midx.get(r["decade"][:7])
            if t is None:
                continue
            a, b = map(int, r["sec"].split("_"))
            zs.setdefault((t, a, b, r["side"]), []).append(float(r[col]))
        sec = (S.jj // 10) * 1000 + S.ii // 10
        tl = ((S.jj + OFF) // TILE) * 1000 + (S.ii + OFF) // TILE
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

    def _prep_s2v2(self, C):
        """Cratères Sentinel-2 v2 (tout le front depuis 2024-07, z local sur 30 jours par tronçon de 20 km × côté, dès
        2025-01 ; précision ≈ 0,73 : un faux positif sur quatre) comme PRESSION PAR CÔTÉ : z_RU − z_UA dans μ (le camp
        pilonné est celui qui défend ou subit la poussée : signe appris), max(z_RU, z_UA) dans π (intensité). Positions
        individuelles non lues (agrégats publiés seulement). Absent : variables retirées (v2)."""
        Z = self._s2(C, "impacts_troncons_decades_v2.csv")
        self.cover = np.zeros(S.NM, bool)
        for t in C.months:
            mu, pi = [], []
            for v in Z[t]:
                if v is not None:
                    mu.append([np.clip(v[0] - v[1], -10, 10)]); pi.append([np.clip(max(v), -3, 10)])
                    self.cover[t] = True
                else:
                    mu.append([0.0]); pi.append([0.0])
            self.F[t] = {"mu": np.array(mu), "pi": np.array(pi)}
        self.mu_names, self.pi_names = ["cratères S2 v2 : z RU − UA"], ["cratères S2 v2 : max z"]

    def _prep_tg_msg(self, C):
        """Telegram ORIENTÉ, attribution au MESSAGE (data/telegram_troncons.csv, tuile de 24 km × mois) :
        (UA − RU)/(total + 2) dans μ, log(1 + messages de combat) dans π. Remplace la version par mailles (attribution à
        la phrase, réservée à l'affichage)."""
        D, cover = DN.telegram_troncons()
        self.cover = cover
        for t in C.months:
            n = len(C.rows[t])
            if not cover[t]:
                self.F[t] = {"mu": np.zeros((n, 1)), "pi": np.zeros((n, 1))}; continue
            v = np.array([D.get((t, int(S.tiles[k])), (0.0, 0.0, 0.0)) for k in C.rows[t]], float).reshape(n, 3)
            self.F[t] = {"mu": ((v[:, 0] - v[:, 1]) / (v[:, 0] + v[:, 1] + 2))[:, None], "pi": np.log1p(v[:, 2])[:, None]}
        self.mu_names, self.pi_names = ["Telegram (message) (UA − RU)/(total + 2)"], ["Telegram (message) log(1 + combats)"]

    def _prep_kab_dyn(self, C):
        """Alertes KAB (canal officiel, data/alertes_kab.csv) comme PRESSION : log(1 + alertes) côté UA à ≤ 20 km et sa
        hausse sur un mois (μ), log(1 + alertes) (π). La KAB précède l'assaut : signal de pression russe."""
        A, cv = DN.brut("kab")
        self.cover = cv
        for t in C.months:
            n = len(C.rows[t])
            if not cv[t]:
                self.F[t] = {"mu": np.zeros((n, 2)), "pi": np.zeros((n, 1))}; continue
            m, nn = C.tile_mean(t, A[t], side="ua", maxd=20.0)
            cur = np.log1p(m * nn)
            if (t - 1) in C.rows and cv[t - 1]:
                m0, n0 = C.tile_mean(t, A[t - 1], side="ua", maxd=20.0)
                prev = np.log1p(m0 * n0)
            else:
                prev = cur
            self.F[t] = {"mu": np.c_[cur, cur - prev], "pi": cur[:, None]}
        self.mu_names, self.pi_names = ["KAB côté UA (log)", "KAB : hausse 1 mois"], ["KAB côté UA (log)"]

    def _prep_ookla_dyn(self, C):
        """Ookla (baisse des tests de débit, bloc de 10 km, trimestre publié seulement) comme PRESSION côté UA à ≤ 15 km :
        niveau (π) et hausse sur 3 mois (μ : la population part devant une poussée)."""
        A, cv = DN.brut("ookla")
        self.cover = cv
        for t in C.months:
            n = len(C.rows[t])
            if not cv[t]:
                self.F[t] = {"mu": np.zeros((n, 1)), "pi": np.zeros((n, 1))}; continue
            m, _ = C.tile_mean(t, A[t], side="ua", maxd=15.0)
            m0, _ = C.tile_mean(t, A[t - 3], side="ua", maxd=15.0) if (t - 3) >= 0 and cv[t - 3] else (m, None)
            self.F[t] = {"mu": (m - m0)[:, None], "pi": m[:, None]}
        self.mu_names, self.pi_names = ["Ookla : hausse de la baisse sur 3 mois"], ["Ookla : baisse des tests (côté UA)"]

    def _prep_nowcast(self, C):
        """Nowcast PUBLIÉ comme témoin du DÉCALAGE (pas comme une carte de plus : il est construit sur les cartes) :
        δ_now = décalage de la ligne des cartes qui reproduit le nowcast dans le tronçon (μ), |δ_now| (π). Son
        coefficient est appris comme celui des cartes séparées (retard / biais), en walk-forward."""
        D = DN.nowcast_shift()
        self.cover = np.array([t in C.rows and any((t, k) in D for k in C.rows[t]) for t in range(S.NM)])
        for t in C.months:
            v = np.array([D.get((t, k), 0.0) for k in C.rows[t]])
            self.F[t] = {"mu": v[:, None], "pi": np.abs(v)[:, None]}
        self.mu_names, self.pi_names = ["δ nowcast publié"], ["|δ nowcast publié|"]

    def _crat(self, C, niveau):
        """v4 : cratères S2 v2 comme PRESSION PAR CÔTÉ (`donnees.crateres_troncons`) : densités par 100 km² comparés,
        d_RU et d_UA ; μ ← a·(log(1 + d_RU) − log(1 + d_UA)) et a ; π ← a·log(1 + d_RU + d_UA) et a, où a = 1 si le tronçon
        a été observé des deux côtés ce mois-là (sinon 0 : ABSENT, pas « zéro cratère »). L'indicatrice a garde à part
        les tronçons-mois sans observation (passé 2023-24 compris) : l'absence n'est ni une pénalité ni un calme. Mois sans
        aucune observation : variables retirées (v2)."""
        D, cv = DN.crateres_troncons(niveau)
        self.cover = cv
        for t in C.months:
            n = len(C.rows[t])
            mu, pi = np.zeros((n, 2)), np.zeros((n, 2))
            for i, k in enumerate(C.rows[t]):
                v = D.get((t, k))
                if v is None or v[2] < 20 or v[3] < 20:
                    continue
                dr, du = 100 * v[0] / v[2], 100 * v[1] / v[3]
                mu[i] = (np.log1p(dr) - np.log1p(du), 1.0)
                pi[i] = (np.log1p(dr + du), 1.0)
            self.F[t] = {"mu": mu, "pi": pi}
        lab = "cratères S2 v2" + (" (strict + élargi)" if niveau > 1 else "")
        self.mu_names = [f"{lab} : log densité RU − UA", f"{lab} : observé"]
        self.pi_names = [f"{lab} : log densité totale", f"{lab} : observé"]

    def _prep_crat(self, C):
        self._crat(C, 1)

    def _prep_crat_large(self, C):
        self._crat(C, 2)

    def _prep_decade(self, C):
        """v4 (résolution temporelle) : vitesse du consensus des cartes à la DÉCADE dans le tronçon (`donnees.
        consensus_decades`) : dernière décade du mois v_d = Y_fin − Y_20 et accélération v_d − (Y_20 − Y_10), en km de
        profondeur (> 0 = gain russe) dans μ, leurs |·| dans π. Connu à la fin du mois t (dates ≤ fin du mois)."""
        D = DN.consensus_decades()
        self.cover = np.array([t in C.rows and any((t, int(S.tiles[k])) in D for k in C.rows[t]) for t in range(S.NM)])
        for t in C.months:
            mu = []
            for k in C.rows[t]:
                v = D.get((t, int(S.tiles[k])))
                if v is None:
                    mu.append([0.0, 0.0]); continue
                vd, vp = v[2] - v[1], v[1] - v[0]
                mu.append([vd, vd - vp])
            mu = np.clip(np.array(mu, float).reshape(-1, 2), -12, 12)
            self.F[t] = {"mu": mu, "pi": np.abs(mu)}
        self.mu_names = ["consensus : vitesse dernière décade", "consensus : accélération (décade)"]
        self.pi_names = ["|vitesse dernière décade|", "|accélération décade|"]

    def _prep_perime(self, C):
        """v4 (régime du TÉMOIN) : avance PÉRIMÉE d'une carte. Pour la carte m (DeepState, ISW, Wikipédia) et le tronçon k,
        part pondérée (demi-vie 6 mois, mois q ∈ [t − 12, t − 2], vérité à q + 2 connue à t) des mois où la carte était en
        avance (|δ_m| ≥ 1 km) sans que le consensus ne la rejoigne (δ vrai de même signe ≥ la moitié de δ_m : non),
        rétrécie × n/(n + 3) : S_m,k(t). μ ← δ_m(t)·S_m,k(t) (une avance qui ne s'est pas confirmée depuis des mois pèse
        moins : coefficient appris, attendu négatif) ; π ← |δ_m(t)|·S_m,k(t). Rien n'est réglé sur un secteur."""
        TF, TC = _TEMOINS["F"], _TEMOINS["colm"]
        self.cover = np.array([t in C.rows for t in range(S.NM)])
        for t in C.months:
            n = len(C.rows[t])
            dl = TF[t]["mu"][:, :3]
            st = np.zeros((n, 3))
            past = [q for q in C.months if t - 12 <= q <= t - 2]
            for i, k in enumerate(C.rows[t]):
                for mi in range(3):
                    num = den = 0.0
                    for q in past:
                        j = C.rpos[q][k]
                        if j < 0:
                            continue
                        dm = TF[q]["mu"][j, mi]
                        dv = C.d2.get((q, k))
                        if dv is None or abs(dm) < 1.0 or not TF[q]["mu"][j, 3 + mi]:
                            continue
                        w = 0.5 ** ((t - q) / 6)
                        den += w
                        num += w * float(not (np.sign(dv) == np.sign(dm) and abs(dv) >= 0.5 * abs(dm)))
                    st[i, mi] = (num / den) * den / (den + 3.0) if den else 0.0
            mu = dl * st
            self.F[t] = {"mu": mu, "pi": np.abs(mu)}
            on = np.asarray(TC[t][0][:3], bool)
            self.colm[t] = (on, on)
        self.mu_names = ["δ DeepState × périmé", "δ ISW × périmé", "δ Wikipédia × périmé"]
        self.pi_names = ["|δ DeepState| × périmé", "|δ ISW| × périmé", "|δ Wikipédia| × périmé"]

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
            self.colm[t] = (np.zeros(0, bool), np.array([bool(arr[s][1][t]) for s in srcs]))
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
        nb = tile_neighbours()
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
        AW = self.AW = (0.6, 0.25, 0.15)
        self.off = {}
        self.offm = {}       # {t: (3, nZ) avance locale lissée de chaque carte (NaN : carte absente)} (v2 : poids des cartes figées)
        self.stm = {}        # {t: (3, nZ) état de chaque carte par maille (0 absente/inconnue, 1 ukrainienne, 2 russe, 3 contestée)}
        for t in C.months:
            sd = np.nan_to_num(S.SDe[t], nan=999)
            n = len(C.rows[t])
            dl = np.zeros((n, 3)); av = np.zeros((n, 3))
            onum = np.zeros(S.nZ, np.float32); oden = 0.0
            offm = np.full((3, S.nZ), np.nan, np.float32)
            stm = np.zeros((3, S.nZ), np.uint8)
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
                    om = S.smooth(S.full(o), 1.5)[S.Zi]
                    offm[mi] = om; stm[mi] = np.where(C.isUAz, st, 0)
                    onum += AW[mi] * om; oden += AW[mi]
                    for r, k in enumerate(C.rows[t]):
                        z = S.tile_cells[k]
                        mk = (np.abs(sd[z]) <= 30) & ((st[z] == 1) | (st[z] == 2))
                        if mk.sum() < NMIN_SHIFT:
                            continue
                        x = sd[z][mk][:, None] + DGRID[None, :]
                        ru = (st[z][mk] == 2)[:, None]
                        miss = (ru & (x <= 0)).sum(0) + (~ru & (x > 0)).sum(0) + 1e-3 * np.abs(DGRID)
                        dl[r, mi] = DGRID[int(np.argmin(miss))]; av[r, mi] = 1.0
            with np.errstate(invalid="ignore"):
                spread = np.where(av.sum(1) >= 2, np.nanmax(np.where(av > 0, dl, np.nan), 1) - np.nanmin(np.where(av > 0, dl, np.nan), 1), 0.0)
            spread = np.nan_to_num(spread)
            on = [bool(av[:, mi].any()) for mi in range(3)]       # carte présente ce mois-là (ISW : tant qu'elle paraît)
            self.colm[t] = (np.array(on + on), np.array(on + [sum(on) >= 2]))
            self.off[t] = (onum / oden if oden else onum).astype(np.float32)
            self.offm[t], self.stm[t] = offm, stm
            self.F[t] = {"mu": np.c_[dl, av], "pi": np.c_[np.abs(dl), spread]}
        _TEMOINS["F"], _TEMOINS["colm"] = self.F, self.colm
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
