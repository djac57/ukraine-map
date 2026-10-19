"""Détecteur de cratères v2 : candidats et attributs d'une date d1 contre une référence (dernière observation claire de
chaque pixel, ≤ 30 jours) et une date de persistance (prochaine observation claire, ≤ 25 jours).

Principe (photo-interprétation à 10 m : cratère de bombe aérienne = petite tache ronde de sol nu, plus claire et moins
verte que le fond, anneau d'éjecta, nouvelle entre deux dates et qui reste) :
  1. changement radiométrique : dV = log(visible d1 / visible réf.) (moyenne B02 B03 B04), dN = NDVI réf. − NDVI d1 ;
     chacun rapporté à sa COURONNE (carré de 130 m moins le carré central de 70 m) : un labour, une récolte, une brume
     ou un écart d'éclairement changent la couronne autant que le centre et s'annulent ; un cratère non ;
  2. C = cV / σV + 0,5 · cN / σN (σ : écart-type robuste de la scène) ; candidats = maxima locaux 5 × 5 de C > seuil ;
  3. attributs calculés sur une fenêtre locale autour de chaque candidat (rapide) :
       - forme : aire de la tache à mi-hauteur, élongation (moments d'ordre 2), rapport des courbures du hessien
         (rond ≈ 1, chemin ou lisière ≈ 0) ;
       - « composante changée » : taille de la zone connexe changée qui contient la tache (un champ labouré = une
         grande composante ; un pilonnage = beaucoup de petites) et plus grande composante changée à ≤ 60 m ;
       - persistance : même contraste à la date suivante ;
       - validité : pixels non valides (nuages, ombres) à ≤ 60 m aux trois dates ; SCL douteux ;
       - brume : hausse du bleu plus forte que celle du rouge ; tache très claire à d0 (carrière, bâti) ;
       - contraste spatial à la référence (déjà là avant ?) et à d1.
La décision est faite à part (rules.py), calibrée sur le jeu de vérité.
"""
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

EPS = 1e-4
R_OUT, R_IN = 13, 7     # couronne : carré 13 × 13 moins carré 7 × 7 (130 m / 70 m)


def boxsum(x, n):
    return ndi.uniform_filter(x, n, mode="nearest") * (n * n)


def ring_contrast(x, ok):
    """(moyenne 3 × 3 pondérée) − (moyenne de la couronne), pixels non valides ignorés ; renvoie aussi la part valide de
    la couronne."""
    xo = np.where(ok, x, 0).astype(np.float32)
    o = ok.astype(np.float32)
    so, si = boxsum(xo, R_OUT), boxsum(xo, R_IN)
    no, ni = boxsum(o, R_OUT), boxsum(o, R_IN)
    ring = (so - si) / np.maximum(no - ni, EPS)
    core = ndi.gaussian_filter(xo, 0.7, truncate=2.0) / np.maximum(ndi.gaussian_filter(o, 0.7, truncate=2.0), EPS)
    return core - ring, (no - ni) / (R_OUT * R_OUT - R_IN * R_IN)


BANDS = ("B02", "B03", "B04", "B08")


def fl(z):
    return {b: z[b].astype(np.float32) / 1e4 for b in BANDS}


def vis(f):
    return (f["B02"] + f["B03"] + f["B04"]) / 3


def ndvi(f):
    return (f["B08"] - f["B04"]) / np.maximum(f["B08"] + f["B04"], 0.02)


