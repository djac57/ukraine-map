"""Bloc de SIGNAUX PHYSIQUES (indépendants des cartes) pour le moteur de SENS, et harnais de validation.

Productionise le détecteur de recherche `ukraine-recherche/analyses/detecteur_contreoffensives/detect_phys.py`
(RAPPORT du 22/10/2026). Trois signaux physiques, chacun normalisé par tronçon sur les 12 mois précédents (passé
seulement, z glissant borné à ±5 ; signe + = UA pousse) :
  - asymétrie des combats   `az`  (z relatif des combats derrière la ligne ; en production : z glissant de l'asymétrie
                                   publiée fa_ru − fa_ua, qui est déjà « combats derrière la ligne ») ;
  - asymétrie des impacts S2 `z_RU − z_UA` (le PLUS SÛR : impacts côté russe = défenseur russe frappé = reprise UA) ;
  - Telegram « qui attaque / présence » (log1p(UA) − log1p(RU) sur 30 j, puis z glissant).
Le score combiné PHYS = moyenne des composantes disponibles (signé).

Rôle dans le moteur (reco du RAPPORT, § Recommandation) :
  (1) 2ᵉ étage SENS / asymétrie : ces signaux entrent dans P(sens | bouge) (build_direction.py). Gain d'AUC de sens.
  (2) INDICE HORS-CARTES sur les tuiles CALMES SUR LES CARTES (|ΔY 60 j| < 1 km) : là où le momentum des cartes est
      muet, le physique voit la bascule venir (gain +0,03 à +0,06 d'AUC, sans aucune carte → sert le KPI hors-cartes).
PAS dans P(mouvement) (le momentum des cartes y est meilleur), PAS en flèche autonome (85-92 % de fausses alertes seul).
Correctif méthodo du RAPPORT repris ici : gâtage `unk < 0,8` (fraction de zone grise), PAS `unk == 0`.

Ce fichier a deux usages :
  - MODULE : build_direction.py importe `trailing_z`, `calm_on_maps`, `hors_cartes_score` pour calculer l'indice
    hors-cartes de façon cohérente avec la validation ;
  - BENCH  : `python3 -I scripts/signaux_physiques.py --bench` rejoue le walk-forward strict sur le panel décadaire
    figé (analyses/sens/panel_dec.csv) et écrit `analyses/detecteur_contreoffensives/integration.txt` :
      (A) reproduction M / P / M+P (sanité vs RAPPORT) ;
      (B) INTÉGRATION : étage sens de PRODUCTION (momentum + asym publiée + Telegram) vs + S2 vs + bloc physique
          complet, AUC de sens (tout le front / tuiles calmes) et AUC « va bouger » (non dégradée) ;
      (C) justification de l'indice SEGMENTÉ sur les tuiles calmes (où S2 paie) ;
      (D) cas Vivaldi : avance S2 (~50-60 j) sur la bascule des cartes.
"""
import math
import os

import numpy as np

# -------------------------------------------------------------------- API module (importée par build_direction.py)
UNK_MAX = 0.8          # tuile exploitable si moins de 80 % en zone grise (unk est une FRACTION) — correctif du RAPPORT
WIN_DEC = 36           # 12 mois de décades pour mean/std glissants
MINOBS = 6
CALM_KM = 1.0          # |ΔY sur ~60 j| < 1 km => « calme sur les cartes »
HC_THRESH = 1.0        # |score hors-cartes| au-dessus duquel l'indice s'allume sur une tuile calme


def trailing_z(values, idx, win=WIN_DEC, minobs=MINOBS, floor=0.5, clip=5.0):
    """z glissant d'une série {indice_de_pas: valeur}, sur les `win` pas PRÉCÉDENTS (pas le pas courant).
    Passé seulement ; renvoie 0.0 si moins de `minobs` points passés ou si le pas courant est absent."""
    if idx not in values:
        return 0.0
    past = [values[q] for q in range(idx - win, idx) if q in values]
    if len(past) < minobs:
        return 0.0
    mu, sd = float(np.mean(past)), float(np.std(past))
    if sd < 1e-6:
        sd = max(floor, 1.0)
    return float(np.clip((values[idx] - mu) / max(sd, floor), -clip, clip))


def calm_on_maps(y_now, y_60d):
    """La tuile est-elle vue IMMOBILE par les cartes sur ~60 j ? (les bascules que le momentum ne voit pas venir)."""
    if y_now is None or y_60d is None:
        return False
    return abs(y_now - y_60d) < CALM_KM


