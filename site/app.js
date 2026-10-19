(function(){
"use strict";
const D = window.DATA, G = window.GEO, O = window.OSINT, FR = window.FRONT;
const FRONT_V = {"asym": "e4a52ea131", "combat": "6f6fcc18b2", "contact": "76fed3e6c2", "destr": "6d2f393980", "equilibre": "98b088d2af", "mcsd": "13c8fc7628", "nowcast": "2c17b54478", "pmove": "39e8819790", "ru": "d83885e60c", "sans_dsm": "afbce7678f", "sans_enveloppes": "9bf7f49912", "sans_gsua": "1093e392a9", "sans_indices": "064df09c9c", "sans_isw": "4113c1839f", "sans_medias": "a61a66abdc", "sans_modru": "14f33ca763", "sans_presence": "b3f26eb64d", "sans_wiki": "1234914807", "tiers": "3511d38ee1", "ua": "c3b7b81f79", "zf": "7d771a092c", "zm": "4565874939", "zu": "83e7b8a779", "zw": "6a685ca6fd"};
const DUMMY = {}; const $ = id => document.getElementById(id) || DUMMY[id] || (DUMMY[id] = document.createElement("div"));
const L = d3.formatLocale({decimal:",",thousands:" ",grouping:[3],currency:[""," €"]});
const fmtFr = L.format(",d");
// traduction (i18n.js) ; « tr » désigne ici la transformation du zoom
const _t = (fr, vars) => window.tr ? window.tr(fr, vars) : fr;
// nombres : séparateurs de la langue de la page
const fmt = v => (document.documentElement.lang || "fr") === "fr" ? fmtFr(v) : new Intl.NumberFormat(({en: "en-GB", uk: "uk-UA", ru: "ru-RU"})[document.documentElement.lang] || "fr-FR", {maximumFractionDigits: 0}).format(v);
const pct = v => String(v).replace(".", ",") + " %";
// décimales : virgule sauf en anglais
const dec = v => (document.documentElement.lang === "en" ? String(v) : String(v).replace(".", ","));
// toponymes dans la langue de la page (toponyms.js, GeoNames) : localités du fond de carte par indice, villes de geo.js par nom
const TOPI = {en: 0, uk: 1, ru: 2};
const topoP = (i, fr) => { const l = TOPI[document.documentElement.lang]; return l === undefined || !window.TOPO || !TOPO.p[i] ? fr : TOPO.p[i][l]; };
const topoC = fr => { const l = TOPI[document.documentElement.lang]; return l === undefined || !window.TOPO || !TOPO.c[fr] ? fr : TOPO.c[fr][l]; };
const MOIS = ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"];
const MC = ["janv.","févr.","mars","avr.","mai","juin","juil.","août","sept.","oct.","nov.","déc."];
// mois dans la langue de la page (Intl), français par défaut
const LOC = () => ({en: "en-GB", uk: "uk-UA", ru: "ru-RU"})[document.documentElement.lang] || "fr-FR";
const monthName = (y, m) => document.documentElement.lang && document.documentElement.lang !== "fr"
  ? new Intl.DateTimeFormat(LOC(), {month: "long", year: "numeric"}).format(new Date(+y, +m - 1, 15)).replace(/\s*г\.$|\s*р\.$/, "")
  : MOIS[+m - 1] + " " + y;
const dayName = (y, m, d) => document.documentElement.lang && document.documentElement.lang !== "fr"
  ? new Intl.DateTimeFormat(LOC(), {day: "numeric", month: "long", year: "numeric"}).format(new Date(+y, +m - 1, +d)) : `${+d} ${MOIS[+m - 1]} ${y}`;
const monthLabel = i => { const [y,m] = D.months[i].split("-"); return monthName(y, m); };
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
let tr = d3.zoomIdentity, cur = lastIdx, mode = "pts", showFire = false, showLoss = false, showFront = true, showLines = true, showUcdp = false, showArcs = false, perim = "tout";
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
const MODEN_FR = {equilibre:"tous (équilibré), cartes seules", nowcast:"tous (équilibré), avec signaux de combat", ua:"vue ukrainienne seule", ru:"vue russe seule", tiers:"sources tierces seules"};
const MODEN = new Proxy(MODEN_FR, {get: (o, k) => o[k] === undefined ? undefined : _t(o[k])});
const STN = new Proxy(["—", "ukrainien", "russe", "contesté"], {get: (o, k) => typeof o[k] === "string" && o[k] !== "—" ? _t(o[k]) : o[k]});
// les navigateurs (mobiles surtout) restaurent l'état des cases au rechargement ou au retour arrière : la page
// repart toujours de l'état par défaut du HTML, sinon des cases cochées n'afficheraient rien
document.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = cb.defaultChecked; cb.setAttribute("autocomplete", "off"); });
document.querySelectorAll("select").forEach(sl => { sl.value = [...sl.options].find(o => o.defaultSelected)?.value ?? sl.options[0].value; sl.setAttribute("autocomplete", "off"); });
window.addEventListener("pageshow", e => { if (e.persisted) location.reload(); });
let fmode = "equilibre", fvar = null, showStates = false;
const rawOn = {dsm: false, isw: false, wiki: false};
let showClaims = false, claimHits = [];
// nowcast (signaux de combat) : par défaut sur la vue équilibrée sans variante de sources ; « cartes seules » sinon
let nowcastOn = !!(document.getElementById("lyrNowcast") && document.getElementById("lyrNowcast").checked), showCombat = false;
// zone grise (contact) mesurée par nos signaux : cochée ; incertitude des cartes (lignes 5 et 95 %) : décochée
let showContact = !!(document.getElementById("lyrContact") && document.getElementById("lyrContact").checked), showUnc = !!(document.getElementById("lyrUnc") && document.getElementById("lyrUnc").checked);
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
  const places = B.places.map((p, i) => [p, i]).filter(([p]) => !near(p[0], p[1])).map(([p, i]) => { const q = proj([p[0], p[1]]); return {x: q[0], y: q[1], n: p[2], i, pop: p[3]}; });
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
const frameLabel = fi => { if (fi < NMF) return monthLabel(fi); const [y, mo, d] = FR.months[fi].date.split("-"); return dayName(y, mo, d); };
const stepLabel = s => { const a = AX[s]; if (!a || !a.d) return monthLabel(a ? a.m : cur); const [y, mo, d] = a.d.split("-"); return dayName(y, mo, d); };
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
      // garde-fou de sens : avancée russe vers −∇p, ukrainienne vers +∇p, à la base de la flèche ; la direction est
      // sgn·n et le sens attendu sgn·∇p, leur produit scalaire vaut n·∇p (positif par construction, vérifié ici)
      if (nx * gx + ny * gy <= 0) continue;
      // garde-fou « combats derrière la ligne » : quand l'activité se concentre du côté du camp que la flèche fait
      // avancer (asymétrie ≥ 0,2 à moins de 12 km de la base) et que le dernier mois ne va pas dans son sens, la flèche
      // devient « incertaine » (creuse). Bilan 2023-2026 : ces flèches russes ne sont suivies d'un gain russe que dans
      // 24 % des cas, contre 45 % pour les autres (cas de Lyman en juin 2026 : moyenne d'avril-mai, juin immobile)
      let uncertain = false;
      const AS = viewFields("asym") ? fullField("asym", fi) : null;
      if (AS && !(Math.sign(vm[0]) === Math.sign(v) && Math.abs(vm[0]) >= 0.05)) {
        const R = 12 / km, adv = v < 0;   // adv : avancée russe
        for (let jj = Math.max(0, Math.floor(ay - R)); jj <= Math.min(GY - 1, Math.ceil(ay + R)) && !uncertain; jj++)
          for (let ii = Math.max(0, Math.floor(ax - R)); ii <= Math.min(GX - 1, Math.ceil(ax + R)); ii++) {
            const c = jj * GX + ii;
            if (cellPlace[c] === -1 || AS[c] < 0.2 || Math.hypot(ii + 0.5 - ax, jj + 0.5 - ay) > R) continue;
            if ((g1[c] > 0.5) === adv) { uncertain = true; break; }
          }
      }
      // combats derrière la ligne RUSSE alors que la flèche dit « avancée russe » (et dernier mois sans gain russe) :
      // la flèche devient une flèche BLEUE en pointillés, « poussée ukrainienne possible », vers le côté russe (décision du
      // 18/10/2026). Bilan 2023-2026 dans la légende. Le cas inverse (combats derrière la ligne ukrainienne) n'est pas
      // retourné : sur le passé, il n'annonce pas de gain russe (14 % contre 24 % en moyenne) ; la flèche reste incertaine.
      if (uncertain && v < 0) {
        out.push({gx: ax, gy: ay, dx: -nx * sgn, dy: -ny * sgn, v: Math.abs(v), ru: false, L: Lkm, segKm: Math.round(a1 - a0), vm: vm.map(x => +x.toFixed(2)),
                  uncertain: true, possible: true, why: "combats derrière la ligne russe"});
        continue;
      }
      const gd = guardDir(G4[1], g1, ax, ay, nx * sgn, ny * sgn, v < 0);
      out.push({gx: ax, gy: ay, dx: gd.ux, dy: gd.uy, v: Math.abs(v), ru: v < 0, L: Lkm, segKm: Math.round(a1 - a0), vm: vm.map(x => +x.toFixed(2)),
                uncertain: uncertain || gd.st === "incertaine", reoriented: gd.st === "réorientée", guarded: gd.st !== "ok", why: gd.why});
    }
  }
  return arrowCache[key] = out;
}
// vue d'ensemble : flèches des tronçons de 20, 50 et 100 km (les longs tronçons captent les poussées larges et lentes),
// éclaircies : au plus une tous les ~30 km, la plus rapide d'abord ; tout tronçon en mouvement a ainsi une flèche
// affichée à moins de 30 km (contrôlé par scripts/check_arrows.py)
// « poussée ukrainienne possible » sans flèche de gradient : combats concentrés derrière la ligne russe (asymétrie ≥ 0,4
// du côté russe) deux mois de suite au même endroit ; la flèche part du point de la ligne 50 % le plus proche vers ce
// foyer. Bilan 2023-2026 (secteurs de 24 km) : reprise ukrainienne dans les deux mois dans 23 % des cas, contre 12 % en
// moyenne ; gain russe 6 %.
const possCache = {};
function possibleArrows(fi, view){
  const key = (view || "") + ":" + fi; if (possCache[key]) return possCache[key];
  const A1 = viewFields("asym") ? fullField("asym", fi) : null, fp = frameAt(fi, 30), A0 = A1 && fp >= 0 ? fullField("asym", fp) : null, g = fullField(view || fieldName(), fi);
  if (!A1 || !A0 || !g) return [];
  const km = FR.grid.size / 1000, hot = [];
  for (let c = 0; c < GX * GY; c++) if (cellPlace[c] !== -1 && A1[c] >= 0.4 && A0[c] >= 0.4 && g[c] > 0.5) hot.push(c);
  const cl = [];
  for (const c of hot) { const x = c % GX + 0.5, y = Math.floor(c / GX) + 0.5;
    const k = cl.find(q => Math.hypot(q.x / q.n - x, q.y / q.n - y) * km < 20); if (k) { k.x += x; k.y += y; k.n++; } else cl.push({x, y, n: 1}); }
  const runs = lines50(view || fieldName(), fi), out = [];
  for (const q of cl) { if (q.n < 6) continue; const cx = q.x / q.n, cy = q.y / q.n;
    let bx = null, by = null, bd = 1e9; for (const run of runs) for (const [x, y] of run) { const d = Math.hypot(x - cx, y - cy); if (d < bd) { bd = d; bx = x; by = y; } }
    if (bx === null || bd * km > 25 || bd < 1e-6) continue;
    out.push({gx: bx, gy: by, dx: (cx - bx) / bd, dy: (cy - by) / bd, v: 1, ru: false, L: 20, segKm: 20, vm: [], uncertain: true, possible: true, why: "combats derrière la ligne russe (2 mois)"}); }
  return possCache[key] = out;
}
function overviewArrows(fi, view){
  const kept = [], minD = 30 / (FR.grid.size / 1000), all = [20, 50, 100].flatMap(L => computeArrows(fi, L, view));
  for (const a of all.sort((x, y) => y.v - x.v)) if (!kept.some(b => Math.hypot(b.gx - a.gx, b.gy - a.gy) < minD)) kept.push(a);
  // les flèches incertaines ou « poussée possible » ne sont pas éliminées par l'éclaircissement : on les ajoute si aucune
  // flèche du même type n'est déjà à moins de 30 km
  for (const a of all.filter(a => a.uncertain)) if (!kept.some(b => b.uncertain && Math.hypot(b.gx - a.gx, b.gy - a.gy) < minD)) kept.push(a);
  for (const a of possibleArrows(fi, view)) if (!kept.some(b => b.possible && Math.hypot(b.gx - a.gx, b.gy - a.gy) < minD)) kept.push(a);
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
  // « en retard sur les cartes » se juge sur le champ des CARTES SEULES un mois plus tôt (et non sur le nowcast, qui
  // anticipe déjà les poussées en cours : il ferait passer pour des confirmations des annonces que seules nos données
  // avaient vues venir, comme Avdiïvka en février 2024)
  const fp0 = frameAt(fi, 30), POLD = fp0 >= 0 ? fullField("equilibre", fp0) : null;
  for (const camp of [1, 0]) {
    const pl = new Map();
    for (let j = 0; j < mo.ev.length; j += 4) {
      if (mo.ev[j + 2] !== camp) continue;
      const key = mo.ev[j].toFixed(2) + "," + mo.ev[j + 1].toFixed(2), sn = src[mo.ev[j + 3]], w = rel[sn] || 0.3;
      if (!pl.has(key)) { const q = proj([mo.ev[j], mo.ev[j + 1]]), gx = (q[0] - gAff[0]) / gAff[2], gy = (q[1] - gAff[1]) / gAff[2];
        // localité déjà nettement au camp annonceur un mois plus tôt (bien derrière l'ancienne ligne) : mention ancienne
        const pz = POLD ? POLD[Math.round(gy - 0.5) * GX + Math.round(gx - 0.5)] : 0.5;
        if (camp === 1 ? pz > 0.9 : pz < 0.1) continue;
        pl.set(key, {gx, gy, w, srcs: new Set([sn])}); }
      else { const p = pl.get(key); p.w = Math.max(p.w, w); p.srcs.add(sn); }
    }
    const pts = [...pl.values()].sort((a, b) => b.w - a.w), cl = [];
    for (const p of pts) {
      const c = cl.find(c => Math.hypot(c.sx - p.gx, c.sy - p.gy) * km <= 15);
      if (c) { c.items.push(p); } else cl.push({sx: p.gx, sy: p.gy, items: [p]});
    }
    // base : ligne 50 % des cartes seules un mois plus tôt (la flèche compare les annonces aux cartes)
    const fp = frameAt(fi, 30), prevLines = fp >= 0 ? lines50("equilibre", fp) : [], curLines = lines50(fieldName(), fi);
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
      // en retard sur les cartes : un mois plus tôt, le champ plaçait déjà le barycentre du côté du camp qui annonce.
      // Ce n'est pas un mouvement nouveau (95 % des cas examinés : annonce postérieure aux cartes) : simple repère
      // « confirmé par annonce », sans direction.
      const pvo = POLD ? POLD[Math.round(cy - 0.5) * GX + Math.round(cx - 0.5)] : 0.5;
      if (camp === 1 ? pvo > 0.5 : pvo < 0.5) { out.push({ax: cx, ay: cy, cx, cy, ru: camp === 1, n, w: sw, dkm: bd * km, late: true, items: c.items}); continue; }
      // sens : une avancée russe va vers le côté ukrainien (−∇p, p = contrôle russe partout, bande russe comprise),
      // une avancée ukrainienne vers le côté russe (+∇p) ; à défaut (cas résiduel), réorientation selon le gradient local
      // Direction, par ordre de priorité : 1) déplacement de la ligne 50 % autour de l'agrégat (ancienne ligne disparue →
      // nouvelle ligne apparue) ; 2) mailles basculées ; 3) localités annoncées qui ont effectivement basculé (adverses
      // un mois plus tôt sur les cartes, au camp annonceur maintenant) ; 4) gradient local. Le point de l'ancienne ligne
      // le plus proche d'une localité ou d'un barycentre peut être sur un flanc, où le gradient est latéral : la flèche
      // suivait alors la ligne au lieu de la traverser (Lyman, septembre 2026).
      let fl = null;
      if (pc && POLD) { let x = 0, y = 0, nn = 0;
        for (const q of c.items) { const k = Math.round(q.gy - 0.5) * GX + Math.round(q.gx - 0.5);
          q.flipped = camp === 1 ? (POLD[k] < 0.5 && pc[k] > 0.5) : (POLD[k] > 0.5 && pc[k] < 0.5);
          if (q.flipped) { x += q.gx; y += q.gy; nn++; } }
        if (nn) fl = [x / nn, y / nn]; }
      const fc = pc && POLD ? flippedCentroid(pc, POLD, cx, cy, camp) : null;
      const sh = !pc || !POLD ? null : lineShift(prevLines, curLines, pc, POLD, cx, cy, camp);
      let arr = sh ? {ax: sh.ax, ay: sh.ay, ux: sh.ux, uy: sh.uy, len: sh.len, mode: "déplacement"} : null;
      for (const [tgt, mode] of [[fc, "bascule"], [fl, "localités"]]) {
        if (!tgt || arr) continue;
        let ax = null, ay = null, ad = 1e9;
        for (const run of prevLines) for (const [x, y] of run) { const d = Math.hypot(x - tgt[0], y - tgt[1]); if (d < ad) { ad = d; ax = x; ay = y; } }
        if (ax === null || ad * km < 1) continue;
        arr = {ax, ay, ux: (tgt[0] - ax) / ad, uy: (tgt[1] - ay) / ad, len: ad, mode};
        if (mode === "localités") {
          // direction : moyenne des déplacements de chaque localité basculée depuis le point de l'ancienne ligne le plus
          // proche d'elle (un agrégat de 15 km peut réunir deux poussées voisines ; leur barycentre seul serait trompeur)
          let sx = 0, sy = 0;
          for (const q of c.items) { if (!q.flipped) continue;
            let qd = 1e9, qx = 0, qy = 0;
            for (const run of prevLines) for (const [x, y] of run) { const d = Math.hypot(x - q.gx, y - q.gy); if (d < qd) { qd = d; qx = x; qy = y; } }
            if (qd * km >= 0.5) { sx += q.w * (q.gx - qx) / qd; sy += q.w * (q.gy - qy) / qd; } }
          const sl = Math.hypot(sx, sy);
          if (sl > 1e-6) { arr.ux = sx / sl; arr.uy = sy / sl; }
        }
      }
      if (!arr) {
        const gp = pc ? gradAt(pc, bx, by) : [0, 0], gn = Math.hypot(gp[0], gp[1]);
        if (gn < 1e-4) continue;
        const sgn = camp === 1 ? -1 : 1, ux = sgn * gp[0] / gn, uy = sgn * gp[1] / gn;
        arr = {ax: bx, ay: by, ux, uy, len: Math.hypot(cx - bx, cy - by), mode: "gradient"};
      }
      // la direction retenue doit aller vers les localités basculées et vers les mailles basculées quand ces repères
      // existent (à plus de 1 km de la base) ; sinon le sens n'est pas établi : repère creux, sans direction
      const against = [fl, fc].some(t => t && Math.hypot(t[0] - arr.ax, t[1] - arr.ay) * km >= 1 && (t[0] - arr.ax) * arr.ux + (t[1] - arr.ay) * arr.uy <= 0);
      if (against) { out.push({ax: cx, ay: cy, cx, cy, ru: camp === 1, n, w: sw, dkm: bd * km, late: true, undirected: true, items: c.items}); continue; }
      const gd = guardDir(POLD, pc, arr.ax, arr.ay, arr.ux, arr.uy, camp === 1, c.items.map(q => [q.gx, q.gy, q.w]));
      if (gd.st === "incertaine" && !gd.dir) { out.push({ax: cx, ay: cy, cx, cy, ru: camp === 1, n, w: sw, dkm: bd * km, late: true, undirected: true, items: c.items, why: gd.why}); continue; }
      const L = Math.max(arr.len, 4 / km);
      out.push({ax: arr.ax, ay: arr.ay, cx: arr.ax + gd.ux * L, cy: arr.ay + gd.uy * L, ru: camp === 1, n, w: sw, dkm: bd * km, late: false,
                items: c.items, dir: [gd.ux, gd.uy], mode: arr.mode, bary: [cx, cy], refs: [fl, fc], reoriented: gd.st !== "ok", uncertain: gd.st === "incertaine", why: gd.why});
    }
  }
  return evCache[fi] = out;
}
// déplacement de la ligne 50 % à moins de 15 km d'un agrégat d'annonces : barycentre des sommets de l'ancienne ligne
// passés du côté du camp qui annonce → barycentre des sommets de la nouvelle ligne qui étaient du côté adverse.
// null si l'un des deux ensembles a moins de 3 sommets ou si le déplacement fait moins de 1 km.
function lineShift(oldRuns, newRuns, pc, pold, cx, cy, camp){
  const km = FR.grid.size / 1000, R = 15 / km, own = v => camp === 1 ? v > 0.55 : v < 0.45, adv = v => camp === 1 ? v < 0.45 : v > 0.55;
  const acc = (runs, g, test) => { let x = 0, y = 0, n = 0;
    for (const run of runs) for (const [px, py] of run) {
      if (Math.hypot(px - cx, py - cy) > R) continue;
      const v = sampleAt(g, px, py); if (v !== null && test(v)) { x += px; y += py; n++; } }
    return n >= 3 ? [x / n, y / n] : null; };
  const A = acc(oldRuns, pc, own), B = acc(newRuns, pold, adv);
  if (!A || !B) return null;
  const dx = B[0] - A[0], dy = B[1] - A[1], len = Math.hypot(dx, dy);
  if (len * km < 1) return null;
  return {ax: A[0], ay: A[1], ux: dx / len, uy: dy / len, len};
}
// barycentre des mailles qui ont basculé vers le camp annonceur (adverses à p_old, au camp à p_now) à moins de 15 km ;
// null s'il y en a moins de 3
// diagnostic (headless) : flèches d'un mois dans une boîte, avec leur tête et le garde-fou de direction
window.__uafDirBox = (fi, b) => { const out = [], p0 = POLDOF(fi), km = FR.grid.size / 1000, ll = (u, v) => proj.invert([gAff[0] + u * gAff[2], gAff[1] + v * gAff[2]]).map(v => +v.toFixed(3));
  const inb = q => q[0] >= b[0] && q[0] <= b[1] && q[1] >= b[2] && q[1] <= b[3];
  for (const L of ["overview", 20, 10]) for (const a of (L === "overview" ? overviewArrows(fi, "equilibre") : computeArrows(fi, L, "equilibre"))) {
    const base = ll(a.gx, a.gy); if (!inb(base)) continue;
    out.push({type: "gradient " + L, ru: a.ru, base, head: ll(a.gx + a.dx * 5 / km, a.gy + a.dy * 5 / km), viol: p0 && !a.uncertain ? dirViolation(p0, a.gx, a.gy, a.dx, a.dy, a.ru) : null, reoriented: !!a.reoriented, uncertain: !!a.uncertain, why: a.why || null}); }
  for (const a of eventArrows(fi)) { const base = ll(a.ax, a.ay); if (!inb(base) || a.late) continue;
    out.push({type: "localités", ru: a.ru, base, head: ll(a.cx, a.cy), viol: p0 ? dirViolation(p0, a.ax, a.ay, a.dir[0], a.dir[1], a.ru) : null, reoriented: !!a.reoriented, uncertain: false, why: a.why || null}); }
  return out; };
