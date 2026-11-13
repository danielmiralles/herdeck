#!/usr/bin/env python3
"""herdeck — deck táctil (tablet/móvil) para varios servidores herdr.

Puente sin dependencias (solo stdlib, Python >= 3.9):
  * Se conecta al socket de cada servidor herdr (local directo, remoto vía
    túnel SSH de socket Unix) y mantiene el estado con session.snapshot +
    events.subscribe (push, sin polling agresivo).
  * Sirve un deck web (deck.html) + API JSON + Server-Sent Events.
  * Ejecuta acciones: enfocar agente, leer salida, responder (opcional),
    invocar acciones de plugins de herdr.

Uso:
  python3 herdeck.py                       # usa ~/.config/herdeck/config.json
  python3 herdeck.py --config ruta.json
  python3 herdeck.py --print-url           # muestra la URL con token y sale
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".config" / "herdeck" / "config.json"
RUN_DIR = Path.home() / ".config" / "herdeck" / "run"
STATUS_ORDER = {"blocked": 0, "error": 1, "done": 2, "working": 3, "unknown": 4, "idle": 5}
CLAUDE_KINDS = {"claude", "claude-code"}
HC_SOCKET = "~/.local/state/herdr-core/default/default.sock"
LIFECYCLE_EVENTS = [
    "pane.created", "pane.closed", "pane.exited", "pane.agent_detected",
    "workspace.created", "workspace.closed", "workspace.renamed",
    "tab.created", "tab.closed", "tab.renamed",
]
CLAUDE_REFRESH_SECONDS = 10.0
SESSION_ID_RE = re.compile(r"[A-Za-z0-9-]{1,128}")
# Runs with python3 locally or on the ssh host (script on stdin, session ids as
# argv). Reads the files written by statusline/herdeck-statusline.py and prints
# {"sessions": {id: {model, context_window}}, "rate_limits": {...} | null}.
CLAUDE_READER = r"""
import json, os, re, sys
d = os.path.expanduser("~/.claude/herdeck")
sd = os.path.expanduser("~/.claude/sessions")
def load(name, base=d):
    try:
        with open(os.path.join(base, name), encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else None
    except Exception:
        return None
out = {"sessions": {}, "rate_limits": None}
for sid in sys.argv[1:]:
    real = sid
    if sid.startswith("pid:"):
        pn = sid[4:]
        if not (pn.isascii() and pn.isdigit()):
            continue
        sj = load(pn + ".json", sd)
        real = sj.get("sessionId") if sj else None
        if not isinstance(real, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", real):
            continue
    j = load(real + ".json")
    if j:
        out["sessions"][sid] = {"model": j.get("model"), "context_window": j.get("context_window")}
try:
    names = [n for n in os.listdir(d) if n.endswith(".json") and not n.startswith(".")]
    names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
except OSError:
    names = []
for n in names[:5]:
    j = load(n)
    if j and isinstance(j.get("rate_limits"), dict):
        out["rate_limits"] = j["rate_limits"]
        break
print(json.dumps(out))
"""


def app_claude_key(row: dict) -> Optional[str]:
    pid = row.get("pid")
    if is_claude(row.get("kind")) and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        return "pid:%d" % pid
    return None


def log(*args: Any) -> None:
    print(time.strftime("%H:%M:%S"), *args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Hub: estado compartido + notificación a clientes SSE
# --------------------------------------------------------------------------
class Hub:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.version = 0
        self.sources: Dict[str, Dict[str, Any]] = {}

    def update_source(self, name: str, data: Dict[str, Any]) -> None:
        with self.cond:
            self.sources[name] = data
            self.version += 1
            self.cond.notify_all()

    def state(self) -> Dict[str, Any]:
        with self.cond:
            agents: List[Dict[str, Any]] = []
            summary = []
            for name, src in self.sources.items():
                agents.extend(src.get("agents", []))
                summary.append({k: v for k, v in src.items() if k not in ("agents", "raw")})
            agents.sort(key=lambda a: (STATUS_ORDER.get(a["status"], 9), -a.get("since", 0)))
            return {
                "version": self.version,
                "now": time.time(),
                "sources": summary,
                "agents": agents,
            }

    def wait_change(self, seen: int, timeout: float) -> int:
        with self.cond:
            if self.version == seen:
                self.cond.wait(timeout)
            return self.version


# --------------------------------------------------------------------------
# Cliente del socket de herdr (NDJSON)
# --------------------------------------------------------------------------
def rpc(sock_path: str, method: str, params: Optional[dict] = None, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
        req = {"id": "hd_" + secrets.token_hex(4), "method": method, "params": params or {}}
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        if not line:
            raise ConnectionError("respuesta vacía de herdr")
        msg = json.loads(line)
        if "error" in msg:
            err = msg["error"]
            raise RuntimeError("%s: %s" % (err.get("code"), err.get("message")))
        return msg.get("result", {})
    finally:
        s.close()


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _as_list(container: Any, *keys: str) -> List[dict]:
    """Devuelve una lista de dicts aunque herdr la entregue como lista o mapa."""
    if not isinstance(container, dict):
        return []
    for k in keys:
        v = container.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [dict(x, _key=kk) for kk, x in v.items() if isinstance(x, dict)]
    return []


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def model_short(model: Any) -> Optional[str]:
    """Short lowercase model family ("opus", "sonnet", "haiku") or the display name."""
    if not isinstance(model, dict):
        return None
    text = " ".join(str(model.get(k) or "") for k in ("id", "display_name")).lower()
    for fam in ("opus", "sonnet", "haiku"):
        if fam in text:
            return fam
    return _first(model, "display_name", "id")


def context_info(cw: Any) -> Optional[dict]:
    if not isinstance(cw, dict) or _num(cw.get("used_percentage")) is None:
        return None
    size = _num(cw.get("context_window_size"))
    return {"pct": round(_num(cw["used_percentage"]), 1), "size": int(size) if size else None}


def quota_info(rate_limits: Any, now: float) -> Optional[dict]:
    """Keeps the 5h / 7d windows that have a percentage and have not reset yet."""
    if not isinstance(rate_limits, dict):
        return None
    out = {}
    for key in ("five_hour", "seven_day"):
        w = rate_limits.get(key)
        if not isinstance(w, dict) or _num(w.get("used_percentage")) is None:
            continue
        resets = _num(w.get("resets_at"))
        if resets is not None and resets < now:
            continue
        out[key] = {"pct": round(_num(w["used_percentage"]), 1), "resets_at": resets}
    return out or None


def space_key(source: str, workspace_id: Any) -> Optional[str]:
    """Stable per-workspace filter key; None when the agent has no workspace id."""
    if workspace_id is None or workspace_id == "":
        return None
    return "ws:%s/%s" % (source, workspace_id)


def space_entries(agents: List[dict]) -> List[Dict[str, str]]:
    """Distinct spaces in first-appearance order: [{"key", "label"}]."""
    out: List[Dict[str, str]] = []
    seen = set()
    for a in agents:
        key = a.get("space_key")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "label": str(a.get("workspace") or a.get("workspace_id"))})
    return out


def is_claude(kind: Any) -> bool:
    return isinstance(kind, str) and kind.lower() in CLAUDE_KINDS


def hc_call(sock_path: str, method: str, params: Optional[dict] = None, timeout: float = 5.0) -> dict:
    """One JSON-RPC 2.0 request/reply (a single line each) on the hc socket."""
    req = {"jsonrpc": "2.0", "id": "hd_" + secrets.token_hex(6), "method": method, "params": params or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        raise ConnectionError("respuesta vacía de hc")
    reply = json.loads(line)
    err = reply.get("error")
    if err:
        data = err.get("data")
        kind = data.get("kind") if isinstance(data, dict) else None
        raise RuntimeError("%s: %s" % (kind or err.get("code"), err.get("message")))
    return reply.get("result") or {}


def machine_of(address: str) -> Optional[str]:
    return address.split("/", 1)[0] if "/" in address else None


def app_names(ls: Any) -> Dict[str, Dict[str, Any]]:
    """terminal address -> {space_id, space, tab} from `herdr-app ls`."""
    out: Dict[str, Dict[str, Any]] = {}
    spaces = ls.get("spaces") if isinstance(ls, dict) else None
    if not isinstance(spaces, list):
        return out
    for sp in spaces:
        if not isinstance(sp, dict):
            continue
        for tab in sp.get("tabs") or []:
            if not isinstance(tab, dict):
                continue
            for t in tab.get("terminals") or []:
                if isinstance(t, dict) and isinstance(t.get("terminal"), str):
                    out[t["terminal"]] = {"space_id": sp.get("id"), "space": sp.get("name"), "tab": tab.get("name")}
    return out


def normalize_hc(terminals: List[dict], names: Dict[str, dict]) -> Dict[str, Any]:
    """Map hc terminal.list entries to deck agent rows (agent terminals only)."""
    agents = []
    for t in terminals:
        st = t.get("status")
        if not isinstance(st, dict) or not st.get("app"):
            continue
        addr = t.get("address")
        labels = t.get("labels") if isinstance(t.get("labels"), dict) else {}
        info = names.get(addr) or {}
        space, tab = info.get("space"), info.get("tab")
        if not info:
            where = labels.get("where")
            if isinstance(where, str):
                space, _, tab = where.partition(" / ")
                tab = tab or None
        wid = info.get("space_id")
        if not wid:
            wid = labels.get("herdr.space")
            m = machine_of(str(addr or ""))
            # herdr-app ids for remote spaces are machine-prefixed; the hc label is bare
            if wid and m and not str(wid).startswith(m + "/"):
                wid = "%s/%s" % (m, wid)
            wid = wid or space
        fg = t.get("foreground") if isinstance(t.get("foreground"), dict) else {}
        pid = fg.get("pid")
        state = str(st.get("state") or "").lower()
        agents.append({
            "pane_id": addr,
            "name": t.get("name"),
            "kind": "claude" if st["app"] == "claude-code" else st["app"],
            "status": state if state in STATUS_ORDER else "unknown",
            "workspace_id": wid,
            "workspace": space,
            "tab": tab,
            "branch": None,
            "cwd": fg.get("cwd") or t.get("cwd"),
            "title": t.get("title") or st.get("title") or st.get("msg"),
            "focused": False,
            "session_id": None,
            "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None,
        })
    return {"agents": agents, "workspaces": len({a["workspace_id"] for a in agents})}


def split_by_machine(terminals: List[dict], machines: List[Optional[str]]) -> Dict[Optional[str], List[dict]]:
    out: Dict[Optional[str], List[dict]] = {m: [] for m in machines}
    for t in terminals:
        m = machine_of(str(t.get("address") or ""))
        if m in out:
            out[m].append(t)
    return out


def machine_errors(list_result: dict) -> Dict[str, str]:
    return {m["name"]: str(m.get("state")) for m in (list_result.get("machines") or [])
            if isinstance(m, dict) and m.get("name") and m.get("state") != "connected"}


def normalize_snapshot(result: dict) -> Dict[str, Any]:
    """Convierte la respuesta de session.snapshot en filas de agente.

    El esquema exacto varía entre versiones de herdr, así que la lectura es
    defensiva. /api/raw muestra el snapshot crudo para ajustar si hiciera falta.
    """
    snap = result.get("snapshot", result) if isinstance(result, dict) else {}
    workspaces = {}
    for w in _as_list(snap, "workspaces"):
        wid = _first(w, "workspace_id", "id", "_key")
        workspaces[wid] = {
            "label": _first(w, "label", "name", "title", default=wid),
            "cwd": _first(w, "cwd", "path", "root"),
            "branch": _first(w, "branch", "git_branch"),
        }
    tabs = {}
    for t in _as_list(snap, "tabs"):
        tid = _first(t, "tab_id", "id", "_key")
        tabs[tid] = _first(t, "label", "name", "title", default=tid)
    panes = {}
    for p in _as_list(snap, "panes"):
        pid = _first(p, "pane_id", "id", "_key")
        panes[pid] = p

    rows = _as_list(snap, "agents")
    if not rows:  # versiones sin lista de agentes: usa paneles con agente
        rows = [p for p in panes.values() if _first(p, "agent", "agent_status", "agent_kind")]

    focused = _first(snap, "focused_pane_id", "focused_pane")
    agents = []
    for a in rows:
        pid = _first(a, "pane_id", "target", "pane", "_key")
        pane = panes.get(pid, {})
        wid = _first(a, "workspace_id", default=_first(pane, "workspace_id"))
        tid = _first(a, "tab_id", default=_first(pane, "tab_id"))
        kind = _first(a, "agent", "kind", "agent_kind", default=_first(pane, "agent", default="agente"))
        if isinstance(kind, dict):
            kind = _first(kind, "id", "name", default="agente")
        status = _first(a, "agent_status", "status", "state",
                        default=_first(pane, "agent_status", default="unknown"))
        if isinstance(status, dict):
            status = _first(status, "state", "status", default="unknown")
        ws = workspaces.get(wid, {})
        sess = _first(a, "agent_session", default=pane.get("agent_session"))
        agents.append({
            "pane_id": pid,
            "name": _first(a, "name", "label", "agent_name"),
            "kind": str(kind),
            "status": str(status).lower(),
            "workspace_id": wid,
            "workspace": ws.get("label", wid),
            "branch": ws.get("branch"),
            "tab": tabs.get(tid, tid),
            "cwd": _first(a, "cwd", default=_first(pane, "cwd", default=ws.get("cwd"))),
            "title": _first(a, "summary", "terminal_title", default=_first(pane, "terminal_title", "title")),
            "focused": bool(_first(a, "focused", default=_first(pane, "focused", default=False))) or pid == focused,
            "session_id": _first(sess, "value") if isinstance(sess, dict) else None,
        })
    # Solo se suscribe a paneles con agente: pane.agent_status_changed exige pane_id
    # y un id inexistente invalida la suscripción entera.
    pane_ids = sorted({a["pane_id"] for a in agents if a["pane_id"]})
    return {"agents": agents, "pane_ids": pane_ids, "workspaces": len(workspaces)}


# --------------------------------------------------------------------------
# Fuente: un servidor herdr (local o remoto)
# --------------------------------------------------------------------------
class BaseSource:
    """State, publishing and Claude statusline info shared by every backend."""

    def __init__(self, cfg: dict, hub: Hub) -> None:
        self.cfg = cfg
        self.name_ = cfg["name"]
        self.hub = hub
        self.since: Dict[str, tuple] = {}  # pane_id -> (status, ts)
        self.error: Optional[str] = None
        self.connected = False
        self.dirty = threading.Event()
        self.pub_lock = threading.Lock()
        # Claude Code info (statusline files), refreshed by a helper thread
        self.claude_keys: List[str] = []
        self.claude_sessions: Dict[str, dict] = {}
        self.claude_rate: Optional[dict] = None
        self.claude_last = 0.0
        self.claude_busy = False
        self.claude_failed = False

    # --- hooks for backends -------------------------------------------------
    def snapshot_rows(self) -> Optional[Dict[str, Any]]:
        """{"agents": [...], "workspaces": int}, or None when there is nothing to show."""
        raise NotImplementedError

    def claude_key(self, row: dict) -> Optional[str]:
        """Key under which Claude statusline info is stored for this row, if any."""
        return None

    def focus(self, pane: str) -> dict:
        raise NotImplementedError

    # --- estado ------------------------------------------------------------
    def publish(self) -> None:
        with self.pub_lock:
            self._publish()
        self.maybe_refresh_claude()

    def _publish(self) -> None:
        now = time.time()
        data = {
            "name": self.name_,
            "label": self.cfg.get("label", self.name_),
            "color": self.cfg.get("color"),
            "icon": self.cfg.get("icon") or ("work" if self.cfg.get("ssh") else "home"),
            "connected": self.connected,
            "error": self.error,
            "remote": bool(self.cfg.get("ssh")),
            "quota": quota_info(self.claude_rate, now),
            "agents": [],
            "counts": {},
            "spaces": [],
        }
        norm = self.snapshot_rows()
        if norm is not None:
            seen = set()
            for a in norm["agents"]:
                key = a["pane_id"]
                seen.add(key)
                prev = self.since.get(key)
                if not prev or prev[0] != a["status"]:
                    self.since[key] = (a["status"], now)
                a["since"] = self.since[key][1]
                a["source"] = self.name_
                a["source_label"] = data["label"]
                a["id"] = "%s/%s" % (self.name_, key)
                a["space_key"] = space_key(self.name_, a.get("workspace_id"))
                info = self.claude_sessions.get(self.claude_key(a) or "")
                if info:
                    a["model"] = model_short(info.get("model"))
                    a["ctx"] = context_info(info.get("context_window"))
                data["counts"][a["status"]] = data["counts"].get(a["status"], 0) + 1
            for k in list(self.since):
                if k not in seen:
                    del self.since[k]
            data["agents"] = norm["agents"]
            data["spaces"] = space_entries(norm["agents"])
            data["workspaces"] = norm["workspaces"]
            self.claude_keys = sorted({k for k in (self.claude_key(a) for a in norm["agents"]) if k})
        self.hub.update_source(self.name_, data)

    # --- info de Claude Code (ficheros del statusline) ----------------------
    def maybe_refresh_claude(self) -> None:
        """Starts a background read of the statusline files, at most every ~10 s."""
        if self.claude_busy or time.time() - self.claude_last < CLAUDE_REFRESH_SECONDS:
            return
        if not self.connected or not self.claude_keys:
            return
        self.claude_busy, self.claude_last = True, time.time()
        threading.Thread(target=self._refresh_claude, args=(list(self.claude_keys),),
                         daemon=True, name="claude-" + self.name_).start()

    def _refresh_claude(self, ids: List[str]) -> None:
        try:
            if self.cfg.get("ssh"):
                cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", self.cfg["ssh"],
                       "python3", "-"] + ids
            else:
                cmd = [sys.executable, "-"] + ids
            r = subprocess.run(cmd, input=CLAUDE_READER, capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:200] or "código %d" % r.returncode)
            out = json.loads(r.stdout)
            sessions = out.get("sessions")
            self.claude_sessions = sessions if isinstance(sessions, dict) else {}
            self.claude_rate = out.get("rate_limits") if isinstance(out.get("rate_limits"), dict) else None
            self.claude_failed = False
        except Exception as e:  # noqa: BLE001 — sin datos, el deck se ve como siempre
            if not self.claude_failed:
                log("[%s] no se pudo leer la info de Claude: %s" % (self.name_, str(e)[:200]))
            self.claude_failed = True
            self.claude_sessions, self.claude_rate = {}, None
        finally:
            self.claude_busy = False
        with self.pub_lock:
            self._publish()


class Source(BaseSource, threading.Thread):
    """herdr backend: one herdr server, local or behind an ssh tunnel."""

    def __init__(self, cfg: dict, hub: Hub, herdr_bin: str) -> None:
        threading.Thread.__init__(self, daemon=True, name="src-" + cfg["name"])
        BaseSource.__init__(self, cfg, hub)
        self.herdr_bin = herdr_bin
        self.ssh_proc: Optional[subprocess.Popen] = None
        self.socket_path = os.path.expanduser(cfg.get("socket") or self._default_socket())
        self.raw: dict = {}

    def snapshot_rows(self) -> Optional[Dict[str, Any]]:
        return normalize_snapshot(self.raw) if self.connected and self.raw else None

    def claude_key(self, row: dict) -> Optional[str]:
        sid = row.get("session_id")
        if is_claude(row["kind"]) and sid and SESSION_ID_RE.fullmatch(str(sid)):
            return str(sid)
        return None

    # --- ubicación del socket -------------------------------------------
    def _default_socket(self) -> str:
        if self.cfg.get("ssh"):
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            return str(RUN_DIR / (self.name_ + ".sock"))
        sess = self.cfg.get("session")
        base = Path.home() / ".config" / "herdr"
        return str(base / "sessions" / sess / "herdr.sock") if sess else str(base / "herdr.sock")

    def _remote_socket(self) -> str:
        if self.cfg.get("remote_socket"):
            return self.cfg["remote_socket"]
        out = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.cfg["ssh"], 'printf %s "$HOME"'],
            capture_output=True, text=True, timeout=20)
        home = out.stdout.strip()
        if out.returncode != 0 or not home:
            raise ConnectionError("ssh %s falló: %s" % (self.cfg["ssh"], out.stderr.strip()[:200]))
        sess = self.cfg.get("remote_session")
        base = home + "/.config/herdr"
        return base + ("/sessions/%s/herdr.sock" % sess if sess else "/herdr.sock")

    def _ensure_tunnel(self) -> None:
        if not self.cfg.get("ssh"):
            return
        if self.ssh_proc and self.ssh_proc.poll() is None and os.path.exists(self.socket_path):
            return
        if self.ssh_proc and self.ssh_proc.poll() is None:
            self.ssh_proc.terminate()
        remote = self._remote_socket()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        cmd = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-o", "StreamLocalBindUnlink=yes",
               "-L", "%s:%s" % (self.socket_path, remote), self.cfg["ssh"]]
        log("[%s] túnel: %s" % (self.name_, " ".join(cmd)))
        self.ssh_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
        for _ in range(100):
            if os.path.exists(self.socket_path):
                return
            if self.ssh_proc.poll() is not None:
                err = (self.ssh_proc.stderr.read() or b"").decode(errors="replace")[:300]
                raise ConnectionError("túnel SSH cerrado: " + err.strip())
            time.sleep(0.1)
        raise ConnectionError("el túnel SSH no creó el socket local")

    def refresh(self) -> List[str]:
        try:
            self.raw = rpc(self.socket_path, "session.snapshot")
        except RuntimeError:  # versiones antiguas sin session.snapshot
            self.raw = {"snapshot": {
                "workspaces": rpc(self.socket_path, "workspace.list").get("workspaces", []),
                "panes": rpc(self.socket_path, "pane.list").get("panes", []),
                "agents": rpc(self.socket_path, "agent.list").get("agents", []),
            }}
        self.connected, self.error = True, None
        self.publish()
        return normalize_snapshot(self.raw)["pane_ids"]

    # --- bucle principal ---------------------------------------------------
    def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                self._ensure_tunnel()
                self._session()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 — se reintenta siempre
                self.connected, self.error = False, str(e)[:300]
                log("[%s] desconectado: %s" % (self.name_, self.error))
                self.publish()
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _session(self) -> None:
        pane_ids = self.refresh()
        subs = [{"type": t} for t in LIFECYCLE_EVENTS]
        subs += [{"type": "pane.agent_status_changed", "pane_id": p} for p in pane_ids]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        s.settimeout(1.0)
        s.sendall(json.dumps({"id": "hd_sub", "method": "events.subscribe",
                              "params": {"subscriptions": subs}}).encode() + b"\n")
        buf, started = b"", False
        last_refresh = time.time()
        poll = float(self.cfg.get("poll_seconds", 20))
        try:
            while True:
                try:
                    chunk = s.recv(65536)
                    if not chunk:
                        raise ConnectionError("herdr cerró la suscripción")
                    buf += chunk
                except socket.timeout:
                    pass
                lifecycle = False
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if "error" in msg:
                        raise ConnectionError("suscripción: %s" % msg["error"].get("code"))
                    if not started:
                        started = True
                        continue
                    ev = msg.get("event") or msg.get("type") or (msg.get("result") or {}).get("type", "")
                    if ev != "pane.agent_status_changed":
                        lifecycle = True
                    self.dirty.set()
                now = time.time()
                if self.dirty.is_set() or now - last_refresh > poll:
                    time.sleep(0.15)  # agrupa ráfagas
                    self.dirty.clear()
                    new_ids = self.refresh()
                    last_refresh = time.time()
                    if lifecycle and new_ids != pane_ids:
                        return  # re-suscribe con el nuevo conjunto de paneles
                self.maybe_refresh_claude()
                if self.ssh_proc and self.ssh_proc.poll() is not None:
                    raise ConnectionError("túnel SSH caído")
        finally:
            s.close()

    # --- acciones ----------------------------------------------------------
    def cli(self, args: List[str], timeout: float = 20) -> subprocess.CompletedProcess:
        env = dict(os.environ, HERDR_SOCKET_PATH=self.socket_path)
        return subprocess.run([self.herdr_bin] + args, capture_output=True, text=True,
                              timeout=timeout, env=env)

    def attach_cmd(self) -> List[str]:
        """herdr client command that attaches to this source's session."""
        if self.cfg.get("ssh"):
            cmd, sess = [self.herdr_bin, "--remote", self.cfg["ssh"]], self.cfg.get("remote_session")
        else:
            cmd, sess = [self.herdr_bin], self.cfg.get("session")
        return cmd + (["--session", sess] if sess else [])

    def has_local_client(self) -> bool:
        """True when a herdr client attached to this source already runs on this machine."""
        want = self.attach_cmd()[1:]
        out = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout
        return any(os.path.basename(argv[0]) == "herdr" and argv[1:] == want
                   for argv in (line.split() for line in out.splitlines()) if argv)

    def focus(self, pane: str) -> dict:
        try:
            return rpc(self.socket_path, "agent.focus", {"target": pane})
        except Exception:
            r = self.cli(["agent", "focus", pane])
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "focus falló")
            return {"ok": True}

    def read(self, pane: str, lines: int) -> str:
        r = self.cli(["agent", "read", pane, "--source", "recent-unwrapped", "--lines", str(lines)])
        if r.returncode != 0:
            r = self.cli(["pane", "read", pane, "--source", "recent", "--lines", str(lines)])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "read falló")
        out = r.stdout
        try:  # si devuelve JSON, extrae el texto
            j = json.loads(out)
            res = j.get("result", j)
            for k in ("text", "output", "content"):
                if isinstance(res.get(k), str):
                    return res[k]
            for v in res.values():
                if isinstance(v, dict):
                    for k in ("text", "output", "content"):
                        if isinstance(v.get(k), str):
                            return v[k]
        except (ValueError, AttributeError):
            pass
        return out

    def prompt(self, pane: str, text: str) -> dict:
        return rpc(self.socket_path, "agent.prompt", {"target": pane, "text": text})

    def keys(self, pane: str, keys: List[str]) -> dict:
        return rpc(self.socket_path, "agent.send_keys", {"target": pane, "keys": keys})

    def plugin_action(self, action_id: str) -> str:
        r = self.cli(["plugin", "action", "invoke", action_id], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "acción falló")
        return r.stdout.strip()


