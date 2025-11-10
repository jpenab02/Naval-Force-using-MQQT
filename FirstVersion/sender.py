# sender.py
import json
import tkinter as tk
from tkinter import scrolledtext
import paho.mqtt.client as mqtt
from datetime import datetime

# --------- CONFIG (ajusta aquí si tu broker no es localhost) ----------
BROKER = "localhost"        # o IP del broker, e.g. "192.168.1.10"
PORT = 1883
ROOM = "room1"
PLAYER_ID = "p1"
QOS = 1
# ---------------------------------------------------------------------

TOPIC_MOVE = f"game/{ROOM}/player/{PLAYER_ID}/move"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        status_text.insert(tk.END, "Conectado al broker MQTT\n")
    else:
        status_text.insert(tk.END, f"Error conexión (rc={rc})\n")

def send_message():
    coord = entry.get().strip()
    if not coord:
        return
    payload = {
        "player_id": PLAYER_ID,
        "coordinate": coord,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    payload_str = json.dumps(payload)
    client.publish(TOPIC_MOVE, payload_str, qos=QOS)
    status_text.insert(tk.END, f"ENVIADO -> {payload_str}\n")
    entry.delete(0, tk.END)

# --- GUI ---
root = tk.Tk()
root.title(f"Sender - {PLAYER_ID}")

tk.Label(root, text="Coordenada a enviar (ej. B5):").pack(padx=8, pady=(8,0))
entry = tk.Entry(root, width=20)
entry.pack(padx=8, pady=(0,8))

send_btn = tk.Button(root, text="Enviar", command=send_message)
send_btn.pack(padx=8, pady=(0,8))

tk.Label(root, text="Estado:").pack(anchor="w", padx=8)
status_text = scrolledtext.ScrolledText(root, height=10, width=60)
status_text.pack(padx=8, pady=(0,8))
status_text.configure(state='normal')

# --- MQTT client ---
client = mqtt.Client(client_id=f"sender-{PLAYER_ID}")
client.on_connect = on_connect
try:
    client.connect(BROKER, PORT, 60)
    client.loop_start()
except Exception as e:
    status_text.insert(tk.END, f"Error al conectar broker: {e}\n")

root.mainloop()
try:
    client.loop_stop()
    client.disconnect()
except:
    pass
