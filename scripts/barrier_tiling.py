"""Pavage des localités contraint par les barrières dures (19/10/2026).

Le pavage VIINA (Voronoï des localités, `gn_UA_tess.geojson`) ignore le relief : dans une large plaine inondable
(plavni du Dnipro en aval de Kakhovka, 5 à 10 km sans village), des mailles d'une rive tombent dans le polygone d'un
village de l'autre rive et prennent son statut. Règle : une maille ne reçoit le statut que d'une localité de SA rive.

- Barrières : mailles « fleuve ≥ 40 m³/s ou plan d'eau » de `site/barrieres_dures.js` (code 1 ; Dnipro et réservoirs,
  Siverskyi Donets, Oskil aval…), interstices diagonaux bouchés pour que la barrière soit étanche en 4-connexité.
  Escarpements et canaux (codes 2 et 4) ne comptent pas : ils se franchissent.
- Une maille hors barrière dont le segment vers sa localité (point VIINA, ramené sur la maille hors barrière la plus
  proche s'il tombe dans le fleuve) traverse une barrière est réattribuée à la localité la plus proche EN DISTANCE
  GÉODÉSIQUE sans traverser de barrière (Dijkstra multi-sources, chanfrein 3-4, pas de coin coupé).
- Rien d'autre ne change : les mailles du fleuve gardent leur localité ; une localité garde son statut, même s'il est
  celui du camp adverse de sa rive (tête de pont : Krynky 2023-2024), et le transmet aux mailles de sa rive.
- `blocked(...)` sert aussi aux noyaux d'annonces : un noyau autour d'une localité ne déborde pas sur l'autre rive.
"""
import base64
import heapq
import math
import os

import numpy as np

CODE_FLEUVE = 1


def load_mask(root, nx, ny, codes=CODE_FLEUVE):
    """Masque (ny*nx,) bool des mailles dures dont le code a un bit dans `codes`."""
    path = os.path.join(root, "site", "barrieres_dures.js")
    if not os.path.exists(path):
        return None
    import json
    src = open(path, encoding="utf-8").read()
    H = json.loads(src[src.index("{"):src.rindex("}") + 1])
    assert H["nx"] == nx and H["ny"] == ny, "barrières : grille différente"
    v = np.frombuffer(base64.b64decode(H["packed"]), dtype="<u4")
    M = np.zeros(nx * ny, bool)
    sel = (v & 7 & codes) > 0
    M[(v[sel] >> 3).astype(np.int64)] = True
    return close_diagonals(M.reshape(ny, nx)).ravel()


def close_diagonals(M):
    """Bouche les passages diagonaux (deux mailles dures en coin, les deux autres libres) : barrière 4-étanche."""
    M = M.copy()
    a, b = M[:-1, :-1], M[1:, 1:]
    c, d = M[:-1, 1:], M[1:, :-1]
    g1 = a & b & ~c & ~d          # diagonale « \ »
    M[:-1, 1:] |= g1
    g2 = c & d & ~a & ~b          # diagonale « / »
    M[:-1, :-1] |= g2
    return M


