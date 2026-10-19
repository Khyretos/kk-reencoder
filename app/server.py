"""
kk-reencoder HTTP server.

Provides the HTTP API for the re-encoder worker, serving static files and
exposing the application state and control methods via JSON endpoints.
"""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Ensure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rx_config import config_from_env
from rx_app import App
import rx_db
import rx_stage
import rx_pool
import rx_sys as rx_sys
import rx_settings


def make_server(app, host, port):
    """Create a ThreadingHTTPServer with a custom handler."""

    class QuietLog:
        def log_message(self, format, *args):
            pass  # Suppress logging

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass  # Quiet log

        def _send_json(self, status_code, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, path, status_code=200):
            try:
                full_path = os.path.join(os.path.dirname(__file__), "static", path.lstrip("/"))
                if not os.path.isfile(full_path):
                    raise FileNotFoundError()
                with open(full_path, "rb") as f:
                    content = f.read()
            except FileNotFoundError:
                self._send_json(404, {"error": "Not Found"})
                return

            ext_map = {
                ".html": "text/html",
                ".js": "application/javascript",
                ".css": "text/css",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".json": "application/json",
            }
            ext = os.path.splitext(path)[1].lower()
            ctype = ext_map.get(ext, "application/octet-stream")

            self.send_response(status_code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                return

            if path == "/":
                self._send_html("index.html")
                return

            if path == "/overview":
                self._send_html("overview.html")
                return

            m = re.match(r"/frames/([0-9a-f]{16})/([0-9]+-[ab]\.jpg)$", path)
            if m:
                full = os.path.join(app.cfg["frames_dir"], m.group(1), m.group(2))
                if not os.path.isfile(full):
                    self._send_json(404, {"error": "Not Found"})
                    return
                with open(full, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
                return

            if path.startswith("/static/"):
                name = path[len("/static/"):]
                # Security: only allow plain names matching [A-Za-z0-9_.-]+
                if not re.match(r"[A-Za-z0-9_.-]+$", name):
                    self._send_json(404, {"error": "Not Found"})
                    return

                full_path = os.path.join(os.path.dirname(__file__), "static", name)
                if not os.path.isfile(full_path):
                    self._send_json(404, {"error": "Not Found"})
                    return

                ext_map = {
                    ".html": "text/html",
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".json": "application/json",
                }
                ext = os.path.splitext(name)[1].lower()
                ctype = ext_map.get(ext, "application/octet-stream")

                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(os.path.getsize(full_path)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(full_path, "rb") as f:
                    self.wfile.write(f.read())
                return

            # API Routes
            if path == "/api/queue":
                self._send_json(200, app.queue())
                return

            if path == "/api/settings":
                self._send_json(200, app.settings())
                return

            if path == "/api/status":
                try:
                    result = app.status()
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/folders":
                try:
                    result = app.folders()
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/libraries":
                try:
                    result = app.libraries()
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/overview":
                try:
                    result = app.overview()
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/report":
                qs = parse_qs(parsed.query)
                library = qs.get("library", [None])[0]
                folder = qs.get("folder", [None])[0]
                if not library or not folder:
                    self._send_json(400, {"error": "Missing library or folder"})
                    return
                try:
                    result = app.report(library, folder)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/frames":
                qs = parse_qs(parsed.query)
                path_arg = qs.get("path", [None])[0]
                if not path_arg:
                    self._send_json(400, {"error": "Missing path"})
                    return
                try:
                    result = app.frames(path_arg)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            # Unknown GET -> 404
            self._send_json(404, {"error": "Not Found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            if path == "/api/pause":
                paused = data.get("paused")
                if paused is None:
                    self._send_json(400, {"error": "Missing paused"})
                    return
                try:
                    result = app.set_paused(paused)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/parallel":
                parallel = data.get("parallel")
                if parallel is None:
                    self._send_json(400, {"error": "Missing parallel"})
                    return
                try:
                    result = app.set_parallel(parallel)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/file":
                path_arg = data.get("path")
                action = data.get("action")
                if not path_arg or not action:
                    self._send_json(400, {"error": "Missing path or action"})
                    return
                try:
                    result = app.file_action(path_arg, action)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except rx_stage.StageError:
                    self._send_json(409, {"error": "Stage Error"})
                except ValueError:
                    self._send_json(400, {"error": "Invalid Action"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/folder-action":
                library = data.get("library")
                folder = data.get("folder")
                action = data.get("action")
                if not all([library, folder, action]):
                    self._send_json(400, {"error": "Missing parameters"})
                    return
                try:
                    result = app.folder_action(library, folder, action)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except rx_stage.StageError:
                    self._send_json(409, {"error": "Stage Error"})
                except ValueError:
                    self._send_json(400, {"error": "Invalid Action"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/folder":
                library = data.get("library")
                folder = data.get("folder")
                priority = data.get("priority")
                paused = data.get("paused")
                encode = data.get("encode")
                if not library or not folder:
                    self._send_json(400, {"error": "Missing library or folder"})
                    return
                try:
                    result = app.set_folder(library, folder, priority=priority, paused=paused, encode=encode)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except rx_stage.StageError:
                    self._send_json(409, {"error": "Stage Error"})
                except ValueError:
                    self._send_json(400, {"error": "Invalid Parameters"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/encode":
                folders = data.get("folders")
                encode_flag = data.get("encode")
                if not isinstance(folders, list):
                    self._send_json(400, {"error": "folders must be a list"})
                    return
                try:
                    result = app.encode_many(folders, encode_flag)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            if path == "/api/queue":
                order = data.get("order") if isinstance(data, dict) else None
                if not isinstance(order, list) or not all(isinstance(x, dict) and "library" in x and "folder" in x for x in order):
                    self._send_json(400, {"error": "order must be a list of {library, folder}"})
                    return
                self._send_json(200, app.reorder(order))
                return

            if path == "/api/settings":
                if self.command == "GET":
                    self._send_json(200, app.settings())
                else:
                    try:
                        if not isinstance(data, dict):
                            raise ValueError("expected a JSON object")
                        self._send_json(200, app.set_settings(data))
                    except ValueError as e:
                        self._send_json(400, {"error": str(e)})
                return

            if path == "/api/scan":
                library = data.get("library")
                folder = data.get("folder")
                try:
                    result = app.rescan(library, folder)
                    self._send_json(200, result)
                except KeyError:
                    self._send_json(404, {"error": "Not Found"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                return

            # Unknown POST -> 404
            self._send_json(404, {"error": "Not Found"})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    cfg = config_from_env(os.environ)

    # Make data dirs
    for key in ("frames_dir", "work_dir", "report_dir"):
        os.makedirs(cfg[key], exist_ok=True)
    os.makedirs(os.path.dirname(cfg["db"]) or ".", exist_ok=True)

    conn = rx_db.connect(cfg["db"])
    rx_settings.load(conn, cfg)
    worker = rx_pool.Worker(conn, cfg)
    worker.start()

    app = App(conn, cfg, worker)

    server = make_server(app, "0.0.0.0", cfg["port"])
    print(f"Serving on 0.0.0.0:{cfg['port']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        worker.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
