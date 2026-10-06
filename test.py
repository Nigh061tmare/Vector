import anki_vector

with anki_vector.Robot() as robot:
    print("Conectado a Vector con exito")
    robot.behavior.say_text("Hola, ya funciono")
