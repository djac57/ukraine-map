#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Client Gemini minimal (REST, urllib, zéro dépendance) pour la couche TENDANCE du brief.
Clé : $GEMINI_API_KEY (secret CI) ou ~/.config/gemini_key. Sortie structurée + retry 429/503.
Best-effort : l'appelant dégrade proprement si la clé manque ou si l'appel échoue."""
import json, os, time, urllib.request, urllib.error

MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")   # le plus free-tier-friendly
BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def have_key():
    return bool(os.environ.get("GEMINI_API_KEY") or os.path.exists(os.path.expanduser("~/.config/gemini_key")))


def _key():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k.strip()
    return open(os.path.expanduser("~/.config/gemini_key")).read().strip()


def generate(prompt, schema=None, model=None, temperature=0.0, max_tokens=1024, retries=4, system=None):
    """Un appel generateContent. Renvoie le texte, ou lève une exception (l'appelant gère le repli)."""
    model = model or MODEL
    url = f"{BASE}/{model}:generateContent?key={_key()}"
    cfg = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if schema is not None:
        cfg["responseMimeType"] = "application/json"; cfg["responseSchema"] = schema
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    data = json.dumps(body).encode("utf-8")
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.load(r)
            parts = ((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}]
            return "".join(p.get("text", "") for p in parts)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(delay); delay = min(delay * 2, 45); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(delay); delay = min(delay * 2, 45); continue
            raise
    raise RuntimeError("Gemini: échec après retries")
