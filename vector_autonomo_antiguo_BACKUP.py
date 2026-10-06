#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR 10.1 — CULMEN ESTABLE (parche del 10.0)
# ============================================================
# Corrige lo que viste en el CMD:
# 1) Estado emocional corrupto (E=0 A=0 B=94) -> se SANA al arrancar
# 2) Nemotron soltaba meta en ingles ("We need to produce...") -> FILTRO
# 3) Esos textos ya NO se hablan ni se guardan como pensamientos
# 4) Prompts mas duros: SOLO frase final en espanol
# 5) Vision "nada claro" no se celebra como novedad
#
# .env (NVIDIA preferido):
#   AI_PROVIDER=openrouter
#   OPENROUTER_API_KEY=sk-or-v1-...
#   MODELO_IA_TEXTO=nvidia/nemotron-nano-9b-v2:free
#   MODELO_IA_VISION=nvidia/nemotron-nano-12b-v2-vl:free
#   MODELO_IA_PLAN=nvidia/nemotron-nano-9b-v2:free
#
# Si el nano-3-30b alucina instrucciones, el 9b-v2 suele ir mas limpio.
# Fallbacks automaticos incluidos.
#
# pip install anki_vector python-dotenv openai
# opcional: pip install SpeechRecognition sounddevice
# Ctrl+C guarda. Suelo cerrado.
# ============================================================

from __future__ import annotations

import base64
import io
import json
import math
import os
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

import anki_vector
from anki_vector.events import Events
from anki_vector.util import degrees, distance_mm, speed_mmps
from dotenv import load_dotenv

try:
    from anki_vector.user_intent import UserIntent
except Exception:
    UserIntent = None  # type: ignore

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

try:
    import speech_recognition as sr  # type: ignore
    TIENE_SR = True
except Exception:
    TIENE_SR = False


# ============================================================
# CONFIG / .ENV
# ============================================================

RUTA = Path(__file__).resolve().parent
ARCH_MEM = RUTA / "memoria_vector.json"
ARCH_DIARIO = RUTA / "diario_vector.md"
ARCH_ENV = RUTA / ".env"
load_dotenv(ARCH_ENV)

AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
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

# NVIDIA preferido — 9b-v2 suele obedecer mejor "solo la frase"
MODELO_TEXTO = os.getenv(
    "MODELO_IA_TEXTO", "nvidia/nemotron-nano-9b-v2:free"
).strip()
MODELO_VISION = os.getenv(
    "MODELO_IA_VISION", "nvidia/nemotron-nano-12b-v2-vl:free"
).strip()
MODELO_PLAN = os.getenv("MODELO_IA_PLAN", MODELO_TEXTO).strip()

FALLBACKS_TEXTO = [
    MODELO_TEXTO,
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openrouter/free",
    "google/gemma-4-31b-it:free",
]
FALLBACKS_VISION = [
    MODELO_VISION,
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "openrouter/free",
    "google/gemma-4-26b-a4b-it:free",
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
    r"the answer|final answer|in english|in spanish only"
    r")"
)

_EN_STOP = set(
    "the a an to of and for with we you i is are be this that "
    "need must should would could produce provide output only "
    "words max maximum monologue internal sensory quotes".split()
)


def limpia(t: str, n: int = 100) -> str:
    s = " ".join(str(t or "").split()).strip().strip("\"'`*").strip()
    # quita prefijos tipo "Vector:" o "Respuesta:"
    s = re.sub(r"^(vector|respuesta|output|frase)\s*[:\-–]\s*", "", s, flags=re.I)
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
# MULTI-IA
# ============================================================