// diagnostic (headless) : flèches dessinées au zoom k dans une boîte [lon0, lon1, lat0, lat1], avec les mailles sondées
window.__uafHeads = (fi, k, b) => { const F = headFields(fi, "equilibre"), ll = (u, v) => proj.invert([gAff[0] + u * gAff[2], gAff[1] + v * gAff[2]]).map(x => +x.toFixed(3));
  return drawnArrows(fi, k, "equilibre").filter(d => { const q = ll(d.ax, d.ay); return q[0] >= b[0] && q[0] <= b[1] && q[1] >= b[2] && q[1] <= b[3]; }).map(d => ({type: d.type, ru: d.ru, base: ll(d.ax, d.ay), g: d.g, Lg: +d.Lg.toFixed(2),
    pts: d.g ? [0.25, 0.5, 0.75, 1].map(f => { const x = d.ax + d.g.ux * d.Lg * f, y = d.ay + d.g.uy * d.Lg * f, c = Math.floor(y) * GX + Math.floor(x); return [ll(x, y), cellPlace[c], RUSSIA(c), F.p0 ? +F.p0[c].toFixed(2) : null, +F.pc[c].toFixed(2)]; }) : null})); };
// diagnostic (headless) : mailles basculées vers un camp entre le mois précédent et le mois fi, à R km d'un point
window.__uafFlipped = (fi, lon, lat, R, ru) => { const p0 = POLDOF(fi), pc = fullField("equilibre", fi), q = proj([lon, lat]), cx = (q[0] - gAff[0]) / gAff[2], cy = (q[1] - gAff[1]) / gAff[2], km = FR.grid.size / 1000, out = [];
  if (!p0 || !pc) return out;
  for (let c = 0; c < GX * GY; c++) { const i = c % GX, j = Math.floor(c / GX); if (Math.hypot(i + 0.5 - cx, j + 0.5 - cy) * km > R || cellPlace[c] === -1) continue;
    if (ru ? (p0[c] < 0.45 && pc[c] > 0.55) : (p0[c] > 0.55 && pc[c] < 0.45)) out.push(proj.invert([gAff[0] + (i + 0.5) * gAff[2], gAff[1] + (j + 0.5) * gAff[2]]).map(v => +v.toFixed(3))); }
  return out; };
const POLDOF = fi => { const f = frameAt(fi, 30); return f >= 0 ? fullField("equilibre", f) : null; };
// ---------- garde-fou de direction (toutes les flèches) ----------
// Une flèche ne pointe jamais vers une zone déjà tenue par le camp qui avance au mois précédent (p ≥ 0,95 pour une
// avancée russe, ≤ 0,05 pour une avancée ukrainienne), ni à travers la frontière d'État vers son propre territoire, et sa
// tête tombe là où ce camp était moins probable qu'à la base. En cas de conflit, la flèche est RÉORIENTÉE le long de la
// limite (ligne 50 % ou frontière) : on retire la composante vers la zone tenue, on garde la tangente, dans le sens où la
// ligne a réellement reculé (mailles basculées de chaque côté) ; à défaut de sens établi, elle devient incertaine.
const RUSSIA = c => cellPlace[c] === -2 || (cellPlace[c] === -1 && ruMask && ruMask[c]);
function dirViolation(p0, ax, ay, ux, uy, ru){
  const km = FR.grid.size / 1000, L = 5 / km, hx = ax + ux * L, hy = ay + uy * L;
  const ci = (x, y) => Math.max(0, Math.min(GY - 1, Math.floor(y))) * GX + Math.max(0, Math.min(GX - 1, Math.floor(x)));
  const cb = ci(ax, ay), ch = ci(hx, hy), pb = p0[cb], ph = p0[ch];
  if (ru ? ph >= 0.95 : ph <= 0.05) return "zone déjà tenue";
  // frontière d'État : sondée jusqu'à 10 km (une avancée russe ne progresse pas vers la Russie, ni une avancée
  // ukrainienne en Russie vers l'Ukraine)
  for (const f of [0.4, 0.6, 0.8, 1.0, 1.4, 2.0]) { const c2 = ci(ax + ux * L * f, ay + uy * L * f);
    if (ru && !RUSSIA(cb) && RUSSIA(c2)) return "frontière vers la Russie";
    if (!ru && RUSSIA(cb) && cellPlace[c2] >= 0) return "frontière vers l'Ukraine"; }
  if (ru ? ph >= pb + 0.02 : ph <= pb - 0.02) return "tête plus favorable que la base";
  return null;
}
function guardDir(p0, pc, ax, ay, ux, uy, ru, pts){
  if (!p0) return {ux, uy, st: "ok"};
  const why = dirViolation(p0, ax, ay, ux, uy, ru);
  if (!why) return {ux, uy, st: "ok"};
  // normale à la limite : gradient de p au mois précédent (vers le côté russe), ou de la frontière d'État
  let [gx, gy] = gradAt(p0, ax, ay), gn = Math.hypot(gx, gy);
  if (why.startsWith("frontière") || gn < 1e-4) {
    const R = 3; gx = 0; gy = 0;
    for (let dj = -R; dj <= R; dj++) for (let di = -R; di <= R; di++) {
      const c = (Math.floor(ay) + dj) * GX + Math.floor(ax) + di; if (c < 0 || c >= GX * GY) continue;
      if (RUSSIA(c)) { gx += di; gy += dj; } }
    gn = Math.hypot(gx, gy);
  }
  if (gn < 1e-6) return {ux, uy, st: "incertaine", why};
  const nx = gx / gn, ny = gy / gn, tx = -ny, ty = nx, back = ru ? -1 : 1;   // back : vers le camp qui recule
  // sens le long de la limite : mailles basculées vers le camp qui avance, de chaque côté de la base (15 km)
  let plus = 0, minus = 0;
  if (pc) { const R = 15 / (FR.grid.size / 1000);
    for (let j = Math.max(0, Math.floor(ay - R)); j <= Math.min(GY - 1, Math.ceil(ay + R)); j++)
      for (let i = Math.max(0, Math.floor(ax - R)); i <= Math.min(GX - 1, Math.ceil(ax + R)); i++) {
        const c = j * GX + i; if (cellPlace[c] === -1 || Math.hypot(i + 0.5 - ax, j + 0.5 - ay) > R) continue;
        const was = p0[c], now = pc[c];
        if (ru ? (was < 0.45 && now > 0.55) : (was > 0.55 && now < 0.45)) { const d = (i + 0.5 - ax) * tx + (j + 0.5 - ay) * ty; if (d > 0) plus++; else if (d < 0) minus++; }
      } }
  // à défaut de mailles basculées, les localités qui ont basculé selon les sources (flèches d'annonces), pondérées par
  // leur position le long de la limite : le sens qu'elles indiquent est gardé, mais la flèche est « incertaine »
  let fromPts = false;
  if (Math.max(plus, minus) < 3 && pts && pts.length >= 2) { plus = 0; minus = 0; fromPts = true;
    for (const [x, y, w] of pts) { const d = (x - ax) * tx + (y - ay) * ty; plus += Math.max(0, d) * w; minus += Math.max(0, -d) * w; } }
  if (fromPts && Math.max(plus, minus) > 1.1 * Math.min(plus, minus) && Math.max(plus, minus) > 0.5) {
    const sg = plus > minus ? 1 : -1;
    if (!dirViolation(p0, ax, ay, sg * tx, sg * ty, ru)) return {ux: sg * tx, uy: sg * ty, st: "incertaine", dir: true, why: why + " ; sens d'après les localités annoncées"};
  }
  if (Math.max(plus, minus) < 2 || Math.max(plus, minus) < 1.5 * Math.min(plus, minus)) return {ux: tx, uy: ty, st: "incertaine", why};
  const sg = plus > minus ? 1 : -1;
  let vx = sg * tx + 0.25 * back * nx, vy = sg * ty + 0.25 * back * ny; const vn = Math.hypot(vx, vy); vx /= vn; vy /= vn;
  if (dirViolation(p0, ax, ay, vx, vy, ru)) { vx = sg * tx; vy = sg * ty; }
  if (dirViolation(p0, ax, ay, vx, vy, ru)) return {ux: sg * tx, uy: sg * ty, st: "incertaine", why};
  return {ux: vx, uy: vy, st: "réorientée", why};
}
function flippedCentroid(pc, pold, cx, cy, camp){
  const km = FR.grid.size / 1000, R = 15 / km; let x = 0, y = 0, n = 0;
  for (let j = Math.max(0, Math.floor(cy - R)); j <= Math.min(GY - 1, Math.ceil(cy + R)); j++)
    for (let i = Math.max(0, Math.floor(cx - R)); i <= Math.min(GX - 1, Math.ceil(cx + R)); i++) {
      if (Math.hypot(i + 0.5 - cx, j + 0.5 - cy) > R) continue;
      const c = j * GX + i, a = pold[c], b = pc[c];
      if (camp === 1 ? (a < 0.45 && b > 0.55) : (a > 0.55 && b < 0.45)) { x += i + 0.5; y += j + 0.5; n++; }
    }
  return n >= 3 ? [x / n, y / n] : null;
}
// ---------- longueur dessinée et garde-fou de tête (toutes les flèches) ----------
// Longueur à l'écran : proportionnelle au mouvement v (km par 30 jours), même règle et même plafond pour toutes les
// flèches (pleines, pointillées, « poussée possible », annonces). Le minimum des flèches pointillées ne sert qu'à
// garder une hampe lisible et ne dépasse jamais le plafond d'une flèche pleine.
// Tête : demi-largeur hs plafonnée à HEAD_MAX px et à 28 % de la longueur (tête ≤ 1,6·hs de long, 2·hs de large), même
// règle pour tous les types ; w imposé pour les flèches d'annonces (épaisseur selon le poids des localités)
const HEAD_MAX = 6;
function arrowPx(v, k, uncertain, wForce){
  // plancher de lisibilité (≈ 20 px, hampe comprise) et plafond (40 px en vue d'ensemble, 70 px au zoom), proportionnel entre
  const overview = k < 1.6, cap = overview ? 40 : 70, floor = 20, w = wForce != null ? wForce : overview ? 1.8 : Math.min(7, 1.2 + v * 0.5);
  let len = overview ? Math.min(cap, floor + v * 4) : Math.min(cap, Math.max(floor, 10 + v * 7));
  let hs = Math.min(HEAD_MAX, 2.5 + w * 0.9, len * 0.28);
  if (uncertain) len = Math.max(len, Math.min(cap, hs * 1.6 + 12));
  hs = Math.min(hs, len * 0.28);
  return {len, w, hs, overview, cap};
}
// barrières dures (grands fleuves, plans d'eau, escarpements, canaux : barrieres_dures.js) : une flèche qui en traverse une
// est TRONQUÉE juste avant (ni supprimée, ni réorientée) ; renvoie la longueur dessinée en px et le code de barrière
function hardLen(ax, ay, ux, uy, len, k){
  if (!window.hardTruncate) return {len, code: 0};
  const Lg = len / (gAff[2] * k), r = window.hardTruncate([ax, ay], [ax + ux * Lg, ay + uy * Lg], 0.5);
  return r.code ? {len: Math.hypot(r.head[0] - ax, r.head[1] - ay) * gAff[2] * k, code: r.code} : {len, code: 0};
}
const evWidth = (a, k) => k < 1.6 ? Math.min(3, 1.4 + a.w * 0.3) : Math.min(5, 1.6 + a.w * 0.7);
// tête interdite : maille de Russie qui n'est pas nettement tenue par l'Ukraine (bande de Koursk : p < 0,2 au mois
// précédent ou au mois affiché ; une bande frontalière seulement incertaine, comme au nord de Vovtchansk, ne compte pas) ;
// sondée le long de la hampe
const headFields = (fi, view) => ({p0: POLDOF(fi), pc: fullField(view || fieldName(), fi)});
function forbiddenAt(F, x, y){
  const c = Math.max(0, Math.min(GY - 1, Math.floor(y))) * GX + Math.max(0, Math.min(GX - 1, Math.floor(x)));
  return RUSSIA(c) && !((F.p0 && F.p0[c] < 0.2) || (F.pc && F.pc[c] < 0.2)) ? c : -1;
}
function segForbidden(F, ax, ay, ux, uy, Lg){
  for (const f of [0.25, 0.5, 0.75, 1]) { const c = forbiddenAt(F, ax + ux * Lg * f, ay + uy * Lg * f); if (c >= 0) return c; }
  // la pointe garde une marge d'une maille (2 km) avec la Russie : une tête posée sur la frontière se lit « en Russie »
  const hx = ax + ux * Lg, hy = ay + uy * Lg;
  for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) { const c = forbiddenAt(F, hx + di, hy + dj); if (c >= 0) return c; }
  // et la flèche ne pointe pas vers la Russie toute proche : prolongée de 2 mailles (4 km), elle doit rester hors de Russie
  for (const e of [1, 2]) { const c = forbiddenAt(F, hx + ux * e, hy + uy * e); if (c >= 0) return c; }
  return -1;
}
// garde-fou de tête : si la flèche entre en Russie, elle est projetée sur la tangente de la frontière (côté ukrainien,
// dans le sens le plus proche de la direction d'origine), puis légèrement inclinée vers l'intérieur ; sinon pas de flèche
function headGuard(F, ax, ay, ux, uy, Lg){
  const c0 = segForbidden(F, ax, ay, ux, uy, Lg); if (c0 < 0) return {ux, uy, moved: false};
  const px = c0 % GX + 0.5, py = Math.floor(c0 / GX) + 0.5, R = 4; let gx = 0, gy = 0;
  for (let dj = -R; dj <= R; dj++) for (let di = -R; di <= R; di++) {
    const c = (Math.floor(py) + dj) * GX + Math.floor(px) + di; if (c < 0 || c >= GX * GY) continue;
    if (RUSSIA(c)) { gx += di; gy += dj; } }
  const gn = Math.hypot(gx, gy); if (gn < 1e-6) return null;
  const nx = gx / gn, ny = gy / gn; let tx = -ny, ty = nx; if (tx * ux + ty * uy < 0) { tx = -tx; ty = -ty; }
  for (const tilt of [0, 0.3, 0.6]) { let vx = tx - tilt * nx, vy = ty - tilt * ny; const vn = Math.hypot(vx, vy); vx /= vn; vy /= vn;
    if (segForbidden(F, ax, ay, vx, vy, Lg) < 0) return {ux: vx, uy: vy, moved: true}; }
  return null;
}
// flèches de gradient dessinées au zoom k (même sélection que drawArrows)
function arrowsAt(fi, k, view){
  const km_ = FR.grid.size / 1000, a20 = k < 3 ? null : computeArrows(fi, 20, view);
  let arr = k < 3 ? overviewArrows(fi, view) : k >= 6 ? computeArrows(fi, 10, view)
    : a20.concat(computeArrows(fi, 10, view).filter(b => !a20.some(a => Math.hypot(a.gx - b.gx, a.gy - b.gy) * km_ < 15)));
  if (k >= 3) arr = arr.concat(possibleArrows(fi, view).filter(b => !arr.some(a => a.possible && Math.hypot(a.gx - b.gx, a.gy - b.gy) * km_ < 20)));
  return arr;
}
// toutes les flèches dessinées au zoom k : base, direction après garde-fou, longueur en mailles (null = pas de flèche)
function drawnArrows(fi, k, view){
  const F = headFields(fi, view), km = FR.grid.size / 1000, out = [];
  for (const a of arrowsAt(fi, k, view)) { const P = arrowPx(a.v, k, a.uncertain), Lg = P.len / (gAff[2] * k), g = headGuard(F, a.gx, a.gy, a.dx, a.dy, Lg);
    const hb = g ? hardLen(a.gx, a.gy, g.ux, g.uy, P.len, k) : {len: P.len, code: 0};
    out.push({type: a.possible ? "poussée possible" : a.uncertain ? "incertaine" : "gradient", a, ax: a.gx, ay: a.gy, ru: a.ru, g, Lg: hb.len / (gAff[2] * k), LgGuard: Lg, P: Object.assign({}, P, {len: hb.len, hs: Math.min(P.hs, hb.len * 0.28)}), hard: hb.code}); }
  for (const a of eventArrows(fi)) { if (a.late) continue;
    const L0 = Math.hypot(a.cx - a.ax, a.cy - a.ay); if (L0 < 1e-6) continue;
    const P = arrowPx(L0 * km, k, a.uncertain, evWidth(a, k)), Lg = P.len / (gAff[2] * k), g = headGuard(F, a.ax, a.ay, (a.cx - a.ax) / L0, (a.cy - a.ay) / L0, Lg);
    const hb = g ? hardLen(a.ax, a.ay, g.ux, g.uy, P.len, k) : {len: P.len, code: 0};
    out.push({type: "annonces", a, ax: a.ax, ay: a.ay, ru: a.ru, g, Lg: hb.len / (gAff[2] * k), LgGuard: Lg, P: Object.assign({}, P, {len: hb.len, hs: Math.min(P.hs, hb.len * 0.28)}), event: true, hard: hb.code}); }
  return out;
}
function drawEventArrows(k){
  const fi = frontIdx(); if (fi < 0) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const HF = headFields(fi);
  for (const a of eventArrows(fi)) {
    const sx = (gAff[0] + a.ax * gAff[2]) * k + tr.x, sy = (gAff[1] + a.ay * gAff[2]) * k + tr.y;
    if (a.late) {
      // « confirmé par annonce » : changement que les cartes montraient déjà ; cercle creux, sans direction
      const r = k < 1.6 ? 3.5 : 5;
      ctx.globalAlpha = 0.7; ctx.strokeStyle = a.ru ? C.front : C.uactl; ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI); ctx.stroke();
      arrowList.push({x: sx, y: sy, a: {ru: a.ru, v: 0, ev: a.n, late: true, undirected: !!a.undirected}});
      continue;
    }
    const L0 = Math.hypot(a.cx - a.ax, a.cy - a.ay); if (L0 < 1e-6) continue;
    const ov = k < 1.6, w = evWidth(a, k), col = a.ru ? C.front : C.uactl;
    // longueur : règle commune (arrowPx, v = déplacement en km sur 30 jours) ; tête jamais en Russie (headGuard)
    const PX = arrowPx(L0 * FR.grid.size / 1000, k, a.uncertain, w);
    const hg = headGuard(HF, a.ax, a.ay, (a.cx - a.ax) / L0, (a.cy - a.ay) / L0, PX.len / (gAff[2] * k));
    if (!hg) { const r = ov ? 3.5 : 5; ctx.globalAlpha = 0.7; ctx.strokeStyle = col; ctx.lineWidth = 1.3; ctx.beginPath(); ctx.arc(sx, sy, r, 0, 2 * Math.PI); ctx.stroke();
      arrowList.push({x: sx, y: sy, a: {ru: a.ru, v: 0, ev: a.n, late: true, undirected: true}}); continue; }
    const L = hardLen(a.ax, a.ay, hg.ux, hg.uy, PX.len, k).len, hs = Math.min(PX.hs, L * 0.28);
    const dx = hg.ux, dy = hg.uy, ex = sx + dx * L, ey = sy + dy * L, hx = -dy, hy = dx;
    const path = () => { ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - dx * hs, ey - dy * hs); };
    const head = () => { ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - dx * hs * 1.6 + hx * hs, ey - dy * hs * 1.6 + hy * hs); ctx.lineTo(ex - dx * hs * 1.6 - hx * hs, ey - dy * hs * 1.6 - hy * hs); ctx.closePath(); };
    // « localités qui ont basculé » : flèche pleine, sans contour, avec un petit cercle creux à la base (distincte des
    // flèches de gradient, qui n'en ont pas)
    ctx.globalAlpha = 0.95; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = w;
    if (a.uncertain) { ctx.setLineDash([4, 3]); ctx.lineWidth = Math.max(2, w); ctx.lineCap = "butt"; path(); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 2; ctx.fillStyle = C.bg; head(); ctx.fill(); ctx.stroke(); }
    else { path(); ctx.stroke(); head(); ctx.fill(); }
    const rb = ov ? 3 : 4.5;
    ctx.fillStyle = C.bg; ctx.lineWidth = 1.6; ctx.beginPath(); ctx.arc(sx, sy, rb, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    arrowList.push({x: (sx + ex) / 2, y: (sy + ey) / 2, a: {ru: a.ru, v: 0, ev: a.n, uncertain: !!a.uncertain}});
  }
  ctx.globalAlpha = 1;
}
// diagnostic (headless) : flèches d'événements d'un mois en coordonnées géographiques
window.__uafEvents = fi => eventArrows(fi).map(a => { const p = proj.invert([gAff[0] + a.cx * gAff[2], gAff[1] + a.cy * gAff[2]]), b = proj.invert([gAff[0] + a.ax * gAff[2], gAff[1] + a.ay * gAff[2]]);
  const ll = (x, y) => proj.invert([gAff[0] + x * gAff[2], gAff[1] + y * gAff[2]]).map(v => +v.toFixed(3));
  return {lon: +p[0].toFixed(2), lat: +p[1].toFixed(2), from: [+b[0].toFixed(2), +b[1].toFixed(2)], ru: a.ru, n: a.n, w: +a.w.toFixed(2), dkm: +a.dkm.toFixed(1), late: !!a.late,
    undirected: !!a.undirected, mode: a.mode || null, dir: a.dir ? a.dir.map(v => +v.toFixed(2)) : null, bary: a.bary ? ll(...a.bary) : null,
    locs: (a.items || []).map(q => [...ll(q.gx, q.gy), +q.w.toFixed(2), [...q.srcs].join("+"), !!q.flipped])}; });
