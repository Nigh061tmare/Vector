#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VECTOR ULTRA 13.5 — NETWORK & AUTHENTICATION GUARD
# ============================================================
# Previene y repara automáticamente caídas de red, cambios de IP
# por DHCP, desconexiones gRPC y errores 401 Unauthorized de Vector.
# ============================================================

import os
import sys
import time
import socket
import logging
from pathlib import Path
from typing import Optional, Tuple
import requests

import vector_log
log = vector_log.get_logger("netguard")

ROBOT_NAME = "Vector-F9E8"
ROBOT_SN = "0090963d"
DEFAULT_IP = "192.168.1.82"
CERT_PATH = Path.home() / ".anki_vector" / f"{ROBOT_NAME}-{ROBOT_SN}.cert"
CONFIG_PATH = Path.home() / ".anki_vector" / "sdk_config.ini"

WIREPOD_HOSTS = [
    "http://192.168.1.85:8080",      # LAN primaria
    "http://100.100.148.91:8080",     # Fallback Tailscale (IP fija permanente)
]

SSH_REMOTE_USER = "vector"
SSH_REMOTE_HOST = "100.100.148.91"
REMOTE_BOT_SDK_INFO = r"C:\Users\pepde\AppData\Roaming\wire-pod\jdocs\botSdkInfo.json"


def resolver_ip_vector(timeout: float = 3.0) -> str:
    """
    Resuelve la IP actual de Vector mediante mDNS (Vector-F9E8.local).
    Si el router asignó una nueva IP por DHCP, la detecta inmediatamente.
    """
    mdns_host = f"{ROBOT_NAME}.local"
    try:
        ip = socket.gethostbyname(mdns_host)
        if ip and (ip.startswith("192.168.") or ip.startswith("10.")):
            log.debug("mDNS resolvió %s -> %s", mdns_host, ip)
            return ip
    except Exception as e:
        log.debug("Fallo mDNS para %s: %s", mdns_host, e)

    # Fallback: leer IP de sdk_config.ini si existe
    if CONFIG_PATH.exists():
        try:
            for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ip ="):
                    cfg_ip = line.split("=")[1].strip()
                    if cfg_ip:
                        return cfg_ip
        except Exception:
            pass

    return DEFAULT_IP


