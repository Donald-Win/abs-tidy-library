"""
ABS Tidy Library – Web Application
Credentials and naming config can be pre-loaded from environment variables.
Config is persisted to /config/naming.json between container restarts.
"""

from __future__ import annotations

import os
import uuid
import json
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response

from tidylibrary_core import (
    scan_library_abs, apply_changes, rollback_moves, clean_empty_dirs,
    NamingConfig, load_naming_config, save_naming_config, log_event,
    detect_metadata_issues, check_filesystem_compatibility,
)
from abs_api import ABSClient

app = Flask(__name__)

# ── Environment-variable defaults ─────────────────────────────────────────────
ENV_SERVER_URL   = os.environ.get("ABS_SERVER_URL",   "").strip().rstrip("/")
ENV_TOKEN        = os.environ.get("ABS_TOKEN",        "").strip()
ENV_LIBRARY_ID   = os.environ.get("ABS_LIBRARY_ID",   "").strip()
ENV_LIBRARY_PATH = os.environ.get("LIBRARY_PATH",     "/library").strip()

# ── In-memory stores ───────────────────────────────────────────────────────────
jobs:      dict = {}
jobs_lock  = threading.Lock()
cache:     dict = {}
cache_lock = threading.Lock()

# Last move log for rollback (keyed by library_path)
move_logs:      dict = {}
move_logs_lock  = threading.Lock()


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
    naming = load_naming_config()
    return render_template(
        "index.html",
        env_server_url   = ENV_SERVER_URL,
        env_token        = ENV_TOKEN,
        env_library_id   = ENV_LIBRARY_ID,
        env_library_path = ENV_LIBRARY_PATH,
        naming           = naming.to_dict(),
        naming_presets   = NamingConfig().PRESETS,
    )


# ── Routes: config persistence ────────────────────────────────────────────────

@app.route("/api/config/naming", methods=["GET"])
def api_config_get():
    naming = load_naming_config()
    return jsonify(naming.to_dict())


@app.route("/api/config/naming", methods=["POST"])
def api_config_save():
    data = request.get_json(force=True)
    naming = NamingConfig.from_dict(data)
    save_naming_config(naming)
    return jsonify({"ok": True})


# ── Routes: env defaults ──────────────────────────────────────────────────────

@app.route("/api/env-defaults")
def api_env_defaults():
    naming = load_naming_config()
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


# ── Routes: cover art proxy ───────────────────────────────────────────────────

@app.route("/api/abs/cover/<item_id>")
def api_abs_cover(item_id: str):
    import requests as req
    server_url = request.args.get("server_url", ENV_SERVER_URL).strip().rstrip("/")
    token      = request.args.get("token",      ENV_TOKEN).strip()

    if not server_url or not token or not item_id:
        return _cover_placeholder()

    try:
        url  = f"{server_url}/api/items/{item_id}/cover"
        resp = req.get(url,
                       headers={"Authorization": f"Bearer {token}"},
                       timeout=8, stream=True)
        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "image/jpeg")
            return Response(resp.content, mimetype=ct,
                            headers={"Cache-Control": "public, max-age=3600"})
    except Exception:
        pass
    return _cover_placeholder()


