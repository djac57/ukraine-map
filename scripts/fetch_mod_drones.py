"""Bilans du MoD RF « drones ukrainiens interceptés au-dessus des régions » : aperçu public Telegram (t.me/s/mod_russia,
sans compte), messages contenant « над территорией / над акваторией ». Messages gardés en local (<brut>/drones/claims),
jamais publiés ; seuls des comptes par région et par mois sortent (build_strikes_ru.py).
    python3 scripts/fetch_mod_drones.py <brut> [--recent]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_claims as F  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = os.path.join(args[0] if args else "raw", "drones")
    os.makedirs(os.path.join(raw, "claims"), exist_ok=True)
    # en CI (cache vide) : seulement les ~4 000 derniers messages (≈ 2 à 3 mois) ; build_strikes_ru.py garde les mois
    # plus anciens déjà publiés. En local, tout l'historique (LOW de fetch_claims).
    if "--recent" in sys.argv:
        first = F.parse(F.get("https://t.me/s/mod_russia"), "mod_russia")
        if first:
            F.LOW["mod_russia"] = max(F.LOW["mod_russia"], max(p["id"] for p in first) - 4000)
    F.crawl(raw, "mod_russia", ["над территорией", "над акваторией", "над территориями"])


if __name__ == "__main__":
    main()
