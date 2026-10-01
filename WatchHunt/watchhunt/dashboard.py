"""Local live watch board.

  python dashboard.py            # serves http://127.0.0.1:8000 and re-checks bids every few minutes

The GitHub version (ci.py + .github/workflows/watchhunt.yml) builds the same board as a static page.
"""
import os
import threading
import time
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file

from board_data import build_board, refresh_bids
from store import Store

load_dotenv()
with open(os.getenv("WH_CONFIG", "config.yaml")) as f:
    CFG = yaml.safe_load(f)
DASH = CFG.get("dashboard", {})

store = Store()
app = Flask(__name__)
state = {"last_refresh": None, "refreshing": False, "last_error": None}


def refresh_all():
    if state["refreshing"]:
        return
    state["refreshing"] = True
    try:
        refresh_bids(store, CFG)
        state["last_error"] = None
    except Exception as e:
        state["last_error"] = str(e)
    finally:
        state["refreshing"] = False
        state["last_refresh"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh_loop():
    while True:
        refresh_all()
        time.sleep(60 * DASH.get("refresh_minutes", 10))


@app.get("/")
def index():
    return send_file("board.html")


@app.get("/api/board")
def board():
    data = build_board(store, CFG)
    data["state"] = state
    data["mode"] = "local"
    return jsonify(data)


@app.post("/api/refresh")
def refresh_now():
    threading.Thread(target=refresh_all, daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/dismiss")
def dismiss():
    store.set_status(request.json["key"], "dismissed")
    return jsonify({"ok": True})


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    host, port = DASH.get("host", "127.0.0.1"), DASH.get("port", 8000)
    print(f"Watch board at http://{'localhost' if host == '127.0.0.1' else host}:{port}")
    app.run(host=host, port=port, debug=False)
