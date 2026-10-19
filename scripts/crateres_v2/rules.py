"""Décision du détecteur v2 (règle à seuils, réglée sur la zone de développement pk_test, août 2026, puis gelée avant
l'évaluation sur les autres zones et dates).

keep(o) : cratère retenu ; group(...) : comptage en groupes (densité locale).
"""
import numpy as np

P_LARGE = dict(
    Cmin=20.0,        # contraste de changement (σ de la scène)
    persist=0.6,      # C à la date suivante ≥ 0,6 × C (la tache reste)
    area_max=30,      # tache à mi-hauteur ≤ 30 px (≈ 60 m de diamètre ; un cratère + éjecta en couvre 3 à 20)
    elong_max=2.5,    # élongation (rejette chemins, lisières, tranchées)
    hr_min=0.2,       # rapport des courbures du hessien (rond ≈ 1, ligne ≈ 0)
    V0_max=0.10,      # référence pas trop claire (carrières, sol crayeux, bâti)
    V1_max=0.13,
    tex0_max=0.35,    # texture de la référence sur 210 m (villages, jardins)
    inv_max=0.0,      # aucun pixel non valide à ≤ 60 m à d0 et d1
    inv2_max=0.3,
    cN_min=8.0,       # le NDVI baisse au centre (σ)
    cV_min=12.0,      # le visible s'éclaircit au centre (σ) : sol nu, éjecta ; une brûlure assombrit
    snr_min=6.0,      # pic isolé : C / (dispersion robuste de C sur 210 m + 0,5) ; une parcelle qui change est bruitée
    c1V_min=-9.0,     # (variante stricte) la tache est plus claire que son voisinage à d1
    N0_min=-9.0,      # (variante stricte) fond végétal à la référence
)
# Règle retenue (« stricte »), gelée le 19/10/2026 après réglage sur pk_test (août 2026, 93 étiquettes), AVANT
# l'évaluation sur les autres zones et dates : plus contrastée, plus claire que son voisinage à d1, sur fond végétal,
# ronde.
P = dict(P_LARGE, Cmin=30.0, cV_min=10.0, c1V_min=0.12, N0_min=0.5, elong_max=2.0)


def keep(o, p=P):
    if o.get("d2") is None or o["C2"] is None or (isinstance(o["C2"], float) and np.isnan(o["C2"])):
        return False
    return (o["C"] >= p["Cmin"] and o["C2"] >= p["persist"] * o["C"] and o["area"] <= p["area_max"]
            and o["elong"] <= p["elong_max"] and o["hr"] >= p["hr_min"] and o["V0"] <= p["V0_max"]
            and o["V1"] <= p["V1_max"] and o.get("tex0", 0) <= p["tex0_max"] and o["inv0"] <= p["inv_max"]
            and o["inv1"] <= p["inv_max"] and o["inv2"] <= p["inv2_max"] and o["cN"] >= p["cN_min"]
            and o["cV"] >= p["cV_min"] and o.get("snr", 99) >= p["snr_min"] and o.get("rmix", 0) == 0
            and o["c1V"] >= p["c1V_min"] and o["N0"] >= p["N0_min"])
