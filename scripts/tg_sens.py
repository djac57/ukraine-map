"""Classement de l'attaquant dans un message de combat (règles lexicales RU/UA, perspective du camp de la source).
Copie du module de recherche ukraine-recherche/signals3/sens.py (19/10/2026), utilisé par build_telegram_layer.py :
classify_strict (tournures explicites) et presence. Ne rien changer ici sans refaire la validation de précision.

classify(texte, camp) → "ua" (attaque ou contre-attaque ukrainienne), "ru" (attaque russe), "sq" (défense, statu quo), None (indéterminé).
Indices, phrase par phrase :
  - acteur explicite + verbe d'attaque : ВСУ/ЗСУ/Сили оборони… → ua ; ВС РФ/окупанти/рашисти… → ru ;
  - « противник / враг / ворог / неприятель » + verbe d'attaque → camp opposé à la source ;
  - « наши / наші » + verbe d'attaque ou de progression → camp de la source ;
  - repousser une attaque (отбили, отразили, відбили, зупинили…) → camp opposé à la source (c'est lui qui attaquait),
    sauf si l'acteur repoussé est nommé ;
  - repli (отошли, отступили, залишили…) des « nôtres » → camp opposé ; de l'ennemi → camp de la source ;
  - « освободили » (source russe) → ru ; « звільнили / деокупували » (source ukrainienne) → ua ;
  - négation (« не смогли », « безуспешно », « не вдалося ») : l'attaque reste comptée pour celui qui attaquait.
Statu quo : « без изменений », « позиционные бои », « утримують » sans indice d'attaque.
Pour une source « tiers », seuls les acteurs explicites comptent.
"""
import re

UA_ACT = r"(всу|зсу|вфу|укров|укро-|хохл|киевск\w* режим\w*|украинск\w* (?:войск|формирован|подразделен|военн|боевик|армi|арми|сил)\w*|" \
         r"сил[иы] оборони|сил оборони|силы обороны|наші (?:воїни|захисники|бійці|підрозділи|сили)|українськ\w* (?:військ|підрозділ|сил)\w*|" \
         r"ukrainian (?:forces|troops|army)|\bafu\b|\buaf\b)"
RU_ACT = r"(вс рф|рф\b|кацап\w*|москал\w*|московит\w*|российск\w* (?:войск|подразделен|военн|арми|сил|штурмов)\w*|русск\w* (?:войск|штурмов|арми)\w*|" \
         r"наши (?:войска|бойцы|штурмовики|подразделения|парни|военные)|окупант\w*|рашист\w*|орк[иа]?\b|росіян\w*|російськ\w* (?:військ|підрозділ|окупант|армі)\w*|" \
         r"russian (?:forces|troops|army))"
ENEMY = r"(противник\w*|враг\w*|ворог\w*|неприятел\w*)"
OURS = r"(наши|наших|нашим|наші|наших)"
ATTACK = r"(атак\w*|атаку\w*|наступ\w*|штурм\w*|прорв\w*|прорыв\w*|продвин\w*|продвиж\w*|просун\w*|просув\w*|вклин\w*|контратак\w*|" \
         r"контрнаступ\w*|зашл\w*|захват\w*|захоп\w*|взял\w*|выбил\w*|вибил\w*|відкину\w*|отброс\w*|переш\w* в наступ\w*|" \
         r"advance\w*|attack\w*|assault\w*|counteroffensive|counterattack\w*|push\w*)"
REPEL = r"(отбил\w*|отбит\w*|отбива\w*|отраз\w*|отражен\w*|відбил\w*|відбит\w*|відбива\w*|зупинил\w*|зупинен\w*|остановил\w*|repel\w*|repuls\w*)"
RETREAT = r"(отош\w*|отступ\w*|оставил\w*|покинул\w*|відійш\w*|відступ\w*|залишил\w*|withdr\w*|retreat\w*)"
SQ = r"(без изменений|без змін|позиционн\w*|позиційн\w*|удержива\w*|утримую\w*|стабильн\w*|стабільн\w*)"
LIB_RU = r"(освободил\w*|освобожден\w*|взяли под контроль|зачистил\w*)"
LIB_UA = r"(звільнил\w*|звільнен\w*|деокупов\w*|визволил\w*|підняли прапор|подняли флаг украин\w*)"


def _has(p, s):
    return re.search(p, s) is not None


def _pos(p, s):
    m = re.search(p, s)
    return m.start() if m else None


