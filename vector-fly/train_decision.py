#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VECTOR-FLY :: ENTRENAR DECISION VISUAL
# La mosca navega: esquiva obstaculos y alcanza un objetivo.
import sys
import math
import time
import json
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fly_core import FlyCore

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"

W = 96
BACKGROUND = 0.5
ARENA = 320.0
ROBOT_R = 6.0
GOAL_R = 22.0
SPEED = 7.0
TURN = 0.30
N_SUB = 5
T_STEPS = 90
AVOID_RANGE = 150.0
GOAL_GAIN = 1.7
AVOID_GAIN = 3.4


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def bearing(th, dx, dy):
    return wrap(math.atan2(dy, dx) - th)


def make_arena(rng, n_obs=5, min_start_goal=150.0):
    px, py = rng.uniform(40, ARENA - 40, 2)
    th = rng.uniform(-math.pi, math.pi)
    for _ in range(40):
        gx, gy = rng.uniform(40, ARENA - 40, 2)
        if math.hypot(gx - px, gy - py) >= min_start_goal:
            break
    obs = []
    for _ in range(n_obs):
        for _ in range(30):
            ox, oy = rng.uniform(30, ARENA - 30, 2)
            r = rng.uniform(11, 20)
            if math.hypot(ox - px, oy - py) < 70:
                continue
            if math.hypot(ox - gx, oy - gy) < 55:
                continue
            obs.append((float(ox), float(oy), float(r)))
            break
    return float(px), float(py), float(th), (float(gx), float(gy)), obs


def panorama(px, py, th, goal, obs):
    pano = np.full(W, BACKGROUND, np.float32)
    az = np.linspace(-1.0, 1.0, W, dtype=np.float32)
    gdx, gdy = goal[0] - px, goal[1] - py
    gd = math.hypot(gdx, gdy) + 1e-6
    gb = bearing(th, gdx, gdy) / (math.pi / 2)
    gw = float(np.clip(0.35 / max(gd / 40.0, 1.0), 0.05, 0.35))
    m = np.abs(az - gb) <= gw
    pano[m] = 1.0
    for ox, oy, r in obs:
        dx, dy = ox - px, oy - py
        d = math.hypot(dx, dy) + 1e-6
        b = bearing(th, dx, dy) / (math.pi / 2)
        half = float(np.clip(r / d * 0.95, 0.04, 0.55))
        m = np.abs(az - b) <= half
        dark = float(np.clip(1.0 - d / 320.0, 0.35, 0.98))
        pano[m] = np.minimum(pano[m], BACKGROUND * (1.0 - dark))
    return pano


def teacher(px, py, th, goal, obs, look=(30, 60, 90, 120)):
    """Profesor: prueba 41 rumbos y elige el que va a meta esquivando."""
    gdir = math.atan2(goal[1] - py, goal[0] - px)
    best_s, best = 0.0, -1e9
    for s in np.linspace(-1.0, 1.0, 41):
        h = th + s * TURN
        align = math.cos(h - gdir)
        pen = 0.0
        for L in look:
            x, y = px + math.cos(h) * L, py + math.sin(h) * L
            for ox, oy, r in obs:
                gap = math.hypot(ox - x, oy - y) - r - ROBOT_R
                if gap < 14.0:
                    pen += (14.0 - gap) / 14.0 * (1.0 - L / 170.0)
        score = align - 1.8 * pen
        if score > best:
            best, best_s = score, float(s)
    return best_s


def population(brain, n_random=2000, seed=0):
    idx = [np.asarray(v, dtype=np.int64) for v in brain.groups.values()]
    vis = brain.cells(["LPLC2", "LC4", "LPLC1", "LC10a", "T2", "T3"])
    rng = np.random.default_rng(seed)
    extra = rng.choice(brain.n, n_random, replace=False)
    return np.unique(np.concatenate([np.concatenate(idx), vis, extra]))


SEL = None       # indices de las neuronas reservorio seleccionadas
NPOP = None      # tamano de la poblacion reservorio
SELECT_N = 400   # cuantas neuronas reservorio conserva el readout


