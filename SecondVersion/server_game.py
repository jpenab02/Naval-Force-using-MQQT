# server_game.py
import json
import os
import time
import logging
import random
from threading import Thread
from typing import Dict
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify
from game_manager import GameManager  # tu implementación existente

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# MQTT / HTTP config
BROKER = "127.0.0.1"
PORT = 1883
STATE_FILE = "rooms_state.json"
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 5000

# MQTT topics
TOPIC_MOVES = "game/+/player/+/move"
PUB_RESULT_TEMPLATE = "game/{room}/server/result"
PUB_BOARD_TEMPLATE = "game/{room}/player/{player_id}/board"

# Inicializar GameManager
gm = GameManager()
waiting_queue = []  # cola simple para matchmaking

# Flask app
app = Flask(__name__)

# ---------- utilidades ----------
def save_state():
    try:
        dump = {}
        for room_id, room in gm.rooms.items():
            dump[room_id] = {
                "turn": room.get("turn"),
                "state": room.get("state"),
                "players": list(room.get("players_list", []))
            }
        with open(STATE_FILE, "w") as f:
            json.dump(dump, f, indent=2)
        logging.info("Estado guardado en %s", STATE_FILE)
    except Exception as e:
        logging.error("Error guardando estado: %s", e)

def publish_board_for_player(mqtt_client, room, pid):
    """Publica el tablero para pid; reveal=True para que vea sus barcos."""
    try:
        board_payload = gm.get_board_summary(room, pid, reveal=True)
        mqtt_client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=pid),
                            json.dumps(board_payload), qos=1)
        logging.info("Publicado tablero MQTT para %s (room=%s)", pid, room)
    except Exception as e:
        logging.exception("Error publicando tablero para %s: %s", pid, e)

def publish_board_all(mqtt_client, room):
    players = gm.rooms[room]["players_list"]
    for pid in players:
        publish_board_for_player(mqtt_client, room, pid)

# ---------- MQTT callbacks ----------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logging.info("Servidor conectado al broker MQTT")
        client.subscribe(TOPIC_MOVES, qos=1)
        logging.info("Suscrito a %s", TOPIC_MOVES)
    else:
        logging.error("Error de conexión al broker, rc=%s", rc)

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        logging.info("Mensaje recibido topic=%s payload=%s", msg.topic, payload)
        data = json.loads(payload)
    except Exception as e:
        logging.exception("Payload inválido o no JSON: %s", e)
        return

    # Extraer control topics? (No, aquí solo moves)
    parts = msg.topic.split('/')
    if not (len(parts) >= 5 and parts[0] == "game" and parts[2] == "player" and parts[4] == "move"):
        logging.debug("Tópico no es move: %s", msg.topic)
        return

    room = parts[1]
    player_id = parts[3]
    coord = data.get("coordinate")
    if coord is None:
        logging.warning("Payload sin campo 'coordinate'. Ignorando.")
        return

    # Si la sala no existe -> error (decidimos NO crearla automáticamente cuando usamos HTTP)
    if room not in gm.rooms:
        logging.warning("Room %s no encontrada (en on_message). Ignorando jugada de %s", room, player_id)
        # podemos responder por MQTT con error
        err = {"error": "room_not_found", "room": room}
        client.publish(PUB_RESULT_TEMPLATE.format(room=room), json.dumps(err), qos=1)
        return

    # Procesar jugada
    result = gm.handle_move(room, player_id, coord)

    # --- Garantizar que el turno (next_turn) se aplique al estado del servidor ---
    try:
        next_turn = result.get("next_turn")
        if next_turn:
            # seguridad: solo actualizar si la sala existe y tiene players_list
            if room in gm.rooms and "players_list" in gm.rooms[room]:
                gm.rooms[room]["turn"] = next_turn
                logging.info("Turno aplicado en estado del servidor: %s -> %s", room, next_turn)
            else:
                logging.debug("No se actualizó turno (sala faltante o formato inesperado).")
    except Exception as e:
        logging.exception("Error aplicando next_turn al estado: %s", e)

    # Publicar resultado general
    out_topic = PUB_RESULT_TEMPLATE.format(room=room)
    out_payload = json.dumps(result)
    client.publish(out_topic, out_payload, qos=1)
    logging.info("Publicado resultado en %s : %s", out_topic, out_payload)


    # Publicar tableros actualizados (cada jugador recibe su propio tablero revelead)
    try:
        attacker_board = gm.get_board_summary(room, player_id, reveal=True)
        defender = result.get("target")
        defender_board = gm.get_board_summary(room, defender, reveal=True if defender in gm.rooms[room]["players_list"] else False)
        # atacante
        client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=player_id),
                       json.dumps(attacker_board), qos=1)
        # defensor (su propia vista)
        client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=defender),
                       json.dumps(defender_board), qos=1)
    except Exception as e:
        logging.debug("No se publicaron tableros (error): %s", e)

    save_state()

def on_subscribe(client, userdata, mid, granted_qos, properties=None):
    logging.info("Suscripción OK mid=%s qos=%s", mid, granted_qos)

def on_disconnect(client, userdata, rc):
    logging.info("Servidor desconectado rc=%s", rc)

