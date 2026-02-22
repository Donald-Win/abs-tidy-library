"""
ABS Tidy Library – Web Application
Flask backend with dual-mode operation:
  • FILE mode  — scan metadata.json on the filesystem (original behaviour)
  • ABS mode   — connect to Audiobookshelf via API, get metadata from there,
                 still move files via filesystem access
"""

from __future__ import annotations

import os
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

from tidylibrary_core import (
    scan_library, scan_library_abs, apply_changes, clean_empty_dirs,
    log_event,
)
from abs_api import ABSClient

app = Flask(__name__)

DEFAULT_LIBRARY_PATH = os.environ.get("LIBRARY_PATH", "/library")

# ── In-memory stores ───────────────────────────────────────────────────────────
# jobs   — { job_id: { status, log, result } }
# cache  — { cache_key: (stats, planned_moves) }
jobs:       dict = {}
jobs_lock   = threading.Lock()
cache:      dict = {}
cache_lock  = threading.Lock()


# ── Job helpers ────────────────────────────────────────────────────────────────

def new_job() -> str:
    jid = str(uuid.uuid4())
    with jobs_lock:
        jobs[jid] = {"status": "running", "log": [], "result": {}}
    return jid


def emit_to(jid: str) -> callable:
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


def cache_key_file(library_path: str) -> str:
    return f"file:{library_path}"


def cache_key_abs(server_url: str, library_id: str) -> str:
    return f"abs:{server_url}:{library_id}"


# ── Routes: UI ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", default_path=DEFAULT_LIBRARY_PATH)


@app.route("/api/default-path")
def api_default_path():
    return jsonify({"path": DEFAULT_LIBRARY_PATH})


# ── Routes: ABS connection ────────────────────────────────────────────────────

@app.route("/api/abs/test", methods=["POST"])
def api_abs_test():
    """Test connection to an ABS server."""
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()

    if not server_url or not token:
        return jsonify({"ok": False, "error": "server_url and token are required"}), 400

    try:
        client      = ABSClient(server_url, token, timeout=10)
        ok, message = client.ping()
        if ok:
            return jsonify({"ok": True, "message": message})
        return jsonify({"ok": False, "error": message})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/abs/diagnose", methods=["POST"])
def api_abs_diagnose():
    """
    Detailed diagnostic endpoint — returns raw responses from several ABS
    endpoints so connection issues can be debugged directly in the browser.
    """
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()

    if not server_url or not token:
        return jsonify({"error": "server_url and token are required"}), 400

    import requests as req
    results = {}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    for name, path in [
        ("ping",       "/api/ping"),
        ("me",         "/api/me"),
        ("libraries",  "/api/libraries"),
    ]:
        url = f"{server_url}{path}"
        try:
            r = req.get(url, headers=headers, timeout=8)
            results[name] = {
                "url":        url,
                "status":     r.status_code,
                "body_snippet": r.text[:300],
            }
        except Exception as e:
            results[name] = {"url": url, "error": str(e)}

    return jsonify(results)


