# ============================================================
# VECTOR ULTRA 13.5 — CENTRO DE MANDO WEB AVANZADO (FASTAPI)
# ============================================================
import io
import os
import time
import queue
import threading
import secrets
import hmac
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, Response, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import uvicorn
from PIL import Image, ImageEnhance, ImageOps, ImageDraw
from dotenv import load_dotenv
import requests

load_dotenv(Path(__file__).parent / ".env")

# Token de acceso al dashboard (se genera aleatorio si no está en .env)
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip() or secrets.token_urlsafe(16)

# Dependencia de autenticación para endpoints protegidos
async def verify_token(x_auth_token: Optional[str] = Header(None), token: Optional[str] = None):
    """Verifica token en header X-Auth-Token o query param ?token=."""
    provided = x_auth_token or token
    if not provided or not hmac.compare_digest(provided, DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="Token inválido o ausente")
    return True

web_command_queue: "queue.Queue" = queue.Queue()

# Modo de visión de cámara: "normal", "brillante", "nocturna"
_modo_vision: str = "brillante"

telemetria_data: Dict[str, Any] = {
    "online": False,
    "bateria_v": 0.0,
    "bateria_pct": 0,
    "en_cargador": False,
    "animo": "curioso",
    "estado": "explorando",
    "tof_mm": 0,
    "ultima_frase": "Iniciando Vector Ultra...",
    "ultimo_pensamiento": "Sincronizando...",
    "modo_camara": "brillante",
}

# Registro histórico de diálogo y pensamientos
registro_chat: List[Dict[str, Any]] = [
    {"tipo": "sistema", "autor": "Sistema", "texto": "Vector Ultra 13.5 conectado al Centro de Mando.", "hora": time.strftime("%H:%M:%S")}
]
_chat_lock = threading.Lock()

_ultimo_frame_bytes: bytes = b""
_ultimo_frame_t: float = 0.0
_frame_lock = threading.Lock()
#: si el ultimo frame tiene mas de estos segundos, se considera camara caida
FRAME_STALE_S = 12.0

def registrar_habla_vector(texto: str, animo: str = "curioso") -> None:
    global telemetria_data
    telemetria_data["ultima_frase"] = texto
    telemetria_data["animo"] = animo
    entrada = {"tipo": "voz", "autor": "Vector", "texto": texto, "hora": time.strftime("%H:%M:%S")}
    with _chat_lock:
        registro_chat.append(entrada)
        if len(registro_chat) > 50:
            registro_chat.pop(0)

def registrar_pensamiento_vector(pensamiento: str) -> None:
    global telemetria_data
    telemetria_data["ultimo_pensamiento"] = pensamiento
    entrada = {"tipo": "pensamiento", "autor": "Pensamiento", "texto": pensamiento, "hora": time.strftime("%H:%M:%S")}
    with _chat_lock:
        registro_chat.append(entrada)
        if len(registro_chat) > 50:
            registro_chat.pop(0)

def registrar_mensaje_humano(texto: str, origen: str = "Web") -> None:
    entrada = {"tipo": "humano", "autor": origen, "texto": texto, "hora": time.strftime("%H:%M:%S")}
    with _chat_lock:
        registro_chat.append(entrada)
        if len(registro_chat) > 50:
            registro_chat.pop(0)

def actualizar_telemetria(datos: Dict[str, Any]) -> None:
    global telemetria_data
    telemetria_data.update(datos)

def actualizar_frame_camara(pil_image: Image.Image) -> None:
    global _ultimo_frame_bytes, _ultimo_frame_t, _modo_vision
    try:
        img = pil_image
        # Filtros de realce digital en tiempo real
        if _modo_vision == "brillante":
            img = ImageOps.autocontrast(img, cutoff=2)
            img = ImageEnhance.Brightness(img).enhance(1.45)
            img = ImageEnhance.Contrast(img).enhance(1.20)
            img = ImageEnhance.Sharpness(img).enhance(1.30)
        elif _modo_vision == "nocturna":
            img = ImageOps.autocontrast(img, cutoff=3)
            img = ImageEnhance.Brightness(img).enhance(1.90)
            img = ImageEnhance.Contrast(img).enhance(1.35)
            img = ImageEnhance.Sharpness(img).enhance(1.40)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        with _frame_lock:
            _ultimo_frame_bytes = buf.getvalue()
            _ultimo_frame_t = time.time()
    except Exception:
        pass

