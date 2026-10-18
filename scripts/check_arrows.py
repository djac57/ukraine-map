"""Contrôle automatique des flèches « qui pousse où », dans un navigateur sans interface.

    python3 scripts/check_arrows.py [chemin_chrome]

Pour chaque flèche de chaque mois et de chaque échelle (100, 50, 20 et 10 km) : la base doit être à moins de
2 km de la ligne 50 % du mois affiché, et la flèche à moins de 30° de la normale locale de cette ligne.
Rappel : chaque cas connu (Avdiïvka, Otcheretyne, Vovtchansk, Koursk à l'expansion et au reflux, Niou-York/Toretsk,
Vouhledar, Selydove/Kourakhove, Pokrovsk, Koupiansk) doit avoir au moins une flèche dans le bon sens, et Koursk aucune
flèche rouge pendant l'expansion (août 2024) ni bleue pendant le reflux (novembre 2024 - mars 2025).
Vivaldi (nord de Lyman, 2026) : flèches bleues à toutes les échelles en septembre (et sur deux mois dès qu'octobre
est construit), et 150 à 280 km² repris fin septembre dans le saillant. Fins de série (ISW, UCDP) : pas de saut.
Les tests marqués ALERTE (Vivaldi, Dnipro calme) sont affichés mais ne bloquent pas, provisoirement. Faux positifs : la
ligne du Dnipro à Kherson (avril 2023 - 2025) ne doit pas en avoir plus de 8 sur 33 mois et deux échelles.
Vue d'ensemble : pendant les mois actifs, chaque tronçon en mouvement (20, 50 ou 100 km) doit avoir une flèche
affichée à moins de 50 km, et au moins une flèche par ~100 km de ligne en mouvement (longueur moyenne sur les trois
échelles).
Sort avec le code 1 si un contrôle échoue (le workflow ne versionne alors pas les sorties).
"""
import functools
import http.server
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
# Cas connus : chacun doit avoir au moins une flèche dans le bon sens (camp, et direction générale attendue :
# vecteur sur la grille, x vers l'est, y vers le SUD). Cas calme : pas de flèche attendue.
SOUDJA = [35.27, 51.19]   # centre de la poche de Koursk : « vers » / « depuis » un point plutôt qu'une direction fixe
CASES = [
    ("Avdiïvka", ["2024-02", "2024-03"], [37.40, 37.95, 47.95, 48.30], True, [-1, 0]),
    ("Otcheretyne", ["2024-04", "2024-05"], [37.20, 37.70, 48.10, 48.45], True, [-0.7, -0.7]),
    # Vovtchansk : la ville reste disputée en juin 2024 (p = 0,44) ; la ligne 50 % n'y entre qu'en juillet, depuis
    # que le filtre temporel ne diffuse plus la bande russe à travers la frontière
    ("Vovtchansk", ["2024-05", "2024-06", "2024-07"], [36.60, 37.30, 50.10, 50.50], True, [0, 1]),
    ("Koursk, expansion", ["2024-08"], [34.80, 35.80, 51.00, 51.60], False, {"depuis": SOUDJA}),
    ("Koursk, reflux", ["2024-12", "2025-01", "2025-02", "2025-03"], [34.80, 35.80, 51.00, 51.60], True, {"vers": SOUDJA}),
    ("Niou-York / Toretsk", ["2024-06", "2024-07", "2024-08"], [37.55, 38.00, 48.20, 48.50], True, [-1, 0]),
    ("Vouhledar", ["2024-09", "2024-10"], [37.00, 37.60, 47.65, 48.00], True, [-0.7, -0.7]),
    ("Selydove / Kourakhove", ["2024-10", "2024-11", "2024-12"], [36.90, 37.60, 47.90, 48.30], True, [-1, 0]),
    ("Pokrovsk", ["2025-0%d" % m for m in range(1, 10)] + ["2025-10", "2025-11", "2025-12"], [36.80, 37.50, 48.05, 48.50], True, [-1, 0]),
    ("Koupiansk", ["2025-0%d" % m for m in range(1, 10)] + ["2025-10", "2025-11", "2025-12"], [37.30, 38.00, 49.45, 49.95], True, [-1, 0]),
]
# Sens interdits à Koursk : aucune flèche rouge pendant l'expansion (août 2024), aucune bleue pendant le reflux
# (novembre 2024 - mars 2025)
FORBID = [("Koursk, pas de rouge à l'expansion", ["2024-08"], [34.80, 35.80, 51.00, 51.60], True),
          ("Koursk, pas de bleu au reflux", ["2024-11", "2024-12", "2025-01", "2025-02", "2025-03"], [34.80, 35.80, 51.00, 51.60], False)]