// diagnostic (headless) : état d'affichage (zoom, info-bulle, pas) pour les tests mobiles
window.__uafState = () => ({k: tr.k, x: tr.x, y: tr.y, tip: tip.style.display, step: stepIdx, nsteps: AX.length, fvar, nowcast: amode() === "nowcast",
  checks: Object.fromEntries([...document.querySelectorAll('input[type="checkbox"]')].map(c => [c.id || c.dataset.raw || c.dataset.var, c.checked]))});
// diagnostic (headless) : recadrage et mois
window.__uafGoto = (month, lon, lat, k) => { const i = D.months.indexOf(month); if (i >= 0) setMonth(i); zoomTo(lon, lat, k); };
// diagnostic (headless) : flèches d'un mois en coordonnées géographiques
window.__uafArrows = (fi, L) => (L === "overview" ? overviewArrows(fi, "equilibre") : computeArrows(fi, L, "equilibre")).map(a => { const ll = proj.invert([gAff[0] + a.gx * gAff[2], gAff[1] + a.gy * gAff[2]]); return {lon: +ll[0].toFixed(2), lat: +ll[1].toFixed(2), ru: a.ru, v: +a.v.toFixed(2), dir: [+a.dx.toFixed(2), +a.dy.toFixed(2)], vm: a.vm, uncertain: !!a.uncertain}; });
// diagnostic (headless) : p(contrôle russe) en un point, km² russes et sommets de la ligne 50 % dans une boîte
const cellOf = (lon, lat) => { const q = proj([lon, lat]); return Math.floor((q[1] - gAff[1]) / gAff[2]) * GX + Math.floor((q[0] - gAff[0]) / gAff[2]); };
const inBox = (gx, gy, b) => { const ll = proj.invert([gAff[0] + gx * gAff[2], gAff[1] + gy * gAff[2]]); return ll[0] >= b[0] && ll[0] <= b[1] && ll[1] >= b[2] && ll[1] <= b[3]; };
// diagnostic (headless) : déplacement du dernier mois (km, signé) de tous les tronçons de L km, sans aucun seuil
window.__uafMoves = (fi, L) => { const save = {...ARROW_PARAMS}; Object.assign(ARROW_PARAMS, {vmin: 0, need: 0, vstrong: 0, jump: 1e9, hl: 0, jumpHl: 0}); arrowCache = {};
  const out = computeArrows(fi, L || 20, "equilibre").map(a => a.vm[0]); Object.assign(ARROW_PARAMS, save); arrowCache = {}; return out; };
// diagnostic (headless) : part des mailles d'une boîte situées dans la zone grise de contact
window.__uafContactShare = (fi, b) => { const F = viewFields("contact"); if (!F || !F[fi]) return null; let n = 0, z = 0;
  for (let u = 0; u < UCELLS.length; u++) { const c = UCELLS[u]; if (cellPlace[c] < 0 || !inBox(c % GX + 0.5, Math.floor(c / GX) + 0.5, b)) continue; n++; z += F[fi][u] / 63; } return n ? z / n : null; };
// diagnostic (headless) : P(contact au sol) maximale à moins de 4 km d'un point
window.__uafContactAt = (fi, lon, lat) => { const F = viewFields("contact"); if (!F || !F[fi]) return null; const k = cellOf(lon, lat), j0 = Math.floor(k / GX), i0 = k % GX; let best = 0;
  for (let dj = -2; dj <= 2; dj++) for (let di = -2; di <= 2; di++) { const u = d3.bisectLeft(UCELLS, (j0 + dj) * GX + i0 + di); if (UCELLS[u] === (j0 + dj) * GX + i0 + di) best = Math.max(best, F[fi][u] / 63); } return best; };
