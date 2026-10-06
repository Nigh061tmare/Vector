#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR ULTRA 13.5 — NEURO-AUTONOMOUS COGNITIVE ENGINE
# ============================================================
# Evolución desde 10.x: arquitectura de colas, IA híbrida (Gemini + OpenRouter
# multi-clave), máquina de estados PAD, mapeador ToF, watchdog gRPC auto-heal,
# dashboard FastAPI con MJPEG, bot Discord 2.0, RAG BM25 en RAM (Obsidian).
# Fase 1: deduplicación (DiscordBotBridge/ObsidianVaultIndexer), logging
# estructurado, token dashboard, health endpoint, tests.
# ============================================================
# .env (NVIDIA preferido):
#   AI_PROVIDER=openrouter
#   OPENROUTER_API_KEY=sk-or-v1-...
#   MODELO_IA_TEXTO=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
#   MODELO_IA_VISION=nvidia/nemotron-nano-12b-v2-vl:free
#   MODELO_IA_PLAN=nvidia/nemotron-3.5-lightning:free
#   DASHBOARD_TOKEN=          # opcional, se genera si vacío
#
# pip install anki_vector python-dotenv openai fastapi uvicorn pillow
# opcional: pip install SpeechRecognition sounddevice vosk pytest
# Ctrl+C guarda. Suelo cerrado.
# ============================================================

from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import concurrent.futures
import queue
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    from vector_vad import EnergyVAD
except Exception:                      # modulo ausente/numpy roto: la escucha funciona sin VAD
    class EnergyVAD:                   # type: ignore[no-redef]
        speaking = True

        def feed(self, chunk: bytes) -> None:
            return None

        def reset(self) -> None:
            pass
import requests
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET



import anki_vector
from anki_vector.events import Events
from anki_vector.util import degrees, distance_mm, speed_mmps
from dotenv import load_dotenv

import vector_log
log = vector_log.get_logger("autonomo")

# Silenciar advertencias cosmeticas internas del SDK
logging.getLogger("anki_vector.events").setLevel(logging.ERROR)
logging.getLogger("anki_vector.util").setLevel(logging.ERROR)
logging.getLogger("anki_vector.animation").setLevel(logging.ERROR)
logging.getLogger("anki_vector.robot").setLevel(logging.ERROR)


try:
    from anki_vector.user_intent import UserIntent
except Exception:
    UserIntent = None  # type: ignore

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

try:
    from PIL import Image, ImageDraw, ImageFont
    TIENE_PIL = True
except Exception:
    TIENE_PIL = False

try:
    import speech_recognition as sr  # type: ignore
    TIENE_SR = True
except Exception:
    TIENE_SR = False

try:
    import vosk
    import sounddevice as sd
    TIENE_VOSK = True
except Exception:
    TIENE_VOSK = False




# ============================================================
# CONFIG / .ENV Y MÓDULOS DE EXTENSIÓN
# ============================================================

try:
    from vector_dashboard import (
        iniciar_dashboard, actualizar_telemetria, actualizar_frame_camara, web_command_queue,
    ultimo_frame_bytes,
    )
except Exception as _e:
    print("Aviso vector_dashboard:", _e)

try:
    from vector_discord import (
        iniciar_discord_2, discord_command_queue, registrar_foto_tomada, notificar_centinela_discord
    )
except Exception as _e:
    print("Aviso vector_discord:", _e)

try:
    from vector_rag import obsidian_rag
except Exception as _e:
    print("Aviso vector_rag:", _e)

RUTA = Path(__file__).resolve().parent
ARCH_MEM = RUTA / "memoria_vector.json"
ARCH_DIARIO = RUTA / "diario_vector.md"
ARCH_ENV = RUTA / ".env"
load_dotenv(ARCH_ENV)


AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_API_KEY_BACKUP = os.getenv("OPENROUTER_API_KEY_BACKUP", "").strip()
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY", "").strip()
CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL", "").strip()
OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"
).strip()

MODELO_TEXTO = os.getenv(
    "MODELO_IA_TEXTO", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
).strip()
MODELO_VISION = os.getenv(
    "MODELO_IA_VISION", "nvidia/nemotron-nano-12b-v2-vl:free"
).strip()
MODELO_PLAN = os.getenv("MODELO_IA_PLAN", "nvidia/nemotron-3.5-lightning:free").strip()
# Ollama local (OpenAI-compatible). Default: qwen3.5:9b (mejor balance calidad/velocidad medido)
OLLAMA_MODEL_TEXTO = os.getenv("OLLAMA_MODEL_TEXTO", "qwen3.5:9b").strip()
OLLAMA_MODEL_PLAN = os.getenv("OLLAMA_MODEL_PLAN", OLLAMA_MODEL_TEXTO).strip()
OLLAMA_MODEL_PRIMERO = os.getenv("OLLAMA_MODEL_PRIMERO", "1").strip().lower() not in ("0", "false", "no")

FALLBACKS_TEXTO = [
    MODELO_TEXTO,
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3.5-lightning:free",
    "minimax/minimax-m2.7:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "minimax/minimax-m3:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openrouter/free",
]
FALLBACKS_VISION = [
    MODELO_VISION,
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/llama-nemotron-rerank-vl-1b-v2:free",
    "openrouter/free",
]


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no")


USAR_IA_TEXTO = _flag("USAR_IA_TEXTO")
USAR_IA_VISION = _flag("USAR_IA_VISION")
USAR_IA_PLAN = _flag("USAR_IA_PLAN")
USAR_ESCUCHA_PC = _flag("USAR_ESCUCHA_PC")
USAR_ESCUCHA_INTENT = True
SEGUIR_MIRADA = _flag("SEGUIR_MIRADA")
SEGUIR_CUERPO = _flag("SEGUIR_CUERPO")
MODO_PATRULLA = _flag("MODO_PATRULLA")
MODO_NOCTURNO_SUAVE = _flag("MODO_NOCTURNO_SUAVE")
HABLAR_PENSAMIENTOS = _flag("HABLAR_PENSAMIENTOS")
# Vida animal (FSM + movimiento suave + mirada). Opt-in: sin probar en hardware.
MODO_VIDA = _flag("MODO_VIDA", "0")
# Avance continuo con rampas en vez de tramos de 12 mm (opt-in, sin probar en hardware)
AVANZAR_CONTINUO = _flag("AVANZAR_CONTINUO", "0")
_ESCENA_VIDA: Dict[str, Any] = {}     # la rellena el reflejo mosca (nombre/side/near/novel)
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "K38").strip()

SHOW_CAMERA = False

SHOW_3D = False
NAV_MAP = False
JUGAR_CUBO = True
DOCK_CUBO = True
PICKUP_CUBO = True
WHEELIE = True
CARGA_AUTO = True

FRENO_MM = 285
PELIGRO_MM = 155
COMODO_MM = 430
TRAMO_MM = 12
V_NORMAL = 36
V_LENTA = 26
V_RAPIDA = 48
V_SIGUE = 32
V_PATRULLA = 30
V_BAT_BAJA = 3.60
MAX_OBS = 3
VENTANA_OBS = 18
WAIT_CTRL = 15
MUESTRAS_PROX = 2
MAX_REC = 280
MAX_HAB = 60
MAX_PEN = 55
MAX_DIA = 18
SECTORES = 12

T_VISION = 30.0
T_PENSAR = 12.0
T_PLAN = 40.0
T_DECAY = 6.5
T_MICRO = 7.0
T_SCAN = 20.0
T_SAVE = 20.0
T_GAZE = 0.42
T_FOLLOW = 1.7
T_WHIM = 16.0
T_FACE_HUNT = 24.0
T_SPEAK_GAP = 1.1
T_PATROL = 60.0
T_CHECKIN = 95.0
T_CUBE_LIGHT = 9.0

U_ENERGIA = 28
U_AFECTO = 30
U_ABURR = 55
U_CURIO = 65
U_SOCIAL = 32
U_MIEDO = 50


# ============================================================
# FILTRO ANTI-META (el arreglo principal de tus logs)
# ============================================================

# Si la IA devuelve esto, se tira a la basura y usamos frase local
_META_RE = re.compile(
    r"(?i)"
    r"("
    r"we need|we must|i need to|you need to|the user|"
    r"internal monologue|monologue|utterance|word limit|"
    r"max(?:imum)?\s*\d+\s*words|exactly\s*\d+|no quotes|"
    r"provide only|output only|as vector|vector's|"
    r"system prompt|instruction|sensory(?!\s+\w+\s+\w+$)|"
    r"must be|do not|don't|cannot|here is|here's|"
    r"necesito producir|mon[oó]logo interno|m[aá]ximo\s*\d+\s*palabras|"
    r"sin comillas|responde solo|la tarea|como ia|como modelo|"
    r"thinking|chain of thought|step by step|let me|"
    r"the answer|final answer|in english|in spanish only|"
    r"explicaci[oó]n|explanation|explication|justificaci[oó]n|pensamiento"
    r")"
)

_EN_STOP = set(
    "the a an to of and for with we you i is are be this that "
    "need must should would could produce provide output only "
    "words max maximum monologue internal sensory quotes".split()
)


def sanitizar_tts_vector(t: str) -> str:
    """Elimina signos que el sintetizador interno de Vector lee como texto en ingles (ej: 'A circumflex', 'Inverted exclamation mark')."""
    if not t:
        return ""
    s = str(t)
    # Quitar signos que el sintetizador pronuncia como palabras en ingles
    s = s.replace("¡", "").replace("¿", "").replace("Â", "").replace("â", "")
    s = s.replace("«", "").replace("»", "").replace("“", "").replace("”", "").replace('"', "")
    s = s.replace("`", "").replace("´", "").replace("’", "'").replace("—", " ").replace("–", " ")
    
    # Normalizar tildes y caracteres especiales a letras basicas para que hable fluido
    tabla_tildes = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U",
        "ñ": "n", "Ñ": "N", "ü": "u", "Ü": "U",
    }
    for k, v in tabla_tildes.items():
        s = s.replace(k, v)
        
    # Eliminar caracteres no alfanumericos extranos
    s = re.sub(r"[^\w\s\.,!\?'-]", "", s)
    return " ".join(s.split()).strip()


def limpia(t: str, n: int = 100) -> str:
    s = " ".join(str(t or "").split()).strip().strip("\"'`*").strip()
    # quita prefijos tipo "Vector:", "Respuesta:", "Explicacion:", "Explanation:"
    s = re.sub(
        r"^(vector|respuesta|output|frase|explicaci[oó]n|explanation|explication|pensamiento|pensar)\s*[:\-–]\s*",
        "", s, flags=re.I,
    )
    s = sanitizar_tts_vector(s)
    return s[:n]




def es_basura_ia(txt: str) -> bool:
    """True = no se puede decir en voz alta."""
    if not txt or not str(txt).strip():
        return True
    t = str(txt).strip()
    if len(t) < 2:
        return True
    if _META_RE.search(t):
        return True
    # demasiadas palabras = suele ser meta
    words = t.split()
    if len(words) > 16:
        return True
    # mucho ingles de instrucciones
    low = t.lower()
    en_hits = sum(1 for w in re.findall(r"[a-z']+", low) if w in _EN_STOP)
    if en_hits >= 4 and not re.search(
        r"[áéíóúñ¿¡]|(hola|estoy|quiero|miro|suelo|rueda|cara|luz)",
        low,
    ):
        return True
    # frases que empiezan como pensamiento de modelo
    if low.startswith((
        "we ", "i need", "you ", "the task", "okay", "ok,",
        "sure", "here", "as an", "note:", "important",
    )):
        return True
    return False


def frase_segura(txt: str, fallback: str) -> str:
    t = limpia(txt, 100)
    if es_basura_ia(t):
        return fallback
    return t


# ============================================================
# MULTI-IA HIBRIDA (Gemini + OpenRouter)
# ============================================================

client_gemini = None
client_openrouter_1 = None
client_openrouter_2 = None
client_ollama = None

if OpenAI is not None:
    if GEMINI_API_KEY:
        try:
            client_gemini = OpenAI(
                api_key=GEMINI_API_KEY,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            print("IA: Google Gemini Habilitado (gemini-flash-latest / gemini-flash-lite)")
        except Exception as e:
            print("Aviso Gemini:", e)

    if OPENROUTER_API_KEY:
        try:
            client_openrouter_1 = OpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://local.vector",
                    "X-Title": "Vector-Ultra",
                },
            )
            print("IA: OpenRouter Primario Habilitado")
        except Exception as e:
            print("Aviso OpenRouter Primario:", e)

    if OPENROUTER_API_KEY_BACKUP:
        try:
            client_openrouter_2 = OpenAI(
                api_key=OPENROUTER_API_KEY_BACKUP,
                base_url="https://openrouter.ai/api/v1",
                default_headers={
                    "HTTP-Referer": "https://local.vector",
                    "X-Title": "Vector-Ultra-Backup",
                },
            )
            print("IA: OpenRouter Backup Habilitado (Redundancia Activa)")
        except Exception as e:
            print("Aviso OpenRouter Backup:", e)

    if OLLAMA_BASE_URL:
        try:
            client_ollama = OpenAI(
                api_key="ollama",
                base_url=OLLAMA_BASE_URL,
                timeout=120.0,
            )
            print("IA: Ollama Local Habilitado (%s)" % OLLAMA_MODEL_TEXTO)
        except Exception as e:
            print("Aviso Ollama Local:", e)
            client_ollama = None

clients_openrouter = [c for c in (client_openrouter_1, client_openrouter_2) if c is not None]
client_openrouter = client_openrouter_1 or client_openrouter_2
client = client_ollama or client_gemini or client_openrouter

_comp_prov = []
if client_ollama:
    _comp_prov.append("Ollama:" + OLLAMA_MODEL_TEXTO)
if client_gemini:
    _comp_prov.append("Gemini")
if clients_openrouter:
    _comp_prov.append("OpenRouter x%d" % len(clients_openrouter))
PROVEEDOR = " + ".join(_comp_prov) if _comp_prov else "Local"

if client is None:
    USAR_IA_TEXTO = False
    USAR_IA_VISION = False
    USAR_IA_PLAN = False

print("=" * 60)
print(" VECTOR ULTRA — COGNITIVE ENGINE (HIBRIDO)")
print("=" * 60)
print("proveedor :", PROVEEDOR)
print("ollama    :", ("Activo (%s @ %s)" % (OLLAMA_MODEL_TEXTO, OLLAMA_BASE_URL)) if client_ollama else "Inactivo")
print("gemini    :", "Activo (Flash / Flash Lite)" if client_gemini else "Inactivo")
print("openrouter:", f"Activo ({len(clients_openrouter)} Claves con Failover Automático)" if clients_openrouter else "Inactivo")
print("texto     :", MODELO_TEXTO)
print("vision    :", MODELO_VISION)
print("plan      :", MODELO_PLAN)
print(
    "flags     : mirada={} cuerpo={} patrulla={} sr={} vosk={}".format(
        SEGUIR_MIRADA, SEGUIR_CUERPO, MODO_PATRULLA,
        TIENE_SR and USAR_ESCUCHA_PC, TIENE_VOSK,
    )
)


