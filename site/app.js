(function(){
"use strict";
const D = window.DATA, G = window.GEO, O = window.OSINT, FR = window.FRONT;
const FRONT_V = {"combat": "5fc9d7ab4b", "equilibre": "d61894e7d1", "mcsd": "336766faa8", "nowcast": "95ef4a8b8b", "ru": "3be56b1e70", "sans_dsm": "2137fc3519", "sans_enveloppes": "eaaaccfec7", "sans_gsua": "fb1195d689", "sans_indices": "eb631fb8a8", "sans_isw": "7204391fa3", "sans_medias": "b2cb9b17f8", "sans_modru": "116a72119e", "sans_presence": "e432ec76fa", "sans_wiki": "790ac0f34b", "tiers": "b5e93517b4", "ua": "11ab8e7462"};
const DUMMY = {}; const $ = id => document.getElementById(id) || DUMMY[id] || (DUMMY[id] = document.createElement("div"));
const L = d3.formatLocale({decimal:",",thousands:" ",grouping:[3],currency:[""," €"]});
const fmt = L.format(",d"), pct = v => String(v).replace(".", ",") + " %";
const MOIS = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"];
const MC = ["janv.","févr.","mars","avr.","mai","juin","juil.","août","sept.","oct.","nov.","déc."];
const monthLabel = i => { const [y,m] = D.months[i].split("-"); return MOIS[+m-1] + " " + y; };
const dateFr = s => { const [y,m,d] = s.split("-"); return (+d) + " " + MOIS[+m-1] + " " + y; };

// ---------- thème ----------
const root = document.documentElement;
try { const t = localStorage.getItem("uaf-theme"); if (t) root.dataset.theme = t; } catch(e) {}
$("theme").onclick = () => {
  const dark = getComputedStyle(root).colorScheme.indexOf("dark") >= 0;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("uaf-theme", root.dataset.theme); } catch(e) {}
  readColors(); legend(); draw(); drawChart(); drawYearly(); drawSky(); oryxTable(); setStep(stepIdx);
};
let C = {};
function readColors(){
  const cs = getComputedStyle(root);
  ["bg","road","uactl","uactl-rgb","front","front-rgb","grey-rgb","ucdp","arc","map-bg","loss-now","loss-old","land","ua","admin","border","water","label","fire-now","fire-old","cool","ember","accent","faint","rule","muted","ink"].forEach(k => C[k] = cs.getPropertyValue("--"+k).trim());
  C.dark = cs.colorScheme.indexOf("dark") >= 0;
}
readColors();

// ---------- données dérivées ----------
const NM = D.months.length;
const covered = D.series.map(s => s[3] > 0);
const steps = d3.range(NM);
const lastIdx = steps[steps.length-1];
const partial = D.lastDay.slice(0,7) === D.months[lastIdx];
const P = D.pts, NP = P.length / 5;
const regC = Object.fromEntries(Object.entries(D.regionNames).map(([k,v]) => [k, v[1]]));
const exTot = D.series.reduce((a,s) => a + s[2], 0);
let exUA = 0, exRU = 0;
for (const [k,v] of Object.entries(D.regions)) { const s = d3.sum(v); if (regC[k] === "RUS") exRU += s; else if (regC[k] === "UKR") exUA += s; }
const peak = d3.greatestIndex(D.series, s => s[2]);
const nCells = D.cells.reduce((a,c) => a + c.length/4, 0);

$("lastDay").textContent = dateFr(D.lastDay);
$("gen").textContent = D.generated.replace("T"," ").replace("Z"," UTC");
$("nFixed").textContent = fmt(D.fixedSources);
$("calib").textContent = Object.entries(D.calibration).map(([y,v]) => y + " : " + pct(v)).join(", ");
$("nrtStart").textContent = dateFr(D.nrtFirst || D.lastDay);
$("spLast").textContent = D.spLast ? dateFr(D.spLast) : "—";
$("gapN").textContent = D.gapDays.length;

const fLast = FR.months.filter(Boolean).pop();
$("stats").innerHTML = [
  [fmt(Math.round(fLast.a.equilibre.tout[0]/100)*100) + "\u00a0km²", "occupés au " + dateFr(fLast.date) + " (intervalle à 95 % : " + fmt(Math.round(fLast.mc.tout[0]/100)*100) + " – " + fmt(Math.round(fLast.mc.tout[1]/100)*100) + " ; vue russe " + fmt(Math.round(fLast.a.ru.tout[0]/100)*100) + ", vue ukrainienne " + fmt(Math.round(fLast.a.ua.tout[0]/100)*100) + ")"],
  [fmt(Math.round(exTot/100)*100), "détections de feux au-dessus de la normale depuis le 24 février 2022"],
  [Math.round(exUA/(exUA+exRU)*100) + " %", "de l'excès en Ukraine, le reste dans les régions russes frontalières"],
  [MC[+D.months[peak].slice(5)-1] + " " + D.months[peak].slice(0,4), "mois record : " + fmt(D.series[peak][2]) + " détections en excès"]
].map(([b,s]) => `<div class="stat"><b>${b}</b><span>${s}</span></div>`).join("");

// ---------- carte ----------
const canvas = $("map"), ctx = canvas.getContext("2d");
let W = 0, H = 0, dpr = 1, proj, paths = {}, px, py, cellXY, cities = [];
let tr = d3.zoomIdentity, cur = lastIdx, mode = "pts", showRef = false, showFire = false, showLoss = false, showFront = true, showLines = true, showUcdp = false, showArcs = false, perim = "tout";
const lossOn = O.groups.map(() => true);
const cutIdx = D.months.indexOf(O.cutoff);
if (cutIdx >= 0 && cutIdx < cur) cur = cutIdx;  // par défaut : dernier mois où toutes les couches sont publiées
const WSP = O.ws.pts, NW = WSP.length / 5;
let wx, wy, gAff = [0, 0, 1];

// ---------- front : décodage ----------
const GX = FR.grid.nx, GY = FR.grid.ny;
const cellPlace = new Int32Array(GX * GY);
{ const b = atob(FR.runs); let o = 0;
  for (let i = 0; i < b.length; i += 3) { const v = b.charCodeAt(i) | b.charCodeAt(i+1) << 8, n = b.charCodeAt(i+2); cellPlace.fill(v === 65535 ? -1 : v === 65534 ? -2 : v, o, o + n); o += n; } }
const pFlags = Uint8Array.from(atob(FR.flags), c => c.charCodeAt(0));
const SRCN = {dsm:"DeepStateMap", isw:"ISW / CTP", wiki:"Wikipédia", boost:"Wikipédia + VIINA"};
const SRCCAMP = {dsm:"ukrainien", isw:"tiers", wiki:"tiers", boost:"tiers"};
const MODEN = {equilibre:"tous (équilibré), cartes seules", nowcast:"tous (équilibré), avec signaux de combat", ua:"vue ukrainienne seule", ru:"vue russe seule", tiers:"sources tierces seules"};
const STN = ["—", "ukrainien", "russe", "contesté"];
// les navigateurs (mobiles surtout) restaurent l'état des cases au rechargement ou au retour arrière : la page
// repart toujours de l'état par défaut du HTML, sinon des cases cochées n'afficheraient rien
document.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = cb.defaultChecked; cb.setAttribute("autocomplete", "off"); });
document.querySelectorAll("select").forEach(sl => { sl.value = [...sl.options].find(o => o.defaultSelected)?.value ?? sl.options[0].value; sl.setAttribute("autocomplete", "off"); });
window.addEventListener("pageshow", e => { if (e.persisted) location.reload(); });
let fmode = "equilibre", fvar = null, showStates = false;
const rawOn = {dsm: false, isw: false, wiki: false, modru: false, gsua: false};
// nowcast (signaux de combat) : par défaut sur la vue équilibrée sans variante de sources ; « cartes seules » sinon
let nowcastOn = !!(document.getElementById("lyrNowcast") && document.getElementById("lyrNowcast").checked), showCombat = false;
const amode = () => fmode === "equilibre" && !fvar && nowcastOn && FR.nowcast ? "nowcast" : fmode;
const fieldName = () => fvar || amode();
const mcOf = mo => amode() === "nowcast" && mo.mcNow ? mo.mcNow : mo.mc;
const BASEMAP_V = "7934ff79a1";
let showBase = !!(document.getElementById("lyrBase") && document.getElementById("lyrBase").checked), baseGeo = null;
function loadBasemap(){
  if (window.BASEMAP || document.getElementById("bmScript")) return;
  const sc = document.createElement("script"); sc.id = "bmScript"; sc.src = `basemap.js?v=${BASEMAP_V}`;
  sc.onload = () => { baseGeo = null; draw(); }; document.head.appendChild(sc);
}
function baseProject(){
  if (baseGeo || !window.BASEMAP || !proj) return baseGeo;
  const B = window.BASEMAP, r1 = new Path2D(), r2 = new Path2D();
  for (const [cls, ref, f] of B.roads) {
    const t = cls === 1 ? r1 : r2;
    for (let i = 0; i < f.length; i += 2) { const q = proj([f[i], f[i+1]]); i ? t.lineTo(q[0], q[1]) : t.moveTo(q[0], q[1]); }
  }
  // localités : on écarte celles déjà présentes dans le fond principal (moins de 5 km)
  const near = (lon, lat) => G.cities.some(c => Math.abs(c[0] - lon) < 0.07 && Math.abs(c[1] - lat) < 0.045);
  const places = B.places.filter(p => !near(p[0], p[1])).map(p => { const q = proj([p[0], p[1]]); return {x: q[0], y: q[1], n: p[2], pop: p[3]}; });
  const labels = B.labels.map(l => { const q = proj([l[0], l[1]]); return {x: q[0], y: q[1], n: l[2], cls: l[3]}; });
  return baseGeo = {r1, r2, places, labels};
}
const monthCodes = []; { let codes = new Uint16Array(FR.nPlaces);
  for (const m of FR.months) { if (!m) { monthCodes.push(null); continue; } codes = codes.slice(); const d = m.d; for (let i = 0; i < d.length; i += 2) codes[d[i]] = d[i+1]; monthCodes.push(codes); } }
// axe du curseur : mois (instantanés mensuels) jusqu'à la période récente, puis un pas tous les FR.stepDays jours,
// le dernier à J-7. Chaque pas porte son mois (cur, pour les feux et les couches OSINT) et son instantané du front.
const NMF = FR.nMonths || FR.months.length, DAYS = FR.steps || [];
const firstDay = DAYS.length ? FR.months[NMF].date : null;
const AX = [];
for (let i = 0; i < NM; i++) {
  const f = i < NMF && FR.months[i] ? i : -1;
  if (firstDay && D.months[i] >= firstDay.slice(0, 7)) break;
  AX.push({m: i, f});
}
DAYS.forEach((d, j) => { const m = D.months.indexOf(d.slice(0, 7)); if (m >= 0) AX.push({m, f: NMF + j, d}); });
for (let i = (AX.length ? AX[AX.length - 1].m + 1 : 0); i < NM; i++) if (!firstDay || D.months[i] > DAYS[DAYS.length - 1].slice(0, 7)) AX.push({m: i, f: -1});
let stepIdx = AX.length - 1;
const lastFront = FR.months.reduce((a, m, i) => m ? i : a, -1);
// après le dernier instantané (règle des 7 jours), on garde le dernier front connu plutôt qu'une carte vide
const frontIdx = () => { const a = AX[stepIdx]; if (!a) return -1; if (a.f >= 0) return a.f;
  return stepIdx > AX.findIndex(x => x.f === lastFront) ? lastFront : -1; };
{ const k = AX.findIndex(x => x.f === lastFront); if (k >= 0) { stepIdx = k; cur = AX[k].m; } }  // ouverture : dernier pas du front
// instantané le plus récent daté au plus tard (date de fi) − jours (+ 3 jours de tolérance) : pour les vitesses
const FDATE = FR.months.map(m => m ? Date.parse(m.date) : null);
function frameAt(fi, days){
  const t = FDATE[fi] - (days - 3) * 864e5; let best = -1, bt = -Infinity;
  for (let i = 0; i < FDATE.length; i++) if (FDATE[i] !== null && FDATE[i] <= t && FDATE[i] > bt && i !== fi) { bt = FDATE[i]; best = i; }
  return best;
}
// libellé d'un instantané : mois, ou date du pas récent
const frameLabel = fi => { if (fi < NMF) return monthLabel(fi); const [y, mo, d] = FR.months[fi].date.split("-"); return `${+d} ${MOIS[+mo - 1]} ${y}`; };
const stepLabel = s => { const a = AX[s]; if (!a || !a.d) return monthLabel(a ? a.m : cur); const [y, mo, d] = a.d.split("-"); return `${+d} ${MOIS[+mo - 1]} ${y}`; };
// champs de probabilité : un fichier par vue (la vue équilibrée est chargée d'office), différences mois à mois
// bande russe : distance à l'Ukraine (en mailles), pour estomper la couleur vers l'intérieur de la Russie
const bandFade = new Float32Array(GX * GY).fill(1);
{ const dist = new Int16Array(GX * GY).fill(-1), q = [];
  for (let c = 0; c < GX * GY; c++) if (cellPlace[c] === -2 && [c - 1, c + 1, c - GX, c + GX].some(x => x >= 0 && x < GX * GY && cellPlace[x] >= 0)) { dist[c] = 0; q.push(c); }
  for (let h = 0; h < q.length; h++) { const c = q[h]; for (const x of [c - 1, c + 1, c - GX, c + GX]) if (x >= 0 && x < GX * GY && cellPlace[x] === -2 && dist[x] < 0) { dist[x] = dist[c] + 1; q.push(x); } }
  const span = 80 / (FR.grid.size / 1000);
  for (let c = 0; c < GX * GY; c++) if (cellPlace[c] === -2) bandFade[c] = Math.max(0, 1 - Math.max(0, dist[c]) / span) ** 1.5; }