def _build_client() -> Tuple[Any, str]:
    if OpenAI is None:
        print("AVISO: pip install openai")
        return None, "none"

    if AI_PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            print("AVISO: falta OPENROUTER_API_KEY en .env")
            return None, "openrouter"
        c = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://local.vector10",
                "X-Title": "Vector-10.1",
            },
        )
        print("IA: OpenRouter OK")
        return c, "openrouter"

    if AI_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            print("AVISO: falta GEMINI_API_KEY en .env")
            return None, "gemini"
        return OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini"

    if AI_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            print("AVISO: falta OPENAI_API_KEY")
            return None, "openai"
        return OpenAI(api_key=OPENAI_API_KEY), "openai"

    if AI_PROVIDER == "ollama":
        return OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL), "ollama"

    if AI_PROVIDER == "custom":
        if not CUSTOM_BASE_URL:
            return None, "custom"
        return OpenAI(
            api_key=CUSTOM_API_KEY or "no-key",
            base_url=CUSTOM_BASE_URL,
        ), "custom"

    if OPENROUTER_API_KEY:
        return OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://local.vector10",
                "X-Title": "Vector-10.1",
            },
        ), "openrouter"
    if GEMINI_API_KEY:
        return OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini"
    if OPENAI_API_KEY:
        return OpenAI(api_key=OPENAI_API_KEY), "openai"
    try:
        return OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL), "ollama"
    except Exception:
        pass
    print("AVISO: sin IA. Frases locales.")
    return None, "none"


client, PROVEEDOR = _build_client()
if client is None:
    USAR_IA_TEXTO = False
    USAR_IA_VISION = False
    USAR_IA_PLAN = False

print("=" * 60)
print(" VECTOR 10.1 — CULMEN ESTABLE")
print("=" * 60)
print("env file :", ARCH_ENV, "| existe:", ARCH_ENV.exists())
print("proveedor:", PROVEEDOR)
print("texto    :", MODELO_TEXTO)
print("vision   :", MODELO_VISION)
print("plan     :", MODELO_PLAN)
print(
    "flags    : mirada={} cuerpo={} patrulla={} sr={}".format(
        SEGUIR_MIRADA, SEGUIR_CUERPO, MODO_PATRULLA,
        TIENE_SR and USAR_ESCUCHA_PC,
    )
)


def ia_raw(
    messages: List[Dict[str, Any]],
    model: str,
    fallbacks: Optional[List[str]] = None,
    max_tokens: int = 40,
    temperature: float = 0.7,
    timeout: float = 20.0,
) -> str:
    if client is None:
        return ""
    modelos = []
    for m in [model] + list(fallbacks or []):
        if m and m not in modelos:
            modelos.append(m)
    err = None
    for mod in modelos:
        try:
            r = client.chat.completions.create(
                model=mod,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            txt = (r.choices[0].message.content or "").strip()
            # si el modelo mete "thinking" multilinea, coge ultima linea corta
            if "\n" in txt:
                lineas = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                # prefiere la ultima linea que NO sea basura
                elegida = ""
                for ln in reversed(lineas):
                    if not es_basura_ia(ln) and len(ln.split()) <= 14:
                        elegida = ln
                        break
                txt = elegida or lineas[-1]
            if txt:
                if mod != model:
                    print("IA fallback OK:", mod)
                return txt
        except Exception as e:
            err = e
            print("IA fail", mod, "->", e)
    if err:
        print("IA agotada:", err)
    return ""


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
}

VOZ_RITMO = {
    "feliz": 0.76, "jugueton": 0.70, "curioso": 0.80, "amoroso": 0.90,
    "triste": 0.98, "cansado": 1.05, "aburrido": 0.93, "asustado": 0.68,
    "enfadado": 0.72, "sorprendido": 0.66, "pensativo": 0.92, "atento": 0.82,
    "tranquilo": 0.86, "cazador": 0.74, "sueno": 1.08, "patrulla": 0.84,
    "fiesta": 0.68,
}