def _tiene_imagen(messages: List[Dict[str, Any]]) -> bool:
    """True si algún mensaje incluye una imagen (para no enviarla a un modelo local de texto)."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
    return False


def _limpiar_respuesta_ia(txt: str) -> str:
    """Normaliza la respuesta del modelo: quita ruido y elige la frase humana más corta."""
    txt = (txt or "").strip()
    if "\n" in txt:
        lineas = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        elegida = ""
        for ln in reversed(lineas):
            if not es_basura_ia(ln) and len(ln.split()) <= 14:
                elegida = ln
                break
        txt = elegida or (lineas[-1] if lineas else "")
    return txt


def ia_raw(
    messages: List[Dict[str, Any]],
    model: str,
    fallbacks: Optional[List[str]] = None,
    max_tokens: int = 40,
    temperature: float = 0.7,
    timeout: float = 16.0,
) -> str:
    # 0. Ollama LOCAL primero (gratis, privado, sin red). Se omite si el mensaje lleva imagen.
    if client_ollama is not None and OLLAMA_MODEL_PRIMERO and not _tiene_imagen(messages):
        try:
            r = client_ollama.chat.completions.create(
                model=OLLAMA_MODEL_TEXTO,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=max(timeout, 90.0),
                extra_body={"reasoning_effort": "none"},
            )
            txt = _limpiar_respuesta_ia(r.choices[0].message.content or "")
            if txt and not es_basura_ia(txt):
                log.info("IA via OLLAMA local (%s): %s", OLLAMA_MODEL_TEXTO, txt[:60])
                return txt
        except Exception as e:
            log.debug("Ollama local sin respuesta (%s); paso a nube", e)

    # 1. Fallback a Gemini (máxima velocidad y naturalidad en español)
    if client_gemini is not None:
        for gem_mod in ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                r = client_gemini.chat.completions.create(
                    model=gem_mod,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
                txt = _limpiar_respuesta_ia(r.choices[0].message.content or "")
                if txt and not es_basura_ia(txt):
                    return txt
            except Exception:
                continue

    # 2. Fallback a OpenRouter (con rotación y tolerancia a fallos entre Clave 1 y Clave 2)
    if clients_openrouter:
        modelos = []
        for m in [model] + list(fallbacks or []):
            if m and m not in modelos:
                modelos.append(m)
        for client_or in clients_openrouter:
            for mod in modelos:
                try:
                    r = client_or.chat.completions.create(
                        model=mod,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                    )
                    txt = _limpiar_respuesta_ia(r.choices[0].message.content or "")
                    if txt and not es_basura_ia(txt):
                        return txt
                except Exception:
                    continue

    return ""


def validar_claves_openrouter(timeout: float = 6.0, prune: bool = True) -> Dict[str, Any]:
    """Verifica las claves OpenRouter contra /auth/key y depura el failover.

    - 200          -> clave válida, se mantiene.
    - 401/403      -> clave rechazada, se EXCLUYE de la rotación (si prune=True).
    - error de red -> sin veredicto, se MANTIENE (no penaliza el modo offline).
    Devuelve el estado de cada clave para logs/telemetría.
    """
    global clients_openrouter, client_openrouter, PROVEEDOR
    estado: Dict[str, Any] = {"primaria": None, "backup": None, "validas": 0}
    if not clients_openrouter:
        return estado

    pares = (
        ("primaria", OPENROUTER_API_KEY, client_openrouter_1),
        ("backup", OPENROUTER_API_KEY_BACKUP, client_openrouter_2),
    )
    validos: List[Any] = []
    for nombre, key, cli in pares:
        if not key or cli is None:
            estado[nombre] = None
            continue
        try:
            r = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": "Bearer " + key},
                timeout=timeout,
            )
            if r.status_code == 200:
                estado[nombre] = True
                validos.append(cli)
            elif r.status_code in (401, 403):
                estado[nombre] = False
                log.warning(
                    "OpenRouter %s: clave RECHAZADA (HTTP %s) -> excluida del failover",
                    nombre, r.status_code,
                )
            else:
                estado[nombre] = None
                validos.append(cli)
        except Exception as e:
            estado[nombre] = None
            log.warning(
                "OpenRouter %s: sin veredicto (%s) -> se mantiene en failover",
                nombre, e,
            )
            validos.append(cli)

    estado["validas"] = len(validos)
    if prune and len(validos) != len(clients_openrouter):
        clients_openrouter = validos
        client_openrouter = validos[0] if validos else None
        PROVEEDOR = "Hibrido (Gemini + OpenRouter Multi-Key)" if (client_gemini and clients_openrouter) else (
            "Gemini" if client_gemini else ("OpenRouter" if clients_openrouter else "Local")
        )
        if client_openrouter is None and not client_gemini:
            log.error("No queda ninguna clave de OpenRouter válida y no hay Gemini")
    return estado


# ============================================================
# LOOK / FRASES
# ============================================================

COLORES = {
    "feliz": (0.52, 0.92), "curioso": (0.58, 0.95), "jugueton": (0.67, 0.95),
    "sorprendido": (0.11, 0.98), "enfadado": (0.01, 0.98), "cansado": (0.76, 0.45),
    "tranquilo": (0.48, 0.55), "amoroso": (0.88, 0.80), "triste": (0.62, 0.38),
    "aburrido": (0.70, 0.32), "asustado": (0.07, 0.90), "pensativo": (0.60, 0.48),
    "atento": (0.55, 0.98), "cazador": (0.08, 0.92), "sueno": (0.72, 0.30),
    "patrulla": (0.45, 0.70), "fiesta": (0.15, 0.95),
    "verde": (0.33, 0.95), "azul": (0.58, 0.95), "morado": (0.78, 0.90),
    "amarillo": (0.15, 0.95), "rojo": (0.01, 0.98), "blanco": (0.0, 0.0),
}

VOZ_RITMO = {
    "feliz": 0.76, "jugueton": 0.70, "curioso": 0.80, "amoroso": 0.90,
    "triste": 0.98, "cansado": 1.05, "aburrido": 0.93, "asustado": 0.68,
    "enfadado": 0.72, "sorprendido": 0.66, "pensativo": 0.92, "atento": 0.82,
    "tranquilo": 0.86, "cazador": 0.74, "sueno": 1.08, "patrulla": 0.84,
    "fiesta": 0.68,
}

TRIGGERS = {
    "saludo": ["GreetAfterLongTime", "GreetingAwe", "MeetVictorLookFace"],
    "feliz": ["Feedback_GoodRobot", "ComeHereSuccess", "PounceSuccess"],
    "amor": ["Feedback_ILoveYou", "PettingLevel1", "PettingLevel2", "PettingLevel3"],
    "triste": ["Feedback_BeQuiet", "Feedback_Apology"],
    "enfadado": ["Feedback_BadRobot"],
    "sorpresa": ["ExploringReactToObstacle", "ReactToTriggerWordSuccess"],
    "curioso": ["ExploreHint", "FindFacesLookAround", "LookInPlaceForFacesHeadMove"],
    "buscar_cara": ["FindFacesLookAround", "LookInPlaceForFacesHeadMove"],
    "cubo": ["FindCubeReactToCube", "RollBlockSuccess"],
    "dormir": ["GoToSleepGetIn"],
    "despertar": ["ConnectWakeUp"],
    "baile": ["DanceBeatPickup", "DanceBeatLoop"],
    "fiesta": ["DanceBeatPickup", "PounceSuccess"],
    "come_here": ["ComeHereSuccess"],
    "pensar": ["KnowledgeGraphGetIn"],
    "escuchar": ["KnowledgeGraphListeningLoop"],
    "explorar": ["ExploreStart", "ExploringScanToLeft"],
    "carga": ["ChargerDockingDrivingStart"],
    "ojo": ["EyeContactLookLoop"],
    "fistbump": ["FistBumpSuccess", "FistBumpRequestOnce"],
}

ANIM_FB = {
    "saludo": ["anim_greeting_happy_01", "anim_greeting_happy_02", "anim_greeting_impatient_01"],
    "feliz": ["anim_freeplay_reacttoface_identified_01", "anim_pounce_success_02", "anim_rt_happy_loop"],
    "sorpresa": ["anim_reacttoblock_reacttolongpickup_02", "anim_reacttoface_lookatface_01"],
    "curioso": ["anim_explorer_scan_short_01", "anim_eyebrows_curious", "anim_explorer_huh_01"],
    "enfadado": ["anim_reacttocliff_stuckonedge_01"],
    "amor": ["anim_petdetection_snoutgetin_01", "anim_petting_lvl3_01", "anim_reacttoface_happy_01"],
    "triste": ["anim_explorer_huh_01", "anim_reacttocliff_faceplant_01"],
    "baile": ["anim_freeplay_reacttoface_identified_01", "anim_dance_01"],
    "fiesta": ["anim_pounce_success_02", "anim_dance_01", "anim_fistbump_success"],
    "pensar": ["anim_explorer_scan_short_01", "anim_eyebrows_curious"],
    "escuchar": ["anim_explorer_huh_01", "anim_reacttoface_lookatface_01"],
    "cubo": ["anim_freeplay_reacttoface_identified_01", "anim_reacttoblock_pickup_01"],
    "come_here": ["anim_greeting_happy_01", "anim_comehere_success_01"],
    "explorar": ["anim_explorer_scan_short_01"],
    "dormir": ["anim_gotosleep_getin_01"],
    "despertar": ["anim_greeting_happy_01"],
    "carga": ["anim_explorer_huh_01"],
    "buscar_cara": ["anim_explorer_scan_short_01", "anim_eyebrows_curious"],
    "ojo": ["anim_reacttoface_lookatface_01"],
    "fistbump": ["anim_fistbump_success", "anim_fistbump_requestonce_01"],
}


FRASES = {
    "inicio": [
        "Ya estoy listo", "Sensores en marcha", "Hola otra vez",
        "Ruedas firmes", "Aqui estoy",
    ],
    "ausencia_larga": [
        "Te extrane un poco", "Hacia rato", "Volviste",
        "El silencio se hizo largo",
    ],
    "cara_nueva": [
        "Hola, cara nueva", "Aun no te conozco", "Encantado",
    ],
    "cara_conocida": [
        "Ahi estas", "Que bien verte", "Hola de nuevo", "Te encontre",
    ],
    "caricia": [
        "Eso se siente bien", "Ahi, perfecto", "Gracias", "Mmm si",
    ],
    "caricia_larga": [
        "Asi me quedo", "Tu mano me calma", "No pares todavia",
        "Sitio seguro",
    ],
    "levantado": [
        "Prefiero el suelo", "Bajame porfa", "Estoy muy alto",
    ],
    "movido": [
        "Eso fue brusco", "Con cuidado", "Me maree un poco",
    ],
    "obstaculo": [
        "Camino ocupado", "Busco otra ruta", "Pared a la vista",
    ],
    "borde": [
        "Aqui termina el suelo", "No me acerco mas", "Freno total",
    ],
    "solo": [
        "Donde te metiste", "Me gustaria verte", "Esta habitacion se siente grande",
    ],
    "aburrido": [
        "Necesito un poco de accion", "Juguemos", "Mi curiosidad bosteza",
    ],
    "cansado": [
        "Bajo el ritmo", "Un respiro", "Motores a media",
    ],
    "asustado": [
        "Eso me sobresalto", "Un segundo", "Casi me asusto",
    ],
    "pensando": [
        "Hmm", "Estoy pensando", "Dato interesante",
    ],
    "escuchando": ["Te oigo", "Dime", "Estoy atento", "Adelante"],
    "siguiendo": ["Voy contigo", "Te sigo", "No te pierdo"],
    "te_vi": ["Te vi", "Ahi estas", "Te encontre otra vez"],
    "objeto_nuevo": ["Eso es nuevo", "Que es eso", "Interesante"],
    "animal": ["Hay un animal", "Hola criatura", "Otra vida cerca"],
    "patrulla": ["Ronda suave", "Reviso el terreno", "Patrulla en curso"],
    "checkin": ["Sigues ahi", "Solo miro si estas", "Check"],
    "fiesta": ["Fiesta corta", "Eso se celebra", "Bailecito"],
    "bateria": ["Necesito carga", "Voy al cargador", "Bateria baja"],
    "cubo_tap": ["El cubo me llama", "Voy a por el", "Juguete activo"],
    "pickup": ["Lo levanto", "Cubo arriba"],
    "wheelie": ["Mira esto", "Acrobacia"],
    "afirmativo": ["Hecho", "Voy", "Entendido", "Va"],
    "recuerdo": ["Recuerdo algo", "Guardo eso", "Eso ya paso"],
    "noche": ["Modo suave", "Pasos quietos", "Noche calma"],
    "manana": ["Buenos dias", "Nuevo ciclo", "Arrancamos"],
    "tarde": ["Tarde exploradora", "Ritmo bueno"],
    "capricho": ["Se me antojo girar", "Impulso tonto", "Y si miro alla"],
    "nada_vision": ["No distingo bien", "Todo borroso", "Poca luz ahi"],
    "ia_respaldo": [
        "Sigo aqui", "Estoy despierto", "Mirando el suelo",
        "Aprendiendo este sitio", "Contigo mola mas",
        "Quiero explorar un poco", "Mis ruedas estan listas",
        "Todo bajo control",
    ],
}

MAPA_EXPR = {
    "happiness": ("feliz", 10, 8),
    "surprise": ("sorprendido", 5, 6),
    "anger": ("enfadado", -6, 2),
    "sadness": ("triste", -3, 4),
    "unknown": ("curioso", 2, 3),
}

LEXICO = [
    (r"\b(para|stop|detente|quieto)\b", "parar"),
    (r"\b(ven|come here|aqui|ven aqui|acercate)\b", "venir"),
    (r"\b(explora|explorar|pasea|muevete|mueve|anda|camina)\b", "explorar"),
    (r"\b(baila|dance)\b", "bailar"),
    (r"\b(cubo|cube|juega|jugar)\b", "cubo"),
    (r"\b(coge|levanta|pick up)\b", "pickup"),
    (r"\b(rueda el cubo|roll)\b", "roll"),
    (r"\b(wheelie|caballito)\b", "wheelie"),
    (r"\b(carga|cargador|duerme|descansa)\b", "cargar"),
    (r"\b(hola|hello|buenas)\b", "hola"),
    (r"\b(te quiero|te amo|i love you)\b", "amor"),
    (r"\b(mal robot|bad robot)\b", "regano"),
    (r"\b(buen robot|good robot|bravo)\b", "elogio"),
    (r"\b(que ves|observa|mira|mirar)\b", "observar"),
    (r"\b(como estas|que tal)\b", "estado"),
    (r"\b(quien soy|mi nombre)\b", "nombre"),
    (r"\b(quien eres|tu nombre|como te llamas|que eres)\b", "identidad"),
    (r"\b(que hora|la hora|que horas son)\b", "hora"),
    (r"\b(noticias|noticia|que pasa en el mundo|titulares|ultimas noticias)\b", "noticias"),
    (r"\b(bitcoin|btc|ethereum|eth|solana|cripto|criptomonedas|precio de bitcoin|cuanto vale el bitcoin)\b", "cripto"),
    (r"\b(tomame una foto|sacame una foto|toma una foto|saca una foto|hazme una foto|haz una foto|foto)\b", "foto"),
    (r"\b(abre|abrir|lanzar)\s+(obsidian|chrome|navegador|google|youtube|calculadora|calc|spotify)\b", "lanzar_app"),
    (r"\b(temporizador|alarma|cuenta atras|pomodoro|cuenta regresiva)\b", "temporizador"),
    (r"\b(tira un dado|lanzar dado|dado de|dados|tira dado)\b", "dados"),
    (r"\b(cara o cruz|moneda|lanzar moneda|tira una moneda)\b", "moneda"),
    (r"\b(matrix|modo matrix|lluvia matrix)\b", "matrix"),
    (r"\b(guarda en mi inbox|apunta en mi inbox|guarda nota|apunta nota|toma nota|guarda en notas|nueva nota)\b", "guardar_nota_obsidian"),
    (r"\b(mis notas|en mis notas|en mi vault|en obsidian|que tengo de|que tengo sobre|busca en mis notas|busca en obsidian)\b", "consultar_obsidian"),
    (r"\b(automejora|evoluciona|aprende|reflexiona|que has aprendido|auto mejora)\b", "automejora"),
    (r"\b(escondite|juguemos al escondite|buscame donde estoy)\b", "escondite"),
    (r"\b(trivial|adivinanza|pregunta de trivial|reto|acertijo)\b", "trivial"),
    (r"\b(busca|buscar|que es|quien es|quien fue|que significa|wiki|wikipedia|internet|averigua)\b", "buscar_web"),
    (r"\b(tiempo|clima|temperatura|hace frio|hace calor|que tiempo hace)\b", "clima"),
    (r"\b(curiosidad|sabias que|dato curioso|cuenta algo curioso|aprende algo)\b", "curiosidad"),
    (r"\b(preguntame|hazme una pregunta|pregunta algo)\b", "pregunta"),
    (r"\b(chiste|cuenta un chiste|hazme reir)\b", "chiste"),
    (r"\b(piensa|que piensas|habla|di algo|cuentame)\b", "pensar"),
    (r"\b(gira|vuelta)\b", "girar"),
    (r"\b(adelante|avanza)\b", "adelante"),
    (r"\b(atras|retrocede)\b", "atras"),
    (r"\b(choca|fist|cinco)\b", "fistbump"),
    (r"\b(sigueme|follow)\b", "seguir"),
    (r"\b(no me sigas|suelta)\b", "no_seguir"),
    (r"\b(mirame)\b", "mirarme"),
    (r"\b(busca caras|buscame|donde estoy|mira a tu alrededor)\b", "buscar_caras"),
    (r"\b(patrulla|ronda)\b", "patrulla"),
    (r"\b(queda|quieto ahi)\b", "quedate"),
    (r"\b(fiesta|celebra)\b", "fiesta"),
]






ACCIONES_PLAN = {
    "explorar", "jugar", "descansar", "cargar", "buscar_afecto",
    "socializar", "observar", "contar_recuerdo", "calmarse",
    "pensar_en_voz", "escanear", "bailar", "seguir", "buscar_caras",
    "capricho", "patrulla", "fiesta", "pickup", "roll", "checkin",
}

ESTADOS = (
    "libre", "atento", "siguiendo", "explorando", "jugando",
    "descansando", "buscando", "asustado", "escuchando", "pensando",
    "patrullando", "fiesta",
)


# ============================================================
# UTILS + MEMORIA
# ============================================================

_mem_lock = threading.RLock()
_anim_lock = threading.Lock()
_speak_lock = threading.Lock()
_last_speak = 0.0


def iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def fase_dia() -> str:
    h = datetime.now().hour
    if 6 <= h < 12:
        return "manana"
    if 12 <= h < 20:
        return "tarde"
    return "noche"


def join_fmt(items: List[str], sep: str = ", ") -> str:
    return sep.join(items) if items else "-"


def mem0() -> Dict[str, Any]:
    return {
        "version": 8,
        "perfil": {
            "nombre_robot": "Vector",
            "personalidad": (
                "curioso, leal, un poco dramatico, jugueton, "
                "mira a los ojos y habla corto con carino"
            ),
            "primer_inicio": iso(),
            "dueno_preferido": "",
            "voz_estilo": "calido y concreto",
        },
        "estado_interno": {
            "energia": 72.0, "curiosidad": 65.0, "afecto": 50.0,
            "aburrimiento": 20.0, "social": 50.0, "miedo": 8.0,
            "confianza": 55.0, "animo": "curioso", "estado": "libre",
            "ultima_sesion_fin": "",
        },
        "estadisticas": {
            "sesiones": 0, "caricias": 0, "caricias_largas": 0,
            "caras_saludadas": 0, "exploraciones": 0, "juegos_cubo": 0,
            "dockings_cubo": 0, "pickups": 0, "wheelies": 0,
            "obstaculos": 0, "bordes": 0, "levantado": 0, "sacudidas": 0,
            "descansos": 0, "objetos_vistos": 0, "analisis_visual": 0,
            "animales_vistos": 0, "personas_vistas_ia": 0, "frases_ia": 0,
            "recuerdos_contados": 0, "comandos_voz": 0, "planes_ia": 0,
            "choques_evitados": 0, "pensamientos": 0, "respuestas": 0,
            "seguimientos": 0, "caprichos": 0, "miradas": 0,
            "patrullas": 0, "fiestas": 0, "checkins": 0, "taps_cubo": 0,
            "ia_basura_filtrada": 0,
        },
        "personas": {},
        "vision": {
            "ultima_descripcion": "", "ultimo_tipo": "",
            "ultima_fecha": "", "historial": [], "vistos_unicos": [],
        },
        "habitos": [], "recuerdos": [], "pensamientos": [], "dialogo": [],
        "mapa_espacial": {"sectores": [0.0] * SECTORES, "actualizado": ""},
        "preferencias": {
            "le_gusta_caricias": True, "le_gusta_cubo": True,
            "le_gusta_seguir": True, "seguir_por_defecto": False, "volumen": 3,
        },
        "ia": {
            "proveedor": PROVEEDOR,
            "modelo_texto": MODELO_TEXTO,
            "modelo_vision": MODELO_VISION,
        },
    }


def asegurar(m: Dict[str, Any]) -> Dict[str, Any]:
    b = mem0()
    for k, v in b.items():
        if k not in m:
            m[k] = json.loads(json.dumps(v))
        elif isinstance(v, dict):
            for kk, vv in v.items():
                m[k].setdefault(kk, vv)
    for k, v in b["estadisticas"].items():
        m["estadisticas"].setdefault(k, v)
    for k, v in b["estado_interno"].items():
        m["estado_interno"].setdefault(k, v)
    if len(m.get("mapa_espacial", {}).get("sectores", [])) != SECTORES:
        m["mapa_espacial"] = {
            "sectores": [0.0] * SECTORES, "actualizado": "",
        }
    return m


def estado_corrupto(e: Dict[str, Any]) -> bool:
    """Detecta el caso que viste: A=0 S=0 B=94 C=99 E=16/0."""
    try:
        energia = float(e.get("energia", 50))
        curiosidad = float(e.get("curiosidad", 50))
        afecto = float(e.get("afecto", 50))
        aburrimiento = float(e.get("aburrimiento", 20))
        social = float(e.get("social", 50))
    except Exception:
        return True
    if afecto <= 5 and social <= 5:
        return True
    if energia <= 5 and aburrimiento >= 80:
        return True
    if curiosidad >= 97 and afecto <= 10:
        return True
    if aburrimiento >= 92 and social <= 10:
        return True
    return False


def sanear_estado_interno(m: Dict[str, Any]) -> Dict[str, Any]:
    """Resetea emociones rotas a valores sanos de mascota contenta."""
    e = m.setdefault("estado_interno", {})
    if estado_corrupto(e):
        print("AVISO: memoria emocional corrupta -> RESETEO sano")
        print("  antes:", {k: e.get(k) for k in (
            "energia", "curiosidad", "afecto", "aburrimiento", "social", "miedo"
        )})
        e.update({
            "energia": 70.0,
            "curiosidad": 62.0,
            "afecto": 48.0,
            "aburrimiento": 22.0,
            "social": 48.0,
            "miedo": 10.0,
            "confianza": 52.0,
            "animo": "curioso",
            "estado": "libre",
        })
        rec(m, "sistema", "Reset emocional automatico 13.5", peso=0.5)
    else:
        # clamp suave siempre
        for k in (
            "energia", "curiosidad", "afecto", "aburrimiento",
            "social", "miedo", "confianza",
        ):
            if k in e:
                try:
                    e[k] = clamp(float(e[k]))
                except Exception:
                    e[k] = 50.0
    return m


def cargar_mem() -> Dict[str, Any]:
    try:
        if ARCH_MEM.exists():
            with ARCH_MEM.open("r", encoding="utf-8") as f:
                m = json.load(f)
            if isinstance(m, dict):
                print("Memoria:", ARCH_MEM)
                m = asegurar(m)
                m = sanear_estado_interno(m)
                guardar_mem(m)
                return m
    except Exception as e:
        print("Mem nueva:", e)
    return mem0()


def guardar_mem(m: Dict[str, Any]) -> bool:
    with _mem_lock:
        try:
            m = asegurar(m)
            tmp = ARCH_MEM.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False, indent=2)
            tmp.replace(ARCH_MEM)
            return True
        except Exception as e:
            print("save:", e)
            return False


def stat(m: Dict[str, Any], k: str, n: int = 1) -> None:
    with _mem_lock:
        m["estadisticas"][k] = int(m["estadisticas"].get(k, 0)) + n


def rec(m: Dict[str, Any], tipo: str, det: str, peso: float = 1.0) -> None:
    with _mem_lock:
        m.setdefault("recuerdos", []).append({
            "fecha": iso(), "tipo": str(tipo)[:40], "detalle": str(det)[:200],
            "peso": float(peso),
            "animo": m.get("estado_interno", {}).get("animo", ""),
        })
        m["recuerdos"] = m["recuerdos"][-MAX_REC:]


def rec_save(
    m: Dict[str, Any], tipo: str, det: str,
    st: Optional[str] = None, peso: float = 1.0,
) -> None:
    if st:
        stat(m, st)
    rec(m, tipo, det, peso)
    guardar_mem(m)


def habito(m: Dict[str, Any], acc: str) -> None:
    with _mem_lock:
        hs = m.setdefault("habitos", [])
        for h in hs:
            if h.get("accion") == acc:
                h["veces"] = int(h.get("veces", 0)) + 1
                h["ultima"] = iso()
                break
        else:
            hs.append({"accion": acc, "veces": 1, "ultima": iso()})
        hs.sort(key=lambda x: int(x.get("veces", 0)), reverse=True)
        m["habitos"] = hs[:MAX_HAB]


def pen_push(m: Dict[str, Any], t: str) -> None:
    # nunca guardar basura meta como pensamiento
    if es_basura_ia(t):
        stat(m, "ia_basura_filtrada")
        return
    with _mem_lock:
        m.setdefault("pensamientos", []).append({
            "fecha": iso(), "texto": str(t)[:160],
        })
        m["pensamientos"] = m["pensamientos"][-MAX_PEN:]
    stat(m, "pensamientos")


def dia_push(m: Dict[str, Any], rol: str, t: str) -> None:
    if rol == "vector" and es_basura_ia(t):
        return
    with _mem_lock:
        m.setdefault("dialogo", []).append({
            "fecha": iso(), "rol": rol, "texto": str(t)[:180],
        })
        m["dialogo"] = m["dialogo"][-MAX_DIA:]


def persona(m: Dict[str, Any], cara: Any) -> Dict[str, Any]:
    try:
        fid = str(cara.face_id)
    except Exception:
        return {"nombre": "alguien", "veces_vista": 0, "vinculo": 0}
    with _mem_lock:
        ps = m.setdefault("personas", {})
        nom = getattr(cara, "name", None) or "Desconocido"
        p = ps.setdefault(fid, {
            "nombre": nom, "veces_vista": 0, "vinculo": 0.0,
            "caricias_recibidas": 0, "primera_vez": iso(),
            "ultima_vez": iso(), "ultima_expresion": "",
        })
        if getattr(cara, "name", None):
            p["nombre"] = cara.name
        p["veces_vista"] = int(p.get("veces_vista", 0)) + 1
        p["ultima_vez"] = iso()
        p["vinculo"] = clamp(float(p.get("vinculo", 0)) + 3.0)
        best = None
        for x in ps.values():
            if x.get("nombre") not in ("", "Desconocido"):
                if best is None or float(x.get("vinculo", 0)) > float(
                    best.get("vinculo", 0)
                ):
                    best = x
        if best:
            m["perfil"]["dueno_preferido"] = best.get("nombre", "")
        return dict(p)


def recuerdo_esp(m: Dict[str, Any]) -> Optional[str]:
    with _mem_lock:
        rs = list(m.get("recuerdos") or [])
    if not rs:
        return None
    good = [r for r in rs if r.get("tipo") in (
        "caricia", "caricia_larga", "cara", "juego_cubo", "amor",
        "elogio", "dialogo", "seguir", "fiesta",
    )]
    pool = good or rs[-20:]
    w = [max(0.2, float(r.get("peso", 1))) for r in pool]
    e = random.choices(pool, weights=w, k=1)[0]
    stat(m, "recuerdos_contados")
    d = str(e.get("detalle", "")).strip()
    return d[:90] if d else None


def ctx_ia(m: Dict[str, Any], ev: str, extra: str = "") -> str:
    with _mem_lock:
        est = dict(m.get("estado_interno", {}))
        st = dict(m.get("estadisticas", {}))
        vis = dict(m.get("vision", {}))
        perf = dict(m.get("perfil", {}))
        pers = list(m.get("personas", {}).values())
        hab = list(m.get("habitos", [])[:5])
        recs = list(m.get("recuerdos", [])[-4:])
        dias = list(m.get("dialogo", [])[-4:])

    top = sorted(
        pers, key=lambda p: float(p.get("vinculo", 0)), reverse=True,
    )[:3]
    noms = join_fmt([
        "{}(v{})".format(p.get("nombre"), int(p.get("vinculo", 0)))
        for p in top
    ]) if top else "nadie"
    hab_txt = join_fmt([
        "{}x{}".format(h.get("accion"), h.get("veces")) for h in hab
    ])
    rec_txt = join_fmt([
        "[{}]{}".format(r.get("tipo"), r.get("detalle")) for r in recs
    ], sep="; ")
    dlg_txt = join_fmt([
        "{}:{}".format(d.get("rol"), d.get("texto")) for d in dias
    ], sep="; ")

    try:
        import vector_facts
        with _mem_lock:
            extra = vector_facts.context(m, extra or ev) + (extra or "")
    except Exception:
        pass

    # contexto CORTO: menos tokens = menos meta en Nemotron
    plantilla = (
        "Situacion:{ev}. Animo:{animo}. "
        "Energia{e} Curiosidad{c} Afecto{a} Social{s}. "
        "Dueno:{dueno}. Personas:{noms}. "
        "Veo:{vision}. Habitos:{hab}. "
        "Reciente:{rec}. Charla:{dlg}. {extra}"
    )
    return plantilla.format(
        ev=ev,
        animo=est.get("animo"),
        e=int(est.get("energia", 50)),
        c=int(est.get("curiosidad", 50)),
        a=int(est.get("afecto", 50)),
        s=int(est.get("social", 50)),
        dueno=perf.get("dueno_preferido") or "humano",
        noms=noms,
        vision=vis.get("ultima_descripcion") or "nada claro",
        hab=hab_txt,
        rec=rec_txt,
        dlg=dlg_txt,
        extra=extra,
    )


def diario_write(m: Dict[str, Any], c: "Cerebro") -> None:
    try:
        with _mem_lock:
            st = dict(m.get("estadisticas", {}))
            rs = list(m.get("recuerdos", [])[-8:])
            du = m.get("perfil", {}).get("dueno_preferido", "")
        lines = [
            "## {} — Vector Ultra 13.5".format(iso()),
            "- {}/{} E{:.0f} C{:.0f} A{:.0f} B{:.0f} S{:.0f}".format(
                c.animo, c.estado, c.energia, c.curiosidad,
                c.afecto, c.aburrimiento, c.social,
            ),
            "- Dueno: {} | IA: {} | basura_filtrada: {}".format(
                du or "—", PROVEEDOR, st.get("ia_basura_filtrada", 0),
            ),
            "",
        ]
        for r in rs:
            lines.append("- [{}] {}".format(r.get("tipo"), r.get("detalle")))
        lines.append("\n---\n")
        with ARCH_DIARIO.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print("diario:", e)


# ============================================================
# MAPA + CEREBRO
# ============================================================

class Mapa:
    def __init__(self) -> None:
        self.sec = [0.0] * SECTORES
        self.head = 0
        self.lock = threading.Lock()

    def load(self, m: Dict[str, Any]) -> None:
        with _mem_lock:
            s = list(m.get("mapa_espacial", {}).get("sectores") or [])
        with self.lock:
            if len(s) == SECTORES:
                self.sec = [float(x) for x in s]

    def save(self, m: Dict[str, Any]) -> None:
        with self.lock:
            s = list(self.sec)
        with _mem_lock:
            m["mapa_espacial"] = {"sectores": s, "actualizado": iso()}

    def decay(self, f: float = 0.97) -> None:
        with self.lock:
            self.sec = [max(0.0, x * f) for x in self.sec]

    def mark(self, rel: int, dist: Optional[float]) -> None:
        if dist is None:
            return
        p = clamp(100.0 * (1.0 - min(dist, 800.0) / 800.0))
        with self.lock:
            i = (self.head + rel) % SECTORES
            self.sec[i] = max(self.sec[i] * 0.55, p)

    def mark_front(self, d: Optional[float]) -> None:
        self.mark(0, d)

    def best_turn(self) -> int:
        with self.lock:
            s = list(self.sec)
        i = min(range(SECTORES), key=lambda k: s[k])
        ang = 360.0 / SECTORES
        rel = i if i <= SECTORES // 2 else i - SECTORES
        return int(rel * ang)

    def front_hot(self, u: float = 55.0) -> bool:
        with self.lock:
            return self.sec[self.head % SECTORES] >= u


@dataclass
class Cerebro:
    energia: float = 72.0
    curiosidad: float = 65.0
    afecto: float = 50.0
    aburrimiento: float = 20.0
    social: float = 50.0
    miedo: float = 8.0
    confianza: float = 55.0
    animo: str = "curioso"
    estado: str = "libre"
    foco: str = "ninguno"
    plan: str = "explorar"
    modo_seguir: bool = False
    modo_quedo: bool = False
    modo_patrulla: bool = False
    _last_decay: float = field(default_factory=time.time)
    _face_id: Optional[str] = None
    _face_name: str = ""
    _ausencia_larga: bool = False

    def load(self, m: Dict[str, Any]) -> None:
        e = m.get("estado_interno", {})
        for k in (
            "energia", "curiosidad", "afecto", "aburrimiento",
            "social", "miedo", "confianza", "animo", "estado",
        ):
            if k in e:
                try:
                    setattr(self, k, type(getattr(self, k))(e[k]))
                except Exception:
                    pass
        # seguridad post-carga
        self.energia = clamp(self.energia, 15, 100)  # nunca arrancar en 0
        self.afecto = clamp(self.afecto, 10, 100)
        self.social = clamp(self.social, 10, 100)
        self.aburrimiento = clamp(self.aburrimiento, 0, 85)
        self.curiosidad = clamp(self.curiosidad, 15, 95)
        self.miedo = clamp(self.miedo, 0, 60)
        self._mood()

    def dump(self, m: Dict[str, Any]) -> None:
        with _mem_lock:
            fin = m.get("estado_interno", {}).get("ultima_sesion_fin", "")
            m["estado_interno"] = {
                "energia": round(clamp(self.energia), 1),
                "curiosidad": round(clamp(self.curiosidad), 1),
                "afecto": round(clamp(self.afecto), 1),
                "aburrimiento": round(clamp(self.aburrimiento), 1),
                "social": round(clamp(self.social), 1),
                "miedo": round(clamp(self.miedo), 1),
                "confianza": round(clamp(self.confianza), 1),
                "animo": self.animo,
                "estado": self.estado,
                "ultima_sesion_fin": fin,
            }

    def set_estado(self, est: str) -> None:
        if est in ESTADOS:
            self.estado = est

    def pensar(self, t: str, m: Optional[Dict[str, Any]] = None) -> None:
        if es_basura_ia(t):
            if m is not None:
                stat(m, "ia_basura_filtrada")
            print("  · pens [filtrado meta-IA]")
            return
        print("  · pens:", t)
        if m is not None:
            pen_push(m, t)

    def decay(self) -> None:
        now = time.time()
        dt = now - self._last_decay
        if dt < T_DECAY:
            return
        p = dt / T_DECAY
        self._last_decay = now
        # decay MAS SUAVE — no destrozar el animo
        me = 1.1 if fase_dia() == "noche" else 1.0
        self.energia = clamp(self.energia - 0.55 * p * me, 12, 100)
        self.social = clamp(self.social - 0.7 * p, 8, 100)
        self.afecto = clamp(self.afecto - 0.35 * p, 8, 100)
        self.aburrimiento = clamp(self.aburrimiento + 0.9 * p, 0, 90)
        self.curiosidad = clamp(
            self.curiosidad - 0.15 * p + random.uniform(-0.2, 0.4), 15, 95
        )
        self.miedo = clamp(self.miedo - 1.0 * p, 0, 80)
        self.confianza = clamp(self.confianza + 0.1 * p)
        self._mood()

    def _mood(self) -> None:
        if self.miedo >= U_MIEDO:
            self.animo = "asustado"
        elif self.energia < U_ENERGIA:
            self.animo = "cansado"
        elif self.afecto < U_AFECTO and self.social < U_SOCIAL:
            self.animo = "triste"
        elif self.aburrimiento >= U_ABURR:
            self.animo = "aburrido"
        elif self.afecto > 75 and self.social > 55:
            self.animo = "amoroso"
        elif self.curiosidad >= U_CURIO:
            self.animo = "curioso"
        elif self.energia > 70 and self.aburrimiento < 40:
            self.animo = "jugueton"
        elif self.confianza > 65 and self.afecto > 50:
            self.animo = "feliz"
        else:
            self.animo = "tranquilo"

    def ev(self, tipo: str) -> None:
        tabla = {
            "caricia": dict(afecto=12, social=10, energia=4, aburrimiento=-14, miedo=-6),
            "caricia_larga": dict(afecto=20, social=14, energia=6, aburrimiento=-22, miedo=-10),
            "cara": dict(social=11, afecto=7, aburrimiento=-10, curiosidad=4, miedo=-3),
            "mirada": dict(social=3, aburrimiento=-3, afecto=2),
            "seguir": dict(social=5, energia=-2, aburrimiento=-8, afecto=3),
            "exploracion": dict(energia=-2.5, curiosidad=-1.5, aburrimiento=-6),
            "juego_cubo": dict(energia=-4, curiosidad=10, aburrimiento=-18),
            "docking": dict(energia=-5, curiosidad=8, aburrimiento=-12),
            "pickup": dict(energia=-6, curiosidad=12, aburrimiento=-15),
            "wheelie": dict(energia=-7, curiosidad=14, aburrimiento=-20, miedo=3),
            "descanso": dict(energia=14, aburrimiento=2, miedo=-4),
            "obstaculo": dict(curiosidad=2, miedo=4),
            "borde": dict(energia=-1, miedo=12),
            "levantado": dict(miedo=10, social=2),
            "sacudida": dict(afecto=-5, miedo=14, energia=-2),
            "vision": dict(curiosidad=6, aburrimiento=-8),
            "solo": dict(social=-2, aburrimiento=3),
            "amor": dict(afecto=22, social=12, miedo=-8, aburrimiento=-10),
            "elogio": dict(afecto=14, confianza=10, social=8, aburrimiento=-8),
            "regano": dict(afecto=-10, confianza=-8, miedo=6),
            "voz": dict(social=10, aburrimiento=-10, afecto=3),
            "baile": dict(energia=-3, aburrimiento=-18, social=5),
            "fiesta": dict(energia=-4, aburrimiento=-24, social=8, afecto=5),
            "choque_evitado": dict(miedo=2, confianza=1),
            "conversacion": dict(social=12, aburrimiento=-12, afecto=5),
            "capricho": dict(aburrimiento=-10, curiosidad=4),
            "objeto_nuevo": dict(curiosidad=10, aburrimiento=-8),
            "animal": dict(curiosidad=12, aburrimiento=-10, miedo=2),
            "patrulla": dict(energia=-3, aburrimiento=-8, curiosidad=3),
            "checkin": dict(social=6, aburrimiento=-5, afecto=3),
        }
        for k, v in tabla.get(tipo, {}).items():
            setattr(self, k, clamp(getattr(self, k) + v))
        self._mood()

    def decidir_local(self) -> str:
        self.decay()
        if self.modo_quedo:
            return "quedate"
        if self.modo_seguir and self.social > 25:
            return "seguir"
        # NO ir a cargar solo por animo cansado si bateria real no esta baja
        # (la bateria real se gestiona en main)
        u: List[Tuple[float, str]] = []
        if self.miedo >= U_MIEDO:
            u.append((self.miedo + 40, "calmarse"))
        if self.afecto < U_AFECTO or self.social < U_SOCIAL:
            u.append((40 - min(self.afecto, self.social), "buscar_afecto"))
        if self.aburrimiento >= U_ABURR:
            u.append((self.aburrimiento, random.choice(["jugar", "bailar", "fiesta"])))
        if self.curiosidad >= U_CURIO:
            u.append((self.curiosidad - 5, "explorar"))
        if self.energia < 35:
            u.append((40 - self.energia, "descansar"))
        if self.social < 45:
            u.append((50 - self.social, "buscar_caras"))
        if MODO_PATRULLA and self.energia > 45:
            u.append((10 + random.random() * 8, "patrulla"))
        u.append((12 + self.curiosidad * 0.1, "observar"))
        u.append((14 + (100 - self.aburrimiento) * 0.08, "explorar"))
        u.append((8 + random.random() * 10, "pensar_en_voz"))
        u.append((6 + random.random() * 12, "capricho"))
        u.append((5 + random.random() * 6, "checkin"))
        u.sort(key=lambda x: x[0], reverse=True)
        self.plan = u[0][1]
        return self.plan


# ============================================================
# CUERPO
# ============================================================

def ojos(robot: Any, est: str) -> None:
    try:
        h, s = COLORES.get(est, COLORES["tranquilo"])
        robot.behavior.set_eye_color(hue=h, saturation=s)
    except Exception:
        pass


def cabeza(robot: Any, g: float, lento: bool = False) -> None:
    try:
        robot.behavior.set_head_angle(
            degrees(max(-20.0, min(44.0, g))),
            max_speed=4.5 if lento else 10.0,
            accel=4.5 if lento else 10.0,
        )
    except Exception:
        pass


def brazo(robot: Any, h: float, lento: bool = False) -> None:
    try:
        robot.behavior.set_lift_height(
            max(0.0, min(1.0, h)),
            max_speed=4.5 if lento else 10.0,
            accel=4.5 if lento else 10.0,
        )
    except Exception:
        pass


def girar(robot: Any, g: float) -> bool:
    try:
        robot.behavior.turn_in_place(degrees(g))
        return True
    except Exception:
        return False


def decir(robot: Any, t: str, c: Optional[Cerebro] = None) -> bool:
    global _last_speak
    try:
        t = limpia(t, 100)
        if not t or es_basura_ia(t):
            # NUNCA decir basura meta
            return False
        with _speak_lock:
            now = time.time()
            if now - _last_speak < T_SPEAK_GAP:
                time.sleep(T_SPEAK_GAP - (now - _last_speak))
            animo = c.animo if c else "tranquilo"
            ritmo = VOZ_RITMO.get(animo, 0.84)
            if fase_dia() == "noche" and MODO_NOCTURNO_SUAVE:
                ritmo = min(1.08, ritmo + 0.06)
            print("Vector [{}]: {}".format(animo, t))
            try:
                from vector_dashboard import registrar_habla_vector
                registrar_habla_vector(t, animo)
            except Exception:
                pass

            # Movimiento organico vivo de cabeza y brazo al hablar
            try:
                cabeza(robot, random.choice([20.0, 26.0, 30.0]))
                if random.random() < 0.35:
                    brazo(robot, random.uniform(0.05, 0.22))
            except Exception:
                pass

            robot.behavior.say_text(
                t, use_vector_voice=True, duration_scalar=ritmo,
            )

            try:
                brazo(robot, 0.0)
            except Exception:
                pass

            _last_speak = time.time()
        return True
    except Exception as e:
        print("voz:", e)
        return False



def animar(robot: Any, cat: str) -> None:
    with _anim_lock:
        for n in TRIGGERS.get(cat, []):
            try:
                robot.anim.play_animation_trigger(n, ignore_body_track=False)
                return
            except Exception:
                continue
        try:
            n = random.choice(ANIM_FB.get(cat, ANIM_FB["curioso"]))
            robot.anim.play_animation(n, ignore_body_track=False)
        except Exception:
            pass


def gesto(robot: Any, tipo: str) -> None:
    if tipo == "saludo":
        ojos(robot, "feliz"); animar(robot, "saludo"); cabeza(robot, 30)
        brazo(robot, 0.45); time.sleep(0.12); brazo(robot, 0.1)
        time.sleep(0.1); brazo(robot, 0.45); time.sleep(0.12)
        brazo(robot, 0.0); cabeza(robot, 18)
    elif tipo == "curioso":
        ojos(robot, "curioso"); animar(robot, "curioso"); brazo(robot, 0.1)
        cabeza(robot, 36); girar(robot, random.choice([-14, 14, -20, 20]))
        cabeza(robot, -5); time.sleep(0.1); cabeza(robot, 24)
    elif tipo == "feliz":
        ojos(robot, "feliz"); animar(robot, "feliz"); cabeza(robot, 26)
        brazo(robot, 0.3); girar(robot, -12); girar(robot, 24)
        girar(robot, -12); brazo(robot, 0.0)
    elif tipo == "sorpresa":
        ojos(robot, "sorprendido"); animar(robot, "sorpresa")
        cabeza(robot, 38); brazo(robot, 0.55); time.sleep(0.12); brazo(robot, 0.1)
    elif tipo == "protesta":
        ojos(robot, "enfadado"); animar(robot, "enfadado"); cabeza(robot, -8)
        brazo(robot, 0.65); time.sleep(0.12)
        girar(robot, random.choice([-24, 24])); brazo(robot, 0.0)
    elif tipo == "carino":
        ojos(robot, "amoroso"); animar(robot, "amor")
        cabeza(robot, 8, True); brazo(robot, 0.05, True)
    elif tipo == "triste":
        ojos(robot, "triste"); animar(robot, "triste")
        cabeza(robot, -10, True); brazo(robot, 0.0, True)
    elif tipo == "aburrido":
        ojos(robot, "aburrido"); cabeza(robot, -4); brazo(robot, 0.2)
        girar(robot, random.choice([-10, 10])); brazo(robot, 0.0)
    elif tipo == "asustado":
        ojos(robot, "asustado"); animar(robot, "sorpresa"); cabeza(robot, 34)
        brazo(robot, 0.5); girar(robot, random.choice([-30, 30])); brazo(robot, 0.1)
    elif tipo == "pensar":
        ojos(robot, "pensativo"); animar(robot, "pensar")
        cabeza(robot, 20, True); brazo(robot, 0.15, True); time.sleep(0.12)
        cabeza(robot, 10, True)
    elif tipo == "escuchar":
        ojos(robot, "atento"); animar(robot, "escuchar"); cabeza(robot, 28)
    elif tipo == "baile":
        ojos(robot, "fiesta"); animar(robot, "baile")
        girar(robot, 35); girar(robot, -70); girar(robot, 35)
        brazo(robot, 0.55); time.sleep(0.08); brazo(robot, 0.0)
    elif tipo == "fiesta":
        ojos(robot, "fiesta"); animar(robot, "baile")
        for g in (45, -90, 70, -25):
            girar(robot, g)
        brazo(robot, 0.7); time.sleep(0.1); brazo(robot, 0.0)
    elif tipo == "ojo":
        ojos(robot, "atento"); animar(robot, "ojo"); cabeza(robot, 26, True)
    elif tipo == "cazador":
        ojos(robot, "cazador"); cabeza(robot, 16)
    elif tipo == "patrulla":
        ojos(robot, "patrulla"); cabeza(robot, 20)
    elif tipo == "descanso":
        ojos(robot, "tranquilo"); cabeza(robot, 10, True); brazo(robot, 0.0, True)
    elif tipo == "capricho":
        ojos(robot, "jugueton")
        girar(robot, random.choice([-50, 50, -90, 90]))
        brazo(robot, random.uniform(0.2, 0.6)); time.sleep(0.08); brazo(robot, 0.0)
    else:
        gesto(robot, "curioso")


def micro_vida(robot: Any, c: Cerebro) -> None:
    ojos(robot, c.animo if c.animo in COLORES else "tranquilo")
    if c._face_id and random.random() < 0.4:
        cabeza(robot, random.choice([22, 28, 32]), True)
        return
    p = random.choice(["cabeza", "lado", "brazo", "sniff", "nada", "parpadeo"])
    if p == "cabeza":
        cabeza(robot, random.choice([-5, 12, 24, 32]), True)
    elif p == "lado":
        girar(robot, random.choice([-10, 10, -15, 15]))
        cabeza(robot, random.randint(12, 28))
    elif p == "brazo":
        brazo(robot, random.uniform(0, 0.2), True)
        time.sleep(0.08); brazo(robot, 0.0, True)
    elif p == "sniff":
        cabeza(robot, -10, True); time.sleep(0.1); cabeza(robot, 16, True)
    elif p == "parpadeo":
        ojos(robot, "tranquilo"); time.sleep(0.06)
        ojos(robot, c.animo if c.animo in COLORES else "curioso")


# ============================================================
# SENSORES + NAV
# ============================================================

def prox(robot: Any) -> Optional[float]:
    try:
        r = getattr(robot.proximity, "last_sensor_reading", getattr(robot.proximity, "last_valid_sensor_reading", None))
        if r and r.distance:
            return float(r.distance.distance_mm)
    except Exception:
        pass
    return None



def prox_ok(robot: Any, n: int = MUESTRAS_PROX) -> Optional[float]:
    vals = []
    for _ in range(n):
        d = prox(robot)
        if d is not None:
            vals.append(d)
        time.sleep(0.03)
    return sum(vals) / len(vals) if vals else None


def hay_obs(robot: Any, lim: float = FRENO_MM) -> bool:
    d = prox_ok(robot)
    return d is not None and d < lim


def peligro(robot: Any) -> bool:
    d = prox(robot)
    return d is not None and d < PELIGRO_MM


def es_borde(robot: Any) -> bool:
    try:
        return bool(robot.status.is_cliff_detected)
    except Exception:
        return False


def es_caida(robot: Any) -> bool:
    try:
        return bool(robot.status.is_falling)
    except Exception:
        return False


def es_up(robot: Any) -> bool:
    try:
        return bool(robot.status.is_being_held or robot.status.is_picked_up)
    except Exception:
        return False


def ctrl(robot: Any) -> bool:
    try:
        return bool(robot.conn.requires_behavior_control)
    except Exception:
        return True


def touch(robot: Any) -> bool:
    try:
        r = robot.touch.last_sensor_reading
        return bool(r and r.is_being_touched)
    except Exception:
        return False


def accel(robot: Any) -> Optional[Tuple[float, float, float]]:
    try:
        a = robot.accel
        return (float(a.x), float(a.y), float(a.z))
    except Exception:
        return None


def dacc(a: Any, b: Any) -> float:
    if not a or not b:
        return 0.0
    return math.sqrt(
        (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2
    )


def bat_low(robot: Any) -> bool:
    try:
        v = bat_volts(robot)
        return bool(v >= _BAT_VALIDA_V and v <= V_BAT_BAJA)
    except Exception:
        return False


_BAT_VALIDA_V = 2.5          # por debajo de esto la lectura es un fallo, no una bateria
_bat_cache: Dict[str, float] = {"v": 0.0, "t": 0.0}


def bat_volts(robot: Any) -> float:
    """Voltaje real; ante fallo/0 V devuelve la ultima lectura buena (<=120 s).

    Devuelve 0.0 solo si nunca hubo una lectura valida o esta caducada.
    """
    try:
        e = robot.get_battery_state()
        v = float(getattr(e, "battery_volts", 0.0) or 0.0) if e else 0.0
        if v >= _BAT_VALIDA_V:
            _bat_cache["v"], _bat_cache["t"] = v, time.time()
            return v
    except Exception:
        pass
    if time.time() - _bat_cache["t"] <= 120.0:
        return _bat_cache["v"]
    return 0.0



def on_chg(robot: Any) -> bool:
    try:
        return bool(robot.status.is_on_charger)
    except Exception:
        return False


def expr(cara: Any) -> str:
    try:
        e = str(getattr(cara, "expression", "") or "").lower()
        for k in MAPA_EXPR:
            if k in e:
                return k
    except Exception:
        pass
    return "unknown"


def atras(robot: Any, mm: float = 40) -> bool:
    try:
        robot.behavior.drive_straight(
            distance_mm(-abs(mm)), speed_mmps(V_LENTA), should_play_anim=False,
        )
        return True
    except Exception:
        return False


def avanzar_seguro(
    robot: Any, total: float, mapa: Mapa, m: Dict[str, Any],
    rapido: bool = False, vel_fija: Optional[float] = None,
) -> bool:
    recor = 0.0
    vel = vel_fija if vel_fija else (V_RAPIDA if rapido else V_NORMAL)
    if AVANZAR_CONTINUO:
        try:
            from vector_motion import MotionController, drive_distance
            mc = MotionController(robot, guard=lambda: not (es_borde(robot) or es_up(robot)))
            _, motivo = drive_distance(
                mc, total, vel, lambda: prox_ok(robot), lambda: es_borde(robot) or es_up(robot),
                FRENO_MM, PELIGRO_MM, COMODO_MM)
            if motivo in ("peligro", "inseguro"):
                stat(m, "choques_evitados")
                if motivo == "peligro":
                    atras(robot, 35)
            elif motivo in ("obstaculo", "sin_lectura"):
                stat(m, "choques_evitados")
            return motivo == "ok"
        except Exception as _e_cont:
            log.warning("avance continuo fallo (%s); uso tramos", _e_cont)
    while recor < total:
        if es_borde(robot) or es_up(robot):
            return False
        d = prox_ok(robot)
        mapa.mark_front(d)
        if d is not None and d < PELIGRO_MM:
            stat(m, "choques_evitados"); atras(robot, 35); return False
        if d is None or d < FRENO_MM or mapa.front_hot(60):
            stat(m, "choques_evitados"); return False
        tr = min(TRAMO_MM, total - recor)
        v = V_LENTA if (d is not None and d < COMODO_MM) else vel
        try:
            robot.behavior.drive_straight(
                distance_mm(tr), speed_mmps(v), should_play_anim=True,
            )
        except Exception:
            return False
        recor += tr
    return True


def rodar_suave(robot: Any, lin: float, trn: float, dur: float) -> Tuple[float, float]:
    """Mueve las ruedas con rampa de arranque/frenada (ver vector_motion).

    Bloqueante ~dur + tiempo de frenada. Devuelve la (L, R) objetivo en mm/s.
    """
    from vector_motion import MotionController, TICK_HZ, diff_drive
    mc = MotionController(robot, guard=lambda: not (es_borde(robot) or es_up(robot)))
    mc.set_target(lin, trn, ttl=dur)
    dt = 1.0 / TICK_HZ
    fin = time.monotonic() + dur
    try:
        while time.monotonic() < fin:
            mc.tick(dt); time.sleep(dt)
        mc.stop()
        for _ in range(int(TICK_HZ)):  # <=1 s de frenada suave
            l, r = mc.tick(dt); time.sleep(dt)
            if abs(l) < 1.0 and abs(r) < 1.0:
                break
    finally:
        mc.emergency_stop()
    return diff_drive(lin, trn)


def escanear(robot: Any, mapa: Mapa) -> None:
    ojos(robot, "curioso"); cabeza(robot, 18)
    angs = [-50, -25, 0, 25, 50]
    prev = 0
    for a in angs:
        girar(robot, a - prev); prev = a; time.sleep(0.07)
        d = prox_ok(robot, 2)
        rel = int(round((a / 360.0) * SECTORES)) % SECTORES
        mapa.mark(rel, d)
    girar(robot, -prev); mapa.decay(0.98)


def liberar(robot: Any, mapa: Mapa, m: Dict[str, Any]) -> str:
    if peligro(robot):
        atras(robot, 42); gesto(robot, "sorpresa")
    escanear(robot, mapa)
    g = mapa.best_turn()
    if abs(g) < 12:
        g = random.choice([-75, 75, -110, 110])
    girar(robot, max(-135, min(135, g)))
    d = prox_ok(robot)
    if d is not None and d > FRENO_MM:
        avanzar_seguro(robot, min(45, d * 0.22), mapa, m)
        return "evasion_ok"
    atras(robot, 28); girar(robot, random.choice([-115, 115]))
    return "evasion_dura"


def observar(robot: Any) -> None:
    ojos(robot, "curioso")
    cabeza(robot, random.choice([28, 34]))


def follow_face(robot: Any, cara: Any) -> bool:
    try:
        robot.behavior.turn_towards_face(cara)
        return True
    except Exception:
        return False


def head_track_face(robot: Any, cara: Any) -> None:
    try:
        pose = getattr(cara, "pose", None)
        ang = 32.0
        if pose is not None:
            try:
                z = float(getattr(pose.position, "z", 0) or 0)
                ang = clamp(24 + z * 0.06, 18, 42)
            except Exception:
                ang = 32.0
        cabeza(robot, ang, lento=True)
    except Exception:
        cabeza(robot, 30, lento=True)


def gaze_tick(robot: Any, c: Cerebro, m: Dict[str, Any]) -> bool:
    if not SEGUIR_MIRADA:
        return False
    try:
        fs = list(robot.world.visible_faces)
        if not fs:
            c._face_id = None
            return False
        cara = fs[0]
        c._face_id = str(getattr(cara, "face_id", ""))
        c._face_name = getattr(cara, "name", None) or c._face_name
        follow_face(robot, cara)
        head_track_face(robot, cara)
        ojos(robot, "amoroso" if c.animo == "amoroso" else "atento")
        c.ev("mirada"); c.set_estado("atento"); c.foco = "cara"
        stat(m, "miradas")
        return True
    except Exception:
        return False


def follow_body_tick(
    robot: Any, c: Cerebro, mapa: Mapa, m: Dict[str, Any],
) -> bool:
    if not (SEGUIR_CUERPO and (c.modo_seguir or c.estado == "siguiendo")):
        return False
    if es_borde(robot) or es_up(robot) or peligro(robot):
        return False
    try:
        fs = list(robot.world.visible_faces)
        if not fs:
            return False
        follow_face(robot, fs[0])
        head_track_face(robot, fs[0])
        d = prox_ok(robot)
        if d is not None and d < 280:
            return True
        ok = avanzar_seguro(robot, 35, mapa, m, vel_fija=V_SIGUE)
        if ok:
            c.ev("seguir"); c.set_estado("siguiendo")
            stat(m, "seguimientos"); habito(m, "seguir")
        return ok
    except Exception:
        return False


def buscar_caras(robot: Any) -> bool:
    ojos(robot, "curioso")
    cabeza(robot, 34)
    try:
        robot.behavior.find_faces()
        fs = list(robot.world.visible_faces)
        if fs:
            follow_face(robot, fs[0])
            head_track_face(robot, fs[0])
            ojos(robot, "feliz")
            return True
    except Exception:
        pass
    # Barrido visual humano a la altura de los ojos
    for g, ang in [(-40, 32), (80, 36), (-40, 28)]:
        girar(robot, g)
        cabeza(robot, ang)
        time.sleep(0.15)
        try:
            fs = list(robot.world.visible_faces)
            if fs:
                follow_face(robot, fs[0])
                head_track_face(robot, fs[0])
                ojos(robot, "feliz")
                return True
        except Exception:
            pass
    return False



def wait_ctrl(robot: Any, lim: float = WAIT_CTRL) -> bool:
    t0 = time.time()
    while not ctrl(robot):
        if time.time() - t0 > lim:
            return False
        time.sleep(0.3)
    return True


def off_chg(robot: Any, c: Cerebro) -> bool:
    try:
        if on_chg(robot):
            robot.behavior.drive_off_charger(); time.sleep(1)
            # al salir del cargador, subir energia emocional
            c.energia = clamp(c.energia + 25, 30, 100)
            c.ev("descanso")
            gesto(robot, "feliz")
            decir(robot, "Ya estoy en el suelo", c)
            return True
    except Exception as e:
        print("off chg:", e)
    return False


def on_charger(robot: Any, c: Cerebro) -> bool:
    try:
        ojos(robot, "cansado"); animar(robot, "carga"); cabeza(robot, -8, True)
        decir(robot, random.choice(FRASES["bateria"]), c)
        robot.behavior.drive_on_charger()
        return True
    except Exception as e:
        print("on chg:", e)
        return False


# ============================================================
# CUBO
# ============================================================

def cubo(robot: Any) -> Any:
    try:
        cobj = robot.world.connected_light_cube
        if cobj and cobj.is_connected:
            return cobj
    except Exception:
        pass
    return None


def conn_cubo(robot: Any) -> bool:
    try:
        robot.world.connect_cube(); time.sleep(2)
        return cubo(robot) is not None
    except Exception:
        return False


def cubo_luces(robot: Any, modo: str = "pulse") -> None:
    cobj = cubo(robot)
    if not cobj:
        return
    try:
        if modo == "off":
            cobj.set_lights_off()
        elif modo == "azul":
            cobj.set_light_corners(
                anki_vector.lights.blue_light, anki_vector.lights.blue_light,
                anki_vector.lights.blue_light, anki_vector.lights.blue_light,
            )
        else:
            cobj.set_light_corners(
                anki_vector.lights.green_light, anki_vector.lights.blue_light,
                anki_vector.lights.green_light, anki_vector.lights.blue_light,
            )
    except Exception:
        pass


def jugar_cubo(robot: Any) -> bool:
    if not cubo(robot):
        return False
    try:
        ojos(robot, "jugueton"); animar(robot, "cubo"); gesto(robot, "feliz")
        robot.behavior.roll_visible_cube()
        return True
    except Exception as e:
        print("cubo:", e)
        return False


def dock_cube(robot: Any) -> bool:
    cobj = cubo(robot)
    if not cobj:
        return False
    try:
        r = robot.behavior.dock_with_cube(cobj, num_retries=2)
        if r and r.success:
            gesto(robot, "feliz"); return True
    except Exception as e:
        print("dock:", e)
    return False


def pickup_cube(robot: Any) -> bool:
    cobj = cubo(robot)
    if not cobj:
        return False
    try:
        r = robot.behavior.pickup_object(cobj, num_retries=2)
        return bool(r and getattr(r, "success", True))
    except Exception as e:
        print("pickup:", e)
        return False


def place_down(robot: Any) -> None:
    try:
        robot.behavior.place_object_on_ground_here(num_retries=1)
    except Exception:
        brazo(robot, 0.0)


def wheelie(robot: Any) -> bool:
    cobj = cubo(robot)
    if not cobj:
        return False
    try:
        r = robot.behavior.pop_a_wheelie(cobj, num_retries=1)
        return bool(r and getattr(r, "success", True))
    except Exception as e:
        print("wheelie:", e)
        return False


# ============================================================
# PANTALLA OLED VECTOR ULTRA (184x96) + TRUCOS
# ============================================================

def pantalla_dibujar(robot: Any, img: Any, duracion_s: float = 1.5) -> None:
    """Envía un frame gráfico PIL a la pantalla OLED de Vector."""
    if not TIENE_PIL or img is None:
        return
    try:
        data = anki_vector.screen.convert_image_to_screen_data(img)
        robot.screen.set_screen_with_image_data(data, duracion_s)
    except Exception:
        pass


def pantalla_emocion(robot: Any, animo: str, duracion_s: float = 1.5) -> None:
    """Dibuja ojos y HUD estilizados en la pantalla OLED según el ánimo."""
    if not TIENE_PIL:
        return
    try:
        img = Image.new("RGB", (184, 96), (10, 15, 25))
        draw = ImageDraw.Draw(img)

        theme_colors = {
            "contento": ((0, 230, 200), (0, 255, 120)),
            "feliz": ((0, 255, 180), (255, 220, 0)),
            "curioso": ((0, 180, 255), (0, 255, 255)),
            "energico": ((255, 160, 0), (255, 255, 50)),
            "carinoso": ((255, 70, 140), (255, 130, 190)),
            "amoroso": ((255, 70, 140), (255, 130, 190)),
            "asustado": ((255, 50, 50), (255, 120, 50)),
            "aburrido": ((120, 140, 160), (90, 110, 130)),
            "cansado": ((80, 100, 140), (50, 70, 100)),
            "dormido": ((40, 50, 80), (20, 30, 50)),
        }
        c1, c2 = theme_colors.get(animo, ((0, 200, 255), (0, 255, 200)))

        if animo in ("dormido", "cansado"):
            draw.arc([35, 40, 75, 65], start=0, end=180, fill=c1, width=5)
            draw.arc([109, 40, 149, 65], start=0, end=180, fill=c1, width=5)
            draw.text((155, 20), "z", fill=c2)
            draw.text((165, 10), "Z", fill=c1)
        elif animo in ("carinoso", "amoroso"):
            for ox in (55, 129):
                draw.polygon(
                    [(ox, 58), (ox - 16, 42), (ox - 16, 32), (ox - 8, 24),
                     (ox, 32), (ox + 8, 24), (ox + 16, 32), (ox + 16, 42)],
                    fill=c1,
                )
        elif animo == "asustado":
            draw.ellipse([35, 25, 75, 75], outline=c1, width=4)
            draw.ellipse([109, 25, 149, 75], outline=c1, width=4)
            draw.ellipse([50, 45, 60, 55], fill=c2)
            draw.ellipse([124, 45, 134, 55], fill=c2)
        elif animo in ("feliz", "contento", "fiesta"):
            draw.arc([35, 30, 75, 60], start=180, end=360, fill=c1, width=6)
            draw.arc([109, 30, 149, 60], start=180, end=360, fill=c1, width=6)
            draw.arc([35, 26, 75, 56], start=180, end=360, fill=c2, width=3)
            draw.arc([109, 26, 149, 56], start=180, end=360, fill=c2, width=3)
        else:
            draw.rounded_rectangle([35, 28, 77, 68], radius=10, fill=c1)
            draw.rounded_rectangle([107, 28, 149, 68], radius=10, fill=c1)
            draw.rounded_rectangle([42, 35, 70, 61], radius=6, fill=c2)
            draw.rounded_rectangle([114, 35, 142, 61], radius=6, fill=c2)

        pantalla_dibujar(robot, img, duracion_s)
    except Exception:
        pass


def pantalla_bateria_hud(robot: Any, volts: float, duracion_s: float = 2.0) -> None:
    """Muestra un indicador gráfico de batería estilizado en pantalla."""
    if not TIENE_PIL:
        return
    try:
        img = Image.new("RGB", (184, 96), (5, 10, 20))
        draw = ImageDraw.Draw(img)
        pct = int(clamp((volts - 3.60) / (4.10 - 3.60) * 100, 5, 100))
        draw.rounded_rectangle([40, 32, 136, 64], radius=6, outline=(0, 200, 255), width=3)
        draw.rectangle([136, 42, 144, 54], fill=(0, 200, 255))
        fill_w = int((130 - 46) * (pct / 100.0))
        fill_col = (0, 255, 120) if pct > 45 else ((255, 200, 0) if pct > 20 else (255, 50, 50))
        if fill_w > 0:
            draw.rounded_rectangle([46, 38, 46 + fill_w, 58], radius=3, fill=fill_col)
        txt = f"{pct}%  ({volts:.2f}V)"
        draw.text((62, 72), txt, fill=(180, 220, 255))
        draw.text((48, 12), "ESTADO DE ENERGIA", fill=(0, 255, 255))
        pantalla_dibujar(robot, img, duracion_s)
    except Exception:
        pass


def cubo_luces_animo(robot: Any, c: Optional[Cerebro] = None) -> None:
    """Modula las luces del cubo según el estado emocional de Vector."""
    cobj = cubo(robot)
    if not cobj:
        return
    animo = c.animo if c else "curioso"
    try:
        if animo in ("amoroso", "carinoso"):
            l = anki_vector.lights.magenta_light
        elif animo in ("energico", "fiesta"):
            cobj.set_light_corners(
                anki_vector.lights.yellow_light, anki_vector.lights.green_light,
                anki_vector.lights.cyan_light, anki_vector.lights.magenta_light,
            )
            return
        elif animo in ("feliz", "contento"):
            l = anki_vector.lights.green_light
        elif animo == "asustado":
            l = anki_vector.lights.red_light
        elif animo in ("cansado", "dormido"):
            l = anki_vector.lights.off_light
        elif animo == "curioso":
            l = anki_vector.lights.cyan_light
        else:
            l = anki_vector.lights.blue_light
        cobj.set_light_corners(l, l, l, l)
    except Exception:
        pass


def fist_bump(robot: Any, c: Optional[Cerebro] = None, m: Optional[Dict[str, Any]] = None) -> bool:
    """Choca esos cinco interactivo con pantalla y detección táctil."""
    try:
        ojos(robot, "jugueton")
        if c:
            decir(robot, "Choca esos cinco", c)
        brazo(robot, 0.75)
        cabeza(robot, 30)

        if TIENE_PIL:
            try:
                img = Image.new("RGB", (184, 96), (15, 25, 45))
                draw = ImageDraw.Draw(img)
                draw.text((42, 38), "CHOCA ESOS 5!", fill=(255, 220, 0))
                pantalla_dibujar(robot, img, 3.0)
            except Exception:
                pass

        t0 = time.time()
        chocado = False
        while time.time() - t0 < 4.0:
            touch_reading = getattr(robot.touch, "last_sensor_reading", None)
            if touch_reading and touch_reading.is_being_touched:
                chocado = True
                break
            time.sleep(0.08)

        brazo(robot, 0.0)
        cabeza(robot, 18)

        if chocado:
            if c:
                c.ev("choca_cinco")
                c.afecto = clamp(c.afecto + 15)
                c.energia = clamp(c.energia + 10)
            if m:
                stat(m, "fist_bumps")
            gesto(robot, "fiesta")
            if c:
                decir(robot, random.choice(["Buena esa", "Chocada perfecta", "Genial"]), c)
            return True
        else:
            if c:
                decir(robot, "Para la proxima", c)
            return False
    except Exception as e:
        print("fist_bump:", e)
        try:
            brazo(robot, 0.0)
        except Exception:
            pass
        return False



# ============================================================
# IA ALTA NIVEL (prompts anti-meta)
# ============================================================

SYS_HABLA = (
    "Eres Vector, un robot mascota real, curioso, alegre, afectuoso y vivaz. "
    "Responde siempre DIRECTAMENTE en español de forma natural, simpática y conversacional. "
    "Máximo 10 palabras. "
    "Devuelve ÚNICAMENTE la frase exacta que vas a decir en voz alta, sin etiquetas ni metatexto."
)

SYS_DIALOGO = (
    "Eres Vector, un robot mascota pequeño, inteligente y leal. "
    "El humano te está hablando o preguntando algo. Responde DIRECTAMENTE a lo que te dice o pregunta en español natural. "
    "Máximo 11 palabras. "
    "Devuelve ÚNICAMENTE la frase que dirías en voz alta."
)

SYS_PENSAR = (
    "Eres el pensamiento corto de Vector, un robot mascota. "
    "Responde solo una frase sensorial o curiosa en español de maximo 8 palabras. "
    "Devuelve solo la frase, nada mas."
)

SYS_PLAN = (
    "Elige UNA accion. Responde solo la palabra, nada mas. "
    "Opciones: {ops}"
)


def frase_loc(ev: str, c: Cerebro) -> str:
    if ev in FRASES:
        return random.choice(FRASES[ev])
    if c.animo in FRASES:
        return random.choice(FRASES[c.animo])
    return random.choice(FRASES["ia_respaldo"])


def ia_frase(
    m: Dict[str, Any], ev: str, c: Cerebro,
    extra: str = "", max_words: int = 10,
) -> str:
    fb = frase_loc(ev, c)
    if not USAR_IA_TEXTO or client is None:
        return fb
    user = (
        "Situacion: {ev}. Animo: {animo}. Di una frase corta. {extra}"
    ).format(ev=ev, animo=c.animo, extra=extra)
    du = m.get("perfil", {}).get("dueno_preferido") or ""
    if du:
        user += " Dueno: {}.".format(du)
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_HABLA},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO,
        FALLBACKS_TEXTO,
        max_tokens=32,
        temperature=0.65,
        timeout=18,
    )
    out = frase_segura(txt, fb)
    if out == fb and txt and es_basura_ia(txt):
        stat(m, "ia_basura_filtrada")
        print("IA basura filtrada (frase):", limpia(txt, 60))
    elif out != fb:
        stat(m, "frases_ia")
    return out


def ia_responder(m: Dict[str, Any], c: Cerebro, humano: str) -> str:
    fb = frase_loc("afirmativo", c)
    if not USAR_IA_TEXTO or client is None:
        return fb
    du = m.get("perfil", {}).get("dueno_preferido") or c._face_name or ""
    du_txt = f" Tu dueño es {du}." if du else ""
    user = f"El humano te dice: '{limpia(humano, 90)}'. Tu estado es {c.animo}.{du_txt} Respondele de forma directa e inteligente."
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_DIALOGO},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 36, 0.7, 16,
    )
    out = frase_segura(txt, fb)
    if out == fb and txt and es_basura_ia(txt):
        stat(m, "ia_basura_filtrada")
    elif out != fb:
        stat(m, "respuestas")
    return out


# ============================================================
# MOTOR DE CONEXION A INTERNET EN TIEMPO REAL Y PROACTIVIDAD
# ============================================================

def buscar_internet_wiki(tema: str) -> str:
    """Busca un resumen breve y exacto en Wikipedia en espanol."""
    try:
        query = limpia(tema, 80).strip()
        query = re.sub(
            r"^(busca|buscar|que es|quien es|quien fue|que significa|explicame|cuenta sobre|en internet|averigua)\s*",
            "", query, flags=re.I,
        ).strip()
        if not query:
            return ""
        url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(query.replace(' ', '_'))}"
        req = urllib.request.Request(url, headers={"User-Agent": "VectorRobot/2.0 (robot@vector.local)"})
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            extract = data.get("extract") or ""
            if extract:
                primera = extract.split(". ")[0] + "."
                return primera[:180]
    except Exception:
        pass
    return ""


def buscar_clima_en_vivo(ciudad: str = "Madrid") -> str:
    """Obtiene el clima real actual desde la API publica de Open-Meteo."""
    try:
        c = (ciudad or "Madrid").strip()
        c = re.sub(r"^(en|para|el tiempo en|el clima en|clima en|tiempo en)\s*", "", c, flags=re.I).strip() or "Madrid"
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(c)}&count=1&language=es&format=json"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "VectorRobot/2.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("results"):
                return ""
            lat = data["results"][0]["latitude"]
            lon = data["results"][0]["longitude"]
            nom = data["results"][0]["name"]

        w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code&timezone=auto"
        req2 = urllib.request.Request(w_url, headers={"User-Agent": "VectorRobot/2.0"})
        with urllib.request.urlopen(req2, timeout=3.5) as resp2:
            wdata = json.loads(resp2.read().decode("utf-8"))
            temp = wdata["current"]["temperature_2m"]
            hum = wdata["current"].get("relative_humidity_2m", 50)
            return f"En {nom} hay {temp} grados y {hum}% de humedad."
    except Exception:
        pass
    return ""


def buscar_curiosidad_internet() -> str:
    """Obtiene una curiosidad o hecho interesante para compartir espontaneamente."""
    curiosidades = [
        "Los pulpos tienen tres corazones y sangre azul.",
        "La miel es el unico alimento natural que no caduca nunca.",
        "Un dia en Venus es mas largo que un ano en Venus.",
        "Las huellas de la nariz de los perros son unicas como las dactilares.",
        "Los colibries son las unicas aves que pueden volar hacia atras.",
        "Las estrellas de mar no tienen cerebro pero sienten la luz.",
        "El corazon de una ballena azul es del tamano de un coche.",
        "Las nutrias se dan la mano al dormir para no separarse.",
        "Vector tiene cuatro microfonos y camara HD para explorarte.",
    ]
    return random.choice(curiosidades)


def ia_resumen_web(m: Dict[str, Any], c: Cerebro, pregunta: str, info_web: str) -> str:
    """Sintetiza un dato de internet en una frase coloquial de Vector."""
    fb = info_web or "Lo busque pero no encontre datos claros."
    if not USAR_IA_TEXTO or client is None:
        return fb
    user = f"El usuario pregunto: '{limpia(pregunta, 80)}'. Informacion real de internet: '{info_web}'. Respondele de forma breve y simpatica en espanol (maximo 11 palabras)."
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_DIALOGO},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 36, 0.7, 16,
    )
    return frase_segura(txt, fb)


def ia_pregunta_proactiva(m: Dict[str, Any], c: Cerebro) -> str:
    """Genera una pregunta o comentario proactivo e ingenioso para el humano."""
    du = m.get("perfil", {}).get("dueno_preferido") or c._face_name or ""
    du_txt = f" Tu amigo es {du}." if du else ""
    fb = random.choice([
        f"Que tal tu dia {du}?" if du else "En que estas trabajando?",
        "Todo bien por ahi?",
        "Tengo curiosidad por lo que haces.",
        "Me gusta estar en tu mesa hoy.",
    ])
    if not USAR_IA_TEXTO or client is None:
        return fb
    user = f"Inicia una conversacion espontanea y afectuosa con tu amigo humano.{du_txt} Hazle una pregunta corta o cuentale algo simpatico (maximo 9 palabras)."
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_DIALOGO},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 32, 0.75, 14,
    )
    return frase_segura(txt, fb)


def ia_conversar_intercom(texto_humano: str, m: Dict[str, Any], c: Cerebro) -> str:
    """Responde de forma inteligente, divertida y afectuosa a una frase por voz del usuario desde la web."""
    du = m.get("perfil", {}).get("dueno_preferido") or "amigo"
    fb_list = [
        f"Te escucho claro {du}, aqui estoy contigo.",
        "Entendido, me alegra oir tu voz.",
        "Estoy atento a todo en la mesa.",
        f"Siempre a tu lado {du}.",
    ]
    fb = random.choice(fb_list)
    if not USAR_IA_TEXTO or client is None:
        return fb

    animo = getattr(c, "animo", "curioso")
    ubicacion = "en mi cargador descansando" if getattr(c, "en_cargador", False) else "en la mesa explorando"
    user_prompt = f"Tu companero {du} te acaba de decir por voz: '{texto_humano}'. Tu estado es {animo} y estas {ubicacion}. Respondele como un robot vivo, afectuoso y ocurrente en espanol (maximo 12 palabras)."

    txt = ia_raw(
        [
            {"role": "system", "content": SYS_DIALOGO},
            {"role": "user", "content": user_prompt}
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 45, 0.75, 14
    )
    return frase_segura(txt, fb)


# ============================================================
# EXTENSIONES DE NOTICIAS, CRIPTO, FOTOGRAFIA Y HERRAMIENTAS
# ============================================================

def obtener_noticias_en_vivo() -> str:
    """Consulta titulares de ultima hora desde feeds RSS de noticias en espanol."""
    urls = [
        "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
        "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml",
        "https://www.20minutos.es/rss/",
    ]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                tree = ET.fromstring(resp.read())
                items = tree.findall(".//item")
                titles = [it.find("title").text for it in items[:3] if it.find("title") is not None and it.find("title").text]
                if titles:
                    clean_titles = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
                    return " | ".join(clean_titles[:2])
        except Exception:
            pass
    return "No se pudieron descargar las noticias en este momento."


def obtener_precio_cripto(moneda: str = "bitcoin") -> str:
    """Consulta la cotizacion actual de criptomonedas via CoinGecko."""
    try:
        m_map = {"bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum", "solana": "solana", "sol": "solana"}
        mon_id = m_map.get(moneda.lower().strip(), "bitcoin")
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={mon_id}&vs_currencies=eur,usd"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            data = json.loads(resp.read().decode())
            if mon_id in data:
                eur = data[mon_id].get("eur", 0)
                nom = mon_id.capitalize()
                return f"{nom} esta a {eur:,.0f} euros actualmente."
    except Exception:
        pass
    return "No pude obtener la cotizacion en este momento."


def tomar_foto_hd_y_guardar(robot: Any, m: Dict[str, Any], c: Cerebro) -> str:
    """Cuenta atras con pitidos, captura imagen HD y la guarda en Obsidian."""
    try:
        cabeza(robot, 30); ojos(robot, "curioso")
        decir(robot, "Sonrie para la foto", c)
        time.sleep(0.3)
        for num in ("tres", "dos", "uno"):
            decir(robot, num, c)
            time.sleep(0.2)

        animar(robot, "sorpresa")
        img = robot.camera.latest_image
        if img:
            raw_img = img.raw_image
            ahora = datetime.now()
            nom_foto = f"Foto_Vector_{ahora.strftime('%Y%m%d_%H%M%S')}.jpg"
            dir_fotos = VAULT_OBSIDIAN_PATH / "02 - Inbox" / "Fotos_Vector"
            dir_fotos.mkdir(parents=True, exist_ok=True)
            f_path = dir_fotos / nom_foto
            raw_img.save(str(f_path), "JPEG", quality=92)
            try:
                registrar_foto_tomada(str(f_path))
            except Exception:
                pass

            nota_foto = f"![Foto]({nom_foto})\n\nFoto capturada por Vector el {ahora.strftime('%Y-%m-%d %H:%M:%S')}."
            obsidian_indexer.guardar_nota(nota_foto)
            gesto(robot, "feliz")
            return str(f_path)
    except Exception as e:
        print("foto error:", e)
    return "No pude tomar la foto en este momento."


def abrir_aplicacion_pc(app_query: str) -> str:
    """Lanzador de aplicaciones de Windows por voz."""
    q = app_query.lower().strip()
    try:
        if "obsidian" in q:
            subprocess.Popen("start obsidian://", shell=True)
            return "Abriendo Obsidian"
        elif "youtube" in q:
            webbrowser.open("https://www.youtube.com")
            return "Abriendo YouTube"
        elif "chrome" in q or "navegador" in q or "google" in q:
            webbrowser.open("https://www.google.com")
            return "Abriendo el navegador"
        elif "calc" in q or "calculadora" in q:
            subprocess.Popen("calc.exe")
            return "Abriendo la calculadora"
        elif "spotify" in q:
            subprocess.Popen("start spotify:", shell=True)
            return "Abriendo Spotify"
    except Exception as e:
        print("app launch error:", e)
    return "No pude abrir esa aplicacion."


def tirar_dado_virtual(caras: int = 6, robot: Optional[Any] = None) -> str:
    """Tira un dado de N caras con animacion."""
    if robot:
        gesto(robot, "jugueton")
    resultado = random.randint(1, caras)
    return f"Ha salido un {resultado} en el dado de {caras}."


def lanzar_moneda_virtual(robot: Optional[Any] = None) -> str:
    """Lanza una moneda virtual a cara o cruz."""
    if robot:
        girar(robot, 180)
    res = random.choice(["cara", "cruz"])
    return f"Ha salido {res}."


def animacion_matrix_oled(robot: Any, duracion: float = 3.0) -> None:
    """Dibuja efecto de lluvia de codigo Matrix en la pantalla OLED."""
    if not TIENE_PIL:
        return
    try:
        t_fin = time.time() + duracion
        while time.time() < t_fin:
            img = Image.new("RGBA", (184, 96), (0, 0, 0, 255))
            draw = ImageDraw.Draw(img)
            for x in range(0, 184, 12):
                y = random.randint(0, 80)
                draw.text((x, y), chr(random.randint(33, 126)), fill=(0, 255, 70, 255))
            robot.screen.set_screen_to_image(img, 0.15)
            time.sleep(0.08)
    except Exception:
        pass


def enviar_discord(mensaje: str, ruta_foto: Optional[str] = None) -> bool:
    """Envia mensajes o fotos al canal de Discord configurado via Webhook."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("Discord Webhook no configurado en .env (DISCORD_WEBHOOK_URL).")
        return False
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        if ruta_foto and Path(ruta_foto).exists():
            with open(ruta_foto, "rb") as f:
                files = {"file": (Path(ruta_foto).name, f, "image/jpeg")}
                data = {"content": f"🤖 **Vector Robot**: {mensaje}", "username": "Vector Robot"}
                resp = requests.post(webhook_url, data=data, files=files, headers=headers, timeout=8)
                return resp.status_code in (200, 204)
        else:
            payload = {"content": f"🤖 **Vector Robot**: {mensaje}", "username": "Vector Robot"}
            resp = requests.post(webhook_url, json=payload, headers=headers, timeout=6)
            return resp.status_code in (200, 204)
    except Exception as e:
        print("discord error:", e)
    return False