class BarrierTiling:
    def __init__(self, mask, nx, ny, place_ij):
        """mask : (ny*nx,) bool ; place_ij : liste de (i, j) flottants (coordonnées de grille) du point de chaque localité."""
        self.M, self.nx, self.ny = mask, nx, ny
        self.pt = place_ij
        self.anchor = [self._anchor(i, j) for i, j in place_ij]
        self._dB = None

    def _anchor(self, fi, fj):
        """Maille hors barrière la plus proche du point (la sienne si elle n'est pas dure), −1 hors grille."""
        i0, j0 = int(math.floor(fi)), int(math.floor(fj))
        if not (0 <= i0 < self.nx and 0 <= j0 < self.ny):
            return -1
        if not self.M[j0 * self.nx + i0]:
            return j0 * self.nx + i0
        best, bd = -1, 1e9
        for r in range(1, 6):
            for dj in range(-r, r + 1):
                for di in range(-r, r + 1):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < self.nx and 0 <= j < self.ny and not self.M[j * self.nx + i]:
                        d = (i + 0.5 - fi) ** 2 + (j + 0.5 - fj) ** 2
                        if d < bd:
                            best, bd = j * self.nx + i, d
            if best >= 0:
                return best
        return j0 * self.nx + i0

    def cut(self, k, q):
        """Comme blocked, avec un raccourci loin des barrières et un cache (noyaux d'annonces, appelés chaque mois)."""
        if self._dB is None:
            from scipy import ndimage
            self._dB = ndimage.distance_transform_edt(~self.M.reshape(self.ny, self.nx)).ravel()
            self._cut = {}
        j0, i0 = divmod(k, self.nx)
        j1, i1 = divmod(q, self.nx)
        if self._dB[k] > math.hypot(j1 - j0, i1 - i0) + 1.5:
            return False
        key = (k, q)
        v = self._cut.get(key)
        if v is None:
            v = self._cut[key] = self.blocked(k, q)
        return v

    def blocked(self, k, q):
        """Le segment entre les centres des mailles k et q traverse-t-il une maille dure (extrémités exclues) ?"""
        nx, M = self.nx, self.M
        j0, i0 = divmod(k, nx)
        j1, i1 = divmod(q, nx)
        di, dj = i1 - i0, j1 - j0
        L = math.hypot(di, dj)
        if L < 1.0:
            return False
        steps = int(math.ceil(L * 4))
        for s in range(1, steps):
            f = s / steps
            i, j = int(math.floor(i0 + 0.5 + di * f)), int(math.floor(j0 + 0.5 + dj * f))
            c = j * nx + i
            if c != k and c != q and M[c]:
                return True
        return False

    def geodesic_labels(self, passable, seeds):
        """Dijkstra multi-sources (chanfrein 3-4, sans couper les coins des mailles dures) sur les mailles `passable`.
        seeds : {maille: étiquette}. Renvoie (étiquette, distance) par maille (−1, inf hors d'atteinte)."""
        nx, ny = self.nx, self.ny
        n = nx * ny
        lab = np.full(n, -1, np.int64)
        dist = np.full(n, np.inf)
        heap = []
        for c, l in seeds.items():
            if passable[c] and (dist[c] > 0 or l < lab[c]):
                dist[c], lab[c] = 0.0, l
                heap.append((0.0, c, l))
        heapq.heapify(heap)
        P, M = passable, self.M
        while heap:
            d, c, l = heapq.heappop(heap)
            if d > dist[c] or lab[c] != l:
                continue
            j, i = divmod(c, nx)
            for dj, di, w in ((0, 1, 3), (0, -1, 3), (1, 0, 3), (-1, 0, 3), (1, 1, 4), (1, -1, 4), (-1, 1, 4), (-1, -1, 4)):
                jj, ii = j + dj, i + di
                if not (0 <= jj < ny and 0 <= ii < nx):
                    continue
                q = jj * nx + ii
                if not P[q]:
                    continue
                if dj and di and (M[j * nx + ii] or M[jj * nx + i]):
                    continue
                nd = d + w
                if nd < dist[q]:
                    dist[q], lab[q] = nd, l
                    heapq.heappush(heap, (nd, q, l))
        return lab, dist

    def reassign(self, cell):
        """cell : liste (indice de localité ≥ 0, −1 hors domaine, −2 bande russe). Renvoie (nouvelle liste, journal)."""
        n = len(cell)
        C = np.asarray(cell, np.int64)
        M = self.M
        land = (C >= 0) & ~M
        # candidates : maille hors barrière dont la localité est assez loin pour qu'une barrière s'interpose
        from scipy import ndimage
        dB = ndimage.distance_transform_edt(~M.reshape(self.ny, self.nx)).ravel()
        cand = np.flatnonzero(land)
        anc = np.array([self.anchor[c] if c >= 0 else -1 for c in range(len(self.anchor))], np.int64)
        a = anc[C[cand]]
        jj, ii = cand // self.nx, cand % self.nx
        aj, ai = a // self.nx, a % self.nx
        seg = np.hypot(jj - aj, ii - ai)
        cand = cand[(a >= 0) & (seg >= 1.0) & (dB[cand] <= seg + 1)]
        bad = [k for k in cand if self.blocked(int(k), int(anc[C[k]]))]
        out = list(cell)
        log = {"blocked": len(bad), "moved": 0, "kept": 0, "pairs": {}}
        if not bad:
            return out, log
        seeds = {}
        for l, c in enumerate(self.anchor):
            if c >= 0 and land[c] and C[c] >= 0:
                if c not in seeds or self._closer(l, seeds[c], c):
                    seeds[c] = l
        lab, dist = self.geodesic_labels(land, seeds)
        for k in bad:
            l = int(lab[k])
            if l < 0 or l == cell[k]:
                log["kept"] += 1
                continue
            log["pairs"].setdefault((int(cell[k]), l), []).append(int(k))
            out[k] = l
            log["moved"] += 1
        return out, log

    # ---------- statuts contaminés par l'autre rive ----------
    def bounded_geodesic(self, src, passable, limit):
        """Distances géodésiques (unités de chanfrein, 3 par maille) depuis src, jusqu'à `limit`."""
        nx, ny, M = self.nx, self.ny, self.M
        dist = {src: 0}
        heap = [(0, src)]
        while heap:
            d, c = heapq.heappop(heap)
            if d > dist.get(c, 1e18):
                continue
            j, i = divmod(c, nx)
            for dj, di, w in ((0, 1, 3), (0, -1, 3), (1, 0, 3), (-1, 0, 3), (1, 1, 4), (1, -1, 4), (-1, 1, 4), (-1, -1, 4)):
                jj, ii = j + dj, i + di
                if not (0 <= jj < ny and 0 <= ii < nx):
                    continue
                q = jj * nx + ii
                if not passable[q] or (dj and di and (M[j * nx + ii] or M[jj * nx + i])):
                    continue
                nd = d + w
                if nd <= limit and nd < dist.get(q, 1e18):
                    dist[q] = nd
                    heapq.heappush(heap, (nd, q))
        return dist

    def bank_neighbours(self, cell, R_cells=6.0, K=8):
        """Localités dont le polygone VIINA déborde sur une barrière (mailles dans le fleuve, ou de l'autre côté), et,
        pour chacune, ses voisines « propres » (polygone d'un seul tenant sur la terre ferme) de la même rive et de
        l'autre rive, à ≤ R_cells mailles. Même rive : distance géodésique sans traverser ≤ 1,5 × distance + 3 mailles ;
        autre rive : segment coupé par une barrière et pas « même rive »."""
        C = np.asarray(cell, np.int64)
        M = self.M
        land = (C >= 0) & ~M
        npl = len(self.anchor)
        nwet = np.zeros(npl, np.int64)
        np.add.at(nwet, C[(C >= 0) & M], 1)
        nblk = np.zeros(npl, np.int64)
        anc = np.array(self.anchor, np.int64)
        for k in np.flatnonzero(land):
            a = anc[C[k]]
            if a >= 0 and a != k and self.blocked(int(k), int(a)):
                nblk[C[k]] += 1
        straddle = (nwet + nblk) > 0
        bucket = {}
        for l, a in enumerate(self.anchor):
            if a >= 0:
                j, i = divmod(a, self.nx)
                bucket.setdefault((j // 8, i // 8), []).append(l)
        out = {}
        lim = int(3 * (1.5 * R_cells + 3)) + 1
        for l in np.flatnonzero(straddle):
            a = self.anchor[l]
            if a < 0 or not land[a]:
                continue
            j0, i0 = divmod(a, self.nx)
            cand = []
            for bj in range(j0 // 8 - 1, j0 // 8 + 2):
                for bi in range(i0 // 8 - 1, i0 // 8 + 2):
                    for q in bucket.get((bj, bi), ()):
                        if q == l or straddle[q]:
                            continue
                        j, i = divmod(self.anchor[q], self.nx)
                        e = math.hypot(j - j0, i - i0)
                        if e <= R_cells:
                            cand.append((e, q))
            if not cand:
                continue
            geo = self.bounded_geodesic(a, land, lim)
            same, other = [], []
            for e, q in sorted(cand):
                g = geo.get(self.anchor[q])
                if g is not None and g / 3.0 <= 1.5 * e + 3:
                    if len(same) < K:
                        same.append(int(q))
                elif self.blocked(a, self.anchor[q]):
                    if len(other) < K:
                        other.append(int(q))
            if same and other:
                out[int(l)] = (same, other)
        self.straddle = straddle
        return out

    def fix_status(self, status, places, nb, n_src=4, min_ref=3, share=0.75, stable_days=65):
        """Statut d'une localité débordante, carte par carte et instantané par instantané. Il est tenu pour une
        contagion de l'autre rive (et remplacé par celui de sa rive) si, à la fois :
        - il est celui de l'autre rive (majorité ≥ 75 % de ses voisines propres) alors que ses voisines propres de la
          même rive disent l'inverse (majorité ≥ 75 %) : localement, pour cette carte, le fleuve est la ligne ;
        - cette situation (statut, rive, autre rive) est stable depuis `stable_days` jours au moins (instantanés passés
          seulement : aucune fuite) ; un front fluide qui traverse la rivière n'est pas touché ;
        - la localité n'a pas pris ce statut en basculant seule contre sa rive (vraie tête de pont, ou dernier carré) ;
          une bascule au même instantané que l'autre rive est, elle, une contagion.
        - une autre carte au moins donne à la localité le statut de sa rive (si toutes la donnent à l'autre camp, on
          garde : rien ne distingue alors une contagion d'une réalité).
        « Contesté » n'est jamais touché ; une tête de pont étendue (voisines propres du même camp) non plus."""
        pid = [p["id"] for p in places]
        log, per_day = {}, {}
        days = sorted(status)
        dnum = {d: (int(d[:4]) * 372 + int(d[4:6]) * 31 + int(d[6:])) for d in days}  # ≈ jours, suffisant ici
        orig = {d: {pid[l]: status[d].get(pid[l]) for l in nb} for d in days}   # statuts VIINA d'origine (non corrigés)

        def mode(codes):
            c = [x for x in codes if x]
            if len(c) < min_ref:
                return 0
            for v in (1, 2, 3):
                if c.count(v) >= share * len(c):
                    return v
            return 0

        def refs(day, l, s):
            st = status[day]
            same, other = nb[l]
            return (mode([st.get(pid[q], (0,) * n_src)[s] for q in same]),
                    mode([st.get(pid[q], (0,) * n_src)[s] for q in other]))
        for n_day, day in enumerate(days):
            st, n = status[day], 0
            win = [d for d in days[:n_day + 1] if dnum[day] - dnum[d] <= stable_days]
            if dnum[day] - dnum[win[0]] < stable_days - 10:
                per_day[day] = 0
                continue
            for l in nb:
                g = pid[l]
                cur = orig[day].get(g)
                if not cur:
                    continue
                new = list(cur)
                for s in range(n_src):
                    v = cur[s]
                    if v not in (1, 2):
                        continue
                    A, B = refs(day, l, s)
                    if not (A in (1, 2) and B in (1, 2) and A != B and v == B):
                        continue
                    # une autre carte (indépendante : Wikipédia+VIINA dérive de Wikipédia) donne à la localité le statut
                    # de sa rive ; si toutes s'accordent sur l'autre camp, on ne corrige pas
                    indep = [x for x in range(min(3, n_src)) if x != s and not (s == 3 and x == 2)]
                    if not any(cur[x] == A for x in indep):
                        continue
                    if any((orig[d].get(g) or (0,) * n_src)[s] != v or refs(d, l, s) != (A, B) for d in win):
                        continue
                    # début de la série courante de ce statut : bascule seule contre sa rive → tête de pont, gardée
                    k = n_day
                    while k > 0 and (orig[days[k - 1]].get(g) or (0,) * n_src)[s] == v:
                        k -= 1
                    if k > 0 and (orig[days[k - 1]].get(g) or (0,) * n_src)[s]:
                        A0, B0 = refs(days[k], l, s)
                        _, B1 = refs(days[k - 1], l, s)
                        if A0 != v and not (B0 == v and B1 != v):
                            continue
                    new[s] = A
                    log[(l, s)] = log.get((l, s), 0) + 1
                    n += 1
                if new != list(cur):
                    st[g] = tuple(new)
            per_day[day] = n
        return log, per_day

    def _closer(self, a, b, c):
        j, i = divmod(c, self.nx)
        da = (self.pt[a][0] - i - 0.5) ** 2 + (self.pt[a][1] - j - 0.5) ** 2
        db = (self.pt[b][0] - i - 0.5) ** 2 + (self.pt[b][1] - j - 0.5) ** 2
        return da < db


def apply(cell, places, laea, X0, Y1, SIZE, NX, NY, root, status=None, verbose=True):
    """Point d'entrée de build_front.py : corrige `status` en place (statuts contaminés par l'autre rive) et renvoie
    (cell réattribuée, BarrierTiling ou None)."""
    M = load_mask(root, NX, NY)
    if M is None:
        print("pavage contraint par les barrières : site/barrieres_dures.js absent, pavage VIINA inchangé", flush=True)
        return cell, None
    ij = []
    for p in places:
        x, y = laea(p["lon"], p["lat"])
        ij.append(((x - X0) / SIZE, (Y1 - y) / SIZE))
    bt = BarrierTiling(M, NX, NY, ij)
    if status is not None:
        nb = bt.bank_neighbours(cell)
        slog, per_day = bt.fix_status(status, places, nb)
        bt.status_log = slog
        locs = {l for l, s in slog}
        if verbose:
            src = ("DeepState", "ISW", "Wikipédia", "Wikipédia+VIINA")
            by = [sum(v for (l, s), v in slog.items() if s == k) for k in range(4)]
            top = sorted(locs, key=lambda l: -sum(v for (q, s), v in slog.items() if q == l))[:10]
            print(f"statuts contaminés par l'autre rive : {int(bt.straddle.sum())} localités au polygone débordant sur une "
                  f"barrière, {len(nb)} avec des voisines propres des deux rives ; {len(locs)} localités corrigées au moins "
                  f"une fois (" + ", ".join(f"{a} {b}" for a, b in zip(src, by)) + " statuts × instantanés) ; principales : "
                  + ", ".join(places[l]["name"] for l in top), flush=True)
    out, log = bt.reassign(cell)
    if verbose:
        top = sorted(log["pairs"].items(), key=lambda kv: -len(kv[1]))[:8]
        desc = ", ".join(f"{places[a]['name']} → {places[b]['name']} ({len(v)})" for (a, b), v in top)
        print(f"pavage contraint par les barrières dures (fleuves ≥ 40 m³/s, plans d'eau ; {int(M.sum())} mailles) : "
              f"{log['blocked']} mailles séparées de leur localité par une barrière, {log['moved']} réattribuées à une "
              f"localité de leur rive ({len(log['pairs'])} couples) ; principales : {desc}", flush=True)
    bt.log = log
    return out, bt
