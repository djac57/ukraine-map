"""Poids DYNAMIQUE de chaque carte, secteur par secteur (tuiles de 24 km) et mois par mois (décision du 19/10/2026).

Problème : une carte peut se tromper durablement dans un secteur (Wikipédia code russes des localités de la rive droite
du Dnipro entre Antonivka et Kozatske depuis 2023-12, contre DeepState et l'ISW) ; son poids global (0,5) ne dit rien
de sa justesse locale.

Règle : au mois t, pour chaque carte s (DeepState, ISW, Wikipédia, Wikipédia + VIINA) et chaque tuile k,
  - erreur passée e_{s,k}(t) = part des mailles où la carte, au mois q, contredisait la VÉRITÉ RETARDÉE (consensus des
    cartes VIVANTES au mois q + H, majorité stricte ; connu seulement après coup), sur les mois q ≤ t − H seulement
    (aucune information future), oubli de demi-vie HL mois, emprunt aux 8 tuiles voisines (poids 0,3), rétrécie vers
    l'erreur moyenne de toutes les cartes (n0 mailles-mois fictives) ;
  - erreur DURABLE seulement : une erreur au mois q ne compte que si la carte contredit ENCORE la vérité au mois
    q + H. Une carte en retard qui se corrige ensuite n'est pas punie ; une carte EN AVANCE (DeepState) que la vérité
    rejoint ensuite non plus : sa justesse compte pour elle, et son poids relatif MONTE quand les autres se trompent ;
    le désaccord du jour ne joue jamais ;
  - multiplicateur m_{s,k}(t) = clip( ((ē_autres + ε) / (e_s + ε))^γ , m_min, m_max ), ē_autres = moyenne des erreurs
    des autres cartes indépendantes de la tuile (Wikipédia + VIINA n'est pas « autre » pour Wikipédia, ni l'inverse).
    Une carte aussi juste que les autres garde 1 ; plus juste, elle monte (jusqu'à m_max) ; durablement démentie,
    elle descend (jusqu'à m_min) ; elle remonte quand ses erreurs sortent de la fenêtre d'oubli.
Deux voix seulement (ISW arrêté le 30/06/2026, série prolongée) : une carte arrêtée n'est plus juge ni jugée après son
arrêt ; la vérité vient des cartes vivantes (DeepState et Wikipédia doivent être d'accord) ; là où elles se
contredisent, aucune nouvelle preuve : le poids appris auparavant est conservé (pas de pénalité à deux voix).

Le poids w_s · m_{s,k} entre dans le pool de son camp, et le camp pèse dans la moyenne des camps à proportion du
multiplicateur moyen de ses sources (sinon un camp réduit à des cartes démenties garderait tout son poids).
Renvoie {mois : {source : multiplicateur par maille de U (float32)}} et un tableau par tuile pour le journal.

Garde-fous : test anti-fuite bloquant (check_no_future : les poids du mois t sont identiques si l'historique s'arrête
à t) ; placebo (perm > 0 : mêmes multiplicateurs, tuiles permutées).

STATUT (19/10/2026) : NON RETENU, désactivé dans build_front.py (WMULT_ON). Variante sélectionnée sur 2023-2024
(A : mmax = 1, réduction seulement) ; évaluée gelée sur 2025-2026 : Brier ≤ 30 km et Dnipro aval meilleurs, mais
mailles qui changent de camp dégradées et avance de DeepState au nord de Lyman (2026) cassée. Variantes essayées : A,
B (mmax = 2), C (A sans poids de camp), D (both = True, H = 3) et le placebo de A. Détails : docs/ETAT.md, § 6.
"""
import numpy as np
from scipy import ndimage as ndi

HELD = ("dsm", "isw", "wiki", "boost")
JUDGES = ("dsm", "isw", "wiki")
OTHERS = {"dsm": ("isw", "wiki"), "isw": ("dsm", "wiki"), "wiki": ("dsm", "isw"), "boost": ("dsm", "isw")}
SIGN = np.array([127, -1, 1, 0], np.int8)   # code VIINA 0 (absent), 1 UA, 2 RU, 3 contesté → signe (+1 russe)


