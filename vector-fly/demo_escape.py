#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR-FLY :: DEMO DE ESCAPE (cerebro de mosca -> robot)
# ============================================================
# Simula una arena con un robot tipo Vector controlado en tiempo
# real por el connectome MaleCNS v1.0 (166.700 neuronas).
#
#   * La amenaza se acerca -> crece el angulo visual (looming)
#   * LC4 / LPLC2 disparan -> se activa el circuito de escape
#   * DNp01 (giant fiber) ordena huida -> las ruedas giran
#
# Genera: demo_escape.mp4 (o .gif) + demo_escape_contact.png
# ============================================================
from __future__ import annotations

import os
import json
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Circle, Polygon

from fly_core import FlyCore

# ------------------------------------------------------------
# Parametros de la simulacion
# ------------------------------------------------------------
FPS = 25
DT_FRAME = 2 * FlyCore.DT          # 40 ms por fotograma
FRAMES = 260                       # ~10.4 s
SEED = 64
OUT_DIR = Path(__file__).parent
ARENA = 200.0

COL_ROBOT = "#00ff9d"
COL_THREAT = "#ff3b5c"
COL_LC4 = "#ff8a3d"
COL_LOOM = "#ffd23d"
COL_ESC = "#ff3b5c"
COL_WHEEL = "#4dd2ff"
BG = "#05070d"
FG = "#c9e6ff"