def _cover_placeholder():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="110" viewBox="0 0 80 110">'
        '<rect width="80" height="110" rx="4" fill="#1a1d27"/>'
        '<text x="40" y="62" text-anchor="middle" font-size="36" fill="#2e3350">📚</text>'
        '</svg>'
    )
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


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

    naming = NamingConfig.from_dict(naming_dict) if naming_dict else load_naming_config()

    # Auto-save naming config whenever a scan runs
    save_naming_config(naming)

    jid  = new_job()
    emit = emit_to(jid)

    def _run():
        try:
            client = ABSClient(server_url, token)

            abs_library_root = ""
            try:
                libs = client.get_libraries()
                for lib in libs:
                    if lib.id == library_id:
                        abs_library_root = lib.root_path
                        break
            except Exception:
                pass
            if abs_library_root:
                emit(f"ABS library root: {abs_library_root}")
                emit(f"Container root:   {library_path}")

            abs_items = client.get_library_items(library_id, emit)
            stats, planned, collisions = scan_library_abs(
                abs_items, root_path, naming, emit,
                abs_library_root=abs_library_root,
            )

            ck = f"abs:{server_url}:{library_id}"
            with cache_lock:
                cache[ck] = (stats, planned)

            # Metadata quality check and filesystem compatibility
            metadata_issues  = detect_metadata_issues(abs_items)
            fs_warnings      = check_filesystem_compatibility(planned, root_path)

            finish_job(jid, {
                "cache_key":       ck,
                "stats":           stats.to_dict(),
                "changes_count":   len(planned),
                "changes":         [bm.to_dict(root_path) for bm in planned],
                "collisions":      collisions,
                "metadata_issues": [m.to_dict() for m in metadata_issues],
                "fs_warnings":     fs_warnings,
                "server_url":      server_url,
                "token":           token,
                "library_id":      library_id,
                "library_path":    library_path,
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: apply ─────────────────────────────────────────────────────────────

@app.route("/api/apply", methods=["POST"])
def api_apply():
    data          = request.get_json(force=True)
    cache_key     = data.get("cache_key",      "").strip()
    library_path  = data.get("library_path",   "").strip()
    dry_run       = bool(data.get("dry_run", False))
    server_url    = data.get("server_url",     "").strip()
    token         = data.get("token",          "").strip()
    library_id    = data.get("library_id",     "").strip()
    selected_ids  = data.get("selected_ids",   None)   # None = apply all
    metadata_edits = data.get("metadata_edits", [])    # [{item_id, title, author, series_name, series_sequence}]

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
            client = ABSClient(server_url, token) if (server_url and token) else None

            # ── Step 1: Apply metadata edits to ABS before moving files ─────────
            # This ensures filenames derived from tokens are based on the corrected
            # metadata, and the ABS database is already updated before the move.
            if metadata_edits and client and not dry_run:
                emit(f"Applying {len(metadata_edits)} metadata edit(s) to ABS...")
                for edit in metadata_edits:
                    iid = edit.get("item_id", "")
                    if not iid:
                        continue
                    ok, msg = client.update_item_metadata(
                        item_id         = iid,
                        title           = edit.get("title"),
                        author          = edit.get("author"),
                        series_name     = edit.get("series_name"),
                        series_sequence = edit.get("series_sequence"),
                    )
                    label = edit.get("title") or iid
                    emit(f"  {'OK' if ok else 'WARN'} [{label}]: {msg}")

            # ── Step 2: Move files, triggering per-item ABS scan after each ─────
            # Per-item scan is far better than a full library rescan:
            # - ABS matches the moved file by inode (same filesystem = no duplicate)
            # - Progress is preserved
            # - Only the moved book is re-indexed, not the entire library
            items_scanned = []

            def on_book_moved(abs_item_id: str):
                if client:
                    ok, msg = client.scan_item(abs_item_id)
                    items_scanned.append(abs_item_id)
                    emit(f"  ABS item scan: {'OK' if ok else 'WARN - ' + msg}")

            exec_stats, move_log = apply_changes(
                planned, root_path, log_file, dry_run, emit,
                selected_ids=selected_ids,
                on_book_moved=on_book_moved,
            )

            # ── Step 3: Persist rollback log ─────────────────────────────────────
            if not dry_run and move_log:
                with move_logs_lock:
                    move_logs[library_path] = move_log
                try:
                    rollback_file = root_path / ".tidy_rollback.json"
                    with open(rollback_file, "w", encoding="utf-8") as f:
                        json.dump(move_log, f, indent=2)
                except Exception:
                    pass

            with cache_lock:
                cache.pop(cache_key, None)

            # ── Step 4: Full library rescan only as fallback ──────────────────────
            # Per-item scans already ran above. A full rescan is only triggered if
            # we have ABS credentials but per-item scanning didn't run (e.g. dry run
            # or no item IDs available).
            rescan_triggered = bool(items_scanned)
            if not dry_run and client and not items_scanned and library_id:
                emit("No per-item scans completed -- falling back to full library rescan...")
                try:
                    ok, msg = client.trigger_library_scan(library_id)
                    rescan_triggered = ok
                    emit(f"  {'OK' if ok else 'WARN'}: {msg}")
                except Exception as e:
                    emit(f"  WARN: ABS rescan error: {e}")

            finish_job(jid, {
                "exec_stats":       exec_stats,
                "dry_run":          dry_run,
                "rescan_triggered": rescan_triggered,
                "items_scanned":    len(items_scanned),
                "has_rollback":     bool(move_log and not dry_run),
            })
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


# ── Routes: rollback ──────────────────────────────────────────────────────────

@app.route("/api/rollback", methods=["POST"])
def api_rollback():
    data         = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()
    if not library_path:
        return jsonify({"error": "library_path required"}), 400

    root_path = Path(library_path)
    log_file  = root_path / "tidy_library_log.txt"

    # Try in-memory first, then disk
    move_log = None
    with move_logs_lock:
        move_log = move_logs.get(library_path)

    if not move_log:
        rollback_file = root_path / ".tidy_rollback.json"
        if rollback_file.exists():
            try:
                with open(rollback_file, "r", encoding="utf-8") as f:
                    move_log = json.load(f)
            except Exception:
                pass

    if not move_log:
        return jsonify({"error": "No rollback data found. Run Apply first."}), 400

    jid  = new_job()
    emit = emit_to(jid)

    def _run():
        try:
            stats = rollback_moves(move_log, log_file, emit)
            # Clear rollback log after successful use
            with move_logs_lock:
                move_logs.pop(library_path, None)
            try:
                (root_path / ".tidy_rollback.json").unlink(missing_ok=True)
            except Exception:
                pass
            finish_job(jid, {"rollback_stats": stats})
        except Exception as e:
            finish_job(jid, {}, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": jid})


@app.route("/api/rollback/status", methods=["POST"])
def api_rollback_status():
    """Check if rollback data is available for a library."""
    data         = request.get_json(force=True)
    library_path = data.get("library_path", "").strip()
    if not library_path:
        return jsonify({"available": False})

    with move_logs_lock:
        if library_path in move_logs:
            n = sum(len(e["moves"]) for e in move_logs[library_path])
            return jsonify({"available": True, "books": len(move_logs[library_path]), "files": n})

    rollback_file = Path(library_path) / ".tidy_rollback.json"
    if rollback_file.exists():
        try:
            with open(rollback_file, "r", encoding="utf-8") as f:
                log = json.load(f)
            n = sum(len(e["moves"]) for e in log)
            return jsonify({"available": True, "books": len(log), "files": n})
        except Exception:
            pass

    return jsonify({"available": False})


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
