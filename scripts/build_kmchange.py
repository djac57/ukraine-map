"""VARIATION DES KM² OCCUPÉS sur 30 jours et 12 mois, estimateur DÉBRUITÉ, et son ERREUR EMPIRIQUE recalculée à chaque
lancement, pour les chiffres clés.

    python3 scripts/build_kmchange.py [--root .]      ->  site/kmchange.js   (window.KMCHANGE)

ESTIMATEUR : variation de la surface où p ≥ 0,5 dans la vue équilibrée (FRONT.months[i].a.equilibre.tout[1], c.-à-d.
déplacement de la ligne 50 % × longueur) entre l'instantané et celui de 1 ou 12 mois plus tôt, au lieu de la variation
de l'espérance Σp (tout[0]). Σp additionne les petites fluctuations de p dans la zone grise de secteurs immobiles sur
1 200 km de front ; la surface p ≥ 0,5 ne bouge que là où la ligne médiane traverse des mailles. Aucun paramètre,
aucune information postérieure à t. Étude : ukraine-recherche/analyses/erreur_km2/RAPPORT.md (« estimateur débruité »).

VÉRITÉ DÉFINITIVE (« def3 », version mensuelle) : par localité VIINA, consensus des cartes (majorité DeepState corrigé
du journal / ISW / Wikipédia ; égalité ou contesté = 0,5 ; aucune carte = 0) reconstruit à chaque fin de mois depuis les
différences publiées FRONT.months[].d ; état définitif à t = médiane des états aux fins de mois t + 1, t + 2, t + 3
(lu 3 mois après, bascules passagères effacées) ; surface = Σ état × mailles de 2 km (mailles ukrainiennes).
Variation vraie = surface définitive(t) − surface définitive(t − 1 ou 12 mois).
Erreur = notre variation − variation vraie. Statistiques : p50 / p80 de |erreur|, biais = moyenne (+ : on surestime
l'avancée russe), n. `error` : fins de mois 2024-01 → dernier mois évaluable (t + 3 publié) ; `errorRecent` : 24
derniers mois évaluables (glissant). `reference` : mêmes statistiques pour l'ancien chiffre (Σp) et le consensus du jour.
Repli : si front.js ne porte pas les états (months[].d), erreurs lues dans data/kmchange_eval.json (figées).

Entrées : site/front.js seul (data/kmchange_eval.json en repli). numpy. Moins d'une seconde.
"""
import argparse
import base64
import datetime as dt
import json
import os

import numpy as np

EVAL_FROM = dt.date(2024, 1, 1)
RECENT_N = 24
LAGS = (1, 2, 3)   # fins de mois lues pour l'état définitif
VIEWS = ("ru", "ua", "tiers")   # vues par camp (FRONT.months[].a.<vue>)


def load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))


def month_end(d, k):
    y, m = d.year, d.month + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)


def stats(e):
    e = np.asarray(e, float)
    if not len(e):
        return None
    a = np.abs(e)
    return {"p50": int(round(float(np.median(a)))), "p80": int(round(float(np.percentile(a, 80)))),
            "bias": int(round(float(e.mean()))), "n": int(len(e))}


def place_cells(FR):
    """Nombre de mailles ukrainiennes (2 km) par localité VIINA, depuis le maillage publié (runs)."""
    G = FR["grid"]
    b = base64.b64decode(FR["runs"])
    v = np.frombuffer(b, np.uint8).reshape(-1, 3).astype(np.int64)
    val, n = v[:, 0] | v[:, 1] << 8, v[:, 2]
    ok = val < 65534
    w = np.bincount(val[ok], weights=n[ok], minlength=FR["nPlaces"])[:FR["nPlaces"]]
    assert n.sum() == G["nx"] * G["ny"], "maillage publié incohérent"
    return w * (G["size"] / 1000) ** 2


