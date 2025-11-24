# !/usr/bin/env python3
"""
client_gui_text.py - VERSIÓN CORREGIDA CON TURNOS ESTRICTOS
"""

import argparse
import json
import queue
import logging
import tkinter as tk
from tkinter import scrolledtext
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
import socket
import random
import requests

# --- Configuración / CLI ---
parser = argparse.ArgumentParser(description="MQTT Battleship client (text+grid GUI)")
parser.add_argument("--broker", default="127.0.0.1", help="MQTT broker host or IP")
parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
parser.add_argument("--room", default="room1", help="Room id")
parser.add_argument("--player", default="p1", help="Player id (unique per instance)")
parser.add_argument("--qos", type=int, default=1, choices=[0,1,2], help="MQTT QoS")
args = parser.parse_args()

BROKER = args.broker
PORT = args.port
ROOM = args.room
PLAYER = args.player
QOS = args.qos

TOPIC_MOVE = f"game/{ROOM}/player/{PLAYER}/move"
TOPIC_ALL_MOVES = f"game/{ROOM}/player/+/move"
TOPIC_SERVER_RESULT = f"game/{ROOM}/server/#"
TOPIC_ALL_BOARDS = f"game/{ROOM}/player/+/board"
TOPIC_MY_BOARD = f"game/{ROOM}/player/{PLAYER}/board"

CLIENT_ID = f"client-{PLAYER}-{socket.gethostname()}-{random.randint(0,9999)}"

logging.basicConfig(level=logging.INFO)
msgq = queue.Queue()

# ---------- Utilidades de coordenadas ----------
ROWS = "ABCDEFGHIJ"
COLS = list(range(1, 11))

def coord_to_index(coord: str):
    """Convierte 'A5' -> (4,0) - CORREGIDO: Letra=Columna, Número=Fila"""
    if not coord or len(coord) < 2:
        raise ValueError("invalid coord")
    
    col_char = coord[0].upper()
    row_str = coord[1:]
    
    if col_char not in ROWS:
        raise ValueError("invalid column")
    try:
        row = int(row_str)
    except:
        raise ValueError("invalid row")
    if row < 1 or row > 10:
        raise ValueError("row out of range")
    
    c = ROWS.index(col_char)
    r = row - 1
    return (r, c)

def index_to_coord(r, c):
    return f"{ROWS[c]}{r+1}"