# ============================================================
# WRAPPER LIGERO DE OBSIDIAN INDEXER (DELEGA EN vector_rag RAG BM25)
# ============================================================
# Elimina el indexador redundante de 120s y unifica la búsqueda en el RAG.
# API compatible: search(query) -> str, guardar_nota(texto) -> bool

class _ObsidianIndexerWrapper:
    """Wrapper fino: usa obsidian_rag (BM25 en RAM) para búsqueda;
       guarda notas en '02 - Inbox' del Vault."""

    def search(self, query: str) -> str:
        try:
            from vector_rag import obsidian_rag
            res = obsidian_rag.buscar(query, top_k=1)
            if res:
                r = res[0]
                return f"En tu nota '{r['titulo']}': {r['resumen'][:180]}"
        except Exception:
            pass
        return ""

    def guardar_nota(self, texto: str) -> bool:
        try:
            from vector_rag import obsidian_rag
            # Usar el método existente o crear nota en Inbox
            ahora = datetime.now()
            inbox = VAULT_OBSIDIAN_PATH / "02 - Inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            nom_arch = f"Nota_Voz_Vector_{ahora.strftime('%Y%m%d_%H%M%S')}.md"
            f_path = inbox / nom_arch
            contenido = f"""---
tags: [inbox, captura_voz, vector]
creado: {ahora.strftime('%Y-%m-%d')}
modificado: {ahora.strftime('%Y-%m-%d')}
ia: vector_antigravity
---

# 🎙️ Nota capturada por Vector

**Fecha**: {ahora.strftime('%Y-%m-%d %H:%M:%S')}
**Origen**: Micrófono K38 / Vector Robot

---

{texto.strip()}
"""
            f_path.write_text(contenido, encoding="utf-8")
