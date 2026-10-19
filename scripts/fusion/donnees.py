"""Données du moteur, lues dans les fichiers PUBLIÉS (`site/`) et versionnés (`data/`), à la résolution la plus fine
disponible, sur la zone Z du socle (mois, mailles de Z).

Chaque chargeur renvoie (A, cover) : A tableau (NM, nZ) float32 de comptes ou de valeurs BRUTES (le modèle
d'observation s'en charge), cover booléen (NM) des mois où la source existe. Agrégats seulement (maille de 2 km au plus
fin, mois), aucun texte.

  feux        détections FIRMS en excès (site/data.js, points à 0,01°), comptes par maille
  ucdp        UCDP GED, tous événements (site/osint.js, cases de 0,1° réparties sur leurs mailles)
  pertes      WarSpotting (site/osint.js, cases de 0,1° réparties)
  em_assauts  état-major UA, assauts par localité (data/officiels_cells_month.csv), par jour couvert
  em_frappes  état-major UA, frappes aériennes par localité
  modru       MoD RF, localités des bilans
  artillerie  menaces d'artillerie, heures par maille (data/alertes_artillerie.csv)
  kab         alertes KAB (data/alertes_kab.csv)
  liste       liste officielle rejouée (data/liste_hostilites_hromada_month.csv + data/hromada_cells.json ; σ = 1,5)
  radar_bati  radar S1, bâti modifié (data/sar_cells.csv, ≤ 15 km de la ligne du mois, mailles bâties ≥ 5 %, σ = 2)
  tg_<type>   Telegram (data/telegram_cells.csv, pas mensuel) : att_ua, att_ru, pres_ua, pres_ru
  asym_a/z    « combats derrière la ligne » (formules de `sectors.Sectors.asym`, recopiées : a et z relatif)
"""
import csv
import json
import os

import numpy as np

from .commun import DATA, DGRID, S, SITE

NM, nZ = S.NM, S.nZ
_MEM = {}


def _site(name):
    s = open(os.path.join(SITE, name), encoding="utf-8").read()
    return json.loads(s[s.index("=") + 1:].strip().rstrip(";"))


def _cells_to_z(cells):
    u = S.u_of_cell(cells)
    z = np.full(len(u), -1, np.int64)
    ok = u >= 0
    z[ok] = S.zpos[u[ok]]
    return z


def _add(A, t, z, w):
    ok = (z >= 0) & (t >= 0) & (t < NM)
    np.add.at(A, (t[ok], z[ok]), w[ok])


def _points(lon, lat, t, w):
    A = np.zeros((NM, nZ), np.float32)
    _add(A, np.asarray(t, np.int64), _cells_to_z(S.cell_of(lon, lat)), np.asarray(w, np.float32))
    return A


def _boxes(lat10, lon10, t, w, n=5):
    """Cases de 0,1° (indices lat·10, lon·10) : le poids est réparti sur n × n points de la case."""
    A = np.zeros((NM, nZ), np.float32)
    off = (np.arange(n) + 0.5) / n
    for a in off:
        for b in off:
            _add(A, np.asarray(t, np.int64), _cells_to_z(S.cell_of((lon10 + b) * 0.1, (lat10 + a) * 0.1)),
                 np.asarray(w, np.float32) / (n * n))
    return A


def _cover(A):
    on = np.flatnonzero(A.sum(1) > 0)
    if not len(on):
        return np.zeros(NM, bool)
    return np.array([on[0] <= t <= on[-1] and t in S.built for t in range(NM)])


def _month_map(months):
    return np.array([S.midx.get(m, -1) for m in months], np.int64)


def load_feux():
    D = _site("data.js")
    mm = _month_map(D["months"])
    p = np.array(D["pts"], np.float64).reshape(-1, 5)
    A = _points(p[:, 1] / 100, p[:, 0] / 100, mm[p[:, 2].astype(int)], np.ones(len(p)))
    return A, _cover(A)


def _osint():
    if "osint" not in _MEM:
        _MEM["osint"] = _site("osint.js")
    return _MEM["osint"]


