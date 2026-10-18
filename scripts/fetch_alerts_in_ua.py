"""Alertes de alerts.in.ua (menaces d'artillerie, combats de rue) : relève du canal officiel muet depuis le 7/9/2026.

    python3 scripts/fetch_alerts_in_ua.py <dossier_brut>

Nécessite le secret ALERTS_IN_UA_TOKEN (jeton personnel, alerts.in.ua/api-request) ; sans lui, l'étape est sautée
sans erreur. Le jeton est envoyé dans l'en-tête Authorization, jamais dans l'URL, et n'est jamais affiché.
L'API ne garde qu'un mois d'historique (month_ago) : il faut l'interroger au moins une fois par mois. On conserve en
local (dossier brut, jamais publié) les alertes de type artillery_shelling et urban_fights ; seuls des agrégats (heures
par hromada puis par maille et par mois) sont publiés, comme pour le canal officiel.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from envkeys import auth_header, present, scrub  # noqa: E402

API = "https://api.alerts.in.ua/v1"
TYPES = {"artillery_shelling", "urban_fights"}
UA = "ukraine-feux/1.0 (non-commercial research)"


def get(path):
    req = urllib.request.Request(f"{API}{path}", headers={"User-Agent": UA, **auth_header("ALERTS_IN_UA_TOKEN")})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "raw"
    if present("ALERTS_IN_UA_TOKEN") == "non":
        print("alerts.in.ua : secret ALERTS_IN_UA_TOKEN absent, étape sautée")
        return
    out_dir = os.path.join(raw, "alerts_in_ua")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "alerts.json")
    known = {a["id"]: a for a in json.load(open(path))} if os.path.exists(path) else {}
    try:
        # identifiants d'oblast d'alerts.in.ua : 3 à 31 (à vérifier à la première exécution avec le jeton ; les
        # identifiants inconnus renvoient une erreur, comptée sans arrêter la collecte)
        bad = 0
        for uid in range(3, 32):
            try:
                rows = get(f"/regions/{uid}/alerts/month_ago.json").get("alerts", [])
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"alerts.in.ua : oblast {uid} : {scrub(e)}")
                continue
            for a in rows:
                if a.get("alert_type") in TYPES:
                    known[a["id"]] = {k: a.get(k) for k in ("id", "alert_type", "location_title", "location_uid", "location_oblast",
                                                              "location_oblast_uid", "started_at", "finished_at")}
            time.sleep(2.0)
        print(f"alerts.in.ua : {29 - bad} oblasts lus, {bad} en erreur")
    except Exception as e:  # noqa: BLE001
        print("alerts.in.ua : erreur", scrub(e))
        return
    json.dump(sorted(known.values(), key=lambda a: a["started_at"] or ""), open(path, "w"), ensure_ascii=False)
    print(f"alerts.in.ua : {len(known)} alertes (artillerie, combats de rue) en local")


if __name__ == "__main__":
    main()
