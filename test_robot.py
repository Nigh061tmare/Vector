import os
cert = r"C:\Users\Jose Luis\.anki_vector\Vector-F9E8-0090963d.cert"
os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = cert
os.environ["GRPC_SSL_TARGET_NAME_OVERRIDE_ARG"] = "Vector-F9E8"

import anki_vector

try:
    with anki_vector.Robot() as robot:
        print("[+] Conexión gRPC exitosa con Vector!")
        bat = robot.get_battery_state()
        print(f"[+] Batería: {bat.battery_volts:.2f}V, en cargador: {bat.is_on_charger_platform}")
except Exception as e:
    print("[-] Error con Robot():", e)