def robust_sd(x, ok):
    v = x[ok]
    if v.size < 2000:
        return None
    v = v[:: max(1, v.size // 300000)]
    q = np.percentile(v, [25, 75])
    return max(float(q[1] - q[0]) / 1.349, 1e-3)


def change_score(fa, fb, ok, sds=None):
    dV = np.log(np.maximum(vis(fb), 0.005) / np.maximum(vis(fa), 0.005))
    dN = ndvi(fa) - ndvi(fb)
    cV, rv = ring_contrast(dV, ok)
    cN, _ = ring_contrast(dN, ok)
    if sds is None:
        m = ok & (rv > 0.6)
        sV, sN = robust_sd(cV, m), robust_sd(cN, m)
        if sV is None:
            return None, None, None, None, (None, None)
        sds = (sV, sN)
    C = cV / sds[0] + 0.5 * cN / sds[1]
    C = np.where(ok & (rv > 0.6), C, -99).astype(np.float32)
    return C, sds, dV, dN, (cV, cN)


def shift_est(a, b, ok):
    """Décalage sub-pixel (dy, dx) de b par rapport à a (corrélation de phase sur le proche infrarouge passe-haut)."""
    def prep(x):
        x = np.where(ok, x, np.median(x[ok]) if ok.any() else 0).astype(np.float32)
        x = x - ndi.uniform_filter(x, 9)
        return x * np.outer(np.hanning(x.shape[0]), np.hanning(x.shape[1])).astype(np.float32)
    A, B = np.fft.rfft2(prep(a)), np.fft.rfft2(prep(b))
    R = A * np.conj(B)
    R /= np.maximum(np.abs(R), 1e-9)
    c = np.fft.irfft2(R, s=a.shape)
    c = np.fft.fftshift(c)
    i, j = np.unravel_index(np.argmax(c), c.shape)
    if not (1 <= i < c.shape[0] - 1 and 1 <= j < c.shape[1] - 1):
        return 0.0, 0.0, 0.0
    def par(m, z, p_):
        d = m - 2 * z + p_
        return 0.0 if d == 0 else 0.5 * (m - p_) / d
    dy = i - c.shape[0] // 2 + par(c[i - 1, j], c[i, j], c[i + 1, j])
    dx = j - c.shape[1] // 2 + par(c[i, j - 1], c[i, j], c[i, j + 1])
    return -float(dy), -float(dx), float(c[i, j])


def coregister(ref, cur, ok, maxs=1.5):
    """Recale cur sur ref (translation sub-pixel par bloc) ; renvoie cur recalé et le décalage."""
    if ok.mean() < 0.2:
        return cur, (0.0, 0.0)
    dy, dx, pk = shift_est(ref["B08"].astype(np.float32), cur["B08"].astype(np.float32), ok)
    if abs(dy) > maxs or abs(dx) > maxs or (abs(dy) < 0.05 and abs(dx) < 0.05):
        return cur, (dy, dx)
    out = dict(cur)
    for b in BANDS:
        out[b] = ndi.shift(cur[b].astype(np.float32), (-dy, -dx), order=1, mode="nearest")
    out["valid"] = ndi.shift(cur["valid"].astype(np.float32), (-dy, -dx), order=0, mode="constant") > 0.5
    return out, (dy, dx)


def haze_pair(fa, fb, ok, n=31, t=0.012):
    """Pixels où le bleu change de façon lisse sur ~300 m (brume, voile, fumée sur l'une des deux dates)."""
    d = np.where(ok, fb["B02"] - fa["B02"], 0).astype(np.float32)
    m = ndi.uniform_filter(d, n) / np.maximum(ndi.uniform_filter(ok.astype(np.float32), n), 1e-3)
    return np.abs(m) > t


def candidates(ref, cur, nxt, land, thr=6.0, win=10):
    """ref, cur, nxt : dicts {bandes int16, valid}; land : masque des pixels admissibles (hors bâti, eau, humide).
    Renvoie (liste d'attributs, masque des pixels observés ref & cur & land, sds)."""
    ok = ref["valid"] & cur["valid"]
    cur, shift = coregister(ref, cur, ok)
    if nxt is not None:
        nxt, _ = coregister(ref, nxt, ref["valid"] & nxt["valid"])
    f0, f1 = fl(ref), fl(cur)
    ok = ref["valid"] & cur["valid"]
    ok &= ~haze_pair(f0, f1, ok)
    cur = dict(cur); cur["valid"] = cur["valid"] & (ok | ~ref["valid"])
    okl = ok & land
    C, sds, dV, dN, (cV, cN) = change_score(f0, f1, ok)
    if C is None:
        return [], okl, None
    C = np.where(land, C, -99)
    mx = ndi.maximum_filter(C, size=5)
    peak = (C == mx) & (C > thr)
    rr, cc = np.nonzero(peak)
    H, W = C.shape
    if nxt is not None:
        f2 = fl(nxt)
        ok2 = ref["valid"] & nxt["valid"]
        ok2 &= ~haze_pair(f0, f2, ok2)
        nxt = dict(nxt); nxt["valid"] = nxt["valid"] & (ok2 | ~ref["valid"])
    V0f, V1f, N0f, N1f = vis(f0), vis(f1), ndvi(f0), ndvi(f1)
    out = []
    # seuils de « pixel changé » pour les composantes : 3 σ du contraste de changement OU changement brut fort
    for r, c in zip(rr, cc):
        if r < win or c < win or r >= H - win or c >= W - win:
            continue
        s = (slice(r - win, r + win + 1), slice(c - win, c + win + 1))
        k = win
        p = float(C[r, c])
        okw = ok[s]
        Cw = C[s]
        # forme (tache à mi-hauteur)
        lab, _ = ndi.label(Cw >= p / 2)
        blob = lab == lab[k, k]
        area = int(blob.sum())
        yy, xx = np.nonzero(blob)
        if area >= 3:
            ev = np.sort(np.linalg.eigvalsh(np.cov(np.vstack([yy, xx]))))
            elong = float(np.sqrt(max(ev[1], 1e-3) / max(ev[0], 1e-3)))
        else:
            elong = 1.0 if area == 1 else 2.0
        Cs = ndi.gaussian_filter(np.where(Cw > -99, Cw, 0), 1.0)
        hyy = (Cs[k + 1, k] + Cs[k - 1, k] - 2 * Cs[k, k]); hxx = (Cs[k, k + 1] + Cs[k, k - 1] - 2 * Cs[k, k])
        hxy = (Cs[k + 1, k + 1] + Cs[k - 1, k - 1] - Cs[k + 1, k - 1] - Cs[k - 1, k + 1]) / 4
        tr_, det = hyy + hxx, hyy * hxx - hxy * hxy
        disc = np.sqrt(max(tr_ * tr_ / 4 - det, 0))
        l1, l2 = tr_ / 2 - disc, tr_ / 2 + disc
        hr = float(l2 / l1) if l1 < 0 else -1.0
        # composantes changées (C > 3) dans la fenêtre
        chg = Cw > 3.0
        lc, nc = ndi.label(chg)
        own = int((lc == lc[k, k]).sum()) if lc[k, k] else 0
        if nc:
            sz = ndi.sum(chg, lc, index=np.arange(1, nc + 1))
            big = float(sz.max())
            ncomp = int((sz >= 2).sum())
        else:
            big, ncomp = 0.0, 0
        fchg = float(chg[okw].mean()) if okw.any() else 0.0
        # changement brut à grande échelle (parcelle entière) : |dN| médian sur 210 m
        dNw = dN[s][okw]
        dVw = dV[s][okw]
        bigN = float(np.median(dNw)) if dNw.size else 0.0
        bigV = float(np.median(dVw)) if dVw.size else 0.0
        # validité à ≤ 60 m
        s6 = (slice(r - 6, r + 7), slice(c - 6, c + 7))
        inv0 = float(1 - ref["valid"][s6].mean()); inv1 = float(1 - cur["valid"][s6].mean())
        # persistance
        if nxt is not None and ok2[s6].mean() > 0.7:
            fa = {b: f0[b][s] for b in f0}; fb = {b: f2[b][s] for b in f2}
            C2w, _, _, _, _ = change_score(fa, fb, ok2[s], sds)
            C2 = float(C2w[k - 1:k + 2, k - 1:k + 2].max())
            inv2 = float(1 - nxt["valid"][s6].mean())
        else:
            C2, inv2 = np.nan, 1.0
        # brume / ombre : bleu contre rouge (contraste de changement local, 3 × 3 contre fenêtre)
        db = np.log(np.maximum(f1["B02"][s], 0.005) / np.maximum(f0["B02"][s], 0.005))
        drd = np.log(np.maximum(f1["B04"][s], 0.005) / np.maximum(f0["B04"][s], 0.005))
        cb = float(db[k - 1:k + 2, k - 1:k + 2].mean() - np.median(db[okw])) if okw.any() else 0.0
        cr = float(drd[k - 1:k + 2, k - 1:k + 2].mean() - np.median(drd[okw])) if okw.any() else 0.0
        V0w, V1w = V0f[s], V1f[s]
        N0w, N1w = N0f[s], N1f[s]
        c0V = float(np.log(max(V0w[k - 1:k + 2, k - 1:k + 2].mean(), 0.005)) - np.log(max(np.median(V0w[okw]), 0.005))) if okw.any() else 0.0
        c1V = float(np.log(max(V1w[k - 1:k + 2, k - 1:k + 2].mean(), 0.005)) - np.log(max(np.median(V1w[okw]), 0.005))) if okw.any() else 0.0
        g0 = float(np.abs(np.diff(np.log(np.maximum(V0w[k - 3:k + 4, k - 3:k + 4], 0.005)), axis=0)).max())
        lv0 = np.log(np.maximum(V0w, 0.005))
        tex0 = float(np.std(lv0[okw])) if okw.sum() > 20 else 9.0          # texture de la référence sur 210 m (bâti, jardins)
        ring = np.ones_like(okw); ring[k - 2:k + 3, k - 2:k + 3] = False
        cw = Cw[ring & okw & (Cw > -99)]
        madw = float(np.median(np.abs(cw - np.median(cw)))) * 1.4826 if cw.size > 50 else 99.0
        snr = p / (madw + 0.5)
        if "rdate" in ref:
            rd = ref["rdate"][s]
            rmix = float((rd[k - 6:k + 7, k - 6:k + 7] != rd[k, k]).mean())
        else:
            rmix = 0.0
        hot1 = float((f1["B02"][s] - 0.5 * f1["B04"][s])[k - 1:k + 2, k - 1:k + 2].mean())
        out.append(dict(r=int(r), c=int(c), C=p, cV=float(cV[r, c] / sds[0]), cN=float(cN[r, c] / sds[1]), area=area, elong=elong,
                        hr=hr, own=own, big=big, ncomp=ncomp, fchg=fchg, bigN=bigN, bigV=bigV, inv0=inv0, inv1=inv1, inv2=inv2,
                        C2=C2, cb=cb, cr=cr, tex0=tex0, hot1=hot1, madw=madw, snr=snr, rmix=rmix, c0V=c0V, c1V=c1V, g0=g0, V0=float(V0w[k, k]), V1=float(V1w[k, k]),
                        N0=float(N0w[k, k]), N1=float(N1w[k, k]), sV=sds[0], sN=sds[1], sh=[round(shift[0], 2), round(shift[1], 2)]))
    if out:
        P = np.array([[o["r"], o["c"]] for o in out], float)
        t = cKDTree(P)
        n50 = t.query_ball_point(P, 50, return_length=True)
        dd, _ = t.query(P, 2)
        for o, n, d1 in zip(out, n50, dd[:, 1]):
            o["n500"] = int(n) - 1
            o["nn"] = float(d1)
    return out, okl, sds