def hors_cartes_score(asym_z=None, s2_asym=None, tg_z=None):
    """Score physique hors-cartes signé (+ = UA pousse, − = RU pousse), moyenne des composantes DISPONIBLES.
    S2 est la composante la plus sûre : on la garde brute (déjà un z d'écart RU−UA) bornée à ±5.
    Renvoie (score, n_composantes) ; (None, 0) si aucune composante."""
    comp = []
    if asym_z is not None:
        comp.append(float(np.clip(asym_z, -5, 5)))
    if s2_asym is not None:
        comp.append(float(np.clip(s2_asym, -5, 5)))
    if tg_z is not None:
        comp.append(float(np.clip(tg_z, -5, 5)))
    if not comp:
        return None, 0
    return float(np.mean(comp)), len(comp)


def hors_cartes_cue(calm, score, n_comp, s2_present):
    """Bits de l'indice hors-cartes pour une tuile (à OR dans le champ `indices` de front_sens.js).
    Ne s'allume QUE sur une tuile calme sur les cartes et avec |score| ≥ seuil : c'est un indice, pas une flèche."""
    bits = 0
    if calm and score is not None and n_comp >= 1 and abs(score) >= HC_THRESH:
        bits |= 1024                               # tuile calme sur les cartes + signal physique de bascule
        bits |= 2048 if score > 0 else 4096        # 2048 : sens UA (reprise) ; 4096 : sens RU (poussée)
        if s2_present:
            bits |= 8192                            # S2 contribue (composante la plus sûre)
    return bits


