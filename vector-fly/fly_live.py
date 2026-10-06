#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR-FLY :: VISOR DE NEURONAS EN VIVO (web)
# ==============================================
# Sirve en http://<ip>:4712 un panel que muestra, en tiempo real,
# la actividad de las 166.700 neuronas del connectome MaleCNS v1.0:
#   - banda de densidad de disparo (histograma de 128 bins)
#   - poblaciones visuales (LPLC2/LC4/LPLC1/LC10a) y descendentes
#   - grupos motores y ruedas
#   - estimulo interactivo (acercar/alejar amenaza) y demo automatica
# ============================================================
from __future__ import annotations

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))
from fly_client import FlyClient  # noqa: E402

HOST = os.getenv("FLY_HOST", "127.0.0.1")
PORT = int(os.getenv("FLY_PORT", "4711"))
WEB_PORT = int(os.getenv("FLY_WEB_PORT", "4712"))

_LOCK = threading.Lock()
_STATE = {"client": None, "demo": False, "demo_t": 0.0}
_CAM = {"on": False, "thread": None, "prev": None, "jpeg": None, "err": ""}
CAM_WIDTH = 160
# La webcam del PC es un respaldo OPCIONAL (este PC no tiene): por defecto desactivada.
USE_WEBCAM_FALLBACK = os.getenv("FLY_WEBCAM", "0").strip().lower() in ("1", "true", "si", "yes")
SUPERVISOR_BACKOFF_S = 2.0


def _vector_frame(timeout: float = 5.0):
    """Un frame JPEG de la camara de Vector (via dashboard). None si falla."""
    url = os.getenv("VECTOR_SNAPSHOT_URL", "http://127.0.0.1:8000/api/snapshot")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vector-fly"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if data and len(data) > 512:
            return data
        _CAM["err"] = "snapshot vacio del dashboard"
    except Exception as e:
        _CAM["err"] = "camara Vector: " + str(e)[:70]
    return None


def _camera_loop_once() -> None:
    """Captura webcam -> panoramica 1-D -> fotorreceptores de la mosca."""
    try:
        import cv2
        import numpy as np
    except Exception as e:
        _CAM["err"] = f"cv2 no disponible: {e}"
        _CAM["on"] = False
        return
    # 1) preferir la camara de Vector (dashboard /api/snapshot)
    fallos = 0
    while _CAM["on"] and (fallos < 3 or not USE_WEBCAM_FALLBACK):
        if fallos >= 3:
            # Sin webcam de respaldo: reintentar la camara de Vector con espera,
            # en vez de rendirse (el nucleo puede tardar en arrancar).
            time.sleep(3.0)
            fallos = 0
        jpg = _vector_frame()
        if jpg is None:
            fallos += 1
            time.sleep(0.4)
            continue
        fallos = 0
        frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            fallos += 1
            continue
        small = cv2.resize(frame, (CAM_WIDTH, 48), interpolation=cv2.INTER_AREA)
        band = (small.astype(np.float32) / 255.0).mean(axis=0)
        _CAM["band"] = band.tolist()
        _CAM["jpeg"] = jpg
        _CAM["src"] = "vector"
        c = get_client()
        if c is not None:
            try:
                c.camera(band.tolist())
            except Exception:
                pass
        time.sleep(0.2)
    if not _CAM["on"] or not USE_WEBCAM_FALLBACK:
        return
    _CAM["err"] = "camara de Vector no disponible, pruebo webcam del PC"
    cap = None
    for _idx in (0, 1, 2):
        for _backend in (cv2.CAP_DSHOW, cv2.CAP_ANY):
            try:
                _c = cv2.VideoCapture(_idx, _backend)
            except Exception:
                _c = None
            if _c is not None and _c.isOpened():
                cap = _c
                break
            if _c is not None:
                _c.release()
        if cap is not None:
            break
    if cap is None:
        # No matar el puente: _camera_supervisor reintenta la camara de Vector.
        _CAM["err"] = "no hay webcam en el PC (usa la camara de Vector)"
        time.sleep(3.0)
        return
    while _CAM["on"]:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (CAM_WIDTH, 48), interpolation=cv2.INTER_AREA)
        band = small.mean(axis=0) / 255.0
        _CAM["band"] = band.tolist()
        c = get_client()
        if c is not None:
            try:
                c.camera(band.tolist())
            except Exception:
                pass
        ok2, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        if ok2:
            _CAM["jpeg"] = buf.tobytes()
        time.sleep(0.05)
    cap.release()


