"""KPI « AVANCE SUR LES CARTES » (« qui avait raison ») : indicateur INTERNE, rejouable, lancé en local.

Décision de l'utilisateur du 19/10/2026 : remplacer le KPI « information hors cartes » (écart aux cartes, que le
placebo fait monter à 7,9 %) par une mesure de JUSTESSE : à la date t, qui était le plus près de ce que les cartes
ont montré ensuite ? On l'applique à notre champ et à chaque source.

VÉRITÉ RETARDÉE : consensus des cartes (majorité DeepState corrigé par son journal daté, ISW prolongée après son arrêt,
Wikipédia, par localité VIINA ; égalité ou contesté : inconnu) à la date t + h, h = 1 mois (≈ délai de report des
cartes : délai médian de confirmation 19 jours) ou 2 mois (on prévoit en plus un mois de mouvement réel).
Pas de calcul : la décade (10, 20, fin de mois). Mois = fins de mois (instantanés du site).

COMPARÉS à la date t (aucune information postérieure à t ; les recalages sont appris sur les seules dates q telles
que q + h ≤ t) :
  - cartes brutes du jour : DeepState (colonne VIINA corrigée par les déclarations datées du journal, comme en
    production), ISW (jusqu'à son arrêt), Wikipédia, consensus du jour ; en dur (1 / 0 / 0,5) et RECALÉES
    (p = σ(a + b·x + c·x⁺), x = distance signée à la ligne de la carte, a, b, c appris en walk-forward) ;
  - persistance : consensus un pas plus tôt (un mois au pas mensuel, une décade au pas décadaire), recalé ;
  - nos champs publiés : « pool » (vue équilibrée, cartes et annonces, `front_equilibre.js`), « nowcast » (champ
    publié, avec les signaux de combat, `front_nowcast.js`), vues par camp (ua, ru, tiers), variantes « sans X »
    (ligne seulement : elles n'ont pas la bande locale) ; au pas décadaire, le dernier instantané mensuel ≤ t
    (ce que montre le site aujourd'hui) ;
  - « données seules » (ligne v4 de recherche : consensus retardé de 3 mois + bascules apprises sur les signaux),
    si le cache de recherche existe ;
  - placebos : (P1) consensus recalé + bruit aléatoire par tronçon de 24 km, de même ampleur que l'écart de notre
    champ au consensus recalé ; (P2) notre nowcast dont l'écart au pool est appliqué dans un sens tiré au hasard
    par tronçon et par mois.

MESURES : Brier et log-loss (p bornée à [0,01 ; 0,99]) sur les mailles ukrainiennes à ≤ 30 km de la ligne du
consensus du jour ou de la ligne vraie (D30), sur les mailles qui changent de camp entre t et t + h (consensus du
jour connu, vérité différente) et sur les mailles stables ; distance moyenne symétrique (km, plafonnée à 30 km)
entre la ligne 50 % et la ligne vraie (lignes intérieures à l'Ukraine) ; par date, par secteur (tuile de 24 km et
grand secteur nommé).

KPI PRINCIPAL : avance sur les cartes = 1 − Brier(nowcast publié) / Brier(consensus du jour recalé), sur D30,
pas mensuel, h = 1 mois, avec intervalle bootstrap par blocs de 3 mois ; en km : 1 − km(nous) / km(consensus).
Détection : délai (jours) entre le moment où une source passe du nouveau côté et celui où le consensus bascule
(pas décadaire), et taux de fausses détections (la source s'écarte du consensus, qui ne la rejoint pas en 2 mois).

    python3 scripts/kpi_avance.py              (≈ 5-10 min ; cache des états VIINA dans <sortie>/cache)
    KPI_REBUILD=1 python3 scripts/kpi_avance.py   (relit VIINA)
Variables : KPI_RAW (dossier VIINA, défaut ukraine-recherche/front/raw), KPI_OUT (défaut
ukraine-recherche/analyses/kpi_avance), KPI_SEEDS (placebos, défaut 20), KPI_BOOT (tirages, défaut 2000),
KPI_CHAMP (champ évalué comme « nous : champ publié », défaut nowcast ; fusion = site/front_fusion.js),
KPI_CAD (pas : mois,decade ; « mois » seul pour un passage rapide).
Écrit : resultats.json, resultats.md (tableaux), mensuel.csv, secteurs.csv, tuiles.csv, detection.csv.
"""
import contextlib
import csv
import datetime as dt
import io
import json
import math
import os
import sys
import time

import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
RECH = "/Users/douglas.comin/Desktop/perso/ukraine-recherche"
RAW = os.environ.get("KPI_RAW", os.path.join(RECH, "front", "raw"))
OUT = os.environ.get("KPI_OUT", os.path.join(RECH, "analyses", "kpi_avance"))
CACHE = os.path.join(OUT, "cache")
NOWCAST_CACHE = os.path.join(RECH, "terrain", "base_nowcast", "data", "cache.npz")
SEEDS = int(os.environ.get("KPI_SEEDS", "20"))
SEEDS_STACK = int(os.environ.get("KPI_SEEDS_EMPILE", "5"))
CADENCES = os.environ.get("KPI_CAD", "mois,decade").split(",")
STACK = [("nowcast", "nous : champ publié (nowcast)"), ("pool", "nous : pool (cartes + annonces)"), ("dsm", "DeepState (journal daté)"),
         ("isw", "ISW"), ("wiki", "Wikipédia"), ("pers", "persistance (consensus t−1)"), ("dataonly", "données seules (v4)"),
         ("placebo_bruit", "PLACEBO bruit pur"), ("placebo_sens", "PLACEBO nowcast à sens tiré au hasard")]
NBOOT = int(os.environ.get("KPI_BOOT", "2000"))
# champ évalué à la place du nowcast publié (ex. KPI_CHAMP=fusion lit site/front_fusion.js ; libellés inchangés)
CHAMP = os.environ.get("KPI_CHAMP", "nowcast")
EVAL_FROM = "2023-01-01"
EPS = 0.01
CAP_KM = 30.0
D30 = 30.0
TILE = 12
sys.path.insert(0, HERE)
from map_dependence import decode_seq, load_meta, load_seq  # noqa: E402

# grands secteurs (repères), chaque tuile de 24 km va au repère le plus proche (≤ 150 km), sinon « autre »
SECTEURS = [("Kherson / Dnipro", 33.0, 46.75), ("Zaporijjia (Orikhiv)", 35.8, 47.5), ("Houliaïpole / Velyka Novosilka", 36.6, 47.8),
            ("Pokrovsk / Kourakhove", 37.2, 48.2), ("Kostiantynivka / Toretsk", 37.75, 48.5), ("Bakhmout / Siversk", 38.1, 48.85),
            ("Lyman / Kreminna", 37.95, 49.1), ("Koupiansk / Borova", 37.7, 49.65), ("Kharkiv nord / Vovtchansk", 36.9, 50.2),
            ("Soumy / frontière nord", 35.0, 51.1)]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def import_build_front():
    argv = sys.argv
    sys.argv = ["build_front.py", RAW]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import build_front as B
    finally:
        sys.argv = argv
    return B


# ---------------------------------------------------------------- site
class Site:
    def __init__(self):
        FR = load_meta(SITE)
        G = FR["grid"]
        self.NX, self.NY, self.km = G["nx"], G["ny"], G["size"] / 1000
        place = np.zeros(self.NX * self.NY, np.int32)
        b, o = __import__("base64").b64decode(FR["runs"]), 0
        for i in range(0, len(b), 3):
            v, n = b[i] | b[i + 1] << 8, b[i + 2]
            place[o:o + n] = -1 if v == 65535 else -2 if v == 65534 else v
            o += n
        self.U = np.flatnonzero(place != -1)
        self.cellU = place[self.U]
        self.nU = len(self.U)
        self.jj, self.ii = self.U // self.NX, self.U % self.NX
        self.isUA = self.cellU >= 0
        self.nPlaces = FR["nPlaces"]
        self.mdates = [(k, m["date"]) for k, m in enumerate(FR["months"]) if m and m.get("kind", "m") == "m" and len(m.get("date", "")) == 10]
        self.fields = {}
        for view in ("nowcast", "equilibre", "ua", "ru", "tiers", "sans_dsm", "sans_isw", "sans_wiki", "sans_enveloppes", "sans_medias",
                     "sans_modru", "sans_gsua", "sans_presence", "sans_indices"):
            fv = CHAMP if view == "nowcast" else view
            path = os.path.join(SITE, f"front_{fv}.js")
            if not os.path.exists(path):
                continue
            seq = decode_seq(load_seq(SITE, fv), self.nU)
            self.fields[view] = {d: (seq[k].astype(np.float32) / 63.0) for k, d in self.mdates if k < len(seq) and seq[k] is not None}
        self.generated = FR.get("generated")
        self.tile = (self.jj // TILE) * 1000 + self.ii // TILE
        self.inv_grid = None

    def grid(self, v, fill=np.nan):
        g = np.full((self.NY, self.NX), fill, np.float32)
        g[self.jj, self.ii] = v
        return g


# ---------------------------------------------------------------- états VIINA par décade
def decade_dates(last):
    out, y, m = [], 2022, 3
    while True:
        end = dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)
        for d in (dt.date(y, m, 10), dt.date(y, m, 20), end):
            if d <= last:
                out.append(d)
        if end >= last:
            return out
        y, m = y + m // 12, m % 12 + 1