def load_ucdp():
    p = np.array(_osint()["ucdp"]["pts"], np.float64).reshape(-1, 5)
    A = _boxes(p[:, 0], p[:, 1], p[:, 2].astype(int), p[:, 3])
    return A, _cover(A)


def load_pertes():
    p = np.array(_osint()["ws"]["pts"], np.float64).reshape(-1, 5)
    A = _boxes(p[:, 0], p[:, 1], p[:, 2].astype(int), p[:, 4])
    return A, _cover(A)


def _read_cells(path, col, filt=None, cell="maille", month="mois"):
    A = np.zeros((NM, nZ), np.float32)
    ms = set()
    ts, cs, ws = [], [], []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if filt and not filt(r):
            continue
        t = S.midx.get(r[month][:7])
        if t is None:
            continue
        ms.add(t)
        ts.append(t); cs.append(int(r[cell])); ws.append(float(r[col] or 0))
    _add(A, np.array(ts, np.int64), _cells_to_z(np.array(cs, np.int64)), np.array(ws, np.float32))
    cover = np.array([t in ms and t in S.built for t in range(NM)])
    return A, cover


def load_officiel(src):
    return _read_cells(os.path.join(DATA, "officiels_cells_month.csv"), "par_jour", lambda r: r["source"] == src)


def load_artillerie():
    return _read_cells(os.path.join(DATA, "alertes_artillerie.csv"), "h_artillerie")


def load_kab():
    return _read_cells(os.path.join(DATA, "alertes_kab.csv"), "n_kab")


def load_liste():
    """Part des localités de la hromada en hostilités actives connue à la fin du mois (versions publiées avant t),
    lissée σ = 1,5 maille ; avant la première version : 0 ; après la dernière : reconduite."""
    Hh = json.load(open(os.path.join(DATA, "hromada_cells.json"), encoding="utf-8"))
    cells = {k: S.pos[np.array(v, np.int64)] for k, v in Hh["mailles"].items()}
    cells = {k: v[v >= 0] for k, v in cells.items()}
    L = np.zeros((NM, S.nU), np.float32)
    for r in csv.DictReader(open(os.path.join(DATA, "liste_hostilites_hromada_month.csv"), encoding="utf-8")):
        t = S.midx.get(r["mois"])
        if t is not None and len(cells.get(r["pcode"], [])):
            L[t, cells[r["pcode"]]] = float(r["part_active_connue"])
    last = S.midx.get(Hh["dernier_mois"], NM - 1)
    for t in range(last + 1, NM):
        L[t] = L[last]
    A = np.stack([S.smooth(L[t], 1.5)[S.Zi] for t in range(NM)]).astype(np.float32)
    return A, np.array([t in S.built for t in range(NM)])


def load_radar_bati():
    A = np.full((NM, S.nU), np.nan, np.float32)
    BS = np.zeros(S.nU, np.float32)
    for r in csv.DictReader(open(os.path.join(DATA, "sar_cells.csv"), encoding="utf-8")):
        t, c = S.midx.get(r["month"]), int(r["cell"])
        if t is None or not (0 <= c < S.NX * S.NY):
            continue
        u = S.pos[c]
        if u < 0:
            continue
        if r["built_damage_since_2021"] != "":
            A[t, u] = float(r["built_damage_since_2021"])
        if r.get("built_share"):
            BS[u] = max(BS[u], float(r["built_share"]))
    for t in range(NM):   # mailles à ≤ 15 km de la ligne des cartes du mois seulement
        sd = np.full(S.nU, 999.0, np.float32)
        sd[S.Zi] = np.nan_to_num(np.abs(S.SDe[t]), nan=999)
        A[t, sd > 15.0] = np.nan
    n = np.isfinite(A).sum(1)
    A = np.where(BS[None, :] >= 0.05, np.nan_to_num(A), 0)
    X = np.stack([S.smooth(A[t], 2.0)[S.Zi] for t in range(NM)]).astype(np.float32)
    return np.maximum(X, 0), (n >= 200) & np.array([t in S.built for t in range(NM)])


