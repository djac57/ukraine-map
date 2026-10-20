"""Historique infra-mensuel (option, 20/10/2026, docs/ETAT.md § 6) : lecture des instantanés quotidiens (ou aux 10 et 20)
publiés à part par `build_front.py` (HIST : `site/front_hist_<année>.js`, `site/front_hist_<année>_<vue>.js`, index dans
`FRONT.hist`) et champ de fusion RÉANCRÉ au jour.

  - `lire()` : pour chaque jour d, mois de référence t (dernier mois écoulé, `months[].ref`), champ des cartes Peq et
    nowcast publiés (sur Z, 6 bits), consensus des cartes T (même règle que le socle, états des cartes = ceux du mois t +
    différences publiées), distances signées SDe / SDn aux lignes 50 % des cartes et du nowcast du jour ;
  - `consensus_Y()` : profondeur russe Y du consensus par tronçon aux 10, 20 et fin de mois (format de
    `donnees.consensus_decades`, lu par l'observation dynamique « decade » du moteur) ;
  - `champ(CH, choice, lams, rho)` : champ de fusion au jour d, SANS rien réapprendre : loi a posteriori de δ,
    facteurs de bande λ et paramètres du champ (β, γ, s₀, s_π, κ) du mois t ; la ligne des cartes est celle du jour, et
    le décalage de chaque tronçon est diminué de ρ·ΔY_k, ΔY_k = déplacement médian de la ligne des cartes entre t et d
    (mailles à ≤ 10 km de la ligne de t) :
        ρ = 1 : fusion de t reportée (la vraie ligne n'a pas bougé, les cartes la rattrapent) ;
        ρ = 0 : décalage entier réancré sur la ligne des cartes du jour (les cartes ont bougé avec la vraie ligne).
    Aucune information postérieure à d : champ des cartes de d, réglages connus à la fin de t.
"""
import datetime as dt
import json
import os

import numpy as np

from .commun import S
from .socle import SITE, decode_seq

_D = {}


def disponible():
    return bool(S.FR.get("hist")) and os.path.exists(os.path.join(SITE, f"front_hist_{min(S.FR['hist']['years'])}.js"))


def _after_eq(path):
    t = open(path, encoding="utf-8").read()
    return json.loads(t[t.index("]=") + 2:].strip().rstrip(";"))


def lire(depuis=None):
    """Liste des jours lisibles : {key, date, t (mois de référence), m (mois du jour), T, Peq, Pnow (uint8, 6 bits),
    SDe, SDn (float16)} sur Z ; depuis : « AAAA-MM-JJ » (jours antérieurs ignorés)."""
    if "D" in _D:
        return _D["D"]
    out = []
    if not disponible():
        _D["D"] = out
        return out
    cur = np.zeros(S.nPlaces, np.int64)
    words = {}
    for i in range(S.NM):
        mo = S.FR["months"][i] if i < len(S.FR["months"]) else None
        if not mo or "d" not in mo:
            continue
        d = np.asarray(mo["d"], np.int64).reshape(-1, 2)
        if len(d):
            cur[d[:, 0]] = d[:, 1]
        words[i] = cur.copy()
    ua = S.cellU >= 0
    for Y in sorted(S.FR["hist"]["years"]):
        H = _after_eq(os.path.join(SITE, f"front_hist_{Y}.js"))
        seq = {f: decode_seq(_after_eq(os.path.join(SITE, f"front_hist_{Y}_{f}.js")), S.nU) for f in ("equilibre", "nowcast")}
        for j, (key, mo) in enumerate(zip(H["keys"], H["months"])):
            t = int(mo["ref"])
            if (depuis and key < depuis) or t not in words or seq["equilibre"][j] is None:
                continue
            w = words[t].copy()
            dd = np.asarray(mo.get("d", []), np.int64).reshape(-1, 2)
            if len(dd):
                w[dd[:, 0]] = dd[:, 1]
            c = np.stack([(w >> (2 * s)) & 3 for s in range(3)], 1)
            ru_v, ua_v = (c == 2).sum(1), (c == 1).sum(1)
            cons = np.where(ru_v > ua_v, 1, np.where((ua_v > ru_v) | (c == 0).all(1), 0, -1)).astype(np.int8)
            T = np.full(S.nU, -1, np.int8)
            T[ua] = cons[S.cellU[ua]]
            qe = seq["equilibre"][j]
            qn = seq["nowcast"][j] if seq["nowcast"][j] is not None else qe
            out.append({"key": key, "date": dt.date.fromisoformat(key), "t": t, "m": S.midx.get(key[:7]), "T": T[S.Zi],
                        "Peq": qe[S.Zi].astype(np.uint8), "Pnow": qn[S.Zi].astype(np.uint8),
                        "SDe": S.sd_of(qe.astype(np.float32) / 63.0)[S.Zi].astype(np.float16),
                        "SDn": S.sd_of(qn.astype(np.float32) / 63.0)[S.Zi].astype(np.float16)})
    _D["D"] = out
    return out