def _supervised(name: str, fn, flag: dict) -> None:
    """Ejecuta fn() en bucle mientras flag['on']; si lanza o termina, la relanza.

    Un hilo del puente que muere deja al cerebro-mosca ciego sin avisar: se
    registra el error en flag['err'] y se reintenta con espera.
    """
    while flag.get("on"):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - el supervisor no debe morir nunca
            flag["err"] = f"{name} reiniciado: {type(e).__name__}: {str(e)[:80]}"
            print("[fly_live]", flag["err"], flush=True)
        if flag.get("on"):
            time.sleep(SUPERVISOR_BACKOFF_S)


def _camera_loop() -> None:
    _supervised("camara", _camera_loop_once, _CAM)


# ---------------- Vision semantica ("Astra ve, la mosca decide") ----------------
SCENE_PROVIDER = os.getenv("SCENE_PROVIDER", "auto").strip().lower()
SCENE_EVERY = float(os.getenv("SCENE_EVERY", "4.0"))

# Local llama.cpp server (Qwen2.5-VL-3B + mmproj) - PRIMARIO
LLAMACPP_BASE = os.getenv("LLAMACPP_BASE", "http://127.0.0.1:8081/v1").rstrip("/")
LLAMACPP_MODEL = os.getenv("LLAMACPP_MODEL", "qwen2.5-vl-3b")

# Ollama (fallback) - actualmente bloqueado por CDN
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
if OLLAMA_BASE.endswith("/v1"):
    OLLAMA_BASE = OLLAMA_BASE[:-3]
OLLAMA_MODEL_VISION = os.getenv("OLLAMA_MODEL_VISION", "qwen2.5vl:7b")

# OpenRouter gratuito (fallback final)
OPENROUTER_MODEL_VISION = os.getenv("OPENROUTER_MODEL_VISION",
                                    "inclusionai/ling-3.0-flash-vl:free")
_SCENE = {"on": False, "thread": None, "last": {}, "raw": "", "err": "",
          "provider": "", "t": 0.0, "count": 0}


_VISION_PROMPT = (
    "You are the eyes of a small robot driven by a fruit-fly brain. "
    "List the physical obstacles or living things visible in the image "
    "(furniture, walls, doors, cables, pets, people). Ignore floor and ceiling. "
    "Answer ONLY with compact JSON, no markdown:\n"
    '{"objects":[{"name":"cat","side":-1.0,"near":0.8}]}\n'
    "side: -1.0 far left, 0.0 straight ahead, 1.0 far right. "
    "near: 0.0 far away, 1.0 very close. Maximum 4 objects, most important first."
)


def _openrouter_key() -> str:
    if os.getenv("OPENROUTER_API_KEY"):
        return os.getenv("OPENROUTER_API_KEY", "")
    p = os.getenv("OPENCODE_AUTH", str(Path.home() / ".local/share/opencode/auth.json"))
    try:
        j = json.loads(Path(p).read_text("utf-8"))
        return str((j.get("openrouter") or {}).get("key", "") or "")
    except Exception:
        return ""


