# Interactive IoT Communication Prototype using MQTT

## Overview
This project implements an interactive educational tool that simulates IoT communication using the MQTT protocol through a digital version of the classic *Battleship* game. It allows users to visualize in real time how MQTT clients, a broker, and a game server interact using the publish/subscribe model.

The system was developed entirely with open-source technologies and aims to facilitate the learning of IoT communication principles in an engaging, visual, and practical way.

## Features
- Real-time MQTT message exchange using the Mosquitto broker.
- Python-based implementation with the Paho-MQTT library.
- Client-server model with centralized game logic.
- Topic-based communication hierarchy (e.g., `game/room/player/id/move`).
- HTTP API (Flask) for room management and matchmaking.
- Scalable, lightweight, and fully open-source.

## Requirements
- Python 3.10+
- Mosquitto MQTT Broker
- Paho-MQTT Library
- Flask Framework

## Installation
1. **Install Mosquitto Broker**
   sudo apt update
   sudo apt install mosquitto mosquitto-clients
   sudo systemctl start mosquitto
2. **Create and activate a virtual environment**
   python3 -m venv venv
   source venv/bin/activate
3. **Install dependencies**
   pip install paho-mqtt flask


## ------------ FIRST VERSION ------------ 
## Usage
1. Run the Mosquitto broker service
2. **Launch the game server:**
  Launch the game server:
3. **Start each client (player):**
  python sender.py
  python receiver.py
4. Observe how MQTT messages are published and received in real time as players interact with the game board.
   
   sudo apt install mosquitto mosquitto-clients
   sudo systemctl start mosquitto


## Future Work
Integration of a graphical web interface
Support for persistent game sessions
Advanced analytics of MQTT traffic
Hybrid MQTT + HTTP communication scenarios