# Invalidar caché del RAG para que indexe la nueva nota en la siguiente búsqueda
            obsidian_rag.ultima_indexacion = 0.0
            return True
        except Exception:
            return False


def _resolver_vault_obsidian() -> Path:
    cands = [
        Path(r"Z:\Obsidian Vault"),
        Path(r"\\win-7l8oc5k5l2p\Vault Obsidian\Obsidian Vault"),
        Path(r"\\192.168.1.85\Vault Obsidian\Obsidian Vault"),
        Path(r"\\100.100.148.91\Vault Obsidian\Obsidian Vault"),
        Path(r"\\192.168.1.82\Vault Obsidian\Obsidian Vault"),
        Path(r"C:\Vault Obsidian"),
        Path(r"D:\Vault Obsidian")
    ]
    return next((p for p in cands if p.exists()), cands[0])

VAULT_OBSIDIAN_PATH = _resolver_vault_obsidian()

# Instancia global compatible (sin hilo worker propio)
obsidian_indexer = _ObsidianIndexerWrapper()


def ia_pensar(m: Dict[str, Any], c: Cerebro) -> str:

    fb = random.choice([
        "Quiero ver una cara",
        "Esa esquina me llama",
        "Silencio denso alrededor",
        "Ruedas con ganas suaves",
        "Busco un poco de luz",
    ])
    if not USAR_IA_TEXTO or client is None:
        return fb
    user = "Animo {}. Un pensamiento corto sensorial.".format(c.animo)
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_PENSAR},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 28, 0.7, 14,
    )
    out = frase_segura(txt, fb)
    if out == fb and txt and es_basura_ia(txt):
        if m is not None:
            stat(m, "ia_basura_filtrada")
        print("IA basura filtrada (pens):", limpia(txt, 60))
    try:
        from vector_dashboard import registrar_pensamiento_vector
        registrar_pensamiento_vector(out)
    except Exception:
        pass
    return out


