"""
ABS Tidy Library – Web Application
Credentials and naming config can be pre-loaded from environment variables.
"""

from __future__ import annotations

import os
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

from tidylibrary_core import (
    scan_library_abs, apply_changes, clean_empty_dirs,
    NamingConfig, log_event,
)
from abs_api import ABSClient

app = Flask(__name__)

# ── Environment-variable defaults ─────────────────────────────────────────────
# These are read once at startup so the UI can pre-fill them.
ENV_SERVER_URL   = os.environ.get("ABS_SERVER_URL",   "").strip().rstrip("/")
ENV_TOKEN        = os.environ.get("ABS_TOKEN",        "").strip()
ENV_LIBRARY_ID   = os.environ.get("ABS_LIBRARY_ID",   "").strip()
ENV_LIBRARY_PATH = os.environ.get("LIBRARY_PATH",     "/library").strip()

# ── In-memory stores ───────────────────────────────────────────────────────────
jobs:      dict = {}
jobs_lock  = threading.Lock()
cache:     dict = {}
cache_lock = threading.Lock()


# ── Job helpers ────────────────────────────────────────────────────────────────

def new_job() -> str:
    jid = str(uuid.uuid4())
    with jobs_lock:
        jobs[jid] = {"status": "running", "log": [], "result": {}}
    return jid


def emit_to(jid: str):
    def _emit(msg: str):
        with jobs_lock:
            if jid in jobs:
                jobs[jid]["log"].append(msg)
    return _emit


def finish_job(jid: str, result: dict, error: str = "") -> None:
    with jobs_lock:
        if jid in jobs:
            jobs[jid]["status"] = "error" if error else "done"
            jobs[jid]["result"] = result
            if error:
                jobs[jid]["log"].append(f"ERROR: {error}")


# ── Routes: UI ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    naming = NamingConfig.from_env()
    return render_template(
        "index.html",
        env_server_url   = ENV_SERVER_URL,
        env_token        = ENV_TOKEN,
        env_library_id   = ENV_LIBRARY_ID,
        env_library_path = ENV_LIBRARY_PATH,
        naming           = naming.to_dict(),
        naming_presets   = NamingConfig().PRESETS,
    )


# ── Routes: env defaults ──────────────────────────────────────────────────────

@app.route("/api/env-defaults")
def api_env_defaults():
    naming = NamingConfig.from_env()
    return jsonify({
        "server_url":   ENV_SERVER_URL,
        "token":        ENV_TOKEN,
        "library_id":   ENV_LIBRARY_ID,
        "library_path": ENV_LIBRARY_PATH,
        "naming":       naming.to_dict(),
        "presets":      NamingConfig().PRESETS,
    })


# ── Routes: connection ────────────────────────────────────────────────────────

@app.route("/api/abs/test", methods=["POST"])
def api_abs_test():
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()
    if not server_url or not token:
        return jsonify({"ok": False, "error": "server_url and token are required"}), 400
    try:
        ok, msg = ABSClient(server_url, token, timeout=10).ping()
        return jsonify({"ok": ok, "message": msg} if ok else {"ok": False, "error": msg})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/abs/diagnose", methods=["POST"])
def api_abs_diagnose():
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()
    if not server_url or not token:
        return jsonify({"error": "server_url and token are required"}), 400
    import requests as req
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    results = {}
    for name, path in [("me", "/api/me"), ("libraries", "/api/libraries")]:
        url = f"{server_url}{path}"
        try:
            r = req.get(url, headers=headers, timeout=8)
            results[name] = {"url": url, "status": r.status_code, "body_snippet": r.text[:400]}
        except Exception as e:
            results[name] = {"url": url, "error": str(e)}
    return jsonify(results)


