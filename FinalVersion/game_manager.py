# game_manager.py
import random
import json
import logging
from typing import List, Tuple, Dict, Set

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- utilidades de coordenadas ---
ROWS = [chr(ord('A') + i) for i in range(10)]  # 'A'..'J'
COLS = list(range(1, 11))                      # 1..10
ROW_INDEX = {r: i for i, r in enumerate(ROWS)}
INDEX_ROW = {i: r for r, i in ROW_INDEX.items()}

def coord_to_index(coord: str) -> Tuple[int,int]:
    """Convierte 'B5' -> (4,1) - CORREGIDO: Letra=Columna, Número=Fila"""
    coord = coord.strip().upper()
    if len(coord) < 2:
        raise ValueError("Coordenada inválida")
    
    col_char = coord[0]  # Columna
    row_str = coord[1:]  # Fila
    
    if col_char not in ROW_INDEX:
        raise ValueError("Columna inválida")
    try:
        row = int(row_str)
    except:
        raise ValueError("Fila inválida")
    if row < 1 or row > 10:
        raise ValueError("Fila fuera de rango")
    
    r = row - 1
    c = ROW_INDEX[col_char]
    
    return (r, c)

def index_to_coord(r: int, c: int) -> str:
    """Convierte (4,1) -> 'B5' - CORREGIDO"""
    return f"{INDEX_ROW[c]}{r+1}"

# --- estructura de datos ---
class Ship:
    def __init__(self, cells: List[Tuple[int,int]]):
        self.cells = cells
        self.hits: Set[Tuple[int,int]] = set()

    def is_sunk(self) -> bool:
        return set(self.cells) <= self.hits

    def to_dict(self):
        return {"cells": [index_to_coord(r,c) for r,c in self.cells],
                "hits": [index_to_coord(r,c) for r,c in sorted(self.hits)]}

class Board:
    def __init__(self, size: int = 10):
        self.size = size
        self.ships: List[Ship] = []
        self.shots: Set[Tuple[int,int]] = set()
        self.place_ships_random()

    def occupied_cells(self) -> Set[Tuple[int,int]]:
        occ = set()
        for s in self.ships:
            occ |= set(s.cells)
        return occ

    def place_ship(self, length: int) -> Ship:
        max_attempts = 1000
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            orientation = random.choice(['H','V'])
            if orientation == 'H':
                r = random.randint(0, self.size-1)
                c0 = random.randint(0, self.size - length)
                cells = [(r, c0 + i) for i in range(length)]
            else:
                c = random.randint(0, self.size-1)
                r0 = random.randint(0, self.size - length)
                cells = [(r0 + i, c) for i in range(length)]
            
            if set(cells) & self.occupied_cells():
                continue
            
            ship = Ship(cells)
            self.ships.append(ship)
            return ship
        raise RuntimeError("No fue posible ubicar el barco")

    def place_ships_random(self):
        self.ships = []
        ship_sizes = [5, 4, 3, 3, 2]
        for length in ship_sizes:
            self.place_ship(length)

    def receive_shot(self, coord: str) -> Dict:
        try:
            r, c = coord_to_index(coord)
        except Exception as e:
            return {"error": f"Coordenada inválida: {e}", "result": "invalid"}
        
        pos = (r, c)
        already = pos in self.shots
        if already:
            return {"already_shot": True, "result": "repeat"}
        
        self.shots.add(pos)
        
        for ship in self.ships:
            if pos in ship.cells:
                ship.hits.add(pos)
                sunk = ship.is_sunk()
                return {
                    "already_shot": False,
                    "result": "hit",
                    "sunk": sunk,
                    "ship_cells": [index_to_coord(r0, c0) for r0, c0 in ship.cells],
                    "remaining_ship_cells": [index_to_coord(r0, c0) for r0, c0 in (set(ship.cells) - ship.hits)]
                }
        
        return {"already_shot": False, "result": "miss"}

    def all_sunk(self) -> bool:
        return all(ship.is_sunk() for ship in self.ships)

    def to_dict(self, reveal_ships=False):
        d = {
            "size": self.size,
            "shots": [index_to_coord(r,c) for r,c in sorted(self.shots)]
        }
        if reveal_ships:
            d["ships"] = [s.to_dict() for s in self.ships]
        else:
            d["ships"] = "hidden"
        return d