def narrate_lines() -> dict:
    """Pide a Ollama (local) 3 frases cortas para narrar la escena.

    Si Ollama no esta disponible, usa plantillas.
    """
    fallback = {
        "chase": "...detecto algo en movimiento. Me acerco con curiosidad.",
        "escape": "¡PELIGRO! Algo se lanza hacia mi. ¡Huyo!",
        "safe": "La amenaza ha pasado. Respito, estoy a salvo.",
    }
    prompt = (
        "Eres Vector, un pequeno robot con un cerebro de mosca de la fruta de 166700 neuronas. "
        "Describe en PRIMERA PERSONA y muy breve (maximo 12 palabras) que sientes en 3 momentos: "
        "1) ves algo moverse a lo lejos, 2) algo se lanza contra ti y huye por reflejo, 3) ya estas a salvo. "
        'Responde SOLO con JSON valido sin markdown: {"chase": "...", "escape": "...", "safe": "..."}'
    )
    try:
        body = json.dumps({
            "model": "qwen3.5:9b",
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.8, "num_predict": 120},
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
        txt = (data.get("response") or "").strip()
        s = txt[txt.find("{"): txt.rfind("}") + 1]
        d = json.loads(s)
        out = {k: str(d.get(k, fallback[k]))[:90] for k in fallback}
        print("[narrate] Ollama:", out)
        return out
    except Exception as e:
        print(f"[narrate] Ollama no disponible ({str(e)[:70]}); usando plantillas")
        return fallback


# ------------------------------------------------------------
# 1. SIMULACION (independiente del dibujado)
# ------------------------------------------------------------
def simulate() -> dict:
    core = FlyCore(seed=SEED, verbose=True)

    # --- neuronas vigiladas para el raster ---------------------
    watched: list[tuple[int, str, str]] = []

    def add(idx_arr, label, color, limit=None):
        arr = np.asarray(idx_arr, dtype=np.int64)
        if limit:
            arr = arr[:limit]
        for g in arr:
            watched.append((int(g), label, color))

    add(core.brain.cells(["LPLC2"]), "LPLC2", COL_LOOM, limit=70)
    add(core.brain.cells(["LC4"]), "LC4", COL_LC4, limit=40)
    add(core.brain.cells(["LC10a"]), "LC10a", "#7dff7d", limit=30)
    for name in ("DNp01", "DNp02", "DNg01"):
        add(core.dn.get(name, []), name, COL_ESC, limit=6)
    add(core.brain.groups.get("escape_L", []), "escape", COL_ESC)
    add(core.brain.groups.get("escape_R", []), "escape", COL_ESC)
    add(core.brain.groups.get("forward_L", []), "forward", COL_ROBOT)
    add(core.brain.groups.get("forward_R", []), "forward", COL_ROBOT)
    add(core.brain.groups.get("steer_L", []), "steer", COL_WHEEL)
    add(core.brain.groups.get("steer_R", []), "steer", COL_WHEEL)

    gidx = np.array([w[0] for w in watched], dtype=np.int64)
    glabel = [w[1] for w in watched]
    gcolor = [w[2] for w in watched]
    row_of = {int(g): r for r, g in enumerate(gidx)}

    # --- mundo ------------------------------------------------
    rx, ry, rth = -40.0, -30.0, 0.0        # robot
    tx, ty = rx + 130.0, ry + 110.0        # amenaza delante-izquierda
    trail = deque(maxlen=400)
    threat_trail = deque(maxlen=400)

    snapped = {k: [] for k in ("LC4", "LPLC2", "escape", "chase", "wheelL", "wheelR")}
    raster_t: list[float] = []
    raster_r: list[int] = []
    frames_meta = []

    def wrap(a):
        return (a + np.pi) % (2 * np.pi) - np.pi

    for f in range(FRAMES):
        t = f * DT_FRAME

        # --- movimiento de la amenaza: se acerca y a los 3s embiste
        dx = rx - tx
        dy = ry - ty
        dist = float(np.hypot(dx, dy)) + 1e-6
        speed = 25.0 if t < 3.0 else (95.0 if t < 5.5 else 30.0)
        tx += dx / dist * speed * DT_FRAME
        ty += dy / dist * speed * DT_FRAME
        # si la amenaza llega a rozar, frena (no se solapa)
        if dist < 26:
            tx -= dx / dist * speed * DT_FRAME * 0.85
            ty -= dy / dist * speed * DT_FRAME * 0.85

        # --- lo que "ve" la mosca ---------------------------------
        ox, oy = tx - rx, ty - ry          # vector robot -> amenaza
        dist = float(np.hypot(ox, oy)) + 1e-6
        bearing = wrap(np.arctan2(oy, ox) - rth)
        # convencion de la mosca: dx<0 = amenaza a la IZQUIERDA
        dx_game = float(np.clip(-bearing * 62.0, -110, 110))
        size = float(np.clip(520.0 / dist, 2.0, 130.0))
        threat_in = float(np.clip((70.0 - dist) / 60.0, 0.0, 1.0))

        inject = core.visual_inject(opp=(dx_game, size), threat=threat_in)

        # --- 2 pasos de neurodinamica por fotograma ----------------
        for _ in range(2):
            snap = core.step(inject=inject)
            for rr in np.asarray(core.brain.fired if hasattr(core.brain, "fired") else []).ravel():
                row = row_of.get(int(rr))
                if row is not None:
                    raster_t.append(t)
                    raster_r.append(row)

        cmd = core.motor_command(snap)

        # --- dinamica del robot (traccion diferencial) -------------
        lin, turn = cmd["linear"], cmd["turn"]
        v = lin * 60.0
        w = turn * 1.9
        rth = wrap(rth + w * DT_FRAME)
        rx += np.cos(rth) * v * DT_FRAME
        ry += np.sin(rth) * v * DT_FRAME
        rx = float(np.clip(rx, -ARENA, ARENA))
        ry = float(np.clip(ry, -ARENA, ARENA))
        trail.append((rx, ry))
        threat_trail.append((tx, ty))

        snapped["LC4"].append(snap.get("LC4", 0.0))
        snapped["LPLC2"].append(snap.get("LPLC2", 0.0))
        snapped["escape"].append(snap.get("escape", 0.0))
        snapped["chase"].append(snap.get("LC10a", 0.0))
        snapped["wheelL"].append(cmd["left"])
        snapped["wheelR"].append(cmd["right"])
        frames_meta.append((rx, ry, rth, tx, ty, cmd, snap, dist, threat_in))

    return {
        "core": core,
        "watched": (gidx, glabel, gcolor),
        "raster_t": np.array(raster_t),
        "raster_r": np.array(raster_r),
        "snapped": snapped,
        "trail": list(trail),
        "threat_trail": list(threat_trail),
        "frames": frames_meta,
        "narration": narrate_lines(),
    }


# ------------------------------------------------------------
# 2. RENDER
# ------------------------------------------------------------
def render(sim: dict, out_stem: str = "demo_escape") -> list[Path]:
    core = sim["core"]
    gidx, glabel, gcolor = sim["watched"]
    snaps = sim["snapped"]
    T = np.arange(FRAMES) * DT_FRAME
    nrows = len(gidx)

    plt.rcParams.update({
        "figure.facecolor": BG, "axes.facecolor": BG,
        "savefig.facecolor": BG, "text.color": FG,
        "axes.labelcolor": FG, "xtick.color": FG, "ytick.color": FG,
        "axes.edgecolor": "#1d2a3a",
    })

    fig = plt.figure(figsize=(15.5, 8.6), dpi=110)
    gs = fig.add_gridspec(3, 2, width_ratios=[1.25, 1.0], height_ratios=[1, 1, 1],
                          hspace=0.42, wspace=0.22)

    ax_arena = fig.add_subplot(gs[:, 0])
    ax_motor = fig.add_subplot(gs[0, 1])
    ax_sense = fig.add_subplot(gs[1, 1])
    ax_rast = fig.add_subplot(gs[2, 1])

    # --- arena ---
    ax_arena.set_xlim(-ARENA, ARENA)
    ax_arena.set_ylim(-ARENA, ARENA)
    ax_arena.set_aspect("equal")
    ax_arena.set_title("ARENA  ·  Vector guiado por MaleCNS v1.0", color=FG, fontsize=12, pad=10)
    ax_arena.grid(True, color="#101a26", lw=0.6)
    ax_arena.set_xticks([]); ax_arena.set_yticks([])
    for s in ax_arena.spines.values():
        s.set_color("#16222f")

    robot_poly = Polygon([(0, 0)], closed=True, fc=COL_ROBOT, ec="#bafff0", lw=1.4, zorder=6)
    ax_arena.add_patch(robot_poly)
    robot_glow = Circle((0, 0), 16, fc=COL_ROBOT, alpha=0.16, zorder=5)
    ax_arena.add_patch(robot_glow)
    threat_pt = Circle((0, 0), 10, fc=COL_THREAT, ec="#ffd0d8", lw=1.2, alpha=0.95, zorder=6)
    ax_arena.add_patch(threat_pt)
    threat_ring = Circle((0, 0), 60, fc="none", ec=COL_THREAT, lw=0.9, ls="--", alpha=0.5, zorder=4)
    ax_arena.add_patch(threat_ring)
    trail_line, = ax_arena.plot([], [], color=COL_ROBOT, lw=1.3, alpha=0.55, zorder=3)
    threat_line, = ax_arena.plot([], [], color=COL_THREAT, lw=1.0, alpha=0.35, zorder=3)
    fov = Polygon([(0, 0)], closed=True, fc=COL_ROBOT, alpha=0.07, zorder=2)
    ax_arena.add_patch(fov)
    status_txt = ax_arena.text(-ARENA + 8, ARENA - 16, "", color=COL_ROBOT, fontsize=11,
                               family="monospace", zorder=8)
    info_txt = ax_arena.text(ARENA - 8, ARENA - 16, "", color=FG, fontsize=9,
                             family="monospace", ha="right", zorder=8)
    narr_txt = ax_arena.text(0, -ARENA + 22, "", color="#ffe9a8", fontsize=11.5,
                             style="italic", ha="center", zorder=9)

    # --- motores ---
    ax_motor.set_title("RUEDAS (decodificadas del connectome)", color=FG, fontsize=11)
    ax_motor.set_ylim(-1.05, 1.05)
    ax_motor.set_xlim(0, FRAMES)
    ax_motor.grid(True, color="#101a26", lw=0.6)
    wl_line, = ax_motor.plot([], [], color=COL_WHEEL, lw=1.8, label="izq")
    wr_line, = ax_motor.plot([], [], color="#ff7ad9", lw=1.8, label="der")
    esc_band = ax_motor.axhspan(0.85, 1.05, color=COL_ESC, alpha=0.0)
    ax_motor.legend(loc="upper right", fontsize=8, framealpha=0.2, labelcolor=FG)
    ax_motor.set_ylabel("velocidad", fontsize=9)

    # --- sentidos ---
    ax_sense.set_title("NEURONAS SENSORIALES", color=FG, fontsize=11)
    ax_sense.set_xlim(0, FRAMES)
    ax_sense.set_ylim(-0.05, max(3.2, max(snaps["LC4"] + snaps["LPLC2"]) * 1.15))
    ax_sense.grid(True, color="#101a26", lw=0.6)
    lc4_line, = ax_sense.plot([], [], color=COL_LC4, lw=1.7, label="LC4 (amenaza)")
    loom_line, = ax_sense.plot([], [], color=COL_LOOM, lw=1.4, alpha=0.9, label="LPLC2 (looming)")
    esc_line, = ax_sense.plot([], [], color=COL_ESC, lw=1.6, label="escape (DNp01)")
    ax_sense.legend(loc="upper left", fontsize=8, framealpha=0.2, labelcolor=FG)
    ax_sense.set_ylabel("actividad", fontsize=9)

    # --- raster ---
    ax_rast.set_title("RASTER  ·  neuronas del connectome", color=FG, fontsize=11)
    ax_rast.set_xlim(0, FRAMES)
    ax_rast.set_ylim(-1, nrows)
    ax_rast.grid(False)
    ax_rast.set_xticks(np.linspace(0, FRAMES, 6))
    ax_rast.set_xticklabels([f"{v*DT_FRAME:.0f}s" for v in np.linspace(0, FRAMES, 6)], fontsize=8)
    ax_rast.set_yticks([])
    # etiquetas de categorias
    seen = {}
    for r, lab in enumerate(glabel):
        seen.setdefault(lab, []).append(r)
    for lab, rows in seen.items():
        ax_rast.text(-4, float(np.mean(rows)), lab, fontsize=7, color=gcolor[rows[0]],
                     ha="right", va="center", family="monospace")
    raster_sc = ax_rast.scatter([], [], s=5, c=[], marker="s")

    fig.suptitle("VECTOR-FLY  ·  166.700 neuronas de mosca controlando un robot",
                 color="#ffffff", fontsize=15, y=0.985, fontweight="bold")
    fig.text(0.5, 0.945,
             "MaleCNS v1.0 (Janelia FlyEM)  ·  simulacion LIF a 50 Hz  ·  dt 20 ms  ·  escape = reflejo real del connectome",
             color="#7fa8c9", fontsize=9.5, ha="center")

    def update(f):
        rx, ry, rth, tx, ty, cmd, snap, dist, thr = sim["frames"][f]
        # robot
        size = 15
        nose = (rx + np.cos(rth) * size, ry + np.sin(rth) * size)
        l = (rx + np.cos(rth + 2.5) * size * 0.8, ry + np.sin(rth + 2.5) * size * 0.8)
        r = (rx + np.cos(rth - 2.5) * size * 0.8, ry + np.sin(rth - 2.5) * size * 0.8)
        robot_poly.set_xy([nose, l, (rx, ry), r])
        robot_glow.center = (rx, ry)
        threat_pt.center = (tx, ty)
        threat_ring.center = (tx, ty)
        threat_ring.set_radius(55 + 30 * (1 - min(dist, 120) / 120))
        # cono de vision
        half = 0.55
        fov.set_xy([(rx, ry),
                    (rx + np.cos(rth - half) * 120, ry + np.sin(rth - half) * 120),
                    (rx + np.cos(rth + half) * 120, ry + np.sin(rth + half) * 120)])
        fov.set_facecolor(COL_ESC if cmd["escape"] else COL_ROBOT)
        fov.set_alpha(0.16 if cmd["escape"] else 0.07)
        # colas
        tr = sim["trail"][:f + 1]
        trail_line.set_data([p[0] for p in tr], [p[1] for p in tr])
        tt = sim["threat_trail"][:f + 1]
        threat_line.set_data([p[0] for p in tt], [p[1] for p in tt])

        mode = cmd["mode"].upper()
        color = COL_ESC if cmd["escape"] else (COL_ROBOT if mode != "IDLE" else "#6b8299")
        status_txt.set_text(f"{mode:8s}  dist={dist:5.1f}")
        status_txt.set_color(color)
        info_txt.set_text(f"t={f*DT_FRAME:4.1f}s   disparos={snap.get('fire_count',0):4d}")
        if cmd["escape"]:
            narr_txt.set_text("Vector: " + sim["narration"]["escape"])
            narr_txt.set_color("#ffb3c0")
        elif dist < 120:
            narr_txt.set_text("Vector: " + sim["narration"]["chase"])
            narr_txt.set_color("#ffe9a8")
        else:
            narr_txt.set_text("Vector: " + sim["narration"]["safe"])
            narr_txt.set_color("#a8e9ff")

        # curvas
        xs = np.arange(f + 1)
        wl_line.set_data(xs, snaps["wheelL"][:f + 1])
        wr_line.set_data(xs, snaps["wheelR"][:f + 1])
        ax_motor.set_xlim(max(0, f - 140), max(140, f + 4))
        lc4_line.set_data(xs, snaps["LC4"][:f + 1])
        loom_line.set_data(xs, snaps["LPLC2"][:f + 1])
        esc_line.set_data(xs, snaps["escape"][:f + 1])
        ax_sense.set_xlim(max(0, f - 140), max(140, f + 4))
        esc_band.set_alpha(0.16 if cmd["escape"] else 0.0)

        # raster (ventana de 3 s)
        tnow = f * DT_FRAME
        keep = sim["raster_t"] > (tnow - 3.0 * (DT_FRAME / DT_FRAME))
        rr = sim["raster_r"][keep]
        tt2 = sim["raster_t"][keep]
        if len(rr):
            raster_sc.set_offsets(np.column_stack([tt2 / DT_FRAME, rr]))
            raster_sc.set_color([gcolor[i] for i in rr])
        else:
            raster_sc.set_offsets(np.empty((0, 2)))
        ax_rast.set_xlim(max(0, f - 75), max(75, f + 2))
        return (robot_poly, robot_glow, threat_pt, threat_ring, trail_line, threat_line,
                fov, status_txt, info_txt, narr_txt, wl_line, wr_line, lc4_line, loom_line,
                esc_line, raster_sc, esc_band)

    anim = FuncAnimation(fig, update, frames=FRAMES, interval=1000 / FPS, blit=False)

    outs: list[Path] = []

    # 1) MP4 si hay ffmpeg, si no GIF
    mp4 = OUT_DIR / f"{out_stem}.mp4"
    gif = OUT_DIR / f"{out_stem}.gif"
    try:
        import imageio_ffmpeg
        matplotlib.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
        writer = matplotlib.animation.FFMpegWriter(fps=FPS, bitrate=4500,
                                                   metadata={"title": "Vector-Fly escape"})
        anim.save(str(mp4), writer=writer)
        outs.append(mp4)
        print(f"[render] MP4 -> {mp4}")
    except Exception as e:
        print(f"[render] MP4 no disponible ({str(e)[:80]}); guardando GIF")
        anim.save(str(gif), writer=PillowWriter(fps=FPS))
        outs.append(gif)
        print(f"[render] GIF -> {gif}")

    # 2) hoja de contactos con los momentos clave
    try:
        contact = OUT_DIR / f"{out_stem}_contact.png"
        idxs = [0, int(FRAMES * 0.25), int(FRAMES * 0.38), int(FRAMES * 0.46),
                int(FRAMES * 0.6), FRAMES - 1]
        fig2, axes = plt.subplots(2, 3, figsize=(15, 8), dpi=110)
        fig2.patch.set_facecolor(BG)
        for ax, fi in zip(axes.ravel(), idxs):
            _draw_static_frame(ax, sim, fi)
        fig2.suptitle("VECTOR-FLY · momentos clave del reflejo de escape",
                      color="white", fontsize=14, y=0.98)
        fig2.tight_layout(rect=(0, 0, 1, 0.95))
        fig2.savefig(contact, facecolor=BG)
        plt.close(fig2)
        outs.append(contact)
        print(f"[render] PNG -> {contact}")
    except Exception as e:
        print(f"[render] contact sheet fallo: {e}")

    plt.close(fig)
    return outs


def _draw_static_frame(ax, sim, f):
    rx, ry, rth, tx, ty, cmd, snap, dist, thr = sim["frames"][f]
    ax.set_facecolor(BG)
    ax.set_xlim(-ARENA, ARENA)
    ax.set_ylim(-ARENA, ARENA)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    tr = sim["trail"][:f + 1]
    ax.plot([p[0] for p in tr], [p[1] for p in tr], color=COL_ROBOT, lw=1.0, alpha=0.5)
    tt = sim["threat_trail"][:f + 1]
    ax.plot([p[0] for p in tt], [p[1] for p in tt], color=COL_THREAT, lw=0.8, alpha=0.35)
    size = 15
    nose = (rx + np.cos(rth) * size, ry + np.sin(rth) * size)
    l = (rx + np.cos(rth + 2.5) * size * 0.8, ry + np.sin(rth + 2.5) * size * 0.8)
    r = (rx + np.cos(rth - 2.5) * size * 0.8, ry + np.sin(rth - 2.5) * size * 0.8)
    ax.add_patch(Polygon([nose, l, (rx, ry), r], closed=True,
                         fc=COL_ROBOT, ec="#bafff0", lw=1.2, zorder=6))
    ax.add_patch(Circle((tx, ty), 10, fc=COL_THREAT, ec="#ffd0d8", lw=1.0, zorder=6))
    ax.add_patch(Circle((rx, ry), 16, fc=COL_ROBOT, alpha=0.16, zorder=5))
    half = 0.55
    ax.add_patch(Polygon([(rx, ry),
                          (rx + np.cos(rth - half) * 120, ry + np.sin(rth - half) * 120),
                          (rx + np.cos(rth + half) * 120, ry + np.sin(rth + half) * 120)],
                         closed=True, fc=COL_ESC if cmd["escape"] else COL_ROBOT, alpha=0.12, zorder=2))
    ax.grid(True, color="#101a26", lw=0.5)
    for s in ax.spines.values():
        s.set_color("#16222f")
    ax.set_title(f"t={f*DT_FRAME:.1f}s · {cmd['mode'].upper()} · dist {dist:.0f}",
                 color=COL_ESC if cmd["escape"] else FG, fontsize=10)
    if cmd["escape"]:
        line, col = sim["narration"]["escape"], "#ffb3c0"
    elif dist < 120:
        line, col = sim["narration"]["chase"], "#ffe9a8"
    else:
        line, col = sim["narration"]["safe"], "#a8e9ff"
    ax.text(0, -ARENA + 22, "Vector: " + line, color=col, fontsize=9.5,
            style="italic", ha="center")


def main():
    print("=" * 64)
    print(" VECTOR-FLY :: DEMO DE ESCAPE")
    print("=" * 64)
    t0 = time.time()
    sim = simulate()
    print(f"[sim] {FRAMES} fotogramas en {time.time()-t0:.1f}s")
    outs = render(sim)
    print("-" * 64)
    for o in outs:
        print(f"  generado: {o}  ({o.stat().st_size/1e6:.2f} MB)")
    print("=" * 64)


if __name__ == "__main__":
    main()
