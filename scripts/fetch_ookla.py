"""Ookla Open Data (Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps, CC BY-NC-SA 4.0) : nombre de
tests de débit par bloc de 10 km et par trimestre, mobile et fixe, sur la zone lon 30-41°, lat 46-52°.

    python3 scripts/fetch_ookla.py                 # ajoute les trimestres publiés absents de data/ookla_blocs_trimestre.csv
    python3 scripts/fetch_ookla.py --seed <csv>    # amorçage depuis ukraine-recherche/signals4/ookla/ookla_blocks_quarter.csv

Source : bucket public s3://ookla-open-data (AWS Open Data, us-west-2), lecture ANONYME (aucune clé), fichiers
Parquet trimestriels triés par quadkey : seuls les groupes de lignes qui recoupent la zone sont lus (1 ou 2 sur 4 à 13),
puis les tuiles (~600 m) sont filtrées par leur centre et SOMMÉES par bloc de 10 km (5 × 5 mailles de la grille du
site). Aucune tuile n'est écrite : seuls les agrégats par bloc sortent. Dépendance : pyarrow (S3 anonyme).

Sortie (versionnée, CC BY-NC-SA 4.0, mention en tête du fichier) : data/ookla_blocs_trimestre.csv
  bloc, trimestre (AAAAQq), tests_mobile, tests_fixe, temoin (1 = bloc de l'ouest de la zone, lon < 33,5°, lat > 47,2°,
  sous contrôle ukrainien toute la période : sert à retirer la tendance générale du volume Speedtest).
Usage dans le modèle : sectors.ookla() → variable de P(mouvement) (baisse des tests par rapport à 2021-Q4).
Un trimestre est publié environ 10 jours après sa fin (2026-Q3 : le 9/10/2026) ; un trimestre absent du bucket est
simplement sauté (nouvel essai au passage suivant).
"""
import csv
import datetime as dt
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "ookla_blocs_trimestre.csv")
LICENCE = ("# Speedtest® by Ookla® Global Fixed and Mobile Network Performance Maps (AWS Open Data, s3://ookla-open-data), "
           "https://github.com/teamookla/ookla-open-data ; agrégats par bloc de 10 km × trimestre dérivés par ukraine-feux ; "
           "licence CC BY-NC-SA 4.0 (https://creativecommons.org/licenses/by-nc-sa/4.0/). Ookla trademarks used under license "
           "and reprinted with permission.")
LON0, LON1, LAT0, LAT1 = 30.0, 41.0, 46.0, 52.0
Z, ZP, B = 16, 7, 5
FIRST = (2021, 4)


def grid():
    s = open(os.path.join(ROOT, "site", "front.js"), encoding="utf-8").read()
    G = json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))["grid"]
    R = G["R"] if isinstance(G.get("R"), (int, float)) else 6371007.2
    return G["nx"], G["ny"], G["size"], G["x0"], G["y1"], G["lon0"], G["lat0"], R


NX, NY, SIZE, X0, Y1, GLON0, GLAT0, RT = grid()


def block_of(lon, lat):
    l, p, l0, p0 = map(math.radians, (lon, lat, GLON0, GLAT0))
    k = math.sqrt(2 / (1 + math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0)))
    x, y = RT * k * math.cos(p) * math.sin(l - l0), RT * k * (math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0))
    i, j = int((x - X0) // SIZE), int((Y1 - y) // SIZE)
    return (j // B) * (NX // B) + i // B if 0 <= i < NX and 0 <= j < NY else -1


def tile_xy(lon, lat, z):
    n = 2 ** z
    s = math.sin(math.radians(lat))
    return int((lon + 180) / 360 * n), int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)


def qk(x, y, z):
    return "".join(str((1 if x & (1 << (i - 1)) else 0) + (2 if y & (1 << (i - 1)) else 0)) for i in range(z, 0, -1))


def qk_center(q):
    x = y = 0
    z = len(q)
    for i, ch in enumerate(q):
        m, d = 1 << (z - i - 1), int(ch)
        x |= m if d & 1 else 0
        y |= m if d & 2 else 0
    n = 2 ** z
    return (x + 0.5) / n * 360 - 180, math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))