_placeholder_cache: bytes = b""


def _frame_placeholder(texto: str = "SIN SENAL DE CAMARA") -> bytes:
    """Frame de sustitucion: el stream MJPEG nunca debe quedarse colgado."""
    global _placeholder_cache
    if _placeholder_cache:
        return _placeholder_cache
    try:
        img = Image.new("RGB", (640, 360), (7, 11, 18))
        d = ImageDraw.Draw(img)
        for y in range(0, 360, 4):
            d.line([(0, y), (640, y)], fill=(10, 16, 26))
        d.rectangle([12, 12, 627, 347], outline=(30, 60, 80), width=2)
        d.text((24, 160), texto, fill=(90, 140, 165))
        d.text((24, 182), "esperando a Vector...", fill=(50, 80, 100))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        _placeholder_cache = buf.getvalue()
    except Exception:
        _placeholder_cache = b""
    return _placeholder_cache


def ultimo_frame_bytes() -> bytes:
    """Ultimo frame JPEG capturado (lo usa la vision IA del nucleo).

    Evita que el nucleo toque robot.camera.latest_image, que bloquea.
    """
    with _frame_lock:
        return _ultimo_frame_bytes


def frame_fresco() -> bool:
    """True si el ultimo frame de camara es reciente (camara viva)."""
    with _frame_lock:
        t = _ultimo_frame_t
    return bool(t) and (time.time() - t) < FRAME_STALE_S


def _generador_mjpeg():
    """Stream MJPEG continuo. Si no hay frame real (o esta obsoleto), emite un
    placeholder: asi la conexion nunca se bloquea y se ve que la camara cayo."""
    while True:
        with _frame_lock:
            frame = _ultimo_frame_bytes
            t = _ultimo_frame_t
        if not frame or (time.time() - t) >= FRAME_STALE_S:
            frame = _frame_placeholder()
        if frame:
            header = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
            )
            yield (header + frame + b"\r\n")
        time.sleep(0.06)

