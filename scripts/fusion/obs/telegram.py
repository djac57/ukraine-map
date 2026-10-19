"""Modèle d'observation Telegram : observations PONCTUELLES, rares et fortes.

Source : `ukraine-feux/data/telegram_cells.csv` (maille de 2 km × mois, toutes sources ; comptes seulement, aucun
texte), quatre types : attaques ukrainiennes décrites (att_ua), attaques russes (att_ru), présence ukrainienne
signalée (pres_ua), présence russe (pres_ru). Chaque maille citée est UN témoin (indicatrice, pas de compte).

Pour chaque type c, la probabilité qu'une mention tombe en x, sachant la ligne décalée de δ :

    P(mention en x | δ) ∝ ε_c + (1 − ε_c) · exp(−|d(x) + δ − m_c| / L_c^±)

m_c (où le type tombe par rapport à la vraie ligne : présence russe côté russe, attaques près de la ligne), L_c^± et le
plancher ε_c (mentions sans rapport avec la ligne : faux positifs du géocodage ou de la classification) sont appris
en walk-forward comme pour les densités. La part de faux positifs estimée est la masse du plancher dans un tronçon
type. log L_tg(δ) = Σ_c log L_c(δ) ; un seul poids pour Telegram dans la fusion.
Absence de mention : non informative (pas de pénalité), conformément à « en pointillés ».
"""
import numpy as np

from ..commun import DGRID

from .densite import ProfilDistance

TYPES = [("att_ua", "attaques ukrainiennes décrites"), ("att_ru", "attaques russes décrites"),
         ("pres_ua", "présence ukrainienne signalée"), ("pres_ru", "présence russe signalée")]


def _ind(x):
    return (x > 0).astype(np.float64)


class PointsTelegram:
    KIND = "position"

    def __init__(self, name, label, types=None):
        self.name, self.label = name, label
        self.sub = [ProfilDistance(f"tg_{c}", lab, [f"tg_{c}"], transform=_ind, cap=None, win_months=36, half_life=24.0)
                    for c, lab in (types or TYPES)]
        self.params = {}

    def prepare(self, C):
        for m in self.sub:
            m.prepare(C)
        self.cover = np.any([m.cover for m in self.sub], 0)

    def fit(self, t, C):
        out = {m.name: m.fit(t, C) for m in self.sub}
        self.params[t] = out
        return out

    def loglik(self, t, C):
        LL = np.zeros((len(C.rows[t]), len(DGRID)))
        for m in self.sub:
            LL += m.loglik(t, C)
        return LL

    def fp_share(self, t):
        """Part de faux positifs estimée par type : masse du plancher ε sur une bande de ±30 km."""
        out = {}
        u = np.arange(-30, 30.01, 0.5)
        for m in self.sub:
            p = m.params.get(t)
            if not p:
                continue
            from .densite import al
            f = al(u, p["m"], p["Lm"], p["Lp"], p["eps"])
            out[m.name] = float(p["eps"] * len(u) / f.sum())
        return out

    def formula(self, t=None):
        lines = [m.formula(t) for m in self.sub]
        fp = self.fp_share(t if t is not None else max(self.params) if self.params else 0)
        return "\n".join(lines) + ("\n  faux positifs estimés (±30 km) : " + ", ".join(f"{k} {v:.0%}" for k, v in fp.items()) if fp else "")
