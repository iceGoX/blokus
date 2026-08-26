from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from .game_engine import (
        create_game,
        place_piece,
        player_slots,
        public_game,
        resign_player,
    )
except ImportError:
    from game_engine import (
        create_game,
        place_piece,
        player_slots,
        public_game,
        resign_player,
    )

ROOT = Path(__file__).resolve().parents[1]
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_RE = re.compile(r"^[A-Z2-9]{6}$")
NAME_RE = re.compile(r"^[^\x00-\x1f<>]{1,16}$")
MAX_BODY_BYTES = 16 * 1024

STATIC_FILES = {
    "/": ROOT / "index.html",
    "/index.html": ROOT / "index.html",
    "/styles.css": ROOT / "styles.css",
    "/game.js": ROOT / "game.js",
    "/shared/pieces.json": ROOT / "shared" / "pieces.json",
}

rooms: dict[str, dict] = {}
room_subscribers: dict[str, list[dict]] = defaultdict(list)
rooms_lock = threading.RLock()
rate_lock = threading.Lock()
request_times: dict[str, deque] = defaultdict(deque)


def now() -> float:
    return time.time()


def make_room_code() -> str:
    for _ in range(100):
        code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(6))
        if code not in rooms:
            return code
    raise RuntimeError("无法生成唯一房间号")


def make_player(name: str, capacity: int, index: int, is_host: bool) -> dict:
    slot = player_slots(capacity)[index]
    return {
        "id": secrets.token_urlsafe(12),
        "token": secrets.token_urlsafe(32),
        "name": name,
        "color": slot["color"],
        "colorLabel": slot["colorLabel"],
        "corner": list(slot["corner"]),
        "isHost": is_host,
        "connections": 0,
    }


def public_room(room: dict) -> dict:
    result = {
        "code": room["code"],
        "capacity": room["capacity"],
        "status": room["status"],
        "version": room["version"],
        "createdAt": room["createdAt"],
        "updatedAt": room["updatedAt"],
        "lastEvent": room.get("lastEvent"),
        "rematchVotes": list(room.get("rematchVotes", [])),
        "players": [
            {
                "id": player["id"],
                "name": player["name"],
                "color": player["color"],
                "colorLabel": player["colorLabel"],
                "corner": player["corner"],
                "isHost": player["isHost"],
                "connected": player["connections"] > 0,
            }
            for player in room["players"]
        ],
        "game": public_game(room["game"]) if room["game"] else None,
    }
    if result["game"]:
        connected = {player["id"]: player["connections"] > 0 for player in room["players"]}
        hosts = {player["id"]: player["isHost"] for player in room["players"]}
        for player in result["game"]["players"]:
            player["connected"] = connected.get(player["id"], False)
            player["isHost"] = hosts.get(player["id"], False)
    return result


def set_room_event(room: dict, event_type: str, message: str, player_id: str | None = None) -> None:
    room["eventSequence"] = room.get("eventSequence", 0) + 1
    room["lastEvent"] = {
        "id": room["eventSequence"],
        "type": event_type,
        "message": message,
        "playerId": player_id,
        "time": int(now()),
    }


def find_player(room: dict, player_id: str | None) -> dict | None:
    return next((player for player in room["players"] if player["id"] == player_id), None)


def authenticate(room: dict, player_id: str | None, token: str | None) -> dict | None:
    player = find_player(room, player_id)
    if not player or not token:
        return None
    return player if secrets.compare_digest(player["token"], token) else None


def enqueue_room(room_code: str) -> None:
    with rooms_lock:
        room = rooms.get(room_code)
        if not room:
            return
        payload = json.dumps(public_room(room), ensure_ascii=False, separators=(",", ":"))
        subscribers = list(room_subscribers.get(room_code, []))
    for subscriber in subscribers:
        channel = subscriber["queue"]
        try:
            channel.put_nowait(payload)
        except queue.Full:
            try:
                channel.get_nowait()
            except queue.Empty:
                pass
            try:
                channel.put_nowait(payload)
            except queue.Full:
                pass


def clean_rooms() -> None:
    while True:
        time.sleep(300)
        current = now()
        with rooms_lock:
            expired = []
            for code, room in rooms.items():
                age = current - room["updatedAt"]
                limit = 3600 if room["status"] == "finished" else 86400
                if room["status"] == "waiting":
                    limit = 7200
                if age > limit and not room_subscribers.get(code):
                    expired.append(code)
            for code in expired:
                rooms.pop(code, None)
                room_subscribers.pop(code, None)


class BlokusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "BlokusRoomServer/1.0"

    def log_message(self, format_string: str, *args) -> None:
        print(
            f"{self.log_date_time_string()} {self.client_address[0]} {format_string % args}",
            flush=True,
        )

    def normalized_path(self) -> str:
        path = urlparse(self.path).path
        if path == "/blokus":
            return "/"
        if path.startswith("/blokus/"):
            return path[len("/blokus") :]
        return path

    def client_ip(self) -> str:
        forwarded = self.headers.get("X-Real-IP")
        return forwarded.strip() if forwarded else self.client_address[0]

    def is_rate_limited(self) -> bool:
        ip = self.client_ip()
        current = now()
        with rate_lock:
            entries = request_times[ip]
            while entries and current - entries[0] > 60:
                entries.popleft()
            if len(entries) >= 180:
                return True
            entries.append(current)
        return False

    def send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("请求长度无效") from error
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("请求内容为空或过大")
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("请求不是有效 JSON") from error
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象")
        return data

    def auth_headers(self) -> tuple[str | None, str | None]:
        return self.headers.get("X-Player-Id"), self.headers.get("X-Player-Token")

    def room_or_error(self, code: str) -> dict | None:
        room = rooms.get(code)
        if not room:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "ROOM_NOT_FOUND", "message": "房间不存在或已经过期。"})
            return None
        return room

    def authenticated_room(self, code: str) -> tuple[dict | None, dict | None]:
        room = self.room_or_error(code)
        if not room:
            return None, None
        player_id, token = self.auth_headers()
        player = authenticate(room, player_id, token)
        if not player:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "UNAUTHORIZED", "message": "玩家身份已失效，请重新加入。"})
            return None, None
        return room, player

    def do_GET(self) -> None:
        if self.is_rate_limited():
            self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试。"})
            return

        path = self.normalized_path()
        if path == "/api/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "rooms": len(rooms), "time": int(now())})
            return

        match = re.fullmatch(r"/api/rooms/([A-Z2-9]{6})", path)
        if match:
            with rooms_lock:
                room, _ = self.authenticated_room(match.group(1))
                if room:
                    self.send_json(HTTPStatus.OK, {"room": public_room(room)})
            return

        match = re.fullmatch(r"/api/rooms/([A-Z2-9]{6})/events", path)
        if match:
            self.handle_events(match.group(1))
            return

        self.serve_static(path)

    def do_POST(self) -> None:
        if self.is_rate_limited():
            self.send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试。"})
            return
        path = self.normalized_path()
        try:
            data = self.read_json()
        except ValueError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "message": str(error)})
            return

        if path == "/api/rooms":
            self.create_room(data)
            return

        match = re.fullmatch(r"/api/rooms/([A-Z2-9]{6})/(join|start|place|resign|finish|pass|rematch|leave)", path)
        if not match:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND", "message": "接口不存在。"})
            return

        code, action = match.groups()
        if action == "join":
            self.join_room(code, data)
        elif action == "start":
            self.start_room(code, data)
        elif action == "place":
            self.place(code, data)
        elif action in {"resign", "finish", "pass"}:
            self.resign_player(code, data)
        elif action == "rematch":
            self.rematch(code, data)
        elif action == "leave":
            self.leave_room(code, data)

    def create_room(self, data: dict) -> None:
        name = str(data.get("name", "")).strip()
        capacity = data.get("capacity")
        if not NAME_RE.fullmatch(name):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "INVALID_NAME", "message": "昵称长度需为 1–16 个字符。"})
            return
        if capacity not in (2, 3, 4):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "INVALID_CAPACITY", "message": "房间人数必须是 2、3 或 4。"})
            return

        with rooms_lock:
            code = make_room_code()
            player = make_player(name, capacity, 0, True)
            room = {
                "code": code,
                "capacity": capacity,
                "status": "waiting",
                "version": 1,
                "createdAt": now(),
                "updatedAt": now(),
                "players": [player],
                "game": None,
                "eventSequence": 0,
                "lastEvent": None,
                "rematchVotes": [],
            }
            rooms[code] = room
            response = {
                "session": {"roomCode": code, "playerId": player["id"], "token": player["token"]},
                "room": public_room(room),
            }
        self.send_json(HTTPStatus.CREATED, response)

    def join_room(self, code: str, data: dict) -> None:
        code = code.upper()
        name = str(data.get("name", "")).strip()
        if not ROOM_CODE_RE.fullmatch(code) or not NAME_RE.fullmatch(name):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "message": "房间号或昵称格式不正确。"})
            return

        with rooms_lock:
            room = self.room_or_error(code)
            if not room:
                return
            if room["status"] != "waiting":
                self.send_json(HTTPStatus.CONFLICT, {"error": "GAME_STARTED", "message": "游戏已经开始，不能再加入。"})
                return
            if len(room["players"]) >= room["capacity"]:
                self.send_json(HTTPStatus.CONFLICT, {"error": "ROOM_FULL", "message": "房间已满，请加入其他房间。"})
                return
            player = make_player(name, room["capacity"], len(room["players"]), False)
            room["players"].append(player)
            set_room_event(room, "PLAYER_JOINED", f"{name} 加入了房间。", player["id"])
            room["version"] += 1
            room["updatedAt"] = now()
            response = {
                "session": {"roomCode": code, "playerId": player["id"], "token": player["token"]},
                "room": public_room(room),
            }
        self.send_json(HTTPStatus.OK, response)
        enqueue_room(code)

    def start_room(self, code: str, data: dict) -> None:
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            if not player["isHost"]:
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "HOST_ONLY", "message": "只有房主可以开始游戏。"})
                return
            if room["status"] != "waiting":
                self.send_json(HTTPStatus.CONFLICT, {"error": "ALREADY_STARTED", "message": "游戏已经开始。"})
                return
            if len(room["players"]) != room["capacity"]:
                self.send_json(HTTPStatus.CONFLICT, {"error": "ROOM_NOT_FULL", "message": "等待所有座位坐满后才能开始。"})
                return
            room["game"] = create_game(room["players"], room["capacity"])
            room["status"] = "playing"
            room["rematchVotes"] = []
            set_room_event(room, "GAME_STARTED", "房间已满，游戏开始。")
            room["version"] += 1
            room["updatedAt"] = now()
            response = public_room(room)
        self.send_json(HTTPStatus.OK, {"room": response})
        enqueue_room(code)

    def require_version(self, room: dict, data: dict) -> bool:
        if data.get("expectedVersion") != room["version"]:
            self.send_json(
                HTTPStatus.CONFLICT,
                {"error": "STALE_STATE", "message": "棋局状态已更新，请按最新棋盘操作。", "room": public_room(room)},
            )
            return False
        return True

    def place(self, code: str, data: dict) -> None:
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            if room["status"] != "playing" or not room["game"]:
                self.send_json(HTTPStatus.CONFLICT, {"error": "NOT_PLAYING", "message": "游戏尚未开始或已经结束。"})
                return
            if not self.require_version(room, data):
                return
            ok, message = place_piece(
                room["game"],
                player["id"],
                str(data.get("pieceId", "")),
                data.get("rotation"),
                data.get("flipped"),
                data.get("anchorX"),
                data.get("anchorY"),
            )
            if not ok:
                self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "INVALID_MOVE", "message": message})
                return
            if room["game"]["status"] == "finished":
                room["status"] = "finished"
                set_room_event(room, "GAME_FINISHED", "所有玩家均已结束，最终排名已生成。")
            else:
                set_room_event(room, "PIECE_PLACED", f"{player['name']} 完成了落子。", player["id"])
            room["version"] += 1
            room["updatedAt"] = now()
            response = public_room(room)
        self.send_json(HTTPStatus.OK, {"room": response})
        enqueue_room(code)

    def resign_player(self, code: str, data: dict) -> None:
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            if room["status"] != "playing" or not room["game"]:
                self.send_json(HTTPStatus.CONFLICT, {"error": "NOT_PLAYING", "message": "游戏尚未开始或已经结束。"})
                return
            if not self.require_version(room, data):
                return
            ok, message = resign_player(room["game"], player["id"])
            if not ok:
                self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "CANNOT_PASS", "message": message})
                return
            if room["game"]["status"] == "finished":
                room["status"] = "finished"
                set_room_event(room, "GAME_FINISHED", "所有玩家均已结束，最终排名已生成。")
            else:
                set_room_event(
                    room,
                    "PLAYER_RESIGNED",
                    f"{player['name']} 已认输并结束，其他玩家继续。",
                    player["id"],
                )
            room["version"] += 1
            room["updatedAt"] = now()
            response = public_room(room)
        self.send_json(HTTPStatus.OK, {"room": response})
        enqueue_room(code)

    def rematch(self, code: str, data: dict) -> None:
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            if room["status"] != "finished":
                self.send_json(HTTPStatus.CONFLICT, {"error": "GAME_NOT_FINISHED", "message": "当前棋局尚未结束。"})
                return
            if len(room["players"]) != room["capacity"]:
                self.send_json(HTTPStatus.CONFLICT, {"error": "PLAYER_LEFT", "message": "已有玩家离开，请返回大厅创建新房间。"})
                return
            if player["id"] not in room["rematchVotes"]:
                room["rematchVotes"].append(player["id"])

            if len(room["rematchVotes"]) == len(room["players"]):
                room["game"] = create_game(room["players"], room["capacity"])
                room["status"] = "playing"
                room["rematchVotes"] = []
                set_room_event(room, "GAME_RESTARTED", "所有玩家已接受邀请，新一局开始。")
            else:
                set_room_event(
                    room,
                    "REMATCH_REQUESTED",
                    f"{player['name']} 邀请大家再来一盘。",
                    player["id"],
                )
            room["version"] += 1
            room["updatedAt"] = now()
            response = public_room(room)
        self.send_json(HTTPStatus.OK, {"room": response})
        enqueue_room(code)

    def leave_room(self, code: str, data: dict) -> None:
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            if room["status"] == "waiting":
                room["players"] = [item for item in room["players"] if item["id"] != player["id"]]
                if not room["players"]:
                    rooms.pop(code, None)
                    room_subscribers.pop(code, None)
                    self.send_json(HTTPStatus.OK, {"left": True})
                    return
                if player["isHost"]:
                    room["players"][0]["isHost"] = True
            elif room["status"] == "finished":
                room["players"] = [item for item in room["players"] if item["id"] != player["id"]]
                room["rematchVotes"] = [item for item in room["rematchVotes"] if item != player["id"]]
                if not room["players"]:
                    rooms.pop(code, None)
                    room_subscribers.pop(code, None)
                    self.send_json(HTTPStatus.OK, {"left": True})
                    return
                if player["isHost"]:
                    room["players"][0]["isHost"] = True
                set_room_event(room, "PLAYER_LEFT", f"{player['name']} 已返回大厅。", player["id"])
            elif room["status"] == "playing":
                self.send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "GAME_IN_PROGRESS", "message": "游戏进行中，请在确认无合法落点后结束。"},
                )
                return
            room["version"] += 1
            room["updatedAt"] = now()
        self.send_json(HTTPStatus.OK, {"left": True})
        enqueue_room(code)

    def handle_events(self, code: str) -> None:
        subscriber = {"queue": queue.Queue(maxsize=8), "playerId": None}
        player = None
        with rooms_lock:
            room, player = self.authenticated_room(code)
            if not room:
                return
            subscriber["playerId"] = player["id"]
            room_subscribers[code].append(subscriber)
            was_offline = player["connections"] == 0
            player["connections"] += 1
            if was_offline and room["status"] == "playing":
                set_room_event(room, "PLAYER_RECONNECTED", f"{player['name']} 已重新连接。", player["id"])
            payload = json.dumps(public_room(room), ensure_ascii=False, separators=(",", ":"))

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        enqueue_room(code)
        try:
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    message = subscriber["queue"].get(timeout=5)
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            with rooms_lock:
                if subscriber in room_subscribers.get(code, []):
                    room_subscribers[code].remove(subscriber)
                current_room = rooms.get(code)
                current_player = find_player(current_room, player["id"]) if current_room else None
                if current_player:
                    current_player["connections"] = max(0, current_player["connections"] - 1)
                    if current_player["connections"] == 0 and current_room["status"] == "playing":
                        set_room_event(
                            current_room,
                            "PLAYER_OFFLINE",
                            f"{current_player['name']} 已离线，等待其重新连接。",
                            current_player["id"],
                        )
            enqueue_room(code)

    def serve_static(self, path: str) -> None:
        file_path = STATIC_FILES.get(path)
        if not file_path or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix in {".html", ".css", ".js", ".json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Blokus room server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8790, type=int)
    args = parser.parse_args()

    threading.Thread(target=clean_rooms, name="room-cleaner", daemon=True).start()
    server = BlokusServer((args.host, args.port), Handler)
    print(f"Blokus server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