def select_feat(feat):
    """Recorta el vector de rasgos a las neuronas elegidas + rasgos visuales."""
    if SEL is None or NPOP is None:
        return np.asarray(feat, np.float32)
    f = np.asarray(feat, np.float32)
    return np.concatenate([f[:NPOP][SEL], f[NPOP:]])


def augment(feat, core, prev_steer):
    """Traza neuronal + rasgos visuales directos + memoria del giro previo."""
    v = np.asarray(getattr(core, "_vis_feats", np.zeros(13, np.float32)), np.float32)
    return np.concatenate([np.asarray(feat, np.float32), v, np.array([prev_steer], np.float32)])


def run_episode(core, trace, arena, policy, readout=None, rng=None, record=False):
    px, py, th, goal, obs = arena
    core.brain.reset(seed=int(rng.integers(1 << 30)) if rng is not None else 1)
    trace.reset()
    prev = None
    frames = []
    collided = False
    reached = False
    prev_steer = 0.0
    for t in range(T_STEPS * 2):
        pano = panorama(px, py, th, goal, obs)
        eye, inj = core.vision_drive(pano, prev)
        prev = pano
        feat = None
        for _ in range(N_SUB):
            fired = core.brain.step(eye_drive=eye, inject=inj)
            feat = trace.observe(fired)
        feat = augment(feat, core, prev_steer)
        if policy == "learned":
            steer = float(np.clip(readout.predict(select_feat(feat)), -1.0, 1.0))
        elif policy == "teacher":
            steer = teacher(px, py, th, goal, obs)
        else:
            steer = float(rng.uniform(-1, 1))
        if record:
            frames.append((px, py, th, goal, list(obs), pano.copy(), steer))
        prev_steer = steer
        th = wrap(th + steer * TURN)
        px += math.cos(th) * SPEED
        py += math.sin(th) * SPEED
        for ox, oy, r in obs:
            if math.hypot(ox - px, oy - py) < r + ROBOT_R:
                collided = True
        if collided:
            break
        if math.hypot(goal[0] - px, goal[1] - py) < GOAL_R:
            reached = True
            break
    return {"reached": reached, "collided": collided, "t": t, "frames": frames}