// mailles du modèle : Ukraine (indice de localité ≥ 0) et bande russe frontalière (-2), sans découpage à la frontière
const UCELLS = []; for (let c = 0; c < GX * GY; c++) if (cellPlace[c] !== -1) UCELLS.push(c);
const fieldCache = {};
function viewFields(view){
  if (fieldCache[view]) return fieldCache[view];
  const enc = window.FRONTP && window.FRONTP[view]; if (!enc) return null;
  const out = []; let prev = new Uint8Array(UCELLS.length);
  for (const e of enc) {
    if (!e) { out.push(null); continue; }
    const b = atob(e), q = new Uint8Array(UCELLS.length); let o = 0, i = 0;
    while (i < b.length) { const v = b.charCodeAt(i++); let n = 0, sh = 0, c; do { c = b.charCodeAt(i++); n |= (c & 127) << sh; sh += 7; } while (c & 128);
      if (v === 255) q.set(prev.subarray(o, o + n), o); else q.fill(v, o, o + n); o += n; }
    out.push(q); prev = q;
  }
  return fieldCache[view] = out;
}
function ensureView(view, then){
  if (window.FRONTP && window.FRONTP[view]) return then();
  const sc = document.createElement("script"); sc.src = `front_${view}.js?v=${FRONT_V[view] || ""}`; sc.onload = then; document.head.appendChild(sc);
}
const oc = document.createElement("canvas"); oc.width = GX; oc.height = GY;
const ocB = document.createElement("canvas"); ocB.width = GX; ocB.height = GY;
const octx = oc.getContext("2d");
let pgrid = new Float32Array(GX * GY), frontFor = -2, frontTheme = "", isoPaths = [], prePath = null;
function ringsPath(polys){
  const p = new Path2D();
  for (const poly of polys) for (const ring of poly) { ring.forEach(([x,y], i) => i ? p.lineTo(x, y) : p.moveTo(x, y)); p.closePath(); }
  return p;
}
// masque « Russie » sur la grille : hors du domaine du modèle, la Russie compte pour p = 1 dans le tracé des
// isolignes (pas de fausse ligne au bord de la bande frontalière)
let ruMask = null;
function buildRuMask(){
  if (ruMask || !proj) return;
  const cv = document.createElement("canvas"); cv.width = GX; cv.height = GY;
  const cx = cv.getContext("2d"), gp = d3.geoPath(proj);
  cx.setTransform(1 / gAff[2], 0, 0, 1 / gAff[2], -gAff[0] / gAff[2], -gAff[1] / gAff[2]);
  cx.fillStyle = "#000";
  for (const f of G.countries) if (f.properties.iso === "RU") cx.fill(new Path2D(gp(f) || ""));
  const d = cx.getImageData(0, 0, GX, GY).data;
  ruMask = new Uint8Array(GX * GY);
  for (let c = 0; c < GX * GY; c++) ruMask[c] = d[c * 4 + 3] > 127 ? 1 : 0;
}
// ---------- flèches « qui pousse où » ----------
// v_n = −Δp / |∇p| sur la ligne 50 % : déplacement de la ligne en km/mois, sur chacun des trois derniers mois ;
// critère : |v| moyen ≥ 0,7 km/mois, même signe trois mois sur trois (deux sur trois au-delà de 1 km/mois), ou saut
// d'un seul mois ≥ 3 km/mois ; pas de seuil Monte Carlo (il surestime le bruit de Δp, les erreurs de position des
// sources étant corrélées d'un mois à l'autre). Calibré sur les cas connus de scripts/check_arrows.py.
// réglages des flèches (calibrés sur les cas connus de scripts/check_arrows.py)
// vmin 0,7 km/mois ; même signe 3 mois sur 3 sous 1 km/mois, 2 sur 3 au-delà ; saut ≥ 3 km/mois en un mois accepté
const ARROW_PARAMS = {vmin: 0.7, need: 3, vstrong: 1.0, jump: 3, jumpHl: 0, hl: 0, mode: "geo"};
// logit borné (p quantifié en 64 niveaux : 0 et 1 exclus)
const lgt = p => { const q = Math.min(1 - 0.5 / 63, Math.max(0.5 / 63, p)); return Math.log(q / (1 - q)); };
window.__uafSetArrowParams = o => { Object.assign(ARROW_PARAMS, o); arrowCache = {}; };
let showArrows = !!(document.getElementById("lyrArrows") && document.getElementById("lyrArrows").checked), arrowCache = {}, arrowList = [];
function loadMcsd(then){
  if (window.FRONTP && window.FRONTP.mcsd) return then();
  const sc = document.createElement("script"); sc.src = `front_mcsd.js?v=${FRONT_V.mcsd || ""}`; sc.onload = then; document.head.appendChild(sc);
}
const sdCache = {};
function sdField(i){
  if (!window.FRONTP || !window.FRONTP.mcsd) return null;
  if (!sdCache.all) { const save = fieldCache.mcsd; sdCache.all = viewFields("mcsd"); }
  return sdCache.all && sdCache.all[i];
}
// champ complet d'un mois (grille), pour la ligne 50 % et les gradients
const fullCache = {};
function fullField(view, i){
  const key = view + ":" + i; if (fullCache[key]) return fullCache[key];
  const F = viewFields(view); if (!F || !F[i]) return null;
  const g = new Float32Array(GX * GY), q = F[i];
  for (let c = 0; c < GX * GY; c++) g[c] = ruMask && ruMask[c] && cellPlace[c] === -1 ? 1 : 0;
  for (let u = 0; u < UCELLS.length; u++) g[UCELLS[u]] = q[u] / 63;
  return fullCache[key] = g;
}
// sommets valides de la ligne 50 % : à l'intérieur du domaine, ni sur la frontière ni sur une côte
function validVertex(x, y){
  const i = Math.floor(x - 0.5), j = Math.floor(y - 0.5);
  if (i < 1 || j < 1 || i >= GX - 2 || j >= GY - 2) return false;
  let ua = 0, band = 0;
  for (let dj = -1; dj <= 2; dj++) for (let di = -1; di <= 2; di++) {
    const k = cellPlace[(j + dj) * GX + i + di];
    if (k === -1) return false; if (k === -2) band++; else ua++;
  }
  return !(ua && band);
}
function lines50(view, i){
  const g = fullField(view, i); if (!g) return [];
  const cs = d3.contours().size([GX, GY]).thresholds([0.5])(g)[0].coordinates, runs = [];
  for (const poly of cs) for (const ring of poly) {
    let cur = [];
    for (const [x, y] of ring) { if (validVertex(x, y)) cur.push([x, y]); else { if (cur.length > 2) runs.push(cur); cur = []; } }
    if (cur.length > 2) runs.push(cur);
  }
  return runs;
}
// normale locale d'une polyligne au sommet t (tangente sur ±2 à ±6 sommets, la première non dégénérée)
function polyNormal(run, t, w0){
  for (const w of [w0 || 2, (w0 || 2) + 1, (w0 || 2) + 2, (w0 || 2) + 4]) {
    const pa = run[Math.max(0, t - w)], pb = run[Math.min(run.length - 1, t + w)];
    const nx = -(pb[1] - pa[1]), ny = pb[0] - pa[0], nl = Math.hypot(nx, ny);
    if (nl > 1.5) return [nx / nl, ny / nl];
  }
  return null;
}
// valeur interpolée d'un champ en coordonnées de grille continues (centres des mailles en i + 0,5)
function sampleAt(g, x, y){
  const u = x - 0.5, v = y - 0.5, i = Math.floor(u), j = Math.floor(v), fx = u - i, fy = v - j;
  if (i < 0 || j < 0 || i >= GX - 1 || j >= GY - 1) return null;
  const c = j * GX + i;
  return (g[c] * (1 - fx) + g[c + 1] * fx) * (1 - fy) + (g[c + GX] * (1 - fx) + g[c + GX + 1] * fx) * fy;
}
// position (km, signée le long de (nx, ny), normale orientée vers le camp russe) de la ligne 50 % d'un mois, à 30 km
// au plus ; null sinon. On ne cherche que d'un côté : si le point était russe ce mois-là (p > 0,5), la ligne était du
// côté ukrainien (−n), et inversement. Chercher des deux côtés apparierait parfois la ligne à une autre (la frontière,
// pendant l'incursion de Koursk) et inverserait le sens.
function crossing(g, x, y, nx, ny){
  const km = FR.grid.size / 1000, step = 0.5, R = 30 / km, f = s => { const v = sampleAt(g, x + s * nx, y + s * ny); return v === null ? null : v - 0.5; };
  const f0 = f(0); if (f0 === null) return null;
  if (f0 === 0) return 0;
  const dir = f0 > 0 ? -1 : 1;
  let a = 0, fa = f0;
  for (let s = step; s <= R; s += step) {
    const fb = f(dir * s); if (fb === null) return null;
    if (Math.sign(fb) !== Math.sign(fa)) return dir * (a + (s - a) * fa / (fa - fb)) * km;
    a = s; fa = fb;
  }
  return null;
}
function gradAt(g, x, y){
  const i = Math.max(1, Math.min(GX - 2, Math.floor(x))), j = Math.max(1, Math.min(GY - 2, Math.floor(y))), c = j * GX + i, km = FR.grid.size / 1000;
  return [(g[c + 1] - g[c - 1]) / (2 * km), (g[c + GX] - g[c - GX]) / (2 * km), c];
}
// flèches : la ligne 50 % du mois affiché est découpée en tronçons de L km ; sur chaque tronçon, v_n = −Δp/|∇p|
// (Δp sur trois mois, ramené au mois), moyenné et pondéré par |∇p| sur les sommets de la ligne ; la flèche part du
// milieu du tronçon, selon la normale locale de la ligne, vers le camp qui recule.
function computeArrows(fi, Lkm, view){
  view = view || fieldName();
  const key = view + ":" + fi + ":" + Lkm;
  if (arrowCache[key]) return arrowCache[key];
  // instantanés de référence : 30, 60 et 90 jours avant (mois précédents, ou pas récents de 3 jours)
  const i1 = fi >= 0 ? frameAt(fi, 30) : -1, i2 = i1 >= 0 ? frameAt(i1, 30) : -1, i3 = i2 >= 0 ? frameAt(i2, 30) : -1, IDX = [fi, i1, i2, i3];
  const G4 = IDX.map(i => i >= 0 ? fullField(view, i) : null), g1 = G4[0];
  const DD = [0, 1, 2].map(m => IDX[m + 1] >= 0 ? Math.max(1, (FDATE[IDX[m]] - FDATE[IDX[m + 1]]) / 864e5) : 30);
  if (G4.some(x => !x)) return arrowCache[key] = [];
  const km = FR.grid.size / 1000, out = [], AP = ARROW_PARAMS;
  for (const run of lines50(view, fi)) {
    // abscisse curviligne
    const acc = [0]; for (let t = 1; t < run.length; t++) acc.push(acc[t - 1] + Math.hypot(run[t][0] - run[t - 1][0], run[t][1] - run[t - 1][1]) * km);
    const total = acc[acc.length - 1], nseg = Math.max(1, Math.round(total / Lkm));
    if (total < Lkm * 0.4) continue;
    for (let sgi = 0; sgi < nseg; sgi++) {
      const a0 = total * sgi / nseg, a1 = total * (sgi + 1) / nseg, mid = (a0 + a1) / 2;
      // vitesse mensuelle du tronçon pour chacun des trois derniers mois : v_m = Σ |∇p| · (−(p_m − p_m−1) / |∇p|) / Σ |∇p|
      let sw = 0, n = 0, ax = 0, ay = 0;
      const vm = [0, 0, 0];
      for (let t = 0; t < run.length; t++) {
        if (acc[t] < a0 || acc[t] > a1) continue;
        const [x, y] = run[t], [gx, gy, c] = gradAt(g1, x, y), gm = Math.hypot(gx, gy);
        n++;
        if (gm < 1e-3) continue;
        if (AP.mode === "geo" || AP.mode === "logit") {
          // déplacement en logit : p = sigmoïde(d/σ), donc logit p est linéaire en distance et −Δlogit/|∇logit|
          // mesure le déplacement de la ligne sans saturer (un effondrement de poche n'est plus plafonné à 0,5/|∇p|)
          const p0 = Math.min(0.98, Math.max(0.02, g1[c])), gl = gm / (p0 * (1 - p0));
          // géométrique : position de la ligne 50 % de chaque mois le long de la normale (vers le camp russe), à
          // ±30 km ; le déplacement d'un mois à l'autre est l'écart entre ces positions, quelle que soit son ampleur
          const sx = AP.mode === "geo" ? G4.map(G => crossing(G, x, y, gx / gm, gy / gm)) : null;
          for (let m = 0; m < 3; m++) vm[m] += gm * (sx && sx[m] !== null && sx[m + 1] !== null ? sx[m] - sx[m + 1]
            : -(lgt(G4[m][c]) - lgt(G4[m + 1][c])) / gl);
        } else for (let m = 0; m < 3; m++) vm[m] += -(G4[m][c] - G4[m + 1][c]);  // pondéré par |∇p| : gm · (−Δp/gm)
        sw += gm;
      }
      if (!n || !sw) continue;
      for (let m = 0; m < 3; m++) vm[m] /= sw * DD[m] / 30;  // km par 30 jours
      // moyenne des trois derniers mois : simple, ou exponentielle (demi-vie AP.hl mois) pour qu'un retournement
      // net du dernier mois (contre-offensive) ressorte sans attendre que la moyenne sur trois mois change de signe
      // un saut du dernier mois (≥ AP.jump km) est jugé sur cette moyenne exponentielle (demi-vie AP.jumpHl mois) :
      // un retournement net, comme la contre-offensive au nord de Lyman en septembre 2026, ressort sans attendre
      // que la moyenne sur trois mois change de signe
      const jump = Math.abs(vm[0]) >= AP.jump, hl = jump ? AP.jumpHl : AP.hl;
      const wt = hl ? [1, 0.5 ** (1 / hl), 0.5 ** (2 / hl)] : [1, 1, 1];
      const v = (wt[0] * vm[0] + wt[1] * vm[1] + wt[2] * vm[2]) / (wt[0] + wt[1] + wt[2]);
      // critère : voir ARROW_PARAMS
      // exception : un saut brutal d'un seul mois (≥ 3 km/mois, comme l'incursion de Koursk) n'a pas d'historique
      // de signe ; à cette amplitude, le bruit de position des sources n'explique plus le déplacement
      const same = vm.filter(x => Math.sign(x) === Math.sign(v) && Math.abs(x) > 0.05).length;
      if (Math.abs(v) < AP.vmin || (!jump && same < (Math.abs(v) >= AP.vstrong ? 2 : AP.need))) continue;
      if (hl && Math.sign(vm[0]) !== Math.sign(v)) continue;  // le dernier mois doit aller dans le sens de la flèche
      // ancrage : parmi les sommets du tiers central, celui dont la normale locale suit le mieux l'orientation
      // générale du tronçon (pas de flèche de travers sur un zigzag)
      const ws = Math.max(2, Math.round(Lkm / km / 6));
      let nn = null, bestAng = 9;
      for (let t = 0; t < run.length; t++) {
        if (Math.abs(acc[t] - mid) > (a1 - a0) / 3) continue;
        const loc = polyNormal(run, t), sm = polyNormal(run, t, ws);
        if (!loc || !sm) continue;
        const ang = Math.acos(Math.min(1, Math.abs(loc[0] * sm[0] + loc[1] * sm[1])));
        if (ang < bestAng) { bestAng = ang; nn = loc; ax = run[t][0]; ay = run[t][1]; }
      }
      if (!nn) continue;
      let [nx, ny] = nn;
      const [gx, gy] = gradAt(g1, ax, ay); if (nx * gx + ny * gy < 0) { nx = -nx; ny = -ny; }
      const sgn = v > 0 ? 1 : -1;
      out.push({gx: ax, gy: ay, dx: nx * sgn, dy: ny * sgn, v: Math.abs(v), ru: v < 0, L: Lkm, segKm: Math.round(a1 - a0), vm: vm.map(x => +x.toFixed(2))});
    }
  }
  return arrowCache[key] = out;
}
// vue d'ensemble : flèches des tronçons de 20, 50 et 100 km (les longs tronçons captent les poussées larges et lentes),
// éclaircies : au plus une tous les ~30 km, la plus rapide d'abord ; tout tronçon en mouvement a ainsi une flèche
// affichée à moins de 30 km (contrôlé par scripts/check_arrows.py)
function overviewArrows(fi, view){
  const kept = [], minD = 30 / (FR.grid.size / 1000), all = [20, 50, 100].flatMap(L => computeArrows(fi, L, view));
  for (const a of all.sort((x, y) => y.v - x.v)) if (!kept.some(b => Math.hypot(b.gx - a.gx, b.gy - a.gy) < minD)) kept.push(a);
  return kept;
}
// flèches d'événements : changements de contrôle annoncés et datés (journal DeepState, état-major UA, MoD RF, bascules
// des cartes) sur les 30 jours précédant l'instantané, pondérés par la fiabilité mesurée de leur source (part confirmée
// par une autre carte dans les 90 jours). Regroupés par camp à 15 km ; seuil : 2 localités, ou 1 source fiable à 80 %.
// La flèche part de la ligne 50 % de l'instantané précédent vers le barycentre des localités basculées.
const evCache = {}, EV_DECL = new Set(["dsmlog", "gsua", "modru"]);
function eventArrows(fi){
  if (evCache[fi]) return evCache[fi];
  const mo = FR.months[fi], E = FR.events; if (!mo || !mo.ev || !E) return evCache[fi] = [];
  const rel = E.reliability, src = E.sources, km = FR.grid.size / 1000, out = [];
  for (const camp of [1, 0]) {
    const pl = new Map();
    for (let j = 0; j < mo.ev.length; j += 4) {
      if (mo.ev[j + 2] !== camp) continue;
      const key = mo.ev[j].toFixed(2) + "," + mo.ev[j + 1].toFixed(2), sn = src[mo.ev[j + 3]], w = rel[sn] || 0.3;
      if (!pl.has(key)) { const q = proj([mo.ev[j], mo.ev[j + 1]]); pl.set(key, {gx: (q[0] - gAff[0]) / gAff[2], gy: (q[1] - gAff[1]) / gAff[2], w, srcs: new Set([sn])}); }
      else { const p = pl.get(key); p.w = Math.max(p.w, w); p.srcs.add(sn); }
    }
    const pts = [...pl.values()].sort((a, b) => b.w - a.w), cl = [];
    for (const p of pts) {
      const c = cl.find(c => Math.hypot(c.sx - p.gx, c.sy - p.gy) * km <= 15);
      if (c) { c.items.push(p); } else cl.push({sx: p.gx, sy: p.gy, items: [p]});
    }
    const fp = frameAt(fi, 30), prevLines = fp >= 0 ? lines50(fieldName(), fp) : [];
    for (const c of cl) {
      const n = c.items.length, sw = c.items.reduce((a, p) => a + p.w, 0), mw = Math.max(...c.items.map(p => p.w));
      // corroboration : une seule carte qui recode ses localités (Wikipédia, DeepStateMap) est une révision, pas une
      // poussée ; il faut deux sources, ou une annonce datée (journal DeepStateMap, état-major UA, MoD RF)
      const srcs = new Set(c.items.flatMap(p => [...p.srcs])), decl = [...srcs].some(x => EV_DECL.has(x));
      const ok = srcs.size >= 2 || (decl && n >= 2) || (srcs.has("dsmlog") && mw >= 0.8);
      if (!ok) continue;
      const cx = c.items.reduce((a, p) => a + p.gx * p.w, 0) / sw, cy = c.items.reduce((a, p) => a + p.gy * p.w, 0) / sw;
      // une prise annoncée en pleine zone adverse (p < 0,3 pour une prise russe, > 0,7 pour une ukrainienne) n'est pas
      // reflétée par les cartes : invraisemblable ou trop précoce, pas de flèche
      const pc = fullField(fieldName(), fi), pv = pc ? pc[Math.round(cy - 0.5) * GX + Math.round(cx - 0.5)] : 0.5;
      if (camp === 1 ? pv < 0.3 : pv > 0.7) continue;
      let bx = null, by = null, bd = 1e9;
      for (const run of prevLines) for (const [x, y] of run) { const d = Math.hypot(x - cx, y - cy); if (d < bd) { bd = d; bx = x; by = y; } }
      // une prise annoncée à plus de 15 km de l'ancienne ligne n'est pas plausible (ex. : « Sviatohirsk » annoncée
      // par le MoD RF le 2 septembre 2026, loin derrière les lignes ukrainiennes) : pas de flèche
      if (bx === null || bd * km > 15) continue;
      out.push({ax: bx, ay: by, cx, cy, ru: camp === 1, n, w: sw, dkm: bd * km});
    }
  }
  return evCache[fi] = out;
}
function drawEventArrows(k){
  const fi = frontIdx(); if (fi < 0) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const a of eventArrows(fi)) {
    const sx = (gAff[0] + a.ax * gAff[2]) * k + tr.x, sy = (gAff[1] + a.ay * gAff[2]) * k + tr.y;
    let ex = (gAff[0] + a.cx * gAff[2]) * k + tr.x, ey = (gAff[1] + a.cy * gAff[2]) * k + tr.y;
    let dx = ex - sx, dy = ey - sy, L = Math.hypot(dx, dy);
    if (L < 1e-3) continue;
    dx /= L; dy /= L;
    const ov = k < 1.6, w = ov ? Math.min(3, 1.4 + a.w * 0.3) : Math.min(6, 1.6 + a.w * 0.9), col = a.ru ? C.front : C.uactl, hs = (ov ? 2 : 3) + w * 1.5, hx = -dy, hy = dx;
    const Lmin = hs * 1.6 + 12;  // toujours un fût visible au-delà de la pointe
    if (L < Lmin) { ex = sx + dx * Lmin; ey = sy + dy * Lmin; L = Lmin; }
    const path = () => { ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - dx * hs, ey - dy * hs); };
    const head = () => { ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - dx * hs * 1.6 + hx * hs, ey - dy * hs * 1.6 + hy * hs); ctx.lineTo(ex - dx * hs * 1.6 - hx * hs, ey - dy * hs * 1.6 - hy * hs); ctx.closePath(); };
    // contour sombre : distingue les flèches d'annonces des flèches de gradient
    ctx.globalAlpha = 0.95; ctx.strokeStyle = C.ink; ctx.lineCap = "round"; ctx.lineJoin = "round";
    const cw = ov ? 1.8 : 2.6;
    ctx.lineWidth = w + cw; path(); ctx.stroke(); ctx.lineWidth = cw; head(); ctx.stroke();
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w; path(); ctx.stroke(); head(); ctx.fill();
    arrowList.push({x: (sx + ex) / 2, y: (sy + ey) / 2, a: {ru: a.ru, v: 0, ev: a.n}});
  }
  ctx.globalAlpha = 1;
}
// diagnostic (headless) : flèches d'événements d'un mois en coordonnées géographiques
window.__uafEvents = fi => eventArrows(fi).map(a => { const p = proj.invert([gAff[0] + a.cx * gAff[2], gAff[1] + a.cy * gAff[2]]), b = proj.invert([gAff[0] + a.ax * gAff[2], gAff[1] + a.ay * gAff[2]]);
  return {lon: +p[0].toFixed(2), lat: +p[1].toFixed(2), from: [+b[0].toFixed(2), +b[1].toFixed(2)], ru: a.ru, n: a.n, w: +a.w.toFixed(2), dkm: +a.dkm.toFixed(1)}; });
