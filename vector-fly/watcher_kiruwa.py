#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watcher de @kiruwaaaaaa (Flyctor).

Vigila el perfil con el motor Vane (sin coste de cuota), y cuando aparece un
post nuevo lo enriquece con la API de fxtwitter (texto, likes, videos) y lo
escribe en kiruwa_posts.log.

Uso:
    python watcher_kiruwa.py --once          # una pasada (baseline)
    python watcher_kiruwa.py                 # bucle cada 15 min
    python watcher_kiruwa.py --cada=300      # bucle cada 5 min
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "kiruwa_seen.json"
LOG = HERE / "kiruwa_posts.log"
CLI = r"Z:\apex-powerscaling-engine\tools\vane\vane_cli.cjs"
USER = "kiruwaaaaaa"
PROFILE = f"https://x.com/{USER}"
UA = {"User-Agent": "Mozilla/5.0"}


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_seen() -> set:
    if STATE.exists():
        try:
            return set(json.loads(STATE.read_text("utf-8")))
        except Exception:
            pass
    return set()


def save_seen(seen: set) -> None:
    try:
        STATE.write_text(json.dumps(sorted(seen)[-800:]), "utf-8")
    except Exception:
        pass


def fetch_ids() -> list:
    try:
        r = subprocess.run(["node", CLI, PROFILE], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        log(f"ERROR navegando el perfil: {e}")
        return []
    ids: list = []
    for m in re.finditer(r"status(?:es)?/(\d+)", out):
        i = m.group(1)
        if i not in ids:
            ids.append(i)
    return ids


def fetch_tweet(tid: str) -> dict:
    url = f"https://api.fxtwitter.com/{USER}/status/{tid}"
    base = {"id": tid, "url": f"https://x.com/{USER}/status/{tid}",
            "texto": "", "likes": 0, "vistas": 0, "videos": 0, "fecha": ""}
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        t = j.get("tweet", {}) or {}
        media = t.get("media", {}) or {}
        base.update({
            "fecha": t.get("created_at", ""),
            "likes": t.get("likes", 0),
            "retweets": t.get("retweets", 0),
            "vistas": t.get("views", 0),
            "texto": (t.get("text") or "").strip(),
            "videos": len(media.get("videos", []) or []),
        })
    except Exception as e:
        base["error"] = str(e)
    return base


def main() -> None:
    una_vez = "--once" in sys.argv
    intervalo = 900
    for a in sys.argv:
        if a.startswith("--cada="):
            intervalo = max(60, int(a.split("=", 1)[1]))
    log(f"=== watcher iniciado (cada {intervalo}s) -> {PROFILE} ===")
    while True:
        seen = load_seen()
        ids = fetch_ids()
        if not ids:
            log("aviso: no se pudo leer el perfil (posible bloqueo temporal)")
        nuevos = [i for i in ids if i not in seen]
        for tid in reversed(nuevos):          # orden cronologico
            info = fetch_tweet(tid)
            txt = " ".join(info.get("texto", "").split())
            log(f"NUEVO POST {info['url']} | {info.get('fecha', '')} | "
                f"likes={info.get('likes', 0)} vistas={info.get('vistas', 0)} "
                f"videos={info.get('videos', 0)}")
            if txt:
                log(f"    >> {txt[:700]}")
            if info.get("error"):
                log(f"    (aviso fxtwitter: {info['error']})")
        if ids:
            seen |= set(ids)
            save_seen(seen)
            if nuevos:
                log(f"total nuevos: {len(nuevos)}")
        if una_vez:
            log("modo --once: fin")
            return
        time.sleep(intervalo)


if __name__ == "__main__":
    main()
