"""Télécharge les détections NASA FIRMS (VIIRS S-NPP) utiles au projet.

    python3 scripts/fetch.py <dossier_brut> [--nrt] [--api [--since AAAA-MM-JJ]] [--data <dossier_data>]

- Archives annuelles par pays (Ukraine, Russian_Federation), 2012 → dernière année publiée :
  filtrées sur la zone d'étude et réduites à un CSV compact par pays et par année.
- --api [--since AAAA-MM-JJ] : API « zone » de FIRMS (clé requise, lue dans FIRMS_MAP_KEY ou
  ~/.config/firms/credentials, jamais affichée) pour l'année sans archive publiée : VIIRS_SNPP_SP
  (traitement standard, avec le type) tant qu'il est disponible, puis VIIRS_SNPP_NRT jusqu'à aujourd'hui.
  Écrit <dossier_data>/viirs_api_<année>.csv (versionné, fusionnable, dédoublonné).
  Sans --since : reprend 10 jours avant la dernière date déjà stockée (ou au 1er janvier).
- --nrt : flux quasi temps réel des 7 derniers jours (S-NPP, comme les archives ; régions
  Europe et Russia_Asia), ajoutés à <dossier_brut>/nrt.csv sans doublons.

Données NASA FIRMS (LANCE / ESDIS), libres de réutilisation avec attribution.
"""
import csv
import datetime as dt
import io
import os
import sys
import time
import urllib.error
import urllib.request

ARCHIVE = "https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/{y}/viirs-snpp_{y}_{c}.csv"
NRT = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/{p}/csv/{n}_{r}_7d.csv"
# S-NPP seulement : la normale 2012-2021 n'existe que pour ce satellite (comparaison homogène)
NRT_PRODUCTS = [("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2")]
COUNTRIES = ["Ukraine", "Russian_Federation"]
FIRST_YEAR = 2012
# Zone d'étude : Ukraine + oblasts russes frontaliers (Koursk, Belgorod, Briansk, Voronej, Rostov…)
LAT, LON = (44.0, 53.6), (22.0, 41.5)
UA = "Mozilla/5.0 (compatible; ukraine-feux/1.0)"


def get(url, timeout=600):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2:
                raise
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
        time.sleep(10 * (attempt + 1))


API = "https://firms.modaps.eosdis.nasa.gov/api/{kind}/csv/{key}/{src}/{area}{rest}"
AREA = f"{LON[0]},{LAT[0]},{LON[1]},{LAT[1]}"


