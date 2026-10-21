"""Assemblage des sorties de build_front.py calculées en parallèle (CI : une tâche par lot, FRONT_VIEWS).

    python3 scripts/front_parts.py stage <dossier>      # copie les fichiers listés par parts/manifest_*.txt dans <dossier>
    python3 scripts/front_parts.py assemble <dossier>   # remet ces fichiers en place et complète site/front.js

Lots (variable FRONT_VIEWS de build_front.py) :
- « vues » : les quatre vues, le nowcast et toutes les couches dérivées (front.js, front_<vue>.js, front_mcsd.js, front_areas.csv,
  front_calibration.csv) ; dans front.js, les surfaces des variantes (clé « v » de chaque instantané) et la sensibilité restent vides ;
- « sans_x,sans_y » : les variantes « sans X » (front_sans_x.js et parts/front_sans_x.json : surfaces par instantané) ;
- « sens » : l'analyse de sensibilité (front_sensibilite.csv et parts/front_sens.json).
Sans FRONT_VIEWS, build_front.py fait tout en un seul passage et ce script est inutile.

assemble : les fichiers du lot « vues » sont copiés tels quels ; les surfaces des variantes et la sensibilité sont insérées dans
site/front.js, dans l'ordre de build_front.VARIANTS, ce qui redonne exactement le front.js d'un passage unique (clé par date
d'instantané ; tout écart de dates entre lots est une erreur, code 3). Les empreintes anti-cache sont refaites par stamp.py.
"""
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "parts")


def stage(dest):
    n = 0
    for man in sorted(glob.glob(os.path.join(PARTS, "manifest_*.txt"))):
        for rel in open(man, encoding="utf-8").read().split("\n"):
            rel = rel.strip()
            if not rel:
                continue
            src = os.path.join(ROOT, rel)
            if not os.path.exists(src):
                print(f"front_parts.py : fichier absent {rel}")
                sys.exit(3)
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copyfile(src, out)
            n += 1
        out = os.path.join(dest, "parts", os.path.basename(man))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(man, out)
    print(f"front_parts.py : {n} fichiers copiés dans {dest}")


def assemble(src):
    import build_front as bm   # VARIANTS (ordre des clés) et meta_js (mêmes options d'écriture) ; rien n'est calculé à l'import
    mans = sorted(glob.glob(os.path.join(src, "parts", "manifest_*.txt")))
    if not mans:
        print("front_parts.py : aucun manifeste dans " + src)
        sys.exit(3)
    lots = set()
    n = 0
    for man in mans:
        lots.add(os.path.basename(man)[len("manifest_"):-4])
        for rel in open(man, encoding="utf-8").read().split("\n"):
            rel = rel.strip()
            if not rel:
                continue
            out = os.path.join(ROOT, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            srcf = os.path.join(src, rel)
            if not (os.path.exists(out) and os.path.samefile(srcf, out)):   # assemblage en place (test) : src == ROOT
                shutil.copyfile(srcf, out)
            n += 1
    print(f"front_parts.py : lots {sorted(lots)} ; {n} fichiers remis en place")
    missing = [v for v in bm.VARIANTS if v not in lots] + [x for x in ("vues", "sens") if x not in lots]
    if missing:
        print("front_parts.py : lots manquants : " + ", ".join(missing))
        sys.exit(3)
    path = os.path.join(ROOT, "site", "front.js")
    js = open(path, encoding="utf-8").read()
    meta = json.loads(js[len("window.FRONT="):-1])
    frames = [mo for mo in meta["months"] if mo]
    for name in bm.VARIANTS:
        part = json.load(open(os.path.join(src, "parts", f"front_{name}.json"), encoding="utf-8"))
        v = part["v"]
        dates = [mo["date"] for mo in frames]
        if sorted(v) != sorted(dates):
            print(f"front_parts.py : instantanés du lot {name} ({len(v)}) différents du lot vues ({len(dates)})")
            sys.exit(3)
        for mo in frames:
            mo["v"][name] = v[mo["date"]]
    meta["sensitivity"] = json.load(open(os.path.join(src, "parts", "front_sens.json"), encoding="utf-8"))
    js = bm.meta_js(meta)
    open(path, "w", encoding="utf-8").write(js)
    print(f"front_parts.py : front.js assemblé ({len(js) // 1024} Ko, {len(frames)} instantanés, {len(bm.VARIANTS)} variantes)")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("stage", "assemble"):
        print(__doc__)
        sys.exit(2)
    (stage if sys.argv[1] == "stage" else assemble)(sys.argv[2])
