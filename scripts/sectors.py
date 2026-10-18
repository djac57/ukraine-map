"""Mesures sectorielles à partir de NOS signaux (build_front.py) : zone de contact, z-scores, couche « combats intenses »,
variables du σ local.

- Rasters mensuels : feux en excès aux coordonnées des détections VIIRS (site/data.js, 0,01°, et non plus en mailles de
  0,1°), combats UCDP et pertes WarSpotting (mailles de 0,1°, leur précision propre).
- Zone de contact : pour chaque secteur (tuile de 50 km), profil de l'activité des trois derniers mois selon la distance
  signée à la ligne 50 % ; largeur de chaque côté = mi-hauteur du profil ; bande continue autour de la ligne ; pas de
  zone si le pic du secteur reste sous 3 fois le fond national (40-60 km de la ligne). Aux résolutions natives des
  signaux, le profil national a une largeur à mi-hauteur de 10 à 14 km ; un seuil « 2 fois le fond » sur des signaux
  lissés à 6 km donnait environ 70 km, faute de résolution.
- z-scores : chaque maille (lissée sur ~5 km, le « secteur ») jugée par rapport à ses 12 mois précédents (mois courant
  exclu) ; plancher de type Poisson pour les petits comptes. Les feux sont déjà désaisonnalisés (excès sur la normale
  du même mois 2012-2021).
- Combats intenses : moyenne des deux plus forts z de combat ≥ 2, à moins de 30 km de la ligne. Chaque secteur est jugé
  par rapport à sa propre normale (le Dnipro, actif mais stable, n'y apparaît plus).
"""
import csv
import json
import math
import os

import numpy as np
from scipy import ndimage

NAMES = ["feux", "ucdp", "pertes"]
REF = 12
TILE_ASYM = 12   # secteurs de 24 km (asymétrie des combats)
TILE = 25          # mailles de 2 km : tuile de 50 km
PKMIN = 3.0
WMAX = 25.0        # largeur maximale de chaque côté de la ligne (km)


