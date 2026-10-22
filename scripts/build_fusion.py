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
v3 (20/10/2026, docs/ETAT.md § 6) : KAB et Ookla en pression, nowcast publié emboîté comme témoin du DÉCALAGE ;
variations de km² lues dans months[i].akm (champ sans extrapolation β·v_loc).
Domaine validé (20/10/2026, docs/ETAT.md § 6) : dans la BANDE RUSSE (mailles place −2 : hors Ukraine, où la fusion n'a
aucune vérité), front_fusion.js publie la valeur du NOWCAST publié (site/front_nowcast.js), pour le champ comme pour
akm ; les surfaces en Ukraine sont inchangées, seule la surface « russie » suit le nowcast.

Sorties :
  site/front_fusion.js
      window.FRONT_FUSION = {generated, version, method, calibration, validation, weights,
                             months: [null | {date, a: {tout, horsCrimee, depuis2022: [espérance, médiane, zone grise,
                                      p > 0,95] km², russie: [espérance, médiane] km²}, akm: même format que a, champ
                                      SANS β·v_loc (v3 : pour les VARIATIONS de km², lire akm.tout[1]), mc: {tout, horsCrimee,
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
      Colonnes 7 et 8 (20/10/2026, sens de la fusion ; publiées pour la page méthode et les analyses, NON affichées sur
      la carte : le sens n'y est porté que par les flèches) : [..., contributions, P(sens RU | bouge) %, P(bouge) %],
      tirées de la loi a posteriori de δ du mois : P_RU = P(δ ≥ +1 km), P_UA = P(δ ≤ −1 km),
      P(sens RU | bouge) = P_RU / (P_RU + P_UA) (null si P_RU + P_UA ≈ 0), P(bouge) = P_RU + P_UA (à ne pas confondre
      avec π, colonne 5, poids du mode « mouvement » du mélange).
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
DYN_PROD = ["temoins", "memoire", "mouvement", "asymetrie", "s2_sens", "tg_oriente", "activite", "liste_dyn", "assauts_dyn",
            # v3 (20/10/2026, docs/ETAT.md § 6) : KAB et Ookla en PRESSION, nowcast publié comme témoin du DÉCALAGE
            "kab_dyn", "ookla_dyn", "nowcast"]
# v3 : testés, NON retenus (règle « ne rien dégrader », réglage 2023-24) : cratères S2 v2 par côté ("s2v2"), Telegram
# orienté par message ("tg_msg" à la place de "tg_oriente"), profil du décalage le long de la ligne (champ.PROFIL),
# contrainte de km² dans le choix du champ (champ.CHOIX). Présence filmée (GeoConfirmed) : `fusion/obs/videos.py`,
# DÉSACTIVÉ (autorisation non obtenue).
# km² : la surface publiée pour les variations (months[i].akm) est celle du champ SANS le terme d'extrapolation de la
# vitesse locale (β = 0, autres paramètres et bande identiques) : β·v_loc fait gagner les mailles qui changent mais
# gonfle la variation de surface sur 12 mois (biais +425 km² en 2025-26, docs/ETAT.md § 6).
KM_SANS_BETA = True
# v2 (19-20/10/2026, docs/ETAT.md § 6) : réglages choisis sur 2023-2024, évalués gelés sur 2025-2026
#  - source absente : ses variables sont retirées du modèle des cartes appris pour le mois (ISW après juin 2026…) ;
#  - POIDS_FIGES : poids d'une carte durablement contredite réduit dans le champ, SEULEMENT dans les secteurs figés
#    (`fusion/obs/figes.PoidsCartesFiges`, κ choisi chaque mois) ; None : désactivé ;
#  - FIGES_DELTA : biais durable appris dans le décalage δ des secteurs figés (`figes.SecteursFiges`) ; None : désactivé.
POIDS_FIGES = dict(W=6, nthr=2, n0=200.0)
FIGES_DELTA = None
# v4 (20/10/2026, docs/ETAT.md § 6, rapport de fusion § v4) : RÉGIME du tronçon (`fusion/obs/regime.py`, mélange doux
# calme / offensive en cours, g connu à t). Candidat évalué : dict(mode="mvt", O0=2.0, cartes=("mu",), poids=True)
# (meilleur sur les mailles qui changent, Pokrovsk, Avdiïvka, délai ; moins bon sur l'erreur des km² à 30 jours et la
# couverture P10 en 2025-26) : NON basculé, v3 reste en production. None : v3.
REGIME = None


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
    rkw = {}
    if REGIME:
        from fusion import obs as OBS
        rkw = dict(regime=OBS.Regime(mode=REGIME["mode"], O0=REGIME["O0"]).prepare(M.C),
                   regime_cartes=tuple(REGIME.get("cartes", ())), regime_poids=bool(REGIME.get("poids")))
    R = M.run(POS_PROD, DYN_PROD, figes=fgd, **rkw)
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
    Fk = {}
    if KM_SANS_BETA:
        for t in F:
            b_, g_, s0_, sp_, kp_ = choice[t]
            Fk[t] = CHM.quant(CH.field(t, CH.tables(t, CH.s_of(t, s0_, sp_), lams[t]), 0.0, g_, kappa=kp_))
        Fk = SO.full_fields(Fk)
    # domaine validé (décision du 20/10/2026, docs/ETAT.md § 6) : la fusion n'est validée qu'en Ukraine (vérité = consensus
    # des cartes, rien en Russie) ; dans la BANDE RUSSE (place −2, cellPlace −2 côté site) on publie le NOWCAST publié
    # (site/front_nowcast.js, même encodage, même indice de FRONT.months), pour le champ affiché comme pour akm. Les
    # surfaces en Ukraine (tout, horsCrimee, depuis2022) sont inchangées ; seule « russie » suit le nowcast.
    from fusion.socle import decode_seq, load_seq
    NOW = decode_seq(load_seq(os.path.join(SITE, "front_nowcast.js")), S.nU)
    band = S.cellU == -2
    nb = 0
    for FF in (Fu, Fk):
        for t, p in FF.items():
            if t < len(NOW) and NOW[t] is not None:
                p[band] = NOW[t][band].astype(np.float32) / 63.0
                nb += 1
    log(f"bande russe : nowcast publié à la place de la fusion ({int(band.sum())} mailles, {nb} champs)")
    months_meta = []
    for i in range(S.nOrder):
        if i < S.NM and i in Fu:
            a = SO.areas(Fu[i])
            mm = {"date": S.mdate[i], "a": a, "mc": SO.mc_interval(M, R, i, a)}
            if i in Fk:
                mm["akm"] = SO.areas(Fk[i])
            months_meta.append(mm)
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
    meta = {"generated": gen, "version": "moteur de fusion v3 (20/10/2026)",
            "method": "décalage δ de la vraie ligne par tronçon de 24 km, loi a posteriori filtrée, un modèle d'observation "
                      "par signal, poids et paramètres réappris chaque mois en walk-forward (ukraine-recherche/analyses/fusion) ; "
                      "v2 : une source absente ne contribue pas (variables retirées du modèle du mois) ; poids d'une carte "
                      "durablement contredite réduit dans les secteurs figés seulement (κ choisi chaque mois) ; v3 : KAB et "
                      "Ookla en pression, nowcast publié comme témoin du décalage ; variations de km² lues dans akm (champ "
                      "sans extrapolation de la vitesse locale)",
            "km": {"akm": "surfaces (même format que a) du champ sans le terme β·v_loc, pour les VARIATIONS de km² (30 j, "
                          "12 mois) ; a = surfaces du champ affiché"},
            "figes": figes,
            "calibration": dict(CALIB, spread=SPREAD_MODE, lam_median_dernier_mois=round(float(np.median(lams[max(lams)])), 2)),
            "fieldParams": {S.months[t]: list(v) for t, v in choice.items()},
            "validation": val, "weights": weights}
    SO.write_front(os.path.join(SITE, "front_fusion.js"), Fu, months_meta, meta)
    ex = SO.explain(M, R)
    # sens tiré de la loi a posteriori de δ (20/10/2026, rapport de fusion § v4 « Sens par tronçon ») : colonnes 7 et 8
    # ajoutées à chaque ligne, dans l'ordre de SO.explain (= M.C.rows[t], même ordre que R["post"][t])
    from fusion.commun import DGRID
    for t, rows in ex.items():
        P = R["post"][t]
        pr, pu = P[:, DGRID >= 1].sum(1), P[:, DGRID <= -1].sum(1)
        for i, row in enumerate(rows):
            mv = float(pr[i] + pu[i])
            row.append(int(round(100 * pr[i] / mv)) if mv >= 1e-6 else None)
            row.append(int(round(100 * mv)))
    SO.write_explain(os.path.join(SITE, "fusion_explain.js"), ex, {
        "generated": gen, "tileCells": TILE,
        "sign": "δ et Δ > 0 : ligne probable plus avancée côté ukrainien que celle des cartes (gain russe) ; < 0 : côté russe",
        "columns": ["colonne (i0)", "ligne (j0)", "δ médian (km × 10)", "δ 5 % (km × 10)", "δ 95 % (km × 10)",
                    "P(le tronçon bouge ≥ 1 km) %", "contributions [[code, Δ km × 10], …]",
                    "P(sens RU | bouge) % (loi de δ ; null : P(bouge) nulle)", "P(|δ| ≥ 1 km) % (loi de δ)"],
        "sens": "colonnes 7 et 8, tirées de la loi a posteriori de δ : P_RU = P(δ ≥ +1 km), P_UA = P(δ ≤ −1 km) ; "
                "P(sens RU | bouge) = P_RU / (P_RU + P_UA), P(bouge) = P_RU + P_UA ; validation walk-forward (rapport de "
                "fusion § v4) : AUC du sens parmi les tronçons qui bougent 0,83 (2023-24) / 0,94 (2025-26), AUC de P_UA "
                "contre une reprise ukrainienne ≥ 1 km 0,94 / 0,97",
        "codes": SO.CODES,
        "note": "probabilité d'une maille : front_fusion.js (moteur) et front_equilibre.js (cartes), au même indice de FRONT.months"})
    sz = [os.path.getsize(os.path.join(SITE, f)) // 1024 for f in ("front_fusion.js", "fusion_explain.js")]
    log(f"écrit site/front_fusion.js ({sz[0]} Ko) et site/fusion_explain.js ({sz[1]} Ko) ; durée totale {time.time() - T0:.0f} s")

    # ---------- fusion QUOTIDIENNE (option, docs/ETAT.md § 6, 21/10/2026) ----------
    # Champ de fusion RÉANCRÉ au jour (loi de δ, bande et paramètres du mois de référence t appris en walk-forward
    # strict ; ligne des cartes DU JOUR), 2022-03-01 → J-1, un fichier PAR ANNÉE en SORTIE DÉDIÉE parallèle
    # (site/front_fusion_hist_<année>.js) : n'écrase ni les vues en ligne ni l'historique des cartes. NE BASCULE PAS la
    # vue par défaut. Mesuré ≥ fusion v3 mensuelle en Brier (≤ 30 km ET mailles qui changent, 2023-24 et 2025-26) ;
    # avance sur les bascules non strictement meilleure (cf. rapport) → sortie parallèle, pas de bascule du défaut.
    # Entrées : site/front_hist_<année>*.js publiés par build_front.py (HIST=jours).
    if os.environ.get("FUSION_HIST") == "jours":
        from fusion import decades as DEC
        if not DEC.disponible():
            log("fusion quotidienne : site/front_hist_<année>*.js absents (HIST=jours non publié) — ignoré")
        else:
            rho = float(os.environ.get("FUSION_HIST_RHO", "0.0"))   # ρ·ΔY sans effet (ΔY médian ≈ 0) ; 0 = réancré au jour
            dF = DEC.champ(CH, choice, lams, rho)
            idx = DEC.ecrire(dF, rho)
            log(f"fusion quotidienne : {len(dF)} jours, écrit site/front_fusion_hist_<année>.js : {idx}")

    # ---------- refit QUOTIDIEN des POIDS (option, docs/ETAT.md § 6, 22/10/2026) ----------
    # « moteur_jour » : la loi a posteriori de δ est RECALCULÉE chaque jour sur la ligne des cartes du jour, à partir de
    # signaux de POSITION lus au pas QUOTIDIEN (data/daily/, causal, publication ≤ d) ; les POIDS des signaux sont
    # réappris jour par jour en walk-forward strict (cible = δ vrai mensuel, consensus à +2 mois). SORTIE DÉDIÉE
    # parallèle (site/front_fusion_refit_hist_<année>.js), vue par défaut INCHANGÉE. Cadence `FUSION_REFIT` (jours entre
    # deux réapprentissages ; coût). Mesuré sur 2025-26 (docs/ETAT.md § 6, rapport de fusion) : avance sur les bascules
    # +4 j vs v3, mailles qui changent meilleures, MAIS Brier ≤ 30 km légèrement dégradé (bande conformale mensuelle
    # appliquée à une loi quotidienne) → NE FRANCHIT PAS « ≥ v3 sur Brier ET avance » : parallèle, pas de bascule.
    # Entrées : site/front_hist_<année>*.js (HIST=jours) + data/daily/ + data/viirs_api_<année>.csv (feux).
    if os.environ.get("FUSION_REFIT"):
        from fusion import decades as DEC
        from fusion import moteur_jour as MJ
        if not DEC.disponible():
            log("refit des poids au jour : site/front_hist_<année>*.js absents (HIST=jours non publié) — ignoré")
        else:
            cad = int(os.environ.get("FUSION_REFIT", "7"))
            mj = MJ.MoteurJour(M, R, CH, choice, lams, cadence=cad)
            dF = mj.champ()
            idx = MJ.ecrire(dF, cad)
            log(f"refit des poids au jour : {len(dF)} jours, cadence {cad}, signaux {mj.signaux}, "
                f"{mj.n_refit} réapprentissages ; écrit site/front_fusion_refit_hist_<année>.js : {idx}")

    # ---------- refit des poids au jour + BANDE conformale RECALIBRÉE au jour (option, docs/ETAT.md § 6, 22/10/2026) ----
    # « champ_bande » : comme FUSION_REFIT, mais la bande (λ par secteur) est RECALCULÉE au pas JOUR sur la loi
    # quotidienne (conformal LOCAL par secteur, walk-forward strict J-1), au lieu d'hériter de la bande MENSUELLE.
    # Validation (scratchpad/.../bench_band_*.json, rapport de fusion § « bande au jour ») : sur 2025-26 les TROIS
    # critères passent (Brier ≤ 30 km 0,02547 ≤ v3 0,02550, avance 100,6 ≥ 94,1 j, couverture médiane 0,951/secteur),
    # MAIS sur 2023-24 (réglage) la refit-poids perd l'avance (91,0 < 102,4) et la bande étroite dégrade les mailles qui
    # changent (0,4282 > 0,3872) et sous-couvre (médiane 0,93) → NE FRANCHIT PAS « ≥ v3 sur les DEUX régimes » : SORTIE
    # PARALLÈLE dédiée (site/front_fusion_refit_bande_hist_<année>.js), vue par défaut INCHANGÉE. Pass 1 (besoin
    # conformal, 21 λ × tous les jours) ~930 s. Entrées : HIST=jours + data/daily/ + data/viirs_api_<année>.csv.
    if os.environ.get("FUSION_REFIT_BANDE"):
        from fusion import decades as DEC
        from fusion import moteur_jour as MJ
        if not DEC.disponible():
            log("refit + bande au jour : site/front_hist_<année>*.js absents (HIST=jours non publié) — ignoré")
        else:
            cad = int(os.environ.get("FUSION_REFIT_BANDE", "7"))
            mj = MJ.MoteurJour(M, R, CH, choice, lams, cadence=cad)
            NEED, dates, months = mj.besoins_jour()
            dF = mj.champ_bande(NEED, dates, months)
            idx = MJ.ecrire(dF, cad, suffixe="refit_bande")
            log(f"refit + bande au jour : {len(dF)} jours, cadence {cad}, {len(NEED)} jours de besoin conformal ; "
                f"écrit site/front_fusion_refit_bande_hist_<année>.js : {idx}")


if __name__ == "__main__":
    main()