def build_states(S):
    path = os.path.join(CACHE, "etats.npz")
    if os.path.exists(path) and os.environ.get("KPI_REBUILD") != "1":
        Z = np.load(path, allow_pickle=False)
        return [dt.date.fromisoformat(str(x)) for x in Z["dates"]], Z["codes"], json.loads(str(Z["meta"]))
    B = import_build_front()
    log("lecture des localités et de VIINA (≈ 2-3 min)…")
    gj = json.load(open(os.path.join(RAW, "gn_UA_tess.geojson")))
    places = []
    for f in gj["features"]:
        g, pr = f["geometry"], f["properties"]
        if not g:
            continue
        places.append({"id": str(int(pr["geonameid"])), "adm1": str(pr.get("admin1_code") or "").split(".")[0],
                       "adm1n": pr.get("ADM1_NAME") or "", "name": pr.get("name") or "",
                       "names": [pr.get("name") or "", pr.get("asciiname") or ""] + [a for a in str(pr.get("alternatenames") or "").split(",") if B.LATIN.match(a)],
                       "lat": float(pr["latitude"]), "lon": float(pr["longitude"])})
    del gj
    assert len(places) == S.nPlaces, "localités différentes du site"
    dates = decade_dates(dt.date.today())
    status, last = B.read_status(dates)
    dates = [d for d in dates if d.strftime("%Y%m%d") in status]
    with contextlib.redirect_stdout(io.StringIO()):
        ended = B.carry_forward(status)
    root = B.ROOT
    os.makedirs(os.path.join(CACHE, "dsm", "data"), exist_ok=True)
    B.ROOT = os.path.join(CACHE, "dsm")        # apply_dsm_log réécrit data/dsm_log.csv : jamais dans le dépôt
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            B.apply_dsm_log(status, places)
    finally:
        B.ROOT = root
    pid = [p["id"] for p in places]
    codes = np.zeros((len(dates), 3, len(places)), np.int8)
    for di, d in enumerate(dates):
        st = status[d.strftime("%Y%m%d")]
        for n, g in enumerate(pid):
            c = st.get(g)
            if c:
                codes[di, :, n] = c[:3]
    meta = {"viina_last": last, "ended": ended}
    os.makedirs(CACHE, exist_ok=True)
    np.savez_compressed(path, dates=np.array([d.isoformat() for d in dates]), codes=codes, meta=json.dumps(meta))
    log(f"{len(dates)} décades, {dates[0]} → {dates[-1]} ; VIINA jusqu'au {last} ; séries prolongées : {ended}")
    return dates, codes, meta


# ---------------------------------------------------------------- géométrie
class Geo:
    def __init__(self, S):
        self.S = S
        self.ins = S.grid(S.isUA.astype(np.float32), 0) > 0.5

    def sfield(self, val):
        """Carte dure (1 russe, 0 ukrainien, autre inconnu) → distance signée en km (positive côté russe)."""
        S = self.S
        ru = S.grid((val == 1).astype(np.float32), 0) > 0.5
        ua = S.grid((val == 0).astype(np.float32), 0) > 0.5
        if not ru.any() or not ua.any():
            return np.full(S.nU, np.nan, np.float32)
        dru = ndimage.distance_transform_edt(~ru) * S.km
        dua = ndimage.distance_transform_edt(~ua) * S.km
        return (dua - dru)[S.jj, S.ii].astype(np.float32)

    def edges(self, f):
        """Ligne d'un champ signé (f > 0 russe), entre deux mailles ukrainiennes (ligne de contact, pas la frontière)."""
        g = self.S.grid(f)
        ru = (g > 0) & self.ins
        ua = (g <= 0) & self.ins
        e = np.zeros_like(ru)
        for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            e |= ru & np.roll(ua, sh, axis=(0, 1))
        return e

    def dist_to(self, e):
        return ndimage.distance_transform_edt(~e) * self.S.km if e.any() else None


def lgt(p):
    p = np.clip(p, 0.5 / 63, 1 - 0.5 / 63)
    return np.log(p / (1 - p))