def compute(model, fm, NX, NY, cell_km, ended=None, H=2, HL=6.0, TS=12, R=10.0, far=30.0, n0=400.0, eps=0.01,
            gamma=1.0, mmin=0.15, mmax=1.0, durable=True, both=False, nbw=0.3, perm=0, verbose=True):
    """perm > 0 : PLACEBO (contrôle seulement) : mêmes multiplicateurs, tuiles permutées au hasard (graine perm)."""
    ended = ended or {}
    U = np.asarray(model.U, np.int64)
    cellU = np.asarray([model.cell[k] for k in model.U], np.int64)
    land = cellU >= 0
    uj, ui = U // NX, U % NX
    tile = (uj // TS) * 1000 + ui // TS
    NTy, NTx = NY // TS + 1, NX // TS + 1
    tflat = (uj // TS) * NTx + ui // TS
    days = [model.snap[m].strftime("%Y%m%d") for m in fm]
    S, T = [], []
    for d in days:
        codes = np.asarray(model.base_codes(d), np.int64)            # (localités, 4)
        live = [n for n, s in enumerate(JUDGES) if not (s in ended and d > ended[s])]
        c3 = codes[:, live]
        ru, ua = (c3 == 2).sum(1), (c3 == 1).sum(1)
        tp = np.where(ru > ua, 1, np.where(ua > ru, 0, -1)).astype(np.int8)
        t = np.full(len(U), -1, np.int8)
        t[land] = tp[cellU[land]]
        T.append(t)
        sg = {}
        for n, s in enumerate(HELD):
            if s in ended and d > ended[s]:
                continue                                          # série prolongée : ni juge ni jugée
            v = np.full(len(U), 127, np.int8)
            v[land] = SIGN[codes[cellU[land], n]]
            if (v != 127).any():
                sg[s] = v
        S.append(sg)
    # erreurs par (mois q, carte) : tuile, poids de l'erreur, mois de « réhabilitation » (premier mois q' > q + H où la
    # vérité rejoint ce que disait la carte au mois q : connu au mois t seulement si q' ≤ t)
    NEVER = 10 ** 6
    ERR, DEN = {}, {}
    for q in range(len(fm) - H):
        tr = T[q + H]
        g = np.full((NY, NX), -1, np.int8)
        g[uj, ui] = tr
        dRU = ndi.distance_transform_edt(g != 1)[uj, ui] * cell_km
        dUA = ndi.distance_transform_edt(g != 0)[uj, ui] * cell_km
        dline = np.where(tr == 1, dUA, np.where(tr == 0, dRU, np.inf))
        tsg = np.where(tr == 1, 1, -1)
        for s, v in S[q].items():
            ok = (v != 127) & (tr >= 0) & (dline <= far)
            wrong = ok & (v == -tsg)
            half = ok & (v == 0)
            if durable:
                later = S[q + H].get(s)
                if later is None:
                    continue
                wrong &= later == -tsg                                # toujours démentie à q + H (pas un simple retard)
                half &= later != tsg
            if both:
                # les DEUX autres cartes, d'accord entre elles, la contredisaient déjà au mois q et encore à q + H
                # (pas de juge arrêté : à deux voix, aucune nouvelle preuve, le poids appris est conservé)
                oth = [(S[q].get(o), S[q + H].get(o)) for o in OTHERS[s]]
                if any(a is None or b is None for a, b in oth):
                    continue
                for a, b in oth:
                    wrong &= (a == tsg) & (b == tsg)
                    half &= (a == tsg) & (b == tsg)
            zone = ok & ((dline <= R) | wrong | half)
            DEN[(q, s)] = np.bincount(tflat[zone], None, NTy * NTx).astype(np.float64)
            iw = np.flatnonzero(wrong)
            vind = np.full(len(iw), NEVER, np.int64)
            target = np.where(v[iw] == 1, 1, 0)
            todo = np.ones(len(iw), bool)
            for q2 in range(q + H + 1, len(fm)):
                hit = todo & (T[q2][iw] == target)
                vind[hit] = q2
                todo &= ~hit
                if not todo.any():
                    break
            ih = np.flatnonzero(half)
            ERR[(q, s)] = (np.r_[tflat[iw], tflat[ih]], np.r_[np.ones(len(iw)), np.full(len(ih), 0.5)],
                           np.r_[vind, np.full(len(ih), NEVER, np.int64)])
    K = np.array([[nbw, nbw, nbw], [nbw, 1.0, nbw], [nbw, nbw, nbw]])
    out, table = {}, {}
    for t, m in enumerate(fm):
        past = [q for q in range(t - H + 1) if any((q, s) in DEN for s in HELD)]
        if len(past) < 6:
            continue
        E = {}
        Nn = {}
        tot_n = tot_d = 0.0
        for s in HELD:
            num = np.zeros(NTy * NTx)
            den = np.zeros(NTy * NTx)
            for q in past:
                if (q, s) in DEN:
                    w = 0.5 ** ((t - q) / HL)
                    tk, ew, vd = ERR[(q, s)]
                    keep = vd > t                                     # pas encore réhabilitée au mois t
                    num += w * np.bincount(tk[keep], ew[keep], NTy * NTx)
                    den += w * DEN[(q, s)]
            if den.sum() == 0:
                continue
            num = ndi.convolve(num.reshape(NTy, NTx), K, mode="constant")
            den = ndi.convolve(den.reshape(NTy, NTx), K, mode="constant")
            E[s], Nn[s] = num, den
            if s in JUDGES:
                tot_n += num.sum()
                tot_d += den.sum()
        e0 = tot_n / max(tot_d, 1.0)
        rate = {s: (E[s] + n0 * e0) / (Nn[s] + n0) for s in E}
        out[m] = {}
        tab = {}
        for s in E:
            oth = [rate[o] for o in OTHERS[s] if o in rate]
            if not oth:
                continue
            mt = np.clip(((np.mean(oth, axis=0) + eps) / (rate[s] + eps)) ** gamma, mmin, mmax).astype(np.float32)
            if perm:  # placebo : multiplicateurs redistribués entre les tuiles déjà renseignées au mois t
                act = np.flatnonzero(Nn[s].ravel() > 50)
                f = mt.ravel().copy()
                f[act] = f[np.random.default_rng(int(perm)).permutation(act)]
                mt = f.reshape(NTy, NTx)
            b = ndi.map_coordinates(mt, [uj / TS - 0.5, ui / TS - 0.5], order=1, mode="nearest").astype(np.float32)
            b[~land] = 1.0
            out[m][s] = b
            tab[s] = (mt, rate[s], Nn[s])
        table[m] = tab
    if verbose and fm[-1] in table:
        tab = table[fm[-1]]
        for s, (mt, r, nn) in tab.items():
            sel = nn > 50
            if sel.any():
                v = mt[sel]
                print(f"  poids dynamique {s} ({fm[-1]}) : multiplicateur médian {np.median(v):.2f}, 10e centile {np.percentile(v, 10):.2f}, "
                      f"90e {np.percentile(v, 90):.2f} ; tuiles < 0,5 : {np.mean(v < 0.5):.0%}, > 1,5 : {np.mean(v > 1.5):.0%} ({int(sel.sum())} tuiles)", flush=True)
    model.wmult_table = table
    model.wmult_grid = (NTy, NTx, TS)
    return out


def tile_table(model, m, places_lonlat=None):
    """Lignes (tuile, source, multiplicateur, erreur, n) pour un mois, pour un rapport."""
    tab = model.wmult_table.get(m, {})
    NTy, NTx, TS = model.wmult_grid
    rows = []
    for s, (mt, r, nn) in tab.items():
        for j in range(NTy):
            for i in range(NTx):
                if nn[j, i] > 50:
                    rows.append((j, i, s, float(mt[j, i]), float(r[j, i]), float(nn[j, i])))
    return rows


def check_no_future(model, fm, out, NX, NY, cell_km, cuts=None, **kw):
    """Test anti-fuite (bloquant) : les poids du mois t recalculés sur l'historique ARRÊTÉ à t doivent être identiques
    à ceux du calcul complet. Une donnée postérieure à t dans le poids de t ferait échouer le build."""
    have = [m for m in fm if m in out]
    if not have:
        return
    cuts = cuts or sorted({have[0], have[len(have) // 2], have[-2] if len(have) > 1 else have[-1]})
    keep = (getattr(model, "wmult_table", None), getattr(model, "wmult_grid", None))
    for m in cuts:
        t = fm.index(m)
        part = compute(model, fm[:t + 1], NX, NY, cell_km, verbose=False, **kw)
        for s, v in out[m].items():
            w = part.get(m, {}).get(s)
            if w is None or not np.array_equal(v, w):
                model.wmult_table, model.wmult_grid = keep
                raise SystemExit(f"source_consensus : FUITE D'INFORMATION, poids {s} de {m} différent quand l'historique s'arrête à {m}")
    model.wmult_table, model.wmult_grid = keep
    print(f"  poids dynamique : test anti-fuite réussi (historique tronqué à {', '.join(cuts)} : poids identiques)", flush=True)