def _post_json(url: str, body: dict, headers: dict, timeout: float = 60.0):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _num(v, default: float) -> float:
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _parse_scene(text: str, max_objects: int = 4):
    """Extrae objetos {name, side, near} de la salida de la VL.

    Robusto a lo que hacen los modelos pequenos: vallas ```json, texto antes/despues,
    JSON TRUNCADO por max_tokens (se recuperan los objetos completos que quepan),
    numeros como cadenas y duplicados.  Antes un JSON cortado caia a una regex que
    solia devolver un unico objeto.
    """
    text = re.sub(r"(?<=[:\s\[,])(-?)\.(\d)", r"\g<1>0.\2", text or "")  # '.5' -> '0.5'
    objs: list = []
    seen: set = set()

    def add(name, side, near) -> None:
        name = str(name or "").strip()[:32]
        key = name.lower()
        if len(name) < 2 or key in seen:
            return
        seen.add(key)
        objs.append({"name": name,
                     "side": _clamp(_num(side, 0.0), -1.0, 1.0),
                     "near": _clamp(_num(near, 0.3), 0.0, 1.0)})

    # 1) JSON completo
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            for o in (j.get("objects") or []) if isinstance(j, dict) else []:
                if isinstance(o, dict):
                    add(o.get("name"), o.get("side", 0.0), o.get("near", 0.3))
        except (ValueError, AttributeError):
            pass
    # 2) objetos planos {...} sueltos (JSON truncado o con basura alrededor)
    if not objs:
        for mm in re.finditer(r"\{[^{}]*\}", text):
            try:
                o = json.loads(mm.group(0))
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("name"):
                add(o.get("name"), o.get("side", 0.0), o.get("near", 0.3))
    # 3) ultimo recurso: pares name/side/near en texto libre
    if not objs:
        for mm in re.finditer(
                r'name"?\s*[:=]\s*"?([\w \-]{2,32})"?[^\d\-]*(-?\d*\.?\d+)[^\d\-]+(\d*\.?\d+)', text):
            add(mm.group(1), mm.group(2), mm.group(3))
    return objs[:max_objects]


def _describe(jpg: bytes):
    b64 = base64.b64encode(jpg).decode("ascii")
    prov = SCENE_PROVIDER
    # 1) llama.cpp local (PRIORITARIO: privado, sin red externa)
    if prov in ("auto", "llamacpp", "llama"):
        try:
            j = _post_json(f"{LLAMACPP_BASE}/chat/completions",
                           {"model": LLAMACPP_MODEL, "temperature": 0, "max_tokens": 400,
                            "messages": [{"role": "user", "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                                {"type": "text", "text": _VISION_PROMPT}
                            ]}]}, {}, timeout=90.0)
            msg = (j.get("choices") or [{}])[0].get("message") or {}
            txt = msg.get("content") or msg.get("reasoning") or ""
            return (_parse_scene(txt), txt, "llamacpp:" + LLAMACPP_MODEL)
        except Exception as e:
            if prov in ("llamacpp", "llama"):
                return [], "", "llamacpp error: " + str(e)[:80]
    # 2) Ollama (fallback - actualmente bloqueado por CDN)
    if prov in ("auto", "ollama"):
        try:
            j = _post_json(f"{OLLAMA_BASE}/api/generate",
                           {"model": OLLAMA_MODEL_VISION, "prompt": _VISION_PROMPT,
                            "images": [b64], "stream": False,
                            "options": {"temperature": 0}}, {}, timeout=90.0)
            return (_parse_scene(j.get("response", "")), j.get("response", ""),
                    "ollama:" + OLLAMA_MODEL_VISION)
        except Exception as e:
            if prov == "ollama":
                return [], "", "ollama error: " + str(e)[:80]
    # 3) OpenRouter gratuito (fallback final)
    key = _openrouter_key()
    if not key:
        return [], "", "sin clave OpenRouter"
    try:
        j = _post_json("https://openrouter.ai/api/v1/chat/completions",
                       {"model": OPENROUTER_MODEL_VISION, "temperature": 0, "max_tokens": 900,
                        "reasoning": {"enabled": False},
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": _VISION_PROMPT}
                        ]}]}, {"Authorization": f"Bearer {key}"}, timeout=90.0)
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        txt = msg.get("content") or msg.get("reasoning") or ""
        return _parse_scene(txt), txt, "openrouter:" + OPENROUTER_MODEL_VISION
    except Exception as e:
        return [], "", "openrouter error: " + str(e)[:80]


def _scene_loop_once() -> None:
    """Cada SCENE_EVERY s mira la camara, la describe con la VL y la manda al cerebro."""
    while _SCENE["on"]:
        jpg = _CAM.get("jpeg")
        if not jpg:
            time.sleep(0.5)
            continue
        objs, raw, prov = _describe(jpg)
        _SCENE["last"] = {"objects": objs}
        _SCENE["raw"] = raw[:600]
        _SCENE["provider"] = prov
        _SCENE["t"] = time.time()
        _SCENE["count"] += 1
        if objs:
            c = get_client()
            if c is not None:
                try:
                    c.scene(objs)
                except Exception:
                    pass
        time.sleep(max(1.0, SCENE_EVERY))