def sigm(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def feat(f):
    x = np.clip(np.nan_to_num(f, nan=0.0), -30, 30) / 10
    return np.stack([np.ones_like(x), x, np.maximum(x, 0)], 1)


def fit_logit(X, y, lam=1e-3, it=12):
    w = np.zeros(X.shape[1])
    for _ in range(it):
        p = sigm(X @ w)
        g = X.T @ (y - p) - lam * np.r_[0, w[1:]]
        H = (X * (p * (1 - p))[:, None]).T @ X + lam * np.eye(X.shape[1]) + 1e-6 * np.eye(X.shape[1])
        w += np.linalg.solve(H, g)
    return w


# ---------------------------------------------------------------- moteur
class Moteur:
    def __init__(self):
        log("lecture du site…")
        self.S = S = Site()
        self.G = Geo(S)
        self.dates, codes, self.meta = build_states(S)
        self.ND = len(self.dates)
        self.didx = {d: i for i, d in enumerate(self.dates)}
        ended = self.meta.get("ended", {})
        self.isw_end = dt.date.fromisoformat(f"{ended['isw'][:4]}-{ended['isw'][4:6]}-{ended['isw'][6:]}") if ended.get("isw") else None
        # cartes par maille : 1 russe, 0 ukrainien, 2 contesté, −1 inconnu
        cu = np.where(S.isUA, S.cellU, 0)
        self.maps = {}
        for n, s in enumerate(("dsm", "isw", "wiki")):
            c = codes[:, n, :][:, cu]
            v = np.where(c == 2, 1, np.where(c == 1, 0, np.where(c == 3, 2, -1))).astype(np.int8)
            v[:, ~S.isUA] = -1
            self.maps[s] = v
        ru = sum((self.maps[s] == 1).astype(np.int8) for s in self.maps)
        ua = sum((self.maps[s] == 0).astype(np.int8) for s in self.maps)
        self.cons = np.where(ru > ua, 1, np.where(ua > ru, 0, -1)).astype(np.int8)
        self.cons[:, ~S.isUA] = -1
        # vérités « sans la source » (les deux autres cartes d'accord)
        self.loo = {}
        for s in self.maps:
            o = [x for x in self.maps if x != s]
            a, b = self.maps[o[0]], self.maps[o[1]]
            self.loo[s] = np.where((a == b) & (a >= 0) & (a <= 1), a, -1).astype(np.int8)
        log("distances signées des cartes…")
        self.F = {k: np.zeros((self.ND, S.nU), np.float32) for k in ("cons", "dsm", "isw", "wiki")}
        for d in range(self.ND):
            self.F["cons"][d] = self.G.sfield(self.cons[d])
            for s in self.maps:
                self.F[s][d] = self.G.sfield(self.maps[s][d])
        self.zone = S.isUA & (np.nan_to_num(np.abs(self.F["cons"]), nan=999) <= 40).any(0)
        self.Zc = np.flatnonzero(self.zone)
        # repère du dernier instantané mensuel publié ≤ d
        snaps = sorted(dt.date.fromisoformat(x) for x in S.fields["nowcast"])
        self.snap_of = []
        for d in self.dates:
            ok = [x for x in snaps if x <= d]
            self.snap_of.append(ok[-1].isoformat() if ok else None)
        self.month_end = np.array([(d + dt.timedelta(days=1)).day == 1 for d in self.dates])
        # secteurs
        B = import_build_front()
        self.sector_of_tile = {}
        for k in np.unique(S.tile[self.zone]):
            j, i = (k // 1000 + 0.5) * TILE, (k % 1000 + 0.5) * TILE
            lon, lat = B.inv_laea(B.X0 + i * B.SIZE, B.Y1 - j * B.SIZE)
            best, bd = "autre", 150.0
            for nm, lo, la in SECTEURS:
                dd = math.hypot((lon - lo) * 111.3 * math.cos(math.radians(lat)), (lat - la) * 111.3)
                if dd < bd:
                    best, bd = nm, dd
            self.sector_of_tile[int(k)] = best
        self.snames = [s[0] for s in SECTEURS] + ["autre"]
        self.sec = np.array([self.snames.index(self.sector_of_tile.get(int(k), "autre")) if self.zone[u] else len(self.snames) - 1
                             for u, k in enumerate(S.tile)], np.int16)
        self.secgrid = S.grid(self.sec.astype(np.float32), len(self.snames) - 1).astype(np.int16)
        self.tilegrid = S.grid(S.tile.astype(np.float32), -1).astype(np.int64)
        self.calib_cache = {}
        self.dataonly = self.build_dataonly()

    # ---------- prédicteurs ----------
    def hard(self, v):
        return np.where(v == 1, 1.0, np.where(v == 0, 0.0, 0.5)).astype(np.float32)

    def our(self, view, d):
        sn = self.snap_of[d]
        f = self.S.fields.get(view, {})
        return f.get(sn) if sn else None

    def step(self, cad):
        return 3 if cad == "mois" else 1

    def eval_dates(self, cad, h):
        out = []
        for d in range(self.ND):
            if self.dates[d].isoformat() < EVAL_FROM or d + h >= self.ND:
                continue
            if cad == "mois" and not self.month_end[d]:
                continue
            out.append(d)
        return out

    def domain(self, d, dt_):
        T = self.cons[dt_]
        f0, ft = self.F["cons"][d], self.F["cons"][dt_]
        return self.S.isUA & (T >= 0) & ((np.nan_to_num(np.abs(f0), nan=999) <= D30) | (np.nan_to_num(np.abs(ft), nan=999) <= D30))

    def calibrated(self, key, fkey, d, h, cad, lagsteps=0):
        """Carte dure recalée : p = σ(w · [1, x, x⁺]), x = distance signée de la carte à la date d − lagsteps ; w appris sur
        les dates q (même cadence) avec q + h ≤ d, 12 derniers mois."""
        ck = (key, d, h, cad)
        if ck in self.calib_cache:
            return self.calib_cache[ck]
        win = 12 if cad == "mois" else 36
        tr = [q for q in range(self.ND) if q + h <= d and q - lagsteps >= 0 and (cad == "decade" or self.month_end[q])][-win:]
        rng = np.random.default_rng(d * 7 + h)
        Xs, Ys = [], []
        for q in tr:
            fq = self.F[fkey][q - lagsteps]
            if not np.isfinite(fq).any():
                continue
            m = np.flatnonzero(self.domain(q, q + h) & np.isfinite(fq))
            if len(m) == 0:
                continue
            m = rng.choice(m, size=min(6000, len(m)), replace=False)
            Xs.append(feat(fq[m]))
            Ys.append((self.cons[q + h][m] == 1).astype(np.float64))
        f = self.F[fkey][d - lagsteps]
        if len(Xs) < 3 or not np.isfinite(f).any():
            p = None
        else:
            w = fit_logit(np.concatenate(Xs), np.concatenate(Ys))
            p = sigm(feat(f) @ w).astype(np.float32)
        self.calib_cache[ck] = p
        return p

    def build_dataonly(self):
        """Ligne « données seules » (recherche v4, `analyses/nowcast/v4_dataonly2.py`), pas mensuel : au mois t, le
        champ des cartes de t − 3 mois (témoin retardé) et une logistique de bascule t − 3 → t apprise en walk-forward
        (distance, côté, feux, UCDP, pertes, assauts de l'état-major, tendance). Absent si le cache de recherche manque."""
        if not os.path.exists(NOWCAST_CACHE):
            return {}
        Z = np.load(NOWCAST_CACHE)
        S = self.S
        if not np.array_equal(Z["U"], S.U):
            return {}
        cm = [str(m) for m in Z["months"]]
        IND = Z["IND"]
        pos = {int(k): n for n, k in enumerate(S.U)}
        OF = np.zeros((len(cm), S.nU), np.float32)
        mi = {m: i for i, m in enumerate(cm)}
        path = os.path.join(ROOT, "data", "officiels_cells_month.csv")
        if os.path.exists(path):
            for r in csv.DictReader(open(path)):
                u, t = pos.get(int(r["maille"])), mi.get(r["mois"])
                if u is not None and t is not None and r["source"] == "etatmajor_assauts":
                    OF[t, u] += float(r["par_jour"])
        H = [np.percentile(IND[n][IND[n] > 0], 90) for n in range(3)]
        # mois du site ↔ fins de mois des décades
        me = {self.dates[d].isoformat()[:7]: d for d in range(self.ND) if self.month_end[d]}
        peq = self.S.fields["equilibre"]
        pm = {m: peq[x] for x in peq for m in [x[:7]]}

        def sm(v, s):
            return ndimage.gaussian_filter(S.grid(np.nan_to_num(v), 0), s)[S.jj, S.ii]
        LAG = 3
        data, out, hist = {}, {}, {}
        months = [m for m in cm if m in me and m in pm]
        for m in months:
            t = cm.index(m)
            m0 = cm[t - LAG] if t - LAG >= 0 else None
            if m0 not in pm or m0 not in me or t - 2 * LAG < 0 or cm[t - 2 * LAG] not in me:
                continue
            p0 = pm[m0]
            sd = self.F["cons"][me[m0]]
            sig = [sum(1 - np.exp(-sm(IND[n][q], 2.0) / H[n]) for q in range(t - LAG + 1, t + 1)) / LAG for n in range(3)]
            gs = sum(np.log1p(sm(OF[q], 2.5)) for q in range(t - LAG + 1, t + 1)) / LAG
            c0, cpp = self.cons[me[m0]], self.cons[me[cm[t - 2 * LAG]]]
            trend = sm(((c0 >= 0) & (cpp >= 0) & (c0 != cpp)).astype(np.float32), 4.0)
            a = np.clip(np.abs(np.nan_to_num(sd, nan=60)), 0, 30) / 10
            ru = (np.nan_to_num(sd) > 0).astype(np.float32)
            X = np.stack([a, ru, a * ru, sig[0], sig[1], sig[2], gs, trend, sig[0] * ru, sig[1] * ru, gs * ru, trend * ru], 1).astype(np.float32)
            ct = self.cons[me[m]]
            msk = (np.abs(np.nan_to_num(sd, nan=999)) <= 30) & (ct >= 0) & (c0 >= 0)
            data[m] = (X, (ct != c0).astype(np.float32), msk, p0)
        rng = np.random.default_rng(0)
        ks = sorted(data)
        for n, m in enumerate(ks):
            tr = [q for q in ks[:n] if cm.index(q) <= cm.index(m) - 2][-18:]
            if len(tr) < 6:
                continue
            Xs, Ys = [], []
            for q in tr:
                X, y, msk, _ = data[q]
                idx = np.flatnonzero(msk)
                idx = rng.choice(idx, size=min(8000, len(idx)), replace=False)
                Xs.append(X[idx]); Ys.append(y[idx])
            Xa, Ya = np.concatenate(Xs), np.concatenate(Ys)
            mu, sv = Xa.mean(0), Xa.std(0) + 1e-9
            w = fit_logit(np.c_[np.ones(len(Xa)), (Xa - mu) / sv], Ya, lam=1.0, it=20)
            X, _, msk, p0 = data[m]
            pf = sigm(np.c_[np.ones(len(X)), (X - mu) / sv] @ w)
            pdat = p0 * (1 - pf) + (1 - p0) * pf
            truth_now = (self.cons[me[m]] == 1).astype(np.float32)
            hist[m] = (p0, pdat, truth_now, msk)
            best, bk = 1e9, 0.0
            for k in (0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0):
                e = [((q0 + k * (qd - q0) - y) ** 2)[mm].mean() for q, (q0, qd, y, mm) in hist.items() if cm.index(q) <= cm.index(m) - 2]
                if e and np.mean(e) < best:
                    best, bk = np.mean(e), k
            out[self.dates[me[m]].isoformat()] = (p0 + bk * (pdat - p0)).astype(np.float32)
        log(f"ligne « données seules » : {len(out)} mois")
        return out

    def predictors(self, d, h, cad):
        """Tous les prédicteurs à la date d (vecteurs sur U), sans information postérieure à d."""
        P = {}
        P["consensus (dur)"] = self.hard(self.cons[d])
        P["consensus recalé"] = self.calibrated("cons", "cons", d, h, cad)
        for s, nm in (("dsm", "DeepState (journal daté)"), ("isw", "ISW"), ("wiki", "Wikipédia")):
            if s == "isw" and self.isw_end and self.dates[d] > self.isw_end:
                continue
            P[nm + " (dur)"] = self.hard(self.maps[s][d])
            P[nm + " recalé"] = self.calibrated(s, s, d, h, cad)
        lag = self.step(cad)
        if d - lag >= 0:
            P["persistance (consensus t−1) dur"] = self.hard(self.cons[d - lag])
            P["persistance (consensus t−1) recalée"] = self.calibrated("pers", "cons", d, h, cad, lagsteps=lag)
        for view, nm in (("equilibre", "nous : pool (cartes + annonces)"), ("nowcast", "nous : champ publié (nowcast)"),
                         ("ua", "nous : vue ukrainienne"), ("ru", "nous : vue russe"), ("tiers", "nous : vue tierce")):
            p = self.our(view, d)
            if p is not None:
                P[nm] = p
        if cad == "mois":
            p = self.dataonly.get(self.dates[d].isoformat())
            if p is not None:
                P["données seules (v4, recherche)"] = p
        return {k: v for k, v in P.items() if v is not None}

    # ---------- empilement (test d'information au-delà du consensus du jour) ----------
    def src_p(self, src, q, cad, seed=0):
        """Champ d'une source à la date q (aucune information postérieure à q)."""
        if src == "nowcast":
            return self.our("nowcast", q)
        if src == "pool":
            return self.our("equilibre", q)
        if src in ("dsm", "isw", "wiki"):
            if src == "isw" and self.isw_end and self.dates[q] > self.isw_end:
                return None
            return self.hard(self.maps[src][q])
        if src == "pers":
            lag = self.step(cad)
            return self.hard(self.cons[q - lag]) if q - lag >= 0 else None
        if src == "dataonly":
            return self.dataonly.get(self.dates[q].isoformat())
        if src == "placebo_bruit":
            ut, inv = np.unique(self.S.tile, return_inverse=True)
            rng = np.random.default_rng(100003 * (seed + 1) + q)
            return sigm(2.0 * rng.normal(0, 1, len(ut))[inv]).astype(np.float32)
        if src == "placebo_sens":
            pn, pe, sn = self.our("nowcast", q), self.our("equilibre", q), self.snap_of[q]
            if pn is None or pe is None:
                return None
            ut, inv = np.unique(self.S.tile, return_inverse=True)
            rng = np.random.default_rng(1000 * seed + 7 + int(sn.replace("-", "")) % 100000)
            r = np.where(rng.random(len(ut)) < 0.5, -1.0, 1.0)[inv]
            return sigm(lgt(pe) + r * (lgt(pn) - lgt(pe))).astype(np.float32)
        raise KeyError(src)

    def _sample(self, q, h):
        k = ("smp", q, h)
        if k not in self.calib_cache:
            m = np.flatnonzero(self.domain(q, q + h) & np.isfinite(self.F["cons"][q]))
            rng = np.random.default_rng(q * 31 + h)
            self.calib_cache[k] = rng.choice(m, size=min(8000, len(m)), replace=False) if len(m) else m
        return self.calib_cache[k]

    def stack_X(self, src, q, cad, idx, seed):
        p = self.src_p(src, q, cad, seed)
        if p is None:
            return None
        f = self.F["cons"][q] if idx is None else self.F["cons"][q][idx]
        p = p if idx is None else p[idx]
        g = np.maximum(0, 1 - np.abs(np.nan_to_num(f, nan=99)) / 30)
        L = np.clip(lgt(p), -4, 4) / 4
        return np.c_[feat(f), L, L * g]

    def stacked(self, src, d, h, cad, seed=0):
        """p(vérité russe) = σ(w · [1, x, x⁺, L, L·g]) : consensus du jour (x, distance signée) + source (L, logit borné,
        g = 1 − |x|/30) ; et la même chose sans la source (base). w appris sur les dates q (même cadence) avec q + h ≤ d,
        12 derniers mois. Renvoie (p_empilé, p_base) sur U, ou (None, None)."""
        win = 12 if cad == "mois" else 36
        tr = [q for q in range(self.ND) if q + h <= d and (cad == "decade" or self.month_end[q])][-win:]
        Xs, Ys = [], []
        for q in tr:
            idx = self._sample(q, h)
            X = self.stack_X(src, q, cad, idx, seed)
            if X is None or len(idx) == 0:
                continue
            Xs.append(X)
            Ys.append((self.cons[q + h][idx] == 1).astype(np.float64))
        Xd = self.stack_X(src, d, cad, None, seed)
        if len(Xs) < 6 or Xd is None:
            return None, None
        Xa, Ya = np.concatenate(Xs), np.concatenate(Ys)
        w = fit_logit(Xa, Ya, lam=1.0)
        w0 = fit_logit(Xa[:, :3], Ya, lam=1.0)
        return sigm(Xd @ w).astype(np.float32), sigm(Xd[:, :3] @ w0).astype(np.float32)

    def variants(self, d):
        out = {}
        for v in ("sans_dsm", "sans_isw", "sans_wiki", "sans_enveloppes", "sans_medias", "sans_modru", "sans_gsua", "sans_presence", "sans_indices"):
            p = self.our(v, d)
            if p is not None:
                out["variante " + v] = p
        return out

    def placebos(self, d, h, cad, seed, base):
        """P1 : consensus recalé + bruit de logit par tronçon (même ampleur moyenne que |logit nowcast − logit consensus
        recalé| sur D30) ; P2 : pool + écart (nowcast − pool) appliqué dans un sens tiré au hasard par tronçon et par mois."""
        S = self.S
        out = {}
        pc, pn, pe = base.get("consensus recalé"), base.get("nous : champ publié (nowcast)"), base.get("nous : pool (cartes + annonces)")
        sn = self.snap_of[d]
        tiles = S.tile
        ut, inv = np.unique(tiles, return_inverse=True)
        if pc is not None and pn is not None:
            m = self.domain(d, d + h)
            amp = float(np.abs(lgt(pn) - lgt(pc))[m].mean()) if m.any() else 0.0
            rng = np.random.default_rng(1000 * seed + d)
            eps = rng.normal(0, 1, len(ut))[inv]
            eps *= amp / max(float(np.abs(eps[m]).mean()), 1e-9)
            out["PLACEBO 1 : consensus recalé + bruit"] = sigm(lgt(pc) + eps).astype(np.float32)
        if pe is not None and pn is not None and sn:
            rng = np.random.default_rng(1000 * seed + 7 + int(sn.replace("-", "")) % 100000)
            r = np.where(rng.random(len(ut)) < 0.5, -1.0, 1.0)[inv]
            out["PLACEBO 2 : nowcast à sens tiré au hasard"] = sigm(lgt(pe) + r * (lgt(pn) - lgt(pe))).astype(np.float32)
        return out

    # ---------- mesures ----------
    def measure(self, p, d, h, truth=None, truth_key="cons", line_only=False, cache=None):
        """Sommes par date (et par secteur) : Brier, log-loss sur D30, mailles qui changent, stables ; ligne (km)."""
        S, G = self.S, self.G
        dt_ = d + h
        T = self.cons[dt_] if truth is None else truth
        key = (d, h, truth_key)
        if cache is not None and key in cache:
            dom, chg, Et, dTt, sec_dom = cache[key]
        else:
            dom = self.domain(d, dt_) & (T >= 0)
            c0 = self.cons[d]
            chg = dom & (c0 >= 0) & (c0 != T)
            ft = self.G.sfield(T) if truth is not None else self.F["cons"][dt_]
            Et = G.edges(ft) & S.grid(self.zone.astype(np.float32), 0).astype(bool)
            dTt = G.dist_to(Et)
            sec_dom = self.sec
            if cache is not None:
                cache[key] = (dom, chg, Et, dTt, sec_dom)
        r = {}
        nsec = len(self.snames)
        if not line_only:
            y = (T == 1).astype(np.float32)
            pc = np.clip(p, EPS, 1 - EPS)
            b = (p - y) ** 2
            ll = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
            st = dom & ~chg
            r.update(n30=int(dom.sum()), b30=float(b[dom].sum()), l30=float(ll[dom].sum()), nchg=int(chg.sum()), bchg=float(b[chg].sum()),
                     lchg=float(ll[chg].sum()), nst=int(st.sum()), bst=float(b[st].sum()))
            r["sec_n30"] = np.bincount(self.sec[dom], minlength=nsec).tolist()
            r["sec_b30"] = np.bincount(self.sec[dom], weights=b[dom], minlength=nsec).tolist()
            r["sec_nchg"] = np.bincount(self.sec[chg], minlength=nsec).tolist()
            r["sec_bchg"] = np.bincount(self.sec[chg], weights=b[chg], minlength=nsec).tolist()
        Ep = G.edges(p - 0.5) & S.grid(self.zone.astype(np.float32), 0).astype(bool)
        if Ep.any() and Et.any():
            dP = G.dist_to(Ep)
            d1 = np.minimum(dTt[Ep], CAP_KM)
            d2 = np.minimum(dP[Et], CAP_KM)
            s1, s2 = self.secgrid[Ep], self.secgrid[Et]
            r.update(k1=float(d1.sum()), n1=int(d1.size), k2=float(d2.sum()), n2=int(d2.size),
                     sec_k1=np.bincount(s1, weights=d1, minlength=nsec).tolist(), sec_n1=np.bincount(s1, minlength=nsec).tolist(),
                     sec_k2=np.bincount(s2, weights=d2, minlength=nsec).tolist(), sec_n2=np.bincount(s2, minlength=nsec).tolist())
            t1, t2 = self.tilegrid[Ep], self.tilegrid[Et]
            r["tile"] = (t1, d1, t2, d2)
        return r


def agg(rows, key_n, key_s):
    n = sum(r.get(key_n, 0) for r in rows)
    return sum(r.get(key_s, 0.0) for r in rows) / n if n else float("nan")


def kmsym(rows):
    n1, n2 = sum(r.get("n1", 0) for r in rows), sum(r.get("n2", 0) for r in rows)
    if not n1 or not n2:
        return float("nan")
    return 0.5 * (sum(r.get("k1", 0) for r in rows) / n1 + sum(r.get("k2", 0) for r in rows) / n2)


def skill(rows_a, rows_b, key_n, key_s):
    """1 − somme(a) / somme(b), mêmes dates."""
    sa = sum(r.get(key_s, 0.0) for r in rows_a)
    sb = sum(r.get(key_s, 0.0) for r in rows_b)
    return 1 - sa / sb if sb else float("nan")


def boot(rows_a, rows_b, fn, nboot=NBOOT, block=3, seed=0):
    """Intervalle à 95 % par bootstrap de blocs de dates consécutives (3 mois au pas mensuel)."""
    n = len(rows_a)
    if n < block + 1:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    starts = np.arange(n - block + 1)
    vals = []
    for _ in range(nboot):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.choice(starts, size=int(math.ceil(n / block)))])[:n]
        vals.append(fn([rows_a[i] for i in idx], [rows_b[i] for i in idx]))
    vals = np.array(vals, float)
    vals = vals[np.isfinite(vals)]
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if len(vals) else (float("nan"), float("nan"))


