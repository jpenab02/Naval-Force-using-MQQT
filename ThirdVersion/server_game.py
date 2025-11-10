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
from game_manager import GameManager

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

gm = GameManager()
mqtt_client_global = None

app = Flask(__name__)

# ---------- Utilidades ----------
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


def publish_board_for_player(mqtt_client, room, pid, reveal=True):
    """Publica el tablero para un jugador."""
    try:
        board_payload = gm.get_board_summary(room, pid, reveal=reveal)
        mqtt_client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=pid),
                            json.dumps(board_payload), qos=1)
        logging.info("Publicado tablero MQTT para %s (reveal=%s)", pid, reveal)
    except Exception as e:
        logging.exception("Error publicando tablero para %s: %s", pid, e)


def publish_all_boards(mqtt_client, room):
    """Publica ambos tableros (cada jugador ve su propio con barcos visibles)."""
    try:
        players = gm.rooms[room]["players_list"]
        for pid in players:
            publish_board_for_player(mqtt_client, room, pid, reveal=True)
    except Exception as e:
        logging.error("Error publicando tableros para room %s: %s", room, e)

# ---------- MQTT Callbacks ----------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logging.info("Servidor conectado al broker MQTT")
        client.subscribe(TOPIC_MOVES, qos=1)
        logging.info("Suscrito a %s", TOPIC_MOVES)
    else:
        logging.error("Error conexión broker rc=%s", rc)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        logging.info("🔴 MENSAJE RECIBIDO topic=%s payload=%s", msg.topic, payload)
        data = json.loads(payload)
    except Exception as e:
        logging.exception("Payload inválido o no JSON: %s", e)
        return

    # Extraer room y player del topic
    parts = msg.topic.split('/')
    if not (len(parts) >= 5 and parts[0] == "game" and parts[2] == "player" and parts[4] == "move"):
        logging.debug("Tópico no es move: %s", msg.topic)
        return

    room = parts[1]
    player_id = parts[3]
    coord = data.get("coordinate")
    
    logging.info(f"🔴 Procesando movimiento: room={room}, player={player_id}, coord={coord}")
    
    if coord is None:
        logging.warning("Payload sin campo 'coordinate'. Ignorando.")
        return

    # Verificar si la sala existe
    if room not in gm.rooms:
        logging.warning("❌ Room %s no encontrada. Ignorando jugada de %s", room, player_id)
        err = {"error": "room_not_found", "room": room}
        client.publish(PUB_RESULT_TEMPLATE.format(room=room), json.dumps(err), qos=1)
        return

    # Procesar jugada
    logging.info(f"🟡 Llamando a handle_move para {player_id} en {room} con coordenada {coord}")
    result = gm.handle_move(room, player_id, coord)
    logging.info(f"🟡 Resultado de handle_move: {result}")

    # Aplicar next_turn al estado del servidor
    try:
        next_turn = result.get("next_turn")
        if next_turn:
            if room in gm.rooms and "players_list" in gm.rooms[room]:
                gm.rooms[room]["turn"] = next_turn
                logging.info("✅ Turno aplicado en estado del servidor: %s -> %s", room, next_turn)
    except Exception as e:
        logging.exception("Error aplicando next_turn: %s", e)

    # Publicar resultado general
    out_topic = PUB_RESULT_TEMPLATE.format(room=room)
    out_payload = json.dumps(result)
    logging.info(f"🟡 PUBLICANDO RESULTADO en {out_topic}: {out_payload}")
    client.publish(out_topic, out_payload, qos=1)
    logging.info("✅ Resultado publicado correctamente")

    # Publicar tableros actualizados
    try:
        attacker = player_id
        defender = result.get("target")
        
        # Tablero del atacante
        attacker_board = gm.get_board_summary(room, attacker, reveal=True)
        client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=attacker),
                      json.dumps(attacker_board), qos=1)
        logging.info(f"✅ Tablero publicado para atacante {attacker}")
        
        # Tablero del defensor
        if defender:
            defender_board = gm.get_board_summary(room, defender, reveal=True)
            client.publish(PUB_BOARD_TEMPLATE.format(room=room, player_id=defender),
                          json.dumps(defender_board), qos=1)
            logging.info(f"✅ Tablero publicado para defensor {defender}")
            
    except Exception as e:
        logging.exception("❌ Error publicando tableros: %s", e)

    save_state()


def on_disconnect(client, userdata, rc):
    logging.info("Servidor desconectado rc=%s", rc)

# ---------- Flask endpoints ----------
@app.route("/create_room", methods=["POST"])
def http_create_room():
    """Crea una sala con dos jugadores."""
    d = request.get_json(force=True)
    room = d.get("room")
    players = d.get("players", [])
    if not room or len(players) < 2:
        return jsonify({"error": "room and 2 players required"}), 400
    if room in gm.rooms:
        return jsonify({"error": "room_exists"}), 400

    try:
        gm.create_room(room, players)
        save_state()
        publish_all_boards(mqtt_client_global, room)

        # Publicar mensaje inicial
        start_msg = {
            "room": room,
            "result": "start",
            "next_turn": gm.rooms[room]["turn"]
        }
        mqtt_client_global.publish(PUB_RESULT_TEMPLATE.format(room=room),
                                   json.dumps(start_msg), qos=1)
        logging.info("Sala %s creada correctamente con %s", room, players)
        return jsonify({"ok": True, "room": room, "players": players}), 201
    except Exception as e:
        logging.exception("Error creando sala HTTP: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/rooms", methods=["GET"])
def http_rooms():
    out = {}
    for rid, r in gm.rooms.items():
        out[rid] = {"turn": r.get("turn"), "state": r.get("state"), "players": r.get("players_list")}
    return jsonify(out), 200


def start_flask():
    logging.info("Iniciando Flask HTTP en %s:%d", HTTP_HOST, HTTP_PORT)
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, use_reloader=False)

# ---------- Main ----------
def main():
    global mqtt_client_global
    mqtt_client = mqtt.Client(client_id="GameManagerServer")
    mqtt_client.enable_logger(logging.getLogger())
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client_global = mqtt_client

    mqtt_client.connect(BROKER, PORT, keepalive=60)
    mqtt_client.loop_start()

    Thread(target=start_flask, daemon=True).start()
    logging.info("Servidor MQTT+HTTP iniciado en %s:%d y Flask en %d", BROKER, PORT, HTTP_PORT)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Servidor detenido manualmente.")
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main()