# ==================================================================== BENCH (validation walk-forward sur panel figé)
def _bench():
    import csv
    import datetime as dt

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RECH = os.environ.get("UKRAINE_RECHERCHE",
                          os.path.join(os.path.dirname(os.path.dirname(ROOT)), "perso", "ukraine-recherche"))
    if not os.path.isdir(RECH):
        RECH = os.path.join(os.path.dirname(ROOT), "ukraine-recherche")
    PANEL = os.path.join(RECH, "analyses", "sens", "panel_dec.csv")
    OUTDIR = os.path.join(RECH, "analyses", "detecteur_contreoffensives")
    OUT = open(os.path.join(OUTDIR, "integration.txt"), "w")

    def pr(*a):
        print(*a)
        print(*a, file=OUT)

    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return np.nan

    rows = list(csv.DictReader(open(PANEL)))
    dates = sorted({r["date"] for r in rows})
    di = {d: i for i, d in enumerate(dates)}
    DATE = [dt.date.fromisoformat(d) for d in dates]
    ks = sorted({int(r["k"]) for r in rows})
    ki = {k: i for i, k in enumerate(ks)}
    ND, NK = len(dates), len(ks)
    NAN = np.nan
    Y = np.full((ND, NK), NAN); UNK = np.ones((ND, NK)); YP = np.full((ND, NK), NAN)
    AZ = np.full((ND, NK), NAN); FA = np.full((ND, NK), NAN)          # az recherche ; fa_ru-fa_ua publié
    S2 = np.full((ND, NK), NAN); TG = np.full((ND, NK), NAN)
    S2RU = np.full((ND, NK), NAN); S2UA = np.full((ND, NK), NAN)
    LON = np.full(NK, NAN); LAT = np.full(NK, NAN)
    for r in rows:
        t, k = di[r["date"]], ki[int(r["k"])]
        Y[t, k] = f(r["Y"]); UNK[t, k] = f(r["unk"]) if r["unk"] != "" else 1.0
        YP[t, k] = f(r["ypub"])
        AZ[t, k] = f(r["az"])
        far, fau = f(r["fa_ru"]), f(r["fa_ua"])
        if not (np.isnan(far) or np.isnan(fau)):
            FA[t, k] = far - fau          # asymétrie publiée signée (+ = combats côté russe = reprise UA possible)
        zru, zua = f(r["s2z30_RU"]), f(r["s2z30_UA"])
        if not (np.isnan(zru) or np.isnan(zua)):
            S2[t, k] = zru - zua
        S2RU[t, k], S2UA[t, k] = zru, zua
        ua = f(r["tg30_ua_attUA"]) + f(r["tg30_ru_attUA"]) + f(r["tg30_ru_presUA"])
        ru = f(r["tg30_ru_attRU"]) + f(r["tg30_ua_attRU"]) + f(r["tg30_ua_presRU"])
        TG[t, k] = math.log1p(max(ua, 0)) - math.log1p(max(ru, 0))
        LON[k] = f(r["lon"]); LAT[k] = f(r["lat"])

    def tz(M):
        Z = np.full_like(M, NAN)
        for k in range(NK):
            col = {t: M[t, k] for t in range(ND) if not np.isnan(M[t, k])}
            for t in range(ND):
                if not np.isnan(M[t, k]):
                    Z[t, k] = trailing_z(col, t)
        return Z

    AZz, FAz, S2z, TGz = tz(AZ), tz(FA), tz(S2), tz(TG)
    PHYS = np.full((ND, NK), NAN)
    for t in range(ND):
        for k in range(NK):
            comp = [v for v in (AZz[t, k], S2z[t, k], TGz[t, k]) if not np.isnan(v)]
            if comp:
                PHYS[t, k] = np.mean(comp)

    H = 3
    pr(f"panel : {len(rows)} tronçon-décades, {ND} décades {dates[0]}..{dates[-1]}, {NK} tronçons ; "
       f"gâtage unk < {UNK_MAX}")

    def future_move(t, k):
        if t + H >= ND or UNK[t, k] > UNK_MAX or UNK[t + H, k] > UNK_MAX:
            return NAN
        a, b = Y[t, k], Y[t + H, k]
        return NAN if (np.isnan(a) or np.isnan(b)) else b - a

    def mom(t, k, lag, M=Y):
        if t - lag < 0 or UNK[t, k] > UNK_MAX or UNK[t - lag, k] > UNK_MAX:
            return NAN
        a, b = M[t - lag, k], M[t, k]
        return NAN if (np.isnan(a) or np.isnan(b)) else b - a

    def z0(x):
        return 0.0 if np.isnan(x) else float(x)

    # --- jeux de variables ---
    def f_M(t, k):                       # cartes seules (momentum vérité) — comme le RAPPORT
        m1, m3, m6 = z0(mom(t, k, 1)), z0(mom(t, k, 3)), z0(mom(t, k, 6))
        return [m1, m3, m6, abs(m1), abs(m3), abs(m6), z0(Y[t, k])]

    def f_P(t, k):                       # physique seul — comme le RAPPORT
        a, s, g = z0(AZz[t, k]), z0(S2z[t, k]), z0(TGz[t, k])
        p = z0(PHYS[t, k])
        return [a, s, g, abs(a), abs(s), abs(g), p, abs(p)]

    def f_base(t, k):                    # 2ᵉ étage PRODUCTION : momentum (publié) + asym PUBLIÉE + Telegram (sans S2)
        m1, m3 = z0(mom(t, k, 1, YP)), z0(mom(t, k, 3, YP))
        return [m1, m3, z0(FA[t, k]), z0(FAz[t, k]), z0(TGz[t, k]), z0(TG[t, k])]

    def f_base_s2(t, k):                 # + asymétrie S2 (la composante la plus sûre)
        return f_base(t, k) + [z0(S2z[t, k]), z0(S2[t, k]), 1.0 if not np.isnan(S2[t, k]) else 0.0]

    def f_base_phys(t, k):               # + bloc physique complet (S2 + az continu)
        return f_base_s2(t, k) + [z0(AZz[t, k])]

    def collect(feat, target, X, movers=False, quiet=None, start="2023-03"):
        """quiet=None : tout le front ; True : tuiles calmes ; False : tuiles non calmes (|ΔY 60 j| < / ≥ 1 km)."""
        yt, sc = [], []
        for t in range(ND):
            if dates[t] < start:
                continue
            Xtr, ytr = [], []
            for q in range(max(0, t - H - WIN_DEC), t - H + 1):
                for k in range(NK):
                    mv = future_move(q, k)
                    if np.isnan(mv) or (movers and abs(mv) < X):
                        continue
                    lab = int(abs(mv) >= X) if target == "move" else int(mv <= -X)
                    Xtr.append(feat(q, k)); ytr.append(lab)
            if len(set(ytr)) < 2 or len(ytr) < 40:
                continue
            clf = LogisticRegression(max_iter=200, C=1.0).fit(np.array(Xtr), np.array(ytr))
            for k in range(NK):
                mv = future_move(t, k)
                if np.isnan(mv) or (movers and abs(mv) < X):
                    continue
                if quiet is not None:
                    m6 = mom(t, k, 6)
                    if np.isnan(m6) or (abs(m6) < CALM_KM) != quiet:
                        continue
                lab = int(abs(mv) >= X) if target == "move" else int(mv <= -X)
                yt.append(lab); sc.append(clf.predict_proba([feat(t, k)])[0, 1])
        if len(set(yt)) < 2:
            return (np.nan, len(yt), int(sum(yt)))
        return (round(roc_auc_score(yt, sc), 3), len(yt), int(sum(yt)))

    FEATS = [("M cartes", f_M), ("P physique", f_P), ("M+P", lambda t, k: f_M(t, k) + f_P(t, k))]
    PRODF = [("BASE prod (mom+asym+Tg)", f_base), ("  + S2", f_base_s2), ("  + bloc physique", f_base_phys)]

    pr("\n" + "=" * 74)
    pr("(A) REPRODUCTION — sanité vs RAPPORT (M / P / M+P)")
    pr("=" * 74)
    for title, tgt, X, mv, q in [("Va bouger ≥1 km", "move", 1.0, False, None),
                                 ("Qui pousse (movers) ≥1 km", "sens", 1.0, True, None),
                                 ("Va bouger ≥1 km, tuiles CALMES", "move", 1.0, False, True),
                                 ("Qui pousse ≥1 km, tuiles CALMES", "sens", 1.0, True, True)]:
        pr(f"\n### {title}")
        res = {}
        for nm, fn in FEATS:
            a = collect(fn, tgt, X, movers=mv, quiet=q)
            res[nm] = a
            pr(f"   {nm:14} AUC {a[0]:.3f}  (n={a[1]}, pos={a[2]})")
        if not any(np.isnan(res[n][0]) for n in ("M cartes", "M+P")):
            pr(f"   -> HORS-CARTES Δ = AUC(M+P)-AUC(M) = {res['M+P'][0]-res['M cartes'][0]:+.3f}")

    pr("\n" + "=" * 74)
    pr("(B) INTÉGRATION — 2ᵉ étage SENS de PRODUCTION + bloc physique")
    pr("=" * 74)
    for title, tgt, X, mv, q in [("Qui pousse (movers) ≥1 km — TOUT le front", "sens", 1.0, True, None),
                                 ("Qui pousse (movers) ≥1 km — tuiles CALMES", "sens", 1.0, True, True),
                                 ("Qui pousse (movers) ≥1 km — tuiles NON calmes", "sens", 1.0, True, False),
                                 ("Qui pousse (movers) ≥2 km — TOUT le front", "sens", 2.0, True, None),
                                 ("Va bouger ≥1 km (contrôle : non dégradé)", "move", 1.0, False, None)]:
        pr(f"\n### {title}")
        base = None
        for nm, fn in PRODF:
            a = collect(fn, tgt, X, movers=mv, quiet=q)
            if base is None:
                base = a[0]
            d = "" if (np.isnan(a[0]) or np.isnan(base)) else f"   Δ vs BASE {a[0]-base:+.3f}"
            pr(f"   {nm:26} AUC {a[0]:.3f}  (n={a[1]}, pos={a[2]}){d}")

    pr("\n" + "=" * 74)
    pr("(C) VIVALDI — avance de l'asymétrie S2 sur la bascule des cartes (tronçon 15048, nord de Lyman)")
    pr("=" * 74)
    pr(f"{'décade':11} {'Y':>6} {'s2z_RU':>7} {'s2z_UA':>7} {'RU-UA':>6} {'S2z(gliss)':>10} {'PHYS':>6}")
    k = ki.get(15048)
    if k is not None:
        for t in range(di.get("2026-04-30", 0), di.get("2026-10-10", ND - 1) + 1):
            pr(f"{dates[t]:11} {z0(Y[t,k]):>6.1f} {z0(S2RU[t,k]):>7.2f} {z0(S2UA[t,k]):>7.2f} "
               f"{z0(S2[t,k]):>6.2f} {z0(S2z[t,k]):>10.2f} {z0(PHYS[t,k]):>6.2f}")
        # première décade où l'asymétrie S2 franchit +2 avant la bascule (Y passe sous 14 km)
        dep = next((t for t in range(di["2026-04-30"], ND) if not np.isnan(Y[t, k]) and Y[t, k] <= 14.0), None)
        alert = next((t for t in range(di["2026-04-30"], (dep or ND)) if not np.isnan(S2[t, k]) and S2[t, k] >= 2), None)
        if dep and alert:
            pr(f"   -> bascule cartes {dates[dep]} ; 1re alerte S2 (RU−UA ≥ 2) {dates[alert]} ; "
               f"avance = {(DATE[dep]-DATE[alert]).days} jours")
    OUT.close()
    print("\nécrit", os.path.join(OUTDIR, "integration.txt"))


if __name__ == "__main__":
    import sys
    if "--bench" in sys.argv:
        _bench()
    else:
        print(__doc__)
        print("Usage : python3 -I scripts/signaux_physiques.py --bench   (valide sur le panel décadaire figé)")