def consensus_Y():
    """{(t, code de tuile): (Y_10, Y_20, Y_fin)} : profondeur russe du consensus (surface russe / côté du tronçon), même
    sélection de mailles que `Socle._tiles_Y` (mailles à ≤ 20 km de la ligne des cartes du mois t) ; Y_fin = S.Y."""
    from .socle import TILE, TILE_N
    out = {}
    D = lire()
    by = {}
    for e in D:
        if e["m"] is None or e["m"] not in S.built:
            continue
        by.setdefault(e["m"], {})[e["date"].day] = e
    for t, ee in by.items():
        if 10 not in ee or 20 not in ee:
            continue
        sd = np.nan_to_num(S.SDe[t], nan=999)
        nr = np.abs(sd) <= 20
        cn = np.bincount(S.tinv, weights=nr, minlength=S.nT)
        nmin = max(5.0, 20.0 * TILE_N)
        Ys = []
        for day in (10, 20):
            T = ee[day]["T"]
            ck = np.bincount(S.tinv, weights=(T >= 0), minlength=S.nT)
            ry = np.bincount(S.tinv, weights=(T == 1), minlength=S.nT)
            Ys.append((ck, ry))
        for j in range(S.nT):
            yf = S.Y.get((t, j))
            if yf is None or cn[j] < nmin or Ys[0][0][j] < nmin or Ys[1][0][j] < nmin:
                continue
            out[(t, int(S.tiles[j]))] = tuple(float(Ys[q][1][j]) * S.km ** 2 / (TILE * S.km) for q in (0, 1)) + (yf,)
    return out


def delta_Y(CH, t, e, near_km=10.0):
    """ΔY par ligne du mois t (km, > 0 : la ligne des cartes a avancé côté ukrainien entre t et la décade)."""
    sdM = S.SDe[t]
    dif = e["SDe"].astype(np.float32) - sdM
    ok = np.isfinite(dif) & (np.abs(np.nan_to_num(sdM, nan=999)) <= near_km)
    out = np.zeros(len(CH.C.rows[t]))
    for i, k in enumerate(CH.C.rows[t]):
        z = S.tile_cells[k]
        z = z[ok[z]]
        if len(z) >= 5:
            out[i] = float(np.clip(np.median(dif[z]), -20, 20))
    return out