def load_tg(kind):
    A, c = _read_cells(os.path.join(DATA, "telegram_cells.csv"), kind, lambda r: r["pas"] == "mois", month="debut")
    on = np.flatnonzero(c)
    cover = np.array([len(on) > 0 and on[0] <= t <= on[-1] and t in S.built for t in range(NM)])
    return A, cover


LOADERS = {
    "feux": load_feux, "ucdp": load_ucdp, "pertes": load_pertes,
    "em_assauts": lambda: load_officiel("etatmajor_assauts"), "em_frappes": lambda: load_officiel("etatmajor_frappes"),
    "modru": lambda: load_officiel("modru_localites"), "artillerie": load_artillerie, "kab": load_kab,
    "liste": load_liste, "radar_bati": load_radar_bati,
    "tg_att_ua": lambda: load_tg("att_ua"), "tg_att_ru": lambda: load_tg("att_ru"),
    "tg_pres_ua": lambda: load_tg("pres_ua"), "tg_pres_ru": lambda: load_tg("pres_ru"),
}


def brut(name):
    if name not in _MEM:
        _MEM[name] = LOADERS[name]()
    return _MEM[name]


# ---------------------------------------------------------------------------------------------------------------------
# asymétrie des combats (formules de scripts/sectors.py, Sectors.asym, recopiées pour ne pas dépendre de son évolution)
# ---------------------------------------------------------------------------------------------------------------------
def asymetrie():
    """(a, z) sur Z par mois : a = (A_russe − A_ukrainien) / (A_russe + A_ukrainien), fenêtre gaussienne de 12 km à
    ≤ 10 km de la ligne des cartes (feux, UCDP, pertes normalisés) ; z relatif aux 12 mois précédents (au moins 4)."""
    if "asym" in _MEM:
        return _MEM["asym"]
    D = _site("data.js")
    O = _osint()
    mm = _month_map(D["months"])
    R = np.zeros((3, NM, S.nU), np.float32)

    def add(n, t, lon, lat, w):
        u = S.u_of_cell(S.cell_of(lon, lat))
        t = np.asarray(t, np.int64)
        ok = (u >= 0) & (t >= 0) & (t < NM)
        np.add.at(R[n], (t[ok], u[ok]), np.asarray(w, np.float32)[ok])
    p = np.array(D["pts"], np.float64).reshape(-1, 5)
    add(0, mm[p[:, 2].astype(int)], p[:, 1] / 100, p[:, 0] / 100, np.ones(len(p)))
    for n, key, wi in ((1, "ucdp", 3), (2, "ws", 4)):
        q = np.array(O[key]["pts"], np.float64).reshape(-1, 5)
        add(n, q[:, 2].astype(int), (q[:, 1] + 0.5) * 0.1, (q[:, 0] + 0.5) * 0.1, q[:, wi])
        last = int(q[:, 2].max()) if len(q) else -1
        for t in range(last + 1, NM):   # fin de série : dernier mois reconduit
            if last >= 0:
                R[n, t] = R[n, last]
    SM = np.stack([np.stack([S.smooth(R[n, t], 2.5) for t in range(NM)]) for n in range(3)])
    Hs = [float(np.percentile(SM[n][SM[n] > 0], 90)) if (SM[n] > 0).any() else 1.0 for n in range(3)]
    aw_all, az_all = np.zeros((NM, nZ), np.float32), np.zeros((NM, nZ), np.float32)
    hist = {}
    for t in S.built:
        sd = S.full(np.nan_to_num(S.SDe[t], nan=999), fill=999)
        A = np.mean(np.stack([1 - np.exp(-SM[n][t] / max(Hs[n], 1e-6)) for n in range(3)]), axis=0)
        near = np.abs(sd) <= 10
        ru_m, ua_m = (near & (sd > 0)).astype(np.float32), (near & (sd <= 0)).astype(np.float32)
        n_ru, n_ua = S.smooth(ru_m, 6.0), S.smooth(ua_m, 6.0)
        m_ru = S.smooth(A * ru_m, 6.0) / np.maximum(n_ru, 1e-6)
        m_ua = S.smooth(A * ua_m, 6.0) / np.maximum(n_ua, 1e-6)
        tot = m_ru + m_ua
        aw = np.where((tot >= 0.05) & (n_ru > 0.05) & (n_ua > 0.05), (m_ru - m_ua) / np.maximum(tot, 1e-6), 0.0).astype(np.float32)
        hist[t] = aw
        past = [hist[q] for q in range(t - 12, t) if q in hist]
        if len(past) >= 4:
            P_ = np.stack(past)
            z = (aw - P_.mean(0)) / np.maximum(P_.std(0), 0.2)
        else:
            z = np.zeros_like(aw)
        aw_all[t], az_all[t] = aw[S.Zi], np.clip(z, -5, 5)[S.Zi]
    _MEM["asym"] = (aw_all, az_all)
    return _MEM["asym"]


