"""Journal public de DeepStateMap : localités annoncées « libérées » ou « occupées », par date.

    python3 scripts/fetch_dsmlog.py <dossier_viina>

Pourquoi : la colonne DeepState de VIINA n'a pas suivi certaines mises à jour de la carte (contre-offensive au
nord de Lyman, 2026 : Serednie est « libérée » le 14 septembre selon DeepState, mais toujours codée russe par
VIINA un mois plus tard). Le journal de DeepState (deepstatemap.live/api/history/public) date chaque changement ;
build_front.py s'en sert pour corriger la colonne DeepState de VIINA, localité par localité.

On ne garde que des FAITS : date, sens du changement, nom de la localité et coordonnées du lien vers la carte
quand il en a (les liens récents ne portent qu'un identifiant interne : la localité est alors retrouvée par son
nom dans build_front.py, près du front). Le texte n'est pas conservé. Seuls les verbes de prise comptent :
« liberated » → ukrainien ; « occupied », « captured » → russe. Les avancées (« advanced », « pushed back »,
« regained control of areas near », « near ») ne disent pas qui tient la localité : elles annulent le verbe qui
précède. Sortie (brute, jamais publiée) : <dossier_viina>/dsm_log_raw.json.
"""
import html
import json
import os
import re
import sys
import urllib.request

import guard

UA = "Mozilla/5.0 (compatible; ukraine-feux/1.0; non-commercial research)"
URL = "https://deepstatemap.live/api/history/public"
LINK = re.compile(r'<a [^>]*href="[^"#]*#([^"]*)"[^>]*>(.*?)</a>', re.S)
COORD = re.compile(r"^\d+(?:\.\d+)?/(-?[\d.]+)/(-?[\d.]+)")
# verbes (anglais, sinon ukrainien) : le dernier vu avant un lien fixe le sens ; les autres l'annulent
VERB = re.compile(r"\b(liberated|occupied|captured|seized|took control of|near|in the area of|advanced|pushed back|"
                  r"regained|clarified|cleared|repelled|attacked|infiltrated|in the direction of)\b|"
                  r"(звільнили|звільнено|окупував|окупували|захопив|захопили|поблизу|біля|в районі|просунувся|"
                  r"просунулися|відкинули|відновили|уточнено|зачистили)", re.I)
SIDE = {"liberated": "ua", "звільнили": "ua", "звільнено": "ua", "occupied": "ru", "captured": "ru", "seized": "ru",
        "took control of": "ru", "окупував": "ru", "окупували": "ru", "захопив": "ru", "захопили": "ru"}


def parse(text):
    """Liens précédés d'un verbe de prise : (camp, nom, lat, lon) dans l'ordre du texte, et les coordonnées de
    tous les liens du message (contexte, pour situer les liens sans coordonnées)."""
    links = []
    for m in LINK.finditer(text):
        c = COORD.match(m.group(1))
        name = html.unescape(re.sub("<[^>]+>", "", m.group(2))).strip()
        links.append((m.start(), name, (float(c.group(1)), float(c.group(2))) if c else (None, None)))
    verbs = [(m.start(), (m.group(1) or m.group(2)).lower()) for m in VERB.finditer(re.sub(r"<a [^>]*>.*?</a>", lambda x: " " * len(x.group(0)), text, flags=re.S))]
    toks = sorted([(p, "v", v) for p, v in verbs] + [(p, "l", (n, ll)) for p, n, ll in links])
    out, side = [], None
    for _, kind, val in toks:
        if kind == "v":
            side = SIDE.get(val)
        elif side:
            out.append((side, val[0], val[1][0], val[1][1]))
    ctx = [ll for _, _, ll in links if ll[0] is not None]
    return out, ctx


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "viina"
    os.makedirs(src, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    hist = json.loads(urllib.request.urlopen(req, timeout=60).read())
    rows = []
    for h in hist:
        text = h.get("descriptionEn") or h.get("description") or ""
        found, ctx = parse(text)
        for side, name, lat, lon in found:
            rows.append({"date": h["createdAt"][:10], "camp": side, "name": name, "lat": lat, "lon": lon, "ctx": ctx})
    out = os.path.join(src, "dsm_log_raw.json")
    old = len(json.load(open(out, encoding="utf-8"))) if os.path.exists(out) else None
    guard.check("fetch_dsmlog.py", [("annonces datées", len(rows), old, "count")])
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    n = {s: sum(1 for r in rows if r["camp"] == s) for s in ("ua", "ru")}
    print(f"dsm_log_raw.json : {len(hist)} mises à jour, {len(rows)} localités datées "
          f"(libérées {n['ua']}, occupées {n['ru']}, dont {sum(1 for r in rows if r['lat'] is None)} sans coordonnées)")


if __name__ == "__main__":
    main()
