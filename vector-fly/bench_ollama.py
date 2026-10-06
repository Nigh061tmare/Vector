import json
import time
import urllib.request

MODELS = [
    "qwen3.5:4b",
    "qwen3.5:9b",
    "qwen9b-silly:latest",
    "qwen14b-silly:latest",
    "richardyoung/qwen3-14b-abliterated:q4_K_M",
    "qwen3.8-35b-fast:latest",
]

SYSTEM = (
    "Eres Vector, un pequeno robot de escritorio con emociones. "
    "Respondes SIEMPRE en espanol, en UNA sola frase corta y natural (max 12 palabras). "
    "No expliques, no pienses en voz alta, no uses comillas. Solo la frase."
)
PROMPT = "Un nino te pregunta: ¿que ves ahora mismo?"


def run(model):
    body = json.dumps({
        "model": model,
        "system": SYSTEM,
        "prompt": PROMPT,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.7, "num_predict": 60},
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read())
    dt = time.time() - t0
    resp = (data.get("response") or "").strip()
    think = data.get("thinking") or ""
    load = data.get("load_duration", 0) / 1e9
    ev = data.get("eval_count", 0)
    ed = max(data.get("eval_duration", 1) / 1e9, 1e-6)
    return dt, load, ev, ev / ed, resp, len(think)


results = []
for m in MODELS:
    try:
        print(f"\n=== {m} ===", flush=True)
        dt, load, ev, tps, resp, thk = run(m)
        # segunda pasada en caliente
        dt2, _, ev2, tps2, resp2, _ = run(m)
        results.append((m, dt2, tps2, resp2))
        print(f"  frio : {dt:.1f}s (load {load:.1f}s) tok/s {tps:.1f} | thinking {thk} chars")
        print(f"  caliente: {dt2:.2f}s tok/s {tps2:.1f}")
        print(f"  resp : {resp2[:120]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n" + "=" * 70)
print(f"{'MODELO':<45} {'s':>6} {'tok/s':>7}")
print("=" * 70)
for m, dt, tps, resp in sorted(results, key=lambda x: -x[2]):
    print(f"{m:<45} {dt:>6.2f} {tps:>7.1f}")