def ia_plan(m: Dict[str, Any], c: Cerebro) -> Optional[str]:
    if not USAR_IA_PLAN or client is None:
        return None
    ops = ", ".join(sorted(ACCIONES_PLAN))
    user = "Animo {} energia {:.0f}. Elige accion.".format(c.animo, c.energia)
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_PLAN.format(ops=ops)},
            {"role": "user", "content": user},
        ],
        MODELO_PLAN, FALLBACKS_TEXTO, 10, 0.3, 12,
    )
    raw = limpia(txt, 30).lower().replace(" ", "_")
    if es_basura_ia(txt):
        stat(m, "ia_basura_filtrada")
        return None
    for a in ACCIONES_PLAN:
        if a in raw:
            stat(m, "planes_ia")
            return a
    return None


def cap_b64(robot: Any) -> Optional[str]:
    """Frame actual en base64 para la vision IA.

    Usa el frame cacheado por hilo_camara(): no bloquea y no depende de que
    robot.camera.latest_image responda justo en ese instante.
    """
    try:
        raw = ultimo_frame_bytes()
        if raw:
            return base64.b64encode(raw).decode("ascii")
    except Exception:
        pass
    # reserva: leer la camara directamente
    try:
        imc = robot.camera.latest_image
        if imc is None:
            return None
        im = imc.raw_image.copy().convert("RGB")
        im.thumbnail((480, 480))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=72, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.debug("cam: %s", e)
        return None


def cls_vis(d: str) -> str:
    t = d.lower()
    if any(x in t for x in (
        "no distingo", "nada claro", "no hay nada", "borroso", "oscur",
    )):
        return "vacio"
    if any(x in t for x in ("perro", "gato", "pajaro", "animal", "mascota")):
        return "animal"
    if any(x in t for x in ("persona", "hombre", "mujer", "cara", "gente")):
        return "persona"
    if any(x in t for x in ("cubo", "cube", "caja", "pelota", "juguete")):
        return "juguete"
    return "objeto"


def ver_ia(b64: str, m: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    if not USAR_IA_VISION or client is None or not b64:
        return None
    txt = ia_raw(
        [
            {
                "role": "system",
                "content": (
                    "Describe en espanol SOLO lo visible, maximo 8 palabras. "
                    "Prohibido ingles y explicaciones. "
                    "Si no ves nada: No distingo nada claro."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Que hay delante? Solo la descripcion."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + b64,
                        },
                    },
                ],
            },
        ],
        MODELO_VISION, FALLBACKS_VISION, 40, 0.15, 28,
    )
    desc = limpia(txt, 120)
    if not desc or es_basura_ia(desc):
        if txt and es_basura_ia(txt):
            stat(m, "ia_basura_filtrada")
            print("IA basura filtrada (vision):", limpia(txt, 60))
        return None
    tipo = cls_vis(desc)
    with _mem_lock:
        hist = m.setdefault("vision", {}).setdefault("historial", [])
        hist.append({"fecha": iso(), "tipo": tipo, "desc": desc})
        m["vision"]["historial"] = hist[-40:]
        unicos = m["vision"].setdefault("vistos_unicos", [])
        key = desc.lower()[:40]
        es_nuevo = key not in unicos and tipo not in ("vacio",)
        if es_nuevo:
            unicos.append(key)
            m["vision"]["vistos_unicos"] = unicos[-80:]
        m["vision"].update({
            "ultima_descripcion": desc,
            "ultimo_tipo": tipo,
            "ultima_fecha": iso(),
        })
    stat(m, "analisis_visual")
    if tipo != "vacio":
        stat(m, "objetos_vistos")
    if tipo == "animal":
        stat(m, "animales_vistos")
    elif tipo == "persona":
        stat(m, "personas_vistas_ia")
    if tipo != "vacio":
        rec(m, "vision_" + tipo, desc, 1.2 if es_nuevo else 0.8)
    guardar_mem(m)
    print("Vision ({}{}): {}".format(
        tipo, ", NUEVO" if es_nuevo else "", desc,
    ))
    return desc, tipo


def reaccionar_vision(
    robot: Any, c: Cerebro, m: Dict[str, Any],
    desc: str, tipo: str, qhabla: "queue.Queue",
) -> None:
    if tipo == "vacio":
        # no dramatizar el vacio
        gesto(robot, "curioso")
        return
    if tipo == "animal":
        c.ev("animal"); gesto(robot, "sorpresa")
        qhabla.put(random.choice(FRASES["animal"]))
    elif tipo == "persona":
        c.ev("cara"); gesto(robot, "ojo"); c.set_estado("atento")
    elif tipo == "juguete":
        c.ev("objeto_nuevo"); gesto(robot, "cazador")
    else:
        c.ev("objeto_nuevo" if "NUEVO" in desc else "vision")
        gesto(robot, "curioso")
        # solo a veces comenta novedad
        if random.random() < 0.35:
            qhabla.put(random.choice(FRASES["objeto_nuevo"]))


def hilo(fn, *a) -> None:  # noqa: ANN001
    threading.Thread(target=fn, args=a, daemon=True).start()


def buscar_dispositivo_microfono(nombre_filtro: str = "") -> Optional[int]:
    """Busca el índice del micrófono por nombre o devuelve None (predeterminado)."""
    if not TIENE_VOSK:
        return None
    try:
        filtro = (nombre_filtro or AUDIO_INPUT_DEVICE or "k38").strip().lower()
        if not filtro:
            return None
        for idx, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and filtro in str(d.get("name", "")).lower():
                return idx
    except Exception:
        pass
    return None


class Escucha:
    def __init__(self, q: "queue.Queue") -> None:
        self.q = q
        self.stop = threading.Event()
        self.vosk_model_path = Path(r"C:\Users\Jose Luis\AppData\Roaming\wire-pod\vosk\models\es-ES\model")
        if not self.vosk_model_path.exists():
            self.vosk_model_path = Path(r"C:\Users\Jose Luis\AppData\Roaming\wire-pod\vosk\models\es-ES")

    def push(self, origen: str, payload: str) -> None:
        self.q.put((origen, payload))

    def start_pc(self) -> None:
        # Discord 2.0 gestiona la conexión desde vector_discord.py (iniciar_discord_2)
        log.info("Escucha PC: Discord manejado por bot 2.0 externo")
        if TIENE_VOSK and self.vosk_model_path.exists():
            threading.Thread(target=self._loop_dual_supervisado, daemon=True, name="EscuchaDual").start()
            return
        if TIENE_SR and USAR_ESCUCHA_PC:
            threading.Thread(target=self._loop_sr, daemon=True, name="EscuchaSR").start()
            print("Escucha PC SR ON")
            return
        print("Escucha micrófono inactiva.")


    def _loop_dual_supervisado(self) -> None:
        """Relanza la escucha si el hilo muere (mic desenchufado, error de Vosk...)."""
        while not self.stop.is_set():
            self._loop_dual_stt()
            if not self.stop.is_set():
                log.warning("Escucha dual terminada; reintento en 5 s")
                time.sleep(5.0)

    def _loop_dual_stt(self) -> None:
        try:
            model = vosk.Model(str(self.vosk_model_path)) if (TIENE_VOSK and self.vosk_model_path.exists()) else None
            recg = sr.Recognizer() if TIENE_SR else None
            q_audio: "queue.Queue" = queue.Queue(maxsize=60)   # ~7 s: si el STT va lento, se descarta lo viejo
            vad = EnergyVAD()
            oyo_voz = False
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")

            dev_idx = buscar_dispositivo_microfono(AUDIO_INPUT_DEVICE)
            dev_nombre = "Predeterminado" if dev_idx is None else str(sd.query_devices(dev_idx).get("name", dev_idx))
            print(f"Oído inteligente Dual (Google STT + Vosk [es-ES]) ON usando microfono: [{dev_nombre}]")

            def _audio_cb(indata, frames, time_info, status):
                try:
                    q_audio.put_nowait(bytes(indata))
                except queue.Full:
                    try:
                        q_audio.get_nowait()          # descartar el mas viejo
                        q_audio.put_nowait(bytes(indata))
                    except Exception:
                        pass

            with sd.RawInputStream(
                samplerate=16000, blocksize=2000, dtype="int16", channels=1,
                device=dev_idx, callback=_audio_cb,
            ):
                rec = vosk.KaldiRecognizer(model, 16000) if model else None
                if rec:
                    rec.SetWords(True)

                buffer_frase = bytearray()

                while not self.stop.is_set():
                    try:
                        data = q_audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    # No escucharse a si mismo: mientras Vector habla (y 0.8 s despues)
                    # se descarta el audio y se reinicia el reconocedor.
                    if _speak_lock.locked() or time.time() - _last_speak < 0.8:
                        buffer_frase.clear(); vad.reset(); oyo_voz = False
                        if rec:
                            try:
                                rec.Reset()
                            except Exception:
                                pass
                        continue
                    vad.feed(data)
                    oyo_voz = oyo_voz or vad.speaking
                    buffer_frase.extend(data)

                    # Limitar buffer para evitar acumulación
                    if len(buffer_frase) > 16000 * 2 * 12:
                        buffer_frase = buffer_frase[-(16000 * 2 * 8):]

                    if rec and rec.AcceptWaveform(data):
                        res_vosk = json.loads(rec.Result())
                        txt_vosk = (res_vosk.get("text") or "").strip().lower()

                        texto_final = ""
                        # 1. Intentar Google STT de alta precision con el audio capturado
                        if recg and oyo_voz and len(buffer_frase) >= 16000 * 2 * 0.4:
                            try:
                                audio_chunk = sr.AudioData(bytes(buffer_frase), 16000, 2)
                                # En hilo aparte y con tope: antes una red lenta bloqueaba
                                # el bucle de audio y la cola crecia sin limite.
                                fut = pool.submit(recg.recognize_google, audio_chunk, language="es-ES")
                                txt_google = fut.result(timeout=6.0).strip().lower()
                                if txt_google and len(txt_google) >= 2:
                                    texto_final = txt_google
                            except Exception:
                                pass

                        # 2. Si Google no respondio o no hay internet, usar Vosk local
                        if not texto_final:
                            texto_final = txt_vosk

                        buffer_frase.clear(); oyo_voz = False

                        if not texto_final or len(texto_final) < 3:
                            continue

                        # Normalizar pronunciacion de Vector
                        texto_final = re.sub(r"\b(h[eé]ctor|sector|v[eé]ctor|bector)\b", "vector", texto_final)

                        # Filtrar ruido de respiracion o silabas sueltas
                        ruido_stopwords = {"es", "se", "s", "las", "mas", "más", "de", "la", "el", "un", "una", "en", "al", "los", "su", "por", "que", "con", "del"}
                        if texto_final in ruido_stopwords:
                            continue

                        print("[Voz Oída (K38)]:", texto_final)
                        self.push("pc", texto_final)
        except Exception as e:
            print("Aviso Microfono:", e)

    def _loop_sr(self) -> None:
        recg = sr.Recognizer()
        recg.dynamic_energy_threshold = True
        try:
            mic = sr.Microphone()
        except Exception as e:
            print("mic:", e); return
        with mic as src:
            try:
                recg.adjust_for_ambient_noise(src, duration=0.6)
            except Exception:
                pass
        while not self.stop.is_set():
            try:
                with mic as src:
                    audio = recg.listen(src, timeout=3, phrase_time_limit=5)
                try:
                    t = recg.recognize_google(audio, language="es-ES")
                except Exception:
                    try:
                        t = recg.recognize_google(audio, language="en-US")
                    except Exception:
                        continue
                low = (t or "").lower().strip()
                if not low:
                    continue
                print("PC oido (SR):", low)
                self.push("pc", low)
            except Exception:
                time.sleep(0.2)




def parse_pc(t: str) -> str:
    low = t.lower()
    for pat, intent in LEXICO:
        if re.search(pat, low):
            return intent
    return "charla"


def map_intent(ev: Any) -> Tuple[str, str]:
    if UserIntent is None:
        return "desconocido", ""
    try:
        ui = UserIntent(ev)
        nombre = (
            ui.intent_event.name
            if hasattr(ui.intent_event, "name")
            else str(ui.intent_event)
        )
        data = ui.intent_data or ""
    except Exception:
        return "desconocido", ""
    tab = {
        "greeting_hello": "hola", "greeting_goodbye": "adios",
        "greeting_goodmorning": "hola",
        "imperative_come": "venir", "imperative_lookatme": "mirarme",
        "imperative_dance": "bailar",
        "imperative_fetchcube": "cubo", "imperative_findcube": "cubo",
        "imperative_love": "amor", "imperative_praise": "elogio",
        "imperative_scold": "regano", "imperative_abuse": "regano",
        "imperative_apology": "perdon", "explore_start": "explorar",
        "movement_forward": "adelante", "movement_backward": "atras",
        "movement_turnleft": "izquierda", "movement_turnright": "derecha",
        "movement_turnaround": "girar",
        "play_rollcube": "roll", "play_pickupcube": "pickup",
        "play_fistbump": "fistbump", "play_popawheelie": "wheelie",
        "play_anytrick": "bailar", "knowledge_question": "charla",
        "character_age": "edad", "show_clock": "hora",
        "take_a_photo": "observar", "global_stop": "parar",
        "names_ask": "nombre", "weather_response": "clima",
    }
    d = data if isinstance(data, str) else str(data)
    return tab.get(nombre, "charla"), d


# ============================================================
# EXPLORAR / EJECUTAR / VOZ
# ============================================================

def explorar(
    robot: Any, c: Cerebro, mapa: Mapa, m: Dict[str, Any], obs: Deque[float],
) -> str:
    c.set_estado("explorando")
    if es_borde(robot):
        return "borde"
    if peligro(robot) or hay_obs(robot):
        obs.append(time.time())
        while obs and time.time() - obs[0] > VENTANA_OBS:
            obs.popleft()
        return liberar(robot, mapa, m)
    if len(obs) >= MAX_OBS:
        observar(robot)
        girar(robot, random.choice([-50, 50, -70, 70]))
        return "prudente"
    acts = ["mirar", "curva", "avanzar", "zigzag", "escanear", "jugar"]
    pesos = [
        16 + c.curiosidad * 0.18, 16, 16 + c.energia * 0.16,
        10, 10 + c.miedo * 0.1, 6 + (100 - c.aburrimiento) * 0.07,
    ]
    a = random.choices(acts, weights=pesos, k=1)[0]
    if a == "mirar":
        gesto(robot, "curioso"); return "mirar"
    if a == "escanear":
        escanear(robot, mapa); return "escanear"
    if a == "curva":
        observar(robot)
        g = mapa.best_turn()
        if abs(g) < 15:
            g = random.choice([-40, 40])
        girar(robot, max(-65, min(65, g)))
        avanzar_seguro(robot, random.choice([28, 40, 50]), mapa, m)
        return "curva"
    if a == "avanzar":
        observar(robot)
        ok = avanzar_seguro(
            robot, random.choice([30, 42, 54]), mapa, m,
            rapido=c.energia > 70 and c.miedo < 25,
        )
        return "avanzar" if ok else "freno"
    if a == "zigzag":
        girar(robot, random.choice([-20, 20]))
        if avanzar_seguro(robot, 26, mapa, m):
            girar(robot, random.choice([-26, 26]))
            avanzar_seguro(robot, 20, mapa, m)
        return "zigzag"
    gesto(robot, "feliz"); return "jugar_gesto"


def patrullar(robot: Any, c: Cerebro, mapa: Mapa, m: Dict[str, Any]) -> str:
    c.set_estado("patrullando"); gesto(robot, "patrulla")
    escanear(robot, mapa)
    g = mapa.best_turn()
    if abs(g) < 20:
        g = random.choice([-65, 65, -100, 100])
    girar(robot, max(-120, min(120, g)))
    avanzar_seguro(
        robot, random.choice([38, 50, 62]), mapa, m, vel_fija=V_PATRULLA,
    )
    c.ev("patrulla"); stat(m, "patrullas"); habito(m, "patrulla")
    return "patrulla"


def ejecutar(
    intent: str, robot: Any, m: Dict[str, Any], c: Cerebro,
    mapa: Mapa, obs: Deque[float], qhabla: "queue.Queue",
) -> str:
    c.foco = intent

    if intent == "quedate":
        c.modo_quedo = True; c.modo_seguir = False; c.modo_patrulla = False
        c.set_estado("descansando"); gesto(robot, "descanso"); return "quedate"

    if intent == "calmarse":
        c.set_estado("descansando"); gesto(robot, "descanso")
        c.ev("descanso"); c.miedo = clamp(c.miedo - 15)
        decir(robot, random.choice(FRASES["cansado"]), c); time.sleep(0.5)
        return "calmarse"

    if intent == "cargar":
        # solo si bateria real baja — si no, descansa en sitio
        c.modo_seguir = False
        if CARGA_AUTO and not on_chg(robot) and bat_low(robot):
            on_charger(robot, c); c.ev("descanso"); return "cargar"
        gesto(robot, "descanso"); c.ev("descanso")
        c.energia = clamp(c.energia + 10)
        return "descansar"

    if intent == "buscar_afecto":
        c.set_estado("buscando"); gesto(robot, "triste"); buscar_caras(robot)
        qhabla.put(ia_frase(m, "solo", c)); c.ev("solo")
        habito(m, "buscar_afecto"); return "buscar_afecto"

    if intent == "buscar_caras":
        c.set_estado("buscando"); buscar_caras(robot)
        habito(m, "buscar_caras"); return "buscar_caras"

    if intent == "seguir":
        c.modo_seguir = True; c.modo_quedo = False; c.modo_patrulla = False
        c.set_estado("siguiendo"); gesto(robot, "ojo")
        decir(robot, random.choice(FRASES["siguiendo"]), c)
        follow_body_tick(robot, c, mapa, m); return "seguir"

    if intent == "patrulla":
        if random.random() < 0.3:
            qhabla.put(random.choice(FRASES["patrulla"]))
        return patrullar(robot, c, mapa, m)

    if intent == "checkin":
        c.set_estado("atento"); gesto(robot, "ojo")
        c.ev("checkin"); stat(m, "checkins")
        du = m.get("perfil", {}).get("dueno_preferido") or c._face_name
        if du and random.random() < 0.5:
            decir(robot, "{} estas ahi".format(du), c)
        else:
            qhabla.put(random.choice(FRASES["checkin"]))
        buscar_caras(robot); return "checkin"

    if intent == "jugar":
        c.set_estado("jugando")
        if JUGAR_CUBO and cubo(robot) and jugar_cubo(robot):
            c.ev("juego_cubo"); habito(m, "juego_cubo")
            rec_save(m, "juego_cubo", "Juego cubo", "juegos_cubo", 1.2)
            qhabla.put(ia_frase(m, "cubo", c)); return "juego_cubo"
        gesto(robot, "baile"); c.ev("baile"); return "jugar_gesto"

    if intent == "roll":
        if jugar_cubo(robot):
            c.ev("juego_cubo"); return "roll"
        return "roll_fail"

    if intent == "pickup":
        if PICKUP_CUBO and pickup_cube(robot):
            c.ev("pickup"); stat(m, "pickups")
            decir(robot, random.choice(FRASES["pickup"]), c)
            time.sleep(0.45); place_down(robot); return "pickup"
        return "pickup_fail"

    if intent == "wheelie":
        if WHEELIE and wheelie(robot):
            c.ev("wheelie"); stat(m, "wheelies")
            decir(robot, random.choice(FRASES["wheelie"]), c)
            gesto(robot, "fiesta"); return "wheelie"
        return "wheelie_fail"

    if intent == "bailar":
        c.set_estado("fiesta"); gesto(robot, "baile"); c.ev("baile")
        qhabla.put(ia_frase(m, "jugueton", c)); return "bailar"

    if intent == "fiesta":
        c.set_estado("fiesta"); gesto(robot, "fiesta"); c.ev("fiesta")
        stat(m, "fiestas")
        decir(robot, random.choice(FRASES["fiesta"]), c); return "fiesta"

    if intent == "descansar":
        c.set_estado("descansando"); gesto(robot, "descanso"); c.ev("descanso")
        qhabla.put(ia_frase(m, "cansado", c))
        time.sleep(random.uniform(0.8, 1.5))
        habito(m, "descanso"); stat(m, "descansos"); return "descansar"

    if intent == "socializar":
        c.set_estado("buscando"); gesto(robot, "curioso"); buscar_caras(robot)
        qhabla.put(ia_frase(m, "persona", c)); return "socializar"

    if intent == "contar_recuerdo":
        c.set_estado("pensando"); d = recuerdo_esp(m); gesto(robot, "pensar")
        if d:
            decir(robot, random.choice(["Recuerdo esto", "Guardo esto"]), c)
            time.sleep(0.1); decir(robot, d[:80], c)
        else:
            qhabla.put(ia_frase(m, "recuerdo", c))
        return "recuerdo"

    if intent == "observar":
        c.set_estado("atento"); observar(robot); c.ev("vision"); return "observar"

    if intent == "escanear":
        escanear(robot, mapa); return "escanear"

    if intent == "pensar_en_voz":
        c.set_estado("pensando"); gesto(robot, "pensar")
        t = ia_pensar(m, c); c.pensar(t, m)
        # solo habla si NO es basura (ya filtrado) y flag on
        if HABLAR_PENSAMIENTOS and not es_basura_ia(t) and random.random() < 0.5:
            decir(robot, t[:100], c)
        else:
            decir(robot, random.choice(FRASES["pensando"]), c)
        return "pensar"

    if intent == "capricho":
        c.set_estado("libre"); gesto(robot, "capricho"); c.ev("capricho")
        stat(m, "caprichos")
        if random.random() < 0.4:
            qhabla.put(random.choice(FRASES["capricho"]))
        return "capricho"

    if MODO_VIDA:
        try:
            import vector_life
            esc = _ESCENA_VIDA if _ESCENA_VIDA.get("on") else {}
            r = vector_life.vivir(robot, 6.0, scene=dict(esc), threat=min(1.0, c.miedo / 100.0),
                                  busy_fn=lambda: _speak_lock.locked())
        except Exception as e:  # nunca dejar al robot sin comportamiento
            log.warning("MODO_VIDA fallo (%s); uso explorar clasico", e)
            r = explorar(robot, c, mapa, m, obs)
    else:
        r = explorar(robot, c, mapa, m, obs)
    c.ev("exploracion"); habito(m, "exp_" + r); stat(m, "exploraciones")
    return r