# --------------------------------------------------------------------------
# herdr-app backend: one hc server, split into sources by machine
# --------------------------------------------------------------------------
APP_DEFAULTS: Dict[str, Any] = {
    "port": 7778, "title": "herdeck app", "accent": "#bb9af7", "hc_socket": HC_SOCKET,
    "herdr_app_bin": "herdr-app", "app_name": "Herdr", "poll_seconds": 20,
}


def app_block(cfg: dict) -> Optional[dict]:
    """The herdr_app block with defaults applied, or None when absent or disabled."""
    block = cfg.get("herdr_app")
    if not isinstance(block, dict) or block.get("enabled") is False:
        return None
    return dict(APP_DEFAULTS, **block)


class AppSource(BaseSource):
    """One herdr-app machine (local when machine is None) fed by HcFeed."""

    def __init__(self, cfg: dict, hub: Hub, block: dict) -> None:
        BaseSource.__init__(self, cfg, hub)
        self.block = block
        self.machine: Optional[str] = cfg.get("machine") or None
        self.terminals: List[dict] = []
        self.names: Dict[str, dict] = {}

    def set_data(self, terminals: List[dict], names: Dict[str, dict], connected: bool,
                 error: Optional[str]) -> None:
        with self.pub_lock:
            self.terminals, self.names = terminals, names
            self.connected, self.error = connected, error

    def snapshot_rows(self) -> Optional[Dict[str, Any]]:
        return normalize_hc(self.terminals, self.names) if self.connected else None

    def claude_key(self, row: dict) -> Optional[str]:
        return app_claude_key(row)


