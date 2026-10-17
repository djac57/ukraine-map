"""Récupère les annonces officielles de prises et de reprises de localités (aperçu web public de Telegram).

    python3 scripts/fetch_claims.py <dossier_brut>

- Ministère russe de la Défense (t.me/s/mod_russia) : messages contenant « освобожд… » ou « контроль над ».
- État-major ukrainien (t.me/s/GeneralStaffZSU) : messages contenant « звільн… » ou « контроль над ».
On ne conserve que l'identifiant, la date et le texte, en local (dossier brut, jamais publié) : seuls les
FAITS extraits (date, localité, coordonnées, source) sont publiés par build_claims.py. En droit russe,
l'article 1259 §6 du Code civil exclut du droit d'auteur les documents officiels des organes de l'État et
les simples informations sur des événements et des faits.
On parcourt le fil public page par page (la recherche de l'aperçu web ne pagine pas) ; seuls les messages
contenant un mot-clé sont gardés. Reprise incrémentale : un parcours interrompu reprend sous le plus bas
message atteint, et les passages suivants s'arrêtent au premier message déjà vu.
"""
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; ukraine-feux/1.0; non-commercial research)"
# mots-clés (radicaux) : on ne garde en local que les messages qui en contiennent un
CHANNELS = {
    "mod_russia": ["освобож", "освободил", "контроль над", "взят под контроль", "взяли под контроль"],
    "GeneralStaffZSU": ["звільн", "контроль над", "відбили", "повернули під контроль"],
}
START = "2022-02-24"
PAUSE = 2.0


def get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,uk;q=0.8,en;q=0.5"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"  erreur {url[:60]}… : {e} (essai {attempt + 1})", flush=True)
            time.sleep(20 * (attempt + 1))
    return ""


def parse(page, channel):
    out = []
    for block in re.split(r'<div class="tgme_widget_message_wrap', page)[1:]:
        m = re.search(rf'data-post="{channel}/(\d+)"', block)
        d = re.search(r'datetime="([^"]+)"', block)
        t = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', block, flags=re.S)
        if not (m and d and t):
            continue
        text = re.sub(r"<br\s*/?>", "\n", t.group(1))
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        out.append({"id": int(m.group(1)), "date": d.group(1)[:10], "text": text})
    return out


LOW = {"mod_russia": 12000, "GeneralStaffZSU": 0}  # identifiants antérieurs au 24 février 2022
WORKERS = 4


def crawl(raw, channel, keys):
    """Parcourt le fil public (?before=…) en plusieurs tranches d'identifiants en parallèle. Les tranches
    déjà parcourues (fichier .meta.json) sont sautées : seuls les messages postérieurs au dernier passage
    sont relus."""
    path = os.path.join(raw, "claims", f"{channel}.json")
    meta_path = path.replace(".json", ".meta.json")
    posts = {}
    if os.path.exists(path):
        posts = {p["id"]: p for p in json.load(open(path))}
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    done = [tuple(x) for x in meta.get("done", [])]   # intervalles [bas, haut] entièrement parcourus
    first = parse(get(f"https://t.me/s/{channel}"), channel)
    if not first:
        print(f"{channel} : aperçu inaccessible", flush=True)
        return
    top = max(p["id"] for p in first) + 1
    lo = LOW[channel]
    # intervalles restant à parcourir
    todo, cur = [], lo
    for a, b in sorted(done):
        if a > cur:
            todo.append((cur, a))
        cur = max(cur, b)
    if cur < top:
        todo.append((cur, top))
    chunks = []
    for a, b in todo:
        n = max(1, min(WORKERS, (b - a) // 2000 + 1))
        step = (b - a) / n
        chunks += [(int(a + i * step), int(a + (i + 1) * step)) for i in range(n)]
    lock = threading.Lock()
    finished = []

    def work(a, b):
        before, pages, complete = b, 0, False
        while True:
            batch = parse(get(f"https://t.me/s/{channel}?before={before}"), channel)
            pages += 1
            time.sleep(PAUSE)
            if not batch:
                complete = before <= a + 25  # page vide : fin de tranche, sinon erreur réseau (tranche à refaire)
                break
            with lock:
                for p in batch:
                    if any(k in p["text"].lower() for k in keys):
                        posts[p["id"]] = p
            oldest = min(p["id"] for p in batch)
            if oldest <= a or min(p["date"] for p in batch) < START or oldest >= before:
                complete = True
                break
            before = oldest
            if pages % 100 == 0:
                print(f"  {channel} [{a}-{b}] : {pages} pages, jusqu'au {min(p['date'] for p in batch)}", flush=True)
        with lock:
            if complete:
                finished.append((a, b))
            else:
                print(f"  {channel} [{a}-{b}] : tranche interrompue, à reprendre au prochain passage", flush=True)
            json.dump(sorted(posts.values(), key=lambda p: p["id"]), open(path, "w"), ensure_ascii=False)
            json.dump({"done": done + finished}, open(meta_path, "w"))
    threads = [threading.Thread(target=work, args=c) for c in chunks]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"{channel} : {len(chunks)} tranches parcourues, {len(posts)} messages retenus", flush=True)


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "raw"
    os.makedirs(os.path.join(raw, "claims"), exist_ok=True)
    threads = [threading.Thread(target=crawl, args=(raw, c, k)) for c, k in CHANNELS.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