def quarters_until(today):
    y, q = FIRST
    out = []
    while (y, q) < ((today.year, (today.month - 1) // 3 + 1)):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch_quarter(typ, y, q):
    """{bloc: tests} pour un type et un trimestre ; None si le fichier n'est pas (encore) publié."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from pyarrow import fs
    s3 = fs.S3FileSystem(anonymous=True, region="us-west-2")
    xa, ya = tile_xy(LON0, LAT1, ZP)
    xb, yb = tile_xy(LON1, LAT0, ZP)
    pref = sorted(qk(x, yy, ZP) for x in range(xa, xb + 1) for yy in range(ya, yb + 1))
    qmin, qmax = pref[0] + "0" * (Z - ZP), pref[-1] + "3" * (Z - ZP)
    key = f"ookla-open-data/parquet/performance/type={typ}/year={y}/quarter={q}/{y}-{3 * (q - 1) + 1:02d}-01_performance_{typ}_tiles.parquet"
    try:
        f = pq.ParquetFile(key, filesystem=s3)
    except (OSError, FileNotFoundError) as e:
        print(f"  {typ} {y}Q{q} : non disponible ({type(e).__name__})", flush=True)
        return None
    md, qi = f.metadata, f.schema_arrow.names.index("quadkey")
    rgs = [i for i in range(md.num_row_groups)
           if (st := md.row_group(i).column(qi).statistics) is None or not st.has_min_max or not (st.max < qmin or st.min > qmax)]
    t = f.read_row_groups(rgs, columns=["quadkey", "tests"])
    t = t.filter(pc.and_(pc.greater_equal(t["quadkey"], qmin), pc.less_equal(t["quadkey"], qmax)))
    d = t.to_pydict()
    out, wit = {}, set()
    for qkey, n in zip(d["quadkey"], d["tests"]):
        lon, lat = qk_center(qkey)
        if not (LON0 <= lon <= LON1 and LAT0 <= lat <= LAT1):
            continue
        b = block_of(lon, lat)
        if b < 0:
            continue
        out[b] = out.get(b, 0) + int(n)
        if lon < 33.5 and lat > 47.2:
            wit.add(b)
    return out, wit


def read_existing():
    rows = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(line for line in f if not line.startswith("#")):
                rows[(int(r["bloc"]), r["trimestre"])] = [int(r["tests_mobile"]), int(r["tests_fixe"]), int(r["temoin"])]
    return rows


def write(rows):
    tmp = OUT + ".part"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.write(LICENCE + "\n")
        w = csv.writer(f)
        w.writerow(["bloc", "trimestre", "tests_mobile", "tests_fixe", "temoin"])
        for (b, qq), v in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            w.writerow([b, qq] + v)
    os.replace(tmp, OUT)


def seed(path):
    """Amorçage depuis les agrégats de recherche (mêmes blocs, même zone), sans retélécharger."""
    rows = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            k = (int(r["bloc"]), r["trimestre"])
            v = rows.setdefault(k, [0, 0, 0])
            v[0 if r["type"] == "mobile" else 1] += int(r["tests"])
    return rows


def main():
    if "--seed" in sys.argv:
        rows = seed(sys.argv[sys.argv.index("--seed") + 1])
        wit_src = os.path.join(os.path.dirname(sys.argv[sys.argv.index("--seed") + 1]), "raw")
        wit = set()
        for fn in sorted(os.listdir(wit_src)) if os.path.isdir(wit_src) else []:
            with open(os.path.join(wit_src, fn), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    lon, lat = float(r["lon"]), float(r["lat"])
                    if lon < 33.5 and lat > 47.2:
                        wit.add(block_of(lon, lat))
        for k, v in rows.items():
            v[2] = 1 if k[0] in wit else 0
        write(rows)
        print(f"amorçage : {len(rows)} blocs-trimestres, {len(wit)} blocs témoins → {os.path.relpath(OUT, ROOT)}")
        return
    rows = read_existing()
    have = {qq for _, qq in rows}
    wit = {b for (b, _), v in rows.items() if v[2]}
    added = 0
    for y, q in quarters_until(dt.date.today()):
        qq = f"{y}Q{q}"
        if qq in have:
            continue
        got = {}
        for i, typ in enumerate(("mobile", "fixed")):
            r = fetch_quarter(typ, y, q)
            if r is None:
                got = None
                break
            for b, n in r[0].items():
                got.setdefault(b, [0, 0, 0])[i] += n
            wit |= r[1]
        if not got:
            continue
        for b, v in got.items():
            rows[(b, qq)] = v
        added += 1
        print(f"  {qq} : {len(got)} blocs, {sum(v[0] for v in got.values()):,} tests mobiles, {sum(v[1] for v in got.values()):,} fixes", flush=True)
    for k, v in rows.items():
        v[2] = 1 if k[0] in wit else 0
    if added or not os.path.exists(OUT):
        write(rows)
    print(f"Ookla : {added} trimestre(s) ajouté(s) ; {len({qq for _, qq in rows})} trimestres en tout")


if __name__ == "__main__":
    main()
