from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

BOARD_SIZES = {2: 14, 3: 17, 4: 20}
ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))

PIECES_PATH = Path(__file__).resolve().parents[1] / "shared" / "pieces.json"
PIECES = json.loads(PIECES_PATH.read_text(encoding="utf-8"))
PIECE_MAP = {piece["id"]: piece for piece in PIECES}

COLOR_LABELS = {"blue": "蓝方", "yellow": "黄方", "red": "红方", "green": "绿方"}

# Two-player rooms use opposite corners. Three-player rooms use three corners.
COLOR_SLOTS = {
    2: ["blue", "red"],
    3: ["blue", "yellow", "red"],
    4: ["blue", "yellow", "red", "green"],
}


def normalize(cells: list[list[int]]) -> list[list[int]]:
    min_x = min(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    return sorted([[x - min_x, y - min_y] for x, y in cells], key=lambda c: (c[1], c[0]))


def transform(cells: list[list[int]], rotation: int = 0, flipped: bool = False) -> list[list[int]]:
    result = [[-x if flipped else x, y] for x, y in cells]
    for _ in range(rotation % 4):
        result = [[-y, x] for x, y in result]
    return normalize(result)


def orientations(piece: dict[str, Any]) -> list[list[list[int]]]:
    unique: dict[str, list[list[int]]] = {}
    for flipped in (False, True):
        for rotation in range(4):
            cells = transform(piece["cells"], rotation, flipped)
            key = ";".join(f"{x},{y}" for x, y in cells)
            unique[key] = cells
    return list(unique.values())


def at_position(shape: list[list[int]], anchor_x: int, anchor_y: int) -> list[list[int]]:
    return [[anchor_x + x, anchor_y + y] for x, y in shape]


def placement_cells(
    shape: list[list[int]],
    anchor_x: int,
    anchor_y: int,
    player: dict[str, Any],
    board_size: int,
) -> list[list[int]]:
    corner_x, corner_y = player["corner"]
    if not player["placements"] and anchor_x == corner_x and anchor_y == corner_y:
        max_x = max(x for x, _ in shape)
        max_y = max(y for _, y in shape)
        origin_x = corner_x - max_x if corner_x == board_size - 1 else corner_x
        origin_y = corner_y - max_y if corner_y == board_size - 1 else corner_y
        return at_position(shape, origin_x, origin_y)
    return at_position(shape, anchor_x, anchor_y)


def board_index(board_size: int, x: int, y: int) -> int:
    return y * board_size + x


def in_bounds(board_size: int, x: int, y: int) -> bool:
    return 0 <= x < board_size and 0 <= y < board_size


def player_slots(capacity: int) -> list[dict[str, Any]]:
    board_size = BOARD_SIZES[capacity]
    corners = {
        "blue": [0, 0],
        "yellow": [board_size - 1, 0],
        "red": [board_size - 1, board_size - 1],
        "green": [0, board_size - 1],
    }
    return [
        {"color": color, "colorLabel": COLOR_LABELS[color], "corner": corners[color]}
        for color in COLOR_SLOTS[capacity]
    ]


def validate_placement(
    game: dict[str, Any], cells: list[list[int]], player: dict[str, Any]
) -> tuple[bool, str]:
    board_size = game["boardSize"]
    if any(not in_bounds(board_size, x, y) for x, y in cells):
        return False, "棋块超出棋盘，请换一个落点。"

    if any(game["board"][board_index(board_size, x, y)] is not None for x, y in cells):
        return False, "这里已有棋块，不能重叠。"

    for x, y in cells:
        for dx, dy in ORTHOGONAL:
            nx, ny = x + dx, y + dy
            if in_bounds(board_size, nx, ny) and game["board"][board_index(board_size, nx, ny)] == player["color"]:
                return False, "同色棋块不能边贴边，只能角接角。"

    if not player["placements"]:
        corner_x, corner_y = player["corner"]
        if any(x == corner_x and y == corner_y for x, y in cells):
            return True, "合法落点"
        return False, f"第一块棋必须覆盖{player['colorLabel']}的起始角。"

    touches_corner = any(
        in_bounds(board_size, x + dx, y + dy)
        and game["board"][board_index(board_size, x + dx, y + dy)] == player["color"]
        for x, y in cells
        for dx, dy in DIAGONAL
    )
    if not touches_corner:
        return False, "棋块必须与至少一个同色棋块角接角。"
    return True, "合法落点"


def create_game(room_players: list[dict[str, Any]], capacity: int) -> dict[str, Any]:
    board_size = BOARD_SIZES[capacity]
    slots = player_slots(capacity)
    players = []
    for index, room_player in enumerate(room_players):
        slot = slots[index]
        players.append(
            {
                "id": room_player["id"],
                "name": room_player["name"],
                "color": slot["color"],
                "colorLabel": slot["colorLabel"],
                "corner": list(slot["corner"]),
                "remaining": [piece["id"] for piece in PIECES],
                "placements": [],
                "out": False,
                "lastPiece": None,
            }
        )
    return {
        "boardSize": board_size,
        "board": [None] * (board_size * board_size),
        "players": players,
        "currentPlayer": 0,
        "turn": 1,
        "status": "playing",
        "lastMove": [],
        "winnerIds": [],
    }


def current_player(game: dict[str, Any]) -> dict[str, Any]:
    return game["players"][game["currentPlayer"]]


def piece_size(piece_id: str) -> int:
    return len(PIECE_MAP[piece_id]["cells"])


def remaining_squares(player: dict[str, Any]) -> int:
    return sum(piece_size(piece_id) for piece_id in player["remaining"])


def score(player: dict[str, Any]) -> int:
    remaining = remaining_squares(player)
    if remaining:
        return -remaining
    return 15 + (5 if player["lastPiece"] == "I1" else 0)


def has_legal_move(game: dict[str, Any], player: dict[str, Any]) -> bool:
    board_size = game["boardSize"]
    for piece_id in player["remaining"]:
        for shape in orientations(PIECE_MAP[piece_id]):
            width = max(x for x, _ in shape) + 1
            height = max(y for _, y in shape) + 1
            for y in range(board_size - height + 1):
                for x in range(board_size - width + 1):
                    valid, _ = validate_placement(game, at_position(shape, x, y), player)
                    if valid:
                        return True
    return False


def finish_game(game: dict[str, Any]) -> None:
    game["status"] = "finished"
    best_score = max(score(player) for player in game["players"])
    game["winnerIds"] = [
        player["id"] for player in game["players"] if score(player) == best_score
    ]


def advance_turn(game: dict[str, Any]) -> None:
    if all(player["out"] for player in game["players"]):
        finish_game(game)
        return

    next_index = game["currentPlayer"]
    while True:
        next_index = (next_index + 1) % len(game["players"])
        if not game["players"][next_index]["out"]:
            break
    game["currentPlayer"] = next_index
    game["turn"] += 1


def place_piece(
    game: dict[str, Any],
    player_id: str,
    piece_id: str,
    rotation: int,
    flipped: bool,
    anchor_x: int,
    anchor_y: int,
) -> tuple[bool, str]:
    if game["status"] != "playing":
        return False, "本局已经结束。"
    player = current_player(game)
    if player["id"] != player_id:
        return False, "还没有轮到你。"
    if piece_id not in PIECE_MAP or piece_id not in player["remaining"]:
        return False, "该棋块不可用。"
    if not isinstance(rotation, int) or rotation not in range(4):
        return False, "旋转参数无效。"
    if not isinstance(flipped, bool):
        return False, "翻面参数无效。"
    if not isinstance(anchor_x, int) or not isinstance(anchor_y, int):
        return False, "落点参数无效。"

    shape = transform(PIECE_MAP[piece_id]["cells"], rotation, flipped)
    cells = placement_cells(shape, anchor_x, anchor_y, player, game["boardSize"])
    valid, reason = validate_placement(game, cells, player)
    if not valid:
        return False, reason

    for x, y in cells:
        game["board"][board_index(game["boardSize"], x, y)] = player["color"]
    player["remaining"].remove(piece_id)
    player["placements"].append({"pieceId": piece_id, "cells": cells})
    player["lastPiece"] = piece_id
    game["lastMove"] = cells
    advance_turn(game)
    return True, "落子成功"


def finish_player(game: dict[str, Any], player_id: str) -> tuple[bool, str]:
    if game["status"] != "playing":
        return False, "本局已经结束。"
    player = current_player(game)
    if player["id"] != player_id:
        return False, "还没有轮到你。"
    if has_legal_move(game, player):
        return False, "你仍有合法落点，暂时不能确认结束。"
    player["out"] = True
    advance_turn(game)
    return True, "已确认结束，其他玩家继续"


pass_turn = finish_player


def resign_player(game: dict[str, Any], player_id: str) -> tuple[bool, str]:
    if game["status"] != "playing":
        return False, "本局已经结束。"
    player = next((item for item in game["players"] if item["id"] == player_id), None)
    if not player:
        return False, "玩家不存在。"
    if player["out"]:
        return False, "你已经结束本局。"

    is_current = current_player(game)["id"] == player_id
    player["out"] = True
    if all(item["out"] for item in game["players"]):
        finish_game(game)
    elif is_current:
        advance_turn(game)
    return True, "已认输并结束，其他玩家继续"


def public_game(game: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(game)
    for player in result["players"]:
        player["score"] = score(player)
        player["remainingSquares"] = remaining_squares(player)
    return result
