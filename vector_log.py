# ============================================================
# VECTOR ULTRA 13.5 — LOGGING ESTRUCTURADO CON ROTACIÓN DIARIA
# ============================================================
import logging
import logging.handlers
import os
import sys
from pathlib import Path

# Directorio de logs
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Forzar UTF-8 en stdout/stderr: al redirigir la salida a un archivo, Windows
# usa cp1252 y los emojis del texto de la IA rompen el logging (tracebacks
# "--- Logging error ---" con "Message:/Arguments:").
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Formato unificado
FMT = "%(asctime)s | %(levelname)-7s | %(threadName)-18s | %(name)s | %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Handler de archivo rotativo diario (conserva 30 días)
file_handler = logging.handlers.TimedRotatingFileHandler(
    LOG_DIR / "vector.log",
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
    utc=False,
)
file_handler.setFormatter(logging.Formatter(FMT, datefmt=DATE_FMT))
file_handler.setLevel(logging.DEBUG)

# Handler de consola (info+)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(FMT, datefmt=DATE_FMT))
console_handler.setLevel(logging.INFO)

# Logger raíz "vector"
_root_logger = logging.getLogger("vector")
_root_logger.setLevel(logging.DEBUG)
_root_logger.addHandler(file_handler)
_root_logger.addHandler(console_handler)
_root_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger hijo de 'vector' con el nombre dado."""
    return _root_logger.getChild(name)


def configure_file_level(level: int = logging.DEBUG) -> None:
    """Cambia el nivel de detalle en el archivo de log."""
    file_handler.setLevel(level)


def configure_console_level(level: int = logging.INFO) -> None:
    """Cambia el nivel de detalle en consola."""
    console_handler.setLevel(level)


# Alias para uso rápido
log = get_logger("core")