def map_key():
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key:
        path = os.path.expanduser("~/.config/firms/credentials")
        if os.path.exists(path):
            for line in open(path):
                if line.strip().startswith("FIRMS_MAP_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


def api_get(key, kind, src, area="", rest=""):
    """Appel à l'API FIRMS ; la clé n'apparaît dans aucun message d'erreur ni journal."""
    url = API.format(kind=kind, key=key, src=src, area=area, rest=rest)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            msg = str(e).replace(key, "***")
            if attempt == 3:
                raise RuntimeError(f"API FIRMS {kind}/{src} {rest} : {msg}") from None
        time.sleep(20 * (attempt + 1))


def api_fill(data_dir, since=None):
    key = map_key()
    if not key:
        print("API FIRMS : pas de clé (FIRMS_MAP_KEY), étape ignorée")
        return
    # dernière date disponible en traitement standard (SP)
    avail = api_get(key, "data_availability", "VIIRS_SNPP_SP").decode()
    sp_end = None
    for r in csv.DictReader(io.StringIO(avail)):
        if r.get("data_id") == "VIIRS_SNPP_SP":
            sp_end = dt.date.fromisoformat(r["max_date"])
    today = dt.date.today()
    year = today.year
    path = os.path.join(data_dir, f"viirs_api_{year}.csv")
    rows = {}
    if os.path.exists(path):
        with open(path) as fh:
            for r in list(csv.reader(fh))[1:]:
                rows[tuple(r[:4])] = r
    if since:
        start = dt.date.fromisoformat(since)
    elif rows:
        start = max(dt.date.fromisoformat(r[2]) for r in rows.values()) - dt.timedelta(days=10)
    else:
        start = dt.date(year, 1, 1)
    start = max(start, dt.date(year, 1, 1))
    # on remplace les jours re-téléchargés (le SP remplace le NRT quand il devient disponible)
    d, calls, n_sp, n_nrt = start, 0, 0, 0
    while d <= today:
        n = min(5, (today - d).days + 1)
        src = "VIIRS_SNPP_SP" if sp_end and d + dt.timedelta(days=n - 1) <= sp_end else "VIIRS_SNPP_NRT"
        if src == "VIIRS_SNPP_NRT" and sp_end and d <= sp_end:
            n = (sp_end - d).days + 1  # finir la partie SP avant de passer au NRT
            src = "VIIRS_SNPP_SP"
        body = api_get(key, "area", src, AREA, f"/{n}/{d.isoformat()}")
        calls += 1
        got = compact_rows(body) if body else []
        days = {(d + dt.timedelta(days=i)).isoformat() for i in range(n)}
        if got or src == "VIIRS_SNPP_SP":
            # remplace proprement les jours de la tranche
            for k in [k for k, r in rows.items() if r[2] in days]:
                del rows[k]
        for r in got:
            r = r[:6] + ["sp" if src.endswith("SP") else "nrt"]
            rows[tuple(r[:4])] = r
        if src.endswith("SP"):
            n_sp += len(got)
        else:
            n_nrt += len(got)
        d += dt.timedelta(days=n)
        time.sleep(1.5)
    out = sorted(rows.values(), key=lambda r: (r[2], r[3], r[0], r[1]))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lat", "lon", "date", "time", "frp", "type", "src"])
        w.writerows(out)
    print(f"API FIRMS {year} : {calls} appels depuis le {start} · SP jusqu'au {sp_end} (+{n_sp}) · NRT (+{n_nrt}) · "
          f"{len(out)} détections stockées")


def in_zone(lat, lon):
    return LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]


def compact_rows(raw):
    """Garde : lat, lon (3 décimales), date, heure, frp, type (absent en NRT)."""
    out = []
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))):
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, ValueError):
            continue
        if not in_zone(lat, lon):
            continue
        out.append([f"{lat:.3f}", f"{lon:.3f}", r["acq_date"], r["acq_time"].zfill(4), r.get("frp") or "0",
                    r.get("type", "")])
    return out


def write(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lat", "lon", "date", "time", "frp", "type"])
        w.writerows(rows)


def main():
    raw_dir = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "raw"
    arg = lambda name: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None  # noqa: E731
    data_dir = arg("--data") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(raw_dir, exist_ok=True)
    this_year = dt.date.today().year
    for y in range(FIRST_YEAR, this_year + 1):
        for c in COUNTRIES:
            path = os.path.join(raw_dir, f"{c}_{y}.csv")
            if os.path.exists(path) and (y < this_year - 1 or time.time() - os.path.getmtime(path) < 30 * 86400):
                continue  # années closes : une fois ; année précédente et en cours : au plus tous les 30 jours
            data = get(ARCHIVE.format(y=y, c=c))
            if data is None:
                print(f"{c} {y} : pas d'archive publiée")
                continue
            rows = compact_rows(data)
            write(path, rows)
            print(f"{c} {y} : {len(rows)} détections dans la zone")
    if "--api" in sys.argv:
        api_fill(data_dir, arg("--since"))
    if "--nrt" in sys.argv:
        path = os.path.join(raw_dir, "nrt.csv")
        have, rows = set(), []
        if os.path.exists(path):
            with open(path) as fh:
                rows = [r for r in csv.reader(fh)][1:]
            have = {tuple(r[:4]) for r in rows}
        added = 0
        for p, n in NRT_PRODUCTS:
            for region in ("Europe", "Russia_Asia"):
                data = get(NRT.format(p=p, n=n, r=region), timeout=300)
                if not data:
                    continue
                for r in compact_rows(data):
                    k = tuple(r[:4])
                    if k not in have:
                        have.add(k)
                        rows.append(r)
                        added += 1
        write(path, rows)
        print(f"NRT : +{added} détections, {len(rows)} au total")


if __name__ == "__main__":
    main()
