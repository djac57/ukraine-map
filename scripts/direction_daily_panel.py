"""Panneau QUOTIDIEN par tronçon de 24 km (socle commun des modèles « sens » et « ampleur » au jour, 20/10/2026).

Entrées (fichiers publiés du site + agrégats versionnés ; aucun texte, aucune unité, mailles de 2 km) :
- site/front.js, site/front_equilibre.js, site/front_asym.js : instantanés mensuels (fins de mois), P(mouvement) et
  asymétrie publiées ;
- site/front_hist_<année>.js + site/front_hist_<année>_equilibre.js (build_front.py, HIST=jours) : champ des cartes au JOUR
  2022-03-01 → J-1 (sans les fins de mois, lues dans la chaîne mensuelle) ;
- data/daily/<source>_<année>.csv.gz : Telegram (tronçons et mailles), état-major UA, MoD RF, UCDP, WarSpotting, lus
  CAUSALEMENT : un événement daté d0 publié d1 n'est visible qu'aux jours ≥ d1 (`date_pub`, `date_pub_min`) ;
  les jours non lus (fichiers de couverture) restent absents (pas des zéros) : les variables sont des z relatifs au passé
  du tronçon, la couverture Telegram étant stable (~65 % des canaux lus, phase 1 de la collecte en cours).

Sorties (`Panel`) : pour chaque jour d (indice) et tronçon k (tuile 12 × 12 mailles) :
  Y[d, t]      profondeur russe (km) = surface des mailles p > 0,5 / 24 km, NaN si la tuile n'est pas « traversée »
               (moins de 20 mailles à ≤ 20 km de la ligne) ;
  SRC[nom][d, t]  comptes du jour (date de l'événement), pour les fenêtres causales : voir `window(...)`.
Rien n'est écrit sur disque sauf le cache optionnel (PANEL_CACHE=<chemin .npz>).
"""
import base64
import csv
import datetime as dt
import glob
import gzip
import json
import os

import numpy as np
from scipy import ndimage

TILE = 12
D0 = dt.date(2022, 3, 1)
NEAR_KM = 20.0
# retards de publication regroupés (jours) : un événement de retard r est rangé dans la classe ≥ r (jamais vu trop tôt)
LAG_BINS = (0, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 61, 90, 180, 10 ** 6)


def say(*a):
    print(*a, flush=True)


def _load_js(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def _load_seq(path):
    s = open(path, encoding="utf-8").read()
    return json.loads(s[s.index("]=") + 2:].strip().rstrip(";"))


def _decode(arr, nU):
    out, prev = [], np.zeros(nU, np.uint8)
    for e in arr:
        if not e:
            out.append(None)
            continue
        b, q, o, i = base64.b64decode(e), np.zeros(nU, np.uint8), 0, 0
        while i < len(b):
            v = b[i]; i += 1
            n = sh = 0
            while True:
                c = b[i]; i += 1
                n |= (c & 127) << sh; sh += 7
                if not c & 128:
                    break
            q[o:o + n] = prev[o:o + n] if v == 255 else v
            o += n
        out.append(q)
        prev = q
    return out


def month_end(y, m):
    return dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)


