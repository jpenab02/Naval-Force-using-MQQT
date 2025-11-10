# client.py
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

logging.basicConfig(level=logging.INFO)

# ---------- Config vía CLI ----------
parser = argparse.ArgumentParser(description="MQTT Battleship client (GUI)")
parser.add_argument("--broker", default="localhost", help="MQTT broker host or IP")
parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
parser.add_argument("--room", default="room1", help="Room id")
parser.add_argument("--player", default="p1", help="Player id (unique per instance)")
parser.add_argument("--qos", type=int, default=1, choices=[0,1,2], help="MQTT QoS")
args = parser.parse_args()

BROKER = args.broker
PORT = args.port
ROOM = args.room
PLAYER_ID = args.player
QOS = args.qos

TOPIC_MOVE = f"game/{ROOM}/player/{PLAYER_ID}/move"
TOPIC_ALL_MOVES = f"game/{ROOM}/player/+/move"
TOPIC_SERVER_RESULT = f"game/{ROOM}/server/#"
TOPIC_BOARD = f"game/{ROOM}/player/{PLAYER_ID}/board"

# Generate a reasonably unique client id
CLIENT_ID = f"client-{PLAYER_ID}-{socket.gethostname()}-{random.randint(0,9999)}"

msg_queue = queue.Queue()

# ---------- helpers ----------
def coord_to_index(coord: str):
    """Convierte 'B5' a (row_index, col_index) con 0-based.
       A-J columnas, 1-10 filas. Acepta 'A10' también."""
    if not isinstance(coord, str):
        raise ValueError("coord debe ser string")
    coord = coord.strip().upper()
    if len(coord) < 2:
        raise ValueError("coord formato inválido")
    # letras al inicio (puede ser varias si formato distinto)
    # asumir letra(s) primero y número después: e.g. 'A10'
    letter_part = ''
    number_part = ''
    for ch in coord:
        if ch.isalpha():
            letter_part += ch
        else:
            number_part += ch
    if not letter_part or not number_part:
        raise ValueError("coord formato inválido")
    col = ord(letter_part[0]) - ord('A')
    row = int(number_part) - 1
    if not (0 <= col < 26 and 0 <= row < 100):
        raise ValueError("coord fuera de rango")
    return (row, col)

# ---------- MQTT callbacks ----------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        msg_queue.put(("__SYSTEM__", "Conectado al broker MQTT"))
        # subscribe to topics
        client.subscribe(TOPIC_ALL_MOVES, qos=QOS)
        client.subscribe(TOPIC_SERVER_RESULT, qos=QOS)
        client.subscribe(TOPIC_BOARD, qos=QOS)
    else:
        msg_queue.put(("__SYSTEM__", f"Error conexión rc={rc}"))

def on_subscribe(client, userdata, mid, granted_qos, properties=None):
    msg_queue.put(("__SYSTEM__", f"Suscripción OK mid={mid} qos={granted_qos}"))

def on_publish(client, userdata, mid):
    msg_queue.put(("__SYSTEM__", f"PUBACK recibido mid={mid}"))

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
    except:
        payload = str(msg.payload)
    # put topic and payload into queue for GUI
    msg_queue.put((msg.topic, payload))

def on_disconnect(client, userdata, rc):
    msg_queue.put(("__SYSTEM__", f"Desconectado del broker (rc={rc})"))

# ---------- GUI ----------
root = tk.Tk()
root.title(f"Jugador {PLAYER_ID} — {ROOM}")

tk.Label(root, text=f"Jugador: {PLAYER_ID}    Sala: {ROOM}    Broker: {BROKER}:{PORT}").pack(padx=6, pady=(6,0), anchor="w")

tk.Label(root, text="Coordenada a enviar (ej. B5):").pack(padx=6, pady=(6,0), anchor="w")
entry = tk.Entry(root, width=10)
entry.pack(padx=6, pady=(0,6), anchor="w")

