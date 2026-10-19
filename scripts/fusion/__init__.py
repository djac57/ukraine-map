"""Moteur de fusion (vue parallèle « fusion » de la carte), porté de `ukraine-recherche/analyses/fusion/` le 19/10/2026.

Lancé par `scripts/build_fusion.py` APRÈS `build_front.py` et `build_direction.py` ; lit seulement `site/` et `data/` ;
écrit `site/front_fusion.js` et `site/fusion_explain.js`. Dépendances : numpy, scipy.
"""