class Panel:
    def __init__(self, root, cache=None, verbose=True):
        self.root = root
        self.site = os.path.join(root, "site")
        self.say = say if verbose else (lambda *a: None)
        if cache and os.path.exists(cache):
            self._load(cache)
            return
        self._build()
        if cache:
            self._save(cache)

    # ------------------------------------------------------------ construction
    def _build(self):
        site = self.site
        FR = _load_js(os.path.join(site, "front.js"))
        G = FR["grid"]
        GX, GY = G["nx"], G["ny"]
        self.grid, self.GX, self.GY = G, GX, GY
        b = base64.b64decode(FR["runs"])
        place = np.zeros(GX * GY, np.int32)
        o = 0
        for i in range(0, len(b), 3):
            v, n = b[i] | b[i + 1] << 8, b[i + 2]
            place[o:o + n] = -1 if v == 65535 else (-2 if v == 65534 else v)
            o += n
        UC = np.flatnonzero(place != -1)
        nU = len(UC)
        jj, ii = UC // GX, UC % GX
        tile = (jj // TILE) * 1000 + ii // TILE
        tiles, tinv = np.unique(tile, return_inverse=True)
        self.tiles, self.tpos = tiles, {int(k): i for i, k in enumerate(tiles)}
        NT = len(tiles)
        cellidx = np.full(GX * GY, -1, np.int64)
        cellidx[UC] = np.arange(nU)
        self.limit = dt.date.fromisoformat(FR.get("limit") or FR["months"][-1]["date"])

        # --- dates : jours de l'historique + fins de mois (chaîne mensuelle)
        frames = [(n, m["date"]) for n, m in enumerate(FR["months"]) if m and m.get("date") and m.get("kind", "m") == "m"]
        EQ = _decode(_load_seq(os.path.join(site, "front_equilibre.js")), nU)
        try:
            AS = _decode(_load_seq(os.path.join(site, "front_asym.js")), nU)
        except (OSError, ValueError):
            AS = [None] * len(EQ)
        src = {}      # date -> (fichier, indice) ou champ
        monthly = {}
        for n, d in frames:
            if n < len(EQ) and EQ[n] is not None and d >= D0.isoformat():
                monthly[dt.date.fromisoformat(d)] = n
        hist_files = []
        for path in sorted(glob.glob(os.path.join(site, "front_hist_[0-9][0-9][0-9][0-9]_equilibre.js"))):
            Y_ = os.path.basename(path)[11:15]
            H = json.loads(
                open(os.path.join(site, f"front_hist_{Y_}.js"), encoding="utf-8").read().split("]=", 1)[1].strip().rstrip(";"))
            hist_files.append((H["keys"], path))
        days = sorted(set(monthly) | {dt.date.fromisoformat(k) for keys, _ in hist_files for k in keys})
        days = [d for d in days if d >= D0]
        self.days = days
        self.di = {d: i for i, d in enumerate(days)}
        ND = len(days)
        self.say(f"panneau quotidien : {ND} jours ({days[0]} → {days[-1]}), {NT} tuiles, {len(monthly)} fins de mois, "
                 f"{sum(len(k) for k, _ in hist_files)} jours d'historique")

        # --- sources quotidiennes (mailles) : date -> listes
        self.src_names = []
        EV = {}   # (source, colonne) -> dict date -> list of (cellidx, lag, value)
        self._read_sources(EV, cellidx, tile, tinv)
        cols = sorted(EV)
        self.cols = cols

        # --- champ par jour, passe 1 : Y (profondeur russe) et tuiles « traversées » au moins une fois
        hist_iter = []
        for keys, path in hist_files:
            dec = _decode(_load_seq(path), nU)
            for k, q in zip(keys, dec):
                d = dt.date.fromisoformat(k)
                if q is not None and d in self.di:
                    hist_iter.append((d, q))
        for d in monthly:
            hist_iter.append((d, EQ[monthly[d]]))
        hist_iter.sort(key=lambda x: x[0])
        NTa = len(tiles)
        Ya = np.full((ND, NTa), np.nan, np.float32)
        GREYa = np.zeros((ND, NTa), np.float32)
        a2, wkm = 4.0, TILE * 2.0
        g = np.full((GY, GX), np.nan, np.float32)
        SD = []

        def field(q):
            peq = q.astype(np.float32) / 63.0
            g[jj, ii] = peq
            ru = g > 0.5
            ua = g < 0.5
            nb = np.zeros_like(ru)
            for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nb |= np.roll(ua, sh, axis=(0, 1))
            edge = ru & nb
            if not edge.any():
                return None, None
            dist = ndimage.distance_transform_edt(~edge) * 2.0
            return peq, np.where(ru, dist, -dist)[jj, ii]

        for d, q in hist_iter:
            i = self.di[d]
            peq, sd = field(q)
            if sd is None:
                continue
            near = np.abs(sd) <= NEAR_KM
            ok = np.bincount(tinv, weights=near, minlength=NTa) >= 20
            own = np.bincount(tinv, weights=(peq > 0.5), minlength=NTa) * a2 / wkm
            Ya[i, ok] = own[ok]
            GREYa[i] = np.bincount(tinv, weights=near & (peq >= 0.05) & (peq <= 0.95), minlength=NTa) * a2 / wkm
        keep = np.flatnonzero(np.isfinite(Ya).any(0))
        tiles = tiles[keep]
        remap = np.full(NTa, -1, np.int64)
        remap[keep] = np.arange(len(keep))
        tinv = remap[tinv]
        self.tiles, self.tpos = tiles, {int(k): i for i, k in enumerate(tiles)}
        NT = len(tiles)
        self.Y, self.GREY = Ya[:, keep], GREYa[:, keep]
        del Ya, GREYa
        self.say(f"  tuiles traversées au moins un jour : {NT}")
        # --- passe 2 : répartition des événements par côté de la ligne du jour (0 total à ≤ 20 km, 1 côté russe, 2 côté ukrainien)
        lagbin = np.array(LAG_BINS)
        LB = {}
        for c in cols:
            present = set()
            for ev in EV[c].values():
                present.update(np.unique(np.searchsorted(lagbin, ev[1])).tolist())
            LB[c] = sorted(present)
        self.LB = LB
        CNT = {c: np.zeros((3, len(LB[c]), ND, NT), np.float32) for c in cols}
        LBI = {c: {b: j for j, b in enumerate(LB[c])} for c in cols}
        for d, q in hist_iter:
            i = self.di[d]
            if not any(d in EV[c] for c in cols):
                continue
            peq, sd = field(q)
            if sd is None:
                continue
            for c in cols:
                ev = EV[c].get(d)
                if not ev:
                    continue
                ci, lg, va = ev
                t_ = tinv[ci]
                s_ = sd[ci]
                nr = (np.abs(s_) <= NEAR_KM) & (t_ >= 0)
                lb = np.array([LBI[c][x] for x in np.searchsorted(lagbin, lg)])
                A = CNT[c]
                np.add.at(A[0], (lb[nr], i, t_[nr]), va[nr])
                rs = nr & (s_ > 0)
                np.add.at(A[1], (lb[rs], i, t_[rs]), va[rs])
                us = nr & (s_ < 0)
                np.add.at(A[2], (lb[us], i, t_[us]), va[us])
        # cumuls sur le temps (par classe de retard) pour des fenêtres causales en O(1)
        self.CUM = {c: np.concatenate([np.zeros((3, len(LB[c]), 1, NT), np.float32), np.cumsum(CNT[c], axis=2)], axis=2) for c in cols}
        del CNT, hist_iter
        # --- publié mensuel : P(mouvement), asymétrie (fa_ru, fa_ua, mailles ≥ 0,4 côté russe deux mois)
        PM = np.full((len(frames), NT), np.nan, np.float32)
        FA = np.zeros((len(frames), NT, 3), np.float32)
        mdates = []
        for fi, (n, d) in enumerate(frames):
            mdates.append(dt.date.fromisoformat(d))
            for c, r, p in (FR["months"][n].get("pm") or []):
                k = (r // TILE) * 1000 + c // TILE
                if k in self.tpos:
                    PM[fi, self.tpos[k]] = p / 100.0
            if n < len(EQ) and EQ[n] is not None:
                a1 = AS[n] / 63.0 if n < len(AS) and AS[n] is not None else np.zeros(nU)
                a0 = AS[n - 1] / 63.0 if n > 0 and n - 1 < len(AS) and AS[n - 1] is not None else np.zeros(nU)
                ru_ = EQ[n] / 63.0 > 0.5
                nr_ = np.bincount(tinv, weights=ru_, minlength=NT)
                nu_ = np.bincount(tinv, weights=~ru_, minlength=NT)
                FA[fi, :, 0] = np.bincount(tinv, weights=a1 * ru_, minlength=NT) / np.maximum(nr_, 1)
                FA[fi, :, 1] = np.bincount(tinv, weights=a1 * ~ru_, minlength=NT) / np.maximum(nu_, 1)
                FA[fi, :, 2] = np.bincount(tinv, weights=ru_ & (a1 >= 0.4) & (a0 >= 0.4), minlength=NT)
        self.mdates, self.PM, self.FA = mdates, PM, FA
        self.ii, self.jj, self.tinv = ii, jj, tinv
        self.lon_lat = self._lonlat()

    def _read_sources(self, EV, cellidx, tile, tinv):
        """Lecture des agrégats quotidiens (mailles) ; colonnes retenues par source."""
        want = {
            "telegram": ["n_combat", "n_att_ua", "n_att_ru", "n_presence_ua", "n_presence_ru"],
            "etatmajor": ["assauts"],
            "modru": ["localites"],
            "ucdp": ["n_sol", "morts_best"],
            "warspotting": ["n_pertes"],
        }
        GX = self.GX
        for srcn, cols in want.items():
            files = sorted(glob.glob(os.path.join(self.root, "data", "daily", f"{srcn}_[0-9][0-9][0-9][0-9].csv.gz")))
            if not files:
                continue
            n = 0
            for path in files:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    rd = csv.reader(f)
                    h = next(rd)
                    hi = {c: i for i, c in enumerate(h)}
                    ic, ii_, ij_ = hi.get("camp_source"), hi["i"], hi["j"]
                    ipub = hi.get("date_pub", hi.get("date_pub_min"))
                    idate = hi["date"]
                    icols = [(c, hi[c]) for c in cols if c in hi]
                    buf = {}
                    for r in rd:
                        d = dt.date.fromisoformat(r[idate])
                        if d < D0 - dt.timedelta(days=120):
                            continue
                        lag = (dt.date.fromisoformat(r[ipub]) - d).days if ipub is not None else 0
                        lag = max(lag, 0)
                        ci = cellidx[int(r[ij_]) * GX + int(r[ii_])]
                        if ci < 0:
                            continue
                        camp = r[ic] if ic is not None else ""
                        for c, j in icols:
                            v = float(r[j] or 0)
                            if v == 0:
                                continue
                            key = f"{srcn}:{c}" + (f":{camp}" if srcn == "telegram" and camp in ("ru", "ua") else "")
                            buf.setdefault(key, {}).setdefault(d, []).append((ci, lag, v))
                            n += 1
                    for key, dd in buf.items():
                        E = EV.setdefault(key, {})
                        for d, lst in dd.items():
                            if d in E:
                                E[d] = tuple(np.concatenate([E[d][q], np.array([x[q] for x in lst])]) for q in range(3))
                            else:
                                E[d] = (np.array([x[0] for x in lst], np.int64), np.array([x[1] for x in lst], np.int64), np.array([x[2] for x in lst], np.float32))
            self.src_names.append(srcn)
            self.say(f"  {srcn} : {n} comptes")
        for key in EV:
            for d in EV[key]:
                v = EV[key][d]
                EV[key][d] = (np.asarray(v[0], np.int64), np.asarray(v[1], np.int64), np.asarray(v[2], np.float32))

    def _lonlat(self):
        G = self.grid
        R, l0, p0 = G["R"], np.radians(G["lon0"]), np.radians(G["lat0"])
        out = {}
        for k in self.tiles:
            x = G["x0"] + ((k % 1000) + 0.5) * TILE * G["size"]
            y = G["y1"] - ((k // 1000) + 0.5) * TILE * G["size"]
            rho = np.hypot(x, y)
            c = 2 * np.arcsin(rho / (2 * R))
            lat = np.arcsin(np.cos(c) * np.sin(p0) + y * np.sin(c) * np.cos(p0) / rho)
            lon = l0 + np.arctan2(x * np.sin(c), rho * np.cos(p0) * np.cos(c) - y * np.sin(p0) * np.sin(c))
            out[int(k)] = (float(np.degrees(lon)), float(np.degrees(lat)))
        return out

    # ------------------------------------------------------------ cache
    def _save(self, path):
        np.savez_compressed(path, LB=json.dumps(self.LB), days=np.array([d.isoformat() for d in self.days]), tiles=self.tiles, Y=self.Y, GREY=self.GREY,
                            mdates=np.array([d.isoformat() for d in self.mdates]), PM=self.PM, FA=self.FA, cols=np.array(self.cols),
                            limit=self.limit.isoformat(), grid=json.dumps(self.grid), src=json.dumps(self.src_names),
                            **{"CUM_" + c: self.CUM[c] for c in self.cols})

    def _load(self, path):
        Z = np.load(path)
        self.days = [dt.date.fromisoformat(str(x)) for x in Z["days"]]
        self.di = {d: i for i, d in enumerate(self.days)}
        self.tiles = Z["tiles"]
        self.tpos = {int(k): i for i, k in enumerate(self.tiles)}
        self.Y, self.GREY, self.PM, self.FA = Z["Y"], Z["GREY"], Z["PM"], Z["FA"]
        self.mdates = [dt.date.fromisoformat(str(x)) for x in Z["mdates"]]
        self.cols = [str(c) for c in Z["cols"]]
        self.limit = dt.date.fromisoformat(str(Z["limit"]))
        self.grid = json.loads(str(Z["grid"]))
        self.src_names = json.loads(str(Z["src"]))
        self.CUM = {c: Z["CUM_" + c] for c in self.cols}
        self.LB = json.loads(str(Z["LB"]))
        self.lon_lat = self._lonlat()

    # ------------------------------------------------------------ accès
    def window(self, col, i, w, side=0):
        """Somme CAUSALE des comptes `col` sur les jours (d − w, d] (d = self.days[i]), côté `side` (0 total à ≤ 20 km de la
        ligne, 1 russe, 2 ukrainien), vus au jour d : un événement du jour j publié avec un retard r n'est compté que
        si j + r ≤ d."""
        C = self.CUM[col][side]
        d = self.days[i]
        out = np.zeros(C.shape[2], np.float32)
        lo = self.index_before(d - dt.timedelta(days=w))
        for b, L in enumerate(self.LB[col]):
            L = LAG_BINS[L] if L < len(LAG_BINS) else 10 ** 6
            hi = self.index_before(d - dt.timedelta(days=L)) if L else i
            if hi <= lo:
                continue
            out += C[b, hi + 1] - C[b, lo + 1]
        return out

    def index_before(self, d):
        """Dernier indice de jour ≤ d (−1 si aucun)."""
        import bisect
        return bisect.bisect_right(self.days, d) - 1

    def y_at(self, d):
        """Profondeur par tuile au dernier jour ≤ d."""
        i = self.index_before(d)
        return self.Y[i] if i >= 0 else np.full(len(self.tiles), np.nan, np.float32)

    def month_index(self, d):
        """Indice du dernier instantané mensuel publié à la date d (fin de mois ≤ d), −1 si aucun."""
        import bisect
        return bisect.bisect_right(self.mdates, d) - 1