def main():
    global SEL, NPOP
    from flybrain.reservoir import Trace, Readout
    episodes = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    seed = 1234
    rng = np.random.default_rng(seed)

    print("=" * 64)
    print(" VECTOR-FLY :: ENTRENAR DECISION (navegacion visual)")
    print("=" * 64)
    core = FlyCore(device="cpu")
    idx = population(core.brain)
    trace = Trace(core.brain, idx=idx, tau=0.1)
    NPOP = len(idx)
    if len(sys.argv) > 2 and sys.argv[2] == "vis":
        SEL = np.array([], dtype=np.int64)
        print("modo: SOLO vision (sin reservorio)", flush=True)
    else:
        SEL = None
    print(f"poblacion de rasgos: {NPOP} neuronas", flush=True)
    print(f"episodios: {episodes}  control: {T_STEPS} pasos x {N_SUB} neuronales")

    CACHE = MODELS / "decision_dataset.npz"
    fresh = "--fresh" in sys.argv
    Xl, Yl, gl = [], [], []
    start_ep = 0
    if CACHE.exists() and not fresh:
        d = np.load(CACHE)
        if int(d["npop"]) == NPOP:
            start_ep = int(d["episodes"])
            Xl = [r for r in np.asarray(d["X"], np.float32)]
            Yl = [float(v) for v in np.asarray(d["Y"], np.float32)]
            gl = [int(v) for v in np.asarray(d["groups"])]
            print(f"cache: reanudo desde {start_ep} episodios (X={np.asarray(d['X']).shape})", flush=True)
    if start_ep < episodes:
        t0 = time.time()
        for ep in range(start_ep, episodes):
            erng = np.random.default_rng(1000 + ep * 7919)
            arena = make_arena(erng)
            px, py, th, goal, obs = arena
            core.brain.reset(seed=int(erng.integers(1 << 30)))
            trace.reset()
            prev = None
            prev_steer = 0.0
            for t in range(T_STEPS):
                pano = panorama(px, py, th, goal, obs)
                eye, inj = core.vision_drive(pano, prev)
                prev = pano
                feat = None
                for _ in range(N_SUB):
                    fired = core.brain.step(eye_drive=eye, inject=inj)
                    feat = trace.observe(fired)
                y = teacher(px, py, th, goal, obs)
                Xl.append(augment(feat, core, prev_steer))
                Yl.append(y)
                gl.append(ep)
                prev_steer = y
                th = wrap(th + y * TURN)
                px += math.cos(th) * SPEED
                py += math.sin(th) * SPEED
                hit = any(math.hypot(ox - px, oy - py) < r + ROBOT_R for ox, oy, r in obs)
                if hit or math.hypot(goal[0] - px, goal[1] - py) < GOAL_R:
                    break
            if (ep + 1) % 5 == 0:
                print(f"  ep {ep+1:3d}/{episodes}  muestras={len(Xl)}  {time.time()-t0:5.1f}s", flush=True)
            if (ep + 1) % 25 == 0:
                np.savez(CACHE, X=np.asarray(Xl, np.float32).astype(np.float16),
                         Y=np.asarray(Yl, np.float32),
                         groups=np.asarray(gl), npop=np.int64(NPOP), episodes=np.int64(ep + 1))
    X = np.asarray(Xl, np.float32)
    Y = np.asarray(Yl, np.float32)
    groups = np.asarray(gl)
    np.savez(CACHE, X=X.astype(np.float16), Y=Y, groups=groups,
             npop=np.int64(NPOP), episodes=np.int64(episodes))
    print(f"dataset: X={X.shape} Y={Y.shape}", flush=True)

    # --- reduce el reservorio a las neuronas mas informativas (varianza) ---
    if SEL is not None and SEL.size == 0:
        Xr = X[:, NPOP:]
        print(f"rasgos SOLO vision: {X.shape[1]} -> {Xr.shape[1]}", flush=True)
    else:
        res_part = X[:, :NPOP]
        var = res_part.var(0)
        n_sel = min(SELECT_N, NPOP)
        SEL = np.argsort(-var)[:n_sel].astype(np.int64)
        Xr = np.concatenate([res_part[:, SEL], X[:, NPOP:]], axis=1)
        print(f"rasgos reducidos: {X.shape[1]} -> {Xr.shape[1]} (reservorio {n_sel} + visuales)", flush=True)

    print("\nentrenando readout de decision (ridge)...", flush=True)
    dec = Readout.fit(Xr, Y, kind="ridge", groups=None, verbose=True)
    print(f"-> readout guardado (neg-MSE CV {dec.cv_score:.4f})")

    print("\nevaluando politicas (20 arenas nuevas)...")
    res = {"learned": [], "teacher": [], "random": []}
    for k in range(20):
        arena = make_arena(np.random.default_rng(900000 + k))
        for pol in res:
            r = run_episode(core, trace, arena, pol if pol != "learned" else "learned",
                            readout=dec if pol == "learned" else None,
                            rng=np.random.default_rng(910000 + k))
            res[pol].append((r["reached"], r["collided"], r["t"]))
    print(f"\n{'politica':10s} {'exito':>7s} {'choques':>8s} {'pasos med':>10s}", flush=True)
    for pol, rows in res.items():
        succ = sum(1 for r, c, t in rows if r) / len(rows)
        coll = sum(1 for r, c, t in rows if c) / len(rows)
        avg = np.mean([t for _, _, t in rows])
        print(f"{pol:10s} {succ*100:6.0f}% {coll*100:7.0f}% {avg:10.1f}", flush=True)

    MODELS.mkdir(exist_ok=True)
    dec.save(MODELS / "decision.npz")
    np.savez(MODELS / "decision_meta.npz", idx=idx.astype(np.int64),
             sel=SEL, npop=np.int64(NPOP), cv_score=dec.cv_score)
    print(f"\nOK. Modelo en {MODELS / 'decision.npz'}")


if __name__ == "__main__":
    main()


