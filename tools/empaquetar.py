#!/usr/bin/env python3
"""Genera entrega/ (zip + parche + checksums) con SOLO lo nuevo o modificado.

  python tools/empaquetar.py --base RUTA_A_COPIA_ORIGINAL

--base: carpeta con tus archivos ANTES de los cambios (con vector-fly/ dentro).
Los archivos nuevos no necesitan base; los modificados se listan y se les genera parche.
"""
import argparse, difflib, hashlib, os, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODIFICADOS = ["vector_autonomo.py", "vector_dashboard.py", "vector_network_guard.py",
               "vector_nexus.html", "vector_talk.py", "vector-fly/fly_core.py", "vector-fly/fly_live.py"]
NUEVOS = ["vector_motion.py", "vector_behavior.py", "vector_gaze.py", "vector_map.py", "vector_life.py",
          "vector_facts.py", "vector_vad.py", "vector_supervisor.py", "smoke_movimiento.py",
          "smoke_vida.py", "vector-fly/SERVICIOS_SUPERVISADOS.bat", "requirements-dev.txt",
          "docs/VIDA_ANIMAL.md", "AUDITORIA_FASE0.md", "INSTALAR.md"]


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="")
    a = ap.parse_args()
    out = os.path.join(ROOT, "entrega"); os.makedirs(out, exist_ok=True)
    tests = sorted(os.path.join("tests", f) for f in os.listdir(os.path.join(ROOT, "tests"))
                   if f.endswith(".py"))
    files = MODIFICADOS + NUEVOS + tests
    sums, patch = [], []
    zpath = os.path.join(out, "Vector_Ultra_ENTREGA.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            src = os.path.join(ROOT, f)
            if not os.path.exists(src):
                print("FALTA", f); continue
            z.write(src, f)
            sums.append(f"{hashlib.sha256(open(src, 'rb').read()).hexdigest()}  {f}")
            if a.base and f in MODIFICADOS and os.path.exists(os.path.join(a.base, f)):
                old = open(os.path.join(a.base, f), encoding="utf-8", errors="replace").read().splitlines(True)
                new = open(src, encoding="utf-8", errors="replace").read().splitlines(True)
                patch += difflib.unified_diff(old, new, f"a/{f}", f"b/{f}")
    open(os.path.join(out, "MANIFEST.sha256"), "w").write("\n".join(sums) + "\n")
    if patch:
        open(os.path.join(out, "CAMBIOS.patch"), "w", encoding="utf-8").write("".join(patch))
    print(f"{len(files)} archivos -> {zpath}")


if __name__ == "__main__":
    main()