def send_action():
    coord = entry.get().strip().upper()
    if not coord:
        return
    payload = {
        "player_id": PLAYER_ID,
        "coordinate": coord,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    payload_str = json.dumps(payload)
    client.publish(TOPIC_MOVE, payload_str, qos=QOS)
    append_status(f"ENVIADO -> {payload_str}")
    entry.delete(0, tk.END)

send_btn = tk.Button(root, text="Enviar", width=12, command=send_action)
send_btn.pack(padx=6, pady=(0,8), anchor="w")

# Logs area
tk.Label(root, text="Mensajes entrantes:").pack(padx=6, anchor="w")
inbox = scrolledtext.ScrolledText(root, height=10, width=70)
inbox.pack(padx=6, pady=(0,8))
inbox.configure(state='disabled')

tk.Label(root, text="Estado:").pack(padx=6, anchor="w")
status_box = scrolledtext.ScrolledText(root, height=6, width=70)
status_box.pack(padx=6, pady=(0,8))
status_box.configure(state='disabled')

def append_inbox(text):
    inbox.configure(state='normal')
    inbox.insert(tk.END, text + "\n")
    inbox.see(tk.END)
    inbox.configure(state='disabled')

def append_status(text):
    status_box.configure(state='normal')
    status_box.insert(tk.END, text + "\n")
    status_box.see(tk.END)
    status_box.configure(state='disabled')

# ---------- Board canvases ----------
BOARD_SIZE = 10
CELL = 28
PADDING = 10

frame_boards = tk.Frame(root)
frame_boards.pack(padx=6, pady=6)

# Own board
own_frame = tk.Frame(frame_boards)
own_frame.grid(row=0, column=0, padx=8)
tk.Label(own_frame, text="Mi Tablero").pack()
own_canvas = tk.Canvas(own_frame, width=BOARD_SIZE*CELL, height=BOARD_SIZE*CELL, bg='white')
own_canvas.pack()
# enemy board
enemy_frame = tk.Frame(frame_boards)
enemy_frame.grid(row=0, column=1, padx=8)
tk.Label(enemy_frame, text="Tablero Enemigo").pack()
enemy_canvas = tk.Canvas(enemy_frame, width=BOARD_SIZE*CELL, height=BOARD_SIZE*CELL, bg='white')
enemy_canvas.pack()

# create grid rects and store ids
own_cells = {}
enemy_cells = {}

for r in range(BOARD_SIZE):
    for c in range(BOARD_SIZE):
        x0 = c*CELL; y0 = r*CELL; x1 = x0+CELL; y1 = y0+CELL
        rect = own_canvas.create_rectangle(x0, y0, x1, y1, fill='lightgray', outline='black')
        own_cells[(r,c)] = rect
        rect2 = enemy_canvas.create_rectangle(x0, y0, x1, y1, fill='lightgray', outline='black')
        enemy_cells[(r,c)] = rect2

def set_own_cell_by_coord(coord, kind):
    """kind in {'own_ship','hit','miss'}"""
    try:
        r,c = coord_to_index(coord)
    except Exception:
        return
    rect = own_cells.get((r,c))
    if not rect: return
    if kind == "own_ship":
        own_canvas.itemconfig(rect, fill='yellow')
    elif kind == "hit":
        own_canvas.itemconfig(rect, fill='red')
    elif kind == "miss":
        own_canvas.itemconfig(rect, fill='white')
    else:
        own_canvas.itemconfig(rect, fill='lightgray')

def set_enemy_cell_by_coord(coord, kind):
    """kind in {'hit','miss'}"""
    try:
        r,c = coord_to_index(coord)
    except Exception:
        return
    rect = enemy_cells.get((r,c))
    if not rect: return
    if kind == "hit":
        enemy_canvas.itemconfig(rect, fill='red')
    elif kind == "miss":
        enemy_canvas.itemconfig(rect, fill='lightblue')
    else:
        enemy_canvas.itemconfig(rect, fill='lightgray')

# ---------- MQTT client setup ----------
client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
client.on_connect = on_connect
client.on_message = on_message
client.on_publish = on_publish
client.on_subscribe = on_subscribe
client.on_disconnect = on_disconnect
client.enable_logger()

# Last Will
client.will_set(f"game/{ROOM}/status", json.dumps({"player_id": PLAYER_ID, "status":"OFFLINE"}), qos=QOS, retain=True)

# connect
try:
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()
except Exception as e:
    append_status(f"Error al conectar broker: {e}")

# ---------- helper to normalize board payload ----------
def normalize_ships(ships):
    """Convierte varias representaciones a [[A1,A2],[C3,C4],...]"""
    norm = []
    if ships is None or ships == "hidden":
        return norm
    if isinstance(ships, list):
        for item in ships:
            if isinstance(item, dict):
                cells = item.get("cells") or item.get("positions") or item.get("coords")
                if isinstance(cells, list):
                    norm.append(cells)
            elif isinstance(item, list):
                norm.append(item)
            elif isinstance(item, str):
                norm.append([item])
    elif isinstance(ships, dict):
        cells = ships.get("cells") or ships.get("positions")
        if isinstance(cells, list):
            norm.append(cells)
    return norm

# ---------- process messages from queue ----------
def handle_board_payload(payload_str):
    """Payload_str es JSON string con keys: size, shots, ships (o 'hidden')"""
    try:
        bd = json.loads(payload_str)
    except Exception:
        # si ya es dict por algún camino
        try:
            bd = payload_str
            if isinstance(bd, str):
                bd = json.loads(bd)
        except Exception as e:
            append_status(f"Error parseando board JSON: {e}")
            return

    ships = bd.get("ships", "hidden")
    shots = bd.get("shots", [])

    append_status(f"[DEBUG BOARD PAYLOAD] {bd}")

    # Limpia own board display (volver a color base)
    for (r,c), rect in own_cells.items():
        own_canvas.itemconfig(rect, fill='lightgray')
    for (r,c), rect in enemy_cells.items():
        enemy_canvas.itemconfig(rect, fill='lightgray')

    # Pintar own ships
    norm = normalize_ships(ships)
    for ship in norm:
        for coord in ship:
            set_own_cell_by_coord(coord, "own_ship")

    # pintar disparos propios/recibidos (shots en payload son coordenadas disparadas)
    for s in shots:
        # no sabemos si shots son tus disparos o disparos recibidos; asumimos para "mi tablero" son disparos RECIBIDOS
        # marque hits con red si cell coincide con ship (we can infer by checking ship cells)
        in_ship = False
        try:
            r,c = coord_to_index(s)
        except Exception:
            r = c = None
        # check if s in any ship
        for ship in norm:
            if s in ship:
                in_ship = True
                break
        if in_ship:
            set_own_cell_by_coord(s, "hit")
        else:
            set_own_cell_by_coord(s, "miss")

def handle_server_result(payload_str):
    """Cuando llega server/result actualizamos tablero enemigo según resultado."""
    try:
        res = json.loads(payload_str)
    except:
        append_status(f"Malformed server result: {payload_str}")
        return
    append_status(f"[server/result] {res}")
    # Res puede contener 'coordinate', 'result' ('hit'|'miss'), 'target', 'ship_cells', etc.
    coord = res.get("coordinate")
    result = res.get("result")
    if coord and result:
        if result == "hit":
            set_enemy_cell_by_coord(coord, "hit")
        elif result == "miss":
            set_enemy_cell_by_coord(coord, "miss")
    # si hay ship_cells (sunk) marcar en enemigo
    ship_cells = res.get("ship_cells") or res.get("remaining_ship_cells")
    if ship_cells and isinstance(ship_cells, list):
        for sc in ship_cells:
            # marcar en enemigo como hit (o distinto si quieres)
            set_enemy_cell_by_coord(sc, "hit")

# ---------- GUI queue polling ----------
def poll_queue():
    while not msg_queue.empty():
        topic, payload = msg_queue.get_nowait()
        # display
        append_inbox(f"[{topic}] {payload}")
        # system messages to status
        if topic == "__SYSTEM__":
            append_status(payload)
        else:
            # handle board topic
            if topic.endswith("/board"):
                handle_board_payload(payload)
            elif "server/result" in topic:
                handle_server_result(payload)
        # continue
    root.after(100, poll_queue)

root.after(100, poll_queue)

# ---------- Run GUI ----------
try:
    root.mainloop()
finally:
    try:
        client.loop_stop()
        client.disconnect()
    except:
        pass
