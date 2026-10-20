"""Modèles d'observation du moteur de fusion : UN module par famille de signal, chacun avec SON modèle conditionné à
la position latente de la ligne (décalage δ du tronçon par rapport à la ligne des cartes).

  - "position"  : vraisemblance log L_s(δ) (densités d'événements, points Telegram, terrain dur) ;
  - "dynamique" : variables d'état (pression, sens, vitesse) qui déplacent la composante « mouvement » de l'a priori
                  des cartes ; les cartes (témoin biaisé et en retard) sont dans `cartes.py`.
"""
import numpy as np

from .densite import ProfilDistance  # noqa: F401
from .telegram import PointsTelegram  # noqa: F401
from .dynamique import Dynamique  # noqa: F401
from .terrain import Barrieres  # noqa: F401
from .cartes import TemoinCartes  # noqa: F401
from .figes import RGRID as FIGES_RGRID, PoidsCartesFiges, SecteursFiges  # noqa: F401
from .videos import PresenceFilmee  # noqa: F401  (emplacement, DÉSACTIVÉ : autorisation GeoConfirmed non obtenue)


def catalogue():
    """Tous les modèles d'observation, dans l'ordre du rapport de recherche (+ variantes revues en production)."""
    lg = np.log1p
    return [
        ProfilDistance("feux", "Feux FIRMS en excès", ["feux"], transform=lg),
        ProfilDistance("ucdp", "UCDP (tous événements)", ["ucdp"], transform=lg),
        ProfilDistance("pertes", "Pertes WarSpotting", ["pertes"], transform=lg),
        ProfilDistance("em_assauts", "État-major UA : assauts (faits)", ["em_assauts"], transform=lg),
        ProfilDistance("em_frappes", "État-major UA : frappes aériennes (faits)", ["em_frappes"], transform=lg),
        ProfilDistance("modru", "MoD RF : localités des bilans (faits)", ["modru"], transform=lg),
        ProfilDistance("artillerie", "Menaces d'artillerie (canal officiel)", ["artillerie"], transform=lg, window="ua"),
        ProfilDistance("kab", "Alertes KAB (canal officiel)", ["kab"], transform=lg, window="ua"),
        ProfilDistance("radar_bati", "Radar S1 : bâti modifié", ["radar_bati"], transform=None, window="radar"),
        PointsTelegram("tg", "Telegram (attaques et présence signalées)"),
        Dynamique("mouvement", "Mouvement récent des cartes (vitesse)"),
        Dynamique("asymetrie", "Asymétrie des combats (pression)"),
        Dynamique("impacts_s2", "Impacts Sentinel-2 par côté (camp pilonné)"),
        Dynamique("s2_sens", "Impacts Sentinel-2 : sens seulement (variante revue)"),
        Dynamique("tg_oriente", "Telegram orienté (qui attaque)"),
        Dynamique("temoins", "Cartes séparées (DeepState, ISW, Wikipédia) : décalage et désaccord"),
        Dynamique("memoire", "Biais et retard des cartes appris par secteur"),
        Dynamique("liste_dyn", "Liste officielle comme pression"),
        Dynamique("assauts_dyn", "Assauts de l'état-major comme pression (variante revue)"),
        Dynamique("activite", "Intensité des combats près de la ligne (probabilité de mouvement)"),
        # v3 (20/10/2026)
        Dynamique("s2v2", "Cratères Sentinel-2 v2 par côté (pression, tout le front)"),
        Dynamique("tg_msg", "Telegram orienté, attribution au message (tronçons)"),
        Dynamique("kab_dyn", "Alertes KAB comme pression"),
        Dynamique("ookla_dyn", "Ookla : baisse des tests de débit comme pression"),
        Dynamique("nowcast", "Nowcast publié : témoin du décalage par rapport aux cartes"),
        Barrieres("terrain", "Terrain dur (fleuves, escarpements, canaux)"),
    ]