@app.route("/api/abs/libraries", methods=["POST"])
def api_abs_libraries():
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()
    if not server_url or not token:
        return jsonify({"error": "server_url and token are required"}), 400
    try:
        libs = ABSClient(server_url, token).get_libraries()
        return jsonify({
            "libraries": [
                {"id": l.id, "name": l.name, "root_path": l.root_path}
                for l in libs
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Routes: scan ──────────────────────────────────────────────────────────────

@app.route("/api/abs/scan", methods=["POST"])
def api_abs_scan():
    data         = request.get_json(force=True)
    server_url   = data.get("server_url",   "").strip().rstrip("/")
    token        = data.get("token",        "").strip()
    library_id   = data.get("library_id",   "").strip()
    library_path = data.get("library_path", "").strip()
    naming_dict  = data.get("naming", {})

    if not all([server_url, token, library_id, library_path]):
        return jsonify({"error": "server_url, token, library_id, and library_path are required"}), 400

    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": f"Filesystem path not accessible: {library_path}"}), 400

    naming = NamingConfig.from_dict(naming_dict) if naming_dict else NamingConfig.from_env()

    jid  = new_job()
    emit = emit_to(jid)

    def _run():
        try:
            client    = ABSClient(server_url, token)
            abs_items = client.get_library_items(library_id, emit)
            stats, planned = scan_library_abs(abs_items, root_path, naming, emit)

            ck = f"abs:{server_url}:{library_id}"
            with cache_lock:
                cache[ck] = (stats, planned)

            finish_job(jid, {
                "cache_key":     ck,
                "stats":         stats.to_dict(),
                "changes_count": len(planned),
                "changes":       [bm.to_dict(root_path) for bm in planned],
                "server_url":    server_url,
                "token":         token,
                "library_id":    library_id,
                "library_path":  library_path,
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: apply ─────────────────────────────────────────────────────────────

@app.route("/api/apply", methods=["POST"])
def api_apply():
    data         = request.get_json(force=True)
    cache_key    = data.get("cache_key",    "").strip()
    library_path = data.get("library_path", "").strip()
    dry_run      = bool(data.get("dry_run", False))
    server_url   = data.get("server_url",   "").strip()
    token        = data.get("token",        "").strip()
    library_id   = data.get("library_id",   "").strip()

    if not cache_key or not library_path:
        return jsonify({"error": "cache_key and library_path are required"}), 400

    with cache_lock:
        cached = cache.get(cache_key)
    if not cached:
        return jsonify({"error": "No scan result found. Please scan first."}), 400

    _, planned = cached
    if not planned:
        return jsonify({"error": "No changes to apply."}), 400

    root_path = Path(library_path)
    log_file  = root_path / "tidy_library_log.txt"
    jid       = new_job()
    emit      = emit_to(jid)

    def _run():
        try:
            exec_stats = apply_changes(planned, root_path, log_file, dry_run, emit)
            with cache_lock:
                cache.pop(cache_key, None)

            rescan_triggered = False
            if not dry_run and server_url and token and library_id:
                emit("Triggering Audiobookshelf library rescan…")
                try:
                    rescan_triggered = ABSClient(server_url, token).trigger_library_scan(library_id)
                    emit("✓ ABS library rescan started." if rescan_triggered
                         else "⚠ Could not trigger ABS rescan — do it manually.")
                except Exception as e:
                    emit(f"⚠ ABS rescan error: {e}")

            finish_job(jid, {
                "exec_stats": exec_stats,
                "dry_run": dry_run,
                "rescan_triggered": rescan_triggered,
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: clean empty dirs ──────────────────────────────────────────────────

@app.route("/api/clean-empty", methods=["POST"])
def api_clean_empty():
    data         = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()
    dry_run      = bool(data.get("dry_run", False))
    if not library_path:
        return jsonify({"error": "library_path is required"}), 400
    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": "Invalid library path"}), 400

    log_file = root_path / "tidy_library_log.txt"
    jid      = new_job()
    emit     = emit_to(jid)

    def _run():
        try:
            s = clean_empty_dirs(root_path, log_file, dry_run, emit)
            finish_job(jid, {"cleanup_stats": s, "dry_run": dry_run})
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: job polling ───────────────────────────────────────────────────────

@app.route("/api/job/<jid>")
def api_job_status(jid: str):
    offset = int(request.args.get("offset", 0))
    with jobs_lock:
        job = jobs.get(jid)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "log":    job["log"][offset:],
        "result": job["result"],
    })


# ── Routes: log viewer ────────────────────────────────────────────────────────

@app.route("/api/log")
def api_log():
    library_path = request.args.get("library_path", "").strip()
    lines        = int(request.args.get("lines", 120))
    if not library_path:
        return jsonify({"error": "library_path required"}), 400
    log_file = Path(library_path) / "tidy_library_log.txt"
    if not log_file.exists():
        return jsonify({"content": "(No log file yet)"})
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return jsonify({"content": "".join(all_lines[-lines:])})
    except IOError as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
