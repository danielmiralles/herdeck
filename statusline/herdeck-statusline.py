#!/usr/bin/env python3
"""herdeck statusline for Claude Code.

Reads the statusline JSON from stdin, stores it atomically in
~/.claude/herdeck/<session_id>.json (herdeck reads it to show context and
quota on the deck) and prints a short line: "Opus · ctx 31% · 5h 42%".
Never fails: on bad input it prints nothing and exits 0.
"""
import json
import os
import re
import sys
import tempfile


def pct(v):
    try:
        return "%d%%" % round(float(v))
    except (TypeError, ValueError):
        return None


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return
    if not isinstance(data, dict):
        return
    sid = re.sub(r"[^A-Za-z0-9-]", "", str(data.get("session_id") or ""))[:128]
    if sid:
        try:
            d = os.path.join(os.path.expanduser("~"), ".claude", "herdeck")
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, os.path.join(d, sid + ".json"))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except Exception:
            pass
    parts = []
    model = data.get("model")
    if isinstance(model, dict) and (model.get("display_name") or model.get("id")):
        parts.append(str(model.get("display_name") or model.get("id")))
    cw = data.get("context_window")
    if isinstance(cw, dict) and pct(cw.get("used_percentage")):
        parts.append("ctx " + pct(cw.get("used_percentage")))
    rl = data.get("rate_limits")
    fh = rl.get("five_hour") if isinstance(rl, dict) else None
    if isinstance(fh, dict) and pct(fh.get("used_percentage")):
        parts.append("5h " + pct(fh.get("used_percentage")))
    if parts:
        print(" · ".join(parts))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
