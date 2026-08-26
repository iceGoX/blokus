#!/usr/bin/env python3
import json
import sys
import urllib.error
import urllib.request


def request(base, path, body=None, session=None):
    headers = {}
    method = "GET"
    payload = None
    if body is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()
    if session:
        headers["X-Player-Id"] = session["playerId"]
        headers["X-Player-Token"] = session["token"]
    req = urllib.request.Request(f"{base}{path}", data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def create_and_start(base, capacity):
    status, created = request(base, "/rooms", {"name": f"{capacity}人房主", "capacity": capacity})
    assert status == 201
    code = created["room"]["code"]
    sessions = [created["session"]]
    for index in range(1, capacity):
        status, joined = request(base, f"/rooms/{code}/join", {"name": f"玩家{index + 1}"})
        assert status == 200
        sessions.append(joined["session"])

    status, rejected = request(base, f"/rooms/{code}/join", {"name": "额外玩家"})
    assert status == 409 and rejected["error"] == "ROOM_FULL"

    status, started = request(base, f"/rooms/{code}/start", {}, sessions[0])
    assert status == 200
    return code, sessions, started["room"]


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4173/api").rstrip("/")
    status, health = request(base, "/health")
    assert status == 200 and health["status"] == "ok"

    code2, sessions2, room2 = create_and_start(base, 2)
    assert room2["game"]["boardSize"] == 14
    assert len(room2["game"]["board"]) == 196
    status, resigned = request(
        base,
        f"/rooms/{code2}/resign",
        {"expectedVersion": room2["version"]},
        sessions2[0],
    )
    assert status == 200 and resigned["room"]["status"] == "playing"
    assert resigned["room"]["lastEvent"]["type"] == "PLAYER_RESIGNED"
    status, finished = request(
        base,
        f"/rooms/{code2}/resign",
        {"expectedVersion": resigned["room"]["version"]},
        sessions2[1],
    )
    assert status == 200 and finished["room"]["status"] == "finished"
    status, invited = request(base, f"/rooms/{code2}/rematch", {}, sessions2[0])
    assert status == 200 and len(invited["room"]["rematchVotes"]) == 1
    status, restarted = request(base, f"/rooms/{code2}/rematch", {}, sessions2[1])
    assert status == 200 and restarted["room"]["status"] == "playing"
    assert restarted["room"]["game"]["turn"] == 1

    _, _, room4 = create_and_start(base, 4)
    assert room4["game"]["boardSize"] == 20
    assert len(room4["game"]["board"]) == 400

    print(json.dumps({
        "health": health["status"],
        "twoPlayerBoard": "14x14",
        "fourPlayerBoard": "20x20",
        "roomFullRejected": True,
        "instantResign": True,
        "rematchStarted": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