app = FastAPI(title="Vector Ultra 13.5 Command Center")

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Vector Ultra 13.5 — Centro de Mando</title>
    <style>
        :root {
            --bg-color: #0b0e14;
            --panel-bg: #151922;
            --panel-card: #1c212d;
            --accent: #00ff88;
            --accent-glow: rgba(0, 255, 136, 0.35);
            --blue: #58a6ff;
            --purple: #d2a8ff;
            --danger: #ff4757;
            --warning: #f1c40f;
            --text: #f0f6fc;
            --text-muted: #8b949e;
            --border: #30363d;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background: var(--bg-color); color: var(--text); padding: 12px; max-width: 1300px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; padding: 12px 18px; background: var(--panel-bg); border-radius: 12px; border: 1px solid var(--border); margin-bottom: 10px; }
        .logo { font-size: 1.25rem; font-weight: 800; color: var(--accent); text-shadow: 0 0 12px var(--accent-glow); display: flex; align-items: center; gap: 8px; }
        .badge { background: #238636; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 700; }
        
        /* TOAST FLOTANTE DE ACCIÓN */
        #toast { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); background: rgba(0, 255, 136, 0.9); color: #000; padding: 8px 18px; border-radius: 30px; font-weight: 700; font-size: 0.85rem; z-index: 9999; box-shadow: 0 4px 15px rgba(0,255,136,0.4); opacity: 0; transition: opacity 0.25s, transform 0.25s; pointer-events: none; }
        #toast.show { opacity: 1; transform: translateX(-50%) translateY(5px); }

        .grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
        @media (min-width: 960px) { .grid { grid-template-columns: 1.2fr 0.8fr; } }
        
        .card { background: var(--panel-bg); border: 1px solid var(--border); border-radius: 12px; padding: 14px; }
        .card h2 { font-size: 1rem; color: var(--accent); margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
        
        .video-box { width: 100%; height: 320px; background: #000; border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center; position: relative; border: 1px solid var(--border); }
        .video-box img { width: 100%; height: 100%; object-fit: contain; }
        
        .cam-controls { display: flex; gap: 6px; margin-top: 8px; justify-content: center; flex-wrap: wrap; }
        .cam-btn { padding: 6px 12px; font-size: 0.75rem; background: var(--panel-card); border: 1px solid var(--border); border-radius: 6px; color: var(--text-muted); cursor: pointer; }
        .cam-btn.active { background: #238636; color: white; border-color: var(--accent); }
        
        .tele-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 10px; }
        .tele-item { background: var(--panel-card); padding: 8px; border-radius: 8px; border: 1px solid #28303f; text-align: center; }
        .tele-label { font-size: 0.65rem; color: var(--text-muted); text-transform: uppercase; }
        .tele-val { font-size: 1rem; font-weight: 700; color: #fff; margin-top: 2px; }
        
        /* CHAT / SUBTÍTULOS EN VIVO */
        .chat-container { background: #0a0d13; border: 1px solid var(--border); border-radius: 8px; height: 210px; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
        .bubble { padding: 8px 12px; border-radius: 8px; font-size: 0.85rem; line-height: 1.35; max-width: 90%; }
        .bubble.voz { background: rgba(0, 255, 136, 0.1); border: 1px solid rgba(0, 255, 136, 0.3); color: var(--accent); align-self: flex-start; }
        .bubble.pensamiento { background: rgba(210, 168, 255, 0.1); border: 1px solid rgba(210, 168, 255, 0.25); color: var(--purple); font-style: italic; align-self: flex-start; font-size: 0.8rem; }
        .bubble.humano { background: rgba(88, 166, 255, 0.15); border: 1px solid rgba(88, 166, 255, 0.3); color: var(--blue); align-self: flex-end; }
        .bubble.sistema { background: #161b22; color: var(--text-muted); align-self: center; font-size: 0.75rem; }
        .bubble-meta { font-size: 0.65rem; opacity: 0.7; margin-bottom: 2px; }

        /* JOYSTICK */
        .joystick-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; max-width: 260px; margin: 0 auto; padding: 6px; }
        button { background: var(--panel-card); color: var(--text); border: 1px solid var(--border); padding: 12px; border-radius: 8px; font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: all 0.1s ease; user-select: none; touch-action: manipulation; }
        button:active { background: var(--accent); color: #000; transform: scale(0.92); }
        .btn-stop { background: var(--danger); border-color: #b33939; color: white; }
        
        .color-row { display: flex; gap: 6px; margin-top: 8px; justify-content: center; }
        .color-dot { width: 32px; height: 32px; border-radius: 50%; cursor: pointer; border: 2px solid var(--border); transition: transform 0.1s; }
        .color-dot:active { transform: scale(1.25); }
        
        .actions-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px; }
        .input-group { display: flex; gap: 6px; margin-top: 8px; }
        input[type="text"] { flex: 1; background: #0a0d13; border: 1px solid var(--border); color: white; padding: 10px 12px; border-radius: 8px; font-size: 0.85rem; outline: none; }
        input[type="text"]:focus { border-color: var(--accent); }
    </style>
</head>
<body>
    <script>const DASHBOARD_TOKEN = "{{DASHBOARD_TOKEN}}";</script>
    <div id="toast">Acción recibida</div>

    <header>
        <div class="logo">🤖 VECTOR ULTRA 13.5</div>
        <div class="badge" id="status-badge">ONLINE</div>
    </header>

    <div class="grid">
        <!-- COLUMNA 1: VÍDEO Y DIÁLOGO -->
        <div class="card">
            <h2>📹 Visión en Directo y Cámara HD</h2>
            <div class="video-box">
                <img id="video-stream" src="/video_feed" alt="Cámara Vector" onerror="reintentarCamara()">
            </div>
            <div class="cam-controls">
                <button class="cam-btn" id="btn-cam-normal" onclick="setCamMode('normal')">Luz Normal</button>
                <button class="cam-btn active" id="btn-cam-brillante" onclick="setCamMode('brillante')">✨ Filtro Brillante</button>
                <button class="cam-btn" id="btn-cam-nocturna" onclick="setCamMode('nocturna')">🌙 Visión Nocturna</button>
                <button class="cam-btn" onclick="reintentarCamara()">🔄 Recargar Vídeo</button>
            </div>

            <div class="tele-grid">
                <div class="tele-item">
                    <div class="tele-label">Batería</div>
                    <div class="tele-val" id="val-bat">--V</div>
                </div>
                <div class="tele-item">
                    <div class="tele-label">Ánimo</div>
                    <div class="tele-val" id="val-animo" style="color: var(--accent)">--</div>
                </div>
                <div class="tele-item">
                    <div class="tele-label">Sensor ToF</div>
                    <div class="tele-val" id="val-tof">-- mm</div>
                </div>
                <div class="tele-item">
                    <div class="tele-label">Ubicación</div>
                    <div class="tele-val" id="val-chg">--</div>
                </div>
            </div>

            <h2 style="margin-top: 14px;">💬 Subtítulos y Diálogo en Vivo</h2>
            <div class="chat-container" id="chat-box">
                <!-- Se llena dinámicamente -->
            </div>
        </div>

        <!-- COLUMNA 2: MANDOS Y HABILIDADES -->
        <div class="card">
            <h2>🎮 Control Manual Inmediato</h2>
            <div class="joystick-grid">
                <div></div>
                <button onclick="enviarControl('adelante', '⬆️ Adelante')">⬆️</button>
                <div></div>
                <button onclick="enviarControl('izquierda', '⬅️ Izquierda')">⬅️</button>
                <button class="btn-stop" onclick="enviarControl('parar', '⏹️ Parar')">⏹️</button>
                <button onclick="enviarControl('derecha', '➡️ Derecha')">➡️</button>
                <div></div>
                <button onclick="enviarControl('atras', '⬇️ Atrás')">⬇️</button>
                <div></div>
            </div>

            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 8px;">
                <button onclick="enviarControl('cabeza_arriba', '👀 Cabeza Arriba')">👀 ⬆️</button>
                <button onclick="enviarControl('cabeza_abajo', '👀 Cabeza Abajo')">👀 ⬇️</button>
                <button onclick="enviarControl('pala_arriba', '🦾 Pala Arriba')">🦾 ⬆️</button>
                <button onclick="enviarControl('pala_abajo', '🦾 Pala Abajo')">🦾 ⬇️</button>
            </div>

            <div style="margin-top: 8px;">
                <button onclick="enviarControl('salir_cargador', '🐾 Saliendo del Cargador')" style="width: 100%; background: #21262d; border-color: var(--warning); color: var(--warning);">
                    🔌 Salir del Cargador a la Mesa
                </button>
            </div>

            <h2 style="margin-top: 14px;">🎨 Color de Ojos OLED</h2>
            <div class="color-row">
                <div class="color-dot" style="background: #00ff88;" title="Verde Esmeralda" onclick="enviarColor('verde', 'Verde')"></div>
                <div class="color-dot" style="background: #00a8ff;" title="Azul Zafiro" onclick="enviarColor('azul', 'Azul')"></div>
                <div class="color-dot" style="background: #9b59b6;" title="Morado Cyberpunk" onclick="enviarColor('morado', 'Morado')"></div>
                <div class="color-dot" style="background: #f1c40f;" title="Amarillo Oro" onclick="enviarColor('amarillo', 'Amarillo')"></div>
                <div class="color-dot" style="background: #e74c3c;" title="Rojo Furia" onclick="enviarColor('rojo', 'Rojo')"></div>
                <div class="color-dot" style="background: #ffffff;" title="Blanco Ártico" onclick="enviarColor('blanco', 'Blanco')"></div>
            </div>

            <h2 style="margin-top: 14px;">🎙️ Hablar por Voz desde el Móvil (Intercom)</h2>
            <div style="display: flex; gap: 8px;">
                <button id="btn-mic" onclick="toggleMicrofono()" style="flex: 1; background: #238636; color: white; display: flex; align-items: center; justify-content: center; gap: 8px; font-size: 1rem; padding: 14px;">
                    🎙️ <span id="mic-text">Pulsar para Hablar</span>
                </button>
            </div>
            <div id="mic-transcript" style="font-size: 0.8rem; color: var(--accent); margin-top: 4px; min-height: 18px;"></div>

            <h2 style="margin-top: 14px;">⚡ Habilidades y Efectos</h2>
            <div class="actions-grid">
                <button onclick="enviarAccion('foto', '📸 Tomando Foto...')">📸 Foto HD</button>
                <button onclick="enviarAccion('patrulla', '🛡️ Iniciando Patrulla')">🛡️ Patrulla</button>
                <button onclick="enviarAccion('inspeccionar', '👁️ Examinando con IA...')">👁️ Ver con IA</button>
                <button onclick="enviarAccion('dado', '🎲 Tirando Dado')">🎲 Dado</button>
                <button onclick="enviarAccion('moneda', '🪙 Moneda')">🪙 Moneda</button>
                <button onclick="enviarAccion('matrix', '📟 Matrix OLED')">📟 Matrix</button>
            </div>

            <h2 style="margin-top: 14px;">🗣️ Enviar Frase Escrita a Vector</h2>
            <div class="input-group">
                <input type="text" id="input-say" placeholder="Escribe lo que quieres que diga en voz alta...">
                <button onclick="enviarHabla()" style="background: #238636; color: white;">Hablar</button>
            </div>

            <h2 style="margin-top: 14px;">📚 Consultar Vault (Obsidian)</h2>
            <div class="input-group">
                <input type="text" id="input-vault" placeholder="Pregunta sobre Power Scaling, notas, prompts...">
                <button onclick="enviarVault()">Buscar</button>
            </div>
            <div id="vault-res" style="font-size: 0.8rem; color: var(--blue); margin-top: 6px;"></div>
        </div>

        <!-- FLYBRAIN: connectome de mosca en vivo -->
        <div class="card" style="grid-column: 1 / -1;">
            <h2>🪰 FlyBrain · Connectome MaleCNS (166.700 neuronas)
                <a href="http://127.0.0.1:4712/" target="_blank" style="margin-left:auto; font-size:0.75rem; color: var(--blue);">Abrir visor completo ↗</a>
            </h2>
            <canvas id="fly-eye" width="600" height="64" style="width:100%; background:#05080d; border-radius:8px;"></canvas>
            <div class="tele-grid" style="grid-template-columns: repeat(6, 1fr);">
                <div class="tele-item"><div class="tele-label">LC4 (amenaza)</div><div class="tele-val" id="fly-lc4">--</div></div>
                <div class="tele-item"><div class="tele-label">LPLC2 (looming)</div><div class="tele-val" id="fly-lplc2">--</div></div>
                <div class="tele-item"><div class="tele-label">Escape</div><div class="tele-val" id="fly-esc">--</div></div>
                <div class="tele-item"><div class="tele-label">Modo</div><div class="tele-val" id="fly-mode">--</div></div>
                <div class="tele-item"><div class="tele-label">Rueda L</div><div class="tele-val" id="fly-l">--</div></div>
                <div class="tele-item"><div class="tele-label">Rueda R</div><div class="tele-val" id="fly-r">--</div></div>
            </div>
        </div>
    </div>

    <script>
        function showToast(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.classList.add('show');
            if (navigator.vibrate) navigator.vibrate(35);
            setTimeout(() => t.classList.remove('show'), 1600);
        }

        function reintentarCamara() {
            const img = document.getElementById('video-stream');
            img.src = '/video_feed?t=' + Date.now();
            showToast('🔄 Reconectando cámara...');
        }

function enviarControl(accion, label) {
            showToast(label || 'Enviando control...');
            fetch('/api/control', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ accion: accion }) });
        }
        function enviarAccion(accion, label) {
            showToast(label || 'Ejecutando acción...');
            fetch('/api/accion', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ accion: accion }) });
        }
        function enviarColor(color, label) {
            showToast('Ojos cambiados a ' + label);
            fetch('/api/color', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ color: color }) });
        }
        function setCamMode(modo) {
            fetch('/api/cam_mode', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ modo: modo }) })
                .then(() => {
                    document.querySelectorAll('.cam-btn').forEach(b => b.classList.remove('active'));
                    document.getElementById(`btn-cam-${modo}`).classList.add('active');
                    showToast('Filtro de cámara: ' + modo);
                    reintentarCamara();
                });
        }
