import json
import threading
import unittest
import urllib.error
import urllib.request

from server.app import BlokusServer, Handler, rooms, room_subscribers


class ServerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = BlokusServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        rooms.clear()
        room_subscribers.clear()

    def request(self, path, body=None, session=None):
        headers = {}
        method = "GET"
        data = None
        if body is not None:
            method = "POST"
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if session:
            headers["X-Player-Id"] = session["playerId"]
            headers["X-Player-Token"] = session["token"]
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def create_room(self, capacity=2, name="房主"):
        status, result = self.request("/api/rooms", {"name": name, "capacity": capacity})
        self.assertEqual(status, 201)
        return result

    def test_room_rejects_extra_player_when_full(self):
        created = self.create_room(2)
        code = created["room"]["code"]
        self.assertEqual(self.request(f"/api/rooms/{code}/join", {"name": "玩家二"})[0], 200)
        status, result = self.request(f"/api/rooms/{code}/join", {"name": "玩家三"})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "ROOM_FULL")

    def test_event_stream_sends_initial_room_state(self):
        created = self.create_room(2)
        code = created["room"]["code"]
        session = created["session"]
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/rooms/{code}/events",
            headers={
                "X-Player-Id": session["playerId"],
                "X-Player-Token": session["token"],
            },
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            line = response.readline().decode("utf-8")
        self.assertTrue(line.startswith("data: "))
        payload = json.loads(line.removeprefix("data: "))
        self.assertEqual(payload["code"], code)

    def test_host_cannot_start_until_room_is_full(self):
        created = self.create_room(3)
        code = created["room"]["code"]
        status, result = self.request(f"/api/rooms/{code}/start", {}, created["session"])
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "ROOM_NOT_FULL")

    def test_full_room_can_start_and_accept_authoritative_move(self):
        created = self.create_room(2)
        code = created["room"]["code"]
        _, joined = self.request(f"/api/rooms/{code}/join", {"name": "玩家二"})
        status, started = self.request(f"/api/rooms/{code}/start", {}, created["session"])
        self.assertEqual(status, 200)
        room = started["room"]
        self.assertEqual(room["status"], "playing")

        status, moved = self.request(
            f"/api/rooms/{code}/place",
            {
                "pieceId": "I5",
                "rotation": 0,
                "flipped": False,
                "anchorX": 0,
                "anchorY": 0,
                "expectedVersion": room["version"],
            },
            created["session"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(moved["room"]["game"]["board"][:5], ["blue"] * 5)

        status, result = self.request(
            f"/api/rooms/{code}/place",
            {
                "pieceId": "I1",
                "rotation": 0,
                "flipped": False,
                "anchorX": 5,
                "anchorY": 1,
                "expectedVersion": moved["room"]["version"],
            },
            created["session"],
        )
        self.assertEqual(status, 422)
        self.assertEqual(result["error"], "INVALID_MOVE")
        self.assertIsNotNone(joined["session"]["token"])

    def test_current_player_thinking_is_shared_and_cleared_after_move(self):
        created = self.create_room(2)
        code = created["room"]["code"]
        _, joined = self.request(f"/api/rooms/{code}/join", {"name": "玩家二"})
        _, started = self.request(f"/api/rooms/{code}/start", {}, created["session"])

        thinking = {
            "pieceId": "I1",
            "rotation": 0,
            "flipped": False,
            "anchorX": 0,
            "anchorY": 0,
        }
        self.assertEqual(self.request(f"/api/rooms/{code}/think", thinking, created["session"])[0], 200)
        status, observed = self.request(f"/api/rooms/{code}", session=joined["session"])
        self.assertEqual(status, 200)
        self.assertEqual(observed["room"]["thinking"]["pieceId"], "I1")
        self.assertEqual(observed["room"]["thinking"]["playerId"], created["session"]["playerId"])
        self.assertEqual(self.request(f"/api/rooms/{code}/think", thinking, joined["session"])[0], 403)

        move = {**thinking, "expectedVersion": started["room"]["version"]}
        self.assertEqual(self.request(f"/api/rooms/{code}/place", move, created["session"])[0], 200)
        _, observed = self.request(f"/api/rooms/{code}", session=joined["session"])
        self.assertIsNone(observed["room"]["thinking"])

    def test_resign_event_keeps_remaining_players_in_game_and_supports_rematch(self):
        created = self.create_room(2)
        code = created["room"]["code"]
        _, joined = self.request(f"/api/rooms/{code}/join", {"name": "玩家二"})
        _, started = self.request(f"/api/rooms/{code}/start", {}, created["session"])
        stream_request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/rooms/{code}/events",
            headers={
                "X-Player-Id": joined["session"]["playerId"],
                "X-Player-Token": joined["session"]["token"],
            },
        )
        with urllib.request.urlopen(stream_request, timeout=3) as stream:
            stream.readline()
            status, result = self.request(
                f"/api/rooms/{code}/resign",
                {"expectedVersion": started["room"]["version"]},
                created["session"],
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["room"]["status"], "playing")
            self.assertEqual(result["room"]["lastEvent"]["type"], "PLAYER_RESIGNED")
            self.assertEqual(result["room"]["game"]["players"][1]["id"], joined["session"]["playerId"])

            received_resign_event = False
            for _ in range(6):
                line = stream.readline().decode("utf-8")
                if not line.startswith("data: "):
                    continue
                event_room = json.loads(line.removeprefix("data: "))
                if (event_room.get("lastEvent") or {}).get("type") == "PLAYER_RESIGNED":
                    received_resign_event = True
                    break
            self.assertTrue(received_resign_event)

        status, finished = self.request(
            f"/api/rooms/{code}/resign",
            {"expectedVersion": result["room"]["version"]},
            joined["session"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(finished["room"]["status"], "finished")
        self.assertEqual(len(finished["room"]["game"]["winnerIds"]), 2)

        status, invited = self.request(f"/api/rooms/{code}/rematch", {}, created["session"])
        self.assertEqual(status, 200)
        self.assertEqual(invited["room"]["status"], "finished")
        self.assertEqual(invited["room"]["rematchVotes"], [created["session"]["playerId"]])

        status, restarted = self.request(f"/api/rooms/{code}/rematch", {}, joined["session"])
        self.assertEqual(status, 200)
        self.assertEqual(restarted["room"]["status"], "playing")
        self.assertEqual(restarted["room"]["game"]["turn"], 1)
        self.assertFalse(any(restarted["room"]["game"]["board"]))


if __name__ == "__main__":
    unittest.main()
