import anki_vector, time
print("Intentando conectar...")
try:
    with anki_vector.Robot(cache_animation_lists=False) as robot:
        print("CONECTADO OK - en cargador:", getattr(robot.status,'is_on_charger','?'))
        time.sleep(1)
except Exception as e:
    print("ERROR:", repr(e)[:200])