TRIGGERS = {
    "saludo": ["GreetAfterLongTime", "GreetingAwe"],
    "feliz": ["Feedback_GoodRobot", "ComeHereSuccess"],
    "amor": ["Feedback_ILoveYou"],
    "triste": ["Feedback_BeQuiet", "Feedback_Apology"],
    "enfadado": ["Feedback_BadRobot"],
    "sorpresa": ["ExploringReactToObstacle"],
    "curioso": ["ExploreHint", "FindFacesLookAround"],
    "buscar_cara": ["FindFacesLookAround", "LookInPlaceForFacesHeadMove"],
    "cubo": ["FindCubeReactToCube"],
    "dormir": ["GoToSleepGetIn"],
    "despertar": ["ConnectWakeUp"],
    "baile": ["DanceBeatPickup"],
    "come_here": ["ComeHereSuccess"],
    "pensar": ["KnowledgeGraphGetIn"],
    "escuchar": ["KnowledgeGraphListeningLoop"],
    "explorar": ["ExploreStart", "ExploringScanToLeft"],
    "carga": ["ChargerDockingDrivingStart"],
    "ojo": ["EyeContactLookLoop"],
}

ANIM_FB = {
    "saludo": ["anim_greeting_happy_01", "anim_greeting_happy_02"],
    "feliz": ["anim_freeplay_reacttoface_identified_01"],
    "sorpresa": ["anim_reacttoblock_reacttolongpickup_02"],
    "curioso": ["anim_explorer_scan_short_01", "anim_explorer_huh_01"],
    "enfadado": ["anim_reacttocliff_stuckonedge_01"],
    "amor": ["anim_petdetection_snoutgetin_01"],
    "triste": ["anim_explorer_huh_01"],
    "baile": ["anim_freeplay_reacttoface_identified_01"],
    "pensar": ["anim_explorer_scan_short_01"],
    "escuchar": ["anim_explorer_huh_01"],
    "cubo": ["anim_freeplay_reacttoface_identified_01"],
    "come_here": ["anim_greeting_happy_01"],
    "explorar": ["anim_explorer_scan_short_01"],
    "dormir": ["anim_explorer_huh_01"],
    "despertar": ["anim_greeting_happy_01"],
    "carga": ["anim_explorer_huh_01"],
    "buscar_cara": ["anim_explorer_scan_short_01"],
    "ojo": ["anim_explorer_huh_01"],
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
    (r"\b(ven|come here|aqui|ven aqui)\b", "venir"),
    (r"\b(explora|explorar|pasea)\b", "explorar"),
    (r"\b(baila|dance)\b", "bailar"),
    (r"\b(cubo|cube|juega)\b", "cubo"),
    (r"\b(coge|levanta|pick up)\b", "pickup"),
    (r"\b(rueda el cubo|roll)\b", "roll"),
    (r"\b(wheelie|caballito)\b", "wheelie"),
    (r"\b(carga|cargador|duerme|descansa)\b", "cargar"),
    (r"\b(hola|hello|buenas)\b", "hola"),
    (r"\b(te quiero|te amo|i love you)\b", "amor"),
    (r"\b(mal robot|bad robot)\b", "regano"),
    (r"\b(buen robot|good robot|bravo)\b", "elogio"),
    (r"\b(que ves|observa|mira)\b", "observar"),
    (r"\b(como estas|que tal)\b", "estado"),
    (r"\b(quien soy|mi nombre)\b", "nombre"),
    (r"\b(piensa|que piensas)\b", "pensar"),
    (r"\b(gira|vuelta)\b", "girar"),
    (r"\b(adelante|avanza)\b", "adelante"),
    (r"\b(atras|retrocede)\b", "atras"),
    (r"\b(choca|fist)\b", "fistbump"),
    (r"\b(sigueme|follow)\b", "seguir"),
    (r"\b(no me sigas|suelta)\b", "no_seguir"),
    (r"\b(mirame)\b", "mirarme"),
    (r"\b(busca caras)\b", "buscar_caras"),
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
        rec(m, "sistema", "Reset emocional automatico 10.1", peso=0.5)
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
            "## {} — Vector 10.1".format(iso()),
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
            robot.behavior.say_text(
                t, use_vector_voice=True, duration_scalar=ritmo,
            )
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
        r = robot.proximity.last_valid_sensor_reading
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
        e = robot.get_battery_state()
        return bool(e and e.battery_volts <= V_BAT_BAJA)
    except Exception:
        return False


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
    ojos(robot, "curioso"); brazo(robot, 0.06)
    for a in (34, 14, -6, 24):
        cabeza(robot, a); time.sleep(0.09)


def follow_face(robot: Any, cara: Any) -> bool:
    try:
        robot.behavior.turn_towards_face(cara)
        return True
    except Exception:
        return False


def head_track_face(robot: Any, cara: Any) -> None:
    try:
        pose = getattr(cara, "pose", None)
        ang = 26.0
        if pose is not None:
            try:
                z = float(getattr(pose.position, "z", 0) or 0)
                ang = clamp(18 + z * 0.05, 8, 40)
            except Exception:
                ang = 28.0
        cabeza(robot, ang, lento=True)
    except Exception:
        cabeza(robot, 26, lento=True)


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
        d = prox_ok(robot)
        if d is not None and d < 280:
            return True
        ok = avanzar_seguro(robot, 26, mapa, m, vel_fija=V_SIGUE)
        if ok:
            c.ev("seguir"); c.set_estado("siguiendo")
            stat(m, "seguimientos"); habito(m, "seguir")
        return ok
    except Exception:
        return False


def buscar_caras(robot: Any) -> bool:
    ojos(robot, "curioso"); animar(robot, "buscar_cara")
    try:
        robot.behavior.find_faces()
        return True
    except Exception:
        for g in (-35, 70, -70, 35):
            girar(robot, g)
            cabeza(robot, random.choice([18, 28, 34]))
            time.sleep(0.12)
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


def fist_bump(robot: Any) -> bool:
    try:
        robot.behavior.fist_bump(); return True
    except Exception:
        brazo(robot, 0.8); time.sleep(0.25); brazo(robot, 0.0); return True


# ============================================================
# IA ALTA NIVEL (prompts anti-meta)
# ============================================================

SYS_HABLA = (
    "Eres Vector, un robot mascota pequeno. "
    "Responde SOLO con una frase final en espanol, maximo 10 palabras. "
    "Prohibido: ingles, explicaciones, instrucciones, pensar en voz alta, "
    "decir 'we need', 'monologue', 'words', comillas o markdown. "
    "Solo la frase que diria el robot en voz alta."
)

SYS_PENSAR = (
    "Eres el pensamiento corto de Vector. "
    "Responde SOLO una frase en espanol de maximo 9 palabras. "
    "Prohibido ingles, meta, instrucciones o explicar la tarea. "
    "Solo el pensamiento, nada mas."
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
    # user message simple — menos tokens, menos meta
    user = (
        "Situacion: {ev}. Animo: {animo}. Di una frase corta. {extra}"
    ).format(ev=ev, animo=c.animo, extra=extra)
    # opcional contexto breve
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
        temperature=0.65,  # mas bajo = menos rollo meta
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
    user = "Te dicen: {}. Responde en espanol, maximo 11 palabras.".format(
        limpia(humano, 80)
    )
    txt = ia_raw(
        [
            {"role": "system", "content": SYS_HABLA},
            {"role": "user", "content": user},
        ],
        MODELO_TEXTO, FALLBACKS_TEXTO, 36, 0.6, 18,
    )
    out = frase_segura(txt, fb)
    if out == fb and txt and es_basura_ia(txt):
        stat(m, "ia_basura_filtrada")
    elif out != fb:
        stat(m, "respuestas")
    return out


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
        print("cam:", e)
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


# ============================================================
# ESCUCHA
# ============================================================

class Escucha:
    def __init__(self, q: "queue.Queue") -> None:
        self.q = q
        self.stop = threading.Event()

    def push(self, origen: str, payload: str) -> None:
        self.q.put((origen, payload))

    def start_pc(self) -> None:
        if not (TIENE_SR and USAR_ESCUCHA_PC):
            print("Escucha PC off (normal si no instalaste SpeechRecognition)")
            return
        threading.Thread(target=self._loop, daemon=True).start()
        print("Escucha PC ON")

    def _loop(self) -> None:
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
                print("PC oido:", low)
                if any(w in low for w in (
                    "vector", "victor", "bector", "oye robot", "hey vector",
                )):
                    self.push("pc", low)
                elif any(re.search(p, low) for p, _ in LEXICO[:6]):
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
    c.set_estado("escuchando"); gesto(robot, "escuchar")

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
        decir(robot, "Pequenito y eterno", c); return
    if orden == "hora":
        decir(robot, "Son las {}".format(datetime.now().strftime("%H:%M")), c); return
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
        fist_bump(robot); gesto(robot, "feliz"); return

    if orden == "clima" and data:
        try:
            j = json.loads(data)
            decir(robot, "En {} esta {}".format(
                j.get("speakableLocationString", "aqui"),
                j.get("condition", "raro"),
            ), c)
        except Exception:
            decir(robot, "Cielo raro", c)
        return

    hum = bruto or data or orden
    gesto(robot, "pensar")
    resp = ia_responder(m, c, hum)
    decir(robot, resp, c); dia_push(m, "vector", resp)
    c.ev("conversacion")
    rec(m, "dialogo", "H:{} V:{}".format(limpia(hum, 40), limpia(resp, 40)), 1.1)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    m = cargar_mem()
    c = Cerebro()
    c.load(m)
    mapa = Mapa()
    mapa.load(m)

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
    rec(m, "inicio", "Inicio Vector 10.1 " + fase_dia(), 1.5)
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
    }
    acc_prev = None
    plan_cache = "explorar"
    had_face = False

    with anki_vector.Robot(
        enable_face_detection=True,
        estimate_facial_expression=True,
        enable_nav_map_feed=NAV_MAP,
        show_viewer=SHOW_CAMERA,
        show_3d_viewer=SHOW_3D,
        cache_animation_lists=True,
    ) as robot:

        print(
            "Cerebro {}/{} E={:.0f} C={:.0f} A={:.0f} B={:.0f} S={:.0f} M={:.0f}".format(
                c.animo, c.estado, c.energia, c.curiosidad, c.afecto,
                c.aburrimiento, c.social, c.miedo,
            )
        )

        try:
            robot.camera.init_camera_feed()
            print("Camara OK")
        except Exception as e:
            print("Camara:", e)

        def on_face(robot_evt, event_type, event):  # noqa: ANN001, ARG001
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

        ojos(robot, "feliz")
        animar(robot, "despertar")
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
        proximo = time.time() + 2.5
        print("Vector 10.1 activo. Ctrl+C detiene.")
        print("Si ves 'IA basura filtrada' es NORMAL: el filtro protege la voz.")

        try:
            while True:
                now = time.time()
                c.decay()
                mapa.decay(0.995)

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
                                cubo_luces(robot, "azul")
                                decir(
                                    robot,
                                    random.choice(FRASES["cubo_tap"]),
                                    c,
                                )
                                ejecutar(
                                    "jugar", robot, m, c, mapa, obs, qhabla,
                                )
                        else:
                            o = parse_pc(payload)
                            brazo(robot, 0.2)
                            time.sleep(0.05)
                            brazo(robot, 0.0)
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
                        proximo = time.time() + 3.0
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
                            decir(
                                robot,
                                random.choice(FRASES["caricia_larga"]),
                                c,
                            )
                        elif dur >= 0.85:
                            c.ev("caricia")
                            gesto(robot, "feliz")
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
                    if random.random() < 0.02 and not ia_busy.is_set():
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
                                and random.random() < 0.22
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

                time.sleep(0.12)

        finally:
            escucha.stop.set()
            with _mem_lock:
                m.setdefault("estado_interno", {})[
                    "ultima_sesion_fin"
                ] = iso()
            c.dump(m)
            mapa.save(m)
            sanear_estado_interno(m)
            rec(m, "fin", "Fin Vector 10.1", 1.0)
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDeteniendo Vector 10.1...")