_app_names_failed = False


def read_app_names(bin: str, timeout: float = 5.0) -> Dict[str, dict]:
    """Space/tab names from `herdr-app ls`; {} (and one log line) while it fails."""
    global _app_names_failed
    try:
        r = subprocess.run([bin, "ls"], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200] or "código %d" % r.returncode)
        names = app_names(json.loads(r.stdout))
    except Exception as e:  # noqa: BLE001 — sin nombres se agrupa por la etiqueta "where"
        if not _app_names_failed:
            log("[herdr-app] no se pudieron leer los nombres: %s" % str(e)[:200])
        _app_names_failed = True
        return {}
    if _app_names_failed:
        log("[herdr-app] nombres recuperados")
    _app_names_failed = False
    return names


class HcFeed(threading.Thread):
    """Single hc subscription feeding every AppSource (terminal.list on change)."""

    def __init__(self, block: dict, sources: List[AppSource]) -> None:
        threading.Thread.__init__(self, daemon=True, name="hc-feed")
        self.block = dict(APP_DEFAULTS, **block)
        self.sources = sources
        self.dirty = threading.Event()
        self.socket_path = os.path.expanduser(self.block["hc_socket"])
        b = self.block["herdr_app_bin"]
        self.app_bin = shutil.which(b) or b
        self.last_terminals: List[dict] = []

    def dispatch(self, list_result: dict, names: Dict[str, dict]) -> None:
        terminals = list_result.get("terminals") or []
        if not isinstance(terminals, list):
            raise ValueError("terminal.list sin lista de terminales")
        self.last_terminals = terminals
        errors = machine_errors(list_result)
        listed = {m.get("name") for m in (list_result.get("machines") or []) if isinstance(m, dict)}
        split = split_by_machine(terminals, [s.machine for s in self.sources])
        for s in self.sources:
            m = s.machine
            if m is not None and (m in errors or m not in listed):
                s.set_data([], names, False, "%s: %s" % (m, errors.get(m) or "no está en hc"))
            else:
                s.set_data(split[m], names, True, None)
            s.publish()

    def fail(self, error: str) -> None:
        self.last_terminals = []
        for s in self.sources:
            s.set_data([], {}, False, error[:300])
            s.publish()

    def refresh(self) -> None:
        r = hc_call(self.socket_path, "terminal.list")
        self.dispatch(r, read_app_names(self.app_bin))

    def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                self._session()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 — se reintenta siempre
                msg = "hc: %s" % (str(e) or type(e).__name__)
                log("[herdr-app] desconectado: %s" % msg[:300])
                self.fail(msg)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _session(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(5.0)
            s.connect(self.socket_path)
            s.sendall((json.dumps({"jsonrpc": "2.0", "id": "hd_sub", "method": "events.subscribe",
                                   "params": {}}) + "\n").encode())
            s.settimeout(1.0)
            buf, started = b"", False
            self.refresh()
            last_refresh = time.time()
            poll = float(self.block.get("poll_seconds") or 20)
            while True:
                try:
                    chunk = s.recv(65536)
                    if not chunk:
                        raise ConnectionError("hc cerró la suscripción")
                    buf += chunk
                except socket.timeout:
                    pass
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("error"):
                        raise ConnectionError("suscripción hc: %s" % msg["error"])
                    if not started:
                        started = True  # the reply's snapshot has no machines: ignore it
                        continue
                    if msg.get("method") != "heartbeat":
                        self.dirty.set()
                if self.dirty.is_set() or time.time() - last_refresh > poll:
                    time.sleep(0.15)  # agrupa ráfagas
                    self.dirty.clear()
                    self.refresh()
                    last_refresh = time.time()
                for src in self.sources:
                    src.maybe_refresh_claude()
        finally:
            s.close()


class AppDeck:
    """The herdr-app flavor of the deck, served on its own port."""

    def __init__(self, cfg: dict, block: dict) -> None:
        self.cfg = cfg
        self.block = dict(APP_DEFAULTS, **block)
        self.hub = Hub()
        self.sources = {s["name"]: AppSource(s, self.hub, self.block) for s in self.block["sources"]}
        self.feed = HcFeed(self.block, list(self.sources.values()))
        self.html = (HERE / "deck.html").read_text(encoding="utf-8")
        self.html_pin = (HERE / "pin.html").read_text(encoding="utf-8")

    def start(self) -> None:
        for s in self.sources.values():
            s.publish()
        self.feed.start()

    def client_config(self) -> dict:
        return {
            "allow_input": False,
            "quick_replies": [],
            "plugin_actions": [],
            "read_lines": 60,
            "title": self.block["title"],
            "version": VERSION,
            "features": {"sheet": False},
            "accent": self.block["accent"],
        }

    def manifest(self) -> dict:
        t = self.block["title"]
        return {"name": t, "short_name": t, "display": "standalone",
                "background_color": "#0b0d10", "theme_color": self.block["accent"], "start_url": "/"}

    def raw(self) -> dict:
        err = next((s.error for s in self.sources.values() if s.error), None)
        return {"socket": self.feed.socket_path, "error": err, "terminals": self.feed.last_terminals}

    def action(self, body: dict) -> dict:
        kind = body.get("action")
        if kind == "refresh":
            self.feed.dirty.set()
            return {"ok": True}
        raise ValueError("acción no disponible en herdr-app: %s" % kind)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
PIN_RE = re.compile(r"[0-9]{4,6}")
LOGIN_READ_TIMEOUT = 5.0
LOGIN_MAX_BODY = 1024


class PinGuard:
    """Per-IP and global lockout with exponential backoff for PIN attempts."""

    GLOBAL = "*"

    def __init__(self, clock: Callable[[], float] = time.monotonic, free_per_ip: int = 5,
                 free_global: int = 20, base: float = 30.0, cap: float = 3600.0) -> None:
        self._clock = clock
        self._free_ip = free_per_ip
        self._free_global = free_global
        self._base = base
        self._cap = cap
        self._lock = threading.Lock()
        self._fails: Dict[str, int] = {}
        self._until: Dict[str, float] = {}

    def _remaining(self, key: str, now: float) -> float:
        return max(0.0, self._until.get(key, 0.0) - now)

    def wait(self, ip: str) -> float:
        """Seconds left on the lock of ip or of the global key; never counts."""
        with self._lock:
            now = self._clock()
            return max(self._remaining(ip, now), self._remaining(self.GLOBAL, now))

    def failed(self, ip: str) -> float:
        """Record a wrong attempt; return the seconds the caller is now locked."""
        with self._lock:
            now = self._clock()
            for key, free in ((ip, self._free_ip), (self.GLOBAL, self._free_global)):
                n = self._fails.get(key, 0) + 1
                self._fails[key] = n
                if n >= free:
                    self._until[key] = now + min(self._cap, self._base * 2 ** (n - free))
            return max(self._remaining(ip, now), self._remaining(self.GLOBAL, now))

    def attempt(self, ip: str, check: Callable[[], bool]) -> "tuple[str, float]":
        """Atomic lock check + compare + record. Returns ("locked"|"ok"|"bad", seconds)."""
        with self._lock:
            now = self._clock()
            left = max(self._remaining(ip, now), self._remaining(self.GLOBAL, now))
            if left > 0:
                return "locked", left
            if check():
                for key in (ip, self.GLOBAL):
                    self._fails.pop(key, None)
                    self._until.pop(key, None)
                return "ok", 0.0
            for key, free in ((ip, self._free_ip), (self.GLOBAL, self._free_global)):
                n = self._fails.get(key, 0) + 1
                self._fails[key] = n
                if n >= free:
                    self._until[key] = now + min(self._cap, self._base * 2 ** (n - free))
            return "bad", max(self._remaining(ip, now), self._remaining(self.GLOBAL, now))

    def succeeded(self, ip: str) -> None:
        with self._lock:
            for key in (ip, self.GLOBAL):
                self._fails.pop(key, None)
                self._until.pop(key, None)


def _same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class App:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.hub = Hub()
        herdr_bin = cfg.get("herdr_bin") or shutil.which("herdr") or "herdr"
        self.sources = {s["name"]: Source(s, self.hub, herdr_bin) for s in cfg["sources"]}
        self.html = (HERE / "deck.html").read_text(encoding="utf-8")
        self.html_pin = (HERE / "pin.html").read_text(encoding="utf-8")

    def start(self) -> None:
        for s in self.sources.values():
            s.publish()
            s.start()

    def client_config(self) -> dict:
        c = self.cfg
        return {
            "allow_input": bool(c.get("allow_input", False)),
            "quick_replies": c.get("quick_replies", []),
            "plugin_actions": c.get("plugin_actions", []),
            "read_lines": int(c.get("read_lines", 60)),
            "title": c.get("title", "herdeck"),
            "version": VERSION,
            "features": {"sheet": True},
        }

    def manifest(self) -> dict:
        return {"name": "herdeck", "short_name": "herdeck", "display": "standalone",
                "background_color": "#0b0d10", "theme_color": "#0b0d10", "start_url": "/"}

    def raw(self) -> dict:
        return {n: {"socket": s.socket_path, "error": s.error, "raw": s.raw}
                for n, s in self.sources.items()}

    def open_terminal(self, src: "Source") -> dict:
        """Bring the terminal app forward, attaching a new herdr client if none is running."""
        app = self.cfg.get("terminal_app")
        if not app:
            return {"ok": True, "terminal": "disabled"}
        if src.has_local_client():
            cmd = ["open", "-a", app]
        else:
            cmd = ["open", "-na", app, "--args", "-e"] + src.attach_cmd()
        log("[%s] terminal: %s" % (src.name_, " ".join(cmd)))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "no se pudo abrir %s" % app)
        return {"ok": True, "terminal": "attached" if len(cmd) > 3 else "activated"}

    def action(self, body: dict) -> dict:
        kind = body.get("action")
        src = self.sources.get(body.get("source", ""))
        if kind == "plugin":
            pa = next((p for p in self.cfg.get("plugin_actions", []) if p.get("id") == body.get("id")), None)
            if not pa:
                raise ValueError("acción de plugin no configurada")
            return {"output": self.sources[pa["source"]].plugin_action(pa["action"])}
        if not src:
            raise ValueError("fuente desconocida")
        pane = str(body.get("pane_id", ""))
        if kind == "focus":
            return src.focus(pane)
        if kind == "open":
            src.focus(pane)
            return self.open_terminal(src)
        if kind == "read":
            return {"text": src.read(pane, int(body.get("lines") or self.cfg.get("read_lines", 60)))}
        if kind == "refresh":
            src.dirty.set()
            return {"ok": True}
        if kind in ("prompt", "keys"):
            if not self.cfg.get("allow_input", False):
                raise PermissionError("allow_input está desactivado en la configuración")
            if kind == "prompt":
                return src.prompt(pane, str(body.get("text", "")))
            return src.keys(pane, [str(k) for k in body.get("keys", [])])
        raise ValueError("acción desconocida: %s" % kind)