// diagnostic (headless) : état d'affichage (zoom, info-bulle, pas) pour les tests mobiles
window.__uafState = () => ({k: tr.k, x: tr.x, y: tr.y, tip: tip.style.display, step: stepIdx, nsteps: AX.length, fvar, nowcast: amode() === "nowcast",
  checks: Object.fromEntries([...document.querySelectorAll('input[type="checkbox"]')].map(c => [c.id || c.dataset.raw || c.dataset.var, c.checked]))});
// diagnostic (headless) : recadrage et mois
window.__uafGoto = (month, lon, lat, k) => { const i = D.months.indexOf(month); if (i >= 0) setMonth(i); zoomTo(lon, lat, k); };
// diagnostic (headless) : flèches d'un mois en coordonnées géographiques
window.__uafArrows = (fi, L) => (L === "overview" ? overviewArrows(fi, "equilibre") : computeArrows(fi, L, "equilibre")).map(a => { const ll = proj.invert([gAff[0] + a.gx * gAff[2], gAff[1] + a.gy * gAff[2]]); return {lon: +ll[0].toFixed(2), lat: +ll[1].toFixed(2), ru: a.ru, v: +a.v.toFixed(2), dir: [+a.dx.toFixed(2), +a.dy.toFixed(2)], vm: a.vm}; });
// diagnostic (headless) : p(contrôle russe) en un point, km² russes et sommets de la ligne 50 % dans une boîte
const cellOf = (lon, lat) => { const q = proj([lon, lat]); return Math.floor((q[1] - gAff[1]) / gAff[2]) * GX + Math.floor((q[0] - gAff[0]) / gAff[2]); };
const inBox = (gx, gy, b) => { const ll = proj.invert([gAff[0] + gx * gAff[2], gAff[1] + gy * gAff[2]]); return ll[0] >= b[0] && ll[0] <= b[1] && ll[1] >= b[2] && ll[1] <= b[3]; };
// diagnostic (headless) : déplacement du dernier mois (km, signé) de tous les tronçons de L km, sans aucun seuil
window.__uafMoves = (fi, L) => { const save = {...ARROW_PARAMS}; Object.assign(ARROW_PARAMS, {vmin: 0, need: 0, vstrong: 0, jump: 1e9, hl: 0, jumpHl: 0}); arrowCache = {};
  const out = computeArrows(fi, L || 20, "equilibre").map(a => a.vm[0]); Object.assign(ARROW_PARAMS, save); arrowCache = {}; return out; };
window.__uafP = (fi, lon, lat) => { const g = fullField("equilibre", fi); return g ? g[cellOf(lon, lat)] : null; };
window.__uafArea = (fi, b, band) => { const g = fullField("equilibre", fi), a = (FR.grid.size / 1000) ** 2; let s = 0; for (let c = 0; c < GX * GY; c++) if ((band ? cellPlace[c] === -2 : cellPlace[c] >= 0) && inBox(c % GX + 0.5, Math.floor(c / GX) + 0.5, b)) s += (band ? 1 - g[c] : g[c]) * a; return s; };
window.__uafLine = (fi, b) => lines50("equilibre", fi).flat().filter(p => inBox(p[0], p[1], b)).length;
// contrôle automatique (headless) : base à moins de 2 km de la ligne 50 %, angle avec la normale locale < 30°
window.__uafArrowCheck = function(){
  const fails = [], km = FR.grid.size / 1000; let total = 0;
  for (let fi = 3; fi < FR.months.length; fi++) {
    if (!FR.months[fi]) continue;
    const runs = lines50("equilibre", fi), pts = runs.flat();
    for (const L of [100, 50, 20, 10]) for (const a of computeArrows(fi, L, "equilibre")) {
      total++;
      // la base doit être sur la ligne ; l'angle est mesuré à la normale la plus favorable parmi les tronçons qui
      // passent à moins de 2 km (deux bras de ligne peuvent se toucher au même sommet)
      let bd = 1e9, ang = 90;
      for (const run of runs) for (let t = 0; t < run.length; t++) {
        const d = Math.hypot(run[t][0] - a.gx, run[t][1] - a.gy);
        if (d < bd) bd = d;
        if (d * km < 2) { const nn = polyNormal(run, t); if (nn) ang = Math.min(ang, Math.acos(Math.min(1, Math.abs(nn[0] * a.dx + nn[1] * a.dy))) * 180 / Math.PI); }
      }
      if (bd * km >= 2 || ang >= 30) fails.push({mois: FR.months[fi].date, L, dist_km: +(bd * km).toFixed(2), angle: +ang.toFixed(1)});
    }
  }
  return {total, fails};
};
function drawArrows(k){
  const fi = frontIdx(); if (fi < 0) return;
  if (!window.FRONTP || !window.FRONTP.mcsd) { loadMcsd(() => { arrowCache = {}; draw(); }); }
  // vue d'ensemble : voir overviewArrows ; au zoom, tronçons de 20 puis 10 km
  const arr = k < 3 ? overviewArrows(fi) : computeArrows(fi, k < 6 ? 20 : 10);
  arrowList = [];
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const a of arr) {
    const sx = (gAff[0] + a.gx * gAff[2]) * k + tr.x, sy = (gAff[1] + a.gy * gAff[2]) * k + tr.y;
    if (sx < -20 || sx > W + 20 || sy < -20 || sy > H + 20) continue;
    // vue d'ensemble : un seul style discret ; au zoom, longueur et épaisseur proportionnelles à la vitesse
    const overview = k < 1.6, len = overview ? Math.min(26, 14 + a.v * 2.5) : Math.min(70, 10 + a.v * 7), w = overview ? 1.6 : Math.min(7, 1.2 + a.v * 0.5);
    const ex = sx + a.dx * len, ey = sy + a.dy * len, col = a.ru ? C.front : C.uactl;
    // liseré clair : la flèche reste lisible sur la densité rouge ou bleue
    const hx = -a.dy, hy = a.dx, hs = 3 + w * 1.6;
    const head = () => { ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - a.dx * hs * 1.6 + hx * hs, ey - a.dy * hs * 1.6 + hy * hs); ctx.lineTo(ex - a.dx * hs * 1.6 - hx * hs, ey - a.dy * hs * 1.6 - hy * hs); ctx.closePath(); };
    ctx.strokeStyle = C.bg; ctx.globalAlpha = 0.8; ctx.lineWidth = w + 2.4; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - a.dx * hs, ey - a.dy * hs); ctx.stroke(); ctx.lineWidth = 2.4; head(); ctx.stroke();
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.globalAlpha = overview ? 0.8 : 0.95; ctx.lineWidth = w;
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - a.dx * w * 1.6, ey - a.dy * w * 1.6); ctx.stroke();
    head(); ctx.fill();
    arrowList.push({x: (sx + ex) / 2, y: (sy + ey) / 2, a});
  }
  ctx.globalAlpha = 1;
}
// voisinages précalculés : maille ukrainienne la plus proche de chaque maille de la bande russe, et l'inverse,
// pour que les isolignes ne longent jamais la frontière (chaque côté recopie la valeur de l'autre)
const nearUA = new Int32Array(GX * GY).fill(-1), nearBand = new Int32Array(GX * GY).fill(-1);
function bfs(fromTest, intoTest, out, maxSteps){
  const q = [], dist = new Int16Array(GX * GY).fill(-1);
  for (let c = 0; c < GX * GY; c++) if (fromTest(c)) { out[c] = c; dist[c] = 0; q.push(c); }
  for (let h = 0; h < q.length; h++) { const c = q[h]; if (dist[c] >= maxSteps) continue;
    for (const x of [c - 1, c + 1, c - GX, c + GX]) if (x >= 0 && x < GX * GY && intoTest(x) && dist[x] < 0) { dist[x] = dist[c] + 1; out[x] = out[c]; q.push(x); } }
}
bfs(c => cellPlace[c] >= 0, c => cellPlace[c] === -2, nearUA, 60);
bfs(c => cellPlace[c] === -2, c => cellPlace[c] >= 0, nearBand, 60);
let pBand = new Float32Array(GX * GY), isoBand = null;
function buildFront(){
  buildRuMask();
  const fi = frontIdx(), key = (C.dark ? "d" : "l") + fieldName() + (showStates ? "s" : ""), F = viewFields(fieldName());
  if (fi === frontFor && key === frontTheme) return;
  frontFor = fi; frontTheme = key;
  const img = octx.createImageData(GX, GY), px4 = img.data, imgB = octx.createImageData(GX, GY), pxB = imgB.data;
  const [r, g, b] = C["front-rgb"].split(",").map(Number), [ur, ug, ub] = C["uactl-rgb"].split(",").map(Number);
  pgrid.fill(0); pBand.fill(0);
  if (fi >= 0 && F && F[fi]) {
    const q = F[fi], [gr, gg, gb] = C["grey-rgb"].split(",").map(Number), full = new Float32Array(GX * GY);
    for (let u = 0; u < UCELLS.length; u++) full[UCELLS[u]] = q[u] / 63;
    for (let u = 0; u < UCELLS.length; u++) {
      const c = UCELLS[u], p = full[c], o = c * 4;
      if (cellPlace[c] === -2) {
        // Russie : seul l'écart à la normale compte, la probabilité de contrôle UKRAINIEN, dans une autre couleur
        const pu = 1 - p;
        // près de la frontière, on ignore la frange due au lissage temporel
        if (pu > 0.05 && (pu > 0.25 || bandFade[c] < 0.93)) { pxB[o] = ur; pxB[o+1] = ug; pxB[o+2] = ub; pxB[o+3] = Math.round(255 * (0.12 + 0.6 * pu)); }
        continue;
      }
      if (showStates) {
        if (p > 0.95) { px4[o] = r; px4[o+1] = g; px4[o+2] = b; px4[o+3] = C.dark ? 150 : 135; }
        else if (p >= 0.05) { const ch = ((c % GX) + Math.floor(c / GX)) % 2; px4[o] = gr; px4[o+1] = gg; px4[o+2] = gb; px4[o+3] = ch ? 170 : 90; }
      } else if (p > 0.004) { px4[o] = r; px4[o+1] = g; px4[o+2] = b; px4[o+3] = Math.round(255 * (C.dark ? 0.06 + 0.54 * p : 0.05 + 0.5 * p)); }
    }
    // isolignes ukrainiennes : la bande russe recopie la valeur ukrainienne voisine (pas de ligne sur la frontière)
    for (let c = 0; c < GX * GY; c++) {
      const k = cellPlace[c];
      if (k >= 0) pgrid[c] = full[c];
      else if (k === -2) pgrid[c] = nearUA[c] >= 0 ? full[nearUA[c]] : 1;
      else pgrid[c] = ruMask && ruMask[c] ? 1 : 0;
      // ligne 50 % côté russe : contrôle ukrainien, l'Ukraine recopie la valeur russe voisine
      if (k === -2) pBand[c] = 1 - full[c];
      else if (k >= 0) pBand[c] = nearBand[c] >= 0 ? 1 - full[nearBand[c]] : 0;
    }
    isoPaths = d3.contours().size([GX, GY]).thresholds([0.05, 0.5, 0.95])(pgrid).map(c => ringsPath(c.coordinates));
    isoBand = ringsPath(d3.contours().size([GX, GY]).thresholds([0.5])(pBand)[0].coordinates);
    // dilatation de 4 mailles hors d'Ukraine : le bord est ensuite donné par la frontière vectorielle (pas de créneaux)
    for (let pass = 0; pass < 4; pass++) {
      const src = new Uint8ClampedArray(px4);
      for (let c = 0; c < GX * GY; c++) {
        if (cellPlace[c] >= 0 || src[c * 4 + 3]) continue;
        for (const x of [c - 1, c + 1, c - GX, c + GX]) if (x >= 0 && x < GX * GY && src[x * 4 + 3]) { const o = c * 4, q = x * 4; px4[o] = src[q]; px4[o+1] = src[q+1]; px4[o+2] = src[q+2]; px4[o+3] = src[q+3]; break; }
      }
    }
  } else { isoPaths = []; isoBand = null; }
  octx.putImageData(img, 0, 0);
  ocB.getContext("2d").putImageData(imgB, 0, 0);
}
// domaines : Ukraine seule et bande russe seule (découpage des isolignes)
let domainPath = null, bandPath = null;
{ const m = new Float32Array(GX * GY), mb = new Float32Array(GX * GY);
  for (let c = 0; c < GX * GY; c++) { const x = c % GX, y = (c - x) / GX, inner = x > 1 && y > 1 && x < GX - 2 && y < GY - 2;
    if (cellPlace[c] >= 0 && inner) m[c] = 1; if (cellPlace[c] === -2 && inner) mb[c] = 1; }
  domainPath = ringsPath(d3.contours().size([GX, GY]).thresholds([0.5])(m)[0].coordinates);
  bandPath = ringsPath(d3.contours().size([GX, GY]).thresholds([0.5])(mb)[0].coordinates); }
{ const m = new Float32Array(GX * GY); for (let c = 0; c < GX * GY; c++) { const k = cellPlace[c]; if (k >= 0 && pFlags[k]) m[c] = 1; }
  prePath = ringsPath(d3.contours().size([GX, GY]).thresholds([0.5])(m)[0].coordinates); }