# v2 (après annotation de l'échantillon de mise au point) :
#  - le message doit contenir un indice de combat au sol (sinon : frappes, politique → None) ;
#  - « ворог / противник / кацапи / москалі… » = acteur ennemi selon le camp de la source ;
#  - deux acteurs dans la phrase : l'attaquant est le premier nommé (sujet), sauf avec un verbe « repousser »
#    (c'est alors le second, celui qui a été repoussé) ;
#  - « наши / наші » ne compte qu'avec un verbe de progression (просунулися, продвинулись, звільнили…) ;
#  - repli ou perte de positions (не утримують, оставили, отошли…) : le camp qui recule est le défenseur.
GROUND = r"(штурм|наступ|продвин|продвиж|просун|просув|контратак|контрнаступ|прорв|прорыв|бои за|бої за|\bбо[йї]\b|\bбоя|боестолкн|боєзіткн|" \
         r"зачист|освобо|звільн|визвол|окруж|оточ|вклин|витісн|вытесн|відтісн|оттесн|відкину|отброс|захват|захоп|инфильтр|інфільтр|" \
         r"просач|просоч|дрг|assault|advance|counteroffensive|counterattack|infiltrat|offensive|pushed)"
ENEMY_W = r"(противник\w*|враг\w*|вражеск\w*|ворог\w*|ворож\w*|неприятел\w*|кацап\w*|москал\w*|московит\w*|окупант\w*|рашист\w*)"
PROGRESS = r"(продвинул\w*|продвиже\w*|просунул\w*|просуван\w*|звільнил\w*|освободил\w*|зачистил\w*|закрепил\w*|закріпил\w*|взяли\w*|выбили|вибили)"
ATTACK2 = ATTACK[:-1] + r"|витісн\w*|вытесн\w*|відтісн\w*|оттесн\w*|відкинул\w*)"
RETREAT2 = r"(отош\w*|отступ\w*|оставил\w*|покинул\w*|відійш\w*|відступ\w*|залишил\w*|не утриму\w*|не удержива\w*|больше не удерживают|withdr\w*|retreat\w*)"


# v3 : sujet (nominatif) / objet (cas obliques) / attaque « possédée » (атака противника = attaque de l'ennemi).
NOM = {
    "ua": r"(украинские (?:формирования|войска|подразделения|силы|военные|боевики)|\bхохлы\b|укронацисты|"
          r"\bзсу\b|сили оборони|наші (?:воїни|захисники|бійці|підрозділи)|українські (?:військові|воїни|підрозділи)|\bзахисники\b|"
          r"ukrainian (?:forces|troops|units|army))",
    "ru": r"(вс рф|вс россии|российские (?:войска|подразделения|военные|штурмовики)|российская армия|наши (?:войска|бойцы|штурмовики|подразделения|парни)|"
          r"штурмовые (?:отряды|группы|подразделения)|\bштурмовики\b|чвк|\bокупанти\b|\bросіяни\b|\bкацапи\b|\bмосковити\b|\bрашисти\b|\bорки\b|"
          r"russian (?:forces|troops|units|army))",
}
OBL = {
    "ua": r"(украинских (?:формирований|войск|подразделений|сил|военных)|"
          r"\bзсу\b|сил оборони|наших (?:захисників|позицій|воїнів)|українських (?:військ|позицій|підрозділів))",
    "ru": r"(российских (?:войск|подразделений|позиций)|\bвс рф\b|\bокупантів\b|\bросіян\b|\bкацапів\b|\bмоскалів\b|\bрашистів\b|"
          r"позиции вс рф|наших позиций)",
}
VSU = r"\bвсу\b"
GEN_NOM = r"(\bпротивник\b|\bвраг\b|\bнеприятель\b|\bворог\b|\bсупротивник\b)"
GEN_OBL = r"(\bпротивника\b|\bпротивнику\b|\bпротивником\b|\bврага\b|\bврагу\b|\bнеприятеля\b|\bворога\b|\bворогу\b|\bсупротивника\b)"
VSU_OBJ_BEFORE = r"(позици\w*|опорн\w*|укреп\w*|штурм\w*|\bпо\b|\bна\b|против|\bиз\b|\bу\b|\bс\b|выбил\w*|отброс\w*|оттесн\w*|группировк\w*|подразделени\w*|бригад\w*)\s+(?:\w+\s+){0,2}$"
ATT_NOUN = r"(атак\w*|наступлени\w*|наступ\w*|штурм\w*|контратак\w*|контрнаступ\w*|прорыв\w*|вылазк\w*|спроб\w* штурм\w*)\s+(?:\w+\s+){0,1}"
POSS = {"ua": ATT_NOUN + r"(всу|украинских|зсу\b|ukrainian)", "ru": ATT_NOUN + r"(вс рф|российских|русских|окупантів|росіян|кацапів|рф\b|russian)"}
POSS_GEN = ATT_NOUN + r"(противника|врага|ворога|неприятеля)"


