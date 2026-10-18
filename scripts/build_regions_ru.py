"""Régions russes dans l'emprise de la carte (Natural Earth admin-1, domaine public), pour la couche « frappes
ukrainiennes signalées » : site/regions_ru.js = {id ISO 3166-2, nom français, clés de reconnaissance russes (radical du
nom au génitif, comme dans « над территорией Брянской области »), géométrie simplifiée}.
    python3 scripts/build_regions_ru.py <dossier des geojson Natural Earth>
"""
import json
import os
import sys

sys.argv = sys.argv[:2]
import build_geo as BG  # noqa: E402  (lit SRC = sys.argv[1])

# id : (nom français, radicaux russes reconnus dans les bilans)
RU = {"RU-BRY": ("Briansk", ["брянск"]), "RU-KRS": ("Koursk", ["курск"]), "RU-BEL": ("Belgorod", ["белгород"]),
      "RU-VOR": ("Voronej", ["воронеж"]), "RU-ROS": ("Rostov", ["ростов"]), "RU-KDA": ("Krasnodar", ["краснодар"]),
      "RU-ORL": ("Orel", ["орловск"]), "RU-LIP": ("Lipetsk", ["липецк"]), "RU-TAM": ("Tambov", ["тамбов"]),
      "RU-KLU": ("Kalouga", ["калужск"]), "RU-TUL": ("Toula", ["тульск"]), "RU-SMO": ("Smolensk", ["смоленск"]),
      "RU-RYA": ("Riazan", ["рязанск"]), "RU-VGG": ("Volgograd", ["волгоград"]), "RU-STA": ("Stavropol", ["ставропол"]),
      "RU-AD": ("Adyguée", ["адыге"]), "RU-KL": ("Kalmoukie", ["калмыки"]), "RU-SAR": ("Saratov", ["саратов"]),
      "RU-PNZ": ("Penza", ["пензенск"]), "RU-MOS": ("oblast de Moscou", ["московской"]), "RU-MOW": ("Moscou", ["москв"])}


def main():
    out = []
    for f in BG.load("ne_10m_admin_1_states_provinces"):
        p = f["properties"]
        code = p.get("iso_3166_2")
        if p.get("adm0_a3") != "RUS" or code not in RU:
            continue
        g = BG.poly_geom(f["geometry"], 0.006, 3, clip=True) if BG.bbox_hit(f["geometry"]) else None
        out.append(BG.feat(g, {"id": code, "n": RU[code][0], "k": RU[code][1]}))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = "window.REGIONS_RU=" + json.dumps({"type": "FeatureCollection", "features": out}, ensure_ascii=False, separators=(",", ":")) + ";"
    open(os.path.join(root, "site", "regions_ru.js"), "w", encoding="utf-8").write(js)
    print(f"regions_ru.js : {len(js) // 1024} Ko, {len(out)} régions ({sum(1 for f in out if f['geometry'])} dans l'emprise)")


if __name__ == "__main__":
    main()
