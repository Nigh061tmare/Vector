import sys
sys.path.insert(0, r"C:\Users\Jose Luis")
import vector_autonomo as va

print("PROVEEDOR:", va.PROVEEDOR)
print("Ollama client:", "OK" if va.client_ollama else "NO")
print("Modelo local:", va.OLLAMA_MODEL_TEXTO)
print("-" * 50)

msgs = [
    {"role": "system", "content": "Eres el robot Vector. Responde en espanol, en una sola frase corta y natural."},
    {"role": "user", "content": "Vector, acabo de llegar a casa. Dime algo simpatico."},
]

import time
t0 = time.time()
resp = va.ia_raw(msgs, va.MODELO_TEXTO, va.FALLBACKS_TEXTO, 40, 0.8, 30)
dt = time.time() - t0

print("RESPUESTA:", resp)
print("LATENCIA  : %.2fs" % dt)
print("Limpiar OK:", va._limpiar_respuesta_ia("Pienso...\nHola, soy Vector."))