def _actors(s, camp):
    """Positions des acteurs sujets et objets, par camp."""
    subj, obj = [], []
    for side in ("ua", "ru"):
        for m in re.finditer(NOM[side], s):
            subj.append((m.start(), side))
        for m in re.finditer(OBL[side], s):
            obj.append((m.start(), side))
    if camp in ("ua", "ru"):
        en = "ru" if camp == "ua" else "ua"
        for m in re.finditer(GEN_NOM, s):
            subj.append((m.start(), en))
        for m in re.finditer(GEN_OBL, s):
            obj.append((m.start(), en))
    for m in re.finditer(VSU, s):
        pre = s[max(0, m.start() - 40):m.start()]
        (obj if re.search(VSU_OBJ_BEFORE, pre) else subj).append((m.start(), "ua"))
    # dans une source, « противник / ворог » désigne toujours l'autre camp : on corrige le camp des mots génériques
    return sorted(subj), sorted(obj)


def classify(text, camp):
    t = text.lower().replace("ё", "е").replace("’", "'")
    if not _has(GROUND, t):
        return None
    opp = {"ru": "ua", "ua": "ru"}.get(camp)
    score = {"ua": 0, "ru": 0}
    sq = False
    for s in re.split(r"(?<=[.!?…;\n])\s+", t):
        att, rep, ret = _has(ATTACK2, s), _has(REPEL, s), _has(RETREAT2, s)
        poss = [side for side in ("ua", "ru") if re.search(POSS[side], s)]
        if camp in ("ua", "ru") and re.search(POSS_GEN, s):
            poss = sorted(set(poss) | {opp})
        subj, obj = _actors(s, camp)
        if len(poss) == 1:
            score[poss[0]] += 2               # « атака противника отбита », « наступ ворога »
        elif subj and (att or rep or ret):
            side = subj[0][1]
            other = "ru" if side == "ua" else "ua"
            if rep or ret:
                score[other] += 1             # le sujet repousse ou recule : l'autre attaque
            else:
                score[side] += 2
        elif obj and att:
            side = obj[0][1]
            score["ru" if side == "ua" else "ua"] += 1   # on attaque, chasse, encercle l'objet
        elif camp in ("ru", "ua"):
            if rep:
                score[opp] += 1
            if _has(OURS, s) and _has(PROGRESS, s):
                score[camp] += 1
            if _has(OURS, s) and ret:
                score[opp] += 1
        if camp == "ru" and _has(LIB_RU, s):
            score["ru"] += 1
        if camp == "ua" and _has(LIB_UA, s):
            score["ua"] += 1
        if _has(SQ, s):
            sq = True
    if score["ua"] > score["ru"]:
        return "ua"
    if score["ru"] > score["ua"]:
        return "ru"
    return "sq" if sq else None


# ---------- présence signalée (sans assaut explicite) ----------
PRES = r"(дрг|диверс\w*|инфильтр\w*|інфільтр\w*|просачива\w*|просочува\w*|просочил\w*|просочил\w*|замечен\w*|обнаружен\w*|выявлен\w*|" \
       r"помічен\w*|виявлен\w*|присутстви\w*|присутн\w*|разведгрупп\w*|розвідгруп\w*|infiltrat\w*|sabotage group\w*|spotted)"


def presence(text, camp):
    """Camp dont la présence est signalée : « ua » ou « ru », sinon None.
    Source russe : présence de l'ennemi (ВСУ, противник, ДРГ ВСУ) → ua ; source ukrainienne : ворог, окупанти, ДРГ → ru.
    Un acteur explicite l'emporte ; « tiers » : acteur explicite seulement."""
    t = text.lower().replace("ё", "е").replace("’", "'")
    found = {"ua": 0, "ru": 0}
    for s in re.split(r"(?<=[.!?…;\n])\s+", t):
        if not _has(PRES, s):
            continue
        if _has(UA_ACT, s) and not _has(RU_ACT, s):
            found["ua"] += 1
        elif _has(RU_ACT, s) and not _has(UA_ACT, s):
            found["ru"] += 1
        elif camp in ("ru", "ua") and (_has(ENEMY, s) or "дрг" in s or "інфільтр" in s or "инфильтр" in s or "просоч" in s or "просачив" in s):
            found["ua" if camp == "ru" else "ru"] += 1
    if found["ua"] > found["ru"]:
        return "ua"
    if found["ru"] > found["ua"]:
        return "ru"
    return None


