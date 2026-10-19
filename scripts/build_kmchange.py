"""VARIATION DES KM² OCCUPÉS sur 30 jours et 12 mois, estimateur DÉBRUITÉ, pour les chiffres clés.

    python3 scripts/build_kmchange.py [--root .]      ->  site/kmchange.js   (window.KMCHANGE)

Estimateur : variation de la surface où p ≥ 0,5 dans la vue équilibrée (FRONT.months[i].a.equilibre.tout[1], c.-à-d.
déplacement de la ligne 50 % × longueur), au lieu de la variation de l'espérance Σp (tout[0]). Σp additionne les
petites fluctuations de p dans la zone grise de secteurs immobiles sur 1 200 km de front ; la surface p ≥ 0,5 ne
bouge que là où la ligne médiane traverse des mailles. Aucun paramètre, aucune information postérieure à t.
Étude : ukraine-recherche/analyses/erreur_km2/RAPPORT.md (« estimateur débruité ») : contre la vérité définitive
(consensus des cartes stabilisé, lu 3 mois après), erreur p80 sur 12 mois 1 189 km² contre 1 670 pour Σp (2024-01 →
2026-06), 723 contre 1 520 depuis 2025 ; sur 30 jours aucun estimateur ne fait mieux que « zéro » (p80 ≈ 600) :
`m1_mesurable` n'est vrai que si |m1| dépasse le p80.

Entrées : site/front.js (mois publiés), data/kmchange_eval.json (erreurs empiriques, recalculées dans l'étude ; le
fichier est facultatif). numpy n'est même pas nécessaire. Quelques dixièmes de seconde.
"""
import argparse
import datetime as dt
import json
import os


def load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))


def month_end(d, k):
    y, m = d.year, d.month + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    FR = load_js(os.path.join(a.root, "site", "front.js"))
    p_eval = os.path.join(a.root, "data", "kmchange_eval.json")
    ev = json.load(open(p_eval, encoding="utf-8")) if os.path.exists(p_eval) else {}
    err = {w: {k: ev[w][k] for k in ("p50", "p80", "bias", "n") if k in ev[w]} for w in ("m1", "m12") if w in ev}
    recent = {w: {k: ev["recent"][w][k] for k in ("p50", "p80", "bias") if k in ev["recent"][w]}
              for w in ("m1", "m12") if w in ev.get("recent", {})}

    def area(mo, i):
        try:
            return mo["a"]["equilibre"]["tout"][i]
        except (KeyError, IndexError, TypeError):
            return None

    by_date = {}
    for mo in FR["months"]:
        if mo and len(mo.get("date", "")) == 10:
            by_date[dt.date.fromisoformat(mo["date"])] = mo
    out = []
    for mo in FR["months"]:
        if not mo or len(mo.get("date", "")) != 10 or area(mo, 1) is None:
            out.append(None)
            continue
        t = dt.date.fromisoformat(mo["date"])
        rec = {"date": mo["date"]}
        for w, L in (("m1", 1), ("m12", 12)):
            # début de fenêtre : instantané de fin du mois L mois plus tôt (aussi pour un instantané quotidien éventuel)
            m0 = by_date.get(month_end(t, -L))
            a1, a0 = area(mo, 1), area(m0, 1) if m0 else None
            rec[w] = None if a0 is None else int(round((a1 - a0) / 10.0) * 10)
            # ancien chiffre (espérance Σp), pour comparaison
            e1, e0 = area(mo, 0), area(m0, 0) if m0 else None
            rec[w + "_esperance"] = None if e0 is None else int(e1 - e0)
        p80 = err.get("m1", {}).get("p80")
        rec["m1_mesurable"] = None if rec["m1"] is None or p80 is None else abs(rec["m1"]) > p80
        p80 = err.get("m12", {}).get("p80")
        rec["m12_mesurable"] = None if rec["m12"] is None or p80 is None else abs(rec["m12"]) > p80
        out.append(rec)
    K = {
        "generated": FR.get("generated"),
        "months": out,
        "error": err,
        "errorRecent": recent,
        "truth": ev.get("verite"),
        "period": ev.get("periode"),
        "method": ("variation de la surface où p ≥ 0,5 (vue équilibrée, ligne 50 % : FRONT.months[i].a.equilibre.tout[1]) "
                   "entre l'instantané et celui de 1 ou 12 mois plus tôt, arrondie à 10 km² ; > 0 = avancée russe. "
                   "Remplace la variation de l'espérance Σp (tout[0]), que le bruit de p dans les secteurs immobiles gonfle. "
                   "error = |écart| médian (p50) et p80 contre la vérité définitive (consensus des cartes stabilisé, lu 3 mois "
                   "après) ; m*_mesurable = |variation| > p80."),
    }
    path = os.path.join(a.root, "site", "kmchange.js")
    open(path, "w", encoding="utf-8").write("window.KMCHANGE=" + json.dumps(K, ensure_ascii=False, separators=(",", ":")) + ";")
    last = next((r for r in reversed(out) if r), None)
    print(f"kmchange.js : {sum(1 for r in out if r)} instantanés ; dernier {last}")


if __name__ == "__main__":
    main()