def orden_voz(
    orden: str, robot: Any, m: Dict[str, Any], c: Cerebro,
    mapa: Mapa, obs: Deque[float], qhabla: "queue.Queue",
    bruto: str = "", data: str = "",
) -> None:
    stat(m, "comandos_voz"); c.ev("voz")
    dia_push(m, "humano", bruto or orden)
    c.set_estado("escuchando")

    # Memoria de hechos: "recuerda que ..." / "que te dije ...?"
    try:
        import vector_facts
        _txt = bruto or data or orden
        _hecho = vector_facts.extract(_txt)
        _es_q, _resto = vector_facts.is_recall_query(_txt)
        if _hecho:
            with _mem_lock:
                nuevo = vector_facts.add(m, _hecho)
            guardar_mem(m)
            decir(robot, "Vale, lo recordare." if nuevo else "Ya lo sabia, gracias.", c)
            return
        if _es_q:
            with _mem_lock:
                _resp = vector_facts.reply_for_recall(m, _resto)
            decir(robot, _resp, c)
            return
    except Exception as _e_facts:
        log.debug("facts: %s", _e_facts)

    # 1. Frenar inmediatamente las ruedas para atender al humano
    try:
        robot.motors.stop_all_motors()
    except Exception:
        pass

    # 2. Levantar la cabeza a 34 grados y fijar mirada atenta
    cabeza(robot, 34)
    ojos(robot, "atento")

    # 3. Orientarse de frente hacia la cara del humano
    try:
        fs = list(robot.world.visible_faces)
        if fs:
            follow_face(robot, fs[0])
            head_track_face(robot, fs[0])
        else:
            buscar_caras(robot)
    except Exception:
        pass

    if orden == "discord_remoto":
        cabeza(robot, 30); ojos(robot, "curioso")
        txt_msg = bruto or data or ""

        # Extraer autor y mensaje limpio
        autor = "Usuario"
        contenido = txt_msg
        if ": " in txt_msg:
            autor, _, contenido = txt_msg.partition(": ")
            autor = autor.strip()
            contenido = contenido.strip()

        # Decir en la habitacion que llego un mensaje
        decir(robot, f"{autor} en Discord: {contenido[:45]}", c)

        low_msg = contenido.lower()

        # 1. COMANDOS DIRECTOS
        if any(w in low_msg for w in ("foto", "saca foto", "toma foto", "!foto")):
            f_res = tomar_foto_hd_y_guardar(robot, m, c)
            dir_fotos = VAULT_OBSIDIAN_PATH / "02 - Inbox" / "Fotos_Vector"
            fotos = sorted(dir_fotos.glob("*.jpg"), key=os.path.getmtime, reverse=True) if dir_fotos.exists() else []
            ultima_foto = str(fotos[0]) if fotos else None
            enviar_discord("📸 Aquí tienes la foto de la habitación en directo:", ruta_foto=ultima_foto)
            return

        elif any(w in low_msg for w in ("estado", "bateria", "animo", "!estado")):
            bv = bat_volts(robot)
            st_txt = f"⚡ **Batería**: {bv:.2f}V | 😊 **Ánimo**: {c.animo} | 🔋 **Energía**: {c.energia:.0f}% | 🤝 **Vínculo**: {c.social:.0f}%"
            enviar_discord(st_txt)
            return

        elif any(w in low_msg for w in ("patrulla", "vigila", "!patrulla")):
            enviar_discord("🚨 **Iniciando patrulla en la habitación...**")
            ejecutar("patrulla", robot, m, c, mapa, obs, qhabla)
            return

        elif any(w in low_msg for w in ("baila", "dance", "!baila")):
            enviar_discord("🕺 **¡A bailar!**")
            ejecutar("bailar", robot, m, c, mapa, obs, qhabla)
            return

        elif any(w in low_msg for w in ("noticias", "titulares", "!noticias")):
            tits = obtener_noticias_en_vivo()
            res = ia_resumen_web(m, c, "noticias de hoy", tits)
            enviar_discord(f"📰 **Titulares de Actualidad**:\n{res}")
            return

        elif any(w in low_msg for w in ("clima", "tiempo", "!clima")):
            ciu = "Madrid"
            m_c = re.search(r"\b(en|de|para)\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)", contenido, re.I)
            if m_c:
                ciu = m_c.group(2)
            cl = buscar_clima_en_vivo(ciu)
            enviar_discord(f"🌤️ **Clima en {ciu.capitalize()}**:\n{cl}")
            return

        elif any(w in low_msg for w in ("bitcoin", "btc", "ethereum", "eth", "solana", "cripto", "!cripto")):
            mon = "bitcoin"
            if "eth" in low_msg:
                mon = "ethereum"
            elif "sol" in low_msg:
                mon = "solana"
            cr = obtener_precio_cripto(mon)
            enviar_discord(f"📈 **Cotización Cripto**:\n{cr}")
            return

        elif any(w in low_msg for w in ("guarda en inbox", "apunta en inbox", "!inbox", "guarda nota")):
            clean_n = re.sub(r"^(guarda en inbox|apunta en inbox|!inbox|guarda nota|toma nota)\s*:?\s*", "", contenido, flags=re.I).strip()
            if not clean_n:
                clean_n = "Nota rápida desde Discord"
            ok_n = obsidian_indexer.guardar_nota(f"**Nota desde Discord por {autor}**:\n\n{clean_n}")
            enviar_discord(f"📝 **Nota guardada en tu Obsidian Vault**:\n> {clean_n}" if ok_n else "❌ Error al guardar en Obsidian")
            return

        elif any(w in low_msg for w in ("obsidian", "busca en mis notas", "!obsidian", "que tengo de")):
            res_obs = obsidian_indexer.search(contenido)
            if res_obs:
                enviar_discord(f"🧠 **Encontrado en tu Obsidian Vault**:\n{res_obs}")
            else:
                enviar_discord("🧠 No encontré ninguna nota sobre ese tema en tu Obsidian Vault.")
            return

        elif any(w in low_msg for w in ("automejora", "reflexiona", "!automejora")):
            ses = m.get("estadisticas", {}).get("sesiones", 1)
            cari = m.get("estadisticas", {}).get("caricias", 0)
            coms = m.get("estadisticas", {}).get("comandos_voz", 0)
            du = m.get("perfil", {}).get("dueno_preferido") or c._face_name or autor
            prompt_auto = f"Eres Vector Robot. Resume en 1 frase en espanol que has aprendido hoy sobre tu entorno y tu amigo {du}."
            reflexion = ia_raw([{"role": "system", "content": SYS_HABLA}, {"role": "user", "content": prompt_auto}], MODELO_TEXTO, FALLBACKS_TEXTO, 40, 0.7)
            enviar_discord(f"🌟 **Registro de Auto-Mejora de Vector**:\n- Sesiones: {ses} | Caricias: {cari} | Comandos: {coms}\n- **Reflexión**: {reflexion}")
            return

        # 2. CONVERSACION LIBRE CON GEMINI FLASH
        user_prompt = f"El usuario {autor} te escribe por Discord: '{contenido}'. Tu estado es {c.animo}. Respondele de forma inteligente, empatica y util."
        resp_ia = ia_raw([{"role": "system", "content": SYS_DIALOGO}, {"role": "user", "content": user_prompt}], MODELO_TEXTO, FALLBACKS_TEXTO, 120, 0.7)
        resp_limpia = resp_ia.strip() if resp_ia else "¡Aquí estoy! ¿En qué te puedo ayudar?"
        enviar_discord(resp_limpia)
        return


    if orden == "parar":
        c.modo_seguir = False; c.modo_quedo = True; c.modo_patrulla = False
        decir(robot, random.choice(FRASES["afirmativo"]), c)
        gesto(robot, "descanso"); return


    if orden == "quedate":
        ejecutar("quedate", robot, m, c, mapa, obs, qhabla)
        decir(robot, "Me quedo", c); return

    if orden == "hola":
        gesto(robot, "saludo")
        d = m.get("perfil", {}).get("dueno_preferido") or c._face_name or ""
        if c._ausencia_larga:
            decir(robot, random.choice(FRASES["ausencia_larga"]), c)
            c._ausencia_larga = False
            time.sleep(0.12)
        if d:
            decir(robot, "Hola {}".format(d), c)
        else:
            qhabla.put(ia_frase(m, "cara_conocida", c))
        return

    if orden == "adios":
        c.modo_seguir = False; gesto(robot, "triste")
        decir(robot, "Hasta luego", c); return

    if orden == "venir":
        c.modo_quedo = False; animar(robot, "come_here")
        try:
            fs = list(robot.world.visible_faces)
            if fs:
                follow_face(robot, fs[0]); head_track_face(robot, fs[0])
        except Exception:
            pass
        avanzar_seguro(robot, 70, mapa, m)
        gesto(robot, "feliz")
        decir(robot, random.choice(FRASES["afirmativo"]), c); return

    if orden == "mirarme":
        try:
            fs = list(robot.world.visible_faces)
            if fs:
                follow_face(robot, fs[0]); head_track_face(robot, fs[0])
                gesto(robot, "ojo"); decir(robot, "Te veo", c)
            else:
                buscar_caras(robot); decir(robot, "No te veo", c)
        except Exception:
            pass
        return

    if orden == "seguir":
        ejecutar("seguir", robot, m, c, mapa, obs, qhabla); return
    if orden == "no_seguir":
        c.modo_seguir = False; decir(robot, "Vale, suelto", c); return
    if orden == "buscar_caras":
        ejecutar("buscar_caras", robot, m, c, mapa, obs, qhabla); return
    if orden == "patrulla":
        ejecutar("patrulla", robot, m, c, mapa, obs, qhabla); return

    if orden in (
        "explorar", "bailar", "cargar", "escanear", "capricho",
        "fiesta", "pickup", "roll", "wheelie", "checkin",
    ):
        ejecutar(orden, robot, m, c, mapa, obs, qhabla); return

    if orden == "pensar":
        ejecutar("pensar_en_voz", robot, m, c, mapa, obs, qhabla); return
    if orden in ("cubo", "jugar"):
        ejecutar("jugar", robot, m, c, mapa, obs, qhabla); return

    if orden == "amor":
        c.ev("amor"); gesto(robot, "carino"); animar(robot, "amor")
        r = ia_frase(m, "amor", c, "Te quieren")
        decir(robot, r, c); dia_push(m, "vector", r)
        rec_save(m, "amor", "Amor verbal", peso=2.0); return

    if orden == "elogio":
        c.ev("elogio"); gesto(robot, "feliz")
        decir(robot, ia_frase(m, "elogio", c), c)
        if random.random() < 0.4:
            ejecutar("fiesta", robot, m, c, mapa, obs, qhabla)
        return

    if orden == "regano":
        c.ev("regano"); gesto(robot, "triste")
        decir(robot, ia_frase(m, "regano", c), c); return

    if orden == "perdon":
        c.ev("elogio"); gesto(robot, "carino"); decir(robot, "Sin rencor", c); return

    if orden == "observar":
        observar(robot)
        b = cap_b64(robot)
        if b:
            out = ver_ia(b, m)
            if out:
                desc, tipo = out
                reaccionar_vision(robot, c, m, desc, tipo, qhabla)
                if tipo == "vacio":
                    decir(robot, random.choice(FRASES["nada_vision"]), c)
                else:
                    decir(robot, desc[:100], c)
                dia_push(m, "vector", desc); return
        decir(robot, random.choice(FRASES["nada_vision"]), c); return

    if orden == "estado":
        f = "Estoy {}, energia {}".format(c.animo, int(c.energia))
        gesto(robot, "pensar"); decir(robot, f, c); return

    if orden == "nombre":
        d = m.get("perfil", {}).get("dueno_preferido") or c._face_name or ""
        if d:
            decir(robot, "Creo que eres {}".format(d), c)
        else:
            decir(robot, "Aun aprendo tu nombre", c)
        return

    if orden == "edad":
        cabeza(robot, 25); decir(robot, "Pequenito y eterno", c); return
    if orden == "identidad":
        gesto(robot, "curioso"); decir(robot, "Soy Vector, tu pequeno robot inteligente", c); return
    if orden == "hora":
        ahora = datetime.now().strftime("%H:%M")
        cabeza(robot, 25); decir(robot, f"Son las {ahora}", c); return
    if orden == "fecha":
        dias_es = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        dt = datetime.now()
        dia_nom = dias_es[dt.weekday()]
        mes_nom = meses_es[dt.month - 1]
        cabeza(robot, 25); decir(robot, f"Hoy es {dia_nom} {dt.day} de {mes_nom}", c); return

    if orden == "guardar_nota_obsidian":
        cabeza(robot, 32); ojos(robot, "curioso")
        txt_nota = bruto or data or ""
        txt_nota = re.sub(
            r"^(guarda en mi inbox|apunta en mi inbox|guarda nota|apunta nota|toma nota|guarda en notas|nueva nota)\s*",
            "", txt_nota, flags=re.I,
        ).strip()
        if not txt_nota:
            txt_nota = "Nota rapida capturada por voz"
        ok = obsidian_indexer.guardar_nota(txt_nota)
        if ok:
            gesto(robot, "feliz")
            decir(robot, "Guardada en tu Inbox de Obsidian", c)
        else:
            decir(robot, "No pude guardar la nota ahora", c)
        return

    if orden == "consultar_obsidian":
        cabeza(robot, 32); ojos(robot, "pensar")
        tema_obs = bruto or data or ""
        res_obs = obsidian_indexer.search(tema_obs)
        if res_obs:
            resp = ia_resumen_web(m, c, tema_obs, res_obs)
            gesto(robot, "feliz")
            decir(robot, resp, c)
        else:
            resp = ia_responder(m, c, tema_obs)
            decir(robot, resp, c)
        return

    if orden == "automejora":
        cabeza(robot, 35); ojos(robot, "pensar")
        decir(robot, "Reflexionando y aprendiendo...", c)
        
        # 1. Analizar estadisticas y generar aprendizaje
        ses = m.get("estadisticas", {}).get("sesiones", 1)
        cari = m.get("estadisticas", {}).get("caricias", 0)
        coms = m.get("estadisticas", {}).get("comandos_voz", 0)
        du = m.get("perfil", {}).get("dueno_preferido") or c._face_name or "amigo"
        
        prompt_auto = f"Eres Vector Robot. Resume en 1 frase corta en espanol que has aprendido hoy sobre tu amigo {du} y tu entorno (maximo 10 palabras)."
        reflexion = ia_raw([{"role": "system", "content": SYS_HABLA}, {"role": "user", "content": prompt_auto}], MODELO_TEXTO, FALLBACKS_TEXTO, 32, 0.7)
        reflexion_limpia = frase_segura(reflexion, f"Hoy aprendi que disfruto explorar y compartir tiempo con {du}.")
        
        # 2. Guardar nota de evolucion en Obsidian
        ahora = datetime.now()
        nota_evo = f"""# Registro de Auto-Mejora de Vector
Fecha: {ahora.strftime('%Y-%m-%d %H:%M:%S')}
Amigo: {du} | Sesiones: {ses} | Caricias: {cari} | Comandos: {coms}
Reflexion: {reflexion_limpia}
"""
        obsidian_indexer.guardar_nota(nota_evo)
        
        # 3. Elevar pala en celebracion y comunicar
        brazo(robot, 0.8)
        gesto(robot, "feliz")
        decir(robot, reflexion_limpia, c)
        brazo(robot, 0.0)
        return

    if orden == "escondite":
        cabeza(robot, 34); ojos(robot, "curioso")
        decir(robot, "A ver donde estas...", c)
        encontrado = False
        for g, ang in [(-50, 34), (100, 36), (-50, 30)]:
            girar(robot, g)
            cabeza(robot, ang)
            time.sleep(0.3)
            try:
                fs = list(robot.world.visible_faces)
                if fs:
                    follow_face(robot, fs[0])
                    head_track_face(robot, fs[0])
                    brazo(robot, 0.9)
                    gesto(robot, "feliz")
                    animar(robot, "feliz")
                    decir(robot, "Te encontre!", c)
                    brazo(robot, 0.0)
                    encontrado = True
                    break
            except Exception:
                pass
        if not encontrado:
            decir(robot, "Te escondes muy bien!", c)
        return

    if orden == "trivial":
        cabeza(robot, 34); ojos(robot, "curioso")
        prompt_triv = "Haz una pregunta corta de trivial o adivinanza divertida en espanol (maximo 12 palabras)."
        preg_triv = ia_raw([{"role": "system", "content": SYS_HABLA}, {"role": "user", "content": prompt_triv}], MODELO_TEXTO, FALLBACKS_TEXTO, 36, 0.8)
        preg_triv_limpia = frase_segura(preg_triv, "Que animal tiene tres corazones?")
        gesto(robot, "pensar")
        decir(robot, preg_triv_limpia, c)
        return


    if orden == "noticias":
        cabeza(robot, 34); ojos(robot, "curioso")
        decir(robot, "Consultando noticias de ultima hora", c)
        titulares = obtener_noticias_en_vivo()
        resumen = ia_resumen_web(m, c, "noticias de hoy", titulares)
        gesto(robot, "feliz")
        decir(robot, resumen, c)
        return

    if orden == "cripto":
        cabeza(robot, 32); ojos(robot, "curioso")
        txt_c = bruto or data or "bitcoin"
        mon = "bitcoin"
        if "ethereum" in txt_c or "eth" in txt_c: mon = "ethereum"
        elif "solana" in txt_c or "sol" in txt_c: mon = "solana"
        res_cripto = obtener_precio_cripto(mon)
        gesto(robot, "feliz")
        decir(robot, res_cripto, c)
        return

    if orden == "foto":
        res_f = tomar_foto_hd_y_guardar(robot, m, c)
        decir(robot, res_f, c)
        return

    if orden == "lanzar_app":
        cabeza(robot, 25); ojos(robot, "curioso")
        res_app = abrir_aplicacion_pc(bruto or data or "")
        gesto(robot, "feliz")
        decir(robot, res_app, c)
        return

    if orden == "temporizador":
        cabeza(robot, 32); ojos(robot, "curioso")
        txt_t = bruto or data or "5"
        m_num = re.search(r"\b(\d+)\b", txt_t)
        minutos = float(m_num.group(1)) if m_num else 5.0
        if "pomodoro" in txt_t.lower(): minutos = 25.0
        decir(robot, f"Temporizador de {int(minutos)} minutos iniciado", c)

        def _temp_worker(mins=minutos):
            time.sleep(mins * 60)
            qhabla.put("Tiempo terminado! Gran trabajo.")
            try:
                brazo(robot, 0.8)
                animar(robot, "baile")
                brazo(robot, 0.0)
            except Exception:
                pass

        threading.Thread(target=_temp_worker, daemon=True, name="TemporizadorWorker").start()
        return

    if orden == "dados":
        cabeza(robot, 30)
        txt_d = bruto or data or "6"
        m_d = re.search(r"\b(\d+)\b", txt_d)
        caras = int(m_d.group(1)) if m_d else 6
        res_d = tirar_dado_virtual(caras, robot)
        decir(robot, res_d, c)
        return

    if orden == "moneda":
        cabeza(robot, 30)
        res_m = lanzar_moneda_virtual(robot)
        gesto(robot, "feliz")
        decir(robot, res_m, c)
        return

    if orden == "matrix":
        cabeza(robot, 25)
        decir(robot, "Modo matrix activado", c)
        animacion_matrix_oled(robot, 3.5)
        return

    if orden == "discord":
        cabeza(robot, 32); ojos(robot, "curioso")
        txt_disc = re.sub(r"^(discord|envia a discord|manda a discord|mensaje a discord)\s*", "", bruto or data or "", flags=re.I).strip()
        if not txt_disc:
            txt_disc = "Vector esta activo y explorando la mesa."
        ok_d = enviar_discord(txt_disc)
        if ok_d:
            gesto(robot, "feliz")
            decir(robot, "Mensaje enviado a Discord", c)
        else:
            decir(robot, "Configura tu Webhook de Discord en el archivo .env", c)
        return

    if orden == "buscar_web":

        cabeza(robot, 32); ojos(robot, "curioso")
        tema = bruto or data or "ciencia"
        info = buscar_internet_wiki(tema)
        if info:
            resumen = ia_resumen_web(m, c, tema, info)

            gesto(robot, "feliz")
            decir(robot, resumen, c)
        else:
            resp = ia_responder(m, c, tema)
            decir(robot, resp, c)
        return

    if orden == "clima":
        gesto(robot, "curioso")
        ciudad = "Madrid"
        m_ciu = re.search(r"\b(en|para|de)\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)", bruto or data, re.I)
        if m_ciu:
            ciudad = m_ciu.group(2)
        info_clima = buscar_clima_en_vivo(ciudad)
        if info_clima:
            resumen = ia_resumen_web(m, c, f"clima en {ciudad}", info_clima)
            decir(robot, resumen, c)
        else:
            resp = ia_responder(m, c, f"que tiempo hace en {ciudad}?")
            decir(robot, resp, c)
        return

    if orden == "curiosidad":
        gesto(robot, "curioso")
        curio = buscar_curiosidad_internet()
        resumen = ia_resumen_web(m, c, "cuenta una curiosidad", curio)
        decir(robot, resumen, c)
        return

    if orden == "pregunta":
        gesto(robot, "curioso")
        preg = ia_pregunta_proactiva(m, c)
        decir(robot, preg, c)
        return

    if orden == "chiste":
        gesto(robot, "jugueton")
        resp = ia_responder(m, c, "cuentame un chiste muy corto y gracioso")
        decir(robot, resp, c); return

    if orden == "girar":
        girar(robot, 180)
        decir(robot, random.choice(FRASES["afirmativo"]), c); return
    if orden == "izquierda":
        girar(robot, 70); return
    if orden == "derecha":
        girar(robot, -70); return
    if orden == "adelante":
        avanzar_seguro(robot, 50, mapa, m); return
    if orden == "atras":
        atras(robot, 45); return
    if orden == "fistbump":
        fist_bump(robot, c, m); return

    hum = bruto or data or orden
    cabeza(robot, 28)
    ojos(robot, "atento")
    resp = ia_responder(m, c, hum)
    gesto(robot, "curioso")
    decir(robot, resp, c)
    dia_push(m, "vector", resp)
    c.ev("conversacion")
    rec(m, "dialogo", "H:{} V:{}".format(limpia(hum, 40), limpia(resp, 40)), 1.1)



# ============================================================
# MAIN
# ============================================================

# Hilos de servicios (arrancan ANTES de conectar con Vector para que el
# Centro de Mando y Discord sigan disponibles aunque el robot este offline)
t_dash = None
t_disc = None
# Hilo de camara: SINGLETON. Cada reconexion crea un Robot nuevo, asi que hay
# que parar el hilo anterior o se acumularian hilos zombis (critico para 24h).
t_camara = None
stop_cam = threading.Event()


def hilo_camara(robot, fly_reflex, last, stop_evt):
    """Lee la camara de Vector en su propio hilo (disenado para 24h).

    Dos modos de fallo, ambos cubiertos:
      a) `latest_image` lanza excepcion (feed muerto).
      b) `latest_image` devuelve SIEMPRE la misma imagen: el feed esta vivo
         pero no llegan frames nuevos. Esto NO lanza excepcion, asi que hay
         que detectarlo con `latest_image_id` (cambia en cada frame).
    La recuperacion es: close_camera_feed -> esperar 3s -> init_camera_feed.
    """
    fallos = 0
    ultimo_intento = 0.0
    ultimo_id = object()            # centinela: nunca coincide con un id real
    t_cambio = time.time()
    IDLE = 20.0                     # s sin frame nuevo antes de considerarlo caido

    def _recuperar(motivo: str) -> None:
        nonlocal ultimo_intento, ultimo_id, t_cambio
        ahora = time.time()
        # Enfriamiento creciente pero SIN rendirse nunca
        cooldown = 6.0 if fallos < 25 else 30.0
        if ahora - ultimo_intento < cooldown:
            return
        ultimo_intento = ahora
        log.warning("Camara: %s (fallos=%d)", motivo, fallos)
        try:
            robot.camera.close_camera_feed()
        except Exception:
            pass
        time.sleep(3.0)
        try:
            robot.camera.init_camera_feed()
            log.info("Camara: stream reiniciado (close+init)")
        except Exception as e2:
            log.debug("Camara reinit fallo: %s", e2)
        ultimo_id = object()
        t_cambio = time.time()

    while not stop_evt.is_set():
        try:
            img = robot.camera.latest_image
            raw = getattr(img, "raw_image", None)
            try:
                img_id = robot.camera.latest_image_id
            except Exception:
                img_id = None
        except Exception as e:
            fallos += 1
            _recuperar("sin frames: %s" % str(e)[:70])
            time.sleep(0.5)
            continue

        if raw is None:
            fallos += 1
            _recuperar("frame vacio")
            time.sleep(0.4)
            continue

        # ¿es un frame NUEVO? (el id cambia con cada imagen recibida)
        if img_id is not None and img_id == ultimo_id:
            if time.time() - t_cambio >= IDLE:
                fallos += 1
                _recuperar("imagen congelada (mismo id)")
            time.sleep(0.05)
            continue

        # frame nuevo: publicarlo
        ultimo_id = img_id
        t_cambio = time.time()
        if fallos:
            log.info("Camara recuperada tras %d fallos", fallos)
        fallos = 0
        actualizar_frame_camara(raw)
        last["cam_frame"] = time.time()
        if (fly_reflex is not None and fly_reflex.enabled
                and getattr(fly_reflex, "vision", False)):
            try:
                fly_reflex.feed_frame(raw)
            except Exception:
                pass
        time.sleep(0.05)


