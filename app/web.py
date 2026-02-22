"""
ABS Tidy Library - Web Application
Flask backend exposing the core tidylibrary functions via a REST API.
"""

import os
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

# Import core functions
from tidylibrary_core import (
    scan_library, apply_changes, clean_empty_dirs,
    LibraryStats, BookMove,
)

app = Flask(__name__)

# ── In-memory job store ────────────────────────────────────────────────────────
# { job_id: { "status": "running"|"done"|"error", "log": [...], "result": {} } }
jobs: dict = {}
jobs_lock = threading.Lock()

# ── Scan cache (so scan result can be reused by apply/clean) ──────────────────
scan_cache: dict = {}  # { library_path: (stats, planned_moves) }
scan_cache_lock = threading.Lock()


def new_job() -> str:
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "running", "log": [], "result": {}}
    return job_id


def emit_to_job(job_id: str, msg: str) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["log"].append(msg)


def finish_job(job_id: str, result: dict, error: str = "") -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = "error" if error else "done"
            jobs[job_id]["result"] = result
            if error:
                jobs[job_id]["log"].append(f"ERROR: {error}")


# ── Routes ─────────────────────────────────────────────────────────────────────

DEFAULT_LIBRARY_PATH = os.environ.get("LIBRARY_PATH", "/library")


@app.route("/")
def index():
    return render_template("index.html", default_path=DEFAULT_LIBRARY_PATH)


@app.route("/api/default-path")
def api_default_path():
    return jsonify({"path": DEFAULT_LIBRARY_PATH})


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Start a scan job. Returns job_id immediately."""
    data = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()

    if not library_path:
        return jsonify({"error": "library_path is required"}), 400

    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": f"Path does not exist or is not a directory: {library_path}"}), 400

    job_id = new_job()

    def _run():
        try:
            emit = lambda msg: emit_to_job(job_id, msg)
            stats, planned_moves = scan_library(root_path, emit)

            # Cache result
            with scan_cache_lock:
                scan_cache[library_path] = (stats, planned_moves)

            result = {
                "stats": stats.to_dict(),
                "changes_count": len(planned_moves),
                "changes": [bm.to_dict(root_path) for bm in planned_moves],
            }
            finish_job(job_id, result)
        except Exception as e:
            finish_job(job_id, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/apply", methods=["POST"])
def api_apply():
    """Apply or dry-run planned changes for a previously scanned library."""
    data = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()
    dry_run = bool(data.get("dry_run", False))

    if not library_path:
        return jsonify({"error": "library_path is required"}), 400

    with scan_cache_lock:
        cached = scan_cache.get(library_path)

    if not cached:
        return jsonify({"error": "No scan result found. Please scan first."}), 400

    _, planned_moves = cached
    if not planned_moves:
        return jsonify({"error": "No changes to apply."}), 400

    root_path = Path(library_path)
    log_file  = root_path / "tidy_library_log.txt"
    job_id    = new_job()

    def _run():
        try:
            emit = lambda msg: emit_to_job(job_id, msg)
            exec_stats = apply_changes(planned_moves, root_path, log_file, dry_run, emit)

            # Invalidate cache so next scan is fresh
            with scan_cache_lock:
                scan_cache.pop(library_path, None)

            finish_job(job_id, {"exec_stats": exec_stats, "dry_run": dry_run})
        except Exception as e:
            finish_job(job_id, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/clean-empty", methods=["POST"])
def api_clean_empty():
    """Clean empty directories."""
    data = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()
    dry_run = bool(data.get("dry_run", False))

    if not library_path:
        return jsonify({"error": "library_path is required"}), 400

    root_path = Path(library_path)
    if not root_path.exists() or not root_path.is_dir():
        return jsonify({"error": "Invalid library path"}), 400

    log_file = root_path / "tidy_library_log.txt"
    job_id   = new_job()

    def _run():
        try:
            emit = lambda msg: emit_to_job(job_id, msg)
            cleanup_stats = clean_empty_dirs(root_path, log_file, dry_run, emit)
            finish_job(job_id, {"cleanup_stats": cleanup_stats, "dry_run": dry_run})
        except Exception as e:
            finish_job(job_id, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def api_job_status(job_id: str):
    """Poll job status and accumulated log lines."""
    offset = int(request.args.get("offset", 0))
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "log": job["log"][offset:],
        "result": job["result"],
    })


@app.route("/api/log")
def api_log():
    """Return the last N lines of the tidy_library_log.txt."""
    library_path = request.args.get("library_path", "").strip()
    lines = int(request.args.get("lines", 100))
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


@app.route("/api/stream/<job_id>")
def api_stream(job_id: str):
    """Server-Sent Events stream for a job."""
    def _generate():
        offset = 0
        while True:
            with jobs_lock:
                job = jobs.get(job_id)
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
            import time
            time.sleep(0.5)

    return Response(stream_with_context(_generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
