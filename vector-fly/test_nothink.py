import time
import sys
sys.path.insert(0, r"C:\Users\Jose Luis")

from openai import OpenAI

cli = OpenAI(api_key="ollama", base_url="http://127.0.0.1:11434/v1", timeout=120)

MSGS = [
    {"role": "system", "content": "Eres el robot Vector. Responde en espanol, una sola frase corta y natural."},
    {"role": "user", "content": "Vector, acabo de llegar a casa. Dime algo simpatico."},
]

PRUEBAS = [
    ("base (sin tocar)", {}, MSGS, "qwen3.5:9b"),
    ("reasoning_effort=none", {"extra_body": {"reasoning_effort": "none"}}, MSGS, "qwen3.5:9b"),
    ("think=false", {"extra_body": {"think": False}}, MSGS, "qwen3.5:9b"),
    ("chat_template enable_thinking=false",
     {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}, MSGS, "qwen3.5:9b"),
    ("/no_think en system", {}, [
        {"role": "system", "content": "Eres el robot Vector. Responde en espanol, una sola frase corta y natural. /no_think"},
        {"role": "user", "content": "Vector, acabo de llegar a casa. Dime algo simpatico."},
    ], "qwen3.5:9b"),
    ("qwen3.5:4b + reasoning none", {"extra_body": {"reasoning_effort": "none"}}, MSGS, "qwen3.5:4b"),
]

for nombre, kw, msgs, mod in PRUEBAS:
    try:
        t0 = time.time()
        r = cli.chat.completions.create(model=mod, messages=msgs, max_tokens=60, temperature=0.8, **kw)
        dt = time.time() - t0
        msg = r.choices[0].message
        txt = (msg.content or "").strip().replace("\n", " ")
        reason = getattr(msg, "reasoning_content", None) or ""
        print(f"{nombre:<45} {dt:>6.2f}s | resp={txt[:60]!r} | think={len(reason)}ch")
    except Exception as e:
        print(f"{nombre:<45} ERROR: {str(e)[:100]}")
