"""Entry points for the GitHub Actions run.

  python ci.py refresh       # re-check bids on everything on the board
  python ci.py export site   # write site/index.html + site/board.json for GitHub Pages
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

from board_data import build_board, refresh_bids
from store import Store


def main():
    load_dotenv()
    with open(os.getenv("WH_CONFIG", "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    store = Store()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "refresh":
        n = refresh_bids(store, cfg)
        print(f"Re-checked bids on {n} watches.")
    elif cmd == "export":
        out = sys.argv[2] if len(sys.argv) > 2 else "site"
        os.makedirs(out, exist_ok=True)
        data = build_board(store, cfg)
        repo = os.getenv("GITHUB_REPOSITORY", "")
        data["mode"] = "static"
        data["state"] = {"last_refresh": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "refreshing": False, "last_error": None}
        data["actions_url"] = f"https://github.com/{repo}/actions/workflows/watchhunt.yml" if repo else None
        with open(os.path.join(out, "board.json"), "w") as f:
            json.dump(data, f)
        shutil.copy("board.html", os.path.join(out, "index.html"))
        print(f"Exported {len(data['rows'])} watches to {out}/")
    else:
        sys.exit("usage: python ci.py refresh | python ci.py export [dir]")


if __name__ == "__main__":
    main()
