#!/usr/bin/env python3
"""Build the self-contained offline edition as one double-clickable HTML file."""

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    js = (ROOT / "game.js").read_text(encoding="utf-8")
    pieces = json.loads((ROOT / "shared" / "pieces.json").read_text(encoding="utf-8"))

    html = html.replace('<link rel="stylesheet" href="styles.css" />', f"<style>\n{css}\n</style>")
    html = html.replace('<script type="module" src="game.js"></script>', "")
    js_start = js.index("let PIECES;")
    js_end = js.index("\n\nconst PIECE_MAP", js_start)
    js = f"const PIECES = {json.dumps(pieces, ensure_ascii=False, separators=(',', ':'))};" + js[js_end:]
    js = js.replace('const API_BASE = location.pathname.startsWith("/blokus") ? "/blokus/api" : "/api";\n', "")
    for start_marker, end_marker in (
        ("async function api(", "function currentBoardSize"),
        ("async function connectStream(", "async function restoreSession"),
        ("async function restoreSession(", "function escapeHtml"),
    ):
        start = js.index(start_marker)
        end = js.index(end_marker, start)
        js = js[:start] + js[end:]
    js = js.replace("function currentBoardSize()", "function queueThinking() {}\n\nfunction currentBoardSize()", 1)
    js = js.replace("buildBoard();\nrestoreSession();", "buildBoard();")
    js += "\n\nstartOfflineMode();"
    html = html.replace("<body data-theme=\"tabletop\">", '<body data-theme="tabletop" data-offline-only="true">')
    html = html.replace("  </body>", f"  <script>\n{js}\n  </script>\n  </body>")
    html = html.replace("</style>", "body[data-offline-only=\"true\"] #lobbyScreen { display: none; }\n</style>", 1)
    (ROOT / "offline.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
