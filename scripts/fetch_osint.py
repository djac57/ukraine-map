"""Télécharge les couches OSINT ouvertes (conditions de réutilisation vérifiées, voir la page).

    python3 scripts/fetch_osint.py <dossier_brut> [--skip-warspotting]

- WarSpotting (API publique, usage non lucratif avec crédit) : pertes russes documentées, jour par
  jour, jusqu'à J-7 au plus tard ; un fichier JSON par jour dans <dossier_brut>/warspotting/.
  Limite de l'API respectée : 10 requêtes / 10 s.
- Oryx via scarnecchia/oryx_data (CC BY 4.0) : compteurs quotidiens par type et par camp.
- VIINA 2.0 (Zhukov et Ayers, ODbL) : statut quotidien de contrôle des localités ukrainiennes selon
  DeepStateMap, l'ISW et Wikipédia, dépêches géocodées et pavage des localités ; dans <dossier_brut>/viina/.
- « Massive missile attacks on Ukraine » (Petro Ivaniuk, Kaggle, CC BY-NC-SA 4.0) : missiles et
  drones lancés / abattus par attaque, d'après les communiqués de l'armée de l'air ukrainienne.
"""
import datetime as dt
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; ukraine-feux/1.0; non-commercial research)"
WS = "https://ukr.warspotting.net/api/losses/russia/{d}/{p}/"
ORYX = "https://raw.githubusercontent.com/scarnecchia/oryx_data/main/daily_count.csv"
MISSILES = "https://www.kaggle.com/api/v1/datasets/download/piterfm/massive-missile-attacks-on-ukraine"
# Alertes aériennes par oblast (Vadimkin, licence MIT) : données bénévoles, fraîches jusqu'à aujourd'hui, niveau oblast.
# (official_data_en.csv a le raïon mais s'arrête début septembre 2026 : on prend le « volunteer » pour le récent.)
AIR_ALERTS = "https://raw.githubusercontent.com/Vadimkin/ukrainian-air-raid-sirens-dataset/main/datasets/volunteer_data_en.csv"
START = dt.date(2022, 2, 24)
LAG_DAYS = int(os.environ.get("LAG_DAYS", "1"))   # décalage de publication : J-1 depuis le 19/10/2026 (décision de l'utilisateur ; J-7 auparavant)


def get(url, timeout=120):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
        time.sleep(15 * (attempt + 1))


class Pace:
    """Au plus une requête par intervalle, tous fils confondus (limite de l'API : 10 / 10 s)."""

    def __init__(self, every):
        self.every, self.next, self.lock = every, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.every
        time.sleep(max(0.0, t - now))


def warspotting(raw_dir, workers=5):
    out = os.path.join(raw_dir, "warspotting")
    os.makedirs(out, exist_ok=True)
    last = dt.date.today() - dt.timedelta(days=LAG_DAYS)
    refresh = last - dt.timedelta(days=60)  # les ajouts tardifs sont fréquents : on relit les 60 derniers jours
    days, d = [], START
    while d <= last:
        if not os.path.exists(os.path.join(out, f"{d}.json")) or d >= refresh:
            days.append(d)
        d += dt.timedelta(days=1)
    pace = Pace(1.2)

    def one(day):
        losses, page = [], 1
        while True:
            pace.wait()
            body = get(WS.format(d=day, p=page))
            batch = json.loads(body).get("losses", []) if body else []
            # on ne garde ni l'unité ni le modèle exact : type, statut, date, position
            losses += [{k: x.get(k) for k in ("id", "type", "status", "date", "geo", "nearest_location")} for x in batch]
            if len(batch) < 100:
                break
            page += 1
        with open(os.path.join(out, f"{day}.json"), "w") as fh:
            json.dump(losses, fh, separators=(",", ":"))
        return day, len(losses)

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for day, n in ex.map(one, days):
            done += 1
            if day.day == 1:
                print(f"WarSpotting {day} ({done}/{len(days)} jours)", flush=True)
    print(f"WarSpotting : {len(days)} jours lus, à jour jusqu'au {last}")