@app.route("/api/abs/libraries", methods=["POST"])
def api_abs_libraries():
    """Return the list of book libraries on an ABS server."""
    data       = request.get_json(force=True)
    server_url = data.get("server_url", "").strip().rstrip("/")
    token      = data.get("token", "").strip()

    if not server_url or not token:
        return jsonify({"error": "server_url and token are required"}), 400

    try:
        client = ABSClient(server_url, token)
        libs   = client.get_libraries()
        return jsonify({
            "libraries": [
                {
                    "id":        lib.id,
                    "name":      lib.name,
                    "root_path": lib.root_path,
                }
                for lib in libs
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Routes: File-mode scan ────────────────────────────────────────────────────

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Start a file-mode scan job."""
    data         = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()

    if not library_path:
        return jsonify({"error": "library_path is required"}), 400

    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": f"Path does not exist or is not a directory: {library_path}"}), 400

    jid = new_job()
    emit = emit_to(jid)

    def _run():
        try:
            stats, planned = scan_library(root_path, emit)
            ck = cache_key_file(library_path)
            with cache_lock:
                cache[ck] = (stats, planned)
            finish_job(jid, {
                "cache_key":     ck,
                "stats":         stats.to_dict(),
                "changes_count": len(planned),
                "changes":       [bm.to_dict(root_path) for bm in planned],
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: ABS-mode scan ─────────────────────────────────────────────────────

@app.route("/api/abs/scan", methods=["POST"])
def api_abs_scan():
    """
    ABS-mode scan:
      1. Fetch items from ABS API
      2. Plan file moves using filesystem paths embedded in each item
      3. Return stats + proposed changes
    """
    data         = request.get_json(force=True)
    server_url   = data.get("server_url", "").strip().rstrip("/")
    token        = data.get("token", "").strip()
    library_id   = data.get("library_id", "").strip()
    library_path = data.get("library_path", "").strip()  # filesystem root

    if not server_url or not token or not library_id:
        return jsonify({"error": "server_url, token, and library_id are required"}), 400
    if not library_path:
        return jsonify({"error": "library_path (filesystem root) is required"}), 400

    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": f"Filesystem path not accessible: {library_path}"}), 400

    jid  = new_job()
    emit = emit_to(jid)

    def _run():
        try:
            client    = ABSClient(server_url, token)
            abs_items = client.get_library_items(library_id, emit)

            stats, planned = scan_library_abs(abs_items, root_path, emit)

            ck = cache_key_abs(server_url, library_id)
            with cache_lock:
                cache[ck] = (stats, planned)

            finish_job(jid, {
                "cache_key":      ck,
                "stats":          stats.to_dict(),
                "changes_count":  len(planned),
                "changes":        [bm.to_dict(root_path) for bm in planned],
                # Pass these back so the apply call can trigger a rescan
                "server_url":     server_url,
                "token":          token,
                "library_id":     library_id,
                "library_path":   library_path,
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: Apply / dry-run ───────────────────────────────────────────────────

@app.route("/api/apply", methods=["POST"])
def api_apply():
    """Apply (or dry-run) planned changes for a previously scanned library."""
    data         = request.get_json(force=True)
    cache_key    = data.get("cache_key", "").strip()
    library_path = data.get("library_path", "").strip()
    dry_run      = bool(data.get("dry_run", False))

    # ABS post-apply rescan fields (optional)
    server_url   = data.get("server_url", "").strip()
    token        = data.get("token", "").strip()
    library_id   = data.get("library_id", "").strip()

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

            # Invalidate cache so next scan is fresh
            with cache_lock:
                cache.pop(cache_key, None)

            # Trigger ABS library rescan after real (non-dry) moves
            rescan_triggered = False
            if not dry_run and server_url and token and library_id:
                emit("Triggering Audiobookshelf library rescan…")
                try:
                    client = ABSClient(server_url, token)
                    rescan_triggered = client.trigger_library_scan(library_id)
                    if rescan_triggered:
                        emit("✓ ABS library rescan started.")
                    else:
                        emit("⚠ Could not trigger ABS rescan — do it manually in ABS settings.")
                except Exception as e:
                    emit(f"⚠ ABS rescan error: {e}")

            finish_job(jid, {
                "exec_stats":       exec_stats,
                "dry_run":          dry_run,
                "rescan_triggered": rescan_triggered,
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: Clean empty dirs ──────────────────────────────────────────────────

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
            cleanup_stats = clean_empty_dirs(root_path, log_file, dry_run, emit)
            finish_job(jid, {"cleanup_stats": cleanup_stats, "dry_run": dry_run})
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: Job polling ───────────────────────────────────────────────────────

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


@app.route("/api/stream/<jid>")
def api_stream(jid: str):
    """Server-Sent Events stream for live terminal output."""
    def _generate():
        import time
        offset = 0
        while True:
            with jobs_lock:
                job = jobs.get(jid)
            if not job:
                yield "data: [Job not found]\n\n"
                return
            new_lines = job["log"][offset:]
            for line in new_lines:
                yield f"data: {line}\n\n"
            offset += len(new_lines)
            if job["status"] in ("done", "error"):
                yield "data: [DONE]\n\n"
                return
            time.sleep(0.5)

    return Response(stream_with_context(_generate()), mimetype="text/event-stream")


# ── Routes: Log file viewer ───────────────────────────────────────────────────

@app.route("/api/log")
def api_log():
    library_path = request.args.get("library_path", "").strip()
    lines        = int(request.args.get("lines", 120))
    if not library_path:
        return jsonify({"error": "library_path required"}), 400
    log_file = Path(library_path) / "tidy_library_log.txt"
    if not log_file.exists():
        return jsonify({"content": "(No log file found yet)"})
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