# ---------- Flask endpoints ----------
@app.route("/create_room", methods=["POST"])
def http_create_room():
    """
    JSON esperado: {"room": "room1", "players": ["p1","p2"]}
    """
    d = request.get_json(force=True)
    room = d.get("room")
    players = d.get("players", [])
    if not room or not players or len(players) < 2:
        return jsonify({"error":"room and at least 2 players required"}), 400
    if room in gm.rooms:
        return jsonify({"error":"room_exists"}), 400
    try:
        gm.create_room(room, players)
        save_state()
        # publicar tableros vía MQTT
        for pid in players:
            publish_board_for_player(mqtt_client_global, room, pid)

        # publicar resultado inicial indicando quién comienza (para que clientes lo muestren)
        try:
            initial_turn = gm.rooms[room]["turn"]
            start_msg = {
                "room": room,
                "player": None,
                "target": None,
                "coordinate": None,
                "result": "start",
                "next_turn": initial_turn,
                "victory": False
            }
            mqtt_client_global.publish(PUB_RESULT_TEMPLATE.format(room=room), json.dumps(start_msg), qos=1)
            logging.info("Publicado mensaje de inicio con turno: %s (room=%s)", initial_turn, room)
        except Exception as e:
            logging.debug("No se pudo publicar mensaje de inicio: %s", e)

        return jsonify({"ok": True, "room": room, "players": players}), 201

    except Exception as e:
        logging.exception("Error creando sala HTTP: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/join", methods=["POST"])
def http_join():
    """
    Simple matchmaking queue.
    JSON: {"player_id":"p3"}
    """
    d = request.get_json(force=True)
    player_id = d.get("player_id")
    if not player_id:
        return jsonify({"error":"player_id required"}), 400
    if player_id in waiting_queue:
        return jsonify({"status":"already_waiting"}), 200
    waiting_queue.append(player_id)
    logging.info("Player %s añadido a la cola (size=%d)", player_id, len(waiting_queue))
    # si hay 2 -> crear sala automáticamente
    if len(waiting_queue) >= 2:
        p1 = waiting_queue.pop(0)
        p2 = waiting_queue.pop(0)
        room_id = f"room{random.randint(1000,9999)}"
        try:
            gm.create_room(room_id, [p1, p2])
            save_state()
            for pid in [p1, p2]:
                publish_board_for_player(mqtt_client_global, room_id, pid)
            logging.info("Matchmaking: creado room %s para %s,%s", room_id, p1, p2)
            return jsonify({"ok":True, "room":room_id, "players":[p1,p2]}), 201
        except Exception as e:
            logging.exception("Error en matchmaking: %s", e)
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"status":"queued", "position": len(waiting_queue)}), 200

@app.route("/rooms", methods=["GET"])
def http_list_rooms():
    out = {}
    for rid, r in gm.rooms.items():
        out[rid] = {"turn": r.get("turn"), "state": r.get("state"), "players": r.get("players_list")}
    return jsonify(out), 200

@app.route("/room/<room_id>", methods=["GET"])
def http_room(room_id):
    if room_id not in gm.rooms:
        return jsonify({"error":"room_not_found"}), 404
    room = gm.rooms[room_id]
    # devolver resumen no revelando otras posiciones
    players = room.get("players_list", [])
    summary = {"turn": room.get("turn"), "state": room.get("state"), "players": {}}
    for pid in players:
        summary["players"][pid] = gm.get_board_summary(room_id, pid, reveal=False)
    return jsonify(summary), 200

@app.route("/shutdown", methods=["POST"])
def shutdown():
    # endpoint dev para detener el servidor flask (solo local)
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        return jsonify({"error":"not running with the Werkzeug Server"}), 500
    func()
    return jsonify({"ok": True}), 200

# ---------- crear MQTT client Y correr Flask en hilo ----------
# Mantendremos una referencia global para que endpoints la usen
mqtt_client_global = None

def start_flask():
    # importante: use_reloader=False evita que flask cree procesos duplicados
    logging.info("Iniciando Flask HTTP en %s:%d", HTTP_HOST, HTTP_PORT)
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, use_reloader=False)

def main():
    global mqtt_client_global
    # iniciar mqtt
    mqtt_client = mqtt.Client(client_id="GameManagerServer")
    mqtt_client.enable_logger(logging.getLogger())
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_subscribe = on_subscribe
    mqtt_client.on_disconnect = on_disconnect

    mqtt_client_global = mqtt_client

    try:
        mqtt_client.connect(BROKER, PORT, keepalive=60)
    except Exception as e:
        logging.error("No se pudo conectar al broker: %s", e)
        return

    # iniciar loop mqtt en background
    mqtt_client.loop_start()

    # lanzar flask en hilo separado
    flask_thread = Thread(target=start_flask, daemon=True)
    flask_thread.start()

    logging.info("Servidor MQTT+HTTP iniciado. MQTT en %s:%d, HTTP en %s:%d", BROKER, PORT, HTTP_HOST, HTTP_PORT)

    try:
        # main thread se mantiene vivo
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Apagando servidor por request (KeyboardInterrupt)...")
    finally:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass
        save_state()
        logging.info("Servidor detenido.")

if __name__ == "__main__":
    main()