def km_skill(ra, rb):
    a, b = kmsym(ra), kmsym(rb)
    return 1 - a / b if b else float("nan")


def br_skill(ra, rb):
    return skill(ra, rb, "n30", "b30")


def chg_skill(ra, rb):
    return skill(ra, rb, "nchg", "bchg")


# ---------------------------------------------------------------- détection (pas décadaire)
def detection(M, preds_by_date, names, thr=0.5):
    """Événements : une maille de la zone dont le consensus bascule à la décade D (et le reste 3 décades). Pour chaque
    source : avance = D − début de la série continue de décades (≤ 9, soit 90 jours) où la source était déjà du nouveau
    côté ; sinon retard jusqu'à ce qu'elle y passe (≤ 9 décades, au-delà : manquée).
    Fausses détections : la source s'écarte du consensus (accord à la décade précédente, consensus inchangé) et le
    consensus ne la rejoint pas dans les 6 décades (2 mois)."""
    Zc = M.Zc
    nd = M.ND
    C = M.cons[:, Zc]
    res = {}
    for nm in names:
        X = np.full((nd, len(Zc)), -1, np.int8)
        for d in range(nd):
            p = preds_by_date[d].get(nm)
            if p is None:
                continue
            q = p[Zc]
            if thr == 0.5:
                X[d] = np.where(q > 0.5, 1, np.where(q < 0.5, 0, -1))
            else:
                X[d] = np.where(q >= thr, 1, np.where(q <= 1 - thr, 0, -1))
        leads, missed, n_ev = [], 0, 0
        ons_t = ons_f = 0
        for d in range(1, nd - 3):
            if M.dates[d].isoformat() < EVAL_FROM:
                continue
            c0, c1 = C[d - 1], C[d]
            ev = (c0 >= 0) & (c1 >= 0) & (c0 != c1)
            for k in range(1, 4):
                ev &= C[d + k] == c1
            ev &= X[d] >= 0
            ev &= (X[max(0, d - 9):d] >= 0).all(0) if d >= 1 else ev
            idx = np.flatnonzero(ev)
            if len(idx):
                tgt = c1[idx]
                L = np.zeros(len(idx), int)
                alive = np.ones(len(idx), bool)
                for j in range(1, 10):
                    if d - j < 0:
                        break
                    alive &= X[d - j, idx] == tgt
                    L += alive
                lead = np.zeros(len(idx))
                ant = L > 0
                lead[ant] = [(M.dates[d] - M.dates[d - L[i]]).days for i in np.flatnonzero(ant)]
                for i in np.flatnonzero(~ant):
                    lag = None
                    for j in range(0, 10):
                        if d + j < nd and X[d + j, idx[i]] == tgt[i]:
                            lag = (M.dates[d + j] - M.dates[d]).days
                            break
                    if lag is None:
                        missed += 1
                        lead[i] = -90
                    else:
                        lead[i] = -lag
                leads.append(lead)
                n_ev += len(idx)
            # départs de la source
            if d + 6 < nd:
                on = (X[d] >= 0) & (C[d] >= 0) & (X[d] != C[d]) & (X[d - 1] >= 0) & (X[d - 1] == C[d - 1]) & (C[d] == C[d - 1])
                oi = np.flatnonzero(on)
                if len(oi):
                    later = np.zeros(len(oi), bool)
                    for j in range(1, 7):
                        later |= C[d + j, oi] == X[d, oi]
                    ons_t += int(later.sum())
                    ons_f += int((~later).sum())
        lead = np.concatenate(leads) if leads else np.zeros(0)
        nmonths = len({M.dates[d].isoformat()[:7] for d in range(1, nd - 6) if M.dates[d].isoformat() >= EVAL_FROM})
        res[nm] = {"evenements": int(n_ev), "part_anticipee": float((lead > 0).mean()) if n_ev else float("nan"),
                   "avance_moy_j_si_anticipee": float(lead[lead > 0].mean()) if (lead > 0).any() else 0.0,
                   "delai_moyen_signe_j": float(lead.mean()) if n_ev else float("nan"),
                   "delai_median_signe_j": float(np.median(lead)) if n_ev else float("nan"),
                   "part_manquee_90j": missed / n_ev if n_ev else float("nan"),
                   "departs": ons_t + ons_f, "fausses": ons_f, "taux_fausses": ons_f / (ons_t + ons_f) if ons_t + ons_f else float("nan"),
                   "km2_departs_par_mois": 4.0 * (ons_t + ons_f) / max(nmonths, 1), "km2_fausses_par_mois": 4.0 * ons_f / max(nmonths, 1)}
    return res


