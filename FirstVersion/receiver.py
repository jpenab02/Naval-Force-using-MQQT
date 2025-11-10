# receiver.py
import tkinter as tk
from tkinter import scrolledtext
import paho.mqtt.client as mqtt
import json
import queue

# --------- CONFIG (ajusta aquí si tu broker no es localhost) ----------
BROKER = "localhost"        # o IP del broker
PORT = 1883
ROOM = "room1"
PLAYER_ID = "p1"            # id local (solo para mostrar)
QOS = 1
# ---------------------------------------------------------------------

# topics que queremos observar
TOPIC_SERVER_RESULT = f"game/{ROOM}/server/#"
TOPIC_ALL_MOVES = f"game/{ROOM}/player/+/move"

msg_queue = queue.Queue()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        msg_queue.put(("__SYSTEM__", "Conectado al broker MQTT"))
        client.subscribe(TOPIC_SERVER_RESULT, qos=QOS)
        client.subscribe(TOPIC_ALL_MOVES, qos=QOS)
    else:
        msg_queue.put(("__SYSTEM__", f"Error conexion (rc={rc})"))

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode('utf-8')
    except:
        payload = str(msg.payload)
    # Enviamos al queue para que lo procese la GUI en su hilo
    msg_queue.put((msg.topic, payload))

# --- GUI ---
root = tk.Tk()
root.title(f"Receiver - {PLAYER_ID}")

tk.Label(root, text="Mensajes entrantes:").pack(anchor="w", padx=8, pady=(8,0))
inbox = scrolledtext.ScrolledText(root, height=20, width=80)
inbox.pack(padx=8, pady=(0,8))
inbox.configure(state='disabled')

tk.Label(root, text="(Los mensajes del broker aparecerán aquí)").pack(anchor="w", padx=8, pady=(0,8))

# --- MQTT client ---
client = mqtt.Client(client_id=f"receiver-{PLAYER_ID}")
client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(BROKER, PORT, 60)
    client.loop_start()
except Exception as e:
    inbox.configure(state='normal')
    inbox.insert(tk.END, f"Error al conectar broker: {e}\n")
    inbox.configure(state='disabled')

def poll_queue():
    while not msg_queue.empty():
        topic, payload = msg_queue.get_nowait()
        inbox.configure(state='normal')
        inbox.insert(tk.END, f"[{topic}] {payload}\n")
        inbox.see(tk.END)
        inbox.configure(state='disabled')
    root.after(100, poll_queue)

root.after(100, poll_queue)
root.mainloop()

try:
    client.loop_stop()
    client.disconnect()
except:
    pass