function enviarHabla() {
            const txt = document.getElementById('input-say').value.trim();
            if (!txt) return;
            showToast('🗣️ Enviando frase a Vector...');
            fetch('/api/decir', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ texto: txt }) });
            document.getElementById('input-say').value = '';
        }
        function enviarVault() {
            const q = document.getElementById('input-vault').value.trim();
            if (!q) return;
            document.getElementById('vault-res').innerText = 'Consultando notas en RAM...';
            fetch('/api/vault', { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN}, body: JSON.stringify({ query: q }) })
                .then(r => r.json())
                .then(d => { document.getElementById('vault-res').innerText = d.resultado || 'Sin resultados.'; });
        }

        let recognition = null;
        let isListening = false;

        function toggleMicrofono() {
            const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRec) {
                alert("Tu navegador no soporta Speech Recognition nativo. Abre Chrome o Edge en tu móvil.");
                return;
            }

            const btn = document.getElementById('btn-mic');
            const txt = document.getElementById('mic-text');
            const statusDiv = document.getElementById('mic-transcript');

            if (!recognition) {
                recognition = new SpeechRec();
                recognition.lang = 'es-ES';
                recognition.continuous = false;
                recognition.interimResults = true;

                recognition.onstart = () => {
                    isListening = true;
                    btn.style.background = '#ff4757';
                    txt.innerText = 'Escuchando... ¡Habla ahora!';
                    statusDiv.innerText = 'Escuchando...';
                    showToast('🎙️ Micrófono activo');
                };

                recognition.onresult = (event) => {
                    let trans = '';
                    for (let i = event.resultIndex; i < event.results.length; ++i) {
                        trans += event.results[i][0].transcript;
                    }
                    statusDiv.innerText = `"${trans}"`;
                    if (event.results[0].isFinal) {
                        enviarIntercom(trans);
                    }
                };

                recognition.onerror = () => {
                    isListening = false;
                    btn.style.background = '#238636';
                    txt.innerText = 'Pulsar para Hablar';
                    statusDiv.innerText = 'Micrófono inactivo.';
                };

                recognition.onend = () => {
                    isListening = false;
                    btn.style.background = '#238636';
                    txt.innerText = 'Pulsar para Hablar';
                };
            }

            if (!isListening) {
                try { recognition.start(); } catch (e) {}
            } else {
                try { recognition.stop(); } catch (e) {}
            }
        }

