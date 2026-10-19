"""Met à jour les empreintes anti-cache (?v=) de tous les scripts locaux dans les pages de site/.

    python3 scripts/stamp.py

Appelé à la fin de chaque script de construction ; à relancer aussi après avoir modifié site/app.js.
"""
import glob
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")


def digest(name):
    path = os.path.join(SITE, name)
    return hashlib.sha1(open(path, "rb").read()).hexdigest()[:10] if os.path.exists(path) else None


def main():
    front_v = {}
    for path in glob.glob(os.path.join(SITE, "front_*.js")):
        front_v[os.path.basename(path)[6:-3]] = digest(os.path.basename(path))
    # couches chargées à la demande par app.js avec FRONT_V.tg / FRONT_V.strikes (mises à jour chaque jour en CI)
    for key, name in (("tg", "telegram_troncons.js"), ("tgc", "telegram_cells.js"), ("strikes", "strikes_ru.js")):
        if digest(name):
            front_v[key] = digest(name)
    for page in glob.glob(os.path.join(SITE, "*.html")):
        src = open(page, encoding="utf-8").read()
        new = re.sub(r'src="([\w.-]+\.js)(\?v=[\w-]*)?"',
                     lambda m: f'src="{m.group(1)}?v={digest(m.group(1))}"' if digest(m.group(1)) else m.group(0), src)
        new = re.sub(r'href="([\w.-]+\.css)(\?v=[\w-]*)?"',
                     lambda m: f'href="{m.group(1)}?v={digest(m.group(1))}"' if digest(m.group(1)) else m.group(0), new)
        new = re.sub(r'const FRONT_V = \{[^}]*\}', "const FRONT_V = " + json.dumps(front_v, sort_keys=True), new)
        if digest("basemap.js"):
            new = re.sub(r'const BASEMAP_V = "[\w-]*"', f'const BASEMAP_V = "{digest("basemap.js")}"', new)
        if new != src:
            open(page, "w", encoding="utf-8").write(new)
    # app.js contient FRONT_V / BASEMAP_V : on le traite en dernier, puis on restampe les pages
    app = os.path.join(SITE, "app.js")
    if os.path.exists(app):
        src = open(app, encoding="utf-8").read()
        new = re.sub(r'const FRONT_V = \{[^}]*\}', "const FRONT_V = " + json.dumps(front_v, sort_keys=True), src)
        if digest("basemap.js"):
            new = re.sub(r'const BASEMAP_V = "[\w-]*"', f'const BASEMAP_V = "{digest("basemap.js")}"', new)
        if new != src:
            open(app, "w", encoding="utf-8").write(new)
        d = digest("app.js")
        for page in glob.glob(os.path.join(SITE, "*.html")):
            s = open(page, encoding="utf-8").read()
            s2 = re.sub(r'src="app\.js(\?v=[\w-]*)?"', f'src="app.js?v={d}"', s)
            if s2 != s:
                open(page, "w", encoding="utf-8").write(s2)


if __name__ == "__main__":
    main()