def champ(CH, choice, lams, rho, decs=None, perm_seed=None):
    """{indice du jour: p sur Z (uint8, 6 bits)} ; perm_seed : PLACEBO (ΔY permutés entre tronçons du même mois)."""
    from .champ import ZG, quant
    out = {}
    D = lire()
    rng = np.random.default_rng(perm_seed) if perm_seed is not None else None
    tabs = {}
    for j, e in enumerate(D):
        if decs is not None and j not in decs:
            continue
        t = e["t"]
        if t not in choice or t not in lams or t not in CH.R["post"]:
            continue
        b, g, s0, sp, kp = choice[t]
        if t not in tabs:
            tabs = {t: CH.tables(t, CH.s_of(t, s0, sp), lams[t])}
        F = tabs[t]
        pc, pr, pw = CH.pairs(t)
        sdM = np.nan_to_num(S.SDe[t], nan=999).astype(np.float64)
        extra = CH.sdp(t, b, g, kp) - sdM                                  # β·v_loc + γ·o du mois t
        dY = delta_Y(CH, t, e) if rho != 1.0 or rng is not None else np.zeros(len(CH.C.rows[t]))
        if rng is not None:
            dY = dY[rng.permutation(len(dY))]
        sdD = np.nan_to_num(e["SDe"].astype(np.float64), nan=999)
        z = sdD[pc] + extra[pc] - rho * dY[pr]
        x = (np.clip(z, ZG[0], ZG[-1]) - ZG[0]) / (ZG[1] - ZG[0])
        i0 = np.minimum(np.floor(x).astype(np.int64), len(ZG) - 2)
        fr = x - i0
        v = F[pr, i0] * (1 - fr) + F[pr, i0 + 1] * fr
        num = np.bincount(pc, weights=pw * v, minlength=S.nZ)
        den = np.bincount(pc, weights=pw, minlength=S.nZ)
        p = np.where(den > 1e-9, num / np.maximum(den, 1e-12), e["Peq"] / 63.0)
        out[j] = np.round(quant(p) * 63).astype(np.uint8)
    return out


def ecrire(dF, rho):
    """Fichiers du champ de fusion au jour : site/front_hist_<année>_fusion.js =
        (window.FRONT_FUSION_HIST=window.FRONT_FUSION_HIST||{})["<année>"] = {keys, rho, months: [{date, a}]} ;
        (window.FRONTH=window.FRONTH||{})["<année>/fusion"] = [...]   (même codage, mêmes clés que front_hist_<année>.js)
    Hors de Z : champ des cartes du jour ; bande russe : nowcast du jour (domaine validé de la fusion = l'Ukraine)."""
    from .socle import encode_field
    from .sortie import areas
    D = lire()
    pos = {e["key"]: j for j, e in enumerate(D)}
    band = S.cellU == -2
    index = {}
    for Y in sorted(S.FR["hist"]["years"]):
        H = _after_eq(os.path.join(SITE, f"front_hist_{Y}.js"))
        seq = {f: decode_seq(_after_eq(os.path.join(SITE, f"front_hist_{Y}_{f}.js")), S.nU) for f in ("equilibre", "nowcast")}
        arr, metas, prev = [], [], None
        for j, key in enumerate(H["keys"]):
            q = seq["equilibre"][j]
            jj = pos.get(key)
            if q is None or jj is None or jj not in dF:
                arr.append(None)
                metas.append(None)
                continue
            p = q.astype(np.float32) / 63.0
            p[S.Zi] = dF[jj].astype(np.float32) / 63.0
            if seq["nowcast"][j] is not None:
                p[band] = seq["nowcast"][j][band].astype(np.float32) / 63.0
            qq = np.minimum(63, (p * 63 + 0.5).astype(np.int64)).astype(np.uint8)
            arr.append(encode_field(qq, prev))
            prev = qq
            metas.append({"date": key, "a": areas(p)})
        head = json.dumps({"keys": H["keys"], "rho": rho, "months": metas}, ensure_ascii=False, separators=(",", ":"))
        path = os.path.join(SITE, f"front_hist_{Y}_fusion.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'(window.FRONT_FUSION_HIST=window.FRONT_FUSION_HIST||{{}})["{Y}"]=' + head + ";\n")
            f.write(f'(window.FRONTH=window.FRONTH||{{}})["{Y}/fusion"]=' + json.dumps(arr, separators=(",", ":")) + ";\n")
        index[Y] = {"n": sum(1 for x in arr if x), "ko": os.path.getsize(path) // 1024}
    return index
