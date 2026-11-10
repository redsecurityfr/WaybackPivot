#!/usr/bin/env python3
"""Interface locale : Wayback filenames -> dorks urlscan.io."""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import wayback_pivot as wp

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8765

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def run_job(job_id: str, body: dict) -> None:
    domain = wp.registrable_hint(str(body.get("domain") or ""))
    en = str(body.get("lang") or "fr") == "en"

    def progress(pages: int, urls: int) -> None:
        with LOCK:
            job = JOBS.get(job_id)
            if job:
                job["message"] = f"Wayback CDX · page {pages} · {urls} " + ("URLs" if en else "URL")

    try:
        if not domain or "." not in domain:
            raise ValueError("Invalid domain." if en else "Domaine invalide.")
        limit = max(1, min(int(body.get("limit") or 5000), 50000))
        top = max(1, min(int(body.get("top") or 40), 500))
        rows = wp.fetch_cdx(
            domain=domain,
            match="host" if body.get("match") == "host" else "domain",
            limit=limit,
            page_size=min(5000, limit),
            status=(str(body.get("status") or "200").strip() or None),
            date_from=(str(body.get("date_from") or "").strip() or None),
            date_to=(str(body.get("date_to") or "").strip() or None),
            delay=0.4,
            timeout=60,
            on_page=progress,
        )
        hits = wp.collect(
            rows,
            include_all=bool(body.get("all")),
            min_len=max(1, int(body.get("min_len") or 6)),
            sample_n=2,
        )[:top]
        field = str(body.get("field") or "filename").strip() or "filename"
        exclude = not bool(body.get("keep_domain"))
        digests: dict[int, str | None] = {}
        done = 0

        def one(index: int, hit: wp.Hit) -> tuple[int, str | None]:
            sample = hit.samples[0] if hit.samples else ""
            return index, wp.sha256_snapshot(hit.last, sample)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(one, i, hit) for i, hit in enumerate(hits)]
            for fut in as_completed(futures):
                index, digest = fut.result()
                digests[index] = digest
                done += 1
                with LOCK:
                    job = JOBS.get(job_id)
                    if job:
                        job["message"] = f"SHA256 {done}/{len(hits)}"

        pivots = []
        for i, hit in enumerate(hits):
            dork = wp.build_dork(hit.name, domain, exclude, field)
            digest = digests.get(i)
            hdork = wp.hash_dork(digest, domain, exclude) if digest else ""
            pivots.append(
                {
                    "filename": hit.name,
                    "dork": dork,
                    "urlscan": wp.search_url(dork),
                    "sha256": digest or "",
                    "hash_dork": hdork,
                    "hash_url": wp.search_url(hdork) if hdork else "",
                    "score": hit.score,
                    "count": hit.count,
                    "first": hit.first,
                    "last": hit.last,
                    "samples": hit.samples,
                }
            )
        with LOCK:
            JOBS[job_id].update(
                status="done",
                message=(f"{len(rows)} URLs · {len(pivots)} dorks" if en else f"{len(rows)} URL · {len(pivots)} dorks"),
                result={"domain": domain, "urls": len(rows), "count": len(pivots), "pivots": pivots},
            )
    except Exception as exc:  # noqa: BLE001 — surface any CDX/input failure to the UI
        with LOCK:
            JOBS[job_id].update(status="error", message=str(exc), result=None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            data = (STATIC / "index.html").read_bytes()
            self._send(200, data, "text/html; charset=utf-8")
            return
        files = {
            "/wayback.ico": "image/x-icon",
            "/wayback.svg": "image/svg+xml",
            "/wayback-toolbar-logo.svg": "image/svg+xml",
            "/fr.svg": "image/svg+xml",
            "/gb.svg": "image/svg+xml",
            "/urlscan.png": "image/png",
            "/wayback.png": "image/png",
            "/favicon.jpg": "image/jpeg",
            "/discord.webp": "image/webp",
            "/logo-osint.webp": "image/webp",
        }
        if path in files:
            self._send(200, (STATIC / path.lstrip("/")).read_bytes(), files[path])
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._json(404, {"error": "introuvable"})
                    return
                self._json(200, {k: job[k] for k in ("id", "status", "message", "result")})
            return
        self._json(404, {"error": "introuvable"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/scan":
            self._json(404, {"error": "introuvable"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "JSON invalide"})
            return
        job_id = uuid.uuid4().hex[:12]
        with LOCK:
            JOBS[job_id] = {"id": job_id, "status": "running", "message": "Connexion à la Wayback Machine…", "result": None}
        threading.Thread(target=run_job, args=(job_id, body), daemon=True).start()
        self._json(202, {"id": job_id})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Wayback pivot  {url}")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