window.__uafP = (fi, lon, lat) => { const g = fullField("equilibre", fi); return g ? g[cellOf(lon, lat)] : null; };
window.__uafArea = (fi, b, band) => { const g = fullField("equilibre", fi), a = (FR.grid.size / 1000) ** 2; let s = 0; for (let c = 0; c < GX * GY; c++) if ((band ? cellPlace[c] === -2 : cellPlace[c] >= 0) && inBox(c % GX + 0.5, Math.floor(c / GX) + 0.5, b)) s += (band ? 1 - g[c] : g[c]) * a; return s; };
window.__uafLine = (fi, b) => lines50("equilibre", fi).flat().filter(p => inBox(p[0], p[1], b)).length;
// contrôle automatique (headless) : base à moins de 2 km de la ligne 50 %, angle avec la normale locale < 30°
window.__uafArrowCheck = function(){
  const fails = [], km = FR.grid.size / 1000; let total = 0, reoriented = 0, evN = 0, late = 0, flipChecked = 0, redir = 0, border = 0;
  const redirList = [], llg = (u, v) => proj.invert([gAff[0] + u * gAff[2], gAff[1] + v * gAff[2]]).map(v => +v.toFixed(2));
  for (let fi = 3; fi < FR.months.length; fi++) {
    if (!FR.months[fi]) continue;
    const runs = lines50("equilibre", fi), pts = runs.flat(), fpv = frameAt(fi, 30), p0 = fpv >= 0 ? fullField("equilibre", fpv) : null;
    for (const L of [100, 50, 20, 10]) for (const a of computeArrows(fi, L, "equilibre")) {
      total++;
      // garde-fou de direction (bloquant) : jamais vers une zone déjà tenue par le camp qui avance ni à travers la
      // frontière d'État vers son propre territoire ; les flèches réorientées le long de la limite sont listées
      if (RUSSIA(Math.floor(a.gy) * GX + Math.floor(a.gx))) border++;
      if (p0 && !a.uncertain) { const w = dirViolation(p0, a.gx, a.gy, a.dx, a.dy, a.ru); if (w) fails.push({mois: FR.months[fi].date, L, direction: w, ru: a.ru, base: llg(a.gx, a.gy)}); }
      if (a.guarded) { redir++; if (redirList.length < 40) redirList.push(`${FR.months[fi].date.slice(0, 7)} L${L} ${a.ru ? "RU" : "UA"} ${llg(a.gx, a.gy).join(",")} ${a.reoriented ? "réorientée" : "incertaine"} (${a.why})`); continue; }
      // la base doit être sur la ligne ; l'angle est mesuré à la normale la plus favorable parmi les tronçons qui
      // passent à moins de 2 km (deux bras de ligne peuvent se toucher au même sommet)
      let bd = 1e9, ang = 90;
      for (const run of runs) for (let t = 0; t < run.length; t++) {
        const d = Math.hypot(run[t][0] - a.gx, run[t][1] - a.gy);
        if (d < bd) bd = d;
        if (d * km < 2) { const nn = polyNormal(run, t); if (nn) ang = Math.min(ang, Math.acos(Math.min(1, Math.abs(nn[0] * a.dx + nn[1] * a.dy))) * 180 / Math.PI); }
      }
      if (bd * km >= 2 || ang >= 30) fails.push({mois: FR.months[fi].date, L, dist_km: +(bd * km).toFixed(2), angle: +ang.toFixed(1)});
      // sens : avancée russe vers −∇p, ukrainienne vers +∇p (p = contrôle russe partout), à la base
      const g = fullField("equilibre", fi), [gx, gy] = gradAt(g, a.gx, a.gy), s = a.ru ? -1 : 1;
      if (s * (a.dx * gx + a.dy * gy) <= 0) fails.push({mois: FR.months[fi].date, L, sens: "gradient à rebours"});
    }
    const ge = fullField(fieldName(), fi);
    for (const a of eventArrows(fi)) {
      total++; evN++; if (a.undirected) reoriented++; if (a.late) { late++; continue; }
      if (POLDOF(fi)) { const w = dirViolation(POLDOF(fi), a.ax, a.ay, a.dir[0], a.dir[1], a.ru); if (w) fails.push({mois: FR.months[fi].date, annonces: true, direction: w, ru: a.ru, base: llg(a.ax, a.ay)}); }
      if (a.reoriented) { redir++; if (redirList.length < 40) redirList.push(`${FR.months[fi].date.slice(0, 7)} annonces ${a.ru ? "RU" : "UA"} ${llg(a.ax, a.ay).join(",")} (${a.why})`); continue; }
      const s = a.ru ? -1 : 1, tx = a.cx - a.ax, ty = a.cy - a.ay;
      // direction comparée au déplacement réel (localités annoncées basculées, mailles basculées), recalculé ici à partir
      // des champs ; le gradient ponctuel, trompeur sur les flancs d'un saillant, ne sert que s'il n'y a aucun repère
      const fp0 = frameAt(fi, 30), po = fp0 >= 0 ? fullField("equilibre", fp0) : null, ll = (u, v) => proj.invert([gAff[0] + u * gAff[2], gAff[1] + v * gAff[2]]).map(v => +v.toFixed(2));
      const refs = [];
      if (po && a.items) {
        let x = 0, y = 0, n = 0;
        for (const q of a.items) { const c = Math.round(q.gy - 0.5) * GX + Math.round(q.gx - 0.5), was = po[c], now = ge[c];
          if (a.ru ? (was < 0.5 && now > 0.5) : (was > 0.5 && now < 0.5)) { x += q.gx; y += q.gy; n++; } }
        if (n) refs.push(["localités basculées", [x / n, y / n]]);
        const fc = flippedCentroid(ge, po, a.bary ? a.bary[0] : a.cx, a.bary ? a.bary[1] : a.cy, a.ru ? 1 : 0);
        if (fc) refs.push(["mailles basculées", fc]);
      }
      const used = refs.filter(([, r]) => Math.hypot(r[0] - a.ax, r[1] - a.ay) * km >= 1);
      if (used.length) {
        flipChecked++;
        for (const [nm, r] of used) if (tx * (r[0] - a.ax) + ty * (r[1] - a.ay) <= 0)
          fails.push({mois: FR.months[fi].date, sens: "annonces contre les " + nm, ru: a.ru, base: ll(a.ax, a.ay), pointe: ll(a.cx, a.cy), ref: ll(...r), mode: a.mode});
        continue;
      }
      const [gx, gy] = gradAt(ge, a.ax, a.ay);
      if (s * (tx * gx + ty * gy) <= 0) fails.push({mois: FR.months[fi].date, sens: "annonces à rebours", ru: a.ru});
    }
  }
  // têtes (bloquant) : aucune flèche dessinée, de quelque type que ce soit, dont la hampe ou la tête entre en Russie
  // hors bande de Koursk tenue par l'Ukraine ; contrôlé à la longueur réellement dessinée (zooms 1 à 8) ;
  // longueur : jamais au-delà du plafond des flèches pleines
  let heads = 0, headMoved = 0, headDropped = 0, headHard = 0;
  for (let fi = 3; fi < FR.months.length; fi++) { if (!FR.months[fi]) continue;
    const F = headFields(fi, "equilibre");
    for (const k of [1, 1.5, 2, 3, 4, 6, 8]) for (const d of drawnArrows(fi, k, "equilibre")) {
      heads++; if (!d.g) { headDropped++; continue; } if (d.g.moved) headMoved++; if (d.hard) headHard++;
      // barrière dure (bloquant) : la flèche dessinée ne la franchit pas
      if (window.hardTruncate && window.hardTruncate([d.ax, d.ay], [d.ax + d.g.ux * d.Lg, d.ay + d.g.uy * d.Lg], 0.5).code) fails.push({mois: FR.months[fi].date, barriere: "franchie", type: d.type, zoom: k, base: llg(d.ax, d.ay)});
      // contrôlé à la longueur validée par headGuard : la flèche tronquée à une barrière en est un sous-segment
      const c = segForbidden(F, d.ax, d.ay, d.g.ux, d.g.uy, d.LgGuard || d.Lg);
      if (c >= 0) fails.push({mois: FR.months[fi].date, tete: "en Russie", type: d.type, zoom: k, ru: d.ru, base: llg(d.ax, d.ay), pointe: llg(d.ax + d.g.ux * d.Lg, d.ay + d.g.uy * d.Lg)});
      if (d.P.len > d.P.cap + 1e-6) fails.push({mois: FR.months[fi].date, longueur: Math.round(d.P.len), plafond: d.P.cap, type: d.type, zoom: k});
      // tête (bloquant) : au plus 2·HEAD_MAX px de large et 1,6·HEAD_MAX de long, et jamais plus de 45 % de la flèche
      if (d.P.hs > HEAD_MAX + 1e-6 || d.P.hs * 1.6 > 0.45 * d.P.len + 1e-6) fails.push({mois: FR.months[fi].date, tete_px: +(2 * d.P.hs).toFixed(1), longueur: Math.round(d.P.len), type: d.type, zoom: k});
    } }
  const unc = {};
  for (let fi = 3; fi < FR.months.length; fi++) { if (!FR.months[fi]) continue;
    const o = overviewArrows(fi, "equilibre").filter(a => a.uncertain).length, z = computeArrows(fi, 20, "equilibre").filter(a => a.uncertain).length + computeArrows(fi, 10, "equilibre").filter(a => a.uncertain).length,
      e = eventArrows(fi).filter(a => a.uncertain).length;
    if (o + z + e) unc[FR.months[fi].date.slice(0, 7)] = [o, z, e]; }
  // missiles et drones (bloquant) : aucun lieu de lancement en territoire ukrainien contrôlé (p < 0,5 au mois du lancement)
  let launches = 0;
  const pl = O.launch.places;
  for (const [mi, li] of O.launch.arcs) { const fl = FR.months.findIndex(m => m && m.date.startsWith(D.months[mi])); if (fl < 0) continue;
    const c = cellOf(pl[li][1], pl[li][2]); if (!(c >= 0 && c < GX * GY) || cellPlace[c] < 0) continue;
    launches++; const g = fullField("equilibre", fl);
    if (g && g[c] < 0.5) fails.push({mois: D.months[mi], lancement: pl[li][0], p: +g[c].toFixed(2)}); }
  return {total, fails, reoriented, evN, late, flipChecked, redir, redirList, border, unc, heads, headMoved, headDropped, headHard, launches};
};
function drawArrows(k){
  const fi = frontIdx(); if (fi < 0) return;
  if (!window.FRONTP || !window.FRONTP.mcsd) { loadMcsd(() => { arrowCache = {}; draw(); }); }
  // vue d'ensemble : voir overviewArrows ; au zoom, tronçons de 20 puis 10 km (arrowsAt)
  const arr = arrowsAt(fi, k), HF = headFields(fi);
  arrowList = [];
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const a of arr) {
    const sx = (gAff[0] + a.gx * gAff[2]) * k + tr.x, sy = (gAff[1] + a.gy * gAff[2]) * k + tr.y;
    if (sx < -20 || sx > W + 20 || sy < -20 || sy > H + 20) continue;
    // vue d'ensemble : un seul style discret ; au zoom, longueur et épaisseur proportionnelles à la vitesse ; même règle
    // pour les flèches pointillées (arrowPx) ; tête jamais en Russie hors bande tenue par l'Ukraine (headGuard)
    const P0 = arrowPx(a.v, k, a.uncertain), w = P0.w, overview = P0.overview, hg = headGuard(HF, a.gx, a.gy, a.dx, a.dy, P0.len / (gAff[2] * k));
    if (!hg) continue;
    const hb = hardLen(a.gx, a.gy, hg.ux, hg.uy, P0.len, k), len = hb.len, hs = Math.min(P0.hs, len * 0.28);
    const dx = hg.ux, dy = hg.uy, ex = sx + dx * len, ey = sy + dy * len, col = a.ru ? C.front : C.uactl;
    const hx = -dy, hy = dx;
    const head = () => { ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - dx * hs * 1.6 + hx * hs, ey - dy * hs * 1.6 + hy * hs); ctx.lineTo(ex - dx * hs * 1.6 - hx * hs, ey - dy * hs * 1.6 - hy * hs); ctx.closePath(); };
    ctx.lineCap = "round"; ctx.lineJoin = "round";   // flèches de gradient : pleines, sans contour
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.globalAlpha = overview ? 0.8 : 0.95; ctx.lineWidth = w;
    if (a.uncertain) {   // sens incertain : trait en tirets courts (lisibles sur une hampe courte), tête creuse
      ctx.setLineDash([4, 3]); ctx.lineWidth = Math.max(2.4, w * 1.2); ctx.lineCap = "butt";
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - dx * hs * 1.6, ey - dy * hs * 1.6); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 2; ctx.fillStyle = C.bg; head(); ctx.fill(); ctx.stroke();
    } else {
    ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex - dx * w * 1.6, ey - dy * w * 1.6); ctx.stroke();
    head(); ctx.fill();
    }
    arrowList.push({x: (sx + ex) / 2, y: (sy + ey) / 2, a: hb.code ? Object.assign({}, a, {hard: true}) : a});
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
// zone grise (contact) : mailles reliées à la ligne où nos signaux de combat dépassent deux fois le fond national
// mesuré à 40-60 km de la ligne (champ « contact », rapport × 10, ≥ 10 dans la zone) ; hachures fines grises
let contactFor = -2, contactPat = null;
function buildContact(fi){
  const key = fi + ":" + (C.dark ? "d" : "l"); if (contactFor === key) return;
  contactFor = key; contactPat = null;
  const F = viewFields("contact"); if (!F || !F[fi]) return;
  const S = 4, cv = document.createElement("canvas"); cv.width = GX * S; cv.height = GY * S;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX * S, GY * S), d = img.data, q = F[fi];
  const [r, g, b] = C.dark ? [225, 225, 220] : [45, 45, 45];   // encre : lisible sur la densité rouge comme sur le fond
  for (let u = 0; u < UCELLS.length; u++) {
    if (q[u] < 5) continue;  // P(contact au sol) ≥ 0,08 ; opacité croissante avec P
    const c = UCELLS[u], x0 = (c % GX) * S, y0 = Math.floor(c / GX) * S, a = Math.min(1, 0.35 + q[u] / 63 * 1.6);
    for (let dy = 0; dy < S; dy++) for (let dx = 0; dx < S; dx++) {
      if (((x0 + dx) - (y0 + dy) + 6000) % 3 > 0) continue;  // hachures
      const o = ((y0 + dy) * GX * S + x0 + dx) * 4; d[o] = r; d[o+1] = g; d[o+2] = b; d[o+3] = Math.round(255 * a);
    }
  }
  cx.putImageData(img, 0, 0); contactPat = cv;
}
// infrastructures statiques et publiques (contexte des combats) : voies ferrées, gares principales, centrales, grands sites
// industriels, cokeries et mines, aéroports. Aucune information de frappe ni de dommages. Chargées à la demande.
let showInfra = false, INF = null;
function loadInfra(then){
  if (INF) return then();
  const go = () => { const I = window.INFRA; if (!I) return;
    const g = (lon, lat) => { const q = proj([lon, lat]); return [(q[0] - gAff[0]) / gAff[2], (q[1] - gAff[1]) / gAff[2]]; };
    const pts = [];
    // étiquette = type traduit (langue de la page) + nom propre tel que publié par la source (OSM, WRI, OurAirports)
    const lourd = t => /mine|coal/.test(t) ? "mine" : /coke/.test(t) ? "cokerie" : /chemi/.test(t) ? "usine chimique" : /steel|metal/.test(t) ? "aciérie, métallurgie" : "site industriel lourd";
    for (const s of I.stations || []) pts.push({x: g(s[0], s[1]), t: "gare", n: s[2], k: "gare"});
    for (const p of I.plants || []) pts.push({x: g(p[0], p[1]), t: ({Nuclear: "centrale nucléaire", Coal: "centrale au charbon", Gas: "centrale au gaz", Hydro: "centrale hydraulique", Oil: "centrale au fioul", Solar: "centrale solaire"})[p[3]] || "centrale électrique", n: `${p[2]} (${p[4]} MW)`, k: "centrale"});
    for (const h of I.heavy || []) pts.push({x: g(h[0], h[1]), t: lourd(h[3]), n: h[2], k: "lourd"});
    for (const a of I.airports || []) pts.push({x: g(a[0], a[1]), t: "aéroport", n: a[2], k: "air"});
    for (const z of I.industrial || []) { const c = z[2]; let sx = 0, sy = 0; for (let n = 0; n < c.length; n += 2) { sx += c[n]; sy += c[n + 1]; } const m = c.length / 2;
      pts.push({x: g(sx / m, sy / m), t: "zone industrielle", n: `${z[1] ? z[1] + " " : ""}(${String(z[0]).replace(".", ",")} km²)`, k: "zone"}); }
    const rails = (I.rail || []).map(l => { const p = new Path2D(); for (let n = 0; n < l.length; n += 2) { const q = proj([l[n], l[n + 1]]); n ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]); } return p; });
    const zones = (I.industrial || []).map(z => { const p = new Path2D(), c = z[2]; for (let n = 0; n < c.length; n += 2) { const q = proj([c[n], c[n + 1]]); n ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]); } p.closePath(); return p; });
    INF = {pts, rails, zones, src: I.sources}; then(); };
  if (window.INFRA) return go();
  const sc = document.createElement("script"); sc.src = "infra.js"; sc.onload = go; document.head.appendChild(sc);
}
// infrastructures : dessinées seulement à partir d'un zoom sur une localité (INFRA_K) ; en vue d'ensemble, rien,
// même case cochée ; trait léger, étiquettes au zoom fort seulement (INFRA_LABEL_K)
// seuils exprimés en km par pixel (même rendu sur ordinateur et sur mobile, quel que soit le cadrage initial)
const INFRA_KMPX = 0.8;
const kmPerPx = k => (FR.grid.size / 1000) / (gAff[2] * k);
function drawInfra(k){
  if (!showInfra || !INF || kmPerPx(k) > INFRA_KMPX) return;
  const u = 1 / k, ink = C.dark ? "rgba(225,225,215," : "rgba(35,35,35,";
  ctx.save();
  // voies ferrées : trait gris fin et traverses espacées, discrètes
  ctx.strokeStyle = ink + "0.4)"; ctx.lineWidth = 0.8 * u; for (const p of INF.rails) ctx.stroke(p);
  ctx.lineWidth = 2 * u; ctx.setLineDash([0.6 * u, 5 * u]); for (const p of INF.rails) ctx.stroke(p); ctx.setLineDash([]);
  ctx.strokeStyle = ink + "0.45)"; ctx.lineWidth = 0.7 * u; ctx.setLineDash([2.5 * u, 2 * u]); for (const p of INF.zones) ctx.stroke(p); ctx.setLineDash([]);
  ctx.fillStyle = ink + "0.95)"; ctx.strokeStyle = ink + "0.95)"; ctx.lineWidth = 1.3 * u;
  for (const p of INF.pts) { if (p.k === "zone") continue;
    const q = [gAff[0] + p.x[0] * gAff[2], gAff[1] + p.x[1] * gAff[2]], r = 3.2 * u;
    ctx.fillStyle = C.bg; ctx.beginPath(); ctx.arc(q[0], q[1], r * 1.5, 0, 7); ctx.fill(); ctx.fillStyle = ink + "0.95)";   // pastille claire
    if (p.k === "centrale") { ctx.beginPath(); ctx.moveTo(q[0] + r * 0.2, q[1] - r); ctx.lineTo(q[0] - r * 0.6, q[1] + r * 0.15); ctx.lineTo(q[0], q[1] + r * 0.15); ctx.lineTo(q[0] - r * 0.2, q[1] + r); ctx.lineTo(q[0] + r * 0.6, q[1] - r * 0.15); ctx.lineTo(q[0], q[1] - r * 0.15); ctx.closePath(); ctx.fill(); }   // éclair
    else if (p.k === "air") { ctx.beginPath(); ctx.moveTo(q[0], q[1] - r); ctx.lineTo(q[0], q[1] + r); ctx.moveTo(q[0] - r, q[1]); ctx.lineTo(q[0] + r, q[1]); ctx.moveTo(q[0] - r * 0.5, q[1] + r * 0.8); ctx.lineTo(q[0] + r * 0.5, q[1] + r * 0.8); ctx.stroke(); }   // avion
    else if (p.k === "gare") { ctx.fillRect(q[0] - r * 0.8, q[1] - r * 0.5, r * 1.6, r); ctx.beginPath(); ctx.arc(q[0] - r * 0.45, q[1] + r * 0.7, r * 0.25, 0, 7); ctx.arc(q[0] + r * 0.45, q[1] + r * 0.7, r * 0.25, 0, 7); ctx.fill(); }   // wagon
    else { ctx.fillRect(q[0] - r * 0.8, q[1] - r * 0.2, r * 1.6, r); ctx.fillRect(q[0] - r * 0.8, q[1] - r, r * 0.45, r * 0.8); ctx.fillRect(q[0] + r * 0.1, q[1] - r * 0.7, r * 0.45, r * 0.5); }   // usine
    // pas d'étiquette sur la carte : les noms des sources sont en langues mêlées et masquaient les localités ; nom au survol
  }
  ctx.restore();
}
// infrastructure la plus proche d'une maille (survol), à moins de 6 km
function nearInfra(j, i){
  if (!INF) return null;
  let best = null, bd = 6 / (FR.grid.size / 1000);
  for (const p of INF.pts) { const d = Math.hypot(p.x[0] - i - 0.5, p.x[1] - j - 0.5); if (d < bd) { bd = d; best = p; } }
  return best ? {label: _t(best.t) + " " + best.n, d: Math.max(1, Math.round(bd * FR.grid.size / 1000))} : null;
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
// « combats derrière la ligne » : secteurs où l'activité de combat se concentre d'un côté de la ligne 50 % (asymétrie
// ≥ 0,2), hachures horizontales du côté concerné ; indicateur sans direction
let asymFor = -2, asymPat = null, showAsym = false;
function buildAsym(fi){
  if (asymFor === fi) return;
  asymFor = fi; asymPat = null;
  const F = viewFields("asym"); if (!F || !F[fi]) return;
  const S = 4, cv = document.createElement("canvas"); cv.width = GX * S; cv.height = GY * S;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX * S, GY * S), d = img.data, q = F[fi];
  for (let u = 0; u < UCELLS.length; u++) {
    if (q[u] < 13) continue;
    const c = UCELLS[u], x0 = (c % GX) * S, y0 = Math.floor(c / GX) * S, a = Math.min(1, 0.6 + q[u] / 63), [r, g, b] = C.dark ? [195, 155, 240] : [115, 55, 170];   // violet : distinct de l'ambre (P(mouvement)), du gris et du rouge
    for (let dy = 0; dy < S; dy++) { if ((y0 + dy) % 4 > 1) continue;
      for (let dx = 0; dx < S; dx++) { if ((x0 + dx) % 4 > 1) continue; const o = ((y0 + dy) * GX * S + x0 + dx) * 4; d[o] = r; d[o+1] = g; d[o+2] = b; d[o+3] = Math.round(240 * a); } }
  }
  cx.putImageData(img, 0, 0); asymPat = cv;
}
// « secteurs susceptibles de bouger » : P(la ligne bouge d'au moins 0,5 km d'ici t+2) par tronçon de 24 km, sans sens,
// liseré ambre côté ukrainien, mailles à ≤ 3 km de la ligne (q = 1 + P × 62), opacité croissante avec P
let pmFor = -2, pmImg = null, showPm = !!(document.getElementById("lyrPm") && document.getElementById("lyrPm").checked);
function buildPm(fi, k){
  // liseré AMBRE du côté ukrainien de la ligne seulement (distinct des hachures grises de contact, du rouge et du bleu) ;
  // élargi à l'affichage en vue d'ensemble pour rester visible ; opacité selon P
  const r = k < 1.6 ? 5 : k < 3 ? 2 : 1, key = fi + ":" + r + ":" + fieldName();
  if (pmFor === key) return;
  pmFor = key; pmImg = null;
  const F = viewFields("pmove"), g = fullField(fieldName(), fi); if (!F || !F[fi] || !g) return;
  const cv = document.createElement("canvas"); cv.width = GX; cv.height = GY;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX, GY), d = img.data, q = F[fi];
  for (let u = 0; u < UCELLS.length; u++) {
    if (!q[u]) continue;
    const p = (q[u] - 1) / 62, a = Math.max(0, Math.min(1, (p - 0.1) / 0.4));   // sous 10 % : rien (taux de base 13 %)
    if (a <= 0) continue;
    const c0 = UCELLS[u], i0 = c0 % GX, j0 = Math.floor(c0 / GX), al = Math.round(255 * (0.2 + 0.75 * a));
    for (let dj = -r; dj <= r; dj++) for (let di = -r; di <= r; di++) {
      if (di * di + dj * dj > r * r || i0 + di < 0 || i0 + di >= GX || j0 + dj < 0 || j0 + dj >= GY) continue;
      const c = (j0 + dj) * GX + i0 + di; if (!(g[c] < 0.5) || cellPlace[c] < 0) continue;
      const o = c * 4; if (d[o + 3] < al) { d[o] = 232; d[o+1] = 162; d[o+2] = 0; d[o+3] = al; } }
  }
  cx.putImageData(img, 0, 0); pmImg = cv;
}
// signaux Telegram (indicateur affiché, pas dans le calcul) : par tronçon de 24 km et par mois, mentions d'attaques et de
// présence ukrainiennes signalées par les sources russes (losange bleu, à gauche) et russes signalées par les sources
// ukrainiennes (losange rouge, à droite) ; taille et opacité selon le nombre ; données figées (telegram_troncons.js)
let showTg = false, TG = null, tgHits = [];
// signaux gardés seulement près du front (ligne à ≤ 30 km du tronçon) ; « traversant » si le tronçon contient du terrain
// tenu par le camp ADVERSE de celui dont on signale l'activité (activité ukrainienne là où la Russie tient, et inversement),
// sinon « même côté » (confirmation de contrôle) ; renvoie null (loin du front), "cross" ou "same"
function tgNearCross(fi, ij, key){
  const g = fullField(fieldName(), fi); if (!g) return null;
  let cross = false, ruSeen = false, uaSeen = false; const R = 15;   // 15 mailles = 30 km autour du tronçon
  for (let j = ij[1] - R; j < ij[1] + TG.tileCells + R; j++) for (let i = ij[0] - R; i < ij[0] + TG.tileCells + R; i++) {
    if (i < 0 || j < 0 || i >= GX || j >= GY) continue; const c = j * GX + i; if (cellPlace[c] === -1) continue;
    const ru = g[c] > 0.5; if (ru) ruSeen = true; else uaSeen = true;
    if (i >= ij[0] && i < ij[0] + TG.tileCells && j >= ij[1] && j < ij[1] + TG.tileCells && (key === "ru" ? ru : !ru)) cross = true; }
  return ruSeen && uaSeen ? (cross ? "cross" : "same") : null;
}
function loadTg(then){ if (window.TG_TRONCONS) { TG = window.TG_TRONCONS; return then && then(); }
  const sc = document.createElement("script"); sc.src = "telegram_troncons.js?v=" + (FRONT_V.tg || ""); sc.onload = () => { TG = window.TG_TRONCONS; if (then) then(); }; document.head.appendChild(sc); }
function drawTg(k){
  tgHits = []; if (!TG) return;
  const fi = frontIdx(); if (fi < 0) return; const m = FR.months[fi].date.slice(0, 7);
  ctx.save(); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  for (const [key, col, side] of [["ru", C.uactl, -1], ["ua", C.front, 1]]) {
    for (const [mm, t, a, p] of TG[key]) { if (mm !== m) continue;
      const ij = TG.tiles[t]; if (!ij) continue;
      const gx = ij[0] + TG.tileCells / 2, gy = ij[1] + TG.tileCells / 2, n = a + p;
      const kind = tgNearCross(fi, ij, key); if (!kind) continue;
      const x = (gAff[0] + gx * gAff[2]) * k + tr.x + side * 5, y = (gAff[1] + gy * gAff[2]) * k + tr.y;
      if (x < -10 || x > W + 10 || y < -10 || y > H + 10) continue;
      const r = Math.min(9, 3 + 1.6 * Math.sqrt(n));
      ctx.globalAlpha = Math.min(0.9, 0.35 + 0.12 * Math.sqrt(n)); ctx.fillStyle = col; ctx.strokeStyle = C.bg; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, y - r); ctx.lineTo(x + r, y); ctx.lineTo(x, y + r); ctx.lineTo(x - r, y); ctx.closePath();
      if (kind === "cross") { ctx.globalAlpha = Math.min(1, 0.6 + 0.1 * Math.sqrt(n)); ctx.fill(); ctx.strokeStyle = C.ink; ctx.lineWidth = 1.4; ctx.stroke(); }   // traverse la ligne : plein, cerné
      else { ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke(); }   // même côté : creux, discret
      tgHits.push({x, y, r, key, a, p, kind});
    } }
  ctx.restore(); ctx.globalAlpha = 1;
}
// impacts détectés par Sentinel-2 (indicateur affiché, données figées) : par tronçon de 20 km et par mois, z local des
// impacts de chaque côté de la ligne (pointillé sombre, le côté se lit par la position ; détail par camp au survol), z ≥ 1
let showImp = false, IMP = null, impHits = [], impOff = null;
function loadImp(then){ if (window.IMPACTS) { IMP = window.IMPACTS; return then && then(); }
  const sc = document.createElement("script"); sc.src = "impacts_troncons.js?v=0"; sc.onload = () => { IMP = window.IMPACTS; if (then) then(); }; document.head.appendChild(sc); }