# ---------- GridBoard ----------
class GridBoard(tk.Canvas):
    def __init__(self, parent, size=10, cell=40, pad=6, title=None, *args, **kwargs):
        self.label_size = 20
        width = size * cell + pad*2 + self.label_size
        height = size * cell + pad*2 + self.label_size
        super().__init__(parent, width=width, height=height, bg="white", highlightthickness=0, *args, **kwargs)
        self.size = size
        self.cell = cell
        self.pad = pad
        self.title = title
        self.states = {}
        self.rects = {}
        self._draw_grid()
        if title:
            self.create_text(width//2, 8, text=title, anchor="n", font=("Arial", 10, "bold"))

    def _draw_grid(self):
        for c in range(self.size):
            x0 = self.pad + self.label_size + c*self.cell + self.cell//2
            y0 = self.pad + 5
            self.create_text(x0, y0, text=ROWS[c], font=("Arial", 10, "bold"))

        for r in range(self.size):
            x0 = self.pad + 8
            y0 = self.pad + self.label_size + r*self.cell + self.cell//2
            self.create_text(x0, y0, text=str(r+1), font=("Arial", 10, "bold"))

        for r in range(self.size):
            for c in range(self.size):
                x0 = self.pad + self.label_size + c*self.cell
                y0 = self.pad + self.label_size + r*self.cell
                x1 = x0 + self.cell
                y1 = y0 + self.cell
                rect = self.create_rectangle(x0, y0, x1, y1, fill="#e9e9e9", outline="#8c8c8c")
                self.rects[(r,c)] = rect
                self.states[(r,c)] = "empty"

    def set_cell(self, coord, state):
        if isinstance(coord, str):
            try:
                r,c = coord_to_index(coord)
            except Exception as e:
                return
        else:
            r,c = coord
        
        if (r,c) not in self.rects:
            return
        
        # COLORES MÁS CONTRASTANTES
        if state == "own_ship":
            color = "yellow"
            outline = "black"
        elif state == "hit":
            color = "red"
            outline = "darkred"
        elif state == "miss":
            color = "lightblue"
            outline = "blue"
        elif state == "sunk":
            color = "darkred"
            outline = "black"
        elif state == "pending":
            color = "gray"
            outline = "black"
        else:
            color = "lightgray"
            outline = "black"
        
        rect = self.rects[(r,c)]
        self.itemconfig(rect, fill=color, outline=outline, width=2)
        self.states[(r,c)] = state

    def clear(self):
        for k in list(self.states.keys()):
            self.set_cell(k, "empty")

# ---------- MQTT callbacks ----------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        msgq.put(("__SYSTEM__", "Connected to broker"))
        client.subscribe(TOPIC_ALL_MOVES, qos=QOS)
        client.subscribe(TOPIC_SERVER_RESULT, qos=QOS)
        client.subscribe(TOPIC_ALL_BOARDS, qos=QOS)
    else:
        msgq.put(("__SYSTEM__", f"Connection error rc={rc}"))

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
    except:
        payload = str(msg.payload)
    msgq.put((msg.topic, payload))

def on_subscribe(client, userdata, mid, granted_qos, properties=None):
    msgq.put(("__SYSTEM__", f"Subscribed mid={mid} qos={granted_qos}"))

def on_publish(client, userdata, mid):
    msgq.put(("__SYSTEM__", f"PUBACK mid={mid}"))

def on_disconnect(client, userdata, rc):
    msgq.put(("__SYSTEM__", f"Disconnected rc={rc}"))

# ---------- GUI App ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Jugador {PLAYER} – {ROOM}")
        
        # 🔥 Estado interno MEJORADO con protección contra disparos múltiples
        self.last_shot = None
        self.my_turn = False
        self.waiting_response = False  # 🆕 NUEVO: Previene disparos mientras esperamos respuesta
        self.client = None

        # Header
        header = tk.Frame(self)
        header.pack(fill="x", padx=6, pady=(6,0))
        tk.Label(header, text=f"Jugador: {PLAYER}    Sala: {ROOM}    Broker: {BROKER}:{PORT}").pack(anchor="w")

        # Área de envío
        entryframe = tk.Frame(self)
        entryframe.pack(fill="x", padx=6, pady=(6,0))
        tk.Label(entryframe, text="Coordenada a enviar (ej. B5):").pack(side="left")
        self.entry = tk.Entry(entryframe, width=10)
        self.entry.pack(side="left", padx=(6,0))
        self.sendbtn = tk.Button(entryframe, text="Enviar", command=self.send_coord)
        self.sendbtn.pack(side="left", padx=(6,8))
        startbtn = tk.Button(entryframe, text="Start Game", command=self.start_game)
        startbtn.pack(side="left")

        # Inicialmente deshabilitado hasta que sea nuestro turno
        self.entry.config(state='disabled')
        self.sendbtn.config(state='disabled')

        # Área de mensajes
        tk.Label(self, text="Mensajes entrantes:").pack(anchor="w", padx=6, pady=(6,0))
        self.inbox = scrolledtext.ScrolledText(self, height=10, width=90)
        self.inbox.pack(padx=6, pady=(0,6))
        self.inbox.configure(state='disabled')

        tk.Label(self, text="Estado:").pack(anchor="w", padx=6)
        self.status = scrolledtext.ScrolledText(self, height=5, width=90)
        self.status.pack(padx=6, pady=(0,6))
        self.status.configure(state='disabled')

        # Tableros
        boards = tk.Frame(self)
        boards.pack(padx=6, pady=6, fill="both", expand=True)
        left = tk.Frame(boards)
        left.pack(side="left", padx=8)
        right = tk.Frame(boards)
        right.pack(side="left", padx=8)

        self.myboard = GridBoard(left, title="Mi Tablero (Mis Barcos + Impactos Recibidos)", cell=40)
        self.myboard.pack(fill="both", expand=True)
        self.enemyboard = GridBoard(right, title="Tablero Enemigo (Mis Disparos)", cell=40)
        self.enemyboard.pack(fill="both", expand=True)

        self.after(100, self.poll_queue)

    def append_inbox(self, text):
        self.inbox.configure(state='normal')
        self.inbox.insert(tk.END, text + "\n")
        self.inbox.see(tk.END)
        self.inbox.configure(state='disabled')

    def append_status(self, text):
        ts = datetime.now(timezone.utc).isoformat()[:19]
        self.status.configure(state='normal')
        self.status.insert(tk.END, f"{ts}  {text}\n")
        self.status.see(tk.END)
        self.status.configure(state='disabled')

    def start_game(self):
        try:
            other = "p2" if PLAYER == "p1" else "p1"
            payload = {"room": ROOM, "players": [PLAYER, other]}
            url = f"http://127.0.0.1:5000/create_room"
            r = requests.post(url, json=payload, timeout=5)
            if r.status_code in (200,201):
                self.append_status(f"Start Game: sala creada {ROOM} players {payload['players']}")
            else:
                self.append_status(f"Start Game: respuesta {r.status_code} {r.text}")
        except Exception as e:
            self.append_status(f"Error Start Game: {e}")

    def send_coord(self):
        # 🔥 PROTECCIÓN CRÍTICA: Verificar múltiples condiciones
        if not self.my_turn:
            self.append_status("❌ No es tu turno. Espera a que el oponente juegue.")
            return
        
        if self.waiting_response:
            self.append_status("⏳ Esperando respuesta del servidor... No envíes otro disparo.")
            return

        coord = self.entry.get().strip().upper()
        if not coord:
            return
        
        # Validación
        if len(coord) < 2 or len(coord) > 3:
            self.append_status(f"Coordenada inválida: {coord}. Use formato como A1, B5, etc.")
            self.entry.delete(0, tk.END)
            return
        
        if not coord[0].isalpha():
            self.append_status(f"Coordenada inválida: {coord}. La primera letra debe ser A-J.")
            self.entry.delete(0, tk.END)
            return
        
        if not coord[1:].isdigit():
            self.append_status(f"Coordenada inválida: {coord}. Los números deben ser 1-10.")
            self.entry.delete(0, tk.END)
            return
        
        try:
            r, c = coord_to_index(coord)
            if not (0 <= r < 10 and 0 <= c < 10):
                self.append_status(f"Coordenada fuera de rango: {coord}. Use A1-J10.")
                self.entry.delete(0, tk.END)
                return
        except Exception as e:
            self.append_status(f"Coordenada inválida: {coord}. Error: {e}")
            self.entry.delete(0, tk.END)
            return

        # 🔥 DESHABILITAR INTERFAZ INMEDIATAMENTE
        self.waiting_response = True
        self.entry.config(state='disabled')
        self.sendbtn.config(state='disabled')

        # Marcar como pendiente
        try:
            self.enemyboard.set_cell(coord, "pending")
            self.append_status(f"🎯 Disparando a {coord}... esperando confirmación")
            self.last_shot = coord
        except Exception as e:
            self.append_status(f"Error marcando coordenada: {e}")

        # Enviar movimiento
        payload = {
            "player_id": PLAYER,
            "coordinate": coord,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            self.client.publish(TOPIC_MOVE, json.dumps(payload), qos=QOS)
            self.append_status(f"📤 ENVIADO -> {payload}")
        except Exception as e:
            self.append_status(f"Error publicando move: {e}")
            # Revertir estado en caso de error
            self.waiting_response = False
            try:
                self.enemyboard.set_cell(coord, "empty")
                self.last_shot = None
            except Exception:
                pass
            # Re-habilitar si seguimos siendo nuestro turno
            if self.my_turn:
                self.entry.config(state='normal')
                self.sendbtn.config(state='normal')
        
        self.entry.delete(0, tk.END)

    def connect_mqtt(self):
        client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_publish = on_publish
        client.on_subscribe = on_subscribe
        client.on_disconnect = on_disconnect
        client.enable_logger()
        
        try:
            client.will_set(f"game/{ROOM}/status", json.dumps({"player_id": PLAYER, "status": "OFFLINE"}), qos=QOS, retain=True)
        except TypeError:
            client.will_set(f"game/{ROOM}/status", json.dumps({"player_id": PLAYER, "status": "OFFLINE"}), qos=QOS)
        
        try:
            client.connect(BROKER, PORT, keepalive=60)
            client.loop_start()
            self.client = client
            self.append_status("Conectado al broker MQTT")
        except Exception as e:
            self.append_status(f"Error al conectar al broker: {e}")

    def poll_queue(self):
        while not msgq.empty():
            topic, payload = msgq.get_nowait()
            
            if topic == "__SYSTEM__":
                self.append_status(payload)
                continue

            self.append_inbox(f"[{topic}] {payload}")

            # PROCESAR SERVER/RESULT
            if topic.startswith(f"game/{ROOM}/server/"):
                try:
                    d = json.loads(payload)
                except Exception as e:
                    self.append_status(f"Error parseando server/result: {e}")
                    continue

                # MANEJAR MENSAJE DE INICIO DEL JUEGO
                if d.get("result") == "start":
                    self.append_status("🎮 JUEGO INICIADO - Sincronizando turnos...")
                    next_turn = d.get("next_turn")
                    if next_turn:
                        self.append_status(f"📢 PRIMER TURNO ASIGNADO: {next_turn}")
                        if next_turn == PLAYER:
                            self.append_status("🎯 ¡COMIENZAS TÚ! - Puedes disparar ahora")
                            self.my_turn = True
                            self.waiting_response = False
                            self.entry.config(state='normal')
                            self.sendbtn.config(state='normal')
                        else:
                            self.append_status("⏳ EL OPONENTE COMIENZA - Espera tu turno...")
                            self.my_turn = False
                            self.waiting_response = False
                            self.entry.config(state='disabled')
                            self.sendbtn.config(state='disabled')
                    continue

                # Manejar mensajes de error
                if "error" in d:
                    error_msg = d.get("error")
                    self.append_status(f"Error del servidor: {error_msg}")
                    
                    # 🔥 RESTABLECER waiting_response en caso de error
                    self.waiting_response = False
                    
                    if error_msg == "not_your_turn":
                        current_turn = d.get("current_turn") or d.get("current turn", "desconocido")
                        self.append_status(f"No es tu turno. Turno actual: {current_turn}")
                        
                        # LIMPIEZA CRÍTICA: Si tenemos un last_shot pendiente, limpiarlo
                        if self.last_shot:
                            try:
                                self.enemyboard.set_cell(self.last_shot, "empty")
                                self.append_status(f"🗑️ Limpiando disparo no permitido en {self.last_shot}")
                            except Exception as e:
                                self.append_status(f"Error limpiando disparo: {e}")
                            finally:
                                self.last_shot = None
                        
                        # Actualizar estado de turno
                        self.my_turn = False
                        self.entry.config(state='disabled')
                        self.sendbtn.config(state='disabled')
                    continue

                # Validar campos requeridos para mensajes de resultado normales
                attacker = d.get("player") or d.get("player_id")
                target = d.get("target") or d.get("target_id")
                coord = d.get("coordinate")
                res = d.get("result")
                
                if not all([attacker, target, coord, res]):
                    self.append_status(f"⚠️ Mensaje server/result con campos incompletos: {d}")
                    if coord and res:
                        self.append_status(f"Procesando disparo en {coord}: {res}")
                    else:
                        continue

                sunk = d.get("sunk", False)
                ship_cells = d.get("ship_cells") or d.get("ship cells") or []

                self.append_status(f"Procesando: {attacker} -> {target} en {coord}: {res}")

                # 🔥 RESTABLECER waiting_response al recibir respuesta
                if attacker == PLAYER:
                    self.waiting_response = False

                # LÓGICA DE ACTUALIZACIÓN
                try:
                    # Si YO fui el ATACANTE -> actualizar TABLERO ENEMIGO
                    if attacker == PLAYER:
                        if res == "hit":
                            self.enemyboard.set_cell(coord, "hit")
                            self.append_status(f"🎯 ¡IMPACTO CONFIRMADO en {coord}!")
                        elif res == "miss":
                            self.enemyboard.set_cell(coord, "miss")
                            self.append_status(f"💧 Disparo fallido en {coord}")
                        
                        if sunk and ship_cells:
                            for cc in ship_cells:
                                self.enemyboard.set_cell(cc, "sunk")
                            self.append_status(f"🔥 ¡BARCO HUNDIDO! en: {ship_cells}")

                    # Si YO fui el DEFENSOR -> actualizar MI TABLERO
                    if target == PLAYER:
                        if res == "hit":
                            self.myboard.set_cell(coord, "hit")
                            self.append_status(f"💥 ¡TE IMPACTARON en {coord}!")
                        elif res == "miss":
                            self.myboard.set_cell(coord, "miss")
                            self.append_status(f"🌊 El enemigo falló en {coord}")
                        
                        if sunk and ship_cells:
                            for cc in ship_cells:
                                self.myboard.set_cell(cc, "sunk")
                            self.append_status(f"💀 ¡TE HUNDIERON un barco en: {ship_cells}!")
                            
                except Exception as e:
                    self.append_status(f"❌ Error actualizando tableros: {e}")

                # Limpiar last_shot si fue procesado (solo si somos el atacante)
                if attacker == PLAYER and self.last_shot == coord:
                    self.last_shot = None

                # 🔥 SINCRONIZACIÓN DE TURNOS - CRÍTICO
                next_turn = d.get("next_turn") or d.get("next turn")
                if next_turn:
                    self.append_status(f"🔄 SINCRO TURNO: {next_turn}")
                    
                    # Actualizar estado interno de turno
                    if next_turn == PLAYER:
                        self.append_status("🎮 ¡ES TU TURNO! - Puedes disparar")
                        self.my_turn = True
                        self.waiting_response = False
                        # Habilitar interfaz para disparar
                        self.entry.config(state='normal')
                        self.sendbtn.config(state='normal')
                    else:
                        self.append_status("⏳ Turno del oponente - Espera...")
                        self.my_turn = False
                        self.waiting_response = False
                        # Deshabilitar interfaz temporalmente
                        self.entry.config(state='disabled')
                        self.sendbtn.config(state='disabled')
                    
                    # Forzar actualización visual
                    self.update_idletasks()
                        
                if d.get("victory"):
                    winner = d.get("player") or "Alguien"
                    self.append_status(f"🏆 ¡PARTIDA TERMINADA! Ganador: {winner}")

                continue

            # PROCESAR BOARD
            if topic.startswith(f"game/{ROOM}/player/") and topic.endswith("/board"):
                try:
                    bd = json.loads(payload)
                except Exception as e:
                    self.append_status(f"Error parseando board: {e}")
                    continue
                
                parts = topic.split('/')
                if len(parts) >= 4:
                    board_player = parts[3]
                else:
                    board_player = None

                # Si es MI tablero -> pintar MIS BARCOS
                if board_player == PLAYER:
                    ships = bd.get("ships", "hidden")
                    
                    if ships and ships != "hidden":
                        self.append_status("Actualizando mi tablero con barcos...")
                        for ship in ships:
                            ship_cells = ship.get("cells", [])
                            for coord in ship_cells:
                                self.myboard.set_cell(coord, "own_ship")
                    
                    # Pintar los impactos recibidos desde el board
                    shots_received = bd.get("shots", [])
                    for coord in shots_received:
                        # Determinar si fue hit o miss
                        is_hit = False
                        if ships and ships != "hidden":
                            for ship in ships:
                                if coord in ship.get("cells", []):
                                    is_hit = True
                                    break
                        
                        if is_hit:
                            self.myboard.set_cell(coord, "hit")
                            self.append_status(f"Impacto recibido en {coord}")
                        else:
                            self.myboard.set_cell(coord, "miss")
                            self.append_status(f"Disparo fallido recibido en {coord}")
                
                continue

            self.append_status(f"Topic ignorado: {topic}")

        self.after(100, self.poll_queue)

def main():
    app = App()
    app.connect_mqtt()
    try:
        app.mainloop()
    finally:
        try:
            if app.client:
                app.client.loop_stop()
                app.client.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()