function enviarIntercom(texto) {
            if (!texto || !texto.trim()) return;
            showToast('🎙️ Enviando voz a Vector...');
            fetch('/api/intercom', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Auth-Token': DASHBOARD_TOKEN},
                body: JSON.stringify({ texto: texto.trim() })
            });
        }

        let ultimoChatLen = 0;
        setInterval(() => {
            fetch('/api/telemetria')
                .then(r => r.json())
                .then(d => {
                    document.getElementById('val-bat').innerText = `${d.bateria_v.toFixed(2)}V (${d.bateria_pct}%)`;
                    document.getElementById('val-animo').innerText = `${d.animo}`;
                    document.getElementById('val-tof').innerText = `${d.tof_mm} mm`;
                    document.getElementById('val-chg').innerText = d.en_cargador ? 'Base 🔌' : 'Mesa 🐾';
                    document.getElementById('status-badge').innerText = d.online ? 'ONLINE' : 'STANDBY';
                    document.getElementById('status-badge').style.background = d.online ? '#238636' : '#d73a49';
                }).catch(() => {});

            fetch('/api/chat_log')
                .then(r => r.json())
                .then(items => {
                    if (items.length !== ultimoChatLen) {
                        ultimoChatLen = items.length;
                        const box = document.getElementById('chat-box');
                        box.innerHTML = '';
                        items.forEach(it => {
                            const d = document.createElement('div');
                            d.className = `bubble ${it.tipo}`;
                            d.innerHTML = `<div class="bubble-meta">${it.hora} • ${it.autor}</div>${it.texto}`;
                            box.appendChild(d);
                        });
                        box.scrollTop = box.scrollHeight;
                    }
                }).catch(() => {});
        }, 1000);

        // --- FlyBrain (mosca) ---
        const flyCv = document.getElementById('fly-eye');
        const flyCtx = flyCv.getContext('2d');
        function dibujarRetina(eye) {
            const w = flyCv.width, h = flyCv.height;
            flyCtx.fillStyle = '#05080d'; flyCtx.fillRect(0, 0, w, h);
            if (!eye || !eye.length) return;
            const bw = w / eye.length;
            for (let i = 0; i < eye.length; i++) {
                const v = Math.max(0, Math.min(1, eye[i]));
                flyCtx.fillStyle = `rgb(${Math.floor(30+v*225)},${Math.floor(60+v*195)},${Math.floor(90+v*165)})`;
                flyCtx.fillRect(i * bw, 0, bw + 0.5, h);
            }
        }
        setInterval(() => {
            fetch('/api/fly').then(r => r.json()).then(d => {
                if (!d || !d.connected) {
                    document.getElementById('fly-mode').innerText = 'offline';
                    return;
                }
                const s = d.snapshot || {}, m = d.motor || {};
                document.getElementById('fly-lc4').innerText = (s.LC4 || 0).toFixed(3);
                document.getElementById('fly-lplc2').innerText = (s.LPLC2 || 0).toFixed(3);
                const esc = document.getElementById('fly-esc');
                esc.innerText = m.escape ? 'SÍ ⚡' : 'no';
                esc.style.color = m.escape ? '#ff4d6d' : '';
                document.getElementById('fly-mode').innerText = m.mode || '--';
                document.getElementById('fly-l').innerText = (m.left || 0).toFixed(2);
                document.getElementById('fly-r').innerText = (m.right || 0).toFixed(2);
                dibujarRetina(d.eye);
            }).catch(() => {});
        }, 700);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    """Centro de Mando NEXUS (web unificada)."""
    try:
        _p = Path(__file__).parent / "vector_nexus.html"
        _html = _p.read_text(encoding="utf-8")
        return HTMLResponse(content=_html.replace("{{DASHBOARD_TOKEN}}", DASHBOARD_TOKEN))
    except Exception as _e:
        # si falta el fichero, seguimos sirviendo el panel clasico
        return HTMLResponse(content=HTML_DASHBOARD.replace("{{DASHBOARD_TOKEN}}", DASHBOARD_TOKEN))