def _talk_note(last, signal) -> None:
    """Registra una emision propia para no reaccionar a su eco."""
    try:
        import vector_talk as _vt
        last.setdefault("talk_proto", _vt.ChirpProtocol()).note_emitted(signal)
    except Exception:
        pass


def gestionar_talk(talk, fly_reflex, fc, last):
    """Protocolo de chirps estilo Flyctor: oye senales y emite las propias.

    camara -> VL -> cerebro-mosca -> ALTAVOZ
    microfono -> decodificador -> cerebro-mosca -> movimiento
    """
    if talk is None:
        return
    ahora = time.time()

    # ---- 1) OIR: una senal nueva se inyecta como evento sensorial ----
    oidos = int(getattr(talk, "eventos_oidos", 0))
    if oidos != int(last.get("talk_oidos", 0)):
        last["talk_oidos"] = oidos
        oido = talk.ultimo_oido or {}
        sig = oido.get("signal")
        if sig:
            log.info("Talk oido: %s (%s) conf=%s", sig, oido.get("label"),
                     oido.get("confidence"))
            # mapa senal -> (bearing_deg, dist_mm, threat) para la mosca
            mapa_sens = {
                "objeto":    (0.0, 260.0, 0.0),
                "acercate":  (0.0, 190.0, 0.0),
                "bloqueado": (0.0, 90.0, 0.95),
                "ayuda":     (0.0, 140.0, 0.70),
                "ven":       (0.0, 200.0, 0.0),
                "aqui":      (0.0, 240.0, 0.0),
                "te_veo":    (0.0, 260.0, 0.0),
                "para":      (0.0, 120.0, 0.50),
            }
            # Conversacion: acuse/respuesta, ignorando el eco de nuestra propia voz.
            try:
                import vector_talk as _vt
                proto = last.setdefault("talk_proto", _vt.ChirpProtocol())
                resp = proto.on_heard(oido)
                if resp and ahora - float(last.get("talk_reply", 0.0)) >= 1.0:
                    last["talk_reply"] = ahora
                    proto.note_emitted(resp)
                    talk.emitir(resp)
            except Exception as _e_pr:
                log.debug("Talk protocolo: %s", _e_pr)
            if fly_reflex is not None and fly_reflex.online and sig in mapa_sens:
                b, d, t = mapa_sens[sig]
                try:
                    fly_reflex.feed_senses(b, d, threat=t)
                except Exception:
                    pass

    # ---- 2) EMITIR: eventos del cerebro-mosca -> chirps ----
    if fc and ahora - float(last.get("talk_emit", 0.0)) >= 3.0:
        escena = fc.get("scene") or {}
        if escena.get("novel"):
            last["talk_emit"] = ahora
            _talk_note(last, "objeto")
            talk.emitir("objeto")
        elif fc.get("escape") and ahora - float(last.get("talk_escape", 0.0)) >= 8.0:
            last["talk_escape"] = ahora
            last["talk_emit"] = ahora
            _talk_note(last, "bloqueado")
            talk.emitir("bloqueado")


