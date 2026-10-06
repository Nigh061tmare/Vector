import sys
sys.path.insert(0, r"C:\Users\Jose Luis\vector-fly")
from fly_core import FlyCore

core = FlyCore(device="cpu")
print("dx<0 = amenaza IZQUIERDA ; dx>0 = amenaza DERECHA\n")
for label, dx in (("IZQUIERDA", -30.0), ("DERECHA", 30.0)):
    core.reset()
    first = None
    for i in range(60):
        dist_game = max(6.0, 70.0 - i * 1.0)
        size = max(2.0, min(130.0, 520.0 / dist_game))
        threat = max(0.0, min(1.0, (70.0 - dist_game) / 60.0))
        inj = core.visual_inject(opp=(dx, size), threat=threat)
        snap = core.step(inject=inj)
        cmd = core.motor_command()
        if cmd.get("escape") and first is None:
            first = dict(cmd)
    f = first or {"left": 0, "right": 0}
    print(f"  {label:10s} dx={dx:+5.1f}  primer escape: L={f.get('left',0):+5.2f}  R={f.get('right',0):+5.2f}")
