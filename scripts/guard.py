"""Garde-fous anti-régression : ne jamais remplacer une sortie publiée par une version plus pauvre.

Chaque script de construction compare ses nouveaux chiffres à ceux de la version déjà présente dans
site/ (celle qui est versionnée et servie). En cas de recul, il n'écrit rien et sort avec le code 3 :
la version précédente reste en ligne, et l'étape suivante de la CI peut continuer.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOLERANCE = 0.05


def published(name, var):
    """Contenu de site/<name> (window.<var>=…;), ou None s'il n'existe pas encore."""
    path = os.path.join(ROOT, "site", name)
    if not os.path.exists(path):
        return None
    src = open(path, encoding="utf-8").read()
    try:
        return json.loads(src[len(f"window.{var}="):].rstrip().rstrip(";"))
    except ValueError:
        return None


def abort(script, reason):
    print(f"{script} : ABANDON, {reason}. La version publiée est conservée.", flush=True)
    sys.exit(3)


def require_files(script, paths):
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        abort(script, "fichiers manquants : " + ", ".join(os.path.basename(p) for p in missing))


def check(script, checks):
    """checks : liste de (libellé, nouveau, ancien, genre) ; genre 'count' (tolérance de 5 %) ou 'max' (pas de recul)."""
    for label, new, old, kind in checks:
        if old is None:
            continue
        if new is None:
            abort(script, f"{label} absent (publié : {old})")
        if kind == "count" and new < old * (1 - TOLERANCE):
            abort(script, f"{label} en recul de plus de {TOLERANCE:.0%} ({new} contre {old} publiés)")
        if kind == "max" and new < old:
            abort(script, f"{label} en recul ({new} contre {old} publié)")