const RAWCOL = {dsm: "#2f7fd8", isw: "#d89a2f", wiki: "#8a5bd8"};
const rawCache = {};
function rawPath(src, fi){
  const key = src + fi; if (rawCache[key]) return rawCache[key];
  const n = FR.viina.indexOf(src), codes = monthCodes[fi], m = new Float32Array(GX * GY);
  let any = false;
  for (let c = 0; c < GX * GY; c++) { const k = cellPlace[c]; if (k < 0) continue; const v = codes[k] >> (2*n) & 3; if (v === 2) { m[c] = 1; any = true; } else if (v === 3) { m[c] = 0.5; any = true; } }
  return rawCache[key] = any ? ringsPath(d3.contours().size([GX, GY]).thresholds([0.5])(m)[0].coordinates) : null;
}
// zones qui dépendent de la source retirée : |p(variante) − p(modèle complet, cartes seules)| > 0,1
let diffFor = -2, diffPat = null;
function buildDiff(fi){
  const key = fvar + ":" + fi; if (diffFor === key) return;
  diffFor = key; diffPat = null;
  const A = viewFields(fvar), B = viewFields("equilibre"); if (!A || !B || !A[fi] || !B[fi]) return;
  const S = 4, cv = document.createElement("canvas"); cv.width = GX * S; cv.height = GY * S;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX * S, GY * S), d = img.data, a = A[fi], b = B[fi];
  const hx = (C.ink || "#222222").replace("#", ""), r = parseInt(hx.slice(0, 2), 16), g = parseInt(hx.slice(2, 4), 16), bl = parseInt(hx.slice(4, 6), 16);
  for (let u = 0; u < UCELLS.length; u++) {
    if (Math.abs(a[u] - b[u]) <= 6) continue;  // 0,1 ≈ 6/63
    const c = UCELLS[u], x0 = (c % GX) * S, y0 = Math.floor(c / GX) * S;
    for (let dy = 0; dy < S; dy++) for (let dx = 0; dx < S; dx++) {
      if (((x0 + dx) - (y0 + dy) + 600) % 5 > 0) continue;  // diagonales dans l'autre sens que les combats
      const o = ((y0 + dy) * GX * S + x0 + dx) * 4; d[o] = r; d[o+1] = g; d[o+2] = bl; d[o+3] = 200;
    }
  }
  cx.putImageData(img, 0, 0); diffPat = cv;
}
// « combats intenses (anomalie) » : score de combat au moins deux fois sa moyenne nationale près de la ligne, sans
// direction ; hachures dessinées sur la grille (4 pixels par maille pour la finesse du trait)
let combatFor = -2, combatPat = null;
function buildCombat(fi){
  if (combatFor === fi) return;
  combatFor = fi; combatPat = null;
  const F = viewFields("combat"); if (!F || !F[fi]) return;
  const S = 4, cv = document.createElement("canvas"); cv.width = GX * S; cv.height = GY * S;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX * S, GY * S), d = img.data, q = F[fi];
  const [r, g, b] = C["front-rgb"].split(",").map(Number);
  for (let u = 0; u < UCELLS.length; u++) {
    if (q[u] < 20) continue;
    const c = UCELLS[u], x0 = (c % GX) * S, y0 = Math.floor(c / GX) * S, a = Math.min(1, 0.45 + (q[u] - 20) / 40);
    for (let dy = 0; dy < S; dy++) for (let dx = 0; dx < S; dx++) {
      if (((x0 + dx) + (y0 + dy)) % 6 > 1) continue;  // diagonales
      const o = ((y0 + dy) * GX * S + x0 + dx) * 4; d[o] = r; d[o+1] = g; d[o+2] = b; d[o+3] = Math.round(255 * a);
    }
  }
  cx.putImageData(img, 0, 0); combatPat = cv;
}
function drawFront(k){
  buildFront();
  ctx.save();
  ctx.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]);
  const u = 1 / (k * gAff[2]);
  if (showFront && frontFor >= 0) {
    ctx.imageSmoothingEnabled = true;
    ctx.restore(); ctx.save(); ctx.clip(paths.uaClip);                   // Ukraine : découpée par la frontière vectorielle
    ctx.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]); ctx.drawImage(oc, 0, 0);
    ctx.restore(); ctx.save(); ctx.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]); ctx.drawImage(ocB, 0, 0);  // Russie
  }
  ctx.strokeStyle = C.ink; ctx.globalAlpha = 0.55; ctx.lineWidth = 1 * u; ctx.setLineDash([2*u, 3*u]);
  ctx.stroke(prePath);
  if (showLines && isoPaths.length) {
    ctx.save(); ctx.clip(domainPath);
    ctx.strokeStyle = C.front; ctx.globalAlpha = 0.9; ctx.setLineDash([4*u, 3*u]); ctx.lineWidth = 0.9 * u;
    ctx.stroke(isoPaths[0]); ctx.stroke(isoPaths[2]);
    ctx.setLineDash([]); ctx.lineWidth = 1.8 * u; ctx.globalAlpha = 1; ctx.stroke(isoPaths[1]);
    ctx.restore();
    if (isoBand) { ctx.save(); ctx.clip(bandPath); ctx.strokeStyle = C.uactl; ctx.lineWidth = 1.8 * u; ctx.stroke(isoBand); ctx.restore(); }
  }
  const fi = frontIdx();
  if (fvar && fi >= 0) { buildDiff(fi); if (diffPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.8; ctx.drawImage(diffPat, 0, 0, GX, GY); ctx.restore(); } }
  if (showCombat && fi >= 0) { buildCombat(fi); if (combatPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.85; ctx.drawImage(combatPat, 0, 0, GX, GY); ctx.restore(); } }
  if (fi >= 0) for (const src of ["dsm", "isw", "wiki"]) {
    if (!rawOn[src]) continue;
    const pth = rawPath(src, fi); if (!pth) continue;
    ctx.strokeStyle = RAWCOL[src]; ctx.globalAlpha = 0.95; ctx.lineWidth = 1.4 * u; ctx.setLineDash(src === "isw" ? [5*u, 2*u] : src === "wiki" ? [1.5*u, 2*u] : []);
    ctx.stroke(pth);
  }
  ctx.restore();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  if (fi >= 0) for (const [src, col, up] of [["modru", C.front, true], ["gsua", C.cool, false]]) {
    if (!rawOn[src] || !FR.months[fi][src]) continue;
    const mo = FR.months[fi][src], fresh = new Set(mo.nouvelles);
    for (const kk of new Set([...mo.actives, ...mo.nouvelles])) {
      const pl = FR.claimPlaces[kk]; if (!pl) continue;
      const q = proj([pl[0], pl[1]]); const sz = (fresh.has(kk) ? 5 : 3.2) / k;
      ctx.fillStyle = col; ctx.globalAlpha = fresh.has(kk) ? 1 : 0.55;
      ctx.beginPath(); if (up) { ctx.moveTo(q[0], q[1] - sz); ctx.lineTo(q[0] + sz, q[1] + sz*0.8); ctx.lineTo(q[0] - sz, q[1] + sz*0.8); } else { ctx.moveTo(q[0], q[1] + sz); ctx.lineTo(q[0] + sz, q[1] - sz*0.8); ctx.lineTo(q[0] - sz, q[1] - sz*0.8); }
      ctx.closePath(); ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}
// ---------- couches UCDP et arcs de missiles ----------
const UC = O.ucdp.pts, NU = UC.length / 5;
function drawUcdp(k){
  const last = Math.min(cur, cutIdx); if (last < cur) return;
  ctx.fillStyle = C.ucdp;
  for (let j = 0; j < NU; j++) {
    if (UC[j*5+2] !== cur) continue;
    const r = cellRect(UC[j*5], UC[j*5+1]);
    ctx.globalAlpha = Math.min(0.85, 0.25 + 0.12 * Math.sqrt(UC[j*5+3]));
    ctx.fillRect(r[0], r[1], r[2], r[3]);
  }
  ctx.globalAlpha = 1;
}
const regionCentroid = {};
for (const f of G.regions) if (f.properties.id) regionCentroid[f.properties.id] = d3.geoCentroid(f);
function drawArcs(k){
  if (cur > cutIdx) return;
  const arcs = O.launch.arcs, pl = O.launch.places;
  ctx.strokeStyle = C.arc; ctx.lineCap = "round";
  for (const [mi, li, tid, n] of arcs) {
    if (mi !== cur || !regionCentroid[tid]) continue;
    const a = proj([pl[li][1], pl[li][2]]), b = proj(regionCentroid[tid]);
    if (!a || !b) continue;
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1];
    const cx = mx - dy * 0.18, cy = my + dx * 0.18;
    ctx.globalAlpha = 0.55; ctx.lineWidth = Math.min(9, 0.6 + Math.sqrt(n) * 0.35) / k;
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.quadraticCurveTo(cx, cy, b[0], b[1]); ctx.stroke();
    ctx.globalAlpha = 0.9; ctx.fillStyle = C.arc; ctx.beginPath(); ctx.arc(b[0], b[1], 2.2 / k, 0, 7); ctx.fill();
  }
  ctx.globalAlpha = 1;
}
const zoneM = {type:"MultiPoint", coordinates:[[23,44.4],[40.2,44.4],[40.2,52.4],[23,52.4]]};
const zone = {type:"MultiPoint", coordinates:[[22,44],[41.5,44],[41.5,53.6],[22,53.6],[31.75,53.6],[31.75,44]]};

function layout(){
  const w = canvas.parentElement.clientWidth;
  W = w; H = Math.round(w < 560 ? w * 1.05 : Math.max(340, Math.min(w * 0.68, 720)));
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.height = H + "px";
  proj = d3.geoAzimuthalEqualArea().rotate([-FR.grid.lon0, -FR.grid.lat0]).fitExtent([[10, W < 560 ? 60 : 10],[W-10,H-10]], W < 560 ? zoneM : zone);
  const gp = d3.geoPath(proj);
  const mk = fs => new Path2D(fs.map(f => gp(f) || "").join(""));
  paths.land = mk(G.land);
  paths.ua = mk(G.countries.filter(f => f.properties.iso === "UA"));
  // découpe de la densité : Ukraine dans ses frontières internationales (Crimée et Sébastopol comprises)
  // (oblasts Natural Earth au 1:10 M, cohérents avec le tracé des frontières, Crimée et Sébastopol comprises)
  paths.uaClip = mk(G.regions.filter(f => f.properties.c === "UKR" || f.properties.id === "UA-43" || f.properties.id === "UA-40")
    .concat(G.countries.filter(f => f.properties.iso === "UA")));
  paths.regions = mk(G.regions);
  paths.borders = mk(G.borders);
  paths.rivers = mk(G.rivers);
  paths.lakes = mk(G.lakes);
  px = new Float32Array(NP); py = new Float32Array(NP);
  for (let j = 0; j < NP; j++) { const q = proj([P[j*5+1]/100, P[j*5]/100]); px[j] = q[0]; py[j] = q[1]; }
  cellXY = new Map();
  wx = new Float32Array(NW); wy = new Float32Array(NW);
  for (let j = 0; j < NW; j++) { const q = proj([(WSP[j*5+1]+0.5)*D.cell, (WSP[j*5]+0.5)*D.cell]); wx[j] = q[0]; wy[j] = q[1]; }
  cities = G.cities.map(c => { const q = proj([c[0],c[1]]); return {x:q[0], y:q[1], n:c[2], r:c[3], cap:c[4]}; });
  baseGeo = null;
  // la grille du front est en projection azimutale équivalente (même centre) : simple homothétie
  const [tx, ty] = proj.translate(), sc = proj.scale(), g = FR.grid;
  gAff = [tx + sc * g.x0 / g.R, ty - sc * g.y1 / g.R, sc * g.size / g.R];
}
function cellRect(i, j){
  const k = i*10000 + j; let r = cellXY.get(k);
  if (!r) { const a = proj([j*D.cell, (i+1)*D.cell]), b = proj([(j+1)*D.cell, i*D.cell]); r = [a[0], a[1], b[0]-a[0], b[1]-a[1]]; cellXY.set(k, r); }
  return r;
}

