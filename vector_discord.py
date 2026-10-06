# ============================================================
# VECTOR ULTRA — PUENTE DISCORD 2.0 (CONTROL REMOTO Y CENTINELA)
# ============================================================
import os
import time
import queue
import asyncio
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import discord
from dotenv import load_dotenv

load_dotenv(r"C:\Users\Jose Luis\.env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "378941255742128133"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# Cola de comandos hacia Vector
discord_command_queue: "queue.Queue" = queue.Queue()

# Evento y variable para sincronizar captura de fotos solicitadas por Discord
_foto_solicitada_event = threading.Event()
_foto_resultado_path: Optional[str] = None

def registrar_foto_tomada(ruta: str) -> None:
    global _foto_resultado_path
    _foto_resultado_path = ruta
    _foto_solicitada_event.set()

class VectorDiscordBot(discord.Client):
    def __init__(self, q_out: "queue.Queue", channel_id: int):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.q_out = q_out
        self.channel_id = channel_id
        self.target_channel: Optional[discord.TextChannel] = None
        self.rag_engine = None

    async def on_ready(self):
        print(f"[+] Discord 2.0: Conectado como {self.user} (ID: {self.user.id})")
        self.target_channel = self.get_channel(self.channel_id)
        if self.target_channel:
            try:
                embed = discord.Embed(
                    title="🤖 Vector Ultra 13.0 Conectado",
                    description="Sistema autónomo operativo. Escribe `!ayuda` para ver los comandos remotos.",
                    color=0x00FF88
                )
                embed.add_field(name="Modo", value="Híbrido (Gemini + OpenRouter)", inline=True)
                embed.add_field(name="Control Móvil", value="http://192.168.1.51:8000", inline=True)
                await self.target_channel.send(embed=embed)
            except Exception as e:
                print("Error enviando saludo de Discord:", e)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.author.id == self.user.id:
            return
        
        # Aceptar mensajes del canal designado o menciones
        if message.channel.id != self.channel_id and not self.user.mentioned_in(message):
            return

        txt = message.content.strip()
        txt_low = txt.lower()

        # 1. COMANDO: AYUDA
        if txt_low in ("!ayuda", "!help", "!comandos"):
            embed = discord.Embed(title="🎮 Comandos de Control de Vector", color=0x58A6FF)
            embed.add_field(name="📸 `!foto`", value="Toma una foto en directo y la sube al chat.", inline=False)
            embed.add_field(name="🔋 `!estado`", value="Consulta el estado de batería, ánimos y cargador.", inline=False)
            embed.add_field(name="🗣️ `!decir <frase>`", value="Vector dice el mensaje en voz alta en la habitación.", inline=False)
            embed.add_field(name="📚 `!vault <tema>`", value="Busca información en las notas de tu Obsidian Vault.", inline=False)
            embed.add_field(name="🛡️ `!patrulla`", value="Inicia una ronda de patrulla y vigilancia.", inline=False)
            embed.add_field(name="🎲 `!dado` / `!moneda`", value="Tira un dado virtual o lanza una moneda.", inline=False)
            embed.add_field(name="📟 `!matrix`", value="Muestra lluvia de código Matrix en su pantalla OLED.", inline=False)
            await message.channel.send(embed=embed)
            return

# 2. COMANDO: FOTO
        if txt_low in ("!foto", "!cam", "!camara", "/foto", "/cam"):
            await message.channel.send("📸 *Vector está apuntando la cámara y tomando una foto...*")
            global _foto_solicitada_event, _foto_resultado_path
            _foto_resultado_path = None
            _foto_solicitada_event.clear()
            
            # Pedir a Vector tomar la foto
            self.q_out.put(("discord", f"{message.author.display_name}: foto"))
            
            # Esperar hasta 5 segundos a que la foto esté lista
            for _ in range(25):
                await asyncio.sleep(0.2)
                if _foto_solicitada_event.is_set() and _foto_resultado_path and Path(_foto_resultado_path).exists():
                    break
            
            if _foto_resultado_path and Path(_foto_resultado_path).exists():
                try:
                    with open(_foto_resultado_path, "rb") as f:
                        file = discord.File(f, filename="foto_vector.jpg")
                        embed = discord.Embed(title="📸 Foto Capturada por Vector", color=0x00FF88)
                        embed.set_image(url="attachment://foto_vector.jpg")
                        await message.channel.send(file=file, embed=embed)
                        return
                except Exception as e:
                    await message.channel.send(f"⚠️ Error al subir la foto: {e}")
                    return
            await message.channel.send("⚠️ Vector no pudo completar la captura a tiempo.")
            return

        # 3. COMANDO: ESTADO
        if txt_low in ("!estado", "!status", "/status"):
            from vector_dashboard import telemetria_data
            bat_v = telemetria_data.get("bateria_v", 0.0)
            bat_pct = telemetria_data.get("bateria_pct", 0)
            chg = "En base de carga 🔌" if telemetria_data.get("en_cargador") else "En la mesa 🐾"
            animo = telemetria_data.get("animo", "curioso")
            embed = discord.Embed(title="📊 Telemetría de Vector Robot", color=0x238636)
            embed.add_field(name="Batería", value=f"{bat_v:.2f}V ({bat_pct}%)", inline=True)
            embed.add_field(name="Ubicación", value=chg, inline=True)
            embed.add_field(name="Estado de Ánimo", value=animo.capitalize(), inline=True)
            embed.add_field(name="Última Frase", value=f"\"{telemetria_data.get('ultima_frase', '...')}\"", inline=False)
            await message.channel.send(embed=embed)
            return

        # 4. COMANDO: DECIR
        if txt_low.startswith("!decir "):
            frase = txt[7:].strip()
            if frase:
                self.q_out.put(("discord", f"{message.author.display_name}: decir:{frase}"))
                await message.add_reaction("🗣️")
            return

        # 5. COMANDO: VAULT OBSIDIAN
        if txt_low.startswith("!vault "):
            tema = txt[7:].strip()
            await message.channel.send(f"🔍 *Buscando en Obsidian:* `{tema}`...")
            try:
                from vector_rag import obsidian_rag
                res = obsidian_rag.buscar(tema, top_k=2)
                if res:
                    embed = discord.Embed(title=f"📚 Resultados del Vault: {tema}", color=0x58A6FF)
                    for r in res:
                        embed.add_field(
                            name=f"📄 {r['titulo']} (Relevancia: {r['score']})",
                            value=f"{r['resumen'][:250]}...\n*Ruta: {r['ruta_relativa']}*",
                            inline=False
                        )
                    await message.channel.send(embed=embed)
                    return
            except Exception as e:
                await message.channel.send(f"⚠️ Error buscando en el Vault: {e}")
                return
            await message.channel.send("No se encontraron notas con información relevante.")
            return

# 6. COMANDO: PATRULLA
        if txt_low in ("!patrulla", "!ronda", "/patrulla"):
            self.q_out.put(("discord", f"{message.author.display_name}: patrulla"))
            await message.channel.send("🛡️ *Iniciando ronda de patrulla y vigilancia en la mesa...*")
            return

        # 7. COMANDO: DADO / MONEDA / MATRIX
        if txt_low.startswith("!dado"):
            caras = 6
            partes = txt_low.split()
            if len(partes) > 1 and partes[1].isdigit():
                caras = int(partes[1])
            self.q_out.put(("discord", f"{message.author.display_name}: dado:{caras}"))
            await message.add_reaction("🎲")
            return
        elif txt_low in ("!moneda", "!caraocruz"):
            self.q_out.put(("discord", f"{message.author.display_name}: moneda"))
            await message.add_reaction("🪙")
            return
        elif txt_low in ("!matrix", "!codigo"):
            self.q_out.put(("discord", f"{message.author.display_name}: matrix"))
            await message.add_reaction("📟")
            return

        # 8. CONVERSACIÓN NATURAL LIBRE
        # Si no es un comando con prefijo, enviarlo como diálogo natural a Vector
        limpio = txt.replace(f"<@{self.user.id}>", "").strip()
        if limpio:
            self.q_out.put(("discord", f"{message.author.display_name}: {limpio}"))
            await message.add_reaction("🤖")

    async def enviar_alerta_centinela(self, titulo: str, descripcion: str, ruta_foto: Optional[str] = None):
        """Envía una alerta de seguridad al canal de Discord con foto adjunta."""
        if not self.target_channel:
            self.target_channel = self.get_channel(self.channel_id)
        if not self.target_channel:
            return
        try:
            embed = discord.Embed(title=f"🚨 ALERTA CENTINELA: {titulo}", description=descripcion, color=0xFF4757)
            embed.set_footer(text=f"Vector Ultra Guard • {time.strftime('%H:%M:%S')}")
            if ruta_foto and Path(ruta_foto).exists():
                with open(ruta_foto, "rb") as f:
                    archivo = discord.File(f, filename="alerta_seguridad.jpg")
                    embed.set_image(url="attachment://alerta_seguridad.jpg")
                    await self.target_channel.send(file=archivo, embed=embed)
            else:
                await self.target_channel.send(embed=embed)
        except Exception as e:
            print("Error enviando alerta centinela:", e)

_bot_instancia: Optional[VectorDiscordBot] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None

def iniciar_discord_2(q_out: "queue.Queue") -> threading.Thread:
    """Inicia el bot de Discord 2.0 en un hilo asyncio dedicado."""
    global _bot_instancia, _bot_loop
    _bot_instancia = VectorDiscordBot(q_out, DISCORD_CHANNEL_ID)

    def _run():
        global _bot_loop
        _bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_bot_loop)
        try:
            _bot_loop.run_until_complete(_bot_instancia.start(DISCORD_BOT_TOKEN))
        except Exception as e:
            print("Aviso Discord 2.0:", e)

    t = threading.Thread(target=_run, daemon=True, name="DiscordBot2")
    t.start()
    return t

def notificar_centinela_discord(titulo: str, descripcion: str, ruta_foto: Optional[str] = None) -> None:
    """Función sincronizada para invocar la alerta de centinela desde cualquier hilo."""
    if _bot_instancia and _bot_loop and _bot_loop.is_running():
        asyncio.run_coroutine_threadsafe(
            _bot_instancia.enviar_alerta_centinela(titulo, descripcion, ruta_foto),
            _bot_loop
        )

if __name__ == "__main__":
    t = iniciar_discord_2(discord_command_queue)
    t.join()