VIINA = "https://media.githubusercontent.com/media/zhukovyuri/VIINA/main/Data/{f}"


def viina(raw_dir):
    out = os.path.join(raw_dir, "viina")
    os.makedirs(out, exist_ok=True)
    year = dt.date.today().year
    jobs = [("gn_UA_tess.geojson", "gn_UA_tess.geojson")]
    for y in range(2022, year + 1):
        jobs += [(f"control_latest_{y}.zip", f"control_{y}.zip"), (f"event_1pd_latest_{y}.zip", f"ev1pd_{y}.zip")]
    for remote, local in jobs:
        path = os.path.join(out, local)
        fresh = os.path.exists(path) and (str(year) not in local and str(year - 1) not in local)
        if fresh:
            continue  # années closes : téléchargées une fois ; année en cours et précédente : à chaque passage
        url = VIINA.format(f=remote)
        if remote.endswith(".geojson"):
            if os.path.exists(path):
                continue
            url = url.replace("media.githubusercontent.com/media", "raw.githubusercontent.com")
        data = get(url, timeout=600)
        if data:
            open(path, "wb").write(data)
    print(f"VIINA : {len(jobs)} fichiers à jour dans {out}")


UCDP = ["https://ucdp.uu.se/downloads/ged/ged261-csv.zip"]
UCDP_CANDIDATE = "https://ucdp.uu.se/downloads/candidateged/GEDEvent_v{v}.csv"


def ucdp(raw_dir):
    """UCDP GED (CC BY 4.0) : version annuelle + versions « candidate » mensuelles de l'année en cours."""
    out = os.path.join(raw_dir, "ucdp")
    os.makedirs(out, exist_ok=True)
    for url in UCDP:
        path = os.path.join(out, url.rsplit("/", 1)[1])
        if not os.path.exists(path):
            data = get(url, timeout=900)
            if data:
                open(path, "wb").write(data)
    yy = dt.date.today().year % 100
    for v in [f"{yy}_01_{yy}_06"] + [f"{yy}_0_{m}" for m in range(7, 13)]:
        path = os.path.join(out, f"GEDEvent_v{v}.csv")
        if os.path.exists(path):
            continue
        data = get(UCDP_CANDIDATE.format(v=v))
        if data:
            open(path, "wb").write(data)
    print("UCDP : à jour")


def geonames_ru(raw_dir):
    """Gazetier GeoNames de la Russie (CC BY 4.0), pour géocoder les annonces sur Koursk, Belgorod, Briansk."""
    out = os.path.join(raw_dir, "geonames")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "RU.zip")
    if not os.path.exists(path):
        data = get("https://download.geonames.org/export/dump/RU.zip", timeout=600)
        if data:
            open(path, "wb").write(data)


def main():
    raw_dir = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "raw"
    os.makedirs(raw_dir, exist_ok=True)
    data = get(ORYX)
    if data:
        open(os.path.join(raw_dir, "oryx_daily_count.csv"), "wb").write(data)
        print(f"Oryx : {len(data) // 1024} Ko")
    for kind in ("destroyed", "abandoned", "captured", "damaged"):
        data = get(ORYX.replace("daily_count", f"event_{kind}"))
        if data:
            open(os.path.join(raw_dir, f"oryx_event_{kind}.csv"), "wb").write(data)
    data = get(MISSILES)
    if data:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in ("missile_attacks_daily.csv", "missiles_and_uavs.csv"):
                open(os.path.join(raw_dir, name), "wb").write(z.read(name))
        print("Missiles et drones : OK")
    data = get(AIR_ALERTS, timeout=300)
    if data:
        open(os.path.join(raw_dir, "air_alerts.csv"), "wb").write(data)
        print(f"Alertes aériennes (Vadimkin, MIT) : {len(data) // 1024} Ko")
    viina(raw_dir)
    ucdp(raw_dir)
    geonames_ru(raw_dir)
    if "--skip-warspotting" not in sys.argv:
        warspotting(raw_dir)


if __name__ == "__main__":
    main()