let impFor = "", impImg = null;
function drawImp(k){
  impHits = []; if (!IMP) return;
  const fi = frontIdx(); if (fi < 0) return; const m = FR.months[fi].date.slice(0, 7);
  const PMF = viewFields("pmove"), g = fullField(fieldName(), fi); if (!PMF || !PMF[fi] || !g) { ensureView("pmove", () => draw()); return; }
  const r = k < 1.6 ? 4 : k < 3 ? 2 : 1, key = fi + ":" + r + ":" + fieldName();
  if (impFor !== key) {
    impFor = key; impImg = document.createElement("canvas"); impImg.width = GX; impImg.height = GY;
    const cx = impImg.getContext("2d"), img = cx.createImageData(GX, GY), d = img.data, q = PMF[fi], z = new Map();
    for (const [mm, gx, gy, zr, zu] of IMP.rows) if (mm === m) z.set(Math.floor(gy / IMP.secCells) * 1000 + Math.floor(gx / IMP.secCells), [zr, zu]);
    for (let u = 0; u < UCELLS.length; u++) { if (!q[u]) continue;
      const c0 = UCELLS[u], i0 = c0 % GX, j0 = Math.floor(c0 / GX), zz = z.get(Math.floor(j0 / IMP.secCells) * 1000 + Math.floor(i0 / IMP.secCells)); if (!zz) continue;
      for (let dj = -r; dj <= r; dj++) for (let di = -r; di <= r; di++) {
        const ii = i0 + di, jj = j0 + dj; if (ii < 0 || jj < 0 || ii >= GX || jj >= GY || di * di + dj * dj > r * r) continue;
        const c = jj * GX + ii, ru = g[c] > 0.5, v = ru ? zz[0] : zz[1]; if (v == null || v < 1 || cellPlace[c] === -1) continue;
        const a = Math.round(255 * Math.min(0.85, 0.2 + 0.1 * v)), o = c * 4;
        if (d[o + 3] < a) { d[o] = d[o + 1] = d[o + 2] = 0; d[o + 3] = a; } } }
    cx.putImageData(img, 0, 0);
  }
  // rendu en pointillé sombre (texture « cratères ») : le masque d'intensité découpe une trame de points en espace écran,
  // pour ne pas confondre avec les feux (carrés brique), la zone grise (hachures) ni les secteurs qui bougent (ambre)
  const cw = Math.round(W * dpr), ch = Math.round(H * dpr);
  if (!impOff || impOff.width !== cw || impOff.height !== ch) { impOff = document.createElement("canvas"); impOff.width = cw; impOff.height = ch; }
  const ox = impOff.getContext("2d"); ox.setTransform(1, 0, 0, 1, 0, 0); ox.globalCompositeOperation = "source-over"; ox.clearRect(0, 0, cw, ch);
  ox.setTransform(dpr, 0, 0, dpr, 0, 0); ox.translate(tr.x, tr.y); ox.scale(k, k); ox.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]);
  ox.save(); ox.clip(domainPath); ox.imageSmoothingEnabled = true; ox.drawImage(impImg, 0, 0, GX, GY); ox.restore();
  const tile = document.createElement("canvas"), ts = Math.round(6 * dpr); tile.width = tile.height = ts;
  const tc = tile.getContext("2d"); tc.fillStyle = C.dark ? "#f2efe6" : "#161616";
  tc.beginPath(); tc.arc(ts * 0.25, ts * 0.25, 1.25 * dpr, 0, 7); tc.arc(ts * 0.75, ts * 0.75, 1.25 * dpr, 0, 7); tc.fill();
  ox.setTransform(1, 0, 0, 1, 0, 0); ox.globalCompositeOperation = "source-in"; ox.fillStyle = ox.createPattern(tile, "repeat"); ox.fillRect(0, 0, cw, ch);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(impOff, 0, 0); ctx.restore();
  for (const [mm, gx, gy, zr, zu] of IMP.rows) if (mm === m && ((zr || 0) >= 1 || (zu || 0) >= 1))
    impHits.push({x: (gAff[0] + gx * gAff[2]) * k + tr.x, y: (gAff[1] + gy * gAff[2]) * k + tr.y, zr, zu});
}
// liseré P(mouvement) dessiné AU-DESSUS des feux et des hachures, avec un fin contour sombre pour le détacher
function drawPm(k){
  const fp = frontIdx(); if (fp < 0) return;
  buildPm(fp, k); if (!pmImg) return;
  ctx.save(); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.translate(tr.x, tr.y); ctx.scale(k, k);
  ctx.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]); ctx.clip(domainPath);
  ctx.imageSmoothingEnabled = true; ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  ctx.filter = C.dark ? "drop-shadow(0 0 0.6px rgba(0,0,0,.95))" : "drop-shadow(0 0 0.6px rgba(60,35,0,.9))";
  ctx.drawImage(pmImg, 0, 0, GX, GY);
  ctx.restore();
}
// destruction cumulée (radar Sentinel-1) : part du bâti modifié depuis 2021, mailles de 2 km, couche d'information
let destrFor = -1, destrImg = null, showDestr = false;
function buildDestr(fi){
  if (destrFor === fi) return;
  destrFor = fi; destrImg = null;
  const F = viewFields("destr"); if (!F || !F[fi]) return;
  const cv = document.createElement("canvas"); cv.width = GX; cv.height = GY;
  const cx = cv.getContext("2d"), img = cx.createImageData(GX, GY), d = img.data, q = F[fi];
  for (let u = 0; u < UCELLS.length; u++) {
    if (q[u] < 8) continue;  // < 6 % du bâti modifié : rien
    const c = UCELLS[u], o = c * 4, v = Math.min(1, q[u] / 63);
    d[o] = 40; d[o+1] = 40; d[o+2] = 40; d[o+3] = Math.round(255 * (0.25 + 0.65 * v));
  }
  cx.putImageData(img, 0, 0); destrImg = cv;
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
  { const fc = frontIdx(); if (showContact && fc >= 0) { buildContact(fc); if (contactPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.7; ctx.drawImage(contactPat, 0, 0, GX, GY); ctx.restore(); } } }
  if ((showLines || showUnc) && isoPaths.length) {
    ctx.save(); ctx.clip(domainPath);
    ctx.strokeStyle = C.front; ctx.globalAlpha = 0.9; ctx.setLineDash([4*u, 3*u]); ctx.lineWidth = 0.9 * u;
    if (showUnc) { ctx.stroke(isoPaths[0]); ctx.stroke(isoPaths[2]); }
    ctx.setLineDash([]); ctx.lineWidth = 1.8 * u; ctx.globalAlpha = 1; if (showLines) ctx.stroke(isoPaths[1]);
    ctx.restore();
    if (isoBand) { ctx.save(); ctx.clip(bandPath); ctx.strokeStyle = C.uactl; ctx.lineWidth = 1.8 * u; ctx.stroke(isoBand); ctx.restore(); }
  }
  const fi = frontIdx();
  if (fvar && fi >= 0) { buildDiff(fi); if (diffPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.8; ctx.drawImage(diffPat, 0, 0, GX, GY); ctx.restore(); } }
  if (showAsym && fi >= 0) { buildAsym(fi); if (asymPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.85; ctx.imageSmoothingEnabled = false; ctx.drawImage(asymPat, 0, 0, GX, GY); ctx.restore(); } }
  if (showDestr && fi >= 0) { buildDestr(fi); if (destrImg) { ctx.save(); ctx.clip(domainPath); ctx.imageSmoothingEnabled = false; ctx.drawImage(destrImg, 0, 0, GX, GY); ctx.restore(); } }
  if (showCombat && fi >= 0) { buildCombat(fi); if (combatPat) { ctx.save(); ctx.clip(domainPath); ctx.globalAlpha = 0.85; ctx.drawImage(combatPat, 0, 0, GX, GY); ctx.restore(); } }
  if (fi >= 0) for (const src of ["dsm", "isw", "wiki"]) {
    if (!rawOn[src]) continue;
    const pth = rawPath(src, fi); if (!pth) continue;
    ctx.strokeStyle = RAWCOL[src]; ctx.globalAlpha = 0.95; ctx.lineWidth = 1.4 * u; ctx.setLineDash(src === "isw" ? [5*u, 2*u] : src === "wiki" ? [1.5*u, 2*u] : []);
    ctx.stroke(pth);
  }
  ctx.restore();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  // annonces officielles (couche dédiée, décochée) : seulement celles des 30 jours précédant le pas affiché, estompées
  // de 30 à 60 jours, jamais plus anciennes ; la plus récente par localité ; cliquables (claimHits)
  claimHits = [];
  if (showClaims && fi >= 0 && FR.claimLog) {
    const fd = Date.parse(FR.months[fi].date);
    for (const [src, col, up] of [["modru", C.front, true], ["gsua", C.cool, false]]) {
      for (const [kk, list] of Object.entries(FR.claimLog[src] || {})) {
        const pl = FR.claimPlaces[kk]; if (!pl) continue;
        let best = null;
        for (const e of list) { const t = Date.parse(e[0].slice(0, 4) + "-" + e[0].slice(4, 6) + "-" + e[0].slice(6)), age = (fd - t) / 864e5;
          if (age >= 0 && age <= 60 && (!best || age < best.age)) best = {age, e}; }
        if (!best) continue;
        const q = proj([pl[0], pl[1]]), sz = (best.age <= 30 ? 4.5 : 3.2) / k;
        ctx.fillStyle = col; ctx.globalAlpha = best.age <= 30 ? 0.95 : 0.35;
        ctx.beginPath(); if (up) { ctx.moveTo(q[0], q[1] - sz); ctx.lineTo(q[0] + sz, q[1] + sz*0.8); ctx.lineTo(q[0] - sz, q[1] + sz*0.8); } else { ctx.moveTo(q[0], q[1] + sz); ctx.lineTo(q[0] + sz, q[1] - sz*0.8); ctx.lineTo(q[0] - sz, q[1] - sz*0.8); }
        ctx.closePath(); ctx.fill();
        claimHits.push({x: q[0] * k + tr.x, y: q[1] * k + tr.y, src, name: pl[2], e: best.e, age: best.age});
      }
    }
  }
  ctx.globalAlpha = 1;
}
// ---------- couches UCDP et arcs de missiles ----------
const UC = O.ucdp.pts, NU = UC.length / 5;
// mois UCDP affiché : le mois courant s'il est publié, sinon le dernier mois publié (au plus 2 mois avant), atténué
const ucdpLastIdx = D.months.indexOf((O.ucdp.last || "").slice(0, 7));
const ucdpMonth = () => (D.months[cur] > (O.ucdp.last || "") && ucdpLastIdx >= 0 && cur - ucdpLastIdx <= 2) ? ucdpLastIdx : cur;
function drawUcdp(k){
  const um = ucdpMonth(), fade = um === cur ? 1 : 0.6;
  if (um === cur && cur > cutIdx) return;
  ctx.fillStyle = C.ucdp;
  for (let j = 0; j < NU; j++) {
    if (UC[j*5+2] !== um) continue;
    const r = cellRect(UC[j*5], UC[j*5+1]);
    ctx.globalAlpha = fade * Math.min(0.85, 0.25 + 0.12 * Math.sqrt(UC[j*5+3]));
    ctx.fillRect(r[0], r[1], r[2], r[3]);
  }
  ctx.globalAlpha = 1;
}
const regionCentroid = {};
for (const f of G.regions) if (f.properties.id) regionCentroid[f.properties.id] = d3.geoCentroid(f);
// missiles et drones (P. Ivaniuk, CC BY-NC-SA 4.0) : choroplèthe des oblasts visés (volume du mois, échelle
// séquentielle) et zones de lancement (cercles ∝ √n, lieux identiques regroupés) ; les arcs ne sont dessinés que pour
// la zone ou l'oblast survolé (ses flux seulement), avec une tête côté cible. Un lancement part de Russie, des territoires
// occupés, de la mer Noire, de la mer d'Azov, de la Caspienne ou de Biélorussie (contrôlé par check_arrows.py).
let arcFocus = null, arcCache = {m: -1};
const regionPath = {}, regionFeat = {};
function arcMonth(){
  if (arcCache.m === cur) return arcCache;
  const pl = O.launch.places, zones = new Map(), regs = {};
  for (const [mi, li, tid, n] of O.launch.arcs) {
    if (mi !== cur || !regionCentroid[tid]) continue;
    const key = pl[li][1] + "," + pl[li][2];
    let z = zones.get(key); if (!z) zones.set(key, z = {key, lon: pl[li][1], lat: pl[li][2], names: [], n: 0, flows: {}});
    if (!z.names.includes(pl[li][0])) z.names.push(pl[li][0]);
    z.n += n; z.flows[tid] = (z.flows[tid] || 0) + n; regs[tid] = (regs[tid] || 0) + n;
  }
  const zs = [...zones.values()], mz = Math.max(1, ...zs.map(z => z.n)), mr = Math.max(1, ...Object.values(regs));
  return arcCache = {m: cur, zones: zs, regs, mz, mr};
}
const zoneR = (z, A) => 3.5 + 10 * Math.sqrt(z.n / A.mz);
function drawArcs(k){
  if (cur > cutIdx) return;
  drawStrikesRu(k);
  const A = arcMonth();
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  // choroplèthe des oblasts visés
  const regs = Object.assign({}, A.regs);
  if (regs["UA-32"] && !regs["UA-30"]) regs["UA-30"] = regs["UA-32"];   // Kyiv ville comptée avec l'oblast dans les rapports
  for (const [tid, n] of Object.entries(regs)) { const p = regionPath[tid]; if (!p) continue;
    ctx.globalAlpha = 0.1 + 0.5 * Math.sqrt(n / A.mr); ctx.fillStyle = C.arc; ctx.fill(p);
    if (arcFocus && arcFocus.kind === "region" && arcFocus.id === tid) { ctx.globalAlpha = 1; ctx.strokeStyle = C.arc; ctx.lineWidth = 2 / k; ctx.stroke(p); } }
  // flux de la zone ou de l'oblast survolé
  if (arcFocus) {
    const flows = arcFocus.kind === "zone" ? Object.entries((A.zones.find(z => z.key === arcFocus.id) || {flows: {}}).flows).map(([t, n]) => [arcFocus.id, t, n])
      : A.zones.filter(z => z.flows[arcFocus.id]).map(z => [z.key, arcFocus.id, z.flows[arcFocus.id]]);
    const mf = Math.max(1, ...flows.map(f => f[2]));
    for (const [zk, tid, n] of flows) {
      const z = A.zones.find(q => q.key === zk), a = proj([z.lon, z.lat]), b = proj(regionCentroid[tid]); if (!a || !b) continue;
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1], cx = mx - dy * 0.15, cy = my + dx * 0.15;
      const w = (1 + 4 * Math.sqrt(n / mf)) / k;
      ctx.globalAlpha = 0.85; ctx.strokeStyle = C.arc; ctx.lineWidth = w;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.quadraticCurveTo(cx, cy, b[0], b[1]); ctx.stroke();
      // tête côté cible, dans l'axe de la fin de la courbe
      const tx = b[0] - cx, ty = b[1] - cy, tl = Math.hypot(tx, ty) || 1, ux = tx / tl, uy = ty / tl, hs = Math.min(6, 2.5 + w * k) / k;
      ctx.globalAlpha = 1; ctx.fillStyle = C.arc; ctx.beginPath(); ctx.moveTo(b[0], b[1]);
      ctx.lineTo(b[0] - ux * hs * 1.8 - uy * hs, b[1] - uy * hs * 1.8 + ux * hs); ctx.lineTo(b[0] - ux * hs * 1.8 + uy * hs, b[1] - uy * hs * 1.8 - ux * hs); ctx.closePath(); ctx.fill();
    }
  }
  // zones de lancement
  for (const z of A.zones) { const a = proj([z.lon, z.lat]); if (!a) continue;
    const r = zoneR(z, A) / k, on = arcFocus && arcFocus.kind === "zone" && arcFocus.id === z.key;
    ctx.globalAlpha = on ? 1 : 0.8; ctx.fillStyle = C.arc; ctx.strokeStyle = C.bg; ctx.lineWidth = 1.2 / k;
    ctx.beginPath(); ctx.arc(a[0], a[1], r, 0, 7); ctx.fill(); ctx.stroke(); }
  ctx.globalAlpha = 1;
}
// diagnostic (headless) : zones de lancement du mois affiché, en pixels de la carte
window.__uafArcZones = () => arcMonth().zones.map(z => { const a = proj([z.lon, z.lat]); return {name: z.names.join(", "), x: a[0] * tr.k + tr.x, y: a[1] * tr.k + tr.y, n: Math.round(z.n)}; }).sort((p, q) => q.n - p.n);
// sens Ukraine → Russie : frappes ukrainiennes SIGNALÉES par région et par mois (strikes_ru.js, build_strikes_ru.py) :
// jours où la région est citée dans les bilans d'interceptions du MoD RF (annonces d'une partie), mentions des autorités
// régionales (Belgorod, Koursk, Briansk), feux anormaux près de sites industriels ; régions russes : regions_ru.js
// (Natural Earth) ; territoires occupés : régions de geo.js. Choroplèthe olive, aucune zone de lancement.
let SRU = null, sruLoading = false, sruCache = {m: -1};
const sruPath = {}, sruFeat = {};
function loadStrikesRu(then){
  if (SRU) return then && then();
  if (sruLoading) return; sruLoading = true;
  let n = 0; const done = () => { if (++n < 2) return; SRU = window.STRIKES_RU || null; sruLoading = false;
    const gp = d3.geoPath(proj);
    for (const f of (window.REGIONS_RU || {features: []}).features) if (f.geometry) { sruPath[f.properties.id] = new Path2D(gp(f) || ""); sruFeat[f.properties.id] = f; }
    for (const id of ["UA-43", "UA-40", "UA-23", "UA-65", "UA-14", "UA-09"]) if (regionPath[id]) { sruPath[id] = regionPath[id]; sruFeat[id] = regionFeat[id]; }
    if (then) then(); };
  for (const src of ["regions_ru.js", "strikes_ru.js"]) { const sc = document.createElement("script"); sc.src = src + "?v=" + (FRONT_V.strikes || ""); sc.onload = done; document.head.appendChild(sc); }
}
function sruMonth(){
  if (sruCache.m === cur || !SRU) return sruCache;
  const R = {};
  for (const key of ["mod", "modN", "gov", "fires"]) for (const [mi, rid, n] of SRU[key] || []) if (mi === cur) { (R[rid] = R[rid] || {mod: 0, modN: 0, gov: 0, fires: 0})[key] += n; }
  const mx = Math.max(1, ...Object.values(R).map(v => v.mod));
  return sruCache = {m: cur, R, mx};
}
const SRU_COL = () => C.dark ? "#b5c25a" : "#6f7f1f";
function drawStrikesRu(k){
  if (!SRU || cur > cutIdx) return;
  const S = sruMonth();
  const R = Object.assign({}, S.R);
  if (R["UA-43"] && !R["UA-40"]) R["UA-40"] = R["UA-43"];   // Sébastopol citée avec la Crimée
  for (const [rid, v] of Object.entries(R)) { const p = sruPath[rid]; if (!p || !(v.mod || v.gov || v.fires)) continue;
    ctx.globalAlpha = 0.12 + 0.5 * Math.sqrt((v.mod || 0.5) / S.mx); ctx.fillStyle = SRU_COL(); ctx.fill(p);
    if (arcFocus && arcFocus.kind === "sru" && arcFocus.id === rid) { ctx.globalAlpha = 1; ctx.strokeStyle = SRU_COL(); ctx.lineWidth = 2 / k; ctx.stroke(p); } }
  ctx.globalAlpha = 1;
}
function sruHover(ll){
  if (!SRU || cur > cutIdx) return null;
  const S = sruMonth();
  for (const [rid, v] of Object.entries(S.R)) if (sruFeat[rid] && d3.geoContains(sruFeat[rid], ll)) {
    const nm = topoC(SRU.names[rid] || rid);
    return {id: rid, text: _t("frappes ukrainiennes signalées : {r} ce mois-ci", {r: nm}) +
      `<div>${_t("bilans d'interceptions du MoD RF (annonces) : région citée {d} jour(s){n}", {d: fmt(v.mod), n: v.modN ? _t(", {x} drones annoncés", {x: fmt(v.modN)}) : ""})}</div>` +
      (v.gov ? `<div>${_t("autorités régionales : {g} mention(s) de frappes", {g: fmt(v.gov)})}</div>` : "") +
      (v.fires ? `<div>${_t("feux anormaux près de sites industriels ou énergétiques : {f}", {f: fmt(v.fires)})}</div>` : "")};
  }
  return null;
}
// survol de la couche : zone de lancement (cercle) ou oblast visé ; renvoie le texte de l'info-bulle ou null
function arcHover(mx, my){
  if (!showArcs || cur > cutIdx) return null;
  const A = arcMonth(), k = tr.k, gx = (mx - tr.x) / k, gy = (my - tr.y) / k;
  let f = null;
  for (const z of A.zones) { const a = proj([z.lon, z.lat]); if (a && Math.hypot((a[0] - gx) * k, (a[1] - gy) * k) <= zoneR(z, A) + 3) { f = {kind: "zone", id: z.key, z}; break; } }
  if (!f) { const ll = proj.invert([gx, gy]);
    const ids = Object.keys(A.regs); if (A.regs["UA-32"] && !A.regs["UA-30"]) ids.unshift("UA-30");
    for (const tid of ids) if (regionFeat[tid] && d3.geoContains(regionFeat[tid], ll)) { f = {kind: "region", id: A.regs[tid] ? tid : "UA-32"}; break; } }
  let sh = null;
  if (!f) { sh = sruHover(proj.invert([gx, gy])); if (sh) f = {kind: "sru", id: sh.id}; }
  const key = f ? f.kind + f.id : null, old = arcFocus ? arcFocus.kind + arcFocus.id : null;
  arcFocus = f ? {kind: f.kind, id: f.id} : null;
  if (key !== old) draw();
  if (!f) return null;
  if (f.kind === "sru") return sh.text;
  if (f.kind === "zone") return _t("zone de lancement : {z} · {n} lancements vers des cibles précisées ce mois-ci", {z: f.z.names.join(", "), n: fmt(Math.round(f.z.n))});
  const nm = (regionFeat[f.id].properties || {}).n || f.id;
  return _t("oblast visé : {o} · {n} lancements (cibles précisées) ce mois-ci", {o: topoC(nm), n: fmt(Math.round(A.regs[f.id]))});
}
// mobile : cadrage d'ouverture sur le front (est et sud, de Kharkiv à Kherson)
const zoneM = {type:"MultiPoint", coordinates:[[32.2,46.1],[39.6,46.1],[39.6,50.5],[32.2,50.5]]};
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
  for (const f of G.regions) if (f.properties.id) { regionPath[f.properties.id] = mk([f]); regionFeat[f.properties.id] = f; }
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

