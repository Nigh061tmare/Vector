#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memoria de hechos que el humano le cuenta a Vector ("recuerda que ...").

Puro: opera sobre el dict de memoria `m` (clave "hechos") y no toca disco; el
nucleo ya persiste `m`.  Recuperacion por solapamiento de palabras (sin
dependencias); suficiente para decenas de hechos.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

MAX_FACTS = 200
MAX_LEN = 140
_STOP = {"el", "la", "los", "las", "un", "una", "de", "del", "al", "y", "o", "que", "es", "en",
         "a", "se", "mi", "tu", "su", "con", "por", "para", "lo", "me", "te", "esta", "este"}

# "recuerda que X", "acuerdate de que X", "ten en cuenta que X", "mi nombre es X"
_PATTERNS = [
    re.compile(r"\b(?:recuerda|recuerdame|acuerdate|acu[eé]rdate|apunta|memoriza)\s+(?:de\s+)?(?:que\s+)?(.+)", re.I),
    re.compile(r"\b(?:ten en cuenta|ten presente)\s+que\s+(.+)", re.I),
    re.compile(r"\b(mi nombre es\s+.+|me llamo\s+.+|mi color favorito es\s+.+|vivo en\s+.+)", re.I),
]
_QUERY = re.compile(r"\b(qu[eé]\s+(?:te\s+)?(?:dije|cont[eé])|qu[eé]\s+recuerdas|te\s+acuerdas\s+de)\b(.*)", re.I)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _words(t: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _norm(t)) if w not in _STOP and len(w) > 1]


def extract(text: str) -> Optional[str]:
    """Hecho a recordar dentro de `text`, o None."""
    for p in _PATTERNS:
        mm = p.search(text or "")
        if mm:
            f = re.sub(r"\s+", " ", mm.group(1)).strip(" .,;:!?¡¿")
            if len(f) >= 3 and f.lower() not in ("que", "de que"):
                return f[:MAX_LEN]
    return None


def is_recall_query(text: str) -> Tuple[bool, str]:
    mm = _QUERY.search(text or "")
    return (True, (mm.group(2) or "").strip()) if mm else (False, "")


def add(m: Dict[str, Any], fact: str, now: Optional[float] = None) -> bool:
    """Guarda el hecho (dedup por palabras). True si es nuevo."""
    hs: List[Dict[str, Any]] = m.setdefault("hechos", [])
    w = set(_words(fact))
    for h in hs:
        hw = set(_words(h["texto"]))
        if w and hw and len(w & hw) / max(len(w), len(hw)) >= 0.8:
            h["t"] = now if now is not None else time.time()      # refresca, no duplica
            return False
    hs.append({"texto": fact[:MAX_LEN], "t": now if now is not None else time.time()})
    del hs[:-MAX_FACTS]
    return True


def retrieve(m: Dict[str, Any], query: str, k: int = 3) -> List[str]:
    """Hechos mas relevantes para `query`; sin query, los mas recientes."""
    hs = list(m.get("hechos") or [])
    q = set(_words(query))
    if not q:
        return [h["texto"] for h in sorted(hs, key=lambda h: -h["t"])[:k]]
    scored = []
    for h in hs:
        s = len(q & set(_words(h["texto"])))
        if s:
            scored.append((s, h["t"], h["texto"]))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [t for _, _, t in scored[:k]]


def context(m: Dict[str, Any], query: str = "", k: int = 3) -> str:
    """Fragmento corto para el prompt de la IA."""
    r = retrieve(m, query, k)
    return ("Sabe:" + "; ".join(r) + ". ") if r else ""


def reply_for_recall(m: Dict[str, Any], rest: str) -> str:
    r = retrieve(m, rest, 2)
    if not r:
        return "No recuerdo nada de eso todavia."
    return "Me dijiste que " + " y que ".join(r) + "."