@app.get("/nexus", response_class=HTMLResponse)
def nexus():
    """Alias explicito del Centro de Mando unificado."""
    return index()

@app.get("/clasico", response_class=HTMLResponse)
def clasico():
    """Panel clasico (v13.5 anterior)."""
    return HTMLResponse(content=HTML_DASHBOARD.replace("{{DASHBOARD_TOKEN}}", DASHBOARD_TOKEN))

@app.get("/video_feed")
def video_feed():
    return StreamingResponse(_generador_mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/api/snapshot")
def get_snapshot():
    with _frame_lock:
        frame = _ultimo_frame_bytes
        t = _ultimo_frame_t
    # 204 si no hay frame o si esta obsoleto (camara caida)
    if frame and (time.time() - t) < FRAME_STALE_S:
        return Response(content=frame, media_type="image/jpeg")
    return Response(status_code=204)

@app.get("/api/telemetria")
def get_telemetria():
    return JSONResponse(content=telemetria_data)

@app.get("/api/health")
def get_health():
    """Health check + heartbeat del motor."""
    with _frame_lock:
        _ft = _ultimo_frame_t
    return JSONResponse(content={
        "status": "ok",
        "timestamp": time.time(),
        "version": "13.5",
        "dashboard_token_configured": bool(os.getenv("DASHBOARD_TOKEN", "").strip()),
        "motor_online": telemetria_data.get("online", False),
        "ultimo_heartbeat": telemetria_data.get("ultimo_heartbeat"),
        "camara_viva": frame_fresco(),
        "frame_edad_s": round(time.time() - _ft, 1) if _ft else None,
    })

@app.get("/api/fly")
def get_fly():
    """Proxy al visor FlyBrain (mosca). Devuelve estado del connectome en vivo."""
    try:
        r = requests.get("http://127.0.0.1:4712/data", timeout=1.2)
        return JSONResponse(content=r.json())
    except Exception as e:
        return JSONResponse(content={"connected": False, "error": str(e)})

@app.get("/api/talk")
def get_talk():
    """Estado del protocolo de chirps (Flyctor): emisor + receptor."""
    try:
        import vector_talk_service as _vts
        svc = _vts.get_service()
        if svc is None:
            return JSONResponse(content={"enabled": False, "activo": False,
                                         "historial": [], "vocabulario": []})
        return JSONResponse(content=svc.estado())
    except Exception as e:
        return JSONResponse(content={"enabled": False, "error": str(e),
                                     "historial": [], "vocabulario": []})

@app.post("/api/talk")
async def post_talk(req: Request, _: bool = Depends(verify_token)):
    """Emite una senal del vocabulario por el altavoz de Vector."""
    data = await req.json()
    nombre = str(data.get("signal", "")).strip()
    web_command_queue.put(("web_talk", nombre))
    return {"status": "ok", "signal": nombre}

@app.get("/api/chat_log")
def get_chat_log():
    with _chat_lock:
        return JSONResponse(content=list(registro_chat))

@app.post("/api/cam_mode")
async def post_cam_mode(req: Request, _: bool = Depends(verify_token)):
    global _modo_vision
    data = await req.json()
    _modo_vision = data.get("modo", "brillante")
    telemetria_data["modo_camara"] = _modo_vision
    return {"modo": _modo_vision}

@app.post("/api/control")
async def post_control(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    web_command_queue.put(("web_control", data.get("accion")))
    return {"status": "ok"}

@app.post("/api/accion")
async def post_accion(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    web_command_queue.put(("web_accion", data.get("accion")))
    return {"status": "ok"}

@app.post("/api/color")
async def post_color(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    web_command_queue.put(("web_color", data.get("color")))
    return {"status": "ok"}

@app.post("/api/decir")
async def post_decir(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    texto = data.get("texto", "").strip()
    if texto:
        registrar_mensaje_humano(texto, "Web")
        web_command_queue.put(("web_decir", texto))
    return {"status": "ok"}

@app.post("/api/intercom")
async def post_intercom(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    texto = data.get("texto", "").strip()
    if texto:
        registrar_mensaje_humano(texto, "Intercom Móvil")
        web_command_queue.put(("web_intercom", texto))
    return {"status": "ok"}

@app.post("/api/vault")
async def post_vault(req: Request, _: bool = Depends(verify_token)):
    data = await req.json()
    # aceptar ambos nombres: 'query' (panel clasico) y 'consulta' (NEXUS)
    q = (data.get("query") or data.get("consulta") or "").strip()
    if not q:
        return {"resultado": "Escribe una pregunta para buscar en el Vault."}
    try:
        from vector_rag import obsidian_rag
        res = obsidian_rag.buscar(q, top_k=1)
        if res:
            r = res[0]
            msg = f"En '{r['titulo']}': {r['resumen'][:160]}..."
            return {"resultado": msg}
    except Exception as e:
        return {"resultado": f"Error en Vault: {e}"}
    return {"resultado": "Sin notas relevantes."}

def iniciar_dashboard(host: str = "0.0.0.0", port: int = 8000) -> threading.Thread:
    def _run():
        uvicorn.run(app, host=host, port=port, log_level="warning")
    t = threading.Thread(target=_run, daemon=True, name="WebDashboard")
    t.start()
    print(f"[+] Web Command Center activo en http://127.0.0.1:{port} y http://192.168.1.51:{port}")
    return t

if __name__ == "__main__":
    t = iniciar_dashboard()
    t.join()