def test_tcp_port(ip: str, port: int = 443, timeout: float = 2.5) -> bool:
    """Comprueba si el puerto TCP (443 para Vector, 8080 para Wire-Pod) está abierto."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def actualizar_sdk_config(ip: str, guid: Optional[str] = None) -> bool:
    """Actualiza sdk_config.ini con la IP y el GUID más recientes."""
    try:
        current_ip = ""
        current_guid = guid
        if CONFIG_PATH.exists():
            for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("guid ="):
                    if not current_guid:
                        current_guid = s.split("=", 1)[1].strip()
                elif s.startswith("ip ="):
                    current_ip = s.split("=", 1)[1].strip()

        if not current_guid:
            log.warning("No se pudo obtener GUID para sdk_config.ini")
            return False

        # Si ni la IP ni el GUID cambiaron, evitar reescribir
        if current_ip == ip and (guid is None or guid == current_guid):
            return True

        content = (
            f"[{ROBOT_SN}]\n"
            f"cert = {CERT_PATH}\n"
            f"ip = {ip}\n"
            f"name = {ROBOT_NAME}\n"
            f"guid = {current_guid}\n"
        )
        CONFIG_PATH.write_text(content, encoding="utf-8")
        log.info("sdk_config.ini actualizado con IP=%s, GUID=%s", ip, current_guid)
        return True
    except Exception as e:
        log.error("Error escribiendo sdk_config.ini: %s", e)
        return False


def sincronizar_remoto_wirepod(ip_vector: str, guid: str) -> None:
    """Sincroniza el nuevo GUID en el archivo botSdkInfo.json de Wire-Pod remoto."""
    try:
        import subprocess
        json_data = (
            '{"global_guid":"tni1TRsTRTaNSapjo0Y+Sw==","robots":['
            f'{{"esn":"{ROBOT_SN}","ip_address":"{ip_vector}","guid":"{guid}","activated":true}}'
            ']}'
        )
        tmp_file = Path.home() / "temp_botSdkInfo.json"
        tmp_file.write_text(json_data, encoding="utf-8")
        # Enviar vía scp silencioso con timeout
        cmd = [
            "scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
            str(tmp_file),
            f"{SSH_REMOTE_USER}@{SSH_REMOTE_HOST}:{REMOTE_BOT_SDK_INFO}"
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        if tmp_file.exists():
            tmp_file.unlink()
        log.info("botSdkInfo.json sincronizado en servidor remoto Wire-Pod (%s)", SSH_REMOTE_HOST)
    except Exception as e:
        log.warning("Aviso sincronización remota Wire-Pod: %s", e)


def renovar_token_wirepod(ip_vector: Optional[str] = None) -> bool:
    """
    Solicita un nuevo GUID a Vector utilizando una sesión fresca de Wire-Pod.
    Prueba primero la IP LAN de Wire-Pod y luego el fallback Tailscale.
    """
    if not ip_vector:
        ip_vector = resolver_ip_vector()

    if not CERT_PATH.exists():
        log.error("Certificado de Vector no encontrado en %s", CERT_PATH)
        return False

    session_token = None
    for wp_url in WIREPOD_HOSTS:
        try:
            resp = requests.post(f"{wp_url}/sessions", json={"username": "wirepod", "password": ""}, timeout=5)
            if resp.status_code == 200:
                session_token = resp.json().get("session", {}).get("session_token")
                if session_token:
                    log.info("Sesión obtenida de Wire-Pod (%s): %s", wp_url, session_token)
                    break
        except Exception as e:
            log.debug("Wire-Pod en %s no respondió: %s", wp_url, e)

    if not session_token:
        log.error("No se pudo obtener sesión de ninguno de los endpoints de Wire-Pod.")
        return False

    try:
        import grpc
        from anki_vector import messaging

        cert_bytes = CERT_PATH.read_bytes()
        creds = grpc.ssl_channel_credentials(root_certificates=cert_bytes)
        channel = grpc.secure_channel(
            f"{ip_vector}:443",
            creds,
            options=(
                ("grpc.ssl_target_name_override", ROBOT_NAME),
                ("grpc.keepalive_time_ms", 10000),
                ("grpc.keepalive_timeout_ms", 5000),
                ("grpc.keepalive_permit_without_calls", 1),
                ("grpc.http2.max_pings_without_data", 0),
            )
        )
        stub = messaging.client.ExternalInterfaceStub(channel)
        client_name = socket.gethostname().encode("utf-8")
        req = messaging.protocol.UserAuthenticationRequest(
            user_session_id=session_token.encode("utf-8"),
            client_name=client_name
        )
        res = stub.UserAuthentication(req, timeout=10)
        new_guid = res.client_token_guid.decode("utf-8")
        log.info("¡Nuevo GUID Vector emitido con éxito!: %s", new_guid)

        # Actualizar config local y remoto
        actualizar_sdk_config(ip=ip_vector, guid=new_guid)
        sincronizar_remoto_wirepod(ip_vector=ip_vector, guid=new_guid)
        return True
    except Exception as err:
        log.error("Error solicitando UserAuthenticationRequest a Vector: %s", err)
        return False


def verificar_y_reparar_conexion() -> Tuple[bool, str]:
    """
    Verificación preventiva de salud de conexión:
    1. Resuelve IP actual de Vector vía mDNS.
    2. Comprueba que el puerto 443 responde.
    3. Si la IP en sdk_config.ini no coincide, la actualiza.
    4. Verifica que el GUID actual es aceptado por Vector con un ping gRPC.
    5. Si falla por 401 UNAUTHENTICATED, renueva el token automáticamente.
    """
    ip_actual = resolver_ip_vector()
    log.info("Comprobando conectividad con Vector en %s:443...", ip_actual)

    if not test_tcp_port(ip_actual, 443, timeout=3.0):
        # Si la IP falló, probar DEFAULT_IP
        if ip_actual != DEFAULT_IP and test_tcp_port(DEFAULT_IP, 443, timeout=2.0):
            ip_actual = DEFAULT_IP
        else:
            msg = f"Vector no responde en {ip_actual}:443. Verifica que esté encendido y con Wi-Fi."
            log.warning(msg)
            return False, msg

    # Asegurar que sdk_config.ini tenga la IP correcta
    actualizar_sdk_config(ip_actual)

    # Prueba de autenticación gRPC rápida
    try:
        import anki_vector
        with anki_vector.Robot(cache_animation_lists=False) as robot:
            bstate = robot.get_battery_state()
            volts = getattr(bstate, "battery_volts", 0.0)
            msg = f"Conexión gRPC verificada con éxito. Batería: {volts:.2f}V"
            log.info(msg)
            return True, msg
    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "UNAUTHENTICATED" in err_str or "unauthorized" in err_str.lower():
            log.warning("Token gRPC desautenticado (401). Iniciando autorenovación con Wire-Pod...")
            ok = renovar_token_wirepod(ip_actual)
            if ok:
                return True, "Token gRPC renovado exitosamente con Wire-Pod."
            return False, "Fallo al autorenovar token con Wire-Pod."
        return False, f"Error gRPC: {e}"


if __name__ == "__main__":
    ok, detalle = verificar_y_reparar_conexion()
    print(f"Resultado: {'OK' if ok else 'FALLO'} - {detalle}")
