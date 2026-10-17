"""Contrôle automatique des flèches « qui pousse où », dans un navigateur sans interface.

    python3 scripts/check_arrows.py [chemin_chrome]

Pour chaque flèche de chaque mois et de chaque échelle (100, 50, 20 et 10 km) : la base doit être à moins de
2 km de la ligne 50 % du mois affiché, et la flèche à moins de 30° de la normale locale de cette ligne.
Rappel : chaque cas connu (Avdiïvka, Otcheretyne, Vovtchansk, Koursk à l'expansion et au reflux, Niou-York/Toretsk,
Vouhledar, Selydove/Kourakhove, Pokrovsk, Koupiansk) doit avoir au moins une flèche dans le bon sens, et Koursk aucune
flèche rouge pendant l'expansion (août 2024) ni bleue pendant le reflux (novembre 2024 - mars 2025). Faux positifs : la
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
# Calme : à partir d'avril 2023 (la fenêtre de trois mois ne contient plus le retrait russe de novembre 2022) ;
# tolérance de 8 flèches sur 33 mois et deux échelles (révisions ponctuelles des cartes sur les îles du delta)
# Mois actifs pour le contrôle de la vue d'ensemble
OVERVIEW = ["2024-02", "2024-05", "2024-08", "2024-11", "2025-03", "2025-08"]
CALM = ("Dnipro à Kherson", [f"{y}-{m:02d}" for y in (2023, 2024, 2025) for m in range(1, 13) if f"{y}-{m:02d}" >= "2023-04"], [32.30, 33.90, 46.40, 47.00])
HARNESS = """<!doctype html><html><head><meta charset="utf-8"></head><body><pre id=o></pre><iframe id=f src="index.html" style="width:1000px;height:800px"></iframe>
<script>const CASES=__CASES__, CALM=__CALM__, OVERVIEW=__OVERVIEW__, FORBID=__FORBID__;
const okDir=(a,dir)=>{if(!dir) return true; if(Array.isArray(dir)) return a.dir[0]*dir[0]+a.dir[1]*dir[1]>0;
 const c=dir.vers||dir.depuis, tx=(c[0]-a.lon)*Math.cos(a.lat*Math.PI/180), ty=-(c[1]-a.lat), d=a.dir[0]*tx+a.dir[1]*ty; return dir.vers? d>0 : d<0;};
f.onload=()=>{const w=f.contentWindow;w.onerror=(m,u,l)=>o.textContent+='ERREUR '+m+' @'+l+'\\n';
const go=(n)=>{ if((!w.FRONTP||!w.FRONTP.mcsd||!w.__uafArrowCheck||!w.__uafArrows)&&n<60){return setTimeout(()=>go(n+1),500);}
 try{const r=w.__uafArrowCheck(); o.textContent+='RESULTAT total='+r.total+' echecs='+r.fails.length+' '+JSON.stringify(r.fails.slice(0,20))+'\\n';
  const months=w.DATA.months, inb=(a,b)=>a.lon>=b[0]&&a.lon<=b[1]&&a.lat>=b[2]&&a.lat<=b[3];
  for(const [name,ms,box,ru,dir] of CASES){let hit=null,n=0;
    for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [100,50,20]) for(const a of w.__uafArrows(i,L)){ if(!inb(a,box)) continue; n++;
      if(a.ru===ru && okDir(a,dir) && !hit) hit=m+' L'+L+' '+a.v+' km/mois';}}
    o.textContent+='CAS|'+name+'|'+(hit?'OK':'ECHEC')+'|'+(hit||'aucune flèche dans le bon sens')+'|'+n+'\\n';}
  for(const [name,ms,box,ru] of FORBID){const bad=[]; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [100,50,20,'overview']) for(const a of w.__uafArrows(i,L)) if(inb(a,box)&&a.ru===ru) bad.push(m+' L'+L+' '+a.v);}
    o.textContent+='CAS|'+name+'|'+(bad.length?'ECHEC':'OK')+'|'+(bad.length?bad.slice(0,4).join(', '):'aucune')+'|'+bad.length+'\\n';}
  {const [name,ms,box]=CALM; let n=0, ex=[]; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; for(const L of [50,20]) for(const a of w.__uafArrows(i,L)){ if(inb(a,box)){n++; if(ex.length<4) ex.push(m+' '+(a.ru?'RU':'UA')+' '+a.v);}}}
    const per={}; for(const m of ms){const i=months.indexOf(m); if(i<0) continue; let c=0; for(const a of w.__uafArrows(i,20)) if(inb(a,box)) c++; if(c) per[m]=c;}
    o.textContent+='CALME|'+name+'|'+(n<=8?'OK':'ECHEC')+'|'+n+' flèche(s) '+ex.join(', ')+' '+JSON.stringify(per)+'\\n';}
  {const R=6371,dk=(a,b)=>{const p=Math.PI/180,x=(b.lon-a.lon)*p*Math.cos((a.lat+b.lat)/2*p),y=(b.lat-a.lat)*p;return R*Math.hypot(x,y);};
   for(const m of OVERVIEW){const i=months.indexOf(m); if(i<0) continue; const all=[20,50,100].flatMap(L=>w.__uafArrows(i,L).map(a=>({...a,L}))), ov=w.__uafArrows(i,'overview');
    const km=all.reduce((s,a)=>s+a.L,0)/3, need=Math.max(1,Math.ceil(km/100)), miss=all.filter(a=>!ov.some(b=>dk(a,b)<50)); const ok=!miss.length&&ov.length>=need;
    o.textContent+='VUE|'+m+'|'+(ok?'OK':'ECHEC')+'|'+ov.length+' flèche(s) affichée(s) pour ~'+Math.round(km)+' km en mouvement (minimum '+need+'), '+miss.length+' tronçon(s) sans flèche à moins de 50 km|'+JSON.stringify(ov.map(a=>[a.lon,a.lat,a.ru?'RU':'UA',a.v]))+'\\n';}}
 }catch(e){o.textContent+='ERREUR '+e;} };
setTimeout(()=>go(0),2000)}</script></body></html>"""


def chrome_path(arg):
    for c in [arg, "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if c and (shutil.which(c) or os.path.exists(c)):
            return shutil.which(c) or c
    return None


def main():
    chrome = chrome_path(sys.argv[1] if len(sys.argv) > 1 else None)
    if not chrome:
        print("check_arrows.py : navigateur introuvable, contrôle en échec")
        return 1
    harness = os.path.join(SITE, "_check_arrows.html")
    import json
    open(harness, "w").write(HARNESS.replace("__CASES__", json.dumps(CASES, ensure_ascii=False)).replace("__CALM__", json.dumps(CALM, ensure_ascii=False)).replace("__OVERVIEW__", json.dumps(OVERVIEW)).replace("__FORBID__", json.dumps(FORBID, ensure_ascii=False)))
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
