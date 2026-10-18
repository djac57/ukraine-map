// Contrainte d'affichage « barrière dure » pour les flèches (proposition pour app.js ; rien n'est appliqué au site).
// Données : barrieres_dures.js (window.HARD_BARRIERS, 12 192 mailles de 2 km, 65 ko) à charger avant app.js.
// Règle validée sur le passé (ETAT_TERRAIN.md § B5, usage c) : une flèche dont la hampe traverse une barrière dure
// (fleuve ≥ 40 m³/s ou plan d'eau, escarpement, canal nommé) du côté envahi est TRONQUÉE juste avant la barrière, sans
// changer son style ni son sens (on ne la supprime pas : la justesse des flèches traversantes, 24,8 %, n'est pas
// significativement plus basse que celle des autres, 29,2 %, mais la ligne ne dépasse la barrière d'ici t+2 que dans
// 5,8 % des cas, et la troncature ramène l'écart longueur / avancée réalisée de 3,69 à 2,16 km).
// Les coordonnées sont celles de la grille LAEA du site (i, j en mailles, flottants), comme dans computeArrows.
(function () {
  const H = window.HARD_BARRIERS;
  if (!H) return;
  const bin = atob(H.packed), n = bin.length / 4, mask = new Uint8Array(H.nx * H.ny);
  for (let k = 0; k < n; k++) {
    const v = (bin.charCodeAt(4 * k) | (bin.charCodeAt(4 * k + 1) << 8) | (bin.charCodeAt(4 * k + 2) << 16) | (bin.charCodeAt(4 * k + 3) << 24)) >>> 0;
    mask[v >>> 3] = v & 7;
  }
  // base = [i, j] sur la ligne, head = [i, j] tête prévue ; renvoie la tête éventuellement tronquée et le code de barrière
  window.hardTruncate = function (base, head, minLenCells) {
    const di = head[0] - base[0], dj = head[1] - base[1], L = Math.hypot(di, dj);
    if (L < 1e-6) return { head, code: 0 };
    const steps = Math.ceil(L * 4);                       // pas d'un quart de maille (500 m)
    for (let s = 1; s <= steps; s++) {
      const f = s / steps, i = Math.floor(base[0] + di * f), j = Math.floor(base[1] + dj * f);
      if (f * L < 0.75) continue;                         // la maille de la base (ligne posée sur la barrière) ne compte pas
      if (i < 0 || j < 0 || i >= H.nx || j >= H.ny) break;
      const c = mask[j * H.nx + i];
      if (c) {
        const keep = Math.max((s - 1) / steps * L, minLenCells || 0.5);   // tête juste avant la barrière, longueur minimale lisible
        const g = Math.min(1, keep / L);
        return { head: [base[0] + di * g, base[1] + dj * g], code: c };
      }
    }
    return { head, code: 0 };
  };
  // usage proposé dans computeArrows / eventArrows / possibleArrows, après headGuard :
  //   const r = hardTruncate([bi, bj], [hi, hj], 0.5); hi = r.head[0]; hj = r.head[1];
  //   et, pour l'info-bulle : if (r.code) note = tr("arrow_hard_barrier") (« arrêtée par une barrière naturelle »).
  // Contrôle suggéré (check_arrows, diagnostic non bloquant) : aucune tête de flèche au-delà d'une maille dure.
})();