# ---------------------------------------------------------------------------------------------------------------------
# géométrie : distances à la ligne du CONSENSUS (vérité), décalage vrai par tronçon, mouvement des cartes
# ---------------------------------------------------------------------------------------------------------------------
def sd_truth():
    """SDT[t] (sur Z) : distance signée à la ligne du consensus des cartes du mois t (mailles inconnues : champ des
    cartes du même mois ; bande russe : champ des cartes, à défaut russe). Sert seulement à APPRENDRE sur des mois
    passés (vérité de q + 2)."""
    if "sdt" in _MEM:
        return _MEM["sdt"]
    SDT = np.full((NM, nZ), np.nan, np.float32)
    isUAz = S.isUA[S.Zi]
    for t in S.built:
        v = np.where(S.T[t] == 1, 1.0, np.where(S.T[t] == 0, 0.0, np.nan_to_num(S.Peq[t], nan=0.5)))
        v = np.where(isUAz, v, np.where(np.isfinite(S.Peq[t]), S.Peq[t], 1.0))
        SDT[t] = S.sd_of(S.full(v.astype(np.float32), fill=np.nan))[S.Zi]
    _MEM["sdt"] = SDT
    return SDT


def _best_shift(d, ru):
    """Décalage δ de DGRID qui reproduit le mieux l'état `ru` (booléen) des mailles de distance signée d :
    erreurs de classement minimales, égalité : le plus petit |δ|."""
    x = d[:, None] + DGRID[None, :]
    miss = (ru[:, None] & (x <= 0)).sum(0) + (~ru[:, None] & (x > 0)).sum(0) + 1e-3 * np.abs(DGRID)
    return float(DGRID[int(np.argmin(miss))])


def delta_true():
    """δ vrai par (t, k) : décalage de la ligne des cartes du mois t qui reproduit le mieux le consensus à t+2 sur les
    mailles connues du tronçon à ≤ 30 km. Renvoie (d2, d1) (d1 : consensus à t+1)."""
    if "dtrue" in _MEM:
        return _MEM["dtrue"]
    out = {1: {}, 2: {}}
    for t in S.built:
        sd = np.nan_to_num(S.SDe[t], nan=999)
        ks = [k for (q, k) in S.Y if q == t]
        for h in (1, 2):
            if t + h not in S.built:
                continue
            y = S.T[t + h]
            for k in ks:
                z = S.tile_cells[k]
                m = (np.abs(sd[z]) <= 30) & (y[z] >= 0)
                if m.sum() < 10:
                    continue
                out[h][(t, k)] = _best_shift(sd[z][m], y[z][m] == 1)
    _MEM["dtrue"] = (out[2], out[1])
    return _MEM["dtrue"]


def map_shift():
    """Variation de la surface russe des cartes (champ équilibré > 0,5) du tronçon entre t−1 et t, en km de
    profondeur (surface / 24 km) : {(t, k): ΔY}."""
    out = {}
    for t in S.built:
        if t - 1 not in S.built:
            continue
        a = np.bincount(S.tinv, weights=(np.nan_to_num(S.Peq[t]) > 0.5), minlength=S.nT)
        b = np.bincount(S.tinv, weights=(np.nan_to_num(S.Peq[t - 1]) > 0.5), minlength=S.nT)
        for k in [k for (q, k) in S.Y if q == t]:
            out[(t, k)] = float(a[k] - b[k]) * S.km ** 2 / (12 * S.km)
    return out