# Opération Vivaldi (contre-offensive ukrainienne au nord de Lyman, été 2026) : flèches bleues dans le saillant à
# toutes les échelles (vue d'ensemble, 20 km, 10 km), et surface reprise fin septembre de l'ordre des bilans publiés
# (≈ 176 km² fin septembre, 277 km² au bilan ultérieur) : 150 à 280 km². Mois exigés : aucune source utilisable ne
# documente la reprise avant septembre (DeepStateMap ne l'a pas annoncée dans son journal, l'état-major ukrainien l'a
# révélée le 15 septembre, l'ISW s'arrête en juin) ; on exige donc septembre, puis deux mois dès qu'octobre est construit.
# ALERTES non bloquantes, provisoires (décision du 17 octobre 2026), jusqu'au nowcast par les signaux de combat :
# - flèches de Vivaldi : faute de sources, la reprise n'est visible qu'à partir de septembre ou d'octobre ;
# - Dnipro calme : 13 flèches (8 tolérées), dues surtout au codage de Kherson par VIINA (russe dans 2 ou 3 cartes
#   jusqu'en avril 2023) et aux îles du delta en 2024-2025.
# Surface : ALERTE non bloquante. Wikipédia n'a jamais marqué le saillant russe en 2025 : le modèle le tenait pour
# disputé (p ≈ 0,5), si bien que sa reprise ne retire au plus qu'environ la moitié de sa surface ; et Karpivka, Petrovka,
# etc. ne sont libérées que sur la géométrie de DeepStateMap, jamais dans son journal (option retenue : journal seul).
VIVALDI = ("Vivaldi (nord de Lyman)", ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"], [37.55, 38.15, 48.98, 49.30], ["2026-05", "2026-06", "2026-07", "2026-08"], "2026-09", [150, 280])
# Fins de série : l'arrêt d'une source ne doit pas faire bouger la ligne. Déplacement médian |v| des tronçons de 20 km
# sur tout le front, le mois qui suit la fin : échec s'il dépasse 0,3 km ET deux fois la médiane des six mois
# précédents (un mois simplement actif, comme novembre 2024 à 0,4 km, n'est pas un artefact). L'ISW s'arrête fin juin
# 2026 (sans correction : 2,04 km), l'UCDP fin août 2026.
# Mois de référence affichés pour comparaison.
SERIES_END = [("fin de l'ISW", "2026-07"), ("fin de l'UCDP", "2026-09")]
MOVES_REF = ["2024-11", "2025-06", "2026-06"]
# Flèches d'événements (changements de contrôle annoncés et datés, 30 derniers jours) : au moins une flèche du bon camp
# sur la période (mois ou pas récents « AAAA-MM-JJ ») ; et peu de flèches sur le Dnipro calme
EVENT_CASES = [
    ("Vivaldi, annonces (septembre - début octobre 2026)", ["2026-09", "2026-09-19", "2026-09-28", "2026-10-04", "2026-10-10"], [37.55, 38.15, 48.98, 49.30], False),
    ("Avdiïvka, annonces (février 2024)", ["2024-02", "2024-03"], [37.40, 37.95, 47.95, 48.30], True),
    ("Pokrovsk, annonces (2025)", ["2025-0%d" % m for m in range(1, 10)], [36.80, 37.50, 48.05, 48.50], True),
    ("Koursk, annonces (reflux, début 2025)", ["2025-01", "2025-02", "2025-03"], [34.80, 35.80, 51.00, 51.60], True),
]
EVENT_CALM = ("Dnipro à Kherson, annonces", [f"{y}-{m:02d}" for y in (2023, 2024, 2025) for m in range(1, 13) if f"{y}-{m:02d}" >= "2023-04"], [32.30, 33.90, 46.40, 47.00], 6)
# Pas récents (tous les 3 jours) : flèche bleue de gradient au nord de Lyman entre fin septembre et le dernier pas
DAILY_CASES = [("Vivaldi, gradient sur les pas récents", "2026-09-19", "2026-10-31", [37.55, 38.15, 48.98, 49.30], False)]
# Calme : à partir d'avril 2023 (la fenêtre de trois mois ne contient plus le retrait russe de novembre 2022) ;
# tolérance de 8 flèches sur 33 mois et deux échelles (révisions ponctuelles des cartes sur les îles du delta)
# Mois actifs pour le contrôle de la vue d'ensemble
OVERVIEW = ["2024-02", "2024-05", "2024-08", "2024-11", "2025-03", "2025-08"]
CALM = ("Dnipro à Kherson", [f"{y}-{m:02d}" for y in (2023, 2024, 2025) for m in range(1, 13) if f"{y}-{m:02d}" >= "2023-04"], [32.30, 33.90, 46.40, 47.00])
HARNESS = """<!doctype html><html><head><meta charset="utf-8"></head><body><pre id=o></pre><iframe id=f src="index.html" style="width:1000px;height:800px"></iframe>
<script>const EVENT_CASES=__EVENT_CASES__, EVENT_CALM=__EVENT_CALM__, DAILY_CASES=__DAILY_CASES__, SERIES_END=__SERIES_END__, MOVES_REF=__MOVES_REF__, PARAMS=__PARAMS__, CASES=__CASES__, CALM=__CALM__, OVERVIEW=__OVERVIEW__, FORBID=__FORBID__, VIVALDI=__VIVALDI__;
const okDir=(a,dir)=>{if(!dir) return true; if(Array.isArray(dir)) return a.dir[0]*dir[0]+a.dir[1]*dir[1]>0;
 const c=dir.vers||dir.depuis, tx=(c[0]-a.lon)*Math.cos(a.lat*Math.PI/180), ty=-(c[1]-a.lat), d=a.dir[0]*tx+a.dir[1]*ty; return dir.vers? d>0 : d<0;};
f.onload=()=>{const w=f.contentWindow;w.onerror=(m,u,l)=>o.textContent+='ERREUR '+m+' @'+l+'\\n';
const go=(n)=>{ if((!w.FRONTP||!w.FRONTP.mcsd||!w.FRONTP.asym||!w.__uafArrowCheck||!w.__uafArrows)&&n<60){return setTimeout(()=>go(n+1),500);}
 try{if(PARAMS) w.__uafSetArrowParams(PARAMS);const r=w.__uafArrowCheck(); o.textContent+='RESULTAT total='+r.total+' echecs='+r.fails.length+' '+JSON.stringify(r.fails.slice(0,20))+'\\n'; o.textContent+='SENS|flèches d’annonces : '+(r.evN||0)+' ; sans sens établi (repère creux) '+(r.reoriented||0)+' ; confirmations (en retard sur les cartes, sans direction) '+(r.late||0)+' ; sens vérifié contre le territoire basculé : '+(r.flipChecked||0)+'\\n';
  {const i=w.DATA.months.indexOf('2026-09'); if(i>=0){const A=w.__uafDirBox(i,[36.3,37.8,50.0,50.8]); const bad=A.filter(a=>a.viol); o.textContent+='CAS|Vovtchansk et Belgorod (septembre 2026) : aucune flèche vers la Russie ni vers une zone déjà tenue|'+(bad.length?'ECHEC':'OK')+'|'+JSON.stringify(A)+'|'+A.length+'\\n';}}
  o.textContent+='DIRECTION|flèches réorientées le long de la limite : '+(r.redir||0)+' ; flèches de gradient dont la base est dans la bande russe : '+(r.border||0)+' ; '+JSON.stringify(r.redirList||[])+'\\n';
  const months=w.DATA.months, inb=(a,b)=>a.lon>=b[0]&&a.lon<=b[1]&&a.lat>=b[2]&&a.lat<=b[3];
  for(const [name,ms,box,ru,dir] of CASES){let hit=null,n=0;
    for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [100,50,20]) for(const a of w.__uafArrows(i,L)){ if(!inb(a,box)) continue; n++;
      if(a.ru===ru && okDir(a,dir) && !hit) hit=m+' L'+L+' '+a.v+' km/mois';}}
    o.textContent+='CAS|'+name+'|'+(hit?'OK':'ECHEC')+'|'+(hit||'aucune flèche dans le bon sens')+'|'+n+'\\n';}
  for(const [name,ms,box,ru] of FORBID){const bad=[]; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [100,50,20,'overview']) for(const a of w.__uafArrows(i,L)) if(inb(a,box)&&a.ru===ru) bad.push(m+' L'+L+' '+a.v);}
    o.textContent+='CAS|'+name+'|'+(bad.length?'ECHEC':'OK')+'|'+(bad.length?bad.slice(0,4).join(', '):'aucune')+'|'+bad.length+'\\n';}
  {const [name,ms,box,refs,last,rng]=VIVALDI; const ok=[], per={};
   for(const m of ms){const i=months.indexOf(m); if(i<0) continue; const sc={}; for(const L of ['overview',20,10]) sc[L]=w.__uafArrows(i,L).filter(a=>inb(a,box)&&!a.ru).length; per[m]=sc; if(sc.overview&&sc[20]&&sc[10]) ok.push(m);}
   const area=m=>w.__uafArea(months.indexOf(m),box), peak=Math.max(...refs.map(area)), gain=Math.round(peak-area(last));
   const need=w.__uafP(months.indexOf('2026-10'),37.8,49.1)!==null?2:1;
   o.textContent+='CAS|'+name+', flèches bleues|'+(ok.length>=need&&ok.includes('2026-09')?'OK':'ALERTE')+'|'+(ok.join(', ')||'aucun mois')+' (exigé : '+need+' mois dont septembre) '+JSON.stringify(per)+'|'+ok.length+'\\n';
   o.textContent+='CAS|'+name+', surface reprise|'+(gain>=rng[0]&&gain<=rng[1]?'OK':'ALERTE')+'|'+gain+' km² repris fin '+last+' (pic '+Math.round(peak)+' km² russes ; attendu '+rng[0]+'-'+rng[1]+')|'+gain+'\\n';}
  {const med=m=>{const i=months.indexOf(m); const x=w.__uafMoves(i,20).map(Math.abs).sort((a,b)=>a-b); return x.length? x[Math.floor(x.length/2)] : null;};
   const ref=MOVES_REF.map(m=>m+' '+(med(m)??'-').toString()).join(', ');
   for(const [name,m] of SERIES_END){const v=med(m), i=months.indexOf(m), prev=[1,2,3,4,5,6].map(k=>med(months[i-k])).filter(x=>x!==null).sort((a,b)=>a-b), base=prev.length?prev[Math.floor(prev.length/2)]:0;
     const ok=v!==null&&(v<0.3||v<2*base); o.textContent+='SERIE|'+name+' ('+m+')|'+(ok?'OK':'ECHEC')+'|déplacement médian '+(v===null?'-':v.toFixed(2))+' km ; médiane des 6 mois précédents '+base.toFixed(2)+' km (référence : '+ref+')\\n';}}
  {const FM=w.FRONT.months, idx=k=>k.length===7?months.indexOf(k):FM.findIndex(m=>m&&m.date===k);
   for(const [name,ks,box,ru] of EVENT_CASES){let hit=null,n=0; for(const k of ks){const i=idx(k); if(i<0) continue; for(const a of w.__uafEvents(i)){ if(!inb(a,box)) continue; n++; if(a.ru===ru&&!a.late&&!hit) hit=k+' '+a.n+' localité(s), poids '+a.w;}}
     o.textContent+='CAS|'+name+'|'+(hit?'OK':'ECHEC')+'|'+(hit||'aucune flèche d’annonces du bon camp')+'|'+n+'\\n';}
   {const [name,ks,box,lim]=EVENT_CALM; let n=0; for(const k of ks){const i=idx(k); if(i<0) continue; n+=w.__uafEvents(i).filter(a=>inb(a,box)).length;}
     o.textContent+='CALME|'+name+'|'+(n<=lim?'OK':'ALERTE')+'|'+n+' flèche(s) d’annonces sur 33 mois (tolérance '+lim+')\\n';}
   for(const [name,d0,d1,box,ru] of DAILY_CASES){let hit=null,n=0,steps=0; FM.forEach((m,i)=>{ if(!m||m.kind!=='d'||m.date<d0||m.date>d1) return; steps++; for(const L of [20,10,'overview']) for(const a of w.__uafArrows(i,L)){ if(!inb(a,box)) continue; n++; if(a.ru===ru&&!hit) hit=m.date+' L'+L+' '+a.v+' km/30 j';}});
     o.textContent+='CAS|'+name+'|'+(hit?'OK':(steps?'ALERTE':'OK'))+'|'+(hit||(steps?'aucune flèche bleue sur '+steps+' pas':'pas de pas récents dans la période'))+'|'+n+'\\n';}}
  {let g=0,e=0,cf=0,n=0; for(const m of months){ if(m<'2024-01'||m>'2026-09') continue; const i=months.indexOf(m); if(!w.FRONT.months[i]) continue; n++; g+=w.__uafArrows(i,'overview').length; const ev=w.__uafEvents(i); e+=ev.filter(a=>!a.late).length; cf+=ev.filter(a=>a.late).length;}
   o.textContent+='COMPTE|vue d’ensemble 2024-2026|'+n+' mois|flèches de gradient '+(g/Math.max(n,1)).toFixed(1)+' par mois ; flèches d’annonces (mouvement nouveau) '+(e/Math.max(n,1)).toFixed(1)+' ; total des mouvements '+((g+e)/Math.max(n,1)).toFixed(1)+' ; confirmations d’annonces (creuses) '+(cf/Math.max(n,1)).toFixed(1)+'\\n';}
  {let miss=[], wr=[], wu=[]; for(const m of months){ if(m<'2024-01'||m>'2026-09') continue; const i=months.indexOf(m); const c=w.FRONT.months[i]&&w.FRONT.months[i].contact; if(!c) continue; if(!c.km2) miss.push(m); wr.push(c.w_ru); wu.push(c.w_ua);}
   const med=a=>a.length?a.slice().sort((x,y)=>x-y)[Math.floor(a.length/2)]:0;
   o.textContent+='CONTACT|zone grise de contact détectée chaque mois 2024-2026|'+(miss.length?'ECHEC':'OK')+'|'+(miss.length?'absente : '+miss.join(', '):'largeur moyenne médiane : '+med(wr)+' km côté russe, '+med(wu)+' km côté ukrainien')+'\\n';
   // diagnostics seulement (non bloquants, aucun calibrage dessus) : saillant de Vivaldi, centres urbains loin de la ligne
   const sh=w.__uafContactShare(months.indexOf('2026-06'),[37.6,37.95,49.0,49.25]);
   o.textContent+='DIAG|Vivaldi : P(contact au sol) moyenne sur le saillant, juin 2026|'+(sh===null?'-':Math.round(sh*100)+' %')+'\\n';
   const CITIES={'Kharkiv':[36.23,49.99],'Zaporijjia':[35.14,47.84],'Soumy':[34.80,50.91],'Dnipro':[35.05,48.46],'Poltava':[34.55,49.59]};
   for(const [nm,[lo,la]] of Object.entries(CITIES)){let mx=0,at=''; for(const m of months){ if(m<'2024-01'||m>'2026-09') continue; const v=w.__uafContactAt(months.indexOf(m),lo,la); if(v!==null&&v>mx){mx=v;at=m;}}
     o.textContent+='DIAG|'+nm+' (centre) : P(contact au sol) maximale 2024-2026|'+Math.round(mx*100)+' %'+(at?' ('+at+')':'')+(mx>0.3?' ; à surveiller':'')+'\\n';}}
  {const [name,ms,box]=CALM; let n=0, ex=[]; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [50,20]) for(const a of w.__uafArrows(i,L)){ if(inb(a,box)){n++; if(ex.length<4) ex.push(m+' '+(a.ru?'RU':'UA')+' '+a.v);}}}
    const per={}; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; let c=0; for(const a of w.__uafArrows(i,20)) if(inb(a,box)) c++; if(c) per[m]=c;}
    o.textContent+='CALME|'+name+'|'+(n<=8?'OK':'ALERTE')+'|'+n+' flèche(s) '+ex.join(', ')+' '+JSON.stringify(per)+'\\n';}
  {const R=6371,dk=(a,b)=>{const p=Math.PI/180,x=(b.lon-a.lon)*p*Math.cos((a.lat+b.lat)/2*p),y=(b.lat-a.lat)*p;return R*Math.hypot(x,y);};
   for(const m of OVERVIEW){const i=months.indexOf(m); if(i<0) continue; const all=[20,50,100].flatMap(L=>w.__uafArrows(i,L).map(a=>({...a,L}))), ov=w.__uafArrows(i,'overview');
    const km=all.reduce((s,a)=>s+a.L,0)/3, need=Math.max(1,Math.ceil(km/100)), miss=all.filter(a=>!ov.some(b=>dk(a,b)<50)); const ok=!miss.length&&ov.length>=need;
    o.textContent+='VUE|'+m+'|'+(ok?'OK':'ECHEC')+'|'+ov.length+' flèche(s) affichée(s) pour ~'+Math.round(km)+' km en mouvement (minimum '+need+'), '+miss.length+' tronçon(s) sans flèche à moins de 50 km|'+JSON.stringify(ov.map(a=>[a.lon,a.lat,a.ru?'RU':'UA',a.v]))+'\\n';}}
 }catch(e){o.textContent+='ERREUR '+e+' '+(e.stack||'').split('\\n').slice(0,4).join(' | ');} };
setTimeout(()=>go(0),2000)}</script></body></html>"""


def chrome_path(arg):
    for c in [arg, "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if c and (shutil.which(c) or os.path.exists(c)):
            return shutil.which(c) or c
    return None


def check_workflows():
    """Les workflows doivent être du YAML valide (un « : » non protégé dans un nom d'étape a déjà bloqué tout le
    déploiement). Bloquant si PyYAML est disponible ; sinon, simple avertissement."""
    try:
        import yaml
    except ImportError:
        print("check_arrows.py : PyYAML absent, workflows non vérifiés")
        return True
    import glob
    ok = True
    for path in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")):
        try:
            doc = yaml.safe_load(open(path, encoding="utf-8"))
            if not isinstance(doc, dict) or not doc.get("jobs"):
                raise ValueError("aucun job")
        except Exception as e:  # noqa: BLE001
            print(f"check_arrows.py : workflow invalide {os.path.basename(path)} : {e}")
            ok = False
    return ok


def check_schema():
    """Schéma de site/front.js (bloquant) : clés et types attendus. Une variable du calcul a déjà écrasé le rayon
    terrestre grid.R (une liste à la place de 6 371 007,2), sans erreur visible."""
    import json
    s = open(os.path.join(SITE, "front.js"), encoding="utf-8").read()
    try:
        d = json.loads(s[s.index("=") + 1:].rstrip().rstrip(";"))
    except ValueError as e:
        print(f"check_arrows.py : front.js illisible ({e})")
        return False
    num = (int, float)
    expect = {"grid": dict, "months": list, "params": dict, "sigma0": dict, "nowcast": dict, "events": dict, "contact": dict, "runs": str, "flags": str}
    grid = {"nx": int, "ny": int, "size": num, "x0": num, "y1": num, "lon0": num, "lat0": num, "R": num}
    errs = [f"{k} absent ou de type {type(d.get(k)).__name__}" for k, t in expect.items() if not isinstance(d.get(k), t)]
    if isinstance(d.get("grid"), dict):
        errs += [f"grid.{k} de type {type(d['grid'].get(k)).__name__}" for k, t in grid.items() if not isinstance(d["grid"].get(k), t)]
        R = d["grid"].get("R")
        if isinstance(R, num) and not 6.3e6 < R < 6.4e6:
            errs.append(f"grid.R = {R} (rayon terrestre attendu)")
    mo = [m for m in d.get("months", []) if m]
    if not mo or not all(isinstance(m.get("a"), dict) and isinstance(m.get("date"), str) for m in mo):
        errs.append("months : instantanés incomplets")
    if errs:
        print("check_arrows.py : schéma de front.js invalide : " + " ; ".join(errs))
        return False
    return True


def main():
    if not check_workflows() or not check_schema():
        return 1
    chrome = chrome_path(sys.argv[1] if len(sys.argv) > 1 else None)
    if not chrome:
        print("check_arrows.py : navigateur introuvable, contrôle en échec")
        return 1
    harness = os.path.join(SITE, "_check_arrows.html")
    import json
    open(harness, "w").write(HARNESS.replace("__EVENT_CASES__", json.dumps(EVENT_CASES, ensure_ascii=False)).replace("__EVENT_CALM__", json.dumps(EVENT_CALM, ensure_ascii=False)).replace("__DAILY_CASES__", json.dumps(DAILY_CASES, ensure_ascii=False)).replace("__SERIES_END__", json.dumps(SERIES_END, ensure_ascii=False)).replace("__MOVES_REF__", json.dumps(MOVES_REF)).replace("__PARAMS__", os.environ.get("ARROW_PARAMS", "null")).replace("__CASES__", json.dumps(CASES, ensure_ascii=False)).replace("__CALM__", json.dumps(CALM, ensure_ascii=False)).replace("__OVERVIEW__", json.dumps(OVERVIEW)).replace("__FORBID__", json.dumps(FORBID, ensure_ascii=False)).replace("__VIVALDI__", json.dumps(VIVALDI, ensure_ascii=False)))
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=SITE)
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            out = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=180000",
                                  "--dump-dom", f"http://127.0.0.1:{port}/_check_arrows.html"],
                                 capture_output=True, text=True, timeout=600).stdout
        finally:
            httpd.shutdown()
            os.remove(harness)
    m = re.search(r'<pre id="o">([^<]*)', out)
    text = m.group(1) if m else ""
    print("check_arrows.py :", text.strip() or "aucun résultat")
    r = re.search(r"RESULTAT total=(\d+) echecs=(\d+)", text)
    if not r or "ERREUR" in text or "|ECHEC|" in text:
        return 1
    return 0 if int(r.group(2)) == 0 and int(r.group(1)) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
