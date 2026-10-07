#!/usr/bin/env python3
# ============================ CONFIG — edit this ============================
PORT       = 9092
HTML_FILE  = "gemma-chat.html"
CONFIG_FILE = "agent_config.json"
HISTORY_DIR = "chat-history"          # sessions live here, in the project folder
HISTORY_FILE = "sessions.json"
HISTORY_BACKUPS = 20                  # rolling snapshots kept alongside
RUN_TIMEOUT_DEFAULT = 30   # seconds, matches agent_tools.DEFAULT_TIMEOUT_S
# ===========================================================================

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

import agent_tools as at

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / CONFIG_FILE
HISTORY_PATH = HERE / HISTORY_DIR / HISTORY_FILE
_config_lock = threading.Lock()
_history_lock = threading.Lock()

print("", flush=True)
print("  ◈ Gemma Agent — file + sandboxed-run tools", flush=True)
print(f"  folder : {HERE}", flush=True)

app = Flask(__name__, static_folder=str(HERE), static_url_path="")


# ── CONFIG (project root) ───────────────────────────────────────────────
def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _get_project_root():
    """Returns a resolved Path if a valid root is configured, else None."""
    cfg = _load_config()
    root = cfg.get("project_root")
    if not root:
        return None
    p = Path(root)
    if not p.is_dir():
        return None
    return p.resolve()


# ── CORS ───────────────────────────────────────────────────────────────────
# The page can be opened two ways: served from this server (same origin, no CORS
# needed) or straight off disk as file:///…/gemma-chat.html, which the browser
# labels Origin: null. Allow that explicitly so the file:// mode can still reach
# /api/sessions and /api/agent/*. Only null + localhost are allowed — not "*".
_ALLOWED_ORIGINS = {"null", "http://localhost:%d" % PORT, "http://127.0.0.1:%d" % PORT}


@app.after_request
def _cors(resp):
    origin = request.headers.get("Origin")
    if origin and (origin in _ALLOWED_ORIGINS or origin.startswith("file://")):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def _cors_preflight(_any):
    return ("", 204)


# ── CHAT HISTORY (persisted in the project folder, not the browser) ────────
def _history_write(payload: dict):
    """Atomic write + a rolling backup, so a crash or a bad payload can never
    destroy the only copy of the chat history."""
    d = HISTORY_PATH.parent
    d.mkdir(parents=True, exist_ok=True)
    if HISTORY_PATH.exists():
        bdir = d / "backups"
        bdir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            shutil.copy2(HISTORY_PATH, bdir / f"sessions.{ts}.json")
        except OSError:
            pass
        snaps = sorted(bdir.glob("sessions.*.json"))
        for old in snaps[:-HISTORY_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    os.replace(tmp, HISTORY_PATH)


@app.route("/api/sessions", methods=["GET"])
def sessions_get():
    if not HISTORY_PATH.exists():
        return jsonify({"ok": True, "sessions": {}, "updated": None})
    try:
        data = json.loads(HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return jsonify({"ok": False, "error": f"history unreadable: {e}", "sessions": {}})
    return jsonify({"ok": True, "sessions": data.get("sessions", {}), "updated": data.get("updated")})


@app.route("/api/sessions", methods=["POST"])
def sessions_post():
    data = request.get_json(silent=True) or {}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return jsonify({"ok": False, "error": "sessions object required"})
    payload = {"updated": datetime.now().isoformat(timespec="seconds"), "sessions": sessions}
    with _history_lock:
        try:
            _history_write(payload)
        except OSError as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    size = HISTORY_PATH.stat().st_size
    return jsonify({"ok": True, "count": len(sessions), "bytes": size,
                    "path": str(HISTORY_PATH.relative_to(HERE))})


# ── STATIC / HEALTH ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(HERE), HTML_FILE)


@app.route("/health")
def health():
    return jsonify({"ok": True})


# ── AGENT CONFIG ──────────────────────────────────────────────────────────
@app.route("/api/agent/config", methods=["GET"])
def agent_config_get():
    root = _get_project_root()
    return jsonify({"configured": root is not None, "project_root": str(root) if root else None})


@app.route("/api/agent/config", methods=["POST"])
def agent_config_post():
    data = request.get_json(silent=True) or {}
    raw = (data.get("project_root") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "project_root is required"})
    p = Path(raw).expanduser()
    if not p.is_dir():
        return jsonify({"ok": False, "error": "not a directory (or doesn't exist)"})
    resolved = p.resolve()
    with _config_lock:
        cfg = _load_config()
        cfg["project_root"] = str(resolved)
        _save_config(cfg)
    try:
        at.write_run_script(resolved)
    except Exception as e:
        # non-fatal — the root is still validly configured even if the
        # convenience script couldn't be (re)written
        return jsonify({"ok": True, "project_root": str(resolved), "warning": f"run-sandboxed.sh not written: {e}"})
    return jsonify({"ok": True, "project_root": str(resolved)})


def _require_root():
    root = _get_project_root()
    if root is None:
        return None, jsonify({"ok": False, "error": "no project root configured"})
    return root, None


# ── READ-ONLY TOOLS ──────────────────────────────────────────────────────
@app.route("/api/agent/list_dir", methods=["POST"])
def agent_list_dir():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(at.list_dir(root, data.get("path", "")))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agent/read_file", methods=["POST"])
def agent_read_file():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(at.read_file(root, data.get("path", "")))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── WRITE/EDIT PREVIEW (dry run — never touches disk) ───────────────────
@app.route("/api/agent/preview_write", methods=["POST"])
def agent_preview_write():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if "content" not in data:
        return jsonify({"ok": False, "error": "content is required"})
    try:
        return jsonify(at.preview_write(root, data.get("path", ""), data["content"]))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agent/preview_edit", methods=["POST"])
def agent_preview_edit():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    for f in ("old_string", "new_string"):
        if f not in data:
            return jsonify({"ok": False, "error": f"{f} is required"})
    try:
        return jsonify(at.preview_edit(root, data.get("path", ""), data["old_string"], data["new_string"]))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── WRITE/EDIT APPLY (real — only ever called after UI approval) ────────
@app.route("/api/agent/write_file", methods=["POST"])
def agent_write_file():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    if "content" not in data:
        return jsonify({"ok": False, "error": "content is required"})
    try:
        return jsonify(at.write_file(root, data.get("path", ""), data["content"]))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agent/edit_file", methods=["POST"])
def agent_edit_file():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    for f in ("old_string", "new_string"):
        if f not in data:
            return jsonify({"ok": False, "error": f"{f} is required"})
    try:
        return jsonify(at.edit_file(root, data.get("path", ""), data["old_string"], data["new_string"]))
    except at.PathViolation as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── SANDBOXED RUN (real — only ever called after UI approval) ───────────
@app.route("/api/agent/run_command", methods=["POST"])
def agent_run_command():
    root, err = _require_root()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    command = (data.get("command") or "").strip()
    if not command:
        return jsonify({"ok": False, "error": "command is required"})
    timeout_s = data.get("timeout_s") or RUN_TIMEOUT_DEFAULT
    snapshot = None
    if data.get("snapshot", True):
        try:
            snapshot = at.snapshot_project(root)
        except Exception as e:
            snapshot = {"ok": False, "error": str(e)}
    try:
        result = at.run_in_sandbox(root, command, timeout_s=timeout_s)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    result["snapshot"] = snapshot
    return jsonify(result)


if __name__ == "__main__":
    print(f"  ✓ serving http://localhost:{PORT}/", flush=True)
    print(f"    (agent tools mounted at /api/agent/*)\n", flush=True)
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
