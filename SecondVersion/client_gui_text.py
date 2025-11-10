#!/usr/bin/env python3
"""
client_gui_text.py
Cliente MQTT con GUI (Tkinter) para el prototipo "Batalla Naval".
- Botón Start Game -> hace POST /create_room al servidor Flask.
- Envía movimientos por MQTT a: game/{ROOM}/player/{PLAYER}/move
- Recibe resultados en: game/{ROOM}/server/result
- Recibe tableros en: game/{ROOM}/player/{player_id}/board
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
import requests  # para Start Game (HTTP)

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
# topic for this player's board (server publishes this so client can know its shots / ships)
TOPIC_MY_BOARD = f"game/{ROOM}/player/{PLAYER}/board"

CLIENT_ID = f"client-{PLAYER}-{socket.gethostname()}-{random.randint(0,9999)}"

logging.basicConfig(level=logging.INFO)
msgq = queue.Queue()

# ---------- Utilidades de coordenadas ----------
ROWS = "ABCDEFGHIJ"  # 10x10
COLS = list(range(1, 11))

def coord_to_index(coord: str):
    """Convierte 'A5' -> (0,4). Acepta letras minúsculas."""
    if not coord or len(coord) < 2:
        raise ValueError("invalid coord")
    row_char = coord[0].upper()
    col_str = coord[1:]
    if row_char not in ROWS:
        raise ValueError("invalid row")
    try:
        c = int(col_str)
    except:
        raise ValueError("invalid col")
    if c < 1 or c > 10:
        raise ValueError("col out of range")
    r = ROWS.index(row_char)
    return (r, c-1)

def index_to_coord(r,c):
    return f"{ROWS[r]}{c+1}"

# ---------- GridBoard: pintar tablero ----------
class GridBoard(tk.Canvas):
    def __init__(self, parent, size=10, cell=28, pad=6, title=None, *args, **kwargs):
        width = size * cell + pad*2
        height = width + 24
        super().__init__(parent, width=width, height=height, bg="white", highlightthickness=0, *args, **kwargs)
        self.size = size
        self.cell = cell
        self.pad = pad
        self.title = title
        self.states = {}  # key: (r,c) -> "empty"|"own_ship"|"hit"|"miss"|"sunk"
        self.rects = {}   # mapping from (r,c) to rect id
        self.texts = {}
        self._draw_grid()
        if title:
            self.create_text(width//2, 12, text=title, anchor="n", font=("Arial", 10, "bold"))

    def _draw_grid(self):
        # coordenadas: origin pad, pad
        for r in range(self.size):
            for c in range(self.size):
                x0 = self.pad + c*self.cell
                y0 = self.pad + 20 + r*self.cell
                x1 = x0 + self.cell
                y1 = y0 + self.cell
                rect = self.create_rectangle(x0, y0, x1, y1, fill="#e9e9e9", outline="#8c8c8c")
                self.rects[(r,c)] = rect
                self.states[(r,c)] = "empty"

    def set_cell(self, coord, state):
        """coord puede ser 'A5' o (r,c). state: 'own_ship'|'hit'|'miss'|'sunk'|'empty'"""
        if isinstance(coord, str):
            try:
                r,c = coord_to_index(coord)
            except Exception:
                return
        else:
            r,c = coord
        if (r,c) not in self.rects:
            return
        rect = self.rects[(r,c)]
        prev = self.states.get((r,c), "empty")
        self.states[(r,c)] = state
        color = "#e9e9e9"
        if state == "own_ship":
            color = "#ffec99"   # amarillo suave para mis barcos
        elif state == "hit":
            color = "#ff6b6b"   # rojo
        elif state == "miss":
            color = "#8fc6ff"   # azul
        elif state == "sunk":
            color = "#b30000"   # rojo oscuro
        elif state == "empty":
            color = "#e9e9e9"
        # update rectangle color
        self.itemconfig(rect, fill=color)

    def clear(self):
        for k in list(self.states.keys()):
            self.set_cell(k, "empty")

# ---------- MQTT callbacks ----------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        msgq.put(("__SYSTEM__", "Connected to broker"))
        # subscribir a topics de interés
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
        self.title(f"Jugador {PLAYER} — {ROOM}")
        # frames
        header = tk.Frame(self)
        header.pack(fill="x", padx=6, pady=(6,0))
        tk.Label(header, text=f"Jugador: {PLAYER}    Sala: {ROOM}    Broker: {BROKER}:{PORT}").pack(anchor="w")

        # send area
        entryframe = tk.Frame(self)
        entryframe.pack(fill="x", padx=6, pady=(6,0))
        tk.Label(entryframe, text="Coordenada a enviar (ej. B5):").pack(side="left")
        self.entry = tk.Entry(entryframe, width=10)
        self.entry.pack(side="left", padx=(6,0))
        sendbtn = tk.Button(entryframe, text="Enviar", command=self.send_coord)
        sendbtn.pack(side="left", padx=(6,8))

        # Start Game button (HTTP)
        startbtn = tk.Button(entryframe, text="Start Game", command=self.start_game)
        startbtn.pack(side="left")

        # incoming messages log
        tk.Label(self, text="Mensajes entrantes:").pack(anchor="w", padx=6, pady=(6,0))
        self.inbox = scrolledtext.ScrolledText(self, height=10, width=90)
        self.inbox.pack(padx=6, pady=(0,6))
        self.inbox.configure(state='disabled')

        tk.Label(self, text="Estado:").pack(anchor="w", padx=6)
        self.status = scrolledtext.ScrolledText(self, height=5, width=90)
        self.status.pack(padx=6, pady=(0,6))
        self.status.configure(state='disabled')

        # boards area
        boards = tk.Frame(self)
        boards.pack(padx=6, pady=6, fill="both", expand=True)
        left = tk.Frame(boards)
        left.pack(side="left", padx=8)
        right = tk.Frame(boards)
        right.pack(side="left", padx=8)

        self.myboard = GridBoard(left, title="Mi Tablero")
        self.myboard.pack()
        self.enemyboard = GridBoard(right, title="Tablero Enemigo")
        self.enemyboard.pack()

        # internal state
        self.my_turn = False
        self.client = None

        # Iniciar polling
        self.after(100, self.poll_queue)

    def append_inbox(self, text):
        self.inbox.configure(state='normal')
        self.inbox.insert(tk.END, text + "\n")
        self.inbox.see(tk.END)
        self.inbox.configure(state='disabled')

    def append_status(self, text):
        ts = datetime.now(timezone.utc).isoformat()
        self.status.configure(state='normal')
        self.status.insert(tk.END, f"{ts}  {text}\n")
        self.status.see(tk.END)
        self.status.configure(state='disabled')

    def start_game(self):
        """Hace POST al servidor para crear la sala (genera barcos en el servidor)."""
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
        coord = self.entry.get().strip().upper()
        if not coord:
            return
        payload = {
            "player_id": PLAYER,
            "coordinate": coord,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            self.client.publish(TOPIC_MOVE, json.dumps(payload), qos=QOS)
            self.append_status(f"ENVIADO -> {payload}")
            # marcar provisional en enemyboard como miss (servidor actualizará a hit si corresponde)
            try:
                self.enemyboard.set_cell(coord, "miss")
            except Exception:
                pass
        except Exception as e:
            self.append_status(f"Error publicando move: {e}")
        self.entry.delete(0, tk.END)

    def connect_mqtt(self):
        """Crear y conectar cliente MQTT. Llamar antes de mainloop (o al iniciar app)."""
        client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_publish = on_publish
        client.on_subscribe = on_subscribe
        client.on_disconnect = on_disconnect
        client.enable_logger()
        # LWT para notificar desconexiones inesperadas
        try:
            client.will_set(f"game/{ROOM}/status", json.dumps({"player_id": PLAYER, "status": "OFFLINE"}), qos=QOS, retain=True)
        except TypeError:
            # compatibilidad librerías antiguas
            client.will_set(f"game/{ROOM}/status", json.dumps({"player_id": PLAYER, "status": "OFFLINE"}), qos=QOS)
        try:
            client.connect(BROKER, PORT, keepalive=60)
            client.loop_start()
            self.client = client
            self.append_status("Conectado al broker MQTT")
        except Exception as e:
            self.append_status(f"Error al conectar al broker: {e}")

    def poll_queue(self):
        """Procesa mensajes que llegan del callback MQTT (colocados en msgq)."""
        while not msgq.empty():
            topic, payload = msgq.get_nowait()
            # system messages
            if topic == "__SYSTEM__":
                self.append_status(payload)
                continue

            # show raw
            self.append_inbox(f"[{topic}] {payload}")

            # process server/result
            if topic.startswith(f"game/{ROOM}/server/"):
                try:
                    d = json.loads(payload)
                except Exception as e:
                    self.append_status(f"Error parseando server/result: {e}")
                    continue

                attacker = d.get("player") or d.get("player_id")
                target = d.get("target") or d.get("target_id")
                coord = d.get("coordinate")
                res = d.get("result")
                sunk = d.get("sunk", False)

                # If I was the attacker -> mark on enemy board
                if attacker == PLAYER and coord:
                    if res == "hit":
                        self.enemyboard.set_cell(coord, "hit")
                    elif res == "miss":
                        self.enemyboard.set_cell(coord, "miss")
                    if sunk and d.get("ship_cells"):
                        for cc in d.get("ship_cells", []):
                            self.enemyboard.set_cell(cc, "sunk")

                # If I was the defender (they shot me) -> mark my board
                if target == PLAYER and coord:
                    if res == "hit":
                        self.myboard.set_cell(coord, "hit")
                    elif res == "miss":
                        self.myboard.set_cell(coord, "miss")
                    if sunk and d.get("ship_cells"):
                        for cc in d.get("ship_cells", []):
                            self.myboard.set_cell(cc, "sunk")

                if d.get("next_turn"):
                    self.append_status(f"Siguiente turno: {d.get('next_turn')}")
                if d.get("victory"):
                    self.append_status(f"¡Partida terminada! {d}")

                continue

            # process board payloads (game/room/player/X/board)
            if topic.startswith(f"game/{ROOM}/player/") and topic.endswith("/board"):
                try:
                    bd = json.loads(payload)
                except Exception as e:
                    self.append_status(f"Error parseando board payload: {e}")
                    continue
                # bd contains shots that that player has made
                # find which player the payload is for
                parts = topic.split('/')
                if len(parts) >= 4:
                    board_player = parts[3]
                else:
                    board_player = None

                # If board is for THIS player, it describes this player's shots & (maybe) their ships.
                if board_player == PLAYER:
                    shots = bd.get("shots", [])
                    # mark my shots on the enemy board (some can be hits later updated by server/result)
                    for s in shots:
                        # only set to miss if not already hit/sunk
                        try:
                            rci = coord_to_index(s)
                            cur = self.enemyboard.states.get(rci)
                            if cur not in ("hit", "sunk"):
                                self.enemyboard.set_cell(s, "miss")
                        except Exception:
                            pass
                    # if board includes ships and reveal (when server sent reveal true), paint them on my board
                    ships = bd.get("ships", "hidden")
                    if ships and ships != "hidden":
                        for s in ships:
                            for cc in s.get("cells", []):
                                self.myboard.set_cell(cc, "own_ship")
                else:
                    # board for opponent: if they sent shots list, that means opponent's own shots -> show on your enemy board? usually ignore
                    # we typically won't paint opponent ships (they are hidden)
                    pass
                continue

            # other topics: just log
            self.append_status(f"Topic ignorado: {topic}")

        # schedule next
        self.after(100, self.poll_queue)


# ---------- Main entry ----------
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