class Sectors:
    def __init__(self, U, cellU, NX, NY, km, months, grid_index, root):
        self.U = np.asarray(U)
        self.cellU = np.asarray(cellU)
        self.NX, self.NY, self.km = NX, NY, km
        self.jj, self.ii = self.U // NX, self.U % NX
        self.isUA = self.cellU >= 0
        self.months = months
        self.pos = {int(k): n for n, k in enumerate(self.U)}
        self.grid_index = grid_index
        self.root = root
        self.AG = None   # radar (abandon agricole), chargé par sar()
        NM = len(months)
        D = json.loads(open(os.path.join(root, "site", "data.js"), encoding="utf-8").read()[len("window.DATA="):].rstrip().rstrip(";"))
        O = json.loads(open(os.path.join(root, "site", "osint.js"), encoding="utf-8").read()[len("window.OSINT="):].rstrip().rstrip(";"))
        self.R = np.zeros((3, NM, len(self.U)), np.float32)
        p = D["pts"]
        midx = {m: i for i, m in enumerate(months)}
        for j in range(0, len(p), 5):
            t = midx.get(D["months"][p[j + 2]])
            self._add(0, t, p[j + 1] / 100, p[j] / 100, 1.0)
        # combats UCDP au sol et artillerie (frappes à distance écartées, voir build_osint.py)
        self.G = np.zeros((NM, len(self.U)), np.float32)
        q = O["ucdp"].get("ground", [])
        for j in range(0, len(q), 5):
            t = q[j + 2]
            if 0 <= t < NM:
                u = self.pos.get(self.grid_index((q[j + 1] + 0.5) * 0.1, (q[j] + 0.5) * 0.1))
                if u is not None:
                    self.G[t, u] += float(q[j + 3])
        for n, key, wi in ((1, "ucdp", 3), (2, "ws", 4)):
            q = O[key]["pts"]
            last = -1
            for j in range(0, len(q), 5):
                self._add(n, q[j + 2], (q[j + 1] + 0.5) * 0.1, (q[j] + 0.5) * 0.1, float(q[j + wi]))
                last = max(last, q[j + 2])
            for t in range(last + 1, NM):   # fin de série : dernier mois reconduit
                if last >= 0:
                    self.R[n, t] = self.R[n, last]
        self.H = []
        for n in range(3):
            v = self.R[n][self.R[n] > 0]
            self.H.append(float(np.percentile(v, 90)) if len(v) else 1.0)
        self.SM = np.stack([np.stack([self.smooth(self.R[n, t], 2.5) for t in range(NM)]) for n in range(3)])
        self._sd = {}
        self.barrier = self._barrier()

    def _add(self, n, t, lon, lat, w):
        if t is None or t < 0 or t >= self.R.shape[1]:
            return
        k = self.grid_index(lon, lat)
        u = self.pos.get(k)
        if u is not None:
            self.R[n, t, u] += w

    def grid(self, v, fill=0.0):
        g = np.full((self.NY, self.NX), fill, np.float32)
        g[self.jj, self.ii] = v
        return g

    def smooth(self, v, sig):
        return ndimage.gaussian_filter(self.grid(v), sig)[self.jj, self.ii] if sig > 0 else v

    def activity(self, t, sig=1.0):
        """Activité fine (lissage 2 km) : moyenne des trois indicateurs normalisés."""
        return np.mean(np.stack([1 - np.exp(-self.smooth(self.R[n, t], sig) / self.H[n]) for n in range(3)]), axis=0)

    def asym(self, t, sd, art=None, thr=0.2):
        """« Combats derrière la ligne » : par secteur de 24 km, asymétrie a = (A_russe − A_ukrainien) / (A_russe + A_ukrainien)
        de l'activité à ≤ 10 km de la ligne 50 % (feux, UCDP, pertes, chacun normalisé ; signaux vus des deux côtés). Renvoie (champ par maille : |a| sur le côté dominant, à ≤ 10 km, là où |a| ≥ thr ; 0 ailleurs, signe
        porté par le côté de la maille), {tuile: a}."""
        X = [self.SM[n][t] for n in range(3)]
        if not hasattr(self, "_Hsm"):   # échelles sur les champs LISSÉS (les mêmes que X)
            self._Hsm = [float(np.percentile(self.SM[n][self.SM[n] > 0], 90)) if (self.SM[n] > 0).any() else 1.0 for n in range(3)]
        Hs = list(self._Hsm)
        # signaux observés des deux côtés seulement (feux, UCDP, pertes) : les menaces d'artillerie n'existent que côté
        # ukrainien et les assauts de l'état-major citent surtout des localités ukrainiennes ; les inclure biaisait
        # l'asymétrie vers le côté ukrainien (saillant de Vivaldi vide en juillet et septembre 2026)
        extra = []
        if not hasattr(self, "_Hx"):
            self._Hx = []
        for n, v in enumerate(extra):
            if len(self._Hx) <= n:
                src = art if n == 0 and art is not None else of["etatmajor_assauts"]
                pos = src[src > 0]
                self._Hx.append(float(np.percentile(pos, 90)) if len(pos) else 1.0)
            X.append(v); Hs.append(self._Hx[n])
        A = np.mean(np.stack([1 - np.exp(-x / max(h, 1e-6)) for x, h in zip(X, Hs)]), axis=0)
        near = np.abs(sd) <= 10
        # champ : asymétrie en fenêtre glissante (gaussienne de 12 km) plutôt que par tuile fixe, qui coupait les saillants
        # en morceaux (Vivaldi, septembre 2026 : 48 km² seulement avec les tuiles)
        ru_m, ua_m = (near & (sd > 0)).astype(np.float32), (near & (sd <= 0)).astype(np.float32)
        g = lambda v: self.smooth(v, 6.0)  # noqa: E731
        n_ru, n_ua = g(ru_m), g(ua_m)
        m_ru = g(A * ru_m) / np.maximum(n_ru, 1e-6)
        m_ua = g(A * ua_m) / np.maximum(n_ua, 1e-6)
        tot = m_ru + m_ua
        aw = np.where((tot >= 0.05) & (n_ru > 0.05) & (n_ua > 0.05), (m_ru - m_ua) / np.maximum(tot, 1e-6), 0.0).astype(np.float32)
        # version RELATIVE : on ne garde que les endroits où la concentration est ANORMALE pour le lieu, z de l'asymétrie
        # par rapport aux 12 mois précédents (au moins 4) ≥ 1,5. L'asymétrie absolue hachurait presque tout le côté russe
        # (frappes de drones et en profondeur) ; bilan 2023-2026 : voir la page méthode
        if not hasattr(self, "_aw"):
            self._aw = {}
        self._aw[t] = aw
        past = [self._aw[q] for q in range(t - 12, t) if q in self._aw]
        if len(past) >= 4:
            P_ = np.stack(past)
            z = (aw - P_.mean(0)) / np.maximum(P_.std(0), 0.2)
        else:
            z = np.zeros_like(aw)
        self.asym_z = z
        if not hasattr(self, "_az"):
            self._az = {}
        self._az[t] = z
        # anomalie : z ≥ 2 ce mois-ci ou dans les deux mois précédents (une concentration anormale qui dure reste
        # signalée même quand elle devient la nouvelle normale du lieu), et asymétrie actuelle toujours ≥ seuil
        if not hasattr(self, "_an"):
            self._an = {}
        self._an[t] = (np.abs(z) >= 2.0) & ((z > 0) == (aw > 0))
        anom = self._an[t] | self._an.get(t - 1, np.zeros_like(aw, bool)) | self._an.get(t - 2, np.zeros_like(aw, bool))
        fld = np.where(near & anom & (np.abs(aw) >= thr) & ((aw > 0) == (sd > 0)), np.abs(aw), 0.0).astype(np.float32)
        tile = (self.jj // TILE_ASYM) * 1000 + self.ii // TILE_ASYM
        out = np.zeros(len(sd), np.float32)
        tiles = {}
        for k in np.unique(tile[near]):
            mm = near & (tile == k)
            ru, ua = mm & (sd > 0), mm & (sd <= 0)
            if ru.sum() < 10 or ua.sum() < 10:
                continue
            a_ru, a_ua = float(A[ru].mean()), float(A[ua].mean())
            if a_ru + a_ua < 0.05:
                continue
            a = (a_ru - a_ua) / (a_ru + a_ua)
            tiles[int(k)] = round(a, 3)
        return fld, tiles

    def signed_dist(self, key, p):
        if key not in self._sd:
            g = self.grid(p, np.nan)
            ru, ua = g > 0.5, g < 0.5
            edge = np.zeros_like(ru)
            for sh in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                edge |= ru & np.roll(ua, sh, axis=(0, 1))
            d = ndimage.distance_transform_edt(~edge) * self.km
            self._sd[key] = (np.where(ru, d, -d)[self.jj, self.ii].astype(np.float32), edge)
        return self._sd[key]

    # ---------- zone de contact ----------
    def contact(self, t, P, pool=3):
        """P : {t: champ p sur U}. Renvoie (masque de zone, statistiques, profil national du mois)."""
        sd, edge = self.signed_dist(("c", t), P[t])
        A0 = self.activity(t)
        far = (np.abs(sd) >= 40) & (np.abs(sd) <= 60)
        bg = float(A0[far].mean()) if far.any() else 1e-3
        bins = np.arange(-40, 42, 2.0)
        mids = (bins[:-1] + bins[1:]) / 2
        tile = (self.jj // TILE) * 1000 + self.ii // TILE
        ts = [s for s in range(t - pool + 1, t + 1) if s in P]
        SDs = {s: self.signed_dist(("c", s), P[s])[0] for s in ts}
        As = {s: (A0 if s == t else self.activity(s)) for s in ts}
        wr = np.zeros(len(self.U), np.float32)
        wu = np.zeros(len(self.U), np.float32)
        tiles = []
        for tk in np.unique(tile[np.abs(sd) <= 2]):
            acc, cnt = np.zeros(len(mids)), np.zeros(len(mids))
            for s in ts:
                m = (tile == tk) & (np.abs(SDs[s]) <= 40)
                idx = np.clip(np.digitize(SDs[s][m], bins) - 1, 0, len(mids) - 1)
                acc += np.bincount(idx, weights=As[s][m], minlength=len(mids))
                cnt += np.bincount(idx, minlength=len(mids))
            prof = acc / np.maximum(cnt, 1)
            c0 = int(np.argmin(np.abs(mids)))
            im = max(0, c0 - 3) + int(np.argmax(prof[max(0, c0 - 3):c0 + 4]))
            pk = prof[im]
            j0, i0 = (tk // 1000) * TILE + TILE // 2, (tk % 1000) * TILE + TILE // 2
            if pk < PKMIN * bg:
                tiles.append((int(j0), int(i0), 0.0, 0.0))
                continue
            hi = im
            while hi < len(mids) - 1 and prof[hi + 1] > pk / 2:
                hi += 1
            lo = im
            while lo > 0 and prof[lo - 1] > pk / 2:
                lo -= 1
            # au plus 25 km de chaque côté : au-delà, des feux ou des combats sont des frappes à distance (villes)
            r, u = min(WMAX, max(0.0, float(mids[hi] + 1))), min(WMAX, max(0.0, float(-(mids[lo] - 1))))
            m = tile == tk
            wr[m], wu[m] = r, u
            tiles.append((int(j0), int(i0), round(r, 1), round(u, 1)))
        zone = ((sd > 0) & (sd <= wr)) | ((sd <= 0) & (-sd <= wu))
        zone &= np.abs(sd) <= 40
        a2 = self.km ** 2
        line_km = float(edge.sum()) * self.km * math.pi / 4
        act = [x for x in tiles if x[2] + x[3] > 0]
        st = {"km2": round(float(zone.sum() * a2)), "w_ru": round(float(np.median([x[2] for x in act])) if act else 0.0, 1),
              "w_ua": round(float(np.median([x[3] for x in act])) if act else 0.0, 1), "tiles": tiles,
              "share": round(len(act) / max(len(tiles), 1), 2)}
        # profil national du mois, de −60 à +60 km par pas de 4 km (page méthode)
        b2 = np.arange(-60, 62, 4.0)
        sel = np.abs(sd) <= 60
        ix = np.clip(np.digitize(sd[sel], b2) - 1, 0, len(b2) - 2)
        nb = np.bincount(ix, weights=A0[sel], minlength=len(b2) - 1)
        nn = np.bincount(ix, minlength=len(b2) - 1)
        return zone, st, (nb / np.maximum(nn, 1)).tolist(), line_km

    # ---------- P(contact au sol) ----------
    def urban(self, places):
        """Masque urbain : rayon 3·√(population / 100 000) km autour des localités d'au moins 20 000 habitants."""
        g = np.zeros((self.NY, self.NX), bool)
        yy, xx = np.mgrid[0:self.NY, 0:self.NX]
        for pl in places:
            if len(pl) < 4 or (pl[3] or 0) < 20000:
                continue
            k = self.grid_index(pl[0], pl[1])
            if k < 0:
                continue
            r = max(1.0, 3 * (pl[3] / 1e5) ** 0.5) / self.km
            j0, i0 = divmod(k, self.NX)
            sl = (slice(max(0, int(j0 - r - 1)), int(j0 + r + 2)), slice(max(0, int(i0 - r - 1)), int(i0 + r + 2)))
            g[sl] |= (yy[sl] - j0) ** 2 + (xx[sl] - i0) ** 2 <= r * r
        return g[self.jj, self.ii].astype(np.float32)

    PC_NAMES = ["|distance|", "côté", "UCDP au sol", "part au sol", "pertes", "feux", "z feux", "urbain", "continuité", "urbain × distance",
                "radar : abandon agricole (côté russe)", "radar : abandon agricole (côté ukrainien)", "radar : pas de cultures en 2021",
                "état-major UA (faits) : assauts", "état-major UA (faits) : frappes aériennes", "MoD RF (faits) : localités des bilans",
                "liste officielle des hostilités (rejouée)", "liste officielle : pas encore publiée"]

    def official_list(self):
        """Part des localités de la hromada en « hostilités actives » d'après la liste officielle ukrainienne, telle qu'elle
        était CONNUE à la fin de chaque mois (versions de l'arrêté rejouées : data/liste_hostilites_hromada_month.csv,
        hromadas → mailles : data/hromada_cells.json, limites OCHA COD-AB CC BY-IGO). Avant décembre 2022, aucune liste
        n'était publiée (indicatrice) ; après le dernier mois rejoué, l'état connu est reporté."""
        L = np.zeros((len(self.months), len(self.U)), np.float32)
        M = np.zeros(len(self.months), np.float32)
        pl, ph = os.path.join(self.root, "data", "liste_hostilites_hromada_month.csv"), os.path.join(self.root, "data", "hromada_cells.json")
        if not (os.path.exists(pl) and os.path.exists(ph)):
            self.LIST, self.LMISS = L, np.ones(len(self.months), np.float32)
            return
        H = json.load(open(ph, encoding="utf-8"))
        cells = {k: [self.pos[c] for c in v if c in self.pos] for k, v in H["mailles"].items()}
        midx = {m: i for i, m in enumerate(self.months)}
        with open(pl, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = midx.get(r["mois"])
                if t is not None and cells.get(r["pcode"]):
                    L[t, cells[r["pcode"]]] = float(r["part_active_connue"])
        first, last = midx.get(H["premier_mois_connu"], 0), midx.get(H["dernier_mois"], len(self.months) - 1)
        for t in range(len(self.months)):
            if t < first:
                M[t] = 1.0
            elif t > last:
                L[t] = L[last]
        self.LIST, self.LMISS = L, M
    OFFICIAL = ("etatmajor_assauts", "etatmajor_frappes", "modru_localites")

    def official(self):
        """Faits officiels des bilans quotidiens (data/officiels_cells_month.csv, build_official_signals.py) :
        {source: tableau (mois, mailles)} par jour couvert."""
        O = {k: np.zeros((len(self.months), len(self.U)), np.float32) for k in self.OFFICIAL}
        path = os.path.join(self.root, "data", "officiels_cells_month.csv")
        if os.path.exists(path):
            midx = {m: i for i, m in enumerate(self.months)}
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    t, u = midx.get(r["mois"]), self.pos.get(int(r["maille"]))
                    if t is not None and u is not None and r["source"] in O:
                        O[r["source"]][t, u] += float(r["par_jour"])
        self.OF = O
        return O
    SAR_ZONE_KM = 15.0   # fenêtre de la mise à jour mensuelle (scripts/sar_update.py --radius 15)

    def sar(self):
        """Amplitude du cycle agricole (dB) par maille et par mois, data/sar_cells.csv (Copernicus Sentinel-1) ; NaN sinon."""
        A = np.full((len(self.months), len(self.U)), np.nan, np.float32)
        self.BD = np.full((len(self.months), len(self.U)), np.nan, np.float32)   # bâti modifié depuis 2021 (part)
        self.BS = np.zeros(len(self.U), np.float32)                               # part de bâti (WorldCover 2021)
        path = os.path.join(self.root, "data", "sar_cells.csv")
        if not os.path.exists(path):
            return A
        midx = {m: i for i, m in enumerate(self.months)}
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t, u = midx.get(r["month"]), self.pos.get(int(r["cell"]))
                if t is None or u is None:
                    continue
                if r["agri_amp_db"] != "":
                    A[t, u] = float(r["agri_amp_db"])
                if r["built_damage_since_2021"] != "":
                    self.BD[t, u] = float(r["built_damage_since_2021"])
                if r.get("built_share"):
                    self.BS[u] = max(self.BS[u], float(r["built_share"]))
        return A

    def sar_features(self, t, sd):
        """Abandon agricole en z du mois, calculé sur les seules mailles à ≤ 15 km de la ligne du mois t. L'historique
        radar a été lu sur des tuiles choisies d'après la ligne de toute la période : hors de cette fenêtre, la présence
        d'une donnée annoncerait l'arrivée future du front ; dans la fenêtre, la lecture est systématique."""
        zone = np.abs(sd) <= self.SAR_ZONE_KM
        raw = np.where(zone, self.AG[t], np.nan) if self.AG is not None else np.full(len(sd), np.nan, np.float32)
        v = raw[np.isfinite(raw)]
        if len(v) > 50:
            q1, q2, q3 = np.percentile(v, [25, 50, 75])
            raw = (raw - q2) / max(q3 - q1, 1e-3)
        ag = self.smooth(np.nan_to_num(raw, nan=0.0), 1.5)
        miss = (zone & ~np.isfinite(raw)).astype(np.float32) if len(v) > 50 else np.zeros(len(sd), np.float32)
        ru = (sd > 0).astype(np.float32)
        return np.c_[ag * ru, ag * (1 - ru), miss]

    def artillery(self, carry=0):
        """Heures de « menace d'artillerie » par maille et par mois (canal officiel des alertes, agrégé par hromada puis
        par maille ; data/alertes_artillerie.csv). Le canal s'est tu le 7 septembre 2026 (aperçu public et jeu de données
        miroir lu par l'API) : rien n'est reporté au-delà (carry = 0) ; sans alerte, P(contact) est celle du modèle sans alertes."""
        A = np.zeros((len(self.months), len(self.U)), np.float32)
        path = os.path.join(self.root, "data", "alertes_artillerie.csv")
        if not os.path.exists(path):
            return A
        midx = {m: i for i, m in enumerate(self.months)}
        last = -1
        with open(path, encoding="utf-8") as f:
            next(f)
            for line in f:
                mo, k, h = line.rstrip("\n").split(",")
                t, u = midx.get(mo), self.pos.get(int(k))
                if t is not None:
                    last = max(last, t)
                    if u is not None:
                        A[t, u] += float(h)
        for t in range(last + 1, min(len(self.months), last + 1 + carry)):
            A[t] = A[last]
        return A

    def pc_features(self, t, sd, ground, urb, zf):
        sm = lambda v: self.smooth(v, 1.5)  # noqa: E731
        g, ua, w, f = sm(ground[t]), sm(self.R[1, t]), sm(self.R[2, t]), sm(self.R[0, t])
        act = (np.log1p(g) + np.log1p(w) + np.log1p(f)) > 0.05
        lab, _ = ndimage.label(self.grid((act | (np.abs(sd) <= 2)).astype(np.float32)) > 0)
        lu = lab[self.jj, self.ii]
        keep = np.unique(lu[np.abs(sd) <= 2])
        cont = np.isin(lu, keep[keep > 0]).astype(np.float32)
        a = np.clip(np.abs(sd), 0, 60) / 10
        X = np.stack([a, np.clip(sd, -60, 60) / 30, np.log1p(g), g / (ua + 0.05), np.log1p(w), np.log1p(f),
                         np.clip(zf, -3, 10) / 3, urb, cont, urb * a], axis=1).astype(np.float32)
        of = getattr(self, "OF", None) or {}
        offc = [np.log1p(self.smooth(of[k][t], 2.5)) if k in of else np.zeros(len(sd), np.float32) for k in self.OFFICIAL]
        lst = getattr(self, "LIST", None)
        lf = np.c_[self.smooth(lst[t], 1.5), np.full(len(sd), self.LMISS[t], np.float32)] if lst is not None else np.zeros((len(sd), 2), np.float32)
        return np.c_[X, self.sar_features(t, sd), np.stack(offc, axis=1), lf].astype(np.float32)

    PC_GROUPS = {"UCDP au sol": [2, 3], "pertes WarSpotting": [4], "feux VIIRS": [5, 6], "urbain et continuité": [7, 8, 9],
                 "radar (abandon agricole)": [10, 11, 12], "état-major UA (faits)": [13, 14], "MoD RF (faits)": [15],
                 "liste officielle (rejouée)": [16, 17]}

    def pcontact(self, ts, SD, ground, urb, Zf, truth, dis, art, start_wf="2023-06", drop=(), use_art=True):
        """P(contact au sol) par maille et par mois, régression logistique réentraînée chaque mois sur les seuls mois
        dont la vérité est connue (t' ≤ t − 3) ; avant la période walk-forward, ajustement sur tout l'historique
        étiqueté (indiqué). Vérité (proxy) : la maille change de camp dans les 3 mois, ou les cartes s'y contredisent."""
        rng = np.random.default_rng(0)
        if not hasattr(self, "_pcF"):
            self._pcF = {t: self.pc_features(t, SD[t], ground, urb, Zf[t]) for t in ts}
        keep = [j for j in range(len(self.PC_NAMES)) if not any(j in self.PC_GROUPS[g] for g in drop)]
        F = {t: self._pcF[t][:, keep] for t in ts}
        # menaces d'artillerie : indice « certain mais rare », en deuxième étape (voir plus bas)
        Ha = {t: self.smooth(art[t], 1.5) for t in ts}
        Za = {t: np.c_[np.log1p(Ha[t]), (Ha[t] > 0.05).astype(np.float32)] for t in ts}
        L = {}
        for t in ts:
            if t + 3 in truth:
                a, b = truth[t], truth[t + 3]
                L[t] = (((a >= 0) & (b >= 0) & (a != b)) | (dis[t] == 1)).astype(np.float32)

        def fit(train):
            Xs, Ys = [], []
            for s in train[-18:]:
                idx = np.flatnonzero(np.abs(SD[s]) <= 60)
                idx = rng.choice(idx, size=min(6000, len(idx)), replace=False)
                Xs.append(F[s][idx]); Ys.append(L[s][idx])
            Xa, Ya = np.concatenate(Xs), np.concatenate(Ys)
            mu, sdv = Xa.mean(0), Xa.std(0) + 1e-9
            X = np.c_[np.ones(len(Xa)), (Xa - mu) / sdv]
            w = np.zeros(X.shape[1])
            for _ in range(15):
                p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
                g = X.T @ (Ya - p) - np.r_[0, w[1:]]
                H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(np.r_[0, np.ones(X.shape[1] - 1)]) + 1e-6 * np.eye(X.shape[1])
                w += np.linalg.solve(H, g)
            return w, mu, sdv
        labelled = [t for t in ts if t in L]
        full = fit(labelled) if labelled else None
        out, wf, base = {}, {}, {}
        sc, ys, dd = [], [], []
        self.pc_boost = {}

        def boost(train):
            """logit P = logit P_base (décalage fixe, modèle sans alertes ajusté sur son propre passé) + γ·log1p(h) + δ·1[h > 0],
            γ, δ ≥ 0 : une alerte relève P, son absence ne le change pas (P = P_base exactement)."""
            bl, zz, yy = [], [], []
            for s in train[-12:]:
                if s not in base:
                    continue
                m = np.abs(SD[s]) <= 60
                idx = rng.choice(np.flatnonzero(m), size=min(6000, int(m.sum())), replace=False)
                bl.append(base[s][idx]); zz.append(Za[s][idx]); yy.append(L[s][idx])
            if not bl:
                return np.zeros(2)
            b0, Zt, Yt = np.concatenate(bl), np.concatenate(zz), np.concatenate(yy)
            g = np.zeros(2)
            for _ in range(25):
                p = 1 / (1 + np.exp(-np.clip(b0 + Zt @ g, -30, 30)))
                gr = Zt.T @ (Yt - p) - 0.1 * g
                Hs = (Zt * (p * (1 - p))[:, None]).T @ Zt + 0.1 * np.eye(2)
                g = np.maximum(0, g + np.linalg.solve(Hs, gr))
            return g
        for t in ts:
            tr = [s for s in labelled if s <= t - 3]
            in_wf = self.months[t] >= start_wf and len(tr) >= 6
            w, mu, sdv = fit(tr) if in_wf else full
            Z = (F[t] - mu) / sdv
            base[t] = w[0] + Z @ w[1:]
            g = boost(tr) if in_wf and use_art else np.zeros(2)
            self.pc_boost[t] = g
            out[t] = 1 / (1 + np.exp(-np.clip(base[t] + Za[t] @ g, -30, 30)))
            wf[t] = in_wf
            # composition : contribution de chaque signal de combat au logit (UCDP au sol, pertes, feux)
            if in_wf and t in L:
                m = np.abs(SD[t]) <= 60
                sc.append(out[t][m]); ys.append(L[t][m]); dd.append(np.abs(SD[t][m]))
        if drop or not use_art:
            return self._auc(sc, ys, dd)
        self.pc_coef = dict(zip(["const"] + self.PC_NAMES, [round(float(x), 3) for x in full[0]])) if full else {}
        last = [t for t in ts if wf.get(t)]
        if last:
            g = self.pc_boost[last[-1]]
            self.pc_coef["menace d'artillerie (étape 2) : log1p(h)"] = round(float(g[0]), 3)
            self.pc_coef["menace d'artillerie (étape 2) : présence"] = round(float(g[1]), 3)
        auc = None
        if sc:
            s, y = np.concatenate(sc), np.concatenate(ys)
            o = np.argsort(s)
            r = np.empty(len(s)); r[o] = np.arange(1, len(s) + 1)
            n1, n0 = y.sum(), len(y) - y.sum()
            auc = float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / max(n1 * n0, 1))
        self.pc_auc = auc
        self.pc_auc_eq = self._auc(sc, ys, dd)[1] if sc else None
        return out, wf

    @staticmethod
    def _auc(sc, ys, dd):
        """(AUC, AUC à distance égale : moyenne pondérée des AUC dans les tranches 0-3, 3-6, 6-10, 10-20 km)."""
        def auc(s, y):
            o = np.argsort(s)
            r = np.empty(len(s)); r[o] = np.arange(1, len(s) + 1)
            n1, n0 = y.sum(), len(y) - y.sum()
            return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / max(n1 * n0, 1))
        s, y, d = np.concatenate(sc), np.concatenate(ys), np.concatenate(dd)
        w = [(auc(s[(d >= a) & (d < b)], y[(d >= a) & (d < b)]), int(((d >= a) & (d < b)).sum())) for a, b in ((0, 3), (3, 6), (6, 10), (10, 20))]
        return auc(s, y), sum(a * n for a, n in w) / max(sum(n for _, n in w), 1)

    # ---------- z-scores ----------
    def z(self, t, ts_ok):
        """z des trois indicateurs de combat au mois t, par rapport aux 12 mois précédents disponibles (t exclu)."""
        ref = [s for s in range(max(0, t - REF), t) if s in ts_ok]
        if len(ref) < 4:
            return np.zeros((3, len(self.U)), np.float32)
        out = []
        for n in range(3):
            R = self.SM[n, ref]
            mu, sd = R.mean(0), R.std(0)
            floor = 0.5 * np.sqrt(mu + 1e-3) + 0.02 * max(float(R.max()), 1e-3)
            out.append((self.SM[n, t] - mu) / np.maximum(sd, floor))
        return np.stack(out).astype(np.float32)

    def intense(self, Z, sd):
        top2 = np.sort(Z, axis=0)[-2:].mean(0)
        return (top2 >= 2.0) & (np.abs(sd) <= 30), top2

    # ---------- σ local ----------
    def _barrier(self):
        """Distance (km) au Dnipro et au réservoir de Kakhovka (Natural Earth, site/geo.js)."""
        G = json.loads(open(os.path.join(self.root, "site", "geo.js"), encoding="utf-8").read()[len("window.GEO="):].rstrip().rstrip(";"))
        mask = np.zeros((self.NY, self.NX), bool)
        lines = []
        for f in G.get("rivers", []):
            cs = f["geometry"]["coordinates"]
            parts = cs if f["geometry"]["type"] == "MultiLineString" else [cs]
            if any(32 < c[0] < 35.5 and 46.3 < c[1] < 47.9 for part in parts for c in part):
                lines += parts
        for f in G.get("lakes", []):
            cs = f["geometry"]["coordinates"]
            polys = cs if f["geometry"]["type"] == "MultiPolygon" else [cs]
            for poly in polys:
                ring = poly[0]
                if any(33 < c[0] < 35.5 and 46.5 < c[1] < 48 for c in ring):
                    lines.append(ring)
        for part in lines:
            for (x0, y0), (x1, y1) in zip(part, part[1:]):
                n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 0.01))
                for s in range(n + 1):
                    k = self.grid_index(x0 + (x1 - x0) * s / n, y0 + (y1 - y0) * s / n)
                    if k >= 0:
                        mask[k // self.NX, k % self.NX] = True
        if not mask.any():
            return np.full(len(self.U), 999.0, np.float32)
        return (ndimage.distance_transform_edt(~mask) * self.km)[self.jj, self.ii].astype(np.float32)

    def volatility(self, cons_hist):
        """cons_hist : liste des consensus par localité (indice de localité → 1/0/−1) des 7 derniers instantanés.
        Nombre de bascules du consensus sur 6 mois, par maille, lissé sur ~5 km."""
        flips = np.zeros(len(self.cellU), np.float32)
        ok = self.cellU >= 0
        for a, b in zip(cons_hist, cons_hist[1:]):
            aa, bb = np.asarray(a), np.asarray(b)
            ch = (aa >= 0) & (bb >= 0) & (aa != bb)
            flips[ok] += ch[self.cellU[ok]]
        return self.smooth(flips, 2.5)

    def pmove(self, tsm, SD, truth, pnow, ind=None, tg=None, win=20, thr_km=0.5):
        """« Secteurs susceptibles de bouger » : par tronçon de 24 km traversé par la ligne, P(la ligne bougera d'au moins
        0,5 km de profondeur d'ici t+2), SANS sens. Position Y = surface russe du tronçon (consensus des cartes ; à défaut,
        champ du mois > 0,5) / 24 km. Variables à t, moyennes à ≤ 20 km de la ligne : assauts de l'état-major, asymétrie
        des combats côté russe et son z relatif, feux, UCDP, pertes (normalisés), mouvement récent |Y_t − Y_t−1| et
        |Y_t − Y_t−3|. Régression logistique (ridge) réentraînée chaque mois sur les tronçons-mois q ≤ t−2 des 20 mois
        précédents (vérité à q+2 connue à t) : walk-forward pour l'historique, même règle pour le dernier mois.
        Telegram et la liste rejouée, testés hors ligne (+0,003 d'AUC), ne sont pas disponibles en CI : écartés.
        Renvoie ({t: {tuile: P}}, AUC walk-forward 2023+, taux de base, n)."""
        T_ = TILE_ASYM
        tile = (self.jj // T_) * 1000 + self.ii // T_
        tid = {int(k): np.flatnonzero(tile == k) for k in np.unique(tile)}
        wkm = T_ * self.km
        # feux EN EXCÈS, UCDP, pertes : indicateurs du nowcast (indicator_grids, grille complète) quand ils sont fournis,
        # comme dans l'étude v4 ; à défaut, les comptes bruts de self.R
        if ind is not None:
            # lissage σ = 3 mailles (6 km) avant l'échelle P90, comme les indicateurs de l'étude v4 (data/cache.npz) :
            # les comptes ponctuels donnaient AUC 0,78 au lieu de 0,82
            RI = np.stack([np.stack([self.smooth(np.asarray(ind[t][n], np.float32)[self.U], 3.0) if t in ind else np.zeros(len(self.U), np.float32) for t in range(len(self.months))]) for n in range(3)])
        else:
            RI = self.R
        Hn = [float(np.percentile(RI[n][RI[n] > 0], 90)) if (RI[n] > 0).any() else 1.0 for n in range(3)]
        Y, F = {}, {}
        for t in tsm:
            src = truth.get(t)
            own = (src == 1) if src is not None and (src >= 0).sum() > 0 else (pnow[t] > 0.5)
            known = (src >= 0) if src is not None and (src >= 0).sum() > 0 else np.ones(len(self.U), bool)
            sd = SD[t]
            for k, us in tid.items():
                if (np.abs(sd[us]) <= 20).sum() >= 20 and known[us].sum() >= 20:
                    Y[(t, k)] = float(own[us].sum()) * self.km ** 2 / wkm
        for t in tsm:
            sd = SD[t]
            if t not in self._aw:
                continue
            gs = np.log1p(self.smooth(self.OF["etatmajor_assauts"][t], 2.5))
            aw, az = self._aw[t], np.clip(self._az.get(t, np.zeros(len(self.U))), -5, 5)
            sig = [1 - np.exp(-self.smooth(RI[n, t], 2.0) / Hn[n]) for n in range(3)]
            ru = sd > 0
            for k, us in tid.items():
                if (t, k) not in Y:
                    continue
                nr = us[np.abs(sd[us]) <= 20]
                r1 = abs(Y[(t, k)] - Y[(t - 1, k)]) if (t - 1, k) in Y else 0.0
                r3 = abs(Y[(t, k)] - Y[(t - 3, k)]) if (t - 3, k) in Y else 0.0
                f = [gs[nr].mean(), (aw[nr] * ru[nr]).mean(), az[nr].mean(), sig[0][nr].mean(), sig[1][nr].mean(), sig[2][nr].mean(), r1, r3]
                if tg is not None:   # Telegram : mentions de combat localisées dans le tronçon (signal de MOUVEMENT, pas de sens)
                    f.append(np.log1p(tg.get((self.months[t], k), 0.0)))
                F[(t, k)] = np.array(f)

        def logi(X, y, lam=3.0):
            X = np.c_[np.ones(len(X)), X]
            w = np.zeros(X.shape[1])
            for _ in range(25):
                p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
                w += np.linalg.solve((X * (p * (1 - p))[:, None]).T @ X + lam * np.diag(np.r_[0, np.ones(X.shape[1] - 1)]), X.T @ (y - p) - lam * np.r_[0, w[1:]])
            return w
        out, sc, ys = {}, [], []
        for t in tsm:
            tr = [(q, k) for (q, k) in F if t - win <= q <= t - 2 and (q + 2, k) in Y]
            te = [(q, k) for (q, k) in F if q == t]
            if len(tr) < 300 or not te:
                continue
            Xr = np.array([F[x] for x in tr])
            yr = np.array([abs(Y[(q + 2, k)] - Y[(q, k)]) >= thr_km for q, k in tr], float)
            mu, sdv = Xr.mean(0), Xr.std(0) + 1e-9
            w = logi((Xr - mu) / sdv, yr)
            out[t] = {}
            for x in te:
                p = float(1 / (1 + np.exp(-np.clip(w[0] + ((F[x] - mu) / sdv) @ w[1:], -30, 30))))
                out[t][x[1]] = p
                if self.months[t] >= "2023-01" and (t + 2, x[1]) in Y:
                    sc.append(p); ys.append(abs(Y[(t + 2, x[1])] - Y[x]) >= thr_km)
        auc = base = None
        if ys and 0 < sum(ys) < len(ys):
            s_, y_ = np.array(sc), np.array(ys, float)
            o = np.argsort(s_); rk = np.empty(len(s_)); rk[o] = np.arange(1, len(s_) + 1); n1 = y_.sum()
            auc, base = float((rk[y_ == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y_) - n1))), float(y_.mean())
        self.pm_tile = tile
        return out, auc, base, len(ys)