# ---------- v4 « strict » : tournures explicites seulement (précision avant le rappel) ----------
_W = r"(?:\s+[\w\-]+){0,3}?\s+"
STRICT = {
    # attaque ou contre-attaque ukrainienne
    "ua": [
        r"контратак\w*" + r"(?:\s+[\w\-]+){0,2}?\s+(?:всу|противника|врага|украинских)",
        r"(?:всу|противник|враг|украинские формирования|украинские войска|хохлы)" + _W +
        r"(?:пытаются|пытался|пытались|предпринял\w*|атаковал\w*|атакуют|атакует|наступают|наступает|перешл\w* в наступление|продвинул\w*|прорвал\w*|вклинил\w*|контратаков\w*|контратакуют|зашл\w*|вошл\w*|закрепил\w*)",
        r"(?:наступлени\w*|контрнаступлени\w*|прорыв\w*|вылазк\w*|вклинени\w*)\s+(?:\w+\s+)?(?:всу|противника|врага|украинских)",
        r"(?:зсу|сили оборони|силами оборони|наші (?:воїни|захисники|бійці|підрозділи)|українські (?:військові|воїни|підрозділи))" + _W +
        r"(?:звільнил\w*|просунул\w*|відкинул\w*|витіснил\w*|контратакува\w*|зачистил\w*|вибил\w*|деокупува\w*)",
        r"(?:звільнено|деокуповано|визволено|звільнили|деокупували)\b",
        r"ukrainian (?:forces|troops|units)" + _W + r"(?:advanced|liberated|recaptured|pushed|counterattacked|regained)",
        r"(?:всу|противник) (?:отбросил\w*|выбил\w*|вернул\w*)",
        r"(?:наши|российские) (?:войска|подразделения|бойцы) (?:отошли|оставили|покинули|отступили)",
    ],
    # attaque russe
    "ru": [
        r"(?:вс рф|вс россии|российские (?:войска|подразделения|штурмовики)|наши (?:войска|бойцы|штурмовики|подразделения)|штурмовики|штурмовые (?:отряды|группы))" + _W +
        r"(?:продвинул\w*|продвигаются|освободил\w*|заняли|заняли|выбил\w*|отбросил\w*|зачистил\w*|закрепил\w*|вошли|зашли|наступают|штурмуют|ведут наступление|развивают успех|расширил\w*)",
        r"(?:освобождени\w*|освободили)\s+(?:\w+\s+){0,2}?(?:с(?:ел|ело|ела)|пос[её]лк\w*|город\w*|н\.\s?п\.|[А-ЯЁа-яё]{4,})",
        r"(?:ворог|окупанти|росіяни|противник|кацапи|рашисти|московити)" + _W +
        r"(?:штурмува\w*|атакува\w*|намагав\w*|намагал\w*|просунув\w*|просунул\w*|наступа\w*|продовжує тиснути|тисне|захопи\w*|зайшл\w*|проник\w*|інфільтр\w*)",
        r"відбил\w*\s+(?:\d+\s+|\w+\s+){0,2}(?:атак\w*|штурм\w*|наступ\w*|спроб\w*)",
        r"(?:атак\w*|штурм\w*|наступ\w*|спроб\w* (?:прорватися|просунутися))\s+(?:\w+\s+)?(?:ворога|окупантів|росіян|противника)",
        r"russian (?:forces|troops|units)" + _W + r"(?:advanced|captured|took|seized|pushed|attacked|stormed|infiltrated)",
        r"(?:зсу|сили оборони|наші (?:воїни|захисники|підрозділи)) (?:відійшли|залишили|відступили)",
        r"(?:атака|атаки|наступление|штурм) (?:всу|противника) (?:отбит\w*|отражен\w*|захлебнул\w*)",
    ],
}
# corrections : la dernière règle « ru » décrit une attaque UKRAINIENNE repoussée (source russe) → la déplacer côté ua
STRICT["ua"].append(STRICT["ru"].pop())
# « противник » dans une source ukrainienne désigne la Russie : règles « ua » qui le mentionnent ne valent que pour les sources russes
GENERIC_UA_RULES = {1, 2, 6}


STRIKE_W = r"(бпла|дрон\w*|беспилотн\w*|безпілотн\w*|ракет\w*|аэродром\w*|аеродром\w*|герань|шахед\w*|\bкаб\b|авиаудар\w*|авіаудар\w*|нпз|пво|ппо|" \
           r"обстрел\w*|обстріл\w*|удар\w* по |прил[еіё]т\w*|drone\w*|missile\w*|airfield)"


def classify_strict(text, camp):
    t = text.lower().replace("ё", "е").replace("’", "'")
    score = {"ua": 0, "ru": 0}
    # phrases de frappes à distance (drones, missiles, aérodromes…) ignorées
    sents = [x for x in re.split(r"(?<=[.!?…;\n])\s+", t) if not re.search(STRIKE_W, x)]
    t = " . ".join(sents)
    for side, pats in STRICT.items():
        for k, p in enumerate(pats):
            if side == "ua" and k in GENERIC_UA_RULES and camp == "ua":
                p = p.replace("противник|", "").replace("|противника", "").replace("противника|", "").replace("|противник", "")
            n = len(re.findall(p, t))
            score[side] += n
    if score["ua"] > score["ru"]:
        return "ua"
    if score["ru"] > score["ua"]:
        return "ru"
    return "sq" if score["ua"] == 0 and _has(SQ, t) and _has(GROUND, t) else None