// ---------- relief (ombrage du MNT Copernicus GLO-90, statique ; case « relief » des calques) ----------
// Images en niveaux de gris dans la projection de la grille (255 = neutre), dessinées en « multiply ».
// Niveaux (relief/relief.js) : z0 1 km/px et z1 400 m/px (images entières), z2 160 m/px (tuiles de 512 px, zone du
// front) ; chargement paresseux : rien avant que la case soit cochée, z1 et z2 seulement au zoom.
let showRelief = !!(document.getElementById("lyrRelief") && document.getElementById("lyrRelief").checked), RLF = null, rlfLoading = false;
const rlfImgs = {};
let rlfCont = null, rlfContLoading = false;
// « over » (défaut) : au-dessus de la densité en multiply léger, visible des deux côtés de la ligne ; « under » (?relief=under) :
// sous la densité, masqué là où la densité russe est opaque
// relief : en clair, teinte HYPSOMÉTRIQUE (vert pâle selon l'altitude, eaux bleu pâle, ombrage discret), opaque, dessinée
// à la place du fond terrestre, SOUS les routes, la densité et les étiquettes ; en sombre (non réglé pour l'hypsométrie)
// ou avec ?relief=ombrage : ombrage seul, au-dessus de la densité en multiply léger (?relief=under : sous la densité)
const RQ = new URLSearchParams(location.search).get("relief"), RELIEF_KIND = () => (C.dark || RQ === "ombrage" || RQ === "under") ? "shade" : "hypso";
let RELIEF_MODE = "under";
// version des images du relief : à incrémenter à chaque régénération (sinon le navigateur garde d'anciennes tuiles)
const RLF_V = "3";
function loadRelief(then){
  if (RLF) return then && then();
  if (rlfLoading) return; rlfLoading = true;
  const kind = RELIEF_KIND(); RELIEF_MODE = kind === "hypso" || RQ === "under" ? "under" : "over";
  const go = () => { RLF = (kind === "hypso" ? window.HYPSO : window.RELIEF) || null; rlfLoading = false; if (then) then(); };
  if (kind === "hypso" ? window.HYPSO : window.RELIEF) return go();
  const sc = document.createElement("script"); sc.src = (kind === "hypso" ? "relief/hypso.js" : "relief/relief.js") + "?v=" + RLF_V; sc.onload = go; document.head.appendChild(sc);
}
function rlfImg(src){
  let im = rlfImgs[src];
  if (!im) { im = new Image(); im.onload = () => draw(); im.src = "relief/" + src + "?v=" + RLF_V; rlfImgs[src] = im; }
  return im.complete && im.naturalWidth ? im : null;
}
function drawRelief(k){
  if (!showRelief || !RLF) return;
  const g = FR.grid, kmpx = kmPerPx(k), L = RLF.levels;
  ctx.save();
  ctx.transform(gAff[2], 0, 0, gAff[2], gAff[0], gAff[1]);          // unités : mailles de 2 km
  if (typeof domainPath !== "undefined" && domainPath) ctx.clip(domainPath);   // Ukraine et bande russe du modèle
  const hyp = RLF === window.HYPSO;
  ctx.globalCompositeOperation = hyp ? "source-over" : "multiply";
  if (hyp) ctx.filter = "blur(0.6px)";   // dégradé adouci : pas de liseré clair aux bords des vallées
  ctx.globalAlpha = hyp ? 1 : RELIEF_MODE === "over" ? 0.5 : 0.85;
  ctx.imageSmoothingEnabled = true;
  const z0 = rlfImg(L[0].file), z1 = kmpx < 0.9 ? rlfImg(L[1].file) : null;
  const full = z1 || z0, z2 = L[2], tiles = [];
  if (z2 && kmpx < 0.3) {
    // tuiles visibles seulement (écran en px CSS : x = tr.x + k·(gAff0 + gAff2·gx))
    const ox = (z2.x0 - g.x0) / g.size, oy = (g.y1 - z2.y1) / g.size, ts = z2.tile * z2.res / g.size;
    const gx0 = (-tr.x / k - gAff[0]) / gAff[2], gx1 = ((W - tr.x) / k - gAff[0]) / gAff[2];
    const gy0 = (-tr.y / k - gAff[1]) / gAff[2], gy1 = ((H - tr.y) / k - gAff[1]) / gAff[2];
    for (const [tx, ty, tw, th] of z2.tiles) {
      const x = ox + tx * ts, y = oy + ty * ts, w = tw * z2.res / g.size, h = th * z2.res / g.size;
      if (x > gx1 || x + w < gx0 || y > gy1 || y + h < gy0) continue;
      const im = rlfImg(z2.dir + tx + "_" + ty + ".webp");
      if (im) tiles.push([im, x, y, w, h]);
    }
  }
  if (full) {
    // le niveau entier est exclu sous les tuiles fines chargées (pas de double ombrage en multiply)
    if (tiles.length) { const cp = new Path2D(); cp.rect(0, 0, g.nx, g.ny); for (const t of tiles) cp.rect(t[1], t[2], t[3], t[4]); ctx.save(); ctx.clip(cp, "evenodd"); ctx.drawImage(full, 0, 0, g.nx, g.ny); ctx.restore(); }
    else ctx.drawImage(full, 0, 0, g.nx, g.ny);
  }
  for (const [im, x, y, w, h] of tiles) ctx.drawImage(im, x, y, w, h);
  window.__uafRelief = {kmpx: kmpx, level: z1 ? "z1" : "z0", tiles: tiles.length, contours: !!rlfCont};
  // pas de courbes de niveau : elles se lisaient comme des routes ou des limites (retour du 19/10)
  ctx.restore();
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
  if (RELIEF_MODE === "under") drawRelief(k);
  bg = showBase ? baseProject() : null;
  // routes : plus épaisses et plus sombres sur le relief, pour ressortir nettement
  if (bg) { const rl = showRelief && RLF === window.HYPSO; ctx.strokeStyle = rl ? "#5a4a35" : C.road; ctx.globalAlpha = rl ? 0.85 : 0.55; ctx.lineWidth = (rl ? 1.7 : 1.1) / k; ctx.stroke(bg.r1);
    if (k >= 2) { ctx.lineWidth = (rl ? 0.9 : 0.6) / k; ctx.globalAlpha = rl ? 0.65 : 0.4; ctx.stroke(bg.r2); } ctx.globalAlpha = 1; }
  drawFront(k);
  if (RELIEF_MODE === "over") drawRelief(k);
  ctx.lineWidth = 0.8/k; ctx.strokeStyle = C.water; ctx.stroke(paths.rivers);
  ctx.lineWidth = 0.5/k; ctx.strokeStyle = C.admin; ctx.stroke(paths.regions);
  ctx.lineWidth = 1.1/k; ctx.strokeStyle = C.border; ctx.stroke(paths.borders);

  drawInfra(k);
  if (showFire) { if (mode === "pts") drawPoints(k); else drawDensity(k); }
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
  if (showUcdp) drawUcdp(k);
  if (showLoss) drawLosses(k);
  if (showArcs) drawArcs(k);
  if (showTg) drawTg(k);
  if (showImp) drawImp(k);
  if (showPm) drawPm(k);   // au-dessus des feux et des combats (sinon l'ambre se perd dans les points de feux)
  if (showArrows) { drawArrows(k); drawEventArrows(k); }
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;

  // villes : en coordonnées écran, seuil selon le zoom
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const lim = W < 500 ? 4 + Math.log2(k)*2 : 4.5 + Math.log2(k)*2;
  ctx.font = "11px 'JetBrains Mono', monospace"; ctx.textBaseline = "middle";
  window.__uafLastLabels = [];
  const placed = [[0, 0, W < 560 ? 290 : 420, (W < 560 ? 62 : 70) + 15 * Math.max(0, layerNotes().length - 1)], [W-48, 0, W, 130]];
  for (const c of cities) {
    if (c.r > lim && !c.cap) continue;
    const x = c.x*k + tr.x, y = c.y*k + tr.y;
    if (x < -40 || x > W+40 || y < 0 || y > H) continue;
    const nm = topoC(c.n), w = ctx.measureText(nm).width, box = [x+5, y-7, x+7+w, y+7];
    window.__uafLastLabels.push(nm);
    if (placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
    placed.push(box);
    ctx.fillStyle = C.label; ctx.beginPath(); ctx.arc(x, y, c.cap ? 2.6 : 1.8, 0, 7); ctx.fill();
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.miterLimit = 2; ctx.strokeStyle = C["map-bg"]; ctx.globalAlpha = .75; ctx.strokeText(nm, x+6, y); ctx.globalAlpha = 1;
    ctx.fillText(nm, x+6, y);
  }
  if (!bg) return;
  // localités GeoNames selon le zoom : > 50 000 hab., puis > 10 000, puis > 1 000 (masquage glouton, population d'abord)
  const minPop = k >= 6 ? 1000 : k >= 3 ? 10000 : k >= 1.4 ? 50000 : Infinity;
  ctx.font = "10px 'JetBrains Mono', monospace";
  for (const c of bg.places) {
    if (c.pop < minPop) break;
    const x = c.x*k + tr.x, y = c.y*k + tr.y;
    if (x < -40 || x > W+40 || y < 0 || y > H) continue;
    const nm = topoP(c.i, c.n), w = ctx.measureText(nm).width, box = [x+4, y-6, x+6+w, y+6];
    window.__uafLastLabels.push(nm);
    if (placed.some(b => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
    placed.push(box);
    ctx.fillStyle = C.label; ctx.globalAlpha = .85; ctx.beginPath(); ctx.arc(x, y, 1.4, 0, 7); ctx.fill();
    ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.miterLimit = 2; ctx.strokeStyle = C["map-bg"]; ctx.globalAlpha = .7; ctx.strokeText(nm, x+5, y); ctx.globalAlpha = .85;
    ctx.fillText(nm, x+5, y); ctx.globalAlpha = 1;
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
// feux en excès cumulés, rendu LISSÉ : chaque maille de 0,1° devient une tache gaussienne (dégradé radial) centrée sur
// la maille, les recouvrements donnent un champ continu, sans carrés ; palette brique (hors de l'ambre de P(mouvement))
const denColor = v => d3.interpolateRgbBasis(C.dark ? ["#3a1208", "#8a2a14", "#ff5a4a", "#ffb0a0", "#fff0ea"] : ["#f1d3c9", "#d98c75", "#b0301c", "#7a1a0c", "#3a0a04"])(v);
function drawDensity(k){
  const acc = new Map();
  for (let m = 0; m <= cur; m++) { const c = D.cells[m]; for (let j = 0; j < c.length; j += 4) { const key = c[j]*10000 + c[j+1]; acc.set(key, (acc.get(key) || 0) + c[j+2] - c[j+3]); } }
  const now = new Set(); { const c = D.cells[cur]; for (let j = 0; j < c.length; j += 4) now.add(c[j]*10000 + c[j+1]); }
  const s = d3.scaleLog().domain([3, 400]).range([0.15, 1]).clamp(true);
  ctx.globalCompositeOperation = "source-over";
  for (const [key, v] of acc) {
    if (v < 1) continue;
    const i = Math.floor(key/10000), jj = key - i*10000, r = cellRect(i, jj), cx = r[0] + r[2] / 2, cy = r[1] + r[3] / 2, R = 0.85 * Math.hypot(r[2], r[3]);
    const col = d3.color(denColor(s(Math.max(3, v)))), a = 0.25 + 0.6 * s(Math.max(3, v));
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
    g.addColorStop(0, `rgba(${col.r},${col.g},${col.b},${a})`); g.addColorStop(0.55, `rgba(${col.r},${col.g},${col.b},${a * 0.55})`); g.addColorStop(1, `rgba(${col.r},${col.g},${col.b},0)`);
    ctx.globalAlpha = 1; ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.fill();
  }
  // mailles anormales du mois affiché : petit anneau, pas de carré
  ctx.globalAlpha = 0.9; ctx.lineWidth = 1/k; ctx.strokeStyle = C.ember;
  for (const key of now) { const i = Math.floor(key/10000), r = cellRect(i, key - i*10000); ctx.beginPath(); ctx.arc(r[0] + r[2] / 2, r[1] + r[3] / 2, Math.min(Math.abs(r[2]), Math.abs(r[3])) * 0.3, 0, 7); ctx.stroke(); }
  ctx.globalAlpha = 1;
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
const playLabel = () => _t("▶ lecture"), pauseLabel = () => _t("❚❚ pause");
function stop(){ if (timer) { clearInterval(timer); timer = null; } $("play").textContent = playLabel(); }
// lecture : pas (tous, mois, trimestre, année ; toujours calés sur le dernier mois) et vitesse réglables
function playSteps(){
  const g = $("gran") ? $("gran").value : "all", lastM = AX[AX.length - 1].m, every = {q: 3, y: 12}[g] || 1;
  const L = []; AX.forEach((a, n) => { if (g === "all" || (!a.d && (lastM - a.m) % every === 0)) L.push(n); });
  if (L[L.length - 1] !== AX.length - 1 && g !== "all") L.push(AX.length - 1);
  return L;
}
const playMs = () => 650 / (+($("speed") && $("speed").value) || 1);
let playTick = null;
$("play").textContent = playLabel();
$("play").onclick = () => {
  if (timer) return stop();
  const L = playSteps(); let s = L.findIndex(n => n > stepIdx) - 1; if (s < -1 || stepIdx >= AX.length - 1) s = -1;
  $("play").textContent = pauseLabel();
  playTick = () => { s++; if (s >= L.length) return stop(); setStep(L[s]); if (s === L.length - 1) stop(); };
  playTick(); timer = setInterval(playTick, playMs());
};
if ($("speed")) $("speed").onchange = () => { if (timer) { clearInterval(timer); timer = setInterval(playTick, playMs()); } };
if ($("gran")) $("gran").onchange = () => { if (timer) { stop(); $("play").click(); } };
function setMode(m){ mode = m; $("modePts").setAttribute("aria-pressed", m === "pts"); $("modeDen").setAttribute("aria-pressed", m === "den"); legend(); draw(); }
$("modePts").onclick = () => setMode("pts"); $("modeDen").onclick = () => setMode("den");
// comparaison 2021 retirée le 18/10/2026 (décision de l'utilisateur) : la normale 2012-2021 ne sert plus qu'au calcul des feux anormaux
$("lyrFront").onchange = e => { showFront = e.target.checked; legend(); draw(); };
$("lyrLines").onchange = e => { showLines = e.target.checked; legend(); draw(); };
if ($("lyrContact")) $("lyrContact").onchange = e => { showContact = e.target.checked; ensureView("contact", () => { contactFor = -2; legend(); draw(); }); };
if ($("lyrUnc")) $("lyrUnc").onchange = e => { showUnc = e.target.checked; legend(); draw(); };
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
if ($("lyrImp")) $("lyrImp").onchange = e => { showImp = e.target.checked; if (showImp) loadImp(() => draw()); legend(); draw(); };
if ($("lyrTg")) $("lyrTg").onchange = e => { showTg = e.target.checked; if (showTg) loadTg(() => draw()); legend(); draw(); };
if ($("lyrClaims")) $("lyrClaims").onchange = e => { showClaims = e.target.checked; legend(); draw(); };
$("lyrFire").onchange = e => { showFire = e.target.checked; $("fireOpts").hidden = !showFire; legend(); draw(); };
$("lyrLoss").onchange = e => { showLoss = e.target.checked; legend(); draw(); };
$("lyrUcdp").onchange = e => { showUcdp = e.target.checked; legend(); draw(); };
$("lyrArcs").onchange = e => { showArcs = e.target.checked; arcFocus = null; if (showArcs) loadStrikesRu(() => { sruCache = {m: -1}; draw(); }); legend(); draw(); };
if ($("lyrNowcast")) $("lyrNowcast").onchange = e => { nowcastOn = e.target.checked; arrowCache = {}; for (const key in evCache) delete evCache[key]; ensureView(fieldName(), () => { frontFor = -2; draw(); updateReadout(); drawChart && drawChart(); }); };
if ($("lyrAsym")) $("lyrAsym").onchange = e => { showAsym = e.target.checked; ensureView("asym", () => { asymFor = -2; legend(); draw(); }); legend(); draw(); };
if ($("lyrPm")) $("lyrPm").onchange = e => { showPm = e.target.checked; ensureView("pmove", () => { pmFor = -2; legend(); draw(); }); legend(); draw(); };
if (showPm) ensureView("pmove", () => { pmFor = -2; draw(); });
// légende de la carte : une entrée par case cochable (attribut data-lyr = id de la case ou raw:source), visible
// seulement quand la case est cochée ; contrôlé par scripts/check_arrows.py (chaque case a son entrée, en 4 langues)
function syncMapLegend(){
  document.querySelectorAll("#mapLegend [data-lyr]").forEach(row => { const k = row.dataset.lyr;
    if (k === "always") { row.hidden = false; return; }
    const cb = k.startsWith("raw:") ? document.querySelector(`input[data-raw="${k.slice(4)}"]`) : document.getElementById(k);
    row.hidden = !(cb && cb.checked); });
  // un groupe sans entrée visible disparaît
  document.querySelectorAll("#mapLegend details.lggrp").forEach(g => { g.hidden = ![...g.querySelectorAll("[data-lyr]")].some(r => !r.hidden); });
}
document.querySelectorAll('#panel input[type="checkbox"]').forEach(cb => cb.addEventListener("change", syncMapLegend));
// ⓘ des secteurs susceptibles de bouger : AUC et taux de base lus dans la validation du build (front.js)
function fillPmTip(){ const e = document.querySelector('[data-tt="lgPmTip"]'); if (!e || !FR.pmove || FR.pmove.auc == null) return;
  e.title = e.title.replace("{auc}", dec(FR.pmove.auc.toFixed(2))).replace("{base}", Math.round(FR.pmove.base * 100)); }
fillPmTip(); setTimeout(fillPmTip, 0); document.addEventListener("uaf-lang", () => setTimeout(fillPmTip, 0));
syncMapLegend();
// le garde-fou des flèches a besoin du champ d'asymétrie : chargé d'emblée
ensureView("asym", () => { arrowCache = {}; for (const k in possCache) delete possCache[k]; draw(); });
if ($("lyrRelief")) $("lyrRelief").onchange = e => { showRelief = e.target.checked; if (showRelief) loadRelief(() => { legend(); draw(); }); legend(); draw(); };
if (showRelief) loadRelief(() => { legend(); draw(); });
if ($("lyrDestr")) $("lyrDestr").onchange = e => { showDestr = e.target.checked; ensureView("destr", () => { destrFor = -2; legend(); draw(); }); legend(); draw(); };
if ($("lyrCombat")) $("lyrCombat").onchange = e => { showCombat = e.target.checked; ensureView("combat", () => { combatFor = -2; legend(); draw(); }); };
$("lyrArrows").onchange = e => { showArrows = e.target.checked; if (showArrows) loadMcsd(() => { arrowCache = {}; draw(); }); legend(); draw(); };
$("lyrBase").onchange = e => { showBase = e.target.checked; if (showBase) loadBasemap(); draw(); };
if ($("lyrInfra")) $("lyrInfra").onchange = e => { showInfra = e.target.checked; if (showInfra) loadInfra(() => { legend(); draw(); }); legend(); draw(); };
const tip = $("tip");
// info-bulle courte au survol (3 lignes au plus) ; les détails (sources, votes, z, composition) au clic sur la maille.
// Sur écran tactile, un premier toucher montre la version courte, un second sur la même maille les détails.
let tipKey = null, tipFull = false;   // position du dernier affichage, détails ouverts ou non
function hover(e){
  const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  if (showImp && impHits.length) {
    let best = null, bd = 40; for (const h of impHits) { const dd = Math.hypot(h.x - mx, h.y - my); if (dd < bd) { bd = dd; best = h; } }
    if (best) {
      const zs = v => v == null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1).replace(".", ",");
      tip.innerHTML = `<div>${_t("impacts détectés (30 j), anomalie locale z : côté russe {r} · côté ukrainien {u}", {r: zs(best.zr), u: zs(best.zu)})}</div><div style="color:var(--faint)">${_t("environ une détection sur deux est un cratère ; indice, pas une preuve")}</div>`;
      tip.style.display = "block"; const tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px"; return;
    }
  }
  if (showTg && tgHits.length) {
    let best = null; for (const h of tgHits) if (Math.hypot(h.x - mx, h.y - my) <= h.r + 2) { best = h; break; }
    if (best) {
      tip.innerHTML = `<div>${_t(best.key === "ru" ? "sources russes, ce mois-ci : {a} mention(s) d'attaques ukrainiennes, {p} de présence ukrainienne" : "sources ukrainiennes, ce mois-ci : {a} mention(s) d'attaques russes, {p} de présence russe", {a: best.a, p: best.p})}</div>` +
        `<div>${_t(best.kind === "cross" ? "signalée chez le camp adverse (traverse la ligne)" : "signalée de son propre côté (confirmation de contrôle)")}</div>` +
        `<div style="color:var(--faint)">${_t("indique qu'un secteur va bouger, pas qui avance")}</div>`;
      tip.style.display = "block"; const tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px"; return;
    }
  }
  if (showClaims && claimHits.length) {
    let best = null, bd = 9;
    for (const h of claimHits) { const d = Math.hypot(h.x - mx, h.y - my); if (d < bd) { bd = d; best = h; } }
    if (best) {
      const [d, conf, stale] = best.e, dd = new Date(d.slice(0, 4) + "-" + d.slice(4, 6) + "-" + d.slice(6) + "T12:00:00Z");
      const date = dd.toLocaleDateString(document.documentElement.lang || "fr", {day: "numeric", month: "long", year: "numeric"});
      const st = stale ? _t("mention ancienne : les cartes donnaient déjà la localité à ce camp 60 jours avant") : conf == null ? _t("pas encore jugeable (aucune autre carte ne s'est prononcée sous 90 jours)")
        : conf < 0 ? _t("non confirmée par une autre carte sous 90 jours") : _t("confirmée par une autre carte après {n} jours", {n: conf});
      tip.innerHTML = `<div class="r"><span>${_t(best.src === "modru" ? "annonce de prise du MoD RF" : "annonce de reprise de l'état-major ukrainien")}</span><b>${topoC(best.name)}</b></div>` +
        `<div class="r"><span>${_t("date")}</span><b>${date}</b></div><div style="color:var(--faint)">${st}</div>`;
      tip.style.display = "block"; const tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px"; return;
    }
  }
  const at = arcHover(mx, my);
  if (at) { tip.innerHTML = at; tip.style.display = "block"; const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px"; return; }
  // vue d'ensemble : aucune info-bulle (même seuil que les infrastructures)
  if (kmPerPx(tr.k) > INFRA_KMPX) { tip.style.display = "none"; return; }
  // les détails ouverts par un clic restent affichés tant que la souris ne s'éloigne pas de plus de 12 px
  if (e.type === "mousemove" && tipFull && tipKey && Math.hypot(tipKey[0] - mx, tipKey[1] - my) < 12) return;
  const near = tipKey && Math.hypot(tipKey[0] - mx, tipKey[1] - my) < 12;
  const full = e.type === "click" && (!("ontouchstart" in window) || (near && !tipFull));
  tipKey = [mx, my]; tipFull = full;
  const more = full ? "" : `<div style="color:var(--faint)">${_t("clic : détails")}</div>`;
  if (showArrows && arrowList.length) {
    let best = null, bd = 14;
    for (const h of arrowList) { const d = Math.hypot(h.x - mx, h.y - my); if (d < bd) { bd = d; best = h; } }
    if (best) {
      const fi = frontIdx(), camp = _t(best.a.ru ? "russe" : "ukrainienne");
      if (!full) {
        tip.innerHTML = (best.a.hard ? `<div style="color:var(--faint)">${_t("flèche arrêtée par une barrière naturelle (grand fleuve, plan d'eau ou escarpement)")}</div>` : "") + (best.a.ev ? (best.a.undirected ? _t("localités qui ont basculé ({c}), sens non établi", {c: camp}) : best.a.late ? _t("confirmé par annonce ({c}), pas un mouvement nouveau", {c: camp})
          : best.a.uncertain ? _t("sens incertain : on se bat derrière la ligne") : _t("localités qui ont basculé : avancée {c} · {n}", {c: camp, n: _t("{n} localité(s)", {n: best.a.ev})})) : best.a.possible ? _t("poussée ukrainienne possible : on se bat anormalement derrière la ligne russe (sur le passé, suivie d'une reprise ukrainienne environ 1 fois sur 3, peu de cas)") : best.a.uncertain ? _t("sens incertain : on se bat derrière la ligne") : _t("avancée {c} · {v} km par 30 jours", {c: camp, v: dec(Math.round(best.a.v * 10) / 10)})) + more;
        tip.style.display = "block";
        const tw = tip.offsetWidth, th = tip.offsetHeight; tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
        return;
      }
      tip.innerHTML = best.a.ev ? (best.a.undirected
        ? `<div class="r"><span>${_t("localités qui ont basculé ({c}), sens non établi", {c: camp})}</span><b>${_t("{n} localité(s)", {n: best.a.ev})}</b></div><div style="margin-top:4px;color:var(--faint)">30 derniers jours ; sens non établi : les localités basculées et le déplacement de la ligne ne vont pas dans la même direction</div>`
        : best.a.late
        ? `<div class="r"><span>${_t("confirmé par annonce ({c}), pas un mouvement nouveau", {c: camp})}</span><b>${_t("{n} localité(s)", {n: best.a.ev})}</b></div><div style="margin-top:4px;color:var(--faint)">changements annoncés dans les 30 jours que les cartes montraient déjà : pas un mouvement nouveau</div>`
        : `<div class="r"><span>${_t("changements annoncés : avancée {c}", {c: camp})}</span><b>${_t("{n} localité(s)", {n: best.a.ev})}</b></div><div style="margin-top:4px;color:var(--faint)">${_t("30 derniers jours ; journal DeepStateMap, état-major UA, MoD RF, bascules des cartes, pondérés par leur fiabilité mesurée")}</div>`)
        : `<div class="r"><span>${_t("avancée {c}", {c: camp})}</span><b>${_t("{v} km par 30 jours", {v: dec(Math.round(best.a.v * 10) / 10)})}</b></div>` +
        `<div class="r"><span>${_t("tronçon")}</span><b>${_t("~{L} km de front", {L: best.a.L})}</b></div><div class="r"><span>${_t("période")}</span><b>${frameLabel(Math.max(0, frameAt(fi, 90)))} → ${frameLabel(fi)}</b></div>` +
        `<div style="margin-top:4px;color:var(--faint)">${_t("déplacement de la ligne 50 %, même sens sur la période")}</div>`;
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
    tip.innerHTML = `<div class="r"><span>${_t(p > 0.95 ? "contrôle russe" : p < 0.05 ? "contrôle ukrainien" : "zone grise")} ${_t("(Russie)")}</span><b>${isNaN(p) ? "…" : Math.round(p*100) + "\u00a0%"}</b></div>` +
      (full ? `<div style="margin-top:4px">${_t("Russie, bande frontalière : a priori russe ; seules les annonces (état-major ukrainien, MoD RF) et les indices de combats peuvent faire déborder la ligne.")}</div>` : more);
    tip.style.display = "block";
    const tw = tip.offsetWidth, th = tip.offsetHeight; tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
    return;
  }
  if (k < 0 || fi < 0) { tip.style.display = "none"; return; }
  const b = monthCodes[fi][k], fl = pFlags[k], F = viewFields(fieldName());
  const u = d3.bisectLeft(UCELLS, j * GX + i), p = F && F[fi] ? F[fi][u] / 63 : NaN;
  const st = isNaN(p) ? "…" : _t(p > 0.95 ? "contrôle russe" : p < 0.05 ? "contrôle ukrainien" : "incertitude des cartes");
  // zone grise de contact : P(contact au sol) à la maille ; tronçon de 50 km le plus proche : largeurs par côté et
  // composition du signal ; z-scores de la maille (secteur jugé par rapport à ses 12 mois précédents)
  const uu = d3.bisectLeft(UCELLS, j * GX + i), CF = viewFields("contact"), pcv = CF && CF[fi] ? CF[fi][uu] / 63 : null, ct = FR.months[fi].contact;
  let ctxt = pcv === null ? "" : `<div class="r"><span>${_t("P(contact au sol)")}</span><b>${Math.round(pcv * 100)} %</b></div>`;
  // bande d'incertitude locale (intervalle conforme du secteur de 24 km le plus proche, dernier mois calculé)
  const LB = FR.localBand && FR.localBand[fieldName() === "nowcast" ? "nowcast" : "equilibre"];
  if (LB && LB.tiles && LB.tiles.length) {
    const ll = proj.invert([gAff[0] + (i + 0.5) * gAff[2], gAff[1] + (j + 0.5) * gAff[2]]), c = Math.cos(ll[1] * Math.PI / 180);
    let tb = null, td = 1e9;
    for (const t of LB.tiles) { const d = Math.hypot((t[0] - ll[0]) * c, t[1] - ll[1]) * 111; if (d < td) { td = d; tb = t; } }
    if (tb && td < 30 && tb[2] != null) ctxt += `<div class="r"><span>${_t("bande 95 % du secteur")}</span><b>${_t("{a} km côté ukr. · {b} km côté russe", {a: dec(tb[2]), b: dec(tb[3])})}</b></div>` +
      `<div style="color:var(--faint)">${_t("secteur de {s} : la ligne réelle est tombée dans sa bande {p} % du temps ({n} points, 2023-2026, sur le seul passé)", {s: tb[7] || "…", p: Math.round(tb[5] * 100), n: tb[6]})}</div>`;
  }
  if (ct && ct.tiles && ct.tiles.length) {
    let tb = null, td = 1e9;
    for (const t of ct.tiles) { const dd = Math.hypot(t[0] - j, t[1] - i); if (dd < td) { td = dd; tb = t; } }
    if (tb && td * FR.grid.size / 1000 < 40) {
      const wr = tb[2], wu = tb[3], tot = wr + wu, asym = tot > 2 && Math.abs(wr - wu) / tot > 0.3 ? _t(wr > wu ? " ; plus étendue côté russe" : " ; plus étendue côté ukrainien") : "";
      const cp = tb[5] || [0, 0, 0, 0];
      ctxt += `<div class="r"><span>${_t("tronçon : largeur de contact")}</span><b>${_t("{a} km côté russe · {b} km côté ukr.", {a: dec(wr), b: dec(wu)})}</b></div>` +
        `<div style="color:var(--faint)">${_t("composition près de la ligne : feux {a} %, combats au sol (UCDP) {b} %, pertes de matériel {c} %, menaces d’artillerie {d} %, assauts rapportés par l’état-major UA (faits) {e} %, localités des bilans du MoD RF (faits) {f} %{g}", {a: Math.round(cp[0] * 100), b: Math.round(cp[1] * 100), c: Math.round(cp[2] * 100), d: Math.round((cp[3] || 0) * 100), e: Math.round((cp[4] || 0) * 100), f: Math.round((cp[5] || 0) * 100), g: asym})}</div>`;
    }
  }
  const zt = ["zf", "zu", "zw", "zm"].map(v => { const Fz = viewFields(v); return Fz && Fz[fi] ? Fz[fi][uu] / 5 - 2 : null; });
  if (zt.some(v => v !== null)) {
    const zs = v => v === null ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1).replace(".", ",");
    ctxt += `<div style="color:var(--faint)">${_t("z (mois affiché / 12 mois précédents) : feux {a} · combats {b} · pertes {c} · mouvement {d}", {a: zs(zt[0]), b: zs(zt[1]), c: zs(zt[2]), d: zs(zt[3])})}</div>`;
  } else ["zf", "zu", "zw", "zm"].forEach(v => ensureView(v, () => {}));
  // secteurs susceptibles de bouger : tronçon de 24 km le plus proche (à moins de 20 km)
  const pmt = showPm && FR.months[fi] && FR.months[fi].pm;
  let pmLine = "";
  if (pmt && pmt.length) {
    let tb = null, td = 1e9;
    for (const t of pmt) { const dd = Math.hypot(t[0] - i, t[1] - j); if (dd < td) { td = dd; tb = t; } }
    if (tb && td * FR.grid.size / 1000 < 20) { pmLine = `<div class="r"><span>${_t("P(la ligne bouge d'ici 2 mois)")}</span><b>${tb[2]} %</b></div>`;
      ctxt += pmLine + `<div style="color:var(--faint)">${_t("tronçon de 24 km, ≥ 0,5 km dans un sens ou l'autre ; moyenne {b} %", {b: Math.round(((FR.pmove && FR.pmove.base) || 0) * 100)})}</div>`; }
  }
  const inf = nearInfra(j, i);
  if (inf) ctxt += `<div style="color:var(--faint)">${_t("à {d} km : {l}", {d: inf.d, l: inf.label})}</div>`;
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
  if (!full) {
    const st3 = isNaN(p) ? "…" : _t(p > 0.95 ? "contrôle russe" : p < 0.05 ? "contrôle ukrainien" : "zone grise");
    let band = "";
    if (LB && LB.tiles && LB.tiles.length) {
      const ll = proj.invert([gAff[0] + (i + 0.5) * gAff[2], gAff[1] + (j + 0.5) * gAff[2]]), c = Math.cos(ll[1] * Math.PI / 180);
      let tb = null, td = 1e9;
      for (const t of LB.tiles) { const d = Math.hypot((t[0] - ll[0]) * c, t[1] - ll[1]) * 111; if (d < td) { td = d; tb = t; } }
      if (tb && td < 30 && tb[2] != null) band = `<div class="r"><span>${_t("bande 95 %")}</span><b>${dec(Math.round((tb[2] + tb[3]) * 10) / 10)} km</b></div>`;
    }
    tip.innerHTML = `<div class="r"><span>${st3}</span><b>p ${isNaN(p) ? "…" : Math.round(p*100) + "\u00a0%"}</b></div>` +
      (pcv !== null && pcv > 0.05 ? `<div class="r"><span>${_t("P(contact au sol)")}</span><b>${Math.round(pcv * 100)} %</b></div>` : "") + band + pmLine + more;
    tip.style.display = "block";
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.min(mx + 14, W - tw - 4) + "px"; tip.style.top = Math.max(4, my - th - 10) + "px";
    return;
  }
  tip.innerHTML = `<div class="r"><span>${_t("p(occupé)")}${fvar ? " · " + fvar.replace("_", " ") : ""}</span><b>${isNaN(p) ? "…" : Math.round(p*100) + "\u00a0%"}</b></div><div class="r"><span>${_t("état")}</span><b>${st}</b></div>` + ctxt +
    `<div style="margin-top:4px;color:var(--faint)">${_t("source · vote · poids dans le calcul")}</div>` +
    act.map(([s, c]) => `<div class="r"><span>${NAMES[s]} <span style="color:var(--faint)">(${_t(({ua: "ukr.", ru: "russe", tiers: "tiers"})[FR.camps[s]])})</span></span><b>${STN[c]} · ${Math.round(FR.weights[s] / cw[FR.camps[s]] / nc * 100)}\u00a0%</b></div>`).join("") +
    `<div style="margin-top:4px;color:var(--faint)">${_t("les tracés votent selon leur distance à la maille : poids nominaux")}</div>` +
    (fl & 1 ? `<div style="margin-top:4px">${_t("Crimée, occupée depuis 2014")}</div>` : fl & 2 ? `<div style="margin-top:4px">${_t("occupé avant 2022")}</div>` : "");
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
  const front = showFront || showLines ? (showStates ? `<span><i style="background:rgba(${C["front-rgb"]},.6);border-radius:0"></i>${_t("contrôle russe (p > 0,95)")}</span><span><i style="background:rgba(${C["grey-rgb"]},.7);border-radius:0"></i>${_t("incertitude des cartes (0,05 – 0,95)")}</span><span>${_t("sans couleur : contrôle ukrainien (p < 0,05)")}</span>` : `<span><i style="background:rgba(${C["front-rgb"]},.75);border-radius:0"></i>${_t("probabilité d'occupation russe")}</span>`) +
    (showLines ? `<span><i style="background:${C.front};height:2px;border-radius:0;vertical-align:3px"></i>${_t("ligne médiane (p = 50 %)")}</span>` : "") +
    (showUnc ? `<span><i style="border-top:1px dashed ${C.front};height:0;border-radius:0;vertical-align:3px"></i>${_t("incertitude des cartes : lignes 5 % et 95 % (bande calibrée secteur par secteur)")}</span>` : "") +
    (showContact ? `<span><i style="background:repeating-linear-gradient(-45deg,rgba(${C["grey-rgb"]},.9) 0 1px,transparent 1px 4px);border-radius:0"></i>${_t("zone grise de contact : probabilité d'un contact au sol (opacité), estimée par nos signaux de combat et la distance à la ligne")}</span>` : "") +
    `<span><i style="border-top:1px dotted ${C.ink};height:0;border-radius:0;vertical-align:3px"></i>${_t("occupé avant 2022")}</span><span style="flex-basis:100%;height:0"></span>` : "";
  const items = mode === "pts"
    ? [[C["fire-now"], _t("feu en excès, mois affiché")], [C["fire-old"], _t("mois précédents (traînée)")]]
    : [[denColor(1), _t("excès cumulé élevé")], [denColor(.25), _t("faible")], ["transparent;border:1px solid " + C.ember + ";border-radius:50%", _t("maille anormale ce mois")]];
  if (!showFire) items.length = 0;
  let html = front + items.map(([c,t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("");
  if (showLoss) html += `<span style="flex-basis:100%;height:0"></span><span>${_t("pertes russes documentées (WarSpotting), cliquer pour filtrer :")}</span>` +
    O.groups.map((g,i) => `<span class="lchip" role="button" tabindex="0" data-g="${i}" aria-pressed="${lossOn[i]}">${pictoSvg(i, C["loss-now"])}${g}</span>`).join("");
  const raws = Object.entries(rawOn).filter(([, v]) => v).map(([s]) => s);
  if (raws.length) html += `<span style="flex-basis:100%;height:0"></span><span>${_t("tracés bruts, au pas affiché :")}</span>` + raws.map(s => `<span><i style="border-top:2px ${s === "isw" ? "dashed" : s === "wiki" ? "dotted" : "solid"} ${RAWCOL[s]};height:0;border-radius:0;vertical-align:3px"></i>${_t("tracé {s} : limite de sa zone russe (statut par localité, VIINA)", {s: s === "dsm" ? "DeepStateMap" : s === "isw" ? "ISW" : "Wikipedia"})}</span>`).join("");
  if (showClaims) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.front};border-radius:0;clip-path:polygon(50% 0,100% 100%,0 100%)"></i><i style="background:${C.cool};border-radius:0;clip-path:polygon(0 0,100% 0,50% 100%)"></i>${_t("annonces officielles des 30 jours précédant le pas affiché (pâles : 30 à 60 jours) : prises annoncées par le MoD RF, reprises annoncées par l'état-major ukrainien ; cliquer un triangle pour sa date et sa confirmation par une autre carte")}</span>`;
  if (showArrows) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.front};height:3px;border-radius:0;vertical-align:3px"></i>${_t("avancée russe")}</span><span><i style="background:${C.uactl};height:3px;border-radius:0;vertical-align:3px"></i>${_t("avancée ukrainienne")}</span>` +
    `<span>${_t("flèche pleine : déplacement de la ligne 50 % (km par 30 jours, sur 3 périodes de 30 jours)")}</span>` +
    `<span><i style="background:${C.uactl};height:3px;border-radius:0;vertical-align:3px;box-shadow:-4px 0 0 -1px ${C.uactl}"></i>${_t("flèche avec un cercle à la base : localités qui ont basculé selon les sources dans les 30 derniers jours (journal DeepStateMap, état-major UA, MoD RF, bascules des cartes), pondérées par la fiabilité mesurée de chaque source")}</span>` +
    `<span><i style="border-top:2px dashed ${C.front};height:0;border-radius:0;vertical-align:3px"></i>${_t("flèche en pointillés : sens incertain (on se bat derrière la ligne, ou sens non établi le long de la limite)")}</span>` +
    `<span><i style="border:1.3px solid ${C.front};width:8px;height:8px;border-radius:50%;vertical-align:0;background:transparent"></i>${_t("cercle creux : confirmé par annonce (changement que les cartes montraient déjà, pas un mouvement nouveau) ou sens non établi")}</span>`;
  if (showAsym) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:radial-gradient(#7337aa 40%,transparent 45%) 0 0/4px 4px;border-radius:0"></i>${_t("combats derrière la ligne : endroits où l'activité de combat vue des deux côtés (feux, UCDP, pertes) se concentre anormalement d'un côté de la ligne (asymétrie ≥ 0,2 et au-dessus de sa normale des 12 mois précédents, z ≥ 2, signal gardé 3 mois), pointillé violet de ce côté ; ne dit pas qui avance")}</span>`;
  if (showPm && FR.pmove) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:linear-gradient(90deg,rgba(232,162,0,.15),rgba(232,162,0,.9));border-radius:0"></i>${_t("secteurs susceptibles de bouger : probabilité que la ligne bouge d'au moins 0,5 km d'ici 2 mois dans ce tronçon de 24 km, dans un sens ou dans l'autre (plus foncé = plus probable ; AUC {a}, taux de base {b} %, estimée sur le seul passé) ; ne dit pas qui avance", {a: dec(FR.pmove.auc.toFixed(2)), b: Math.round(FR.pmove.base * 100)})}</span>`;
  if (showDestr) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:rgba(40,40,40,.7);border-radius:0"></i>${_t("destruction cumulée (radar Sentinel-1) : part du bâti dont la rétrodiffusion s'écarte durablement de 2021, jusqu'au mois précédent, mailles de 2 km ; contient des données Copernicus Sentinel modifiées")}</span>`;
  if (showRelief) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:linear-gradient(90deg,#fff,#b9b9b9,#fff);border-radius:0"></i>${_t("relief : ombrage du modèle numérique de surface Copernicus GLO-90 (90 m), exagéré pour un pays plat ; arbres et bâti y laissent une texture ; © DLR e.V. 2010-2014 et © Airbus Defence and Space GmbH 2014-2018, fourni dans le cadre de COPERNICUS par l'Union européenne et l'ESA ; tous droits réservés")}</span>`;
  if (showCombat) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:repeating-linear-gradient(45deg,rgba(${C["front-rgb"]},.9) 0 2px,transparent 2px 5px);border-radius:0"></i>${_t("combats intenses : signaux de combat (feux, UCDP, pertes) anormaux pour le secteur, z ≥ 2 par rapport à ses 12 mois précédents ; ne dit pas qui avance")}</span>`;
  if (showInfra) html += `<span style="flex-basis:100%;height:0"></span><span><i style="border-top:1px solid rgba(95,95,95,.6);height:0;border-radius:0;vertical-align:3px"></i>${_t("voies ferrées")}</span><span><i style="background:${C.ink};width:6px;height:6px;border-radius:0"></i>${_t("gares principales, sites lourds")}</span><span><i style="background:${C.ink};width:7px;height:7px;clip-path:polygon(50% 0,100% 100%,0 100%);border-radius:0"></i>${_t("centrales électriques")}</span><span><i style="border:1.2px solid ${C.ink};width:6px;height:6px;border-radius:50%;background:transparent"></i>${_t("aéroports et grandes bases")}</span><span>${_t("contours : zones industrielles de plus de 1 km² ; dessinées seulement après un zoom sur une localité{z} ; infrastructures statiques et publiques (Natural Earth, WRI Global Power Plant Database CC BY 4.0, OurAirports, © contributeurs OpenStreetMap ODbL)", {z: kmPerPx(tr.k) > INFRA_KMPX ? _t(" (zoomez pour les voir)") : ""})}</span>`;
  if (fvar) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:repeating-linear-gradient(-45deg,${C.ink} 0 1.5px,transparent 1.5px 5px);border-radius:0"></i>${_t("zones qui dépendent de la source retirée (probabilité changée de plus de 0,1)")}</span>`;
  if (showUcdp) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.ucdp};border-radius:0"></i>${_t("événements de combat UCDP du mois (mailles de 0,1°, précision ≤ 25 km ; ni camp ni auteur)")}</span>`;
  if (showArcs) html += `<span style="flex-basis:100%;height:0"></span><span><i style="background:${C.arc};height:2px;border-radius:0;vertical-align:3px"></i>${_t("missiles et drones : oblasts visés (teinte selon le volume du mois) · zones de lancement (cercles selon le nombre) ; survoler une zone ou un oblast montre ses flux ({n} % des lancements ont une cible précisée ; source : armée de l'air ukrainienne, compilation P. Ivaniuk)", {n: O.launch.specificShare})}</span>` +
    `<span><i style="background:${SRU_COL()};opacity:.6;border-radius:0"></i>${_t("frappes ukrainiennes signalées sur la Russie et les territoires occupés : régions citées dans les bilans d'interceptions du MoD RF (annonces d'une partie), mentions des autorités régionales de Belgorod, Koursk et Briansk, feux anormaux près de sites industriels ; aucune zone de lancement")} ${_t("Bilans du MoD RF moins complets depuis avril 2026 (le ministère publie surtout sur MAX).")}</span>`;
  $("legend").innerHTML = html;
}

function updateReadout(){
  const s = D.series[cur], m = +D.months[cur].slice(5) - 1;
  let cum = 0; for (let i = 0; i <= cur; i++) cum += D.series[i][2];
  $("mLabel").textContent = stepLabel(stepIdx);
  $("mSub").innerHTML = layerNotes().join("<br>");
  const nd = v => covered[cur] ? fmt(v) : "—";
  $("readout").innerHTML = [
    ["feux détectés ce mois-ci", nd(s[0])],
    ["normale du mois (2012-2021)", fmt(s[1])],
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
    if (fi < 0) n.push(_t("front : pas d'instantané pour ce mois"));
    else if (!AX[stepIdx] || AX[stepIdx].f < 0) n.push(_t("front : {d} (dernier disponible, J-7)", {d: frameLabel(fi)}));
    else if (AX[stepIdx].d) n.push(_t("front : estimation au {d} (pas de {s} jours, signaux de combat sur {w} jours)", {d: frameLabel(fi), s: FR.stepDays || 3, w: FR.windowDays || 14}));
  }
  if (showFire) {
    if (!covered[cur]) n.push(_t("feux : aucune donnée S-NPP ce mois-ci"));
    else if (!D.series[cur][2]) n.push(_t("feux : aucun excès détecté ce mois-ci") + (cur === lastIdx && partial ? _t(" (mois en cours, jusqu'au {d})", {d: +D.lastDay.slice(8)}) : ""));
    else if (cur === lastIdx && partial) n.push(_t("feux : mois en cours, jusqu'au {d}", {d: +D.lastDay.slice(8)}));
    else if (D.series[cur][3] < 1) n.push(_t("feux : {p} % des jours observés", {p: Math.round(D.series[cur][3]*100)}));
  } else if (cur === 0) n.push(_t("à partir du 24 février"));
  if (showLoss && cur > cutIdx) n.push(_t("pertes : pas encore publiées (mois complet + 7 jours)"));
  else if (showLoss && !O.ws.perMonth[cur].some(Boolean)) n.push(_t("pertes : aucune ce mois-ci"));
  if (showUcdp) { let any = false; for (let j = 0; j < NU; j++) if (UC[j*5+2] === cur) { any = true; break; }
    if (!any && ucdpMonth() !== cur) n.push(_t("combats UCDP : ce mois n'est pas encore publié, dernier mois publié affiché ({m})", {m: monthLabel(ucdpMonth())}));
    else if (!any) n.push(_t(cur > cutIdx || D.months[cur] > (O.ucdp.last || "") ? "combats UCDP : pas encore publiés pour ce mois" : "combats UCDP : aucun événement localisé")); }
  if (showArcs && !O.launch.arcs.some(a => a[0] === cur)) n.push(_t(cur > cutIdx ? "missiles : pas encore publiés (mois complet + 7 jours)" : D.months[cur] < "2022-10" ? "missiles : série à partir d'octobre 2022" : "missiles : aucune cible précisée ce mois-ci"));
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
    title: i => FR.months[i] ? _t("{m} — {mode} : {a} km² (95 % : {lo} – {hi}) ; vue russe {r}, vue ukrainienne {u}", {m: monthLabel(i), mode: MODEN[amode()], a: fmt(FR.months[i].a[amode()][perim][0]), lo: fmt(mcOf(FR.months[i])[perim][0]), hi: fmt(mcOf(FR.months[i])[perim][1]), r: fmt(FR.months[i].a.ru[perim][0]), u: fmt(FR.months[i].a.ua[perim][0])}) : _t("{m} — pas d'instantané", {m: monthLabel(i)})});
  const rrows = chartFrames().map((v, i) => v && v.a[amode()] && v.a[amode()].russie ? [i, v.a[amode()].russie[1]] : null).filter(Boolean);
  const ruEl = document.getElementById("ruChart");
  if (ruEl && rrows.some(r => r[1] >= 50)) {
    lineChart(ruEl, rrows, {zero: true, color: C.cool, lines: [{i: 1, color: C.cool, w: 1.8}], h: 90,
      title: i => FR.months[i] && FR.months[i].a[amode()].russie ? _t("{m} — en Russie, sous contrôle ukrainien (p < 0,5) : {v} km²", {m: monthLabel(i), v: fmt(FR.months[i].a[amode()].russie[1])}) : ""});
  } else if (ruEl) ruEl.replaceChildren();
  const grows = chartFrames().map((v, i) => v && v.grey ? [i, v.contact ? v.contact.km2 : null, v.grey.km2] : null).filter(Boolean);
  lineChart($("greyChart"), grows, {zero: true, color: C.accent, lines: [{i: 2, color: C.ink, w: 1.2, dash: "5 3", op: .7}, {i: 1, color: C.accent, w: 2.2}],
    title: i => { const v = FR.months[i]; if (!v || !v.grey) return `${monthLabel(i)} — pas d'instantané`;
      return `${monthLabel(i)} — zone grise de contact (nos signaux) ${v.contact ? fmt(v.contact.km2) + " km², " + String(v.contact.w_ru).replace(".", ",") + " km côté russe et " + String(v.contact.w_ua).replace(".", ",") + " km côté ukrainien en moyenne" : "—"} ; incertitude des cartes ${fmt(v.grey.km2)} km²`; }});
  // profil de l'activité de combat selon la distance à la ligne (tous instantanés)
  const cpEl = document.getElementById("contactProfile");
  if (cpEl && FR.contact && FR.contact.profile && FR.contact.profile.length) {
    const P = FR.contact.profile, Bn = FR.contact.bins, w = cpEl.clientWidth, h = 170, m = {t: 10, r: 8, b: 30, l: 46};
    const bgU = (P.filter((_, k) => Bn[k] <= -40).reduce((a, x) => a + x, 0) / Math.max(1, P.filter((_, k) => Bn[k] <= -40).length)) || 1e-3;
    const bgR = (P.filter((_, k) => Bn[k] >= 40).reduce((a, x) => a + x, 0) / Math.max(1, P.filter((_, k) => Bn[k] >= 40).length)) || 1e-3;
    const x = d3.scaleLinear().domain([-60, 60]).range([m.l, w - m.r]), y = d3.scaleLinear().domain([0, d3.max(P) * 1.05]).range([h - m.b, m.t]);
    const svg = d3.create("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("height", h);
    svg.append("path").attr("d", d3.line().x((_, k) => x(Bn[k])).y(v => y(v)).curve(d3.curveMonotoneX)(P)).attr("fill", "none").attr("stroke", C.accent).attr("stroke-width", 2);
    svg.append("line").attr("x1", x(-60)).attr("x2", x(0)).attr("y1", y(2 * bgU)).attr("y2", y(2 * bgU)).attr("stroke", C.ink).attr("stroke-dasharray", "4 3").attr("opacity", .6);
    svg.append("line").attr("x1", x(0)).attr("x2", x(60)).attr("y1", y(2 * bgR)).attr("y2", y(2 * bgR)).attr("stroke", C.ink).attr("stroke-dasharray", "4 3").attr("opacity", .6);
    svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.t).attr("y2", h - m.b).attr("stroke", C.front).attr("stroke-width", 1.5);
    svg.append("g").selectAll("text").data([-60, -40, -20, 0, 20, 40, 60]).join("text").attr("class", "tick").attr("x", x).attr("y", h - m.b + 14).attr("text-anchor", "middle").text(v => (v > 0 ? "+" : "") + v);
    svg.append("text").attr("class", "tick").attr("x", m.l).attr("y", h - 2).text("km de la ligne 50 % (− côté ukrainien, + côté russe) ; tirets : deux fois le fond mesuré à 40-60 km");
    cpEl.replaceChildren(svg.node());
  }
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
  if (FR.asym && FR.asym.bilan && $("asymTab")) {
    const pc = x => Math.round(x * 100) + " %";
    $("asymTab").innerHTML = `<tr><th>asymétrie (côté russe − côté ukrainien)</th><th>secteurs-mois</th><th>gain russe ensuite (t → t+2)</th><th>reprise ukrainienne</th><th>rien</th></tr>` +
      Object.entries(FR.asym.bilan).map(([k, v]) => `<tr><td>${k}</td><td>${fmt(v[0])}</td><td>${pc(v[1])}</td><td>${pc(v[2])}</td><td>${pc(v[3])}</td></tr>`).join("");
  }
  if (FR.localBand && FR.localBand.nowcast && $("lbTab")) {
    const B = FR.localBand.nowcast, f = x => x == null ? "–" : String(x).replace(".", ","), pc = x => x == null ? "–" : String(Math.round(x * 1000) / 10).replace(".", ",") + " %";
    $("lbSum").textContent = `${B.tilesN} secteurs de ${B.params.tile_km} km ; couverture par secteur : médiane ${pc(B.local.median)}, 10e centile ${pc(B.local.p10)}, minimum ${pc(B.local.min)} ; ` +
      `secteurs entre 90 et 99 % : ${Math.round(B.local.in90_99 * 100)} % (bande unique pour tout le front : ${Math.round(B.global.in90_99 * 100)} %) ; sous 90 % : ${Math.round(B.local.below90 * 100)} % (bande unique : ${Math.round(B.global.below90 * 100)} %). ` +
      `Ancienne bande publiée (σ0 par source) : couverture ${pc(B.rawCoverage)}, donc trop large.`;
    $("lbStrata").innerHTML = `<tr><th>strate</th><th>couverture, bande locale</th><th>couverture, bande unique</th><th>points</th></tr>` +
      Object.entries(B.strata).map(([k, v]) => `<tr><td>${k}</td><td>${pc(v.local)}</td><td>${pc(v.global)}</td><td>${fmt(v.n)}</td></tr>`).join("");
    const rows = [...B.tiles].filter(t => t[2] != null).sort((a, b) => (b[2] + b[3]) - (a[2] + a[3]));
    $("lbTab").innerHTML = `<tr><th>secteur (localité la plus proche)</th><th>largeur actuelle : côté ukr. + côté russe</th><th>largeur médiane 2023-2026</th><th>couverture passée</th><th>points</th></tr>` +
      rows.map(t => `<tr><td>${t[7] || ""} <span style="color:var(--faint)">${f(t[1])} N, ${f(t[0])} E</span></td><td>${f(t[2])} + ${f(t[3])} = ${f(Math.round((t[2] + t[3]) * 10) / 10)} km</td><td>${f(t[4])} km</td><td>${pc(t[5])}</td><td>${fmt(t[6])}</td></tr>`).join("");
    // carte : un disque par secteur, couleur selon la largeur actuelle, projection équirectangulaire simple
    const svg = d3.select("#lbMap"), Wm = 640, Hm = 380, x = d3.scaleLinear([29.5, 40.5], [10, Wm - 10]), y = d3.scaleLinear([44.3, 52.6], [Hm - 10, 10]);
    const col = d3.scaleSequential(d3.interpolateYlOrRd).domain([4, 30]);
    svg.attr("viewBox", `0 0 ${Wm} ${Hm}`).selectAll("*").remove();
    svg.selectAll("circle").data(rows).join("circle").attr("cx", t => x(t[0])).attr("cy", t => y(t[1])).attr("r", 7)
      .attr("fill", t => col(t[2] + t[3])).attr("stroke", "var(--ink)").attr("stroke-width", 0.4).append("title").text(t => `${t[7]} : ${f(Math.round((t[2] + t[3]) * 10) / 10)} km, couverture ${pc(t[5])}`);
    for (const [n, lo, la] of [["Kharkiv", 36.25, 49.98], ["Lyman", 37.8, 48.99], ["Pokrovsk", 37.18, 48.28], ["Zaporijjia", 35.14, 47.84], ["Kherson", 32.62, 46.64]])
      svg.append("text").attr("x", x(lo) + 9).attr("y", y(la) + 3).attr("font-size", 10).attr("fill", "var(--ink)").text(n);
    const lg = svg.append("g").attr("transform", `translate(12,${Hm - 24})`);
    [5, 10, 15, 20, 25].forEach((v, k) => { lg.append("rect").attr("x", k * 34).attr("width", 34).attr("height", 8).attr("fill", col(v)); lg.append("text").attr("x", k * 34).attr("y", 18).attr("font-size", 9).attr("fill", "var(--ink)").text(v + " km"); });
  }
  if (FR.contact && FR.contact.auc != null) {
    $("pContactAuc").textContent = FR.contact.auc.toFixed(3).replace(".", ",");
    if ($("pContrib") && FR.contact.contrib) {
      const f4 = v => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(4).replace(".", ",");
      $("pContactAucEq").textContent = FR.contact.aucEq != null ? FR.contact.aucEq.toFixed(3).replace(".", ",") : "–";
      $("pContrib").innerHTML = `<tr><th>signal retiré</th><th>perte d'AUC</th><th>perte d'AUC à distance égale</th></tr>` +
        FR.contact.contrib.map(([g, a, e]) => `<tr><td>${g}</td><td>${f4(a)}</td><td>${f4(e)}</td></tr>`).join("");
    }
    $("pContactCoef").textContent = Object.entries(FR.contact.coef || {}).filter(([k]) => k !== "const").map(([k, v]) => `${k} ${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2).replace(".", ",")}`).join(" ; ") + " (variables standardisées)";
  }
  if (FR.localSigma && FR.localSigma.validation) {
    const V = FR.localSigma.validation, f = x => x == null ? "–" : String(x).replace(".", ",");
    $("pLocal").textContent = ["stable", "actif", "dnipro"].filter(s => V.global[s]).map(s =>
      `${({stable: "secteurs stables", actif: "secteurs actifs", dnipro: "Dnipro"})[s]} : bande 5-95 % ≈ ${f(V.global[s].band_km)} → ${f(V.local[s].band_km)} km, erreur de calibration ${f(V.global[s].ece)} → ${f(V.local[s].ece)}`).join(" ; ");
  }
  if (FR.events && $("evTab")) {
    const LB = {dsmlog: "journal DeepStateMap (déclarations datées)", gsua: "état-major ukrainien", modru: "MoD RF", dsm: "bascules de DeepStateMap (VIINA)", wiki: "bascules de Wikipédia (VIINA)"};
    // tableau public de fiabilité : annonces jugeables (une autre carte se prononce sous 90 jours), confirmées, délai de
    // confirmation (jours, P25 · médiane · P75), mentions anciennes écartées (localité déjà au camp annonceur), par année
    const TB = FR.events.table || {}, yrs = [...new Set(Object.values(TB).flatMap(v => Object.keys(v.years || {})))].sort();
    $("evTab").innerHTML = `<tr><th>source</th><th>jugeables</th><th>confirmées par une autre carte (90 j)</th><th>délai de confirmation (jours : P25 · médiane · P75)</th><th>mentions anciennes écartées</th>${yrs.map(y => `<th>${y}</th>`).join("")}</tr>` +
      FR.events.sources.map(k => { const v = TB[k];
        if (!v) return `<tr><td>${LB[k] || k}</td><td>–</td><td>${Math.round(FR.events.reliability[k] * 100)} %</td><td>–</td><td>–</td>${yrs.map(() => "<td>–</td>").join("")}</tr>`;
        return `<tr><td>${LB[k] || k}</td><td>${fmt(v.n)}</td><td>${Math.round(v.rate * 100)} % (${fmt(v.ok)})</td><td>${v.delay ? v.delay.join(" · ") : "–"}</td><td>${k === "dsm" || k === "wiki" ? "–" : fmt(v.stale)}</td>` +
          yrs.map(y => { const a = (v.years || {})[y]; return `<td>${a && a[1] ? Math.round(a[0] / a[1] * 100) + " %" : "–"}</td>`; }).join("") + "</tr>"; }).join("");
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
  // page méthode : section algorithme (FR, EN ; UK et RU renvoient à FR/EN), titres traduits, note sur le corps en français
  document.querySelectorAll(".algo .algo-l").forEach(d => d.hidden = d.dataset.lang !== code);
  if (window.METHODE_H) {
    const li = {en: 0, uk: 1, ru: 2}[code];
    document.querySelectorAll("h1, h2, h3").forEach(h => { if (h.closest(".i18n") || h.closest(".algo")) return;
      if (!h.dataset.fr) h.dataset.fr = h.textContent;
      const tt = window.METHODE_H[h.dataset.fr]; h.textContent = li === undefined || !tt ? h.dataset.fr : tt[li]; });
    const n = document.getElementById("frNote"); if (n) { n.hidden = code === "fr"; n.textContent = code === "fr" ? "" : window.METHODE_NOTE[{en: 0, uk: 1, ru: 2}[code]]; }
  }
  document.documentElement.lang = code;
  document.querySelectorAll("[data-lang-btn]").forEach(b => b.setAttribute("aria-pressed", b.dataset.langBtn === code));
  try { localStorage.setItem("uaf-lang", code); } catch(e) {}
}
document.querySelectorAll("[data-lang-btn]").forEach(b => b.onclick = () => setLang(b.dataset.langBtn));
try { const l = localStorage.getItem("uaf-lang"); if (l && document.querySelector(`.intro .i18n[data-lang="${l}"]`)) setLang(l); } catch(e) {}
legend(); full();
window.addEventListener("load", () => { if (showBase) setTimeout(loadBasemap, 300); if (showArrows) loadMcsd(() => { arrowCache = {}; draw(); }); });
document.addEventListener("uaf-lang", () => { updateArea(); setStep(stepIdx); legend(); drawAreaChart(); draw(); });
let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (canvas.parentElement.clientWidth !== W) full(); }, 150); });
document.fonts && document.fonts.ready.then(() => draw());
// légende de la carte repliée d'office sur mobile (elle prenait la place de la carte) ; un toucher l'ouvre
if ($("mapLegend") && window.matchMedia && window.matchMedia("(max-width:560px)").matches) $("mapLegend").open = false;
})();
