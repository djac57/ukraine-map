"""Agrégats QUOTIDIENS versionnés par source (data/daily/), lus par les moteurs sur l'axe de temps quotidien (20/10/2026).

Un fichier par source et par année : data/daily/<source>_<année>.csv.gz (gzip déterministe, mtime 0, pour que git ne
voie un changement que si les comptes changent), trié par date puis unité ; en tête : date, [date_pub,] maille, i, j,
camp_source, puis les comptes de la source. Un fichier de couverture par source : data/daily/<source>_couverture.csv
(date, lu 0/1, n_messages, …) : un jour non lu ne vaut PAS zéro.
La CI ne recalcule que les jours récents (remplace_jours) et ne réécrit que les années touchées.
Aucun texte, aucun nom de canal, aucune unité ni personne : des comptes par maille de 2 km (ou tronçon de 24 km).
"""
import csv
import glob
import gzip
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "daily")


def path(source, year):
    return os.path.join(DIR, f"{source}_{year}.csv.gz")


def years(source):
    out = []
    for p in glob.glob(os.path.join(DIR, f"{source}_*.csv.gz")):
        y = os.path.basename(p)[len(source) + 1:-7]
        if y.isdigit():
            out.append(int(y))
    return sorted(out)


def read(source, year=None):
    """(en-tête, lignes) ; lignes = listes de chaînes. Toutes les années si year est None."""
    header, rows = None, []
    for y in ([year] if year else years(source)):
        p = path(source, y)
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt", encoding="utf-8", newline="") as fh:
            r = csv.reader(fh)
            h = next(r)
            header = header or h
            rows.extend(r)
    return header, rows


def _write_gz(p, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    data = buf.getvalue().encode("utf-8")
    tmp = p + ".tmp"
    with open(tmp, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as gz:
            gz.write(data)
    os.replace(tmp, p)


def write(source, header, rows, only_years=None, nkey=2):
    """Écrit une ligne par (date, unité…) ; rows = listes (date en premier) ; tri par les nkey premières colonnes
    (date, unité numérique). Seules les années de only_years sont réécrites (toutes si None). Renvoie {année: lignes}."""
    os.makedirs(DIR, exist_ok=True)
    by = {}
    for r in rows:
        by.setdefault(int(str(r[0])[:4]), []).append([str(x) for x in r])
    done = {}
    for y, rs in sorted(by.items()):
        if only_years is not None and y not in only_years:
            continue
        rs.sort(key=lambda r: tuple(int(x) if x.lstrip("-").isdigit() else x for x in r[:nkey]))
        _write_gz(path(source, y), header, rs)
        done[y] = len(rs)
    return done


def read_cov(source):
    p = os.path.join(DIR, f"{source}_couverture.csv")
    if not os.path.exists(p):
        return None, {}
    with open(p, encoding="utf-8", newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        return h, {row[0]: row for row in r}


def write_cov(source, header, rows_by_date):
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, f"{source}_couverture.csv")
    with open(p + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for d in sorted(rows_by_date):
            w.writerow([str(x) for x in rows_by_date[d]])
    os.replace(p + ".tmp", p)


def replace_days(source, header, new_rows, days, nkey=2):
    """Remplace, dans les fichiers publiés, toutes les lignes des jours `days` (ensemble de dates AAAA-MM-JJ) par
    new_rows ; ne réécrit que les années touchées. Renvoie {année: lignes}."""
    ys = {int(d[:4]) for d in days}
    rows = []
    for y in ys:
        h, old = read(source, y)
        rows.extend(r for r in old if r[0] not in days)
    rows.extend(r for r in new_rows if r[0] in days)
    return write(source, header, rows, only_years=ys, nkey=nkey)
