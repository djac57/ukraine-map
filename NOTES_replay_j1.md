# Notes agent — passage J-1 (textes) + replay quotidien (`site/app.js`)

Fichier de remarques personnel (je ne touche pas `docs/ETAT.md`). Date : 2026-10-22.

## 1. Textes J-7 → J-1 (fait)
- `site/app.js` : commentaires et notes de couches « règle des 7 jours » → « règle J-1 », « mois complet + 7 jours » → « mois complet + 1 jour ». Laissés tels quels : le « 7 jours » du regroupement **par semaine** (vue hebdo, `playSteps` g==="w") et le **retard de documentation** WarSpotting (sans rapport avec le décalage de publication).
- `site/methode.html` : déjà à jour (« J-1 ... ; J-7 auparavant/jusqu'au 19 octobre 2026 ») — cadrage historique correct, **non modifié**.

## 2. Replay quotidien (fait, dans `site/app.js`)
- Bouton **⟲ replay** injecté dans `.controls` (index.html non modifié), barre d'options dessous (pas **par mois**/par jour, case **« ce que le modèle savait ce jour-là »**).
- Données : `front_fusion_hist_<année>.js` (`FRONT_FUSION_HIST[année].{keys,months[].a}` + `FRONTH["<année>/fusion"]`, champ base64 même format que `FRONTP`), **chargées par année à la demande** au scrub, avec préchargement de l'année suivante.
- Vue **mensuelle par défaut** (axe `D.months` ≥ 2022-03, instantané = dernier jour ≤ fin de mois) ; granularité **jour** = axe calendaire 2022-03-01→J-1.
- **Lissage** : moyenne glissante causale du champ (21 j en mensuel, 7 j en journalier) pour éviter le scintillement du réancrage ; l'option « ce que le modèle savait ce jour-là » montre le champ **brut** du jour (W=1).
- Rendu : réutilise `buildFront`/`drawFront` via un pseudo-champ `__replay` (hooks dans `fieldName`/`frontIdx`/`viewFields`, indice fixe `REPLAY.fi`). En replay, toutes les couches overlay sont neutralisées (champ de contrôle + ligne seulement) et restaurées à la sortie ; la légende et les cases sont accordées. Testé en Chrome headless : 0 erreur JS, rendu correct (invasion mars 2022, saillant de Koursk en sept. 2024, etc.).

## 3. À faire côté AUTRES agents (hors de mes fichiers)
- **`i18n.js`** (pas à moi) : ajouter les clés FR→EN/UK/RU des nouvelles chaînes replay et des notes modifiées :
  `⟲ replay`, `Rejouer toute la guerre jour par jour`, `par mois`, `par jour`, `pas du replay`,
  `ce que le modèle savait ce jour-là`, `replay · chargement {y}…`, `replay : données indisponibles pour {y}`,
  `{v} km² occupés`, `champ du jour (brut)`, `lissé`,
  et `(mois complet + 1 jour)`, `(règle J-1)` si elles étaient traduites. Sans clé, repli FR (corrigé) — acceptable.
- **`.github/update.yml`** (pas à moi) : `FUSION_HIST=jours` pour régénérer `front_fusion_hist_<année>.js` à chaque build (cf. ETAT §6, 21/10).
- Les fichiers `front_hist_<année>*.js` (territoire par carte, 6 bits) ne sont **pas** utilisés par le replay (pas de champ de probabilité par jour) ; seul `front_fusion_hist_*` porte le champ. Si on veut un replay « cartes seules », il faudra produire le champ équilibré par jour.