def _scene_loop() -> None:
    _supervised("vision", _scene_loop_once, _SCENE)


def get_client() -> FlyClient:
    """Cliente persistente: reutiliza si llega telemetria; reconecta con enfriamiento."""
    with _LOCK:
        c = _STATE["client"]
        now = time.time()
        if c is not None and c.alive:
            return c
        if c is None:
            c = FlyClient(HOST, PORT, timeout=1.0)
            try:
                c.connect()
            except Exception:
                pass
            _STATE["client"] = c
            return c
        if now - float(_STATE.get("last_try", 0.0)) >= 2.0:
            _STATE["last_try"] = now
            try:
                if not c.connected:
                    c.connect()
            except Exception:
                pass
        return c


def current_frame() -> dict:
    scene = {"on": _SCENE["on"], "provider": _SCENE["provider"], "err": _SCENE["err"],
             "count": _SCENE["count"], "last": _SCENE["last"], "t": _SCENE["t"]}
    c = get_client()
    if c is None:
        return {"connected": False, "snapshot": {}, "motor": {}, "hist": [0] * 128,
                "spikes": [], "bmap": [], "neurons": 0, "fps": 0, "scene": scene}
    snap = dict(c.snapshot)
    motor = dict(c.motor)
    hist = list(c.hist)
    return {"connected": True, "snapshot": snap, "motor": motor,
            "hist": hist, "eye": list(getattr(c, "eye", [])),
            "bmap": list(getattr(c, "bmap", [])),
            "spikes": list(getattr(c, "spikes", [])),
            "neurons": int((getattr(c, "brain", {}) or {}).get("neurons", 0) or 0),
            "fps": c.fps,
            "demo": _STATE["demo"], "camera": _CAM["on"],
            "cam_err": _CAM.get("err", ""), "scene": scene}


def _demo_loop() -> None:
    """Simula una amenaza que se acerca y luego se aleja (ciclo)."""
    while _STATE["demo"]:
        for i in range(90):
            if not _STATE["demo"]:
                return
            mm = max(40.0, 950.0 - i * 11.0)
            _STATE["demo_t"] = mm
            _send_senses(-32.0, mm)
            time.sleep(0.08)
        for i in range(50):
            if not _STATE["demo"]:
                return
            mm = 40.0 + i * 18.0
            _send_senses(-32.0, mm)
            time.sleep(0.08)
        time.sleep(0.6)


def _send_senses(bearing: float, dist_mm: float) -> None:
    c = get_client()
    if c is None:
        return
    import math
    dx = max(-110.0, min(110.0, math.radians(bearing) * 62.0))
    dg = max(6.0, dist_mm / 15.0)
    size = max(2.0, min(130.0, 520.0 / dg))
    threat = max(0.0, min(1.0, (70.0 - dg) / 60.0))
    c.see(opp=(dx, size), threat=threat)


_VECTOR_LOG = Path(r"C:\Users\Jose Luis\vector_run.log")


