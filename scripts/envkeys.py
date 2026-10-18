"""Jetons d'accès lus depuis l'environnement (secrets GitHub Actions), jamais écrits ni affichés.

    from envkeys import secret, auth_header
    tok = secret("ALERTS_IN_UA_TOKEN")      # None si absent : le script appelant saute l'étape proprement

Règles : la valeur n'est jamais imprimée, journalisée, écrite dans un fichier ni placée dans une URL ; seuls le nom
du secret et sa présence (oui / non) peuvent apparaître dans un journal. Les messages d'erreur réseau sont nettoyés
de toute occurrence de la valeur avant affichage (scrub).
"""
import os

KNOWN = ("EARTHDATA_TOKEN", "ALERTS_IN_UA_TOKEN", "FIRMS_MAP_KEY")


def secret(name):
    v = os.environ.get(name, "").strip()
    return v or None


def auth_header(name):
    """En-tête HTTP « Authorization: Bearer … » si le secret existe, sinon {}."""
    v = secret(name)
    return {"Authorization": f"Bearer {v}"} if v else {}


def scrub(text):
    """Retire de `text` toute valeur de secret connue (pour les messages d'erreur)."""
    s = str(text)
    for n in KNOWN:
        v = secret(n)
        if v:
            s = s.replace(v, "***")
    return s


def present(name):
    return "oui" if secret(name) else "non"