function draw(){
  const k = tr.k;
  let bg = null;
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.fillStyle = C["map-bg"]; ctx.fillRect(0,0,W,H);
  ctx.setTransform(dpr*k,0,0,dpr*k,dpr*tr.x,dpr*tr.y);
  ctx.fillStyle = C.land; ctx.fill(paths.land);
  ctx.fillStyle = C.ua; ctx.fill(paths.ua);
  ctx.fillStyle = C.water; ctx.fill(paths.lakes);
  bg = showBase ? baseProject() : null;
  if (bg) { ctx.strokeStyle = C.road; ctx.globalAlpha = 0.55; ctx.lineWidth = 1.1 / k; ctx.stroke(bg.r1); if (k >= 2) { ctx.lineWidth = 0.6 / k; ctx.globalAlpha = 0.4; ctx.stroke(bg.r2); } ctx.globalAlpha = 1; }
  drawFront(k);
  ctx.lineWidth = 0.8/k; ctx.strokeStyle = C.water; ctx.stroke(paths.rivers);
  ctx.lineWidth = 0.5/k; ctx.strokeStyle = C.admin; ctx.stroke(paths.regions);
  ctx.lineWidth = 1.1/k; ctx.strokeStyle = C.border; ctx.stroke(paths.borders);

  if (showRef) drawRef(k);
  if (showFire) { if (mode === "pts") drawPoints(k); else drawDensity(k); }
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  if (showUcdp) drawUcdp(k);
  if (showLoss) drawLosses(k);
  if (showArcs) drawArcs(k);
  if (showArrows) { drawArrows(k); drawEventArrows(k); }
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;

  // villes : en coordonnées écran, seuil selon le zoom
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const lim = W < 500 ? 4 + Math.log2(k)*2 : 4.5 + Math.log2(k)*2;
  ctx.font = "11px 'JetBrains Mono', monospace"; ctx.textBaseline = "middle";
  const placed = [[0, 0, W < 560 ? 290 : 420, (W < 560 ? 62 : 70) + 15 * Math.max(0, layerNotes().length - 1)], [W-48, 0, W, 130]];
  for (const c of cities) {
    if (c.r > lim && !c.cap) continue;
    const x = c.x*k + tr.x, y = c.y*k + tr.y;
    if (x < -40 || x > W+40 || y < 0 || y > H) continue;
    const w = ctx.measureText(c.n).width, box = [x+5, y-7, x+7+w, y+7];
    if (placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
    placed.push(box);
    ctx.fillStyle = C.label; ctx.beginPath(); ctx.arc(x, y, c.cap ? 2.6 : 1.8, 0, 7); ctx.fill();
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.miterLimit = 2; ctx.strokeStyle = C["map-bg"]; ctx.globalAlpha = .75; ctx.strokeText(c.n, x+6, y); ctx.globalAlpha = 1;
    ctx.fillText(c.n, x+6, y);
  }
  if (!bg) return;
  // localités GeoNames selon le zoom : > 50 000 hab., puis > 10 000, puis > 1 000 (masquage glouton, population d'abord)
  const minPop = k >= 6 ? 1000 : k >= 3 ? 10000 : k >= 1.4 ? 50000 : Infinity;
  ctx.font = "10px 'JetBrains Mono', monospace";
  for (const c of bg.places) {
    if (c.pop < minPop) break;
    const x = c.x*k + tr.x, y = c.y*k + tr.y;
    if (x < -40 || x > W+40 || y < 0 || y > H) continue;
    const w = ctx.measureText(c.n).width, box = [x+4, y-6, x+6+w, y+6];
    if (placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
    placed.push(box);
    ctx.fillStyle = C.label; ctx.globalAlpha = .85; ctx.beginPath(); ctx.arc(x, y, 1.4, 0, 7); ctx.fill();
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.miterLimit = 2; ctx.strokeStyle = C["map-bg"]; ctx.globalAlpha = .7; ctx.strokeText(c.n, x+5, y); ctx.globalAlpha = .85;
    ctx.fillText(c.n, x+5, y); ctx.globalAlpha = 1;
  }
  // numéros des routes (M-, H-, P-) au zoom
  if (k >= 3) {
    ctx.font = "9px 'JetBrains Mono', monospace";
    for (const l of bg.labels) {
      if (l.cls === 2 && k < 5) continue;
      const x = l.x*k + tr.x, y = l.y*k + tr.y;
      if (x < 0 || x > W || y < 0 || y > H) continue;
      const w = ctx.measureText(l.n).width + 6, box = [x - w/2, y - 6, x + w/2, y + 6];
      if (placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
      placed.push(box);
      ctx.fillStyle = C["map-bg"]; ctx.globalAlpha = .85; ctx.fillRect(box[0], box[1], w, 12);
      ctx.strokeStyle = C.road; ctx.lineWidth = 1; ctx.globalAlpha = .9; ctx.strokeRect(box[0], box[1], w, 12);
      ctx.fillStyle = C.label; ctx.textAlign = "center"; ctx.fillText(l.n, x, y); ctx.textAlign = "start"; ctx.globalAlpha = 1;
    }
  }
}
function drawPoints(k){
  ctx.globalCompositeOperation = C.dark ? "lighter" : "source-over";
  const r = Math.max(0.55, 1.1 / Math.sqrt(k)) * (W < 500 ? 0.8 : 1);
  // traînée : mois précédents, plus pâles avec l'âge
  ctx.fillStyle = C["fire-old"];
  const byAge = new Map();
  for (let j = 0; j < NP; j++) { const m = P[j*5+2]; if (m >= cur) continue; const a = cur - m; (byAge.get(a) || byAge.set(a, []).get(a)).push(j); }
  for (const [a, js] of byAge) {
    ctx.globalAlpha = Math.max(C.dark ? 0.16 : 0.16, (C.dark ? 0.6 : 0.55) * Math.exp(-a/10));
    ctx.beginPath();
    for (const j of js) ctx.rect(px[j]-r/2, py[j]-r/2, r, r);
    ctx.fill();
  }
  // mois affiché
  const r2 = r * 2.1;
  ctx.fillStyle = C["fire-now"]; ctx.globalAlpha = C.dark ? 0.85 : 0.8;
  ctx.beginPath();
  for (let j = 0; j < NP; j++) if (P[j*5+2] === cur) { ctx.moveTo(px[j]+r2/2, py[j]); ctx.arc(px[j], py[j], r2/2, 0, 7); }
  ctx.fill();
}
const denColor = v => d3.interpolateRgbBasis(C.dark ? ["#3a1d08","#b0560f","#f2a33a","#ffd9a0","#fff6e8"] : ["#f3d6a8","#e39a3d","#c8361a","#7a1a0c","#3a0a04"])(v);
function drawDensity(k){
  const acc = new Map();
  for (let m = 0; m <= cur; m++) { const c = D.cells[m]; for (let j = 0; j < c.length; j += 4) { const key = c[j]*10000 + c[j+1]; acc.set(key, (acc.get(key) || 0) + c[j+2] - c[j+3]); } }
  const now = new Set(); { const c = D.cells[cur]; for (let j = 0; j < c.length; j += 4) now.add(c[j]*10000 + c[j+1]); }
  const s = d3.scaleLog().domain([3, 400]).range([0.15, 1]).clamp(true);
  ctx.globalAlpha = 0.92;
  for (const [key, v] of acc) {
    const i = Math.floor(key/10000), jj = key - i*10000, r = cellRect(i, jj);
    ctx.fillStyle = denColor(s(Math.max(3, v))); ctx.fillRect(r[0], r[1], r[2], r[3]);
  }
  ctx.globalAlpha = 1; ctx.lineWidth = 1/k; ctx.strokeStyle = C.ember;
  for (const key of now) { const i = Math.floor(key/10000), r = cellRect(i, key - i*10000); ctx.strokeRect(r[0], r[1], r[2], r[3]); }
}
// pictogrammes des pertes : un par grand type
function picto(c, g, x, y, s){
  c.beginPath();
  if (g === 0) { c.rect(x-s/2, y-s/2, s, s); c.fill(); }
  else if (g === 1) { c.rect(x-s/2, y-s/2, s, s); c.stroke(); }
  else if (g === 2) { c.moveTo(x, y-s*0.6); c.lineTo(x+s*0.55, y+s*0.45); c.lineTo(x-s*0.55, y+s*0.45); c.closePath(); c.fill(); }
  else if (g === 3) { c.moveTo(x, y-s*0.65); c.lineTo(x+s*0.65, y); c.lineTo(x, y+s*0.65); c.lineTo(x-s*0.65, y); c.closePath(); c.stroke(); }
  else if (g === 4) { c.arc(x, y, s*0.4, 0, 7); c.fill(); }
  else { c.moveTo(x-s/2, y-s/2); c.lineTo(x+s/2, y+s/2); c.moveTo(x+s/2, y-s/2); c.lineTo(x-s/2, y+s/2); c.stroke(); }
}
function pictoSvg(g, col){
  const c = col || "currentColor", sh = [
    `<rect x="2" y="2" width="8" height="8" fill="${c}"/>`, `<rect x="2.5" y="2.5" width="7" height="7" fill="none" stroke="${c}" stroke-width="1.4"/>`,
    `<path d="M6 1.5L11 10.5H1Z" fill="${c}"/>`, `<path d="M6 1L11 6L6 11L1 6Z" fill="none" stroke="${c}" stroke-width="1.4"/>`,
    `<circle cx="6" cy="6" r="3.6" fill="${c}"/>`, `<path d="M2 2L10 10M10 2L2 10" stroke="${c}" stroke-width="1.6"/>`][g];
  return `<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">${sh}</svg>`;
}
function drawLosses(k){
  const last = Math.min(cur, cutIdx);
  ctx.lineWidth = 1.2/k;
  for (let j = 0; j < NW; j++) {
    const m = WSP[j*5+2], g = WSP[j*5+3];
    if (m > last || !lossOn[g]) continue;
    const now = m === cur, a = cur - m;
    ctx.globalAlpha = now ? 0.95 : Math.max(0.12, 0.4 * Math.exp(-a/6));
    ctx.fillStyle = ctx.strokeStyle = now ? C["loss-now"] : C["loss-old"];
    const sz = Math.min(13, (now ? 4 : 3) + 1.4 * Math.sqrt(WSP[j*5+4])) / k;
    picto(ctx, g, wx[j], wy[j], sz);
  }
  ctx.globalAlpha = 1;
}
function drawRef(k){
  const m = +D.months[cur].slice(5) - 1, c = D.ref2021[m];
  ctx.strokeStyle = C.cool; ctx.lineWidth = 0.8/k; ctx.globalAlpha = C.dark ? 0.8 : 0.7;
  ctx.beginPath();
  for (let j = 0; j < c.length; j += 3) {
    const q = proj([(c[j+1]+0.5)*D.cell, (c[j]+0.5)*D.cell]), rr = Math.min(6, 0.9 + Math.sqrt(c[j+2]) * 0.55) / Math.sqrt(k);
    ctx.moveTo(q[0]+rr, q[1]); ctx.arc(q[0], q[1], rr, 0, 7);
  }
  ctx.stroke(); ctx.globalAlpha = 1;
}

// zoom
const zoom = d3.zoom().scaleExtent([1, 14]).on("zoom", e => { tr = e.transform; draw(); });
const sel = d3.select(canvas).call(zoom);
function setExtent(){ zoom.translateExtent([[-40,-40],[W+40,H+40]]).extent([[0,0],[W,H]]); }
$("zin").onclick = () => sel.transition().duration(300).call(zoom.scaleBy, 1.8);
$("zout").onclick = () => sel.transition().duration(300).call(zoom.scaleBy, 1/1.8);
$("zreset").onclick = () => sel.transition().duration(400).call(zoom.transform, d3.zoomIdentity);
function zoomTo(lon, lat, k){
  const q = proj([lon, lat]);
  sel.transition().duration(700).call(zoom.transform, d3.zoomIdentity.translate(W/2, H/2).scale(k).translate(-q[0], -q[1]));
}

// ---------- contrôles ----------
const slider = $("slider");
slider.max = AX.length - 1;
function setStep(sx){
  stepIdx = Math.max(0, Math.min(AX.length - 1, sx)); cur = AX[stepIdx].m; slider.value = stepIdx;
  draw(); updateReadout(); updateRank(); markChart(); marks.forEach(f => f()); varNote();
}
// aller à un mois : son pas mensuel, sinon le dernier pas récent de ce mois
function setMonth(i){ let k = AX.findIndex(a => a.m === i && !a.d); if (k < 0) k = AX.map(a => a.m).lastIndexOf(i); if (k >= 0) setStep(k); }
slider.oninput = () => { stop(); setStep(+slider.value); };
let timer = null;
function stop(){ if (timer) { clearInterval(timer); timer = null; } $("play").textContent = "▶ lecture"; }
$("play").onclick = () => {
  if (timer) return stop();
  let s = stepIdx; if (s >= AX.length - 1) s = -1;
  $("play").textContent = "❚❚ pause";
  const tick = () => { s++; if (s >= AX.length) return stop(); setStep(s); if (s === AX.length - 1) stop(); };
  tick(); timer = setInterval(tick, 650);
};
function setMode(m){ mode = m; $("modePts").setAttribute("aria-pressed", m === "pts"); $("modeDen").setAttribute("aria-pressed", m === "den"); legend(); draw(); }
$("modePts").onclick = () => setMode("pts"); $("modeDen").onclick = () => setMode("den");
$("ref").onclick = () => { showRef = !showRef; $("ref").setAttribute("aria-pressed", showRef); legend(); draw(); updateReadout(); };
$("lyrFront").onchange = e => { showFront = e.target.checked; legend(); draw(); };
$("lyrLines").onchange = e => { showLines = e.target.checked; legend(); draw(); };
$("lyrStates").onchange = e => { showStates = e.target.checked; frontFor = -2; legend(); draw(); };
// variante de sources : un seul choix à la fois (fichiers précalculés), effet chiffré et zones qui en dépendent
const VARLAB = {sans_dsm: "Sans DeepStateMap", sans_isw: "Sans ISW", sans_wiki: "Sans Wikipédia", sans_modru: "Sans les annonces du MoD RF",
  sans_gsua: "Sans les annonces de l'état-major UA", sans_medias: "Sans les annonces des médias", sans_enveloppes: "Sans les enveloppes",
  sans_presence: "Sans la présence documentée", sans_indices: "Sans les indices de combats"};
function varNote(){
  const el = $("varNote"), fi = frontIdx(), mo = fi >= 0 ? FR.months[fi] : null;
  if (!fvar || !mo || !mo.v || !mo.v[fvar]) { el.textContent = ""; return; }
  const a = mo.v[fvar].tout[0], b = mo.a.equilibre.tout[0], d = a - b;
  el.textContent = `${VARLAB[fvar] || fvar} : ${fmt(Math.round(a / 10) * 10)} km² (${d >= 0 ? "+" : "−"}${fmt(Math.abs(Math.round(d / 10) * 10))} par rapport au modèle complet, cartes seules).`;
}
$("varSel").onchange = () => {
  fvar = $("varSel").value || null;
  if (fvar && fmode !== "equilibre") document.querySelector('[data-mode="equilibre"]').click();
  diffFor = -2;
  ensureView(fieldName(), () => { frontFor = -2; diffFor = -2; draw(); updateArea(); varNote(); });
  frontFor = -2; draw(); updateArea(); legend(); varNote();
};
document.querySelectorAll("[data-raw]").forEach(cb => cb.onchange = () => { rawOn[cb.dataset.raw] = cb.checked; legend(); draw(); });
$("lyrFire").onchange = e => { showFire = e.target.checked; $("fireOpts").hidden = !showFire; legend(); draw(); };
$("lyrLoss").onchange = e => { showLoss = e.target.checked; legend(); draw(); };
$("lyrUcdp").onchange = e => { showUcdp = e.target.checked; legend(); draw(); };
$("lyrArcs").onchange = e => { showArcs = e.target.checked; legend(); draw(); };
if ($("lyrNowcast")) $("lyrNowcast").onchange = e => { nowcastOn = e.target.checked; arrowCache = {}; for (const key in evCache) delete evCache[key]; ensureView(fieldName(), () => { frontFor = -2; draw(); updateReadout(); drawChart && drawChart(); }); };
if ($("lyrCombat")) $("lyrCombat").onchange = e => { showCombat = e.target.checked; if ($("lgCombatRow")) $("lgCombatRow").hidden = !showCombat; ensureView("combat", () => { combatFor = -2; legend(); draw(); }); };
$("lyrArrows").onchange = e => { showArrows = e.target.checked; if (showArrows) loadMcsd(() => { arrowCache = {}; draw(); }); legend(); draw(); };
$("lyrBase").onchange = e => { showBase = e.target.checked; if (showBase) loadBasemap(); draw(); };
const tip = $("tip");
function hover(e){
  const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  if (showArrows && arrowList.length) {
    let best = null, bd = 14;
    for (const h of arrowList) { const d = Math.hypot(h.x - mx, h.y - my); if (d < bd) { bd = d; best = h; } }
    if (best) {
      const fi = frontIdx();
      tip.innerHTML = `<div class="r"><span>${best.a.ru ? "avancée russe" : "avancée ukrainienne"}</span><b>${String(Math.round(best.a.v * 10) / 10).replace(".", ",")} km/mois</b></div>` +
        `<div class="r"><span>segment</span><b>~${best.a.L} km de front</b></div><div class="r"><span>période</span><b>${frameLabel(Math.max(0, frameAt(fi, 90)))} → ${frameLabel(fi)}</b></div>` +
        `<div style="margin-top:4px;color:var(--faint)">déplacement moyen de la ligne 50 %, au-delà du bruit Monte Carlo</div>`;
      tip.style.display = "block";
      const tw = tip.offsetWidth, th = tip.offsetHeight; tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
      return;
    }
  }
  const bx = (mx - tr.x) / tr.k, by = (my - tr.y) / tr.k;
  const i = Math.floor((bx - gAff[0]) / gAff[2]), j = Math.floor((by - gAff[1]) / gAff[2]);
  const fi = frontIdx(), k = i >= 0 && j >= 0 && i < GX && j < GY ? cellPlace[j * GX + i] : -1;
  if (k === -2 && fi >= 0) {
    const F = viewFields(fieldName()), u = d3.bisectLeft(UCELLS, j * GX + i), p = F && F[fi] ? F[fi][u] / 63 : NaN;
    tip.innerHTML = `<div class="r"><span>p(contrôle russe)</span><b>${isNaN(p) ? "…" : Math.round(p*100) + "\u00a0%"}</b></div><div class="r"><span>état</span><b>${p > 0.95 ? "contrôle russe" : p < 0.05 ? "contrôle ukrainien" : "zone grise"}</b></div>` +
      `<div style="margin-top:4px">Russie, bande frontalière : a priori russe ; seules les annonces (état-major ukrainien, MoD RF) et les indices de combats peuvent faire déborder la ligne.</div>`;
    tip.style.display = "block";
    const tw = tip.offsetWidth, th = tip.offsetHeight; tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
    return;
  }
  if (k < 0 || fi < 0) { tip.style.display = "none"; return; }
  const b = monthCodes[fi][k], fl = pFlags[k], F = viewFields(fieldName());
  const u = d3.bisectLeft(UCELLS, j * GX + i), p = F && F[fi] ? F[fi][u] / 63 : NaN;
  const st = isNaN(p) ? "…" : p > 0.95 ? "contrôle russe" : p < 0.05 ? "contrôle ukrainien" : "zone grise";
  // votes des sources à cette maille (statuts par localité) et part de chaque source dans le pool
  const avail = s => !!(FR.months[fi].sigma && FR.months[fi].sigma[s] != null);
  const sv = n => (b >> (2*n) & 3) || (avail(FR.viina[n]) ? 1 : 0), av = FR.viina.map((s, n) => sv(n)).filter(Boolean);
  const rank = c => c === 1 ? 0 : c === 3 ? 1 : 2;
  const votes = [
    ["dsm", sv(0)], ["isw", sv(1)], ["wiki", sv(2)], ["boost", sv(3)],
    ["uamin", av.length ? av.reduce((a, c) => rank(c) < rank(a) ? c : a) : 0], ["rumax", av.length ? av.reduce((a, c) => rank(c) > rank(a) ? c : a) : 0],
    ["uaclaims", b & 256 ? 1 : 0], ["ruclaims", b & 512 ? 2 : 0], ["modru", b & 1024 ? 2 : 0], ["gsua", b & 2048 ? 1 : 0]];
  const drop = new Set(fvar ? FR.variants[fvar].drop : []), camps = FR.views[fmode];
  const act = votes.filter(([s, c]) => c && !drop.has(s) && camps.includes(FR.camps[s]));
  const cw = {}; for (const [s] of act) cw[FR.camps[s]] = (cw[FR.camps[s]] || 0) + FR.weights[s];
  const nc = Object.keys(cw).length;
  const NAMES = {dsm:"DeepStateMap", isw:"ISW", wiki:"Wikipédia", boost:"Wikipédia + VIINA", uamin:"enveloppe ukr.", rumax:"enveloppe russe", uaclaims:"annonce médias ukr.", ruclaims:"annonce médias russes", modru:"annonce MoD RF", gsua:"annonce état-major ukr."};
  tip.innerHTML = `<div class="r"><span>p(occupé)${fvar ? " · " + fvar.replace("_", " ") : ""}</span><b>${isNaN(p) ? "…" : Math.round(p*100) + "\u00a0%"}</b></div><div class="r"><span>état</span><b>${st}</b></div>` +
    `<div style="margin-top:4px;color:var(--faint)">source · vote · poids dans le calcul</div>` +
    act.map(([s, c]) => `<div class="r"><span>${NAMES[s]} <span style="color:var(--faint)">(${({ua: "ukr.", ru: "russe", tiers: "tiers"})[FR.camps[s]]})</span></span><b>${STN[c]} · ${Math.round(FR.weights[s] / cw[FR.camps[s]] / nc * 100)}\u00a0%</b></div>`).join("") +
    `<div style="margin-top:4px;color:var(--faint)">les tracés votent selon leur distance à la maille : poids nominaux</div>` +
    (fl & 1 ? `<div style="margin-top:4px">Crimée, occupée depuis 2014</div>` : fl & 2 ? `<div style="margin-top:4px">occupé avant 2022</div>` : "");
  tip.style.display = "block";
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
}
canvas.addEventListener("mousemove", hover);
canvas.addEventListener("click", hover);
canvas.addEventListener("mouseleave", () => tip.style.display = "none");
$("legend").addEventListener("click", e => {
  const b = e.target.closest("[data-g]"); if (!b) return;
  const g = +b.dataset.g; lossOn[g] = !lossOn[g]; legend(); draw();
});

function legend(){
  if (typeof layerNotes === "function" && $("mSub")) $("mSub").innerHTML = layerNotes().join("<br>");
  const front = showFront || showLines ? (showStates ? `<span><i style="background:rgba(${C["front-rgb"]},.6);border-radius:0"></i>contrôle russe (p > 0,95)</span><span><i style="background:rgba(${C["grey-rgb"]},.7);border-radius:0"></i>zone grise (0,05 – 0,95)</span><span>sans couleur : contrôle ukrainien (p < 0,05)</span>` : `<span><i style="background:rgba(${C["front-rgb"]},.75);border-radius:0"></i>probabilité d'occupation russe</span>`) +
    (showLines ? `<span><i style="background:${C.front};height:2px;border-radius:0;vertical-align:3px"></i>ligne médiane (p = 50 %)</span><span><i style="border-top:1px dashed ${C.front};height:0;border-radius:0;vertical-align:3px"></i>5 % et 95 %</span>` : "") +
    `<span><i style="border-top:1px dotted ${C.ink};height:0;border-radius:0;vertical-align:3px"></i>occupé avant 2022</span><span style="flex-basis:100%;height:0"></span>` : "";
  const items = mode === "pts"
    ? [[C["fire-now"], "feu en excès, mois affiché"], [C["fire-old"], "mois précédents (traînée)"]]
    : [[denColor(1), "excès cumulé élevé"], [denColor(.25), "faible"], ["transparent;border:1px solid " + C.ember + ";border-radius:0", "maille anormale ce mois"]];
  if (!showFire) items.length = 0;
  if (showRef) items.push(["transparent;border:1.5px solid " + C.cool, "tous les feux du même mois en 2021"]);
  let html = front + items.map(([c,t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("");
  if (showLoss) html += `<span style="flex-basis:100%;height:0"></span><span>pertes russes documentées (WarSpotting), cliquer pour filtrer :</span>` +
    O.groups.map((g,i) => `<span class="lchip" role="button" tabindex="0" data-g="${i}" aria-pressed="${lossOn[i]}">${pictoSvg(i, C["loss-now"])}${g}</span>`).join("");
  const raws = Object.entries(rawOn).filter(([, v]) => v).map(([s]) => s);
  if (raws.length) html += `<span style="flex-basis:100%;height:0"></span><span>tracés bruts, au pas affiché :</span>` + raws.map(s => s === "modru" ? `<span><i style="background:${C.front};border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%)"></i>annonces de prise du MoD RF (actives ; grandes : annoncées dans les 30 derniers jours)</span>` : s === "gsua" ? `<span><i style="background:${C.cool};border-radius:0;clip-path:polygon(0 0,100% 0,50% 100%)"></i>annonces de reprise de l'état-major ukrainien</span>` : `<span><i style="border-top:2px ${s === "isw" ? "dashed" : s === "wiki" ? "dotted" : "solid"} ${RAWCOL[s]};height:0;border-radius:0;vertical-align:3px"></i>tracé de ${s === "dsm" ? "DeepStateMap" : s === "isw" ? "l'ISW" : "Wikipédia"} : limite de sa zone russe (statut par localité, VIINA)</span>`).join("");
  if (showArrows) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.front};height:3px;border-radius:0;vertical-align:3px"></i>avancée russe</span><span><i style="background:${C.uactl};height:3px;border-radius:0;vertical-align:3px"></i>avancée ukrainienne</span>` +
    `<span>flèche pleine : déplacement de la ligne 50 % (km par 30 jours, sur 3 périodes de 30 jours)</span>` +
    `<span><i style="background:${C.front};height:5px;border-radius:0;vertical-align:2px;outline:1.5px solid ${C.ink}"></i>flèche cerclée : changements de contrôle annoncés et datés des 30 derniers jours (journal DeepStateMap, état-major UA, MoD RF, bascules des cartes), pondérés par la fiabilité mesurée de chaque source</span>`;
  if (showCombat) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:repeating-linear-gradient(45deg,rgba(${C["front-rgb"]},.9) 0 2px,transparent 2px 5px);border-radius:0"></i>combats intenses : signaux de combat (feux, UCDP, pertes) au moins deux fois leur moyenne nationale près de la ligne, sur 14 jours ; ne dit pas qui avance</span>`;
  if (fvar) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:repeating-linear-gradient(-45deg,${C.ink} 0 1.5px,transparent 1.5px 5px);border-radius:0"></i>zones qui dépendent de la source retirée (probabilité changée de plus de 0,1)</span>`;
  if (showUcdp) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.ucdp};border-radius:0"></i>événements de combat UCDP du mois (mailles de 0,1°, précision ≤ 25 km ; ni camp ni auteur)</span>`;
  if (showArcs) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.arc};height:2px;border-radius:0;vertical-align:3px"></i>missiles et drones lancés du lieu annoncé vers l'oblast visé, agrégés sur le mois (${O.launch.specificShare}\u00a0% des lancements ont une cible précisée ; source : armée de l'air ukrainienne)</span>`;
  $("legend").innerHTML = html;
}

const ref2021Tot = D.ref2021.map(c => { let s = 0; for (let j = 2; j < c.length; j += 3) s += c[j]; return s; });
function updateReadout(){
  const s = D.series[cur], m = +D.months[cur].slice(5) - 1;
  let cum = 0; for (let i = 0; i <= cur; i++) cum += D.series[i][2];
  $("mLabel").textContent = stepLabel(stepIdx);
  $("mSub").innerHTML = layerNotes().join("<br>");
  const nd = v => covered[cur] ? fmt(v) : "—";
  $("readout").innerHTML = [
    ["feux détectés ce mois-ci", nd(s[0])],
    ["normale du mois (2012-2021)", fmt(s[1])],
    ["même mois en 2021", fmt(ref2021Tot[m])],
    ["en excès ce mois-ci", nd(s[2]), "ex"]
  ].map(([t,v,c]) => `<div class="${c||""}">${t}<b>${v}</b></div>`).join("") +
  `<div style="grid-column:1/-1">cumul depuis le 24 février 2022 : <span style="color:var(--ember)">${fmt(cum)}</span> détections au-dessus de la normale</div>`;
  const c = CTX.filter(x => x.month === D.months[cur]);
  $("note").innerHTML = c.map(x => `<b>${x.title}.</b> ${x.short}`).join(" ");
  updateOsint();
}
// une ligne par couche affichée : ce qu'elle montre ce mois-ci, ou pourquoi elle est vide
function layerNotes(){
  const n = [], fi = frontIdx();
  if (showFront || showLines) {
    if (fi < 0) n.push("front : pas d'instantané pour ce mois");
    else if (!AX[stepIdx] || AX[stepIdx].f < 0) n.push(`front : ${frameLabel(fi)} (dernier disponible, J-7)`);
    else if (AX[stepIdx].d) n.push(`front : estimation au ${frameLabel(fi)} (pas de ${FR.stepDays || 3} jours, signaux de combat sur ${FR.windowDays || 14} jours)`);
  }
  if (showFire) {
    if (!covered[cur]) n.push("feux : aucune donnée S-NPP ce mois-ci");
    else if (!D.series[cur][2]) n.push("feux : aucun excès détecté ce mois-ci" + (cur === lastIdx && partial ? ` (mois en cours, jusqu'au ${+D.lastDay.slice(8)})` : ""));
    else if (cur === lastIdx && partial) n.push(`feux : mois en cours, jusqu'au ${+D.lastDay.slice(8)}`);
    else if (D.series[cur][3] < 1) n.push(`feux : ${Math.round(D.series[cur][3]*100)}\u00a0% des jours observés`);
  } else if (cur === 0) n.push("à partir du 24 février");
  if (showLoss && cur > cutIdx) n.push("pertes : pas encore publiées (mois complet + 7 jours)");
  else if (showLoss && !O.ws.perMonth[cur].some(Boolean)) n.push("pertes : aucune ce mois-ci");
  if (showUcdp) { let any = false; for (let j = 0; j < NU; j++) if (UC[j*5+2] === cur) { any = true; break; }
    if (!any) n.push(cur > cutIdx || D.months[cur] > (O.ucdp.last || "") ? "combats UCDP : pas encore publiés pour ce mois" : "combats UCDP : aucun événement localisé"); }
  if (showArcs && !O.launch.arcs.some(a => a[0] === cur)) n.push(cur > cutIdx ? "missiles : pas encore publiés (mois complet + 7 jours)" : D.months[cur] < "2022-10" ? "missiles : série à partir d'octobre 2022" : "missiles : aucune cible précisée ce mois-ci");
  return n;
}
const SECT = {"Kharkiv":"Kharkiv", "Luhans'k":"Louhansk", "Donets'k":"Donetsk", "Zaporizhzhya":"Zaporijjia", "Kherson":"Kherson", "Dnipropetrovs'k":"Dnipropetrovsk", "Sumy":"Soumy", "Mykolayiv":"Mykolaïv", "Chernihiv":"Tchernihiv", "Kiev":"Kyiv (oblast)"};
function greyTable(){
  const fi = frontIdx(), el = $("greyTab"); if (!el) return;
  const g = fi >= 0 && FR.months[fi].grey; if (!g) { el.innerHTML = ""; return; }
  const rows = Object.entries(g.sectors || {}).filter(([, v]) => v[0] > 0).sort((a, b) => b[1][0] - a[1][0]);
  el.innerHTML = `<tr><th>secteur (oblast), ${frameLabel(fi)}</th><th>zone grise (km²)</th><th>largeur moyenne (km)</th></tr>` +
    rows.map(([n, v]) => `<tr><td>${SECT[n] || n}</td><td>${fmt(v[0])}</td><td>${v[1] == null ? "—" : String(v[1]).replace(".", ",")}</td></tr>`).join("") +
    `<tr style="font-weight:600"><td>ensemble du front</td><td>${fmt(g.km2)}</td><td>${g.width_km == null ? "—" : String(g.width_km).replace(".", ",")}</td></tr>`;
}
function updateArea(){
  greyTable();
  const big = document.getElementById("bigKm");
  if (big) {
    const fi = frontIdx(), mo = fi >= 0 ? FR.months[fi] : null;
    if (!mo) big.innerHTML = "—";
    else { const a = mo.a[fieldName() in mo.a ? fieldName() : fmode] || mo.a.equilibre, r = v => fmt(Math.round(v/100)*100), v = fvar && mo.v && mo.v[fvar] ? mo.v[fvar].tout[0] : a.tout[0];
      const ru = a.russie ? a.russie[1] : 0;
      big.innerHTML = `<b>${r(v)} km²</b><span>${document.documentElement.dataset.kmLabel || "occupés"} · ${frameLabel(fi)}${fmode === "equilibre" && !fvar ? ` · 95 % : ${r(mcOf(mo).tout[0])} – ${r(mcOf(mo).tout[1])}` : ""}</span>` +
        (ru >= 50 ? `<span style="color:var(--cool)">${document.documentElement.dataset.ruLabel || "en Russie, sous contrôle ukrainien"} : ${fmt(Math.round(ru/10)*10)} km²</span>` : ""); }
  }
  const fi = frontIdx(), el = $("areaBox");
  if (fi < 0) { el.innerHTML = `<div style="grid-column:1/-1"><span class="na">front : pas d'instantané pour ce mois</span></div>`; return; }
  const mo = FR.months[fi], a = mo.a[amode()], sh = v => (v / UA_KM2 * 100).toFixed(1).replace(".", ",") + "\u00a0%";
  const r10 = v => fmt(Math.round(v/10)*10), mc = mcOf(mo), eq = fmode === "equilibre";
  const crimea = a.tout[0] - a.horsCrimee[0], pre = a.horsCrimee[0] - a.depuis2022[0];
  el.innerHTML = `<div>occupé au ${dateFr(mo.date)} · ${MODEN[amode()]}<b>${r10(a.tout[0])} km²</b>${sh(a.tout[0])} de l'Ukraine · ` +
      (eq ? `intervalle à 95\u00a0% : ${r10(mc.tout[0])} – ${r10(mc.tout[1])} km²` : `vue partielle (intervalle calculé pour la vue équilibrée)`) + ` · sous la ligne médiane : ${r10(a.tout[1])} km²</div>` +
    `<div>pris depuis le 24 février 2022<b>${r10(a.depuis2022[0])} km²</b>` + (eq ? `95\u00a0% : ${r10(mc.depuis2022[0])} – ${r10(mc.depuis2022[1])} km² · ` : "") + `Crimée ${r10(crimea)} et Donbass d'avant 2022 ${r10(pre)} km² comptés à part</div>` +
    `<div>zone grise (0,05 ≤ p ≤ 0,95)<b style="color:var(--accent)">${r10(mo.grey ? mo.grey.km2 : 0)} km²</b>largeur moyenne ${mo.grey && mo.grey.width_km != null ? String(mo.grey.width_km).replace(".", ",") : "—"} km le long de ${fmt(mo.grey ? mo.grey.front_km : 0)} km de front · désaccord des vues russe et ukrainienne : ${r10(mo.dis.tout[0])} km² (vue russe ${r10(mo.a.ru.tout[0])}, vue ukr. ${r10(mo.a.ua.tout[0])})</div>` +
    (fvar && mo.v && mo.v[fvar] ? `<div style="grid-column:1/-1">sans cette source (${fvar.replace("_", " ")}) : <i>${r10(mo.v[fvar].tout[0])} km²</i>, soit ${mo.v[fvar].tout[0] - mo.a.equilibre.tout[0] >= 0 ? "+" : "−"}${fmt(Math.abs(Math.round((mo.v[fvar].tout[0] - mo.a.equilibre.tout[0])/10)*10))} km² par rapport au modèle complet ; zone grise ${r10(mo.v[fvar].tout[2])} km²</div>` : "");
}
const UA_KM2 = (() => { let n = 0; for (let c = 0; c < GX * GY; c++) if (cellPlace[c] >= 0) n++; return n * (FR.grid.size/1000) ** 2; })();
function lineChart(el, rows, opt){
  const w = el.clientWidth, h = opt.h || (w < 500 ? 200 : 240), m = {t:10, r:8, b:26, l: w < 500 ? 46 : 58};
  const x = d3.scaleBand().domain(d3.range(NM)).range([m.l, w-m.r]).paddingInner(0.18), cx = i => x(i) + x.bandwidth()/2;
  // abscisse d'un instantané : mois au centre de sa bande ; pas récent à sa place dans le mois
  const fx = fi => { if (fi < NMF) return cx(fi); const d = FR.months[fi].date, mi = D.months.indexOf(d.slice(0, 7)), nd = new Date(+d.slice(0, 4), +d.slice(5, 7), 0).getDate();
    return cx(mi) + ((+d.slice(8, 10) - 0.5) / nd - 0.5) * x.step(); };
  const ext = d3.extent(rows.flatMap(r => r.slice(1).filter(v => v != null)));
  const y = d3.scaleLinear().domain(opt.zero ? [0, ext[1] * 1.05] : [ext[0] * 0.98, ext[1] * 1.01]).nice().range([h-m.b, m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
  svg.append("g").selectAll("line").data(y.ticks(4)).join("line").attr("x1",m.l).attr("x2",w-m.r).attr("y1",y).attr("y2",y).attr("stroke",C.rule);
  svg.append("g").selectAll("text").data(y.ticks(4)).join("text").attr("class","tick").attr("x",m.l-6).attr("y",y).attr("dy","0.32em").attr("text-anchor","end").text(v => L.format(",d")(v));
  if (opt.band) svg.append("path").attr("d", d3.area().defined(r => r[opt.band[0]] != null).x(r => fx(r[0])).y0(r => y(r[opt.band[0]])).y1(r => y(r[opt.band[1]])).curve(d3.curveMonotoneX)(rows)).attr("fill", opt.color).attr("opacity", .16);
  for (const ln of opt.lines) svg.append("path").attr("d", d3.line().defined(r => r[ln.i] != null).x(r => fx(r[0])).y(r => y(r[ln.i])).curve(d3.curveMonotoneX)(rows))
    .attr("fill","none").attr("stroke", ln.color).attr("stroke-width", ln.w || 1.6).attr("stroke-dasharray", ln.dash || null).attr("opacity", ln.op || 1);
  svg.append("g").selectAll("text").data(d3.range(NM).filter(i => D.months[i].endsWith("-01"))).join("text").attr("class","tick").attr("x", i => x(i)).attr("y", h-m.b+16).text(i => D.months[i].slice(0,4));
  const mk = svg.append("line").attr("y1", m.t).attr("y2", h-m.b).attr("stroke", C.accent).attr("stroke-width", 1.5);
  svg.append("g").selectAll("rect").data(d3.range(NM)).join("rect").attr("x", i => x(i) - x.step()*x.paddingInner()/2).attr("width", x.step())
    .attr("y", m.t).attr("height", h-m.b-m.t).attr("fill","transparent").style("cursor","pointer")
    .on("click", (e,i) => { stop(); setMonth(i); }).append("title").text(i => opt.title(i));
  marks.push(() => { const fi = frontIdx(), xx = fi >= 0 && AX[stepIdx] && AX[stepIdx].f >= 0 ? fx(fi) : cx(cur); mk.attr("x1", xx).attr("x2", xx).attr("opacity", AX[stepIdx] && AX[stepIdx].f >= 0 ? 1 : .3); });
  el.replaceChildren(svg.node());
}
const chartFrames = () => FR.months.map((v, i) => v && !(i < NMF && firstDay && v.date >= firstDay) ? v : null);
function drawAreaChart(){
  const eq = fmode === "equilibre";
  const rows = chartFrames().map((v, i) => v ? [i, v.a[amode()][perim][0], v.a[amode()][perim][1], eq ? mcOf(v)[perim][0] : null, eq ? mcOf(v)[perim][1] : null,
    v.a.ru[perim][0], v.a.ua[perim][0]] : null).filter(Boolean);
  const lean = document.body.classList.contains("lean");
  lineChart($("areaChart"), rows, {color: C.front, band: [3, 4], lines: lean ? [{i: 1, color: C.front, w: 2.2}] : [
      {i: 5, color: C.ink, w: 1, dash: "2 3", op: .55}, {i: 6, color: C.cool, w: 1, dash: "2 3", op: .8},
      {i: 2, color: C.ink, w: 1.2, dash: "5 3", op: .7}, {i: 1, color: C.front, w: 2.2}],
    title: i => FR.months[i] ? `${monthLabel(i)} — ${MODEN[amode()]} : ${fmt(FR.months[i].a[amode()][perim][0])} km² (95 % : ${fmt(mcOf(FR.months[i])[perim][0])} – ${fmt(mcOf(FR.months[i])[perim][1])}) ; vue russe ${fmt(FR.months[i].a.ru[perim][0])}, vue ukrainienne ${fmt(FR.months[i].a.ua[perim][0])}` : `${monthLabel(i)} — pas d'instantané`});
  const rrows = chartFrames().map((v, i) => v && v.a[amode()] && v.a[amode()].russie ? [i, v.a[amode()].russie[1]] : null).filter(Boolean);
  const ruEl = document.getElementById("ruChart");
  if (ruEl && rrows.some(r => r[1] >= 50)) {
    lineChart(ruEl, rrows, {zero: true, color: C.cool, lines: [{i: 1, color: C.cool, w: 1.8}], h: 90,
      title: i => FR.months[i] && FR.months[i].a[amode()].russie ? `${monthLabel(i)} — en Russie, sous contrôle ukrainien (p < 0,5) : ${fmt(FR.months[i].a[amode()].russie[1])} km²` : ""});
  } else if (ruEl) ruEl.replaceChildren();
  const grows = chartFrames().map((v, i) => v && v.grey ? [i, v.grey.km2] : null).filter(Boolean);
  lineChart($("greyChart"), grows, {zero: true, color: C.accent, lines: [{i: 1, color: C.accent, w: 2.2}],
    title: i => FR.months[i] && FR.months[i].grey ? `${monthLabel(i)} — zone grise ${fmt(FR.months[i].grey.km2)} km², largeur moyenne ${String(FR.months[i].grey.width_km).replace(".", ",")} km` : `${monthLabel(i)} — pas d'instantané`});
  const drows = chartFrames().map((v, i) => v ? [i, v.dis[perim][0], v.dis[perim][1]] : null).filter(Boolean);
  lineChart($("disChart"), drows, {zero: true, color: C.accent, lines: [{i: 2, color: C.ink, w: 1.2, dash: "5 3", op: .7}, {i: 1, color: C.accent, w: 2.2}],
    title: i => FR.months[i] ? `${monthLabel(i)} — |Δp| > 0,5 sur ${fmt(FR.months[i].dis[perim][0])} km² ; Σ|Δp| × surface ${fmt(FR.months[i].dis[perim][1])} km²` : `${monthLabel(i)} — pas d'instantané`});
}
document.querySelectorAll("[data-mode]").forEach(b => b.onclick = () => {
  fmode = b.dataset.mode;
  if (fmode !== "equilibre" && fvar) { $("varSel").value = ""; fvar = null; varNote(); }
  document.querySelectorAll("[data-mode]").forEach(x => x.setAttribute("aria-pressed", x === b));
  ensureView(fmode, () => { frontFor = -2; draw(); });
  draw(); updateArea(); drawSky(); marks.forEach(f => f()); legend();
});
document.querySelectorAll("[data-perim]").forEach(b => b.onclick = () => {
  perim = b.dataset.perim;
  document.querySelectorAll("[data-perim]").forEach(x => x.setAttribute("aria-pressed", x === b));
  drawSky(); marks.forEach(f => f());
});
function methodTables(){
  const P = FR.params;
  $("pAlpha").textContent = String(P.alpha).replace(".", ","); $("pH0").textContent = String(P.h0).replace(".", ",");
  $("pBeta").textContent = String(P.beta).replace(".", ","); $("pHalfLife").textContent = String(Math.round(P.official_half_life || 30)); $("pV").textContent = String(FR.v).replace(".", ",");
  if (FR.nowcast && $("nowTab")) {
    const N = FR.nowcast, pc = (a, b) => `${((b - a) / a * 100).toFixed(1).replace(".", ",").replace("-", "−")} %`;
    const row = (lab, x) => `<tr><td>${lab}</td><td>${x.band[0].toFixed(4)} → ${x.band[1].toFixed(4)} (${pc(x.band[0], x.band[1])})</td><td>${x.changing[0].toFixed(3)} → ${x.changing[1].toFixed(3)} (${pc(x.changing[0], x.changing[1])})</td><td>${fmt(x.nChanging)}</td></tr>`;
    $("pNow").textContent = `β = ${String(N.beta_final).replace(".", ",")} ; échelles (90e centile dans la bande) : ${Object.entries(N.scales).map(([k, v]) => k + " " + String(v).replace(".", ",")).join(", ")} ; validation ${N.period ? N.period.join(" → ") : ""}`;
    $("nowTab").innerHTML = `<tr><th>période ou cas</th><th>Brier, bande de 30 km (cartes seules → nowcast)</th><th>Brier, mailles qui changent de camp</th><th>mailles qui changent</th></tr>` +
      row("<b>ensemble (walk-forward)</b>", N.overall) + Object.entries(N.years).map(([y, x]) => row(y, x)).join("") + Object.entries(N.cases).map(([c, x]) => row(c, x)).join("");
  }
  if (FR.events && $("evTab")) {
    const LB = {dsmlog: "journal DeepStateMap (déclarations datées)", gsua: "état-major ukrainien", modru: "MoD RF", dsm: "bascules de DeepStateMap (VIINA)", wiki: "bascules de Wikipédia (VIINA)"};
    $("evTab").innerHTML = `<tr><th>source</th><th>fiabilité mesurée (confirmée par une autre carte sous 90 jours)</th></tr>` +
      FR.events.sources.map(k => `<tr><td>${LB[k] || k}</td><td>${Math.round(FR.events.reliability[k] * 100)} %</td></tr>`).join("");
  }
  const CS = FR.claimStats || {}, CR = FR.claimReliability || {};
  const cs = k => CS[k] ? `${k === "modru" ? "MoD RF" : "état-major ukrainien"} : ${fmt(CS[k].annonces || 0)} annonces géocodées, ${Math.round((CS[k].taux_rejet_localites || 0) * 100)}\u00a0% des localités reconnues rejetées (${fmt(CS[k].ambigu || 0)} ambiguës, ${fmt(CS[k]["loin du front"] || 0)} loin du front, ${fmt(CS[k]["incohérent"] || 0)} incohérentes), et ${fmt(CS[k]["introuvable"] || 0)} mots à majuscule sans correspondance (noms communs, localités absentes de GeoNames)` : "";
  $("pClaimStats").textContent = ["modru", "gsua"].map(cs).filter(Boolean).join(" ; ") || "annonces non encore disponibles";
  $("pClaimRel").textContent = Object.entries(CR).map(([k, v]) => `${({modru: "MoD RF", gsua: "état-major ukrainien", ruclaims: "médias russes", uaclaims: "médias ukrainiens"})[k]} : ${v[0]} sur ${v[1]}`).join(" ; ");
  $("pCalN").textContent = FR.calibMonths; $("pMC").textContent = FR.mcDraws; $("pSlope").textContent = String(FR.calibSlope).replace(".", ",");
  const period = s => { const ms = FR.months.filter(m => m && m.sigma && m.sigma[s] != null); return ms.length ? `${ms[0].date.slice(0,7)} → ${ms[ms.length-1].date.slice(0,7)}` : "—"; };
  const f1 = v => v == null ? "—" : String(Math.round(v * 10) / 10).replace(".", ",");
  const rows = [
    ["DeepStateMap", "ukrainien", FR.weights.dsm, "ODbL (via VIINA)", period("dsm"), f1(FR.sigma0.dsm), FR.mae.dsm],
    ["enveloppe la plus ukrainienne*", "ukrainien", FR.weights.uamin, "dérivée (ODbL)", period("uamin"), f1(FR.sigma0.uamin), null],
    ["annonces de reprise, médias ukrainiens*", "ukrainien", FR.weights.uaclaims, "ODbL (VIINA)", "2022 →", "noyau", null],
    ["ISW / CTP", "tiers (américain)", FR.weights.isw, "ODbL (via VIINA)", period("isw"), f1(FR.sigma0.isw), FR.mae.isw],
    ["Wikipédia", "tiers", FR.weights.wiki, "ODbL (via VIINA)", period("wiki"), f1(FR.sigma0.wiki), FR.mae.wiki],
    ["Wikipédia corrigé par VIINA", "tiers", FR.weights.boost, "ODbL (VIINA)", period("boost"), f1(FR.sigma0.boost), null],
    ["enveloppe la plus russe*", "russe", FR.weights.rumax, "dérivée (ODbL)", period("rumax"), f1(FR.sigma0.rumax), null],
    ["annonces de prise, médias russes*", "russe", FR.weights.ruclaims, "ODbL (VIINA)", "2022 → 2026, surtout avant 2025", "noyau", null],
  ];
  $("srcTab").innerHTML = `<tr><th>source</th><th>camp</th><th>poids dans le camp</th><th>licence</th><th>période</th><th>σ0 (km)</th><th>écart moyen calibré</th></tr>` +
    rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${String(r[2]).replace(".", ",")}</td><td>${r[3]}</td><td>${r[4]}</td><td>${r[5]}</td><td>${r[6] == null ? "—" : f1(r[6]) + " km"}</td></tr>`).join("");
  const S = FR.sensitivity;
  $("sensTab").innerHTML = `<tr><th>variante</th><th>équilibré</th><th>dont depuis 2022</th><th>vue ukr.</th><th>vue russe</th><th>tiers</th><th>zone grise</th></tr>` +
    S.map((r, i) => `<tr${i ? "" : ' style="font-weight:600"'}><td>${r[0]}</td><td>${fmt(r[1])}</td><td>${fmt(r[2])}</td><td>${fmt(r[3])}</td><td>${r[5] > 200000 ? "— *" : fmt(r[5])}</td><td>${fmt(r[7])}</td><td>${r[9] != null ? fmt(r[9]) : "—"}</td></tr>`).join("") +
    `<tr><td colspan="7" style="text-align:left;color:var(--faint);font-size:11px">* sans enveloppes, la vue russe ne repose plus que sur des annonces ponctuelles et n'est pas définie ailleurs.</td></tr>`;
  // diagramme de calibration
  const el = $("calibChart"), w = Math.min(el.clientWidth || 320, 360), h = w, m = {t:10, r:10, b:34, l:40};
  const x = d3.scaleLinear([0, 1], [m.l, w - m.r]), y = d3.scaleLinear([0, 1], [h - m.b, m.t]);
  const pts = FR.calibration.filter(b => b[1] != null);
  const rmax = d3.max(pts, b => b[2]) || 1;
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", w).attr("height", h);
  svg.append("g").selectAll("line").data([0, .25, .5, .75, 1]).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", y).attr("y2", y).attr("stroke", C.rule);
  svg.append("line").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1)).attr("stroke", C.faint).attr("stroke-dasharray", "4 3");
  svg.append("path").attr("d", d3.line().x(b => x(b[0])).y(b => y(b[1]))(pts)).attr("fill", "none").attr("stroke", C.front).attr("stroke-width", 1.8);
  svg.append("g").selectAll("circle").data(pts).join("circle").attr("cx", b => x(b[0])).attr("cy", b => y(b[1])).attr("r", b => 2 + 6 * Math.sqrt(b[2] / rmax)).attr("fill", C.front).attr("opacity", .8)
    .append("title").text(b => `p prédite ≈ ${String(b[0]).replace(".", ",")} : ${Math.round(b[1] * 100)} % occupé selon la source retirée (${fmt(b[2])} mailles)`);
  svg.append("g").selectAll("text").data([0, .5, 1]).join("text").attr("class", "tick").attr("x", x).attr("y", h - m.b + 14).attr("text-anchor", "middle").text(v => String(v).replace(".", ","));
  svg.append("g").selectAll("text").data([0, .5, 1]).join("text").attr("class", "tick").attr("x", m.l - 6).attr("y", y).attr("dy", ".32em").attr("text-anchor", "end").text(v => String(v).replace(".", ","));
  svg.append("text").attr("class", "tick").attr("x", (m.l + w - m.r) / 2).attr("y", h - 4).attr("text-anchor", "middle").text("p prédite par les autres sources");
  el.replaceChildren(svg.node());
  const b7 = FR.calibration.find(b => Math.abs(b[0] - 0.75) < 0.01);
  $("calibNote").innerHTML = `<p>Chaque point regroupe les mailles d'une tranche de probabilité prédite ; sa hauteur donne la part que la source retirée classe russe, sa taille le nombre de mailles. ` +
    (b7 && b7[1] != null ? `Parmi les mailles prédites entre 70 et 80 %, ${Math.round(b7[1] * 100)} % sont russes selon la source retirée.` : "") + ` Écarts moyens entre la limite de chaque source et la ligne des autres : ` +
    Object.entries(FR.mae).map(([s, v]) => `${SRCN[s]} ${f1(v)} km`).join(", ") + `.</p>`;
}
function updateOsint(){
  updateArea();
  const after = cur > cutIdx, wait = `<span class="na">pas encore publié (mois complet + 7 jours)</span>`;
  // WarSpotting : cumul depuis le 24 février 2022 en grand, mois et cumul par catégorie, Oryx en regard
  const lastWs = Math.min(cur, cutIdx), mlab = MC[+D.months[cur].slice(5)-1] + " " + D.months[cur].slice(0,4);
  const cumG = O.groups.map(() => 0);
  for (let i = 0; i <= lastWs; i++) O.ws.perMonth[i].forEach((v, g) => cumG[g] += v);
  const pmG = after ? null : O.ws.perMonth[cur];
  const orx = O.oryx.snapshot, oryxDay = dateFr(O.oryx.lastDay);
  let a = `<h4>Pertes russes documentées · WarSpotting · cumul depuis le 24 février 2022</h4>` +
    `<span class="big">${fmt(d3.sum(cumG))}</span><span>engins perdus (détruits, endommagés, abandonnés ou capturés) jusqu'à fin ${monthLabel(lastWs)}</span>` +
    `<div class="tablewrap"><table class="oryx" style="margin-top:8px;font-size:12px"><tr><th>catégorie</th><th>ce mois-ci (${mlab})</th><th>cumul WarSpotting</th><th>cumul Oryx, Russie (arrêté au ${oryxDay})</th></tr>` +
    O.groups.map((g, i) => `<tr><td>${pictoSvg(i, C["loss-now"])} ${g}</td><td>${pmG ? fmt(pmG[i]) : "—"}</td><td>${fmt(cumG[i])}</td><td>${fmt(orx[i][0])}</td></tr>`).join("") +
    `</table></div><p style="margin:8px 0 0">WarSpotting et Oryx ne comptent que les pertes prouvées par photo ou vidéo : ce sont des minimums. Les derniers mois sont sous-estimés, car la documentation arrive avec retard.` +
    (after ? ` Ce mois-ci : pas encore publié (mois complet + 7 jours).` : "") + ` La série Oryx n'est pas prolongée au-delà du ${oryxDay}.</p>`;
  // Oryx : cumul des deux camps à la fin du mois affiché
  let b = `<h4>Pertes documentées des deux camps · Oryx · cumul</h4>`;
  const ox = O.oryx.total[cur];
  const od = v => v ? fmt(v) : "—";
  if (ox && (ox[0] || ox[1])) b += `<div class="r"><span>Russie</span><b>${od(ox[0])}</b></div><div class="r"><span>Ukraine</span><b>${od(ox[1])}</b></div>` + (ox[0] && ox[1] ? `<div class="r"><span>rapport</span><b>${(ox[0]/ox[1]).toFixed(1).replace(".",",")} pour 1</b></div>` : "") + `<p style="margin:8px 0 0">cumul à la fin de ${monthLabel(cur)}, en entrées documentées (une par preuve photo ou vidéo, à la date de relevé)</p>`;
  else if (ox) b += `<span class="na">pas encore de relevé dans le jeu de données</span>`;
  else { const l = O.oryx.total.filter(Boolean).pop(); b += `<span class="na">série arrêtée au ${oryxDay}, non prolongée</span><div class="r" style="margin-top:6px"><span>Russie</span><b>${fmt(l[0])}</b></div><div class="r"><span>Ukraine</span><b>${fmt(l[1])}</b></div><p style="margin:8px 0 0">cumul au ${oryxDay}</p>`; }
  // Missiles et drones
  let c = `<h4>Missiles et drones lancés · armée de l'air · ce mois-ci (${mlab})</h4>`;
  const mm = O.missiles.perMonth[cur];
  if (after) c += wait;
  else if (!d3.sum(mm)) c += `<span class="na">série disponible à partir de fin septembre 2022</span>`;
  else c += `<div class="r"><span>drones lancés</span><b>${fmt(mm[2])}</b></div><div class="r"><span>dont annoncés abattus</span><b>${fmt(mm[3])}</b></div>` +
    `<div class="r" style="margin-top:6px"><span>missiles lancés</span><b>${fmt(mm[0])}</b></div><div class="r"><span>dont annoncés abattus</span><b>${fmt(mm[1])}</b></div>`;
  $("osint").innerHTML = `<div style="grid-column:1/-1">${a}</div><div>${b}</div><div style="grid-column:span 2">${c}</div>`;
}

// petits graphiques mensuels synchronisés (drones, missiles)
const marks = [];
function barChart(el, launched, downed, color){
  const w = el.clientWidth, h = 150, m = {t:8, r:4, b:22, l:36};
  const x = d3.scaleBand().domain(d3.range(NM)).range([m.l, w-m.r]).paddingInner(0.18);
  const y = d3.scaleLinear().domain([0, d3.max(launched) || 1]).nice().range([h-m.b, m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
  svg.append("g").selectAll("line").data(y.ticks(3)).join("line").attr("x1",m.l).attr("x2",w-m.r).attr("y1",y).attr("y2",y).attr("stroke",C.rule);
  svg.append("g").selectAll("text").data(y.ticks(3)).join("text").attr("class","tick").attr("x",m.l-5).attr("y",y).attr("dy","0.32em").attr("text-anchor","end").text(v => L.format("~s")(v).replace("k","\u202fk"));
  svg.append("g").selectAll("rect").data(launched).join("rect").attr("x",(v,i) => x(i)).attr("width",x.bandwidth()).attr("y",v => y(v)).attr("height",v => y(0)-y(v)).attr("fill",color).attr("opacity",.95);
  svg.append("g").selectAll("rect").data(downed).join("rect").attr("x",(v,i) => x(i)).attr("width",x.bandwidth()).attr("y",v => y(v)).attr("height",v => y(0)-y(v)).attr("fill",C.bg || "#000").attr("opacity",.45);
  svg.append("g").selectAll("text").data(d3.range(NM).filter(i => D.months[i].endsWith("-01"))).join("text").attr("class","tick").attr("x", i => x(i)).attr("y", h-m.b+14).text(i => D.months[i].slice(0,4));
  const mk = svg.append("rect").attr("y", m.t).attr("height", h-m.b-m.t).attr("fill","none").attr("stroke",C.accent).attr("stroke-width",1.5);
  svg.append("g").selectAll("rect").data(d3.range(NM)).join("rect").attr("x", i => x(i) - x.step()*x.paddingInner()/2).attr("width", x.step())
    .attr("y", m.t).attr("height", h-m.b-m.t).attr("fill","transparent").style("cursor","pointer")
    .on("click", (e,i) => { stop(); setMonth(i); })
    .append("title").text(i => `${monthLabel(i)} — ${fmt(launched[i])} lancés, ${fmt(downed[i])} annoncés abattus`);
  marks.push(() => mk.attr("x", x(cur) - 1.5).attr("width", x.bandwidth() + 3));
  el.replaceChildren(svg.node());
}
function drawSky(){
  marks.length = 0;
  const pm = O.missiles.perMonth.map((v,i) => i > cutIdx ? [0,0,0,0] : v);
  barChart($("drones"), pm.map(v => v[2]), pm.map(v => v[3]), C.accent);
  barChart($("missiles"), pm.map(v => v[0]), pm.map(v => v[1]), C.ember);
  drawAreaChart();
}
function oryxTable(){
  const sn = O.oryx.snapshot, tr2 = (n, r, u, b) => `<tr${b ? ' style="font-weight:600"' : ""}><td>${n}</td><td>${fmt(r)}</td><td>${fmt(u)}</td><td>${(r/Math.max(1,u)).toFixed(1).replace(".",",")}</td></tr>`;
  $("oryxTab").innerHTML = `<tr><th>type</th><th>Russie</th><th>Ukraine</th><th>rapport</th></tr>` +
    O.groups.map((g,i) => tr2(`${pictoSvg(i, C.muted)} ${g}`, sn[i][0], sn[i][1])).join("") +
    tr2("total", d3.sum(sn, v => v[0]), d3.sum(sn, v => v[1]), true);
  $("oryxDay").textContent = dateFr(O.oryx.lastDay);
}

// ---------- graphique mensuel ----------
let markChart = () => {};
function drawChart(){
  const el = $("chart"), w = el.clientWidth, h = w < 500 ? 220 : 260, m = {t:10, r:6, b:28, l: w < 500 ? 34 : 44};
  const x = d3.scaleBand().domain(d3.range(NM)).range([m.l, w-m.r]).paddingInner(0.18);
  const y = d3.scaleLinear().domain([0, d3.max(D.series, s => Math.max(s[0], s[1]))]).nice().range([h-m.b, m.t]);
  const yf = L.format("~s");
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
  const defs = svg.append("defs");
  defs.append("pattern").attr("id","hatch").attr("width",5).attr("height",5).attr("patternUnits","userSpaceOnUse").attr("patternTransform","rotate(45)")
    .append("line").attr("y2",5).attr("stroke",C.faint).attr("stroke-width",1).attr("opacity",.5);
  svg.append("g").selectAll("line").data(y.ticks(4)).join("line").attr("x1",m.l).attr("x2",w-m.r).attr("y1",y).attr("y2",y).attr("stroke",C.rule);
  svg.append("g").selectAll("text").data(y.ticks(4)).join("text").attr("class","tick").attr("x",m.l-6).attr("y",y).attr("dy","0.32em").attr("text-anchor","end").text(v => yf(v).replace("k"," k"));
  // mois non couverts
  const gaps = d3.range(NM).filter(i => !covered[i]);
  if (gaps.length) {
    const g0 = x(gaps[0]), g1 = x(gaps[gaps.length-1]) + x.bandwidth();
    svg.append("rect").attr("x",g0).attr("y",m.t).attr("width",g1-g0).attr("height",h-m.b-m.t).attr("fill","url(#hatch)");
    if (g1 - g0 > 70) svg.append("text").attr("class","tick").attr("x",(g0+g1)/2).attr("y",m.t+12).attr("text-anchor","middle").text("satellite muet");
  }
  svg.append("g").selectAll("rect").data(D.series).join("rect").attr("x",(s,i) => x(i)).attr("width",x.bandwidth())
    .attr("y", s => y(s[0])).attr("height", s => y(0)-y(s[0])).attr("fill", C.faint).attr("opacity",.45);
  svg.append("g").selectAll("rect").data(D.series).join("rect").attr("x",(s,i) => x(i)).attr("width",x.bandwidth())
    .attr("y", s => y(s[2])).attr("height", s => y(0)-y(s[2])).attr("fill", C.ember);
  const line = d3.line().x((s,i) => x(i) + x.bandwidth()/2).y(s => y(s[1])).curve(d3.curveStep);
  svg.append("path").attr("d", line(D.series)).attr("fill","none").attr("stroke",C.ink).attr("stroke-width",1.2).attr("stroke-dasharray","3 2").attr("opacity",.8);
  const years = d3.range(NM).filter(i => D.months[i].endsWith("-01") || i === 0);
  svg.append("g").selectAll("text").data(years).join("text").attr("class","tick").attr("x", i => x(i)).attr("y", h-m.b+16).text(i => D.months[i].slice(0,4));
  const mark = svg.append("rect").attr("y", m.t).attr("height", h-m.b-m.t).attr("fill","none").attr("stroke",C.accent).attr("stroke-width",1.5);
  svg.append("g").selectAll("rect").data(d3.range(NM)).join("rect").attr("x", i => x(i) - x.step()*x.paddingInner()/2).attr("width", x.step())
    .attr("y", m.t).attr("height", h-m.b-m.t).attr("fill","transparent").style("cursor", "pointer")
    .on("click", (e,i) => { stop(); setMonth(i); })
    .append("title").text(i => covered[i] ? `${monthLabel(i)} — ${fmt(D.series[i][0])} détections, normale ${fmt(D.series[i][1])}, excès ${fmt(D.series[i][2])}` : `${monthLabel(i)} — pas de données`);
  markChart = () => mark.attr("x", x(cur) - 1.5).attr("width", x.bandwidth() + 3);
  markChart();
  el.replaceChildren(svg.node());
}

// ---------- années ----------
function drawYearly(){
  const el = $("yearly"), w = el.clientWidth, h = 200, m = {t:16, r:4, b:24, l:4};
  const ys = Object.keys(D.yearly).filter(y => +y < 2026), v = ys.map(y => D.yearly[y]);
  const x = d3.scaleBand().domain(ys).range([m.l, w-m.r]).padding(0.2);
  const y = d3.scaleLinear().domain([0, d3.max(v)]).range([h-m.b, m.t]);
  const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
  svg.append("g").selectAll("rect").data(ys).join("rect").attr("x", x).attr("width", x.bandwidth())
    .attr("y", d => y(D.yearly[d])).attr("height", d => y(0) - y(D.yearly[d])).attr("fill", d => +d >= 2022 ? C.ember : C.faint).attr("opacity", d => +d >= 2022 ? .9 : .55);
  svg.append("g").selectAll("text").data(ys).join("text").attr("class","tick").attr("x", d => x(d) + x.bandwidth()/2).attr("y", h-m.b+14).attr("text-anchor","middle")
    .text(d => w < 420 ? "’" + d.slice(2) : d);
  svg.append("g").selectAll("text").data(ys.filter(d => ["2014","2021","2024"].includes(d))).join("text").attr("class","tick").attr("x", d => x(d) + x.bandwidth()/2)
    .attr("y", d => y(D.yearly[d]) - 4).attr("text-anchor","middle").text(d => L.format(".3~s")(D.yearly[d]).replace("k"," k"));
  el.replaceChildren(svg.node());
}

// ---------- classement ----------
function updateRank(){
  const rows = Object.entries(D.regions).filter(([k]) => k !== "autre").map(([k,v]) => [k, d3.sum(v.slice(0, cur+1))]).filter(r => r[1] > 0).sort((a,b) => b[1]-a[1]).slice(0, 14);
  const mx = d3.max(rows, r => r[1]) || 1;
  $("rank").innerHTML = rows.map(([k,v]) => `<div class="row ${regC[k] === "RUS" ? "ru" : ""}"><span>${D.regionNames[k][0]}</span><div><div class="bar" style="width:${(v/mx*100).toFixed(1)}%"></div></div><span class="v">${fmt(v)}</span></div>`).join("") || `<p class="sub">Aucun excès à cette date.</p>`;
}

// ---------- repères (dates vérifiées dans la presse) ----------
const CTX = [
  {month:"2022-03", lon:30.0, lat:50.6, k:3, title:"Offensive sur Kyiv", flag:"ok", short:"Les grappes à l'ouest de Kyiv et dans la zone de Tchernobyl suivent l'avancée russe ; au sud-est, le siège de Marioupol.",
   text:"Les forces russes avancent vers Kyiv par le nord et l'ouest. Le 2 avril 2022, l'Ukraine annonce avoir repris toute la région de Kyiv après le retrait russe. En mars, les excès se concentrent à l'ouest de la capitale et autour de Marioupol.",
   src:"AFP via ABS-CBN, 3 avril 2022", url:"https://www.abs-cbn.com/overseas/04/03/22/ukraine-regains-control-of-kyiv-region"},
  {month:"2022-06", lon:38.4, lat:48.9, k:4, title:"Sievierodonetsk", flag:"ok", short:"Louhansk concentre l'excès du mois.",
   text:"Après des semaines parmi les plus meurtrières du conflit, le maire annonce le 25 juin 2022 que Sievierodonetsk est « entièrement occupée ». Les excès de mai-juin dans l'oblast de Louhansk correspondent à cette bataille.",
   src:"Reuters via BusinessLIVE, 24 juin 2022", url:"https://www.businesslive.co.za/amp/bd/world/europe/2022-06-24-ukrainian-troops-set-to-withdraw-from-sievierodonetsk/"},
  {month:"2022-11", lon:33.0, lat:46.8, k:4, title:"Libération de Kherson", flag:"no", short:"Non détectée par la méthode : la région brûlait déjà beaucoup avant guerre.",
   text:"Les troupes ukrainiennes entrent dans Kherson le 11 novembre 2022 après le retrait russe sur la rive gauche du Dnipro. La contre-offensive d'août-novembre ne ressort pas : les détections restent dans la fourchette des brûlages agricoles des années 2012-2021.",
   src:"NPR, 11 novembre 2022", url:"https://www.boisestatepublicradio.org/2022-11-11/ukrainian-troops-enter-kherson-city-after-russia-retreats"},
  {month:"2023-05", lon:38.0, lat:48.6, k:5, title:"Bakhmout", flag:"no", short:"Bataille d'hiver presque invisible depuis l'espace.",
   text:"Le 20 mai 2023, le chef de Wagner revendique la prise totale de Bakhmout, contestée par Kyiv. La bataille, menée surtout en hiver, ne produit pas d'excès net : nuages, petits feux et terrain déjà détruit limitent la détection.",
   src:"The Moscow Times, 20 mai 2023", url:"https://themoscowtimes.com/2023/05/20/wagner-chief-claims-complete-capture-of-bakhmut-a81217"},
  {month:"2023-09", lon:33.0, lat:46.6, k:4, title:"Le Dnipro comme front", flag:"mix", short:"Rive gauche de la région de Kherson : excès récurrents chaque été depuis 2023.",
   text:"Depuis novembre 2022, le Dnipro sépare les deux armées dans la région de Kherson. À partir de l'été 2023, la rive gauche occupée (Oleshky, delta, côte) ressort chaque année : feux d'artillerie, mais aussi végétation abandonnée qui brûle sans être éteinte.",
   src:"NPR, 11 novembre 2022 (retrait russe)", url:"https://www.boisestatepublicradio.org/2022-11-11/ukrainian-troops-enter-kherson-city-after-russia-retreats"},
  {month:"2024-02", lon:37.75, lat:48.14, k:6, title:"Avdiïvka", flag:"mix", short:"Excès modeste mais localisé autour de la ville.",
   text:"L'armée ukrainienne se retire d'Avdiïvka le 17 février 2024 pour éviter l'encerclement, après quatre mois de combats. La méthode y relève un excès faible mais groupé entre octobre 2023 et février 2024.",
   src:"AP via PBS NewsHour, 17 février 2024", url:"https://www.pbs.org/newshour/world/ukraine-to-withdraw-from-avdiivka-where-outnumbered-defenders-held-out-for-4-months"},
  {month:"2024-05", lon:36.9, lat:50.3, k:5, title:"Offensive sur Vovtchansk", flag:"ok", short:"Nouveau front au nord de Kharkiv.",
   text:"Le 10 mai 2024, la Russie ouvre une offensive dans le nord de l'oblast de Kharkiv, en direction de Vovtchansk. Une bande d'excès apparaît le long de la frontière, absente les mois précédents.",
   src:"Kyiv Independent, mai 2024", url:"https://kyivindependent.com/defense-ministry-confirms-russian-units-entered-vovchansk-battles-ongoing/"},
  {month:"2024-08", lon:35.2, lat:51.3, k:5, title:"Incursion dans la région de Koursk", flag:"ok", short:"Le front passe en Russie : la région de Koursk s'allume.",
   text:"Le 6 août 2024, l'armée ukrainienne franchit la frontière russe près de Soudja. C'est le cas le plus net de la série : environ 2 400 détections en excès d'août à décembre 2024 dans un rayon autour de Soudja, quasi aucune auparavant.",
   src:"RFE/RL, août 2024", url:"https://www.rferl.org/a/russia-ukraine-kursk-incursion-invasion/33070123.html"},
  {month:"2024-09", lon:34.5, lat:50.5, k:1.6, title:"2024, année record d'incendies", flag:"mix", short:"Sécheresse et guerre : le pic de la série mêle feux de combat et incendies qui s'étendent.",
   text:"Selon le Centre commun de recherche européen, environ 965 000 hectares ont brûlé en Ukraine en 2024, plus de deux fois la surface brûlée dans toute l'UE, avec des feux alignés sur le front. Septembre 2024 est le mois record de la série ; la grande grappe à la frontière Briansk-Tchernihiv n'a pas de cause établie ici.",
   src:"Reuters via Arab News, mars 2025", url:"https://www.arabnews.com/node/2594918/amp"},
  {month:"2025-07", lon:37.2, lat:48.4, k:5, title:"Pokrovsk", flag:"ok", short:"L'oblast de Donetsk atteint son excès mensuel maximal.",
   text:"En juillet 2025, l'excès de l'oblast de Donetsk culmine autour de Pokrovsk et Kostiantynivka. Moscou revendique la prise de Pokrovsk le 1er décembre 2025, ce que l'Ukraine dément alors ; l'ISW juge ensuite la capture probablement achevée début 2026.",
   src:"The Moscow Times, 2 décembre 2025 · ISW via NV", url:"https://www.themoscowtimes.com/2025/12/02/russian-army-claims-capture-of-key-city-of-pokrovsk-in-eastern-ukraine-a91299/pdf", url2:"https://english.nv.ua/amp/pokrovsk-falls-to-russia-but-kremlin-fails-to-exploit-its-capture-50587178.html"}
];
const FLAG = {ok:["détecté","ok"], no:["non détecté","no"], mix:["partiel ou ambigu","mix"]};
$("ctxs").innerHTML = CTX.map((c,i) => {
  const [d,y] = [MC[+c.month.slice(5)-1], c.month.slice(0,4)];
  return `<article class="ctx"><time>${d} ${y}</time><h3>${c.title}</h3><span class="flag ${FLAG[c.flag][1]}">● ${FLAG[c.flag][0]}</span><p>${c.text}</p>
  <div class="foot"><a href="${c.url}" target="_blank" rel="noopener">${c.src}</a>${c.url2 ? ` <a href="${c.url2}" target="_blank" rel="noopener">ISW</a>` : ""}<button class="btn" type="button" data-i="${i}">voir ↗</button></div></article>`;
}).join("");
$("ctxs").addEventListener("click", e => {
  const b = e.target.closest("button[data-i]"); if (!b) return;
  const c = CTX[+b.dataset.i], i = D.months.indexOf(c.month);
  stop(); setMonth(i); zoomTo(c.lon, c.lat, c.k);
  $("map").scrollIntoView({behavior:"smooth", block:"center"});
});

// ---------- init ----------
function full(){ layout(); setExtent(); tr = d3.zoomIdentity; sel.call(zoom.transform, d3.zoomIdentity); drawChart(); drawYearly(); drawSky(); oryxTable(); methodTables(); setStep(stepIdx); }
$("cutoff").textContent = monthLabel(cutIdx);
$("frontLimit").textContent = lastFront >= 0 ? dateFr(FR.months[lastFront].date) : "—";
if (window.innerWidth < 560) $("panel").open = false;
// langue de l'encadré de présentation (mémorisée)
function setLang(code){
  document.querySelectorAll(".intro .i18n").forEach(d => d.hidden = d.dataset.lang !== code);
  document.querySelectorAll("[data-lang-btn]").forEach(b => b.setAttribute("aria-pressed", b.dataset.langBtn === code));
  try { localStorage.setItem("uaf-lang", code); } catch(e) {}
}
document.querySelectorAll("[data-lang-btn]").forEach(b => b.onclick = () => setLang(b.dataset.langBtn));
try { const l = localStorage.getItem("uaf-lang"); if (l && document.querySelector(`.intro .i18n[data-lang="${l}"]`)) setLang(l); } catch(e) {}
legend(); full();
window.addEventListener("load", () => { if (showBase) setTimeout(loadBasemap, 300); if (showArrows) loadMcsd(() => { arrowCache = {}; draw(); }); });
document.addEventListener("uaf-lang", () => updateArea());
let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (canvas.parentElement.clientWidth !== W) full(); }, 150); });
document.fonts && document.fonts.ready.then(() => draw());
})();