def consensus_areas(FR, wkm2):
    """{date de fin de mois: état du consensus par localité (1 / 0,5 / 0)} reconstruit depuis months[].d."""
    cur = np.zeros(FR["nPlaces"], np.int64)
    out = {}
    for mo in FR["months"]:
        if not mo or "d" not in mo or len(mo.get("date", "")) != 10:
            continue
        d = np.asarray(mo["d"], np.int64).reshape(-1, 2)
        if len(d):
            cur[d[:, 0]] = d[:, 1]
        c = np.stack([(cur >> (2 * s)) & 3 for s in range(3)], 1)
        ru, ua = (c == 2).sum(1), (c == 1).sum(1)
        known = (c > 0).any(1)
        out[dt.date.fromisoformat(mo["date"])] = np.where(ru > ua, 1.0, np.where(ua > ru, 0.0, np.where(known, 0.5, 0.0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    FR = load_js(os.path.join(a.root, "site", "front.js"))

    def area(mo, i, view="equilibre"):
        try:
            return mo["a"][view]["tout"][i]
        except (KeyError, IndexError, TypeError):
            return None

    by_date = {dt.date.fromisoformat(mo["date"]): mo for mo in FR["months"] if mo and len(mo.get("date", "")) == 10}
    out = []
    for mo in FR["months"]:
        if not mo or len(mo.get("date", "")) != 10 or area(mo, 1) is None:
            out.append(None)
            continue
        t = dt.date.fromisoformat(mo["date"])
        rec = {"date": mo["date"]}
        for w, L in (("m1", 1), ("m12", 12)):
            m0 = by_date.get(month_end(t, -L))   # début de fenêtre : instantané de fin du mois L mois plus tôt
            a1, a0 = area(mo, 1), area(m0, 1) if m0 else None
            rec[w] = None if a0 is None else int(round((a1 - a0) / 10.0) * 10)
            e1, e0 = area(mo, 0), area(m0, 0) if m0 else None
            rec[w + "_esperance"] = None if e0 is None else int(e1 - e0)   # ancien chiffre (Σp), pour comparaison
            for v in VIEWS:   # même estimateur dans les vues par camp
                b1, b0 = area(mo, 1, v), area(m0, 1, v) if m0 else None
                rec[f"{w}_{v}"] = None if b1 is None or b0 is None else int(round((b1 - b0) / 10.0) * 10)
        out.append(rec)

    # ---- erreur empirique recalculée ----
    has_states = any(mo and "d" in mo for mo in FR["months"])
    err = recent = ref = views = None
    src = None
    if has_states:
        C = consensus_areas(FR, None)
        w_ = place_cells(FR)
        A0 = {d: float((v * w_).sum()) for d, v in C.items()}            # consensus du jour
        Adef = {}
        for d in C:
            xs = [C.get(month_end(d, k)) for k in LAGS]
            if all(x is not None for x in xs):
                Adef[d] = float((np.median(np.stack(xs), 0) * w_).sum())
        err, recent, ref = {}, {}, {"esperance": {}, "consensus_du_jour": {}}
        views = {v: {} for v in ("equilibre",) + VIEWS}
        views["desaccord"] = {}
        rng = {}
        for w, L in (("m1", 1), ("m12", 12)):
            R = []
            for t in sorted(by_date):
                t0 = month_end(t, -L)
                if t < EVAL_FROM or t0 not in by_date or t not in Adef or t0 not in Adef:
                    continue
                truth = Adef[t] - Adef[t0]
                ours = area(by_date[t], 1) - area(by_date[t0], 1)
                esp = area(by_date[t], 0) - area(by_date[t0], 0)
                c0 = A0[t] - A0[t0] if t in A0 and t0 in A0 else None
                dv = {}
                for v in VIEWS:
                    b1, b0 = area(by_date[t], 1, v), area(by_date[t0], 1, v)
                    dv[v] = None if b1 is None or b0 is None else b1 - b0
                for v in ("nowcast",):   # NOTRE estimation (champ avec signaux de combat) : espérance et surface p ≥ 0,5
                    for k_ in (0, 1):
                        b1, b0 = area(by_date[t], k_, v), area(by_date[t0], k_, v)
                        dv[f"{v}{k_}"] = None if b1 is None or b0 is None else b1 - b0
                R.append((t, ours - truth, esp - truth, None if c0 is None else c0 - truth, dv, truth))
            if not R:
                continue
            err[w] = dict(stats([r[1] for r in R]), **{"from": R[0][0].isoformat(), "to": R[-1][0].isoformat()})
            Rr = R[-RECENT_N:]
            recent[w] = dict(stats([r[1] for r in Rr]), **{"from": Rr[0][0].isoformat(), "to": Rr[-1][0].isoformat()})
            ref["esperance"][w] = stats([r[2] for r in R])
            ref["consensus_du_jour"][w] = stats([r[3] for r in R if r[3] is not None])
            views["equilibre"][w] = stats([r[1] for r in R])
            for v in VIEWS:
                views[v][w] = stats([r[4][v] - r[5] for r in R if r[4][v] is not None])
            for k_, lab in ((0, "esperance"), (1, "p50")):
                views.setdefault("nowcast_" + lab, {})[w] = stats([r[4][f"nowcast{k_}"] - r[5] for r in R if r[4].get(f"nowcast{k_}") is not None])
            dd = [r[4]["ru"] - r[4]["ua"] for r in R if r[4]["ru"] is not None and r[4]["ua"] is not None]
            st = stats(dd)
            views["desaccord"][w] = None if st is None else dict(st, signe=st.pop("bias"))
            rng[w] = (R[0][0], R[-1][0])
        src = "recalculée depuis site/front.js (états des cartes months[].d)"
    if not err:
        p_eval = os.path.join(a.root, "data", "kmchange_eval.json")
        ev = json.load(open(p_eval, encoding="utf-8")) if os.path.exists(p_eval) else {}
        err = {w: {k: ev[w][k] for k in ("p50", "p80", "bias", "n") if k in ev[w]} for w in ("m1", "m12") if w in ev}
        recent = {w: {k: ev["recent"][w][k] for k in ("p50", "p80", "bias", "n") if k in ev["recent"][w]}
                  for w in ("m1", "m12") if w in ev.get("recent", {})}
        ref = views = None
        src = "repli : data/kmchange_eval.json (figé, vérité par décades du cache de recherche)"

    for rec in out:
        if rec is None:
            continue
        for w in ("m1", "m12"):
            p80 = (err.get(w) or {}).get("p80")
            rec[w + "_mesurable"] = None if rec[w] is None or p80 is None else abs(rec[w]) > p80

    K = {
        "generated": FR.get("generated"),
        "months": out,
        "error": err,
        "errorRecent": recent,
        "reference": ref,
        "views": views,
        "errorSource": src,
        "truth": ("consensus des cartes (DeepState corrigé du journal / ISW / Wikipédia, localités VIINA, égalité ou contesté "
                  "= 0,5) ; état définitif à t = médiane des états aux fins de mois t + 1, t + 2, t + 3"),
        "method": ("variation de la surface où p ≥ 0,5 (vue équilibrée, ligne 50 % : FRONT.months[i].a.equilibre.tout[1]) "
                   "entre l'instantané et celui de 1 ou 12 mois plus tôt, arrondie à 10 km² ; > 0 = avancée russe. "
                   "Remplace la variation de l'espérance Σp (tout[0]), que le bruit de p dans les secteurs immobiles gonfle. "
                   "error = |écart| médian (p50), p80 et biais contre la vérité définitive, fins de mois 2024-01 → dernier mois "
                   f"évaluable ; errorRecent = {RECENT_N} derniers mois évaluables ; m*_mesurable = |variation| > error.p80."),
    }
    path = os.path.join(a.root, "site", "kmchange.js")
    open(path, "w", encoding="utf-8").write("window.KMCHANGE=" + json.dumps(K, ensure_ascii=False, separators=(",", ":")) + ";")
    last = next((r for r in reversed(out) if r), None)
    print(f"kmchange.js : {sum(1 for r in out if r)} instantanés ; dernier {last}")
    print(f"erreur ({src}) : {err} ; récente : {recent}")
    if ref:
        print(f"références : {ref}")
        print(f"vues : {views}")


if __name__ == "__main__":
    main()