# ---------------------------------------------------------------- principal
def fmt(x, n=4):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x:.{n}f}".replace(".", ",")


def pct(x, n=1):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{100 * x:+.{n}f} %".replace(".", ",")


def main():
    os.makedirs(OUT, exist_ok=True)
    M = Moteur()
    S = M.S
    log(f"zone : {len(M.Zc)} mailles ; ISW arrêtée le {M.isw_end} ; site généré {S.generated}")
    R = {"meta": {"genere": dt.datetime.now().isoformat(timespec="seconds"), "site": S.generated, "viina_last": M.meta.get("viina_last"),
                  "isw_fin": M.isw_end.isoformat() if M.isw_end else None, "eval_depuis": EVAL_FROM, "secteurs": M.snames}}
    REF, US, POOL = "consensus recalé", "nous : champ publié (nowcast)", "nous : pool (cartes + annonces)"
    mens_rows, tile_acc, sec_rows = [], {}, []
    allres = {}
    for cad in CADENCES:
        for hm in (1, 2):
            h = 3 * hm
            ds = M.eval_dates(cad, h)
            log(f"pas {cad}, h = {hm} mois : {len(ds)} dates")
            rows = {}
            cache = {}
            for d in ds:
                base = M.predictors(d, h, cad)
                allp = dict(base)
                if cad == "mois":
                    allp.update(M.variants(d))
                for nm, p in allp.items():
                    r = M.measure(p, d, h, line_only=nm.startswith("variante"), cache=cache)
                    r["date"] = M.dates[d].isoformat()
                    tl = r.pop("tile", None)
                    if tl is not None and cad == "mois" and hm == 1 and nm in (US, POOL, REF, "DeepState (journal daté) (dur)", "ISW (dur)", "Wikipédia (dur)", "consensus (dur)"):
                        t1, d1, t2, d2 = tl
                        for tt, dd in ((t1, d1), (t2, d2)):
                            key = (nm, r["date"] >= "2025-09-01")
                            acc = tile_acc.setdefault(key, {})
                            for k, v in zip(tt.tolist(), dd.tolist()):
                                a = acc.setdefault(k, [0.0, 0])
                                a[0] += v
                                a[1] += 1
                    rows.setdefault(nm, []).append(r)
                # vérités « sans la source » (cartes brutes et nous), pas mensuel seulement
                if cad == "mois":
                    for s, lab in (("dsm", "DeepState (journal daté)"), ("isw", "ISW"), ("wiki", "Wikipédia")):
                        T = M.loo[s][d + h]
                        for nm in (lab + " (dur)", lab + " recalé", US, POOL, "consensus (dur)"):
                            if nm in base:
                                r = M.measure(base[nm], d, h, truth=T, truth_key="loo_" + s, cache=cache)
                                r.pop("tile", None)
                                r["date"] = M.dates[d].isoformat()
                                rows.setdefault(f"[sans {s}] {nm}", []).append(r)
                for src, lab in STACK:
                    for seed in (range(SEEDS_STACK) if src.startswith("placebo") else (0,)):
                        if cad == "decade" and src == "dataonly":
                            continue
                        ps, pb = M.stacked(src, d, h, cad, seed)
                        if ps is None:
                            continue
                        sfx = f" #{seed}" if src.startswith("placebo") else ""
                        for nm, p in ((f"EMPILÉ consensus + {lab}{sfx}", ps), (f"EMPILÉ base pour {lab}{sfx}", pb)):
                            r = M.measure(p, d, h, cache=cache)
                            r.pop("tile", None)
                            r["date"] = M.dates[d].isoformat()
                            rows.setdefault(nm, []).append(r)
                for seed in range(SEEDS):
                    for nm, p in M.placebos(d, h, cad, seed, base).items():
                        r = M.measure(p, d, h, cache=cache)
                        r.pop("tile", None)
                        r["date"] = M.dates[d].isoformat()
                        rows.setdefault(f"{nm} #{seed}", []).append(r)
                cache.clear()
            allres[(cad, hm)] = rows
    # ------------------ synthèse
    out = {}
    for (cad, hm), rows in allres.items():
        key = f"{cad}_h{hm}"
        dates_all = sorted({r["date"] for r in rows[US]})
        last12 = dates_all[-12:] if cad == "mois" else dates_all[-36:]
        per = {}
        for label, sel in (("periode", set(dates_all)), ("12_derniers_mois", set(last12))):
            tab = {}
            ref = [r for r in rows[REF] if r["date"] in sel]
            refd = {r["date"]: r for r in ref}
            for nm, rr in rows.items():
                if "#" in nm and not nm.endswith("#0"):
                    continue
                rr = [r for r in rr if r["date"] in sel]
                if not rr:
                    continue
                dd = [r["date"] for r in rr]
                rb = [refd[x] for x in dd if x in refd]
                ra = [r for r in rr if r["date"] in refd]
                tab[nm] = {"dates": f"{dd[0]} → {dd[-1]}", "n_dates": len(rr), "brier30": agg(rr, "n30", "b30"), "logloss30": agg(rr, "n30", "l30"),
                           "brier_chg": agg(rr, "nchg", "bchg"), "logloss_chg": agg(rr, "nchg", "lchg"), "brier_stables": agg(rr, "nst", "bst"),
                           "km": kmsym(rr), "km_pred_vers_vrai": agg(rr, "n1", "k1"), "km_vrai_vers_pred": agg(rr, "n2", "k2"),
                           "avance_brier": br_skill(ra, rb) if ra else float("nan"), "avance_chg": chg_skill(ra, rb) if ra else float("nan"),
                           "avance_km": km_skill(ra, rb) if ra else float("nan")}
            # placebos : moyenne et étendue sur les graines
            for base_nm in ("PLACEBO 1 : consensus recalé + bruit", "PLACEBO 2 : nowcast à sens tiré au hasard"):
                vals = {"avance_brier": [], "avance_chg": [], "avance_km": [], "vs_pool_brier": [], "vs_pool_km": [], "brier30": [], "km": []}
                for seed in range(SEEDS):
                    rr = [r for r in rows.get(f"{base_nm} #{seed}", []) if r["date"] in sel]
                    if not rr:
                        continue
                    ra = [r for r in rr if r["date"] in refd]
                    rb = [refd[r["date"]] for r in ra]
                    pool = {r["date"]: r for r in rows[POOL] if r["date"] in sel}
                    rp = [pool[r["date"]] for r in ra if r["date"] in pool]
                    ra2 = [r for r in ra if r["date"] in pool]
                    vals["avance_brier"].append(br_skill(ra, rb))
                    vals["avance_chg"].append(chg_skill(ra, rb))
                    vals["avance_km"].append(km_skill(ra, rb))
                    vals["vs_pool_brier"].append(br_skill(ra2, rp))
                    vals["vs_pool_km"].append(km_skill(ra2, rp))
                    vals["brier30"].append(agg(rr, "n30", "b30"))
                    vals["km"].append(kmsym(rr))
                if vals["brier30"]:
                    tab[base_nm + " (moyenne des graines)"] = {k: float(np.mean(v)) for k, v in vals.items()}
                    tab[base_nm + " (moyenne des graines)"].update({k + "_max": float(np.max(v)) for k, v in vals.items()})
            # KPI et intervalles
            ra = [r for r in rows[US] if r["date"] in sel and r["date"] in refd]
            rb = [refd[r["date"]] for r in ra]
            pool = {r["date"]: r for r in rows[POOL] if r["date"] in sel}
            rp = [pool[r["date"]] for r in ra if r["date"] in pool]
            ra2 = [r for r in ra if r["date"] in pool]
            blk = 3 if cad == "mois" else 9
            kpi = {"avance_brier": br_skill(ra, rb), "IC95_brier": boot(ra, rb, br_skill, block=blk),
                   "avance_chg": chg_skill(ra, rb), "IC95_chg": boot(ra, rb, chg_skill, block=blk),
                   "avance_km": km_skill(ra, rb), "IC95_km": boot(ra, rb, km_skill, block=blk),
                   "ecart_km": kmsym(ra) - kmsym(rb),
                   "nowcast_vs_pool_brier": br_skill(ra2, rp), "IC95_nowcast_vs_pool_brier": boot(ra2, rp, br_skill, block=blk),
                   "nowcast_vs_pool_km": km_skill(ra2, rp), "IC95_nowcast_vs_pool_km": boot(ra2, rp, km_skill, block=blk),
                   "pool_vs_consensus_brier": br_skill(rp, [refd[r["date"]] for r in rp]),
                   "pool_vs_consensus_km": km_skill(rp, [refd[r["date"]] for r in rp])}
            hard = {r["date"]: r for r in rows["consensus (dur)"] if r["date"] in sel}
            rh = [hard[r["date"]] for r in ra if r["date"] in hard]
            kpi["avance_brier_vs_consensus_dur"] = br_skill([r for r in ra if r["date"] in hard], rh)
            kpi["avance_km_vs_consensus_dur"] = km_skill([r for r in ra if r["date"] in hard], rh)
            kpi["IC95_brier_vs_consensus_dur"] = boot([r for r in ra if r["date"] in hard], rh, br_skill, block=blk)
            # contributions des composantes du pool (ligne seulement) : km(variante) − km(pool)
            contrib = {}
            for nm in rows:
                if nm.startswith("variante "):
                    rv = {r["date"]: r for r in rows[nm] if r["date"] in sel}
                    common = [x for x in pool if x in rv]
                    if common:
                        contrib[nm.replace("variante ", "")] = kmsym([rv[x] for x in common]) - kmsym([pool[x] for x in common])
            kpi["contrib_km_retrait"] = contrib
            # empilement : gain de (consensus du jour + source) sur (consensus du jour seul), mêmes apprentissages
            emp = {}
            for src, lab in STACK:
                seeds = range(SEEDS_STACK) if src.startswith("placebo") else (0,)
                acc = {"brier": [], "chg": [], "km": [], "stables": [], "IC": None}
                for seed in seeds:
                    sfx = f" #{seed}" if src.startswith("placebo") else ""
                    a = {r["date"]: r for r in rows.get(f"EMPILÉ consensus + {lab}{sfx}", []) if r["date"] in sel}
                    b = {r["date"]: r for r in rows.get(f"EMPILÉ base pour {lab}{sfx}", []) if r["date"] in sel}
                    common = sorted(x for x in a if x in b)
                    if not common:
                        continue
                    ra_, rb_ = [a[x] for x in common], [b[x] for x in common]
                    acc["brier"].append(br_skill(ra_, rb_))
                    acc["chg"].append(chg_skill(ra_, rb_))
                    acc["km"].append(km_skill(ra_, rb_))
                    acc["stables"].append(skill(ra_, rb_, "nst", "bst"))
                    if seed == 0:
                        acc["IC"] = boot(ra_, rb_, br_skill, block=blk)
                        acc["IC_chg"] = boot(ra_, rb_, chg_skill, block=blk)
                        acc["IC_km"] = boot(ra_, rb_, km_skill, block=blk)
                        acc["n_dates"] = len(common)
                        acc["dates"] = f"{common[0]} → {common[-1]}"
                        acc["brier_base"] = agg(rb_, "n30", "b30")
                        acc["brier_empile"] = agg(ra_, "n30", "b30")
                if acc["brier"]:
                    emp[lab] = {"avance_brier": float(np.mean(acc["brier"])), "avance_brier_max": float(np.max(acc["brier"])),
                                "avance_chg": float(np.mean(acc["chg"])), "avance_km": float(np.mean(acc["km"])),
                                "avance_stables": float(np.mean(acc["stables"])), "IC95_brier": acc["IC"], "IC95_chg": acc.get("IC_chg"),
                                "IC95_km": acc.get("IC_km"), "n_dates": acc.get("n_dates"), "dates": acc.get("dates"),
                                "brier_base": acc.get("brier_base"), "brier_empile": acc.get("brier_empile"), "graines": len(acc["brier"])}
            kpi["empilement"] = emp
            # KPI NET : gain d'empilement de notre champ moins celui du placebo « sens tiré au hasard » (moyenne des
            # graines), apparié date par date ; intervalle par bootstrap de blocs sur la différence
            plab = "PLACEBO nowcast à sens tiré au hasard"
            net = {}
            for src_lab in ("nous : champ publié (nowcast)", "nous : pool (cartes + annonces)"):
                a = {r["date"]: r for r in rows.get(f"EMPILÉ consensus + {src_lab}", []) if r["date"] in sel}
                b = {r["date"]: r for r in rows.get(f"EMPILÉ base pour {src_lab}", []) if r["date"] in sel}
                pa = [{r["date"]: r for r in rows.get(f"EMPILÉ consensus + {plab} #{s}", [])} for s in range(SEEDS_STACK)]
                pb = [{r["date"]: r for r in rows.get(f"EMPILÉ base pour {plab} #{s}", [])} for s in range(SEEDS_STACK)]
                common = sorted(x for x in a if x in b and all(x in q for q in pa) and all(x in q for q in pb))
                if len(common) < 4:
                    continue

                def netf(idx, key_n, key_s, common=common, a=a, b=b, pa=pa, pb=pb):
                    ds_ = [common[i] for i in idx]
                    s0 = skill([a[x] for x in ds_], [b[x] for x in ds_], key_n, key_s)
                    sp = np.mean([skill([pa[s][x] for x in ds_], [pb[s][x] for x in ds_], key_n, key_s) for s in range(len(pa))])
                    return s0 - sp

                def netkm(idx, common=common, a=a, b=b, pa=pa, pb=pb):
                    ds_ = [common[i] for i in idx]
                    s0 = km_skill([a[x] for x in ds_], [b[x] for x in ds_])
                    return s0 - np.mean([km_skill([pa[s][x] for x in ds_], [pb[s][x] for x in ds_]) for s in range(len(pa))])
                n = len(common)
                rng = np.random.default_rng(11)
                starts = np.arange(max(1, n - blk + 1))
                bs = {"brier": [], "chg": [], "km": []}
                for _ in range(max(200, NBOOT // 4)):
                    idx = np.concatenate([np.arange(s, min(n, s + blk)) for s in rng.choice(starts, size=int(math.ceil(n / blk)))])[:n]
                    bs["brier"].append(netf(idx, "n30", "b30"))
                    bs["chg"].append(netf(idx, "nchg", "bchg"))
                    bs["km"].append(netkm(idx))
                full = np.arange(n)
                ic = {k: [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))] for k, v in bs.items()}
                net[src_lab] = {"net_brier": netf(full, "n30", "b30"), "IC95_brier": ic["brier"], "net_chg": netf(full, "nchg", "bchg"),
                                "IC95_chg": ic["chg"], "net_km": netkm(full), "IC95_km": ic["km"], "n_dates": n, "dates": f"{common[0]} → {common[-1]}"}
            kpi["net_placebo"] = net
            per[label] = {"tableau": tab, "kpi": kpi}
        out[key] = per
        # courbe mensuelle et secteurs (pas mensuel)
        for nm in (US, POOL, REF, "consensus (dur)", "DeepState (journal daté) (dur)", "ISW (dur)", "Wikipédia (dur)",
                   "persistance (consensus t−1) recalée", "données seules (v4, recherche)"):
            for r in rows.get(nm, []):
                mens_rows.append([cad, hm, r["date"], nm, r.get("n30"), fmt(r["b30"] / r["n30"], 5) if r.get("n30") else "", r.get("nchg"),
                                  fmt(r["bchg"] / r["nchg"], 4) if r.get("nchg") else "", fmt(kmsym([r]), 3)])
        if cad == "mois":
            dates_all = sorted({r["date"] for r in rows[US]})
            for label, sel in (("periode", set(dates_all)), ("12_derniers_mois", set(dates_all[-12:]))):
                for nm in (US, POOL, REF, "consensus (dur)", "DeepState (journal daté) (dur)", "ISW (dur)", "Wikipédia (dur)"):
                    rr = [r for r in rows.get(nm, []) if r["date"] in sel]
                    if not rr:
                        continue
                    for si, sname in enumerate(M.snames):
                        n30 = sum(r["sec_n30"][si] for r in rr if "sec_n30" in r)
                        b30 = sum(r["sec_b30"][si] for r in rr if "sec_b30" in r)
                        nc = sum(r["sec_nchg"][si] for r in rr if "sec_nchg" in r)
                        bc = sum(r["sec_bchg"][si] for r in rr if "sec_bchg" in r)
                        n1 = sum(r["sec_n1"][si] for r in rr if "sec_n1" in r)
                        k1 = sum(r["sec_k1"][si] for r in rr if "sec_k1" in r)
                        n2 = sum(r["sec_n2"][si] for r in rr if "sec_n2" in r)
                        k2 = sum(r["sec_k2"][si] for r in rr if "sec_k2" in r)
                        km = 0.5 * (k1 / n1 + k2 / n2) if n1 and n2 else float("nan")
                        sec_rows.append([f"h{hm}", label, sname, nm, n30, b30 / n30 if n30 else float("nan"), nc, bc / nc if nc else float("nan"), km])
    R["resultats"] = out
    # ------------------ détection (pas décadaire, toutes les dates)
    log("détection (pas décadaire)…")
    preds_by_date = []
    for d in range(M.ND):
        if M.dates[d].isoformat() < "2022-10-01":
            preds_by_date.append({})
            continue
        P = {}
        P["consensus (dur)"] = M.hard(M.cons[d])
        for s, nm in (("dsm", "DeepState (journal daté)"), ("isw", "ISW"), ("wiki", "Wikipédia")):
            if s == "isw" and M.isw_end and M.dates[d] > M.isw_end:
                continue
            P[nm] = M.hard(M.maps[s][d])
        for view, nm in (("equilibre", POOL), ("nowcast", US)):
            p = M.our(view, d)
            if p is not None:
                P[nm] = p
        if "nous : champ publié (nowcast)" in P and POOL in P:
            sn = M.snap_of[d]
            rng = np.random.default_rng(int(sn.replace("-", "")))
            ut, inv = np.unique(S.tile, return_inverse=True)
            r = np.where(rng.random(len(ut)) < 0.5, -1.0, 1.0)[inv]
            P["PLACEBO 2 : nowcast à sens tiré au hasard"] = sigm(lgt(P[POOL]) + r * (lgt(P[US]) - lgt(P[POOL])))
        preds_by_date.append(P)
    names = ["DeepState (journal daté)", "ISW", "Wikipédia", POOL, US, "PLACEBO 2 : nowcast à sens tiré au hasard"]
    det = detection(M, preds_by_date, names)
    det08 = detection(M, preds_by_date, [POOL, US, "PLACEBO 2 : nowcast à sens tiré au hasard"], thr=0.8)
    R["detection"] = {"seuil_0_5": det, "seuil_0_8": det08}
    # ------------------ écritures
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [clean(v) for v in o]
        if isinstance(o, float) and not math.isfinite(o):
            return None
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        return o
    json.dump(clean(R), open(os.path.join(OUT, "resultats.json"), "w"), ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "mensuel.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pas", "h_mois", "date", "source", "n_mailles_30km", "brier_30km", "n_mailles_changent", "brier_changent", "ligne_km"])
        w.writerows(mens_rows)
    with open(os.path.join(OUT, "secteurs.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["h", "periode", "secteur", "source", "n_mailles_30km", "brier_30km", "n_mailles_changent", "brier_changent", "ligne_km"])
        for r in sec_rows:
            w.writerow([r[0], r[1], r[2], r[3], r[4], fmt(r[5], 5), r[6], fmt(r[7], 4), fmt(r[8], 3)])
    B = import_build_front()
    with open(os.path.join(OUT, "tuiles.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "periode", "tuile", "lon", "lat", "secteur", "n_points_ligne", "ligne_km"])
        for (nm, recent), acc in sorted(tile_acc.items()):
            for k, (s, n) in sorted(acc.items()):
                if k < 0 or n < 5:
                    continue
                j, i = (k // 1000 + 0.5) * TILE, (k % 1000 + 0.5) * TILE
                lon, lat = B.inv_laea(B.X0 + i * B.SIZE, B.Y1 - j * B.SIZE)
                w.writerow([nm, "12_derniers_mois" if recent else "avant", k, round(lon, 3), round(lat, 3), M.sector_of_tile.get(int(k), "autre"), n, fmt(s / n, 3)])
    with open(os.path.join(OUT, "detection.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["seuil", "source", "evenements", "part_anticipee", "avance_moy_j_si_anticipee", "delai_moyen_signe_j", "delai_median_signe_j",
                    "part_manquee_90j", "departs", "fausses", "taux_fausses", "km2_departs_par_mois", "km2_fausses_par_mois"])
        for th, dd in (("0,5", det), ("0,8", det08)):
            for nm, v in dd.items():
                w.writerow([th, nm] + [fmt(v[k], 3) if isinstance(v[k], float) else v[k] for k in ("evenements", "part_anticipee", "avance_moy_j_si_anticipee",
                                                                                                    "delai_moyen_signe_j", "delai_median_signe_j", "part_manquee_90j",
                                                                                                    "departs", "fausses", "taux_fausses", "km2_departs_par_mois",
                                                                                                    "km2_fausses_par_mois")])
    write_md(R, sec_rows)
    for key in out:
        e = out[key]["periode"]["kpi"]["empilement"]
        e12 = out[key]["12_derniers_mois"]["kpi"]["empilement"]
        print(f"KPI avance sur les cartes ({key} ; empilement, Brier ≤ 30 km) : "
              + json.dumps({nm: {"periode": round(e[nm]["avance_brier"], 4), "IC95": [round(x, 4) for x in e[nm]["IC95_brier"]],
                                 "12_mois": round(e12[nm]["avance_brier"], 4) if nm in e12 else None,
                                 "changent": round(e[nm]["avance_chg"], 4)} for nm in e if nm.startswith(("nous", "PLACEBO"))}, ensure_ascii=False), flush=True)
        for lab_ in ("periode", "12_derniers_mois"):
            nv = out[key][lab_]["kpi"].get("net_placebo", {}).get("nous : champ publié (nowcast)")
            if nv:
                print(f"  KPI NET ({key}, {lab_}) : Brier {nv['net_brier']:+.4f} {[round(x, 4) for x in nv['IC95_brier']]} ; "
                      f"changent {nv['net_chg']:+.4f} {[round(x, 4) for x in nv['IC95_chg']]} ; km {nv['net_km']:+.4f}", flush=True)


def write_md(R, sec_rows):
    L = ["# Résultats bruts du KPI « avance sur les cartes » (généré par `scripts/kpi_avance.py`, ne pas éditer)", "",
         f"Généré le {R['meta']['genere']} ; site du {R['meta']['site']} ; VIINA jusqu'au {R['meta']['viina_last']} ; ISW arrêtée le {R['meta']['isw_fin']}.", ""]
    for key, per in R["resultats"].items():
        for label, blk in per.items():
            k = blk["kpi"]
            L += [f"## {key} — {label}", "",
                  f"KPI avance (Brier ≤ 30 km, nowcast contre consensus recalé) : **{pct(k['avance_brier'])}** "
                  f"[{pct(k['IC95_brier'][0])} ; {pct(k['IC95_brier'][1])}] ; mailles qui changent {pct(k['avance_chg'])} "
                  f"[{pct(k['IC95_chg'][0])} ; {pct(k['IC95_chg'][1])}] ; ligne {pct(k['avance_km'])} [{pct(k['IC95_km'][0])} ; {pct(k['IC95_km'][1])}] "
                  f"(écart {fmt(k['ecart_km'], 3)} km) ; contre le consensus DUR : Brier {pct(k['avance_brier_vs_consensus_dur'])}, ligne {pct(k['avance_km_vs_consensus_dur'])}.",
                  f"Nowcast contre pool : Brier {pct(k['nowcast_vs_pool_brier'])} [{pct(k['IC95_nowcast_vs_pool_brier'][0])} ; {pct(k['IC95_nowcast_vs_pool_brier'][1])}], "
                  f"ligne {pct(k['nowcast_vs_pool_km'])} [{pct(k['IC95_nowcast_vs_pool_km'][0])} ; {pct(k['IC95_nowcast_vs_pool_km'][1])}] ; "
                  f"pool contre consensus recalé : Brier {pct(k['pool_vs_consensus_brier'])}, ligne {pct(k['pool_vs_consensus_km'])}.", ""]
            if k.get("empilement"):
                L += ["Empilement (gain de « consensus du jour + source » sur « consensus du jour seul », walk-forward) :", "",
                      "| Source ajoutée | Dates | Brier base → empilé | Avance Brier [IC 95 %] | Avance mailles qui changent [IC] | Avance mailles stables | Avance ligne km [IC] | Graines |",
                      "|---|---|---|---|---|---|---|---|"]
                for nm, v in k["empilement"].items():
                    ic, icc, ick = v.get("IC95_brier") or (None, None), v.get("IC95_chg") or (None, None), v.get("IC95_km") or (None, None)
                    L.append(f"| {nm} | {v.get('dates')} | {fmt(v.get('brier_base'), 5)} → {fmt(v.get('brier_empile'), 5)} | {pct(v['avance_brier'], 2)} "
                             f"[{pct(ic[0], 2)} ; {pct(ic[1], 2)}] (max {pct(v['avance_brier_max'], 2)}) | {pct(v['avance_chg'])} [{pct(icc[0])} ; {pct(icc[1])}] | "
                             f"{pct(v['avance_stables'])} | {pct(v['avance_km'])} [{pct(ick[0])} ; {pct(ick[1])}] | {v['graines']} |")
                L.append("")
            for nm, v in (k.get("net_placebo") or {}).items():
                L.append(f"**KPI NET (empilement − placebo à sens aléatoire), {nm}** ({v['dates']}, {v['n_dates']} dates) : Brier ≤ 30 km "
                         f"{pct(v['net_brier'], 2)} [{pct(v['IC95_brier'][0], 2)} ; {pct(v['IC95_brier'][1], 2)}] ; mailles qui changent "
                         f"{pct(v['net_chg'], 2)} [{pct(v['IC95_chg'][0], 2)} ; {pct(v['IC95_chg'][1], 2)}] ; ligne {pct(v['net_km'], 2)} "
                         f"[{pct(v['IC95_km'][0], 2)} ; {pct(v['IC95_km'][1], 2)}].")
                L.append("")
            if k.get("contrib_km_retrait"):
                L.append("Retrait d'une composante du pool (km de ligne, + = la composante aide) : "
                         + " ; ".join(f"{n} {fmt(v, 3)}" for n, v in sorted(k["contrib_km_retrait"].items())) + ".")
                L.append("")
            L += ["| Source | Dates | Brier ≤ 30 km | Log-loss | Brier changent | Brier stables | Ligne (km) | Avance Brier | Avance changent | Avance km |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
            for nm, v in blk["tableau"].items():
                L.append(f"| {nm} | {v.get('dates', '')} | {fmt(v.get('brier30'), 5)} | {fmt(v.get('logloss30'), 4)} | {fmt(v.get('brier_chg'), 4)} | "
                         f"{fmt(v.get('brier_stables'), 5)} | {fmt(v.get('km'), 3)} | {pct(v.get('avance_brier'))} | {pct(v.get('avance_chg'))} | {pct(v.get('avance_km'))} |")
            L.append("")
    L += ["## Détection (pas décadaire)", "", "| Seuil | Source | Événements | Anticipés | Avance moy. si anticipé (j) | Délai moyen signé (j) | Manqués 90 j | Départs | Taux de fausses | km² de départs / mois |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for th, dd in (("0,5", R["detection"]["seuil_0_5"]), ("0,8", R["detection"]["seuil_0_8"])):
        for nm, v in dd.items():
            L.append(f"| {th} | {nm} | {v['evenements']} | {pct(v['part_anticipee'])} | {fmt(v['avance_moy_j_si_anticipee'], 1)} | {fmt(v['delai_moyen_signe_j'], 1)} | "
                     f"{pct(v['part_manquee_90j'])} | {v['departs']} | {pct(v['taux_fausses'])} | {fmt(v['km2_departs_par_mois'], 0)} |")
    L.append("")
    L += ["## Par secteur (pas mensuel)", "", "| h | Période | Secteur | Source | Mailles ≤ 30 km | Brier ≤ 30 km | Mailles qui changent | Brier changent | Ligne (km) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sec_rows:
        L.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {fmt(r[5], 5)} | {r[6]} | {fmt(r[7], 4)} | {fmt(r[8], 3)} |")
    open(os.path.join(OUT, "resultats.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