def vector_speech():
    """Ultimas frases y pensamientos de Vector (parsea su log)."""
    import re
    out = {"speech": [], "pens": [], "status": {}, "updated": time.time()}
    try:
        with open(_VECTOR_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-800:]
    except Exception as e:
        out["error"] = str(e)
        return out
    for ln in lines:
        m = re.search(r"Vector \[([^\]]*)\]:\s*(.+)", ln)
        if m:
            out["speech"].append({"mood": m.group(1).strip(), "text": m.group(2).strip()})
        m2 = re.search(r"pens:\s*(.+)", ln)
        if m2:
            out["pens"].append(m2.group(1).strip())
        m3 = re.search(r"Cerebro ([^ /]+)/([^ ]+) E=(\d+) C=(\d+) A=(\d+) B=(\d+) S=(\d+) M=(\d+)", ln)
        if m3:
            out["status"] = {"animo": m3.group(1), "modo": m3.group(2), "E": m3.group(3),
                             "C": m3.group(4), "A": m3.group(5), "B": m3.group(6),
                             "S": m3.group(7), "M": m3.group(8)}
        m4 = re.search(r"([\d.]+)V", ln)
        if m4 and "ater" in ln:
            out["status"]["voltios"] = m4.group(1)
        m5 = re.search(r"IA via (\w+)", ln)
        if m5:
            out["status"]["proveedor"] = m5.group(1)
    out["speech"] = out["speech"][-40:]
    out["pens"] = out["pens"][-25:]
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel: str, ctype: str = "text/html; charset=utf-8"):
        try:
            body = (_DIR / rel).read_bytes()
        except Exception:
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._static("fly_live.html")
        elif path == "/brain.html":
            self._static("web/brain.html")
        elif path == "/neurons.html":
            self._static("web/neurons.html")
        elif path == "/data":
            self._json(current_frame())
        elif path == "/brainmap":
            c = get_client()
            meta = dict(getattr(c, "brain_map", {}) or {})
            if not meta:
                try:
                    c.mapmeta()
                except Exception:
                    pass
            self._json(meta)
        elif path == "/camera.jpg":
            jpg = _CAM.get("jpeg")
            if not jpg:
                self._json({"error": "sin imagen"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpg)
        elif path == "/speech":
            self._json(vector_speech())
        elif path == "/scene":
            self._json({"on": _SCENE["on"], "provider": _SCENE["provider"],
                        "err": _SCENE["err"], "count": _SCENE["count"],
                        "last": _SCENE["last"], "raw": _SCENE["raw"]})
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(current_frame(), ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.1)
            except Exception:
                return
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        if path == "/stim":
            _send_senses(float(data.get("bearing", -30.0)), float(data.get("dist", 300.0)))
            self._json({"ok": True})
        elif path == "/demo":
            _STATE["demo"] = bool(data.get("on", True))
            if _STATE["demo"]:
                threading.Thread(target=_demo_loop, daemon=True).start()
            self._json({"ok": True, "demo": _STATE["demo"]})
        elif path == "/reset":
            c = get_client()
            if c:
                c.reset()
            self._json({"ok": True})
        elif path == "/camera":
            want = bool(data.get("on", True))
            _CAM["on"] = want
            _CAM["err"] = ""
            if want and (_CAM["thread"] is None or not _CAM["thread"].is_alive()):
                _CAM["thread"] = threading.Thread(target=_camera_loop, daemon=True)
                _CAM["thread"].start()
            # la vision semantica va ligada a la camara
            _SCENE["on"] = want
            if want and (_SCENE["thread"] is None or not _SCENE["thread"].is_alive()):
                _SCENE["thread"] = threading.Thread(target=_scene_loop, daemon=True)
                _SCENE["thread"].start()
            self._json({"ok": True, "camera": _CAM["on"], "err": _CAM.get("err", ""),
                        "scene": _SCENE["on"]})
        elif path == "/scene":
            want = bool(data.get("on", True))
            _SCENE["on"] = want
            if want and (_SCENE["thread"] is None or not _SCENE["thread"].is_alive()):
                _SCENE["thread"] = threading.Thread(target=_scene_loop, daemon=True)
                _SCENE["thread"].start()
            self._json({"ok": True, "scene": _SCENE["on"],
                        "provider": _SCENE["provider"]})
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    print("=" * 60)
    print(" VECTOR-FLY :: VISOR DE NEURONAS EN VIVO")
    print("=" * 60)
    c = get_client()
    print("  sidecar:", "ONLINE" if c else "OFFLINE (arranca fly_server.py)")
    ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    print(f"  abre en:  http://{ip}:{WEB_PORT}/  (o http://127.0.0.1:{WEB_PORT}/)")
    print("=" * 60)

    # Auto-activar camara + vision semantica: sin esto habria que pulsar el
    # boton en la web cada vez, y el sistema debe funcionar 24h sin manos.
    try:
        _CAM["on"] = True
        _CAM["err"] = ""
        _CAM["thread"] = threading.Thread(target=_camera_loop, daemon=True)
        _CAM["thread"].start()
        _SCENE["on"] = True
        _SCENE["thread"] = threading.Thread(target=_scene_loop, daemon=True)
        _SCENE["thread"].start()
        print("  camara + vision semantica: AUTO-ACTIVADAS")
    except Exception as _e_auto:
        print("  aviso auto-camara:", _e_auto)

    srv = None
    while srv is None:
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
        except OSError as e:  # puerto ocupado por una instancia que esta muriendo
            print(f"  puerto {WEB_PORT} ocupado ({e}); reintento en 3 s", flush=True)
            time.sleep(3.0)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
