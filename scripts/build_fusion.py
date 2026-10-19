"""Moteur de fusion en PRODUCTION, vue PARALLÈLE « fusion » (rien n'est modifié dans les vues publiées).

À lancer APRÈS `build_front.py` et `build_direction.py` :

    python3 scripts/build_fusion.py            (≈ 5 min ; numpy + scipy ; lit site/ et data/ seulement)
    python3 scripts/build_fusion.py --kpi      (en plus : KPI « avance sur les cartes » empilé, net du placebo ; ≈ +10 min)

Entrées (toutes publiées ou versionnées) : site/front.js (grille, mois, états par localité et par carte dans
months[].d, âge des cartes), site/front_equilibre.js (champ des cartes), site/front_nowcast.js (champ publié, référence
de la règle « ne rien dégrader »), site/data.js (feux), site/osint.js (UCDP, WarSpotting), site/barrieres_dures.js,
data/officiels_cells_month.csv, data/alertes_artillerie.csv, data/alertes_kab.csv, data/telegram_cells.csv,
data/impacts_troncons_decades.csv, data/liste_hostilites_hromada_month.csv, data/hromada_cells.json, data/sar_cells.csv.

Méthode (`scripts/fusion/`, copie de `ukraine-recherche/analyses/fusion/`, version retenue du 19/10/2026) : δ_k(t),
décalage de la vraie ligne par rapport à la ligne des cartes par tronçon de 24 km, loi a posteriori discrète filtrée
dans le temps ; un modèle d'observation par signal ; TOUT est réappris chaque mois en walk-forward (profils, a priori
des cartes, poids des signaux, paramètres du champ, bande) sur les seuls mois q ≤ t − 2 dont la vérité (consensus à
q + 2) est connue à t. Bande recalibrée PAR SECTEUR (conformal local, `fusion/champ.py`).
v2 (19-20/10/2026, docs/ETAT.md § 6) : une source absente ne contribue plus (ses variables sont retirées du modèle des
cartes du mois : plus d'« ISW présente » après son arrêt) ; poids d'une carte durablement contredite réduit dans
l'avance locale des cartes du champ, SEULEMENT dans les secteurs figés (`fusion/obs/figes.py`, κ choisi chaque mois).
Testés et non retenus : tronçons de 12 et 16 km, couplage entre voisins, double pavage décalé, biais δ des secteurs
figés (FUSION_TILE / FUSION_OFFSET, FIGES_DELTA restent disponibles pour les essais).

Sorties :
  site/front_fusion.js
      window.FRONT_FUSION = {generated, version, method, calibration, validation, weights,
                             months: [null | {date, a: {tout, horsCrimee, depuis2022: [espérance, médiane, zone grise,
                                      p > 0,95] km², russie: [espérance, médiane] km²}, mc: {tout, horsCrimee,
                                      depuis2022: [bas, haut] km² (95 %)}}, …]}      (indices = FRONT.months)
      (window.FRONTP=window.FRONTP||{})["fusion"] = [...]   même encodage que front_<vue>.js (6 bits, différences,
                                      une entrée par entrée de FRONT.months ; pas récents : dernier mois reporté)
  site/fusion_explain.js
      window.FUSION_EXPLAIN = {generated, tileCells: 12, sign, columns, codes: {code: libellé}, months:
                               [null | [[colonne, ligne, δ médian ×10, δ 5 % ×10, δ 95 % ×10, π %, [[code, Δ ×10], …]], …]]}
      colonne, ligne = première maille du tronçon (i0, j0, multiples de 12, comme FRONT.months[].pm) ; δ en km × 10,
      > 0 = la ligne probable est plus avancée côté ukrainien que celle des cartes (gain russe) ; π = probabilité que le
      tronçon bouge (≥ 1 km) ; Δ = contribution du signal au décalage médian (km × 10, même signe), 6 au plus,
      |Δ| ≥ 0,05 km.
"""
import argparse
import datetime as dt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# bande : conformal local par secteur (choisi le 19/10/2026 sur 2023-01 → 2026-07, voir docs/ETAT.md § 6)
CALIB = dict(calib="conformal", level=0.95, gamma_a=0.1, lam_max=2.0, hl=9.0, n0=10.0, win=36)
SPREAD_MODE = "both"
# modèles revus le 19/10/2026 (rapport de recherche, « à revoir ») : assauts de l'état-major en PRESSION (et plus en
# position), Sentinel-2 en SENS seulement ; le reste = version retenue en recherche
POS_PROD = ["feux", "ucdp", "pertes", "em_frappes", "modru", "artillerie", "kab", "radar_bati", "tg"]
DYN_PROD = ["temoins", "memoire", "mouvement", "asymetrie", "s2_sens", "tg_oriente", "activite", "liste_dyn", "assauts_dyn"]
# v2 (19-20/10/2026, docs/ETAT.md § 6) : réglages choisis sur 2023-2024, évalués gelés sur 2025-2026
#  - source absente : ses variables sont retirées du modèle des cartes appris pour le mois (ISW après juin 2026…) ;
#  - POIDS_FIGES : poids d'une carte durablement contredite réduit dans le champ, SEULEMENT dans les secteurs figés
#    (`fusion/obs/figes.PoidsCartesFiges`, κ choisi chaque mois) ; None : désactivé ;
#  - FIGES_DELTA : biais durable appris dans le décalage δ des secteurs figés (`figes.SecteursFiges`) ; None : désactivé.
POIDS_FIGES = dict(W=6, nthr=2, n0=200.0)
FIGES_DELTA = None


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kpi", action="store_true", help="KPI « avance sur les cartes » empilé, net du placebo (lent)")
    ap.add_argument("--no-write", action="store_true", help="mesurer sans écrire les fichiers du site")
    args = ap.parse_args()
    T0 = time.time()
    import numpy as np
    from fusion.commun import S, SITE, TILE
    from fusion.moteur import Moteur
    from fusion import champ as CHM, mesures as ME, sortie as SO
    log(f"socle : {S.NM} mois ({S.months[0]} → {S.months[-1]}), {len(S.built)} construits, zone {S.nZ} mailles, "
        f"{S.nT} tuiles ({time.time() - T0:.0f} s)")
    M = Moteur(set(POS_PROD) | set(DYN_PROD))
    fgd = M.figes(**FIGES_DELTA) if FIGES_DELTA else None
    R = M.run(POS_PROD, DYN_PROD, figes=fgd)
    log(f"moteur : loi a posteriori de δ, {len(M.C.months)} mois ({time.time() - T0:.0f} s)")
    pk = M.poids_figes(**POIDS_FIGES) if POIDS_FIGES else None
    CH = CHM.Champ(M, R, figes=pk)
    grid, ll = CHM.table_choix(CH)
    log(f"table de choix du champ ({len(grid)} points) ({time.time() - T0:.0f} s)")
    CHM.SPREAD["mode"] = SPREAD_MODE
    F, choice, c, lams = CHM.champs(CH, grid, ll, **CALIB)
    log(f"champs calibrés ({time.time() - T0:.0f} s)")

    # ---------- validation (walk-forward) ----------
    prod = {t: S.Pnow[t] for t in S.built if np.isfinite(S.Pnow[t]).any()}
    mp, mf = ME.metriques(prod), ME.metriques(F)
    se = ME.boot_diff(mf["_mon"], mp["_mon"])
    l12 = ME.eval_months()[-12:]
    mp12, mf12 = ME.metriques(prod, l12), ME.metriques(F, l12)

    def pub(m):
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in m.items() if not k.startswith("_")}
    val = {"periode": f"{S.months[ME.eval_months()[0]]} → {S.months[ME.eval_months()[-1]]}", "verite": "consensus des cartes à t+2",
           "production": pub(mp), "fusion": pub(mf), "et_ecart": {"brier_bande": round(se[0], 5), "brier_change2": round(se[1], 4)},
           "12_derniers_mois": {"production": pub(mp12), "fusion": pub(mf12)}}
    for lab, m in (("production (nowcast)", mp), ("fusion", mf), ("production 12 mois", mp12), ("fusion 12 mois", mf12)):
        log(f"{lab:22s} Brier ≤ 30 km {m['brier_bande']:.5f} | mailles qui changent t+2 {m['brier_change2']:.4f} "
            f"t+1 {m['brier_change1']:.4f} | couverture par secteur : médiane {100 * m['cov_med']:.1f} %, P10 "
            f"{100 * m['cov_p10']:.1f} %, {m['cov_sous90']} secteurs < 90 % sur {m['n_secteurs']}")
    log(f"écart fusion − production : Brier ≤ 30 km {mf['brier_bande'] - mp['brier_bande']:+.5f} (± {se[0]:.5f}), "
        f"mailles qui changent {mf['brier_change2'] - mp['brier_change2']:+.4f} (± {se[1]:.4f})")
    if args.kpi:
        K = ME.KPI()
        val["kpi_avance"] = {}
        for h in (1, 2):
            for lab, src in (("production", prod), ("fusion", F)):
                r = K.net(src, h)
                val["kpi_avance"][f"h{h} {lab}"] = {k: (np.round(v, 4).tolist() if isinstance(v, list) else round(float(v), 4)) for k, v in r.items()}
                log(f"KPI avance h = {h} mois | {lab:10s} | NET ≤ 30 km {100 * r['net_d30']:+.2f} pt [{100 * r['ic_net_d30'][0]:+.1f} ; "
                    f"{100 * r['ic_net_d30'][1]:+.1f}], mailles qui changent {100 * r['net_chg']:+.2f} pt [{100 * r['ic_net_chg'][0]:+.1f} ; "
                    f"{100 * r['ic_net_chg'][1]:+.1f}] | 12 mois {100 * r['net_d30_12m']:+.2f} / {100 * r['net_chg_12m']:+.2f} pt "
                    f"(brut {100 * r['brut_d30']:+.1f} / {100 * r['brut_chg']:+.1f} %, placebo {100 * r['placebo_d30']:+.1f} / {100 * r['placebo_chg']:+.1f} %)")
        log(f"KPI calculé ({time.time() - T0:.0f} s)")

    # poids appris (moyenne et part des mois non nuls, depuis 2023)
    ts = [t for t in R["w"] if S.months[t] >= "2023-01"]
    keys = sorted({k for t in ts for k in R["w"][t]})
    weights = {k: {"moyenne": round(float(np.mean([R["w"][t].get(k, 0) for t in ts])), 3),
                   "mois_non_nuls": round(float(np.mean([R["w"][t].get(k, 0) > 0 for t in ts])), 2),
                   "dernier": R["w"][max(R["w"])].get(k, 0)} for k in keys}
    log("poids appris (moyenne / part des mois ≠ 0 / dernier mois) : " + ", ".join(
        f"{k} {v['moyenne']:.2f}/{v['mois_non_nuls']:.0%}/{v['dernier']}" for k, v in weights.items()))

    if args.no_write:
        log(f"terminé sans écriture ({time.time() - T0:.0f} s)")
        return
    # ---------- sorties ----------
    Fu = SO.full_fields(F)
    months_meta = []
    for i in range(S.nOrder):
        if i < S.NM and i in Fu:
            a = SO.areas(Fu[i])
            months_meta.append({"date": S.mdate[i], "a": a, "mc": SO.mc_interval(M, R, i, a)})
        else:
            months_meta.append(None)
    gen = dt.date.today().isoformat()
    figes = None
    if pk is not None:
        tl = max(pk.frz)
        figes = {"reglage": POIDS_FIGES, "kappa_grille": list(pk.KGRID),
                 "dernier_mois": {"mois": S.months[tl], "troncons_figes": int(pk.frz[tl].sum()), "troncons": len(pk.frz[tl]),
                                  "cartes_reduites": {nm: int((pk.frz[tl] & (pk.x[tl][:, mi] > 0.02)).sum())
                                                      for mi, nm in enumerate(("deepstate", "isw", "wikipedia"))
                                                      if np.isfinite(M.models["temoins"].offm[tl][mi]).any()},
                                  "kappa": choice.get(tl, (0, 0, 0, 0, 0.0))[4]},
                 "kappa_par_mois": {S.months[t]: v[4] for t, v in choice.items()}}
        log(f"secteurs figés : {figes['dernier_mois']}")
    meta = {"generated": gen, "version": "moteur de fusion v2 (20/10/2026)",
            "method": "décalage δ de la vraie ligne par tronçon de 24 km, loi a posteriori filtrée, un modèle d'observation "
                      "par signal, poids et paramètres réappris chaque mois en walk-forward (ukraine-recherche/analyses/fusion) ; "
                      "v2 : une source absente ne contribue pas (variables retirées du modèle du mois) ; poids d'une carte "
                      "durablement contredite réduit dans les secteurs figés seulement (κ choisi chaque mois)",
            "figes": figes,
            "calibration": dict(CALIB, spread=SPREAD_MODE, lam_median_dernier_mois=round(float(np.median(lams[max(lams)])), 2)),
            "fieldParams": {S.months[t]: list(v) for t, v in choice.items()},
            "validation": val, "weights": weights}
    SO.write_front(os.path.join(SITE, "front_fusion.js"), Fu, months_meta, meta)
    ex = SO.explain(M, R)
    SO.write_explain(os.path.join(SITE, "fusion_explain.js"), ex, {
        "generated": gen, "tileCells": TILE,
        "sign": "δ et Δ > 0 : ligne probable plus avancée côté ukrainien que celle des cartes (gain russe) ; < 0 : côté russe",
        "columns": ["colonne (i0)", "ligne (j0)", "δ médian (km × 10)", "δ 5 % (km × 10)", "δ 95 % (km × 10)",
                    "P(le tronçon bouge ≥ 1 km) %", "contributions [[code, Δ km × 10], …]"],
        "codes": SO.CODES,
        "note": "probabilité d'une maille : front_fusion.js (moteur) et front_equilibre.js (cartes), au même indice de FRONT.months"})
    sz = [os.path.getsize(os.path.join(SITE, f)) // 1024 for f in ("front_fusion.js", "fusion_explain.js")]
    log(f"écrit site/front_fusion.js ({sz[0]} Ko) et site/fusion_explain.js ({sz[1]} Ko) ; durée totale {time.time() - T0:.0f} s")


if __name__ == "__main__":
    main()