def make_handler(app: Any, token: str, pin: str, guard: PinGuard):
    cookie_hdr = {"Set-Cookie": "herdeck=%s; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly" % token}

    class H(http.server.BaseHTTPRequestHandler):
        server_version = "herdeck/" + VERSION

        def log_message(self, fmt: str, *args: Any) -> None:
            if app.cfg.get("access_log"):
                log("http", fmt % args)

        def _authed(self) -> bool:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if _same(q.get("t", [""])[0], token):
                return True
            if _same(self.headers.get("X-Herdeck-Token") or "", token):
                return True
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "herdeck" and _same(v, token):
                    return True
            return False

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if not self._authed():
                if path in ("/", "/index.html"):
                    ua = "".join(c if c.isprintable() else "?" for c in (self.headers.get("User-Agent") or "")[:80])
                    log("[auth] sin credenciales: %s %s UA=%s" % (self.client_address[0], path, ua))
                    return self._send(200, app.html_pin.encode(), "text/html; charset=utf-8")
                return self._send(401, "Falta el token: abre la URL con ?t=TOKEN".encode(), "text/plain; charset=utf-8")
            cookie = cookie_hdr
            if path in ("/", "/index.html"):
                return self._send(200, app.html.encode(), "text/html; charset=utf-8", cookie)
            if path == "/manifest.webmanifest":
                return self._json(200, app.manifest())
            if path == "/api/config":
                return self._json(200, app.client_config())
            if path == "/api/state":
                return self._json(200, app.hub.state())
            if path == "/api/raw":
                return self._json(200, app.raw())
            if path == "/api/stream":
                return self._stream()
            self._send(404, b"not found", "text/plain")

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            seen = -1
            try:
                while True:
                    v = app.hub.wait_change(seen, 15.0)
                    if v != seen:
                        seen = v
                        data = json.dumps(app.hub.state(), ensure_ascii=False)
                        self.wfile.write(("event: state\ndata: %s\n\n" % data).encode())
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _login(self) -> None:
            ip = self.client_address[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > LOGIN_MAX_BODY:
                return self._json(400, {"ok": False, "error": "bad_request"})
            try:
                self.connection.settimeout(LOGIN_READ_TIMEOUT)
                raw = self.rfile.read(n) if n else b""
            except OSError:  # includes socket.timeout: drop the connection quietly
                self.close_connection = True
                return
            try:
                got = json.loads(raw or b"{}").get("pin")
            except (ValueError, AttributeError):
                got = None
            verdict, secs = guard.attempt(ip, lambda: isinstance(got, str) and _same(got, pin))
            if verdict == "locked":
                return self._json(429, {"ok": False, "error": "locked", "retry_after": math.ceil(secs)})
            if verdict == "ok":
                return self._send(200, b'{"ok": true}', "application/json; charset=utf-8", cookie_hdr)
            return self._json(401, {"ok": False, "error": "bad_pin", "retry_after": math.ceil(secs)})

        def do_POST(self) -> None:  # noqa: N802
            if urllib.parse.urlparse(self.path).path == "/api/login":
                return self._login()
            if not self._authed():
                return self._json(401, {"error": "unauthorized"})
            if urllib.parse.urlparse(self.path).path != "/api/action":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                return self._json(200, {"ok": True, "result": app.action(body)})
            except PermissionError as e:
                return self._json(403, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"ok": False, "error": str(e)[:500]})

    return H


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def load_config(path: Path) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        example = json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
        example["token"] = secrets.token_urlsafe(18)
        path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        log("config creada en", path, "— revísala (sobre todo el host ssh del remoto)")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(18)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not cfg.get("pin"):
        cfg["pin"] = "%06d" % secrets.randbelow(10 ** 6)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif not PIN_RE.fullmatch(str(cfg["pin"])):
        raise SystemExit("config: 'pin' debe tener entre 4 y 6 dígitos")
    cfg["pin"] = str(cfg["pin"])
    names = [s.get("name") for s in cfg.get("sources", [])]
    if not names or len(set(names)) != len(names) or not all(names):
        raise SystemExit("config: cada fuente necesita un 'name' único")
    block = app_block(cfg)
    if block is not None:
        srcs = block.get("sources")
        if not isinstance(srcs, list) or not srcs or not all(isinstance(x, dict) for x in srcs):
            raise SystemExit("config: herdr_app necesita una lista 'sources'")
        names = [x.get("name") for x in srcs]
        if len(set(names)) != len(names) or not all(names):
            raise SystemExit("config: cada fuente de herdr_app necesita un 'name' único")
        machines = [x.get("machine") or None for x in srcs]
        if len(set(machines)) != len(machines):
            raise SystemExit("config: dos fuentes de herdr_app comparten 'machine'")
        try:
            block["port"] = int(block["port"])
        except (TypeError, ValueError):
            raise SystemExit("config: herdr_app.port debe ser un número")
        if block["port"] == int(cfg.get("port", 7777)):
            raise SystemExit("config: herdr_app.port no puede ser el mismo que 'port'")
        cfg["herdr_app"] = block
    return cfg


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Deck táctil para herdr")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--print-url", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    host, port = cfg.get("bind", "0.0.0.0"), int(cfg.get("port", 7777))
    shown = cfg.get("public_host") or (lan_ip() if host in ("0.0.0.0", "") else host)
    url = "http://%s:%d/?t=%s" % (shown, port, cfg["token"])
    if args.print_url:
        print(url)
        print("PIN: %s" % cfg["pin"])
        return
    app = App(cfg)
    app.start()
    guard = PinGuard()
    srv = Server((host, port), make_handler(app, cfg["token"], cfg["pin"], guard))
    log("herdeck %s escuchando en %s:%d" % (VERSION, host, port))
    block = app_block(cfg)
    if block is not None:
        deck2 = AppDeck(cfg, block)
        deck2.start()
        srv2 = Server((host, block["port"]), make_handler(deck2, cfg["token"], cfg["pin"], guard))
        threading.Thread(target=srv2.serve_forever, daemon=True, name="http-app").start()
        log("herdeck app escuchando en %s:%d" % (host, block["port"]))
    log("abre en la tablet:", url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for s in app.sources.values():
            if s.ssh_proc and s.ssh_proc.poll() is None:
                s.ssh_proc.terminate()


if __name__ == "__main__":
    main()