# --- GameManager multi-room ---
class GameManager:
    def __init__(self):
        self.rooms: Dict[str, Dict] = {}

    def create_room(self, room_id: str, players: List[str]):
        if room_id in self.rooms:
            raise ValueError("Room ya existe")
        boards = {}
        for p in players:
            b = Board()
            boards[p] = b
        first = random.choice(players)
        self.rooms[room_id] = {
            "players": boards, 
            "turn": first, 
            "state": "playing", 
            "players_list": players
        }
        return {"room": room_id, "first_turn": first}

    def get_board_summary(self, room_id: str, player_id: str, reveal=False):
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("Room no existe")
        board = room["players"][player_id]
        return board.to_dict(reveal_ships=reveal)

    def handle_move(self, room_id: str, player_id: str, coord: str) -> Dict:
        """MÉTODO CRÍTICO QUE FALTABA - Procesa una jugada"""
        logger.info(f"🎯 [GAME_MANAGER] INICIANDO handle_move: room={room_id}, player={player_id}, coord={coord}")
        
        room = self.rooms.get(room_id)
        if not room:
            logger.warning("❌ [GAME_MANAGER] Room %s no existe", room_id)
            return {"error": "room_not_found"}
        
        logger.info(f"🟡 [GAME_MANAGER] Room state: {room.get('state')}, turn: {room.get('turn')}")
        
        if room["state"] != "playing":
            logger.warning("❌ [GAME_MANAGER] Room %s no está en estado 'playing'", room_id)
            return {"error": "game_finished"}
        
        current_turn = room.get("turn")
        if current_turn != player_id:
            logger.warning("❌ [GAME_MANAGER] No es turno de %s. Turno actual: %s", player_id, current_turn)
            return {"error": "not_your_turn", "current_turn": current_turn}
        
        players = room["players_list"]
        if len(players) != 2:
            logger.warning("❌ [GAME_MANAGER] La sala no tiene exactamente 2 jugadores: %s", players)
            return {"error": "expected_two_players"}
        
        opp = players[1] if players[0] == player_id else players[0]
        opp_board = room["players"][opp]
        
        logger.info(f"🎯 [GAME_MANAGER] Procesando disparo de {player_id} contra {opp} en {coord}")
        
        try:
            res = opp_board.receive_shot(coord)
            logger.info(f"🎯 [GAME_MANAGER] Resultado del disparo: {res}")
        except Exception as e:
            logger.exception("❌ [GAME_MANAGER] Error en receive_shot: %s", e)
            return {"error": "invalid_shot", "message": str(e)}
        
        victory = False
        if res.get("result") == "hit" and opp_board.all_sunk():
            victory = True
            room["state"] = "finished"
            logger.info(f"🏆 [GAME_MANAGER] ¡VICTORIA! {player_id} ha ganado")
        
        # Lógica de turnos CORREGIDA
        if res.get("result") == "miss":
            room["turn"] = opp
            logger.info(f"🔄 [GAME_MANAGER] Turno cambiado a: {opp} (por miss)")
        else:
            logger.info(f"🔄 [GAME_MANAGER] Turno se mantiene en: {player_id} (por hit)")
        
        out = {
            "room": room_id,
            "player": player_id,
            "target": opp,
            "coordinate": coord,
            "result": res.get("result"),
            "sunk": res.get("sunk", False),
            "ship_cells": res.get("ship_cells", []),
            "already_shot": res.get("already_shot", False),
            "next_turn": room["turn"],
            "victory": victory
        }
        
        logger.info(f"✅ [GAME_MANAGER] handle_move completado: {out}")
        return out