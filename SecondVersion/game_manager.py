# game_manager.py
import random
import json
from typing import List, Tuple, Dict, Set

# --- utilidades de coordenadas ---
ROWS = [chr(ord('A') + i) for i in range(10)]  # 'A'..'J'
COLS = list(range(1, 11))                      # 1..10
ROW_INDEX = {r: i for i, r in enumerate(ROWS)}
# inverse map optional
INDEX_ROW = {i: r for r, i in ROW_INDEX.items()}

def coord_to_index(coord: str) -> Tuple[int,int]:
    """Convierte 'B5' -> (1,4) (fila índice, col índice 0-based)"""
    coord = coord.strip().upper()
    if len(coord) < 2:
        raise ValueError("Coordenada inválida")
    # separar letra(s) y número(s)
    row_char = coord[0]
    col_str = coord[1:]
    if row_char not in ROW_INDEX:
        raise ValueError("Fila inválida")
    try:
        col = int(col_str)
    except:
        raise ValueError("Columna inválida")
    if col not in COLS:
        raise ValueError("Columna fuera de rango")
    r = ROW_INDEX[row_char]
    c = col - 1
    return (r, c)

def index_to_coord(r: int, c: int) -> str:
    return f"{INDEX_ROW[r]}{c+1}"

# --- estructura de datos ---
class Ship:
    def __init__(self, cells: List[Tuple[int,int]]):
        self.cells = cells                # lista de tuplas (r,c)
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
        self.place_ships_random()  # 🔥 genera los barcos automáticamente


    def occupied_cells(self) -> Set[Tuple[int,int]]:
        occ = set()
        for s in self.ships:
            occ |= set(s.cells)
        return occ

    def place_ship(self, length: int) -> Ship:
        """Intenta colocar un barco de longitud `length` aleatoriamente sin solapar."""
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
            # comprobar solapamiento
            if set(cells) & self.occupied_cells():
                continue
            # si válido, crear y agregar
            ship = Ship(cells)
            self.ships.append(ship)
            return ship
        raise RuntimeError("No fue posible ubicar el barco (intente reiniciar)")

    def place_ships_random(self):
        """Coloca 5 barcos de diferentes tamaños (clásicos de batalla naval)."""
        self.ships = []
        ship_sizes = [5, 4, 3, 3, 2]
        for length in ship_sizes:
            self.place_ship(length)



    def receive_shot(self, coord: str) -> Dict:
        """Recibe 'B5', devuelve dict con resultado: hit/miss, sunk?, ship_cells?, already_shot?"""
        r,c = coord_to_index(coord)
        pos = (r,c)
        already = pos in self.shots
        if already:
            return {"already_shot": True, "result": "repeat"}
        # registrar disparo
        self.shots.add(pos)
        # buscar si golpea algún barco
        for ship in self.ships:
            if pos in ship.cells:
                ship.hits.add(pos)
                sunk = ship.is_sunk()
                return {
                    "already_shot": False,
                    "result": "hit",
                    "sunk": sunk,
                    "ship_cells": [index_to_coord(r0,c0) for r0,c0 in ship.cells],
                    "remaining_ship_cells": [index_to_coord(r0,c0) for r0,c0 in (set(ship.cells) - ship.hits)]
                }
        # si no golpea
        return {"already_shot": False, "result": "miss"}

    def all_sunk(self) -> bool:
        return all(ship.is_sunk() for ship in self.ships)

    def to_dict(self, reveal_ships=False):
        """Representación. Si reveal_ships=False no muestra posiciones de los barcos (para clientes)."""
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
        # estructura: rooms[room_id] = {"players": {player_id: Board}, "turn": player_id, "state": "playing"|"finished"}
        self.rooms: Dict[str, Dict] = {}

    def create_room(self, room_id: str, players: List[str], num_ships=5, ship_length=3):
        if room_id in self.rooms:
            raise ValueError("Room ya existe")
        boards = {}
        for p in players:
            b = Board()
            b.place_ships_random()
            boards[p] = b
        # turno aleatorio
        first = random.choice(players)
        self.rooms[room_id] = {"players": boards, "turn": first, "state": "playing", "players_list": players}
        return {"room": room_id, "first_turn": first}

    def get_board_summary(self, room_id: str, player_id: str, reveal=False):
        room = self.rooms.get(room_id)
        if not room:
            raise ValueError("Room no existe")
        board = room["players"][player_id]
        return board.to_dict(reveal_ships=reveal)

    def handle_move(self, room_id: str, player_id: str, coord: str) -> Dict:
        """Procesa una jugada: valida turno, ejecuta shot contra oponente y devuelve resultado listo para publicar."""
        room = self.rooms.get(room_id)
        if not room:
            return {"error": "room_not_found"}
        if room["state"] != "playing":
            return {"error": "game_finished"}
        if room["turn"] != player_id:
            return {"error": "not_your_turn", "current_turn": room["turn"]}
        # oponente (asumimos 2 jugadores)
        players = room["players_list"]
        if len(players) != 2:
            return {"error": "expected_two_players"}
        opp = players[1] if players[0] == player_id else players[0]
        opp_board = room["players"][opp]
        res = opp_board.receive_shot(coord)
        # si hit y hunde, comprobar si victoria
        victory = False
        if res.get("result") == "hit" and opp_board.all_sunk():
            victory = True
            room["state"] = "finished"
        # actualizar turno (si miss -> cambia, si hit -> se queda en quien disparó? En batallanaval clásico miss -> turno al otro; hit -> sigue)
        # Aquí: adoptamos regla clásica: si hit -> mismo jugador sigue; if miss -> turno cambia
        if res.get("result") == "miss":
            room["turn"] = opp
        # preparar salida
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
        return out