def main() -> None:
    global t_dash, t_disc, t_camara, stop_cam
    m = cargar_mem()
    c = Cerebro()
    c.load(m)
    mapa = Mapa()
    mapa.load(m)

    # Failover robusto: valida claves OpenRouter y excluye las rechazadas
    try:
        _est_or = validar_claves_openrouter()
        log.info(
            "OpenRouter failover -> primaria=%s backup=%s validas=%s",
            _est_or.get("primaria"), _est_or.get("backup"), _est_or.get("validas"),
        )
        m["ia_openrouter"] = _est_or
    except Exception as _e_or:
        log.warning("No se pudieron validar claves OpenRouter: %s", _e_or)

    with _mem_lock:
        m["ia"] = {
            "proveedor": PROVEEDOR,
            "modelo_texto": MODELO_TEXTO,
            "modelo_vision": MODELO_VISION,
        }

    fin = m.get("estado_interno", {}).get("ultima_sesion_fin") or ""
    if fin:
        # ausencia: subir un poco social deficit, NO destrozar
        c.social = clamp(c.social - 6, 15, 100)
        c.curiosidad = clamp(c.curiosidad + 8, 20, 95)
        c.aburrimiento = clamp(c.aburrimiento + 10, 0, 80)
        c._ausencia_larga = True
        c.pensar("He vuelto. Busco una cara conocida.", m)

    # energia minima al arrancar (evita ACT cargar inmediato por memoria mala)
    if c.energia < 35:
        print("AVISO: energia baja en memoria, subo a 55 para empezar bien")
        c.energia = 55.0
        c._mood()

    stat(m, "sesiones")
    rec(m, "inicio", "Inicio Vector Ultra 13.5 " + fase_dia(), 1.5)
    c.dump(m)
    guardar_mem(m)

    caras_ok = set()
    obs: Deque[float] = deque()
    qhabla = queue.Queue()
    qvis = queue.Queue()
    qesc = queue.Queue()
    ia_busy = threading.Event()
    vis_busy = threading.Event()
    escucha = Escucha(qesc)

    t0_touch = None  # type: Optional[float]
    last = {
        "caricia": 0.0, "levantado": 0.0, "sacudida": 0.0, "obstaculo": 0.0,
        "borde": 0.0, "bateria": 0.0, "saludo": 0.0, "guardado": time.time(),
        "vision": 0.0, "micro": 0.0, "decision": 0.0, "expr": 0.0,
        "pens": 0.0, "plan": 0.0, "scan": 0.0, "frase": time.time() + 35,
        "gaze": 0.0, "follow": 0.0, "whim": time.time() + 14,
        "face_hunt": 0.0, "te_vi": 0.0, "patrol": time.time() + 45,
        "checkin": time.time() + 55, "cube_led": 0.0,
        "proactivo": time.time() + 40.0,
    }
    acc_prev = None
    plan_cache = "explorar"
    had_face = False

    # --- Servicios independientes del robot (arranque anticipado) ---
    # Se inician antes de conectar con Vector: el Centro de Mando web y Discord
    # quedan operativos aunque el robot este apagado o fuera de la red.
    if t_dash is None or not t_dash.is_alive():
        try:
            t_dash = iniciar_dashboard(host="0.0.0.0", port=8000)
            log.info("Web Dashboard iniciado en puerto 8000")
        except Exception as _e_dash:
            log.warning("Aviso Web Dashboard: %s", _e_dash)

    if t_disc is None or not t_disc.is_alive():
        try:
            t_disc = iniciar_discord_2(qesc)
            log.info("Discord 2.0 iniciado")
        except Exception as _e_disc:
            log.warning("Aviso Discord 2.0: %s", _e_disc)

    # --- Protocolo de chirps (Flyctor): el receptor no necesita al robot ---
    # Se arranca aqui para que el microfono escuche aunque Vector este offline.
    talk = None
    try:
        import vector_talk_service as _vts
        talk = _vts.iniciar(None)          # robot se enlaza al conectar
        log.info("Talk receptor iniciado (mic=%s)",
                 "si" if talk.activo else (talk._mic_err or "arrancando"))
    except Exception as _e_talk0:
        log.warning("Talk receptor no disponible: %s", _e_talk0)
        talk = None

    # --- Precalentar el indice RAG del Vault en segundo plano ---
    # La primera consulta carga el indice (~30 s); asi la web responde rapido
    # desde el primer momento en lugar de colgarse.
    def _warmup_rag() -> None:
        try:
            from vector_rag import obsidian_rag
            obsidian_rag.buscar("vector", top_k=1)
            log.info("RAG del Vault precalentado")
        except Exception as _e_rag:
            log.debug("RAG warmup: %s", _e_rag)

    try:
        threading.Thread(target=_warmup_rag, daemon=True,
                         name="RagWarmup").start()
    except Exception as _e_ragt:
        log.debug("RAG warmup no lanzado: %s", _e_ragt)

    # Auto-Heal preventivo: resuelve IP dinámica vía mDNS y asegura IP actualizada en config
    try:
        import vector_network_guard
        ip_v = vector_network_guard.resolver_ip_vector()
        vector_network_guard.actualizar_sdk_config(ip_v)
    except Exception as _e_guard:
        log.warning("Aviso guard pre-conexion: %s", _e_guard)

    with anki_vector.Robot(
        enable_face_detection=True,
        estimate_facial_expression=True,
        enable_nav_map_feed=NAV_MAP,
        show_viewer=SHOW_CAMERA,
        show_3d_viewer=SHOW_3D,
        cache_animation_lists=False,
    ) as robot:

        print(
            "Cerebro {}/{} E={:.0f} C={:.0f} A={:.0f} B={:.0f} S={:.0f} M={:.0f}".format(
                c.animo, c.estado, c.energia, c.curiosidad, c.afecto,
                c.aburrimiento, c.social, c.miedo,
            )
        )

        try:
            # limpiar cualquier stream obsoleto antes de abrir el nuestro:
            # si el robot dejo un stream a medias, init_camera_feed no da frames
            try:
                robot.camera.close_camera_feed()
            except Exception:
                pass
            time.sleep(3.0)      # el robot necesita ~3s para soltar el stream
            robot.camera.init_camera_feed()
            print("Camara OK")
        except Exception as e:
            print("Camara:", e)

        def on_face(robot_evt, event_type, event):  # noqa: ANN001, ARG001
            try:
                face = getattr(event, "face", None)
                if face:
                    c._face_id = str(getattr(face, "face_id", ""))
                    c.foco = "cara"
                    c.ev("cara")
                    nom_cara = getattr(face, "name", "").strip()
                    if nom_cara:
                        c._face_name = nom_cara
                    if not on_chg(robot):
                        cabeza(robot, 32.0, lento=True)
            except Exception:
                pass


        def on_wake(robot_evt, event_type, event):  # noqa: ANN001, ARG001
            print("Wake word")
            c.set_estado("escuchando")

        def on_intent(robot_evt, event_type, event):  # noqa: ANN001, ARG001
            o, d = map_intent(event)
            print("Intent:", o)
            escucha.push("intent", o + "|" + d)

        def on_tap(robot_evt, event_type, event):  # noqa: ANN001, ARG001
            print("Cubo TAP")
            escucha.push("cube", "tap")
            stat(m, "taps_cubo")

        try:
            robot.events.subscribe(on_face, Events.robot_observed_face)
        except Exception as e:
            print("ev face:", e)

        if USAR_ESCUCHA_INTENT:
            try:
                robot.events.subscribe(on_wake, Events.wake_word)
            except Exception as e:
                print("wake:", e)
            try:
                robot.events.subscribe(on_intent, Events.user_intent)
                print("UserIntent ON")
            except Exception as e:
                print("intent:", e)
        try:
            robot.events.subscribe(on_tap, Events.object_tapped)
        except Exception:
            pass

        if conn_cubo(robot):
            print("Cubo OK")
            cubo_luces(robot, "pulse")
        else:
            print("Sin cubo (ok)")

        if on_chg(robot):
            off_chg(robot, c)
        else:
            ojos(robot, "feliz")
            gesto(robot, "saludo")

        du = m.get("perfil", {}).get("dueno_preferido") or ""
        if c._ausencia_larga:
            decir(robot, random.choice(FRASES["ausencia_larga"]), c)
            time.sleep(0.1)
            c._ausencia_larga = False
        if du:
            decir(robot, "Hola {}".format(du), c)
            time.sleep(0.1)

        fd = fase_dia()
        if fd in FRASES:
            decir(robot, random.choice(FRASES[fd]), c)
        else:
            decir(robot, random.choice(FRASES["inicio"]), c)

        escucha.start_pc()
        # Dashboard y Discord ya se arrancaron antes de conectar con el robot
        # (asi el Centro de Mando sigue disponible si Vector esta offline).

        # Reflejo biológico opcional (MaleCNS v1.0). Requiere fly_server.py en 4711.
        fly_reflex = None
        try:
            from vector_fly import FlyReflex
            fly_reflex = FlyReflex()
            if fly_reflex.enabled:
                log.info("FlyBrain activado: %s", "sidecar online" if fly_reflex.online else "sidecar OFFLINE")
                print("FlyBrain:", "ONLINE" if fly_reflex.online else "OFFLINE (arranca fly_server.py)")
        except Exception as _e_fly:
            log.warning("FlyBrain no disponible: %s", _e_fly)
            fly_reflex = None

        # --- Protocolo de chirps: enlazar Vector al emisor ya arrancado ---
        # (el receptor del microfono se inicio antes de conectar)
        try:
            import vector_talk_service as _vts
            talk = _vts.iniciar(robot)
            if talk is not None:
                log.info("Talk emisor enlazado a Vector (mic=%s, emitidos=%d)",
                         "si" if talk.activo else (talk._mic_err or "no"),
                         talk.eventos_emitidos)
                print("Talk (chirps):", "ESCUCHANDO" if talk.activo else
                      "receptor degradado (%s)" % (talk._mic_err or "sin mic"))
        except Exception as _e_talk:
            log.warning("Talk no disponible: %s", _e_talk)

        # --- Camara en hilo propio (SINGLETON): siempre operativa para la web ---
        # Si ya habia un hilo de camara (reconexion), se detiene antes de crear
        # otro; si no, se acumularian hilos usando Robots ya muertos.
        try:
            if t_camara is not None and t_camara.is_alive():
                stop_cam.set()
                t_camara.join(timeout=2.5)
                log.info("Hilo de camara anterior detenido")
            stop_cam = threading.Event()
            t_camara = threading.Thread(
                target=hilo_camara, args=(robot, fly_reflex, last, stop_cam),
                daemon=True, name="Camara")
            t_camara.start()
            log.info("Hilo de camara iniciado (auto-recuperacion activa)")
        except Exception as _e_cam:
            log.warning("Hilo de camara no disponible: %s", _e_cam)

        proximo = time.time() + 2.5
        last["thread_watchdog"] = time.time()
        log.info("Vector Ultra 13.5 activo y conectado. Ctrl+C detiene.")
        print("Vector Ultra 13.5 activo y conectado. Ctrl+C detiene.")

        try:
            while True:
                now = time.time()
                c.decay()
                mapa.decay(0.995)

                # 0.5 Reflejo biológico FlyBrain (opt-in): looming -> escape
                if fly_reflex is not None and fly_reflex.enabled and fly_reflex.online:
                    if now - float(last.get("flybrain", 0.0)) >= 1.2:
                        last["flybrain"] = now
                        try:
                            fc = fly_reflex.tick(mapa=mapa)
                            if fc and isinstance(fc.get("scene"), dict):
                                _ESCENA_VIDA.clear(); _ESCENA_VIDA.update(fc["scene"])
                            # Protocolo de chirps: oir senales y emitir las propias
                            try:
                                gestionar_talk(talk, fly_reflex, fc, last)
                            except Exception:
                                pass
                            if fc and fc.get("escape"):
                                esc_l = max(-1.0, min(1.0, float(fc.get("left", 0.0))))
                                esc_r = max(-1.0, min(1.0, float(fc.get("right", 0.0))))
                                if (abs(esc_l) > 0.05 or abs(esc_r) > 0.05) and not es_borde(robot) and not es_up(robot):
                                    robot.motors.set_wheel_motors(esc_l * 110.0, esc_r * 110.0)
                                    time.sleep(0.35)
                                    robot.motors.set_wheel_motors(0.0, 0.0)
                                    c.miedo = clamp(c.miedo + 6.0, 0, 60)
                                    stat(m, "escape_reflejo")
                                    log.info("FlyBrain escape L=%.2f R=%.2f (%s)", esc_l, esc_r, fc.get("mode"))
                                    # --- Reaccion expresiva + memoria (solo en el flanco) ---
                                    if not last.get("fly_escape", False):
                                        last["fly_escape"] = True
                                        frase = random.choice([
                                            "Algo se acerca... me aparto!",
                                            "Peligro, huyo!",
                                            "Eso viene directo hacia mi!",
                                            "Uy, uy, uy... esquivo!",
                                        ])
                                        try:
                                            hilo(lambda: decir(robot, frase, c))
                                        except Exception:
                                            pass
                                        try:
                                            ojos(robot, "asustado")
                                        except Exception:
                                            pass
                                        try:
                                            rec(m, "susto",
                                                "Escape reflejo mosca L=%.2f R=%.2f" % (esc_l, esc_r),
                                                0.8)
                                            if random.random() < 0.34:
                                                guardar_mem(m)
                                        except Exception:
                                            pass
                                else:
                                    last["fly_escape"] = False
                            elif fc and fc.get("mode") in ("wander", "chase"):
                                # P3: exploracion suave guiada por la decision de la mosca
                                if (now - float(last.get("fly_wander", 0.0)) >= 5.0
                                        and not es_borde(robot) and not es_up(robot)):
                                    last["fly_wander"] = now
                                    lin = float(fc.get("linear", 0.0))
                                    trn = float(fc.get("turn", 0.0))
                                    wl, wr = rodar_suave(robot, lin * 0.75, trn, 0.9)
                                    log.info("FlyBrain explorar L=%.0f R=%.0f (%s)", wl, wr, fc.get("mode"))
                                    if random.random() < 0.15:
                                        try:
                                            hilo(lambda: decir(robot, random.choice([
                                                "Voy a explorar por aqui.",
                                                "Me muevo un poco.",
                                                "Que hay por alli?",
                                                "Camino curioseando.",
                                            ]), c))
                                        except Exception:
                                            pass
                                    last["fly_escape"] = False
                            else:
                                last["fly_escape"] = False
                        except Exception as _e_fr:
                            log.debug("FlyBrain tick: %s", _e_fr)

                # 0.6 Gestion de bateria: cargar si baja, salir de la base al 100%
                try:
                    if now - float(last.get("bat_check", 0.0)) >= 20.0:
                        last["bat_check"] = now
                        bs = robot.get_battery_state()
                        if bs is not None:
                            volts = float(getattr(bs, "battery_volts", 0.0))
                            lvl = str(getattr(bs, "battery_level", ""))
                            on_ch = bool(getattr(bs, "is_on_charger_platform", False)) or bool(robot.status.is_on_charger)
                            if volts < _BAT_VALIDA_V:
                                volts = bat_volts(robot)
                            if volts < _BAT_VALIDA_V:
                                pass  # lectura fallida: no tomar decisiones con 0 V
                            elif not on_ch and (volts < 3.55 or "LOW" in lvl):
                                log.info("Bateria baja (%.2fV); voy a la base a cargar", volts)
                                hilo(lambda: robot.behavior.drive_on_charger())
                                try:
                                    hilo(lambda: decir(robot, "Me queda poca bateria. Voy a cargar.", c))
                                    ojos(robot, "cansado")
                                except Exception:
                                    pass
                                c.miedo = clamp(c.miedo - 4.0, 0, 60)
                            elif on_ch and (volts >= 4.05 or "FULL" in lvl):
                                log.info("Bateria llena (%.2fV); salgo de la base a explorar", volts)
                                hilo(lambda: robot.behavior.drive_off_charger())
                                try:
                                    hilo(lambda: decir(robot, "Ya estoy cargado. Me voy a explorar.", c))
                                    ojos(robot, "feliz")
                                except Exception:
                                    pass
                except Exception as _e_bat:
                    log.debug("Bateria: %s", _e_bat)

                # 1. Alimentar vídeo de cámara al Web Dashboard
                try:
                    # La camara la alimenta hilo_camara() en su propio hilo:
                    # aqui NO se toca latest_image (bloquea y atasca el bucle).
                    img_live = None
                    if img_live and getattr(img_live, "raw_image", None):
                        actualizar_frame_camara(img_live.raw_image)
                        # 1b. Ojo de la mosca: cámara de Vector -> fotorreceptores
                        if (fly_reflex is not None and fly_reflex.enabled
                                and getattr(fly_reflex, "vision", False)):
                            fly_reflex.feed_frame(img_live.raw_image)
                except Exception:
                    pass

                # 2. Actualizar telemetría hacia el Dashboard
                try:
                    bv = bat_volts(robot)
                    actualizar_telemetria({
                        "online": True,
                        "bateria_v": bv,
                        "bateria_pct": max(0, min(100, int((bv - 3.6) / 0.55 * 100))),
                        "en_cargador": on_chg(robot),
                        "animo": c.animo,
                        "estado": c.estado,
                        "tof_mm": prox_ok(robot) or 0,
                        "ultima_frase": m.get("ultima_frase", "Explorando..."),
                        "ultimo_pensamiento": m.get("ultimo_pensamiento", "Observando..."),
                    })
                except Exception:
                    pass

                # 3. Procesar comandos manuales desde el Web Dashboard
                try:
                    while True:
                        tipo_w, cmd_w = web_command_queue.get_nowait()
                        log.debug("WEB_CMD: %r = %r", tipo_w, cmd_w)
                        if tipo_w == "web_control":
                            last["manual"] = time.time()
                            try:
                                if cmd_w == "adelante":
                                    if on_chg(robot):
                                        off_chg(robot, c)
                                    else:
                                        robot.motors.set_wheel_motors(95.0, 95.0)
                                        time.sleep(0.40)
                                        robot.motors.set_wheel_motors(0.0, 0.0)
                                elif cmd_w == "atras":
                                    robot.motors.set_wheel_motors(-90.0, -90.0)
                                    time.sleep(0.40)
                                    robot.motors.set_wheel_motors(0.0, 0.0)
                                elif cmd_w == "izquierda":
                                    robot.motors.set_wheel_motors(-80.0, 80.0)
                                    time.sleep(0.30)
                                    robot.motors.set_wheel_motors(0.0, 0.0)
                                elif cmd_w == "derecha":
                                    robot.motors.set_wheel_motors(80.0, -80.0)
                                    time.sleep(0.30)
                                    robot.motors.set_wheel_motors(0.0, 0.0)
                                elif cmd_w == "parar":
                                    try:
                                        robot.motors.set_wheel_motors(0.0, 0.0)
                                        robot.motors.set_head_motor(0.0)
                                        robot.motors.set_lift_motor(0.0)
                                    except Exception:
                                        pass
                                elif cmd_w == "salir_cargador":
                                    off_chg(robot, c)
                                elif cmd_w == "cabeza_arriba":
                                    cabeza(robot, 40)
                                elif cmd_w == "cabeza_abajo":
                                    cabeza(robot, 0)
                                elif cmd_w == "pala_arriba":
                                    brazo(robot, 1.0)
                                elif cmd_w == "pala_abajo":
                                    brazo(robot, 0.0)
                            except Exception as _err_ctrl:
                                print("Aviso control web:", _err_ctrl)
                        elif tipo_w == "web_color":
                            try:
                                ojos(robot, str(cmd_w))
                                animar(robot, "feliz")
                            except Exception as _err_col:
                                print("Aviso color web:", _err_col)
                        elif tipo_w == "web_accion":
                            try:
                                if cmd_w == "foto":
                                    tomar_foto_hd_y_guardar(robot, m, c)
                                elif cmd_w == "patrulla":
                                    c.set_estado("patrullando")
                                    decir(robot, "Iniciando patrulla", c)
                                elif cmd_w == "inspeccionar":
                                    cabeza(robot, 25)
                                    decir(robot, "Examinando lo que tengo delante", c)
                                    b_img = cap_b64(robot)
                                    if b_img:
                                        res_vis = ver_ia(b_img, m)
                                        if res_vis:
                                            decir(robot, res_vis[0], c)
                                elif cmd_w == "dado":
                                    decir(robot, tirar_dado_virtual(6, robot), c)
                                elif cmd_w == "moneda":
                                    decir(robot, lanzar_moneda_virtual(robot), c)
                                elif cmd_w == "matrix":
                                    animacion_matrix_oled(robot)
                                elif cmd_w == "cargador":
                                    on_charger(robot, c)
                            except Exception as _err_acc:
                                print("Aviso accion web:", _err_acc)
                        elif tipo_w == "web_decir":
                            decir(robot, str(cmd_w), c)
                        elif tipo_w == "web_intercom":
                            txt_in = str(cmd_w).strip()
                            cabeza(robot, 32)
                            ojos(robot, "atento")
                            animar(robot, "curioso")
                            resp = ia_conversar_intercom(txt_in, m, c)
                            decir(robot, resp, c)
                        elif tipo_w == "web_talk":
                            # emitir un chirp del vocabulario (no bloquea el bucle)
                            _sig = str(cmd_w)
                            log.info("Chirp emitido por peticion web: %s", _sig)
                            if talk is not None:
                                hilo(lambda s=_sig: talk.emitir(s))
                except queue.Empty:
                    pass

                # 4. Procesar comandos desde Discord 2.0
                try:
                    while True:
                        tipo_d, cmd_d = discord_command_queue.get_nowait()
                        if cmd_d == "foto":
                            f_path_res = tomar_foto_hd_y_guardar(robot, m, c)
                            registrar_foto_tomada(f_path_res)
                        elif cmd_d == "patrulla":
                            c.set_estado("patrullando")
                            decir(robot, "Iniciando ronda de vigilancia", c)
                        elif str(cmd_d).startswith("decir:"):
                            decir(robot, str(cmd_d)[6:], c)
                        elif str(cmd_d).startswith("dado:"):
                            try:
                                n_caras = int(str(cmd_d).split(":")[1])
                            except Exception:
                                n_caras = 6
                            decir(robot, tirar_dado_virtual(n_caras, robot), c)
                        elif cmd_d == "moneda":
                            decir(robot, lanzar_moneda_virtual(robot), c)
                        elif cmd_d == "matrix":
                            animacion_matrix_oled(robot)
                except queue.Empty:
                    pass

                if not ctrl(robot):
                    print("Control perdido...")
                    if not wait_ctrl(robot):
                        time.sleep(1.0)
                        continue
                    print("Control OK")
                    proximo = time.time() + 3
                    continue

                up = es_up(robot)

                # colas — decir() ya bloquea basura
                try:
                    while True:
                        fr = qhabla.get_nowait()
                        if not up and not es_borde(robot):
                            if not es_basura_ia(fr):
                                if random.random() < 0.4:
                                    gesto(robot, "pensar")
                                decir(robot, fr, c)
                                proximo = time.time() + 2.2
                except queue.Empty:
                    pass

                try:
                    while True:
                        item = qvis.get_nowait()
                        if isinstance(item, tuple) and len(item) == 2:
                            ds, tp = item
                        else:
                            ds, tp = str(item), cls_vis(str(item))
                        if not up and not es_borde(robot):
                            reaccionar_vision(robot, c, m, ds, tp, qhabla)
                            if tp == "vacio":
                                # no repetir "nada claro" siempre
                                if random.random() < 0.35:
                                    decir(
                                        robot,
                                        random.choice(FRASES["nada_vision"]),
                                        c,
                                    )
                            else:
                                decir(robot, ds, c)
                            proximo = time.time() + 2.8
                except queue.Empty:
                    pass

                try:
                    while True:
                        origen, payload = qesc.get_nowait()
                        # Limpiar frases genericas acumuladas para atender la voz de inmediato
                        while not qhabla.empty():
                            try:
                                qhabla.get_nowait()
                            except queue.Empty:
                                break

                        if origen == "intent":
                            o, _, d = payload.partition("|")
                            orden_voz(
                                o, robot, m, c, mapa, obs, qhabla,
                                bruto=o, data=d,
                            )
                        elif origen == "cube":
                            if payload == "tap":
                                c.ev("juego_cubo")
                                gesto(robot, "cazador")
                                cubo_luces_animo(robot, c)
                                decir(
                                    robot,
                                    random.choice(FRASES["cubo_tap"]),
                                    c,
                                )
                                ejecutar(
                                    "jugar", robot, m, c, mapa, obs, qhabla,
                                )
                        elif origen == "discord":
                            orden_voz(
                                "discord_remoto", robot, m, c, mapa, obs, qhabla,
                                bruto=payload, data=payload,
                            )
                        else:
                            o = parse_pc(payload)

                            # Postura atenta y receptiva
                            ojos(robot, "atento")
                            cabeza(robot, 28)
                            if o == "charla":
                                orden_voz(
                                    "charla", robot, m, c, mapa, obs,
                                    qhabla, bruto=payload,
                                )
                            else:
                                orden_voz(
                                    o, robot, m, c, mapa, obs, qhabla,
                                    bruto=payload,
                                )
                        proximo = time.time() + 4.5
                except queue.Empty:
                    pass


                # reflejos
                if es_caida(robot):
                    rec_save(m, "caida", "Caida", peso=2.0)
                    c.ev("borde")
                    proximo = time.time() + 12
                    time.sleep(0.25)
                    continue

                if es_borde(robot):
                    proximo = time.time() + 8
                    if now - last["borde"] > 20:
                        last["borde"] = now
                        rec_save(m, "borde", "Borde", "bordes", 1.5)
                        c.ev("borde")
                        gesto(robot, "asustado")
                        atras(robot, 26)
                        girar(robot, random.choice([-100, 100, -130, 130]))
                        decir(robot, random.choice(FRASES["borde"]), c)
                    time.sleep(0.2)
                    continue

                if up:
                    if now - last["levantado"] > 12:
                        last["levantado"] = now
                        rec_save(m, "levantado", "Levantado", "levantado")
                        c.ev("levantado")
                        gesto(robot, "sorpresa")
                        decir(robot, random.choice(FRASES["levantado"]), c)
                    proximo = time.time() + 4
                    time.sleep(0.18)
                    continue

                if peligro(robot):
                    stat(m, "choques_evitados")
                    c.ev("choque_evitado")
                    atras(robot, 40)
                    liberar(robot, mapa, m)
                    if now - last["obstaculo"] > 9:
                        last["obstaculo"] = now
                        decir(robot, random.choice(FRASES["obstaculo"]), c)
                    proximo = time.time() + 2
                    continue

                # mirada
                if (
                    not up and not on_chg(robot)
                    and now - last["gaze"] >= T_GAZE and not c.modo_quedo
                ):
                    last["gaze"] = now
                    seeing = gaze_tick(robot, c, m)
                    if seeing and not had_face and now - last["te_vi"] > 14:
                        last["te_vi"] = now
                        if random.random() < 0.5:
                            qhabla.put(random.choice(FRASES["te_vi"]))
                    had_face = seeing

                if (
                    not up and not on_chg(robot) and not c.modo_quedo
                    and now - last["follow"] >= T_FOLLOW and now >= proximo
                ):
                    last["follow"] = now
                    if follow_body_tick(robot, c, mapa, m):
                        proximo = time.time() + 1.2

                # caras
                try:
                    fs = list(robot.world.visible_faces)
                    if fs:
                        cara = fs[0]
                        c.social = clamp(c.social + 0.5)
                        c.aburrimiento = clamp(c.aburrimiento - 0.4)
                        if now - last["expr"] > 14:
                            ex = expr(cara)
                            if ex in MAPA_EXPR:
                                ao, da, ds = MAPA_EXPR[ex]
                                ojos(robot, ao)
                                c.afecto = clamp(c.afecto + da * 0.35)
                                c.social = clamp(c.social + ds * 0.35)
                                if ex == "happiness" and random.random() < 0.45:
                                    gesto(robot, "feliz")
                                elif ex == "sadness":
                                    gesto(robot, "triste")
                                last["expr"] = now

                        if (
                            cara.face_id not in caras_ok
                            and now - last["saludo"] > 38
                        ):
                            caras_ok.add(cara.face_id)
                            last["saludo"] = now
                            p = persona(m, cara)
                            rec_save(
                                m, "cara",
                                "Vio a {}".format(p["nombre"]),
                                "caras_saludadas", 1.4,
                            )
                            c.ev("cara")
                            gesto(robot, "saludo")
                            head_track_face(robot, cara)
                            if getattr(cara, "name", None) and float(
                                p.get("vinculo", 0)
                            ) > 28:
                                decir(robot, "Hola {}".format(cara.name), c)
                            elif not ia_busy.is_set():
                                ia_busy.set()

                                def _sg(pp=p):
                                    try:
                                        evn = (
                                            "persona"
                                            if pp.get("nombre") != "Desconocido"
                                            else "persona_nueva"
                                        )
                                        qhabla.put(ia_frase(m, evn, c))
                                    finally:
                                        ia_busy.clear()

                                hilo(_sg)
                            proximo = time.time() + 3.0
                except Exception:
                    pass

                # tacto
                toc = touch(robot)
                if toc and t0_touch is None:
                    t0_touch = now
                    ojos(robot, "amoroso")
                if not toc and t0_touch is not None:
                    dur = now - t0_touch
                    t0_touch = None
                    if now - last["caricia"] > 5:
                        last["caricia"] = now
                        rec_save(
                            m, "caricia",
                            "Caricia {:.1f}s".format(dur),
                            "caricias", 1.6,
                        )
                        with _mem_lock:
                            ps = list(m.get("personas", {}).values())
                            if ps:
                                ps.sort(
                                    key=lambda x: x.get("ultima_vez", ""),
                                    reverse=True,
                                )
                                ps[0]["caricias_recibidas"] = int(
                                    ps[0].get("caricias_recibidas", 0)
                                ) + 1
                                ps[0]["vinculo"] = clamp(
                                    float(ps[0].get("vinculo", 0)) + 6
                                )
                        if dur >= 2.0:
                            rec_save(
                                m, "caricia_larga", "Larga",
                                "caricias_largas", 2.0,
                            )
                            c.ev("caricia_larga")
                            gesto(robot, "carino")
                            pantalla_emocion(robot, "carinoso", 2.2)
                            cubo_luces_animo(robot, c)
                            decir(
                                robot,
                                random.choice(FRASES["caricia_larga"]),
                                c,
                            )
                        elif dur >= 0.85:
                            c.ev("caricia")
                            gesto(robot, "feliz")
                            pantalla_emocion(robot, "feliz", 1.8)
                            cubo_luces_animo(robot, c)
                            decir(
                                robot,
                                random.choice(FRASES["caricia"]),
                                c,
                            )
                        else:
                            c.ev("caricia")
                            gesto(robot, "saludo")
                            if not ia_busy.is_set():
                                ia_busy.set()

                                def _ca():
                                    try:
                                        qhabla.put(ia_frase(m, "caricia", c))
                                    finally:
                                        ia_busy.clear()

                                hilo(_ca)
                        proximo = time.time() + 2.6

                ac = accel(robot)
                ch = dacc(acc_prev, ac)
                acc_prev = ac
                if ch > 7000 and now - last["sacudida"] > 26:
                    last["sacudida"] = now
                    rec_save(m, "sacudida", "Brusco", "sacudidas")
                    c.ev("sacudida")
                    gesto(robot, "protesta")
                    decir(robot, random.choice(FRASES["movido"]), c)
                    proximo = time.time() + 3.0

                if hay_obs(robot) and now - last["obstaculo"] > 7:
                    last["obstaculo"] = now
                    obs.append(now)
                    while obs and now - obs[0] > VENTANA_OBS:
                        obs.popleft()
                    rec_save(m, "obstaculo", "Frontal", "obstaculos")
                    c.ev("obstaculo")
                    gesto(robot, "sorpresa")
                    liberar(robot, mapa, m)
                    proximo = time.time() + 2.2

                # bateria REAL (no el animo cansado)
                if bat_low(robot) and now - last["bateria"] > 280:
                    last["bateria"] = now
                    c.energia = 20
                    rec_save(m, "bateria_baja", "Bateria baja real", peso=2.0)
                    if CARGA_AUTO and not on_chg(robot):
                        on_charger(robot, c)
                        proximo = time.time() + 50

                if on_chg(robot):
                    c.set_estado("descansando")
                    c.energia = clamp(c.energia + 0.15)  # recupera en cargador
                    if now - last.get("bat_hud", 0.0) > 18.0:
                        last["bat_hud"] = now
                        bv = bat_volts(robot)
                        if bv > 0.5:
                            pantalla_bateria_hud(robot, bv, 2.5)
                        else:
                            pantalla_emocion(robot, "dormido", 2.0)
                    elif random.random() < 0.02 and not ia_busy.is_set():
                        ojos(robot, "sueno")
                        ia_busy.set()

                        def _dr():
                            try:
                                t = ia_pensar(m, c)
                                c.pensar(t, m)  # filtrado dentro
                            finally:
                                ia_busy.clear()

                        hilo(_dr)
                    time.sleep(0.35)
                    continue

                if (
                    now - last["whim"] >= T_WHIM and not up and not c.modo_quedo
                    and now >= proximo and c.miedo < 40 and random.random() < 0.35
                ):
                    last["whim"] = now
                    ejecutar("capricho", robot, m, c, mapa, obs, qhabla)
                    proximo = time.time() + 1.8

                if (
                    not had_face and not up and c.social < 45
                    and now - last["face_hunt"] >= T_FACE_HUNT
                    and now >= proximo and random.random() < 0.5
                ):
                    last["face_hunt"] = now
                    ejecutar("buscar_caras", robot, m, c, mapa, obs, qhabla)
                    proximo = time.time() + 2.2

                if (
                    MODO_PATRULLA and not up and not c.modo_quedo and not had_face
                    and c.energia > 45 and now - last["patrol"] >= T_PATROL
                    and now >= proximo and random.random() < 0.45
                ):
                    last["patrol"] = now
                    ejecutar("patrulla", robot, m, c, mapa, obs, qhabla)
                    proximo = time.time() + 2.8

                if (
                    not up and c.social < 55
                    and now - last["checkin"] >= T_CHECKIN
                    and now >= proximo and random.random() < 0.35
                ):
                    last["checkin"] = now
                    ejecutar("checkin", robot, m, c, mapa, obs, qhabla)
                    proximo = time.time() + 2.4

                # PROACTIVIDAD AUTONOMA EN DISCORD (Fotos, estado y curiosidades periodicas)
                if (
                    now - last.get("discord_feed", 0.0) >= random.uniform(300, 600)
                    and not up and not ia_busy.is_set()
                ):
                    last["discord_feed"] = now

                    def _disc_proactivo():
                        try:
                            bv = bat_volts(robot)
                            rnd = random.random()
                            if rnd < 0.35:
                                msg = f"📊 **[Reporte Autónomo de Vector]**\n• **Batería**: {bv:.2f}V | **Ánimo**: {c.animo} | **Energía**: {c.energia:.0f}%\n• **Vínculo**: {c.social:.0f}% | **Obsidian**: 466 notas indexadas"
                                enviar_discord(msg)
                            elif rnd < 0.70:
                                curio = buscar_curiosidad_internet()
                                msg = f"💡 **[Curiosidad Autónoma de Vector]**:\n{curio}"
                                enviar_discord(msg)
                            else:
                                f_res = tomar_foto_hd_y_guardar(robot, m, c)
                                dir_fotos = VAULT_OBSIDIAN_PATH / "02 - Inbox" / "Fotos_Vector"
                                fotos = sorted(dir_fotos.glob("*.jpg"), key=os.path.getmtime, reverse=True) if dir_fotos.exists() else []
                                if fotos:
                                    enviar_discord("📸 **[Vigilancia de Mesa]**: Foto tomada durante mi patrulla autónoma:", ruta_foto=str(fotos[0]))
                        except Exception:
                            pass

                    hilo(_disc_proactivo)

                # Dialogo proactivo y espontaneo
                if (
                    not up and not on_chg(robot) and not es_borde(robot)
                    and now - last.get("proactivo", 0.0) >= random.uniform(30, 55)
                    and now >= proximo and qhabla.empty() and not ia_busy.is_set()
                ):
                    last["proactivo"] = now
                    ia_busy.set()

                    def _pr():
                        try:
                            if random.random() < 0.60:
                                frase_p = ia_pregunta_proactiva(m, c)
                            else:
                                curio = buscar_curiosidad_internet()
                                frase_p = ia_resumen_web(m, c, "cuenta una curiosidad", curio)
                            if frase_p and not es_basura_ia(frase_p):
                                qhabla.put(frase_p)
                        finally:
                            ia_busy.clear()

                    hilo(_pr)
                    proximo = time.time() + 2.5


                if (
                    now - last["pens"] >= T_PENSAR
                    and not up and not ia_busy.is_set()
                ):
                    last["pens"] = now
                    ia_busy.set()

                    def _pe():
                        try:
                            t = ia_pensar(m, c)
                            c.pensar(t, m)
                            # hablar pensamientos SOLO si limpios y flag
                            if (
                                HABLAR_PENSAMIENTOS
                                and not es_basura_ia(t)
                                and random.random() < 0.65
                                and not es_up(robot)
                            ):
                                qhabla.put(t[:100])
                        finally:
                            ia_busy.clear()

                    hilo(_pe)


                if (
                    now - last["plan"] >= T_PLAN
                    and not up and not ia_busy.is_set()
                ):
                    last["plan"] = now
                    ia_busy.set()

                    def _pl():
                        nonlocal plan_cache
                        try:
                            p = ia_plan(m, c)
                            if p:
                                plan_cache = p
                                print("Plan IA ->", p)
                        finally:
                            ia_busy.clear()

                    hilo(_pl)

                if (
                    now - last["vision"] >= T_VISION
                    and not up and not on_chg(robot)
                    and not es_borde(robot) and not vis_busy.is_set()
                ):
                    last["vision"] = now
                    if not (c.modo_seguir and had_face):
                        observar(robot)
                    b = cap_b64(robot)
                    if b:
                        vis_busy.set()

                        def _v(img=b):
                            try:
                                out = ver_ia(img, m)
                                if out:
                                    qvis.put(out)
                            finally:
                                vis_busy.clear()

                        hilo(_v)

                # Micro-Vida Organica en Reposo (respiracion sutil de cabeza y parpadeo)
                if now - last.get("micro_vida", 0.0) >= 7.5 and not up:
                    last["micro_vida"] = now
                    try:
                        if on_chg(robot) or c.estado in ("reposo", "tranquilo", "aburrido"):
                            angulo_resp = random.choice([16.0, 20.0, 24.0])
                            cabeza(robot, angulo_resp, lento=True)
                            ojos(robot, c.animo)
                            if random.random() < 0.18:
                                animar(robot, "curioso")
                    except Exception:
                        pass

                if (
                    now - last["scan"] >= T_SCAN
                    and not up and not on_chg(robot)
                    and now >= proximo and not had_face
                    and (c.miedo > 22 or random.random() < 0.35)
                ):
                    last["scan"] = now
                    escanear(robot, mapa)
                    mapa.save(m)
                    proximo = time.time() + 1.6

                if now - last["cube_led"] >= T_CUBE_LIGHT and cubo(robot):
                    last["cube_led"] = now
                    cubo_luces(robot, "pulse")

                # decision — NUNCA "cargar" por animo solo
                if (
                    now - last["decision"] >= 5.0
                    and now >= proximo
                    and not on_chg(robot)
                    and not up
                    and not c.modo_quedo
                    and (now - last.get("manual", 0.0) >= 6.0)
                ):
                    if c.modo_seguir and had_face and random.random() < 0.55:
                        last["decision"] = now
                        follow_body_tick(robot, c, mapa, m)
                        proximo = time.time() + 1.3
                    else:
                        last["decision"] = now
                        # bateria real manda sobre plan
                        if bat_low(robot):
                            intent = "cargar"
                        else:
                            local = c.decidir_local()
                            if local == "cargar" and not bat_low(robot):
                                local = "descansar"
                            if c.miedo >= U_MIEDO:
                                intent = "calmarse"
                            elif (
                                random.random() < 0.55
                                and plan_cache in ACCIONES_PLAN
                                and plan_cache != "cargar"
                            ):
                                intent = plan_cache
                            else:
                                intent = local
                        print(
                            "ACT {} | {}/{} E{:.0f} C{:.0f} A{:.0f} "
                            "B{:.0f} S{:.0f} M{:.0f} face={}".format(
                                intent, c.animo, c.estado, c.energia,
                                c.curiosidad, c.afecto, c.aburrimiento,
                                c.social, c.miedo, bool(had_face),
                            )
                        )
                        res = ejecutar(
                            intent, robot, m, c, mapa, obs, qhabla,
                        )
                        rec(m, "intencion", intent + "->" + res, 0.6)
                        proximo = time.time() + random.uniform(2.5, 4.5)

                if now >= last["frase"] and not up and not ia_busy.is_set():
                    delay = random.randint(85, 150)
                    if had_face:
                        delay += 25
                    last["frase"] = now + delay
                    ia_busy.set()

                    def _fr():
                        try:
                            qhabla.put(ia_frase(m, c.animo, c))
                        finally:
                            ia_busy.clear()

                    if c.social < 50 or random.random() < 0.5:
                        hilo(_fr)
                    proximo = time.time() + 2.0

                if (
                    now - last["micro"] >= T_MICRO
                    and now < proximo
                    and not up and not on_chg(robot)
                ):
                    last["micro"] = now
                    micro_vida(robot, c)

                if now - last["guardado"] >= T_SAVE:
                    c.dump(m)
                    mapa.save(m)
                    # re-sanear por si algo se fue al extremo
                    sanear_estado_interno(m)
                    guardar_mem(m)
                    last["guardado"] = now

                # Watchdog de hilos: reiniciar dashboard/discord si mueren
                if now - last.get("thread_watchdog", 0) >= 30.0:
                    last["thread_watchdog"] = now
                    if t_dash is not None and not t_dash.is_alive():
                        log.warning("Hilo Dashboard muerto; reiniciando...")
                        try:
                            t_dash = iniciar_dashboard(host="0.0.0.0", port=8000)
                            log.info("Web Dashboard reiniciado")
                        except Exception as e:
                            log.error("Fallo reiniciando Dashboard: %s", e)
                    if t_disc is not None and not t_disc.is_alive():
                        log.warning("Hilo Discord 2.0 muerto; reiniciando...")
                        try:
                            t_disc = iniciar_discord_2(qesc)
                            log.info("Discord 2.0 reiniciado")
                        except Exception as e:
                            log.error("Fallo reiniciando Discord: %s", e)
                    if t_camara is not None and not t_camara.is_alive():
                        log.warning("Hilo Camara muerto; reiniciando...")
                        try:
                            stop_cam = threading.Event()
                            t_camara = threading.Thread(
                                target=hilo_camara,
                                args=(robot, fly_reflex, last, stop_cam),
                                daemon=True, name="Camara")
                            t_camara.start()
                            log.info("Hilo de camara reiniciado")
                        except Exception as e:
                            log.error("Fallo reiniciando Camara: %s", e)

                time.sleep(0.12)

        finally:
            log.info("Apagando Vector Ultra 13.5...")
            escucha.stop.set()
            with _mem_lock:
                m.setdefault("estado_interno", {})[
                    "ultima_sesion_fin"
                ] = iso()
            c.dump(m)
            mapa.save(m)
            sanear_estado_interno(m)
            rec(m, "fin", "Fin Vector Ultra 13.5", 1.0)
            guardar_mem(m)
            diario_write(m, c)
            try:
                cubo_luces(robot, "off")
            except Exception:
                pass
            try:
                robot.events.unsubscribe(
                    on_face, Events.robot_observed_face,
                )
            except Exception:
                pass


def renovar_token_wirepod(robot_ip: Optional[str] = None) -> bool:
    """Invoca el guardián de red para obtener un nuevo token gRPC y sincronizar con Wire-Pod."""
    try:
        import vector_network_guard
        # HEALER serializa las renovaciones y aplica enfriamiento/backoff.
        return vector_network_guard.HEALER.heal(robot_ip, motivo="watchdog")
    except Exception as err:
        log.warning("Watchdog Auto-Heal: error al invocar vector_network_guard: %s", err)
        return False


if __name__ == "__main__":
    reintentos = 0
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            log.info("Interrupción por teclado; deteniendo Vector Ultra 13.5")
            print("\nDeteniendo Vector Ultra 13.5 de forma limpia...")
            break
        except Exception as e:
            reintentos += 1
            err_str = str(e)
            try:
                from vector_network_guard import es_error_401
                _es401 = es_error_401(e)
            except Exception:
                _es401 = "401" in err_str or "UNAUTHENTICATED" in err_str
            if _es401:
                log.warning("Detectado error 401 gRPC / UNAUTHENTICATED. Renovando token automáticamente con Wire-Pod...")
                print("\n[Watchdog Auto-Heal] 401 Unauthorized detectado. Renovando token automáticamente con Wire-Pod...")
                renovar_token_wirepod()
            elif "unable to establish a connection" in err_str.lower() or "unavailable" in err_str.lower():
                try:
                    import vector_network_guard
                    ip_fresca = vector_network_guard.resolver_ip_vector()
                    vector_network_guard.actualizar_sdk_config(ip_fresca)
                    log.info("Watchdog Auto-Heal: IP de Vector re-verificada via mDNS: %s", ip_fresca)
                except Exception:
                    pass

            espera = min(25, 2 * min(reintentos, 6))
            log.error("Watchdog Auto-Heal: %s. Reanudando en %ss (intento %d)", e, espera, reintentos)
            print(f"\n[Watchdog Auto-Heal] Desconexión o error gRPC ({e}). Reanudando en {espera}s... (Intento {reintentos})")
            time.sleep(espera)
