#!/usr/bin/env python3
"""Turn Wayback Machine filenames into urlscan.io pivot dorks.

A domain can be absent from urlscan.io and still have left unique file
names in the Wayback CDX index. Searching those names on urlscan.io
surfaces other hosts that requested the same file.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


ART = [
    "██╗    ██╗ █████╗ ██╗   ██╗██████╗  █████╗  ██████╗██╗  ██╗",
    "██║    ██║██╔══██╗╚██╗ ██╔╝██╔══██╗██╔══██╗██╔════╝██║ ██╔╝",
    "██║ █╗ ██║███████║ ╚████╔╝ ██████╔╝███████║██║     █████╔╝ ",
    "██║███╗██║██╔══██║  ╚██╔╝  ██╔══██╗██╔══██║██║     ██╔═██╗ ",
    "╚███╔███╔╝██║  ██║   ██║   ██████╔╝██║  ██║╚██████╗██║  ██╗",
    " ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝",
]


def _rainbow(line: str, phase: float = 0.0, freq: float = 0.022) -> str:
    out = []
    last = None
    for i, ch in enumerate(line):
        if ch == " ":
            out.append(ch)
            continue
        hue = (i * freq + phase) % 1.0
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, 1.0))
        code = (r, g, b)
        if code != last:
            out.append(f"\033[1;38;2;{r};{g};{b}m")
            last = code
        out.append(ch)
    out.append(Colors.RESET)
    return "".join(out)


def print_banner() -> None:
    art = "\n".join(_rainbow(row, phase=idx * 0.07) for idx, row in enumerate(ART))
    print(
        f"""
{art}
{Colors.CYAN}      Wayback filenames  ·  {Colors.MAGENTA}urlscan.io dorks{Colors.CYAN}  ·  {Colors.GREEN}SHA256 pivot{Colors.RESET}
{Colors.YELLOW}+{'=' * 74}+
{Colors.YELLOW}|  {Colors.GREEN}\x1b]8;;https://x.com/RosoOSINT\x1b\\@RosoOSINT\x1b]8;;\x1b\\{Colors.YELLOW} - {Colors.GREEN}\x1b]8;;https://roso.info\x1b\\roso.info\x1b]8;;\x1b\\{Colors.YELLOW} - {Colors.GREEN}\x1b]8;;https://osint.paris/\x1b\\osint.paris\x1b]8;;\x1b\\{Colors.YELLOW}{' ' * 36}|
{Colors.YELLOW}|{' ' * 74}|
{Colors.YELLOW}|  {Colors.RED}Join the OSINT community:{Colors.YELLOW}{' ' * 47}|
{Colors.YELLOW}|  {Colors.GREEN}\x1b]8;;https://discord.com/invite/rPkY5jaTfF\x1b\\https://discord.com/invite/rPkY5jaTfF\x1b]8;;\x1b\\{Colors.YELLOW}{' ' * 35}|
{Colors.YELLOW}+{'=' * 74}+{Colors.RESET}
"""
    )


def enable_windows_utf8() -> None:
    if sys.platform != "win32":
        return
    try:
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        os.system("chcp 65001 > nul 2>&1")
    except Exception:
        pass


CDX = "https://web.archive.org/cdx/search/cdx"
URLSCAN_SEARCH = "https://urlscan.io/search/#"
USER_AGENT = "wayback-pivot/1.0 (+https://github.com/wayback-pivot; public CDX research)"

# Names that show up on almost every site and do not pivot.
NOISE = {
    "index.html",
    "index.htm",
    "index.php",
    "index.asp",
    "index.aspx",
    "index.jsp",
    "default.html",
    "default.aspx",
    "home.html",
    "favicon.ico",
    "robots.txt",
    "sitemap.xml",
    "sitemap_index.xml",
    "ads.txt",
    "app-ads.txt",
    "security.txt",
    "humans.txt",
    "browserconfig.xml",
    "manifest.json",
    "site.webmanifest",
    "crossdomain.xml",
    "clientaccesspolicy.xml",
    "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png",
    "logo.png",
    "logo.svg",
    "style.css",
    "styles.css",
    "main.css",
    "app.css",
    "bootstrap.css",
    "bootstrap.min.css",
    "normalize.css",
    "jquery.js",
    "jquery.min.js",
    "jquery-ui.js",
    "jquery-ui.min.js",
    "angular.js",
    "angular.min.js",
    "react.js",
    "react.min.js",
    "react-dom.js",
    "vue.js",
    "vue.min.js",
    "modernizr.js",
    "analytics.js",
    "gtag.js",
    "gtm.js",
    "fbevents.js",
    "hotjar.js",
    "recaptcha.js",
    "wp-embed.min.js",
    "wp-emoji-release.min.js",
    "comment-reply.min.js",
}

# Extensions worth pivoting. Generic HTML pages are skipped unless --all.
INTERESTING = {
    "js",
    "mjs",
    "cjs",
    "css",
    "map",
    "json",
    "wasm",
    "xml",
    "yml",
    "yaml",
    "toml",
    "ini",
    "conf",
    "config",
    "env",
    "properties",
    "php",
    "phtml",
    "asp",
    "aspx",
    "jsp",
    "ashx",
    "asmx",
    "cgi",
    "pl",
    "py",
    "rb",
    "sql",
    "bak",
    "old",
    "orig",
    "swp",
    "zip",
    "tar",
    "gz",
    "tgz",
    "7z",
    "rar",
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "csv",
    "log",
    "txt",
    "graphql",
    "gql",
    "vue",
    "svelte",
    "ts",
    "tsx",
    "jsx",
    "woff",
    "woff2",
    "svg",
    "webp",
    "gif",
    "png",
    "jpg",
    "jpeg",
    "ico",
}

HASH_RE = re.compile(r"(?:^|[._-])([a-f0-9]{8,}|[a-z0-9]{8,})(?:[._-]|$)", re.I)
# Build-tool chunks: main.a1b2c3d4.js, chunk-abc123.js, app~vendor.hash.js
CHUNK_RE = re.compile(
    r"(?:^|[._~-])(?:chunk|bundle|vendor|runtime|main|app|index|polyfills?)[._~-][a-z0-9]{5,}",
    re.I,
)


@dataclass
class Hit:
    name: str
    count: int = 0
    samples: list[str] = field(default_factory=list)
    first: str = ""
    last: str = ""

    @property
    def score(self) -> int:
        score = 0
        stem, _, ext = self.name.rpartition(".")
        if HASH_RE.search(stem) or CHUNK_RE.search(self.name):
            score += 50
        if len(self.name) >= 16:
            score += 15
        elif len(self.name) >= 10:
            score += 8
        if ext.lower() in {"js", "mjs", "json", "map", "wasm", "xml", "yml", "yaml", "env", "bak", "sql"}:
            score += 10
        # Seen once is often more distinctive than a file on every page.
        if self.count == 1:
            score += 5
        return score


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def registrable_hint(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/")[0].split(":")[0]
    if domain.startswith("*."):
        domain = domain[2:]
    return domain.strip(".")


def filename_of(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path or "")
    if not path or path.endswith("/"):
        return None
    name = path.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        return None
    if len(name) > 180:
        return None
    # Drop trailing junk from badly archived paths.
    name = name.split("#", 1)[0]
    if not name or " " in name or "\n" in name or "\r" in name:
        return None
    return name


def extension_of(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def keep(name: str, include_all: bool, min_len: int) -> bool:
    if len(name) < min_len:
        return False
    low = name.lower()
    if low in NOISE:
        return False
    if low.startswith("jquery-") or low.startswith("jquery."):
        return False
    if include_all:
        return True
    return extension_of(name) in INTERESTING


def escape_dork_value(value: str) -> str:
    # Elastic query-string reserved characters.
    return re.sub(r'([+\-=&|><!(){}\[\]^"~*?:\\/])', r"\\\1", value)


def build_dork(name: str, domain: str, exclude_domain: bool, field: str) -> str:
    quoted = escape_dork_value(name)
    dork = f'{field}:"{quoted}"'
    if exclude_domain and domain:
        dork += f" AND NOT page.domain:{domain}"
    return dork


def search_url(dork: str) -> str:
    return URLSCAN_SEARCH + urllib.parse.quote(dork, safe="")


def hash_dork(digest: str, domain: str, exclude_domain: bool) -> str:
    dork = f"hash:{digest}"
    if exclude_domain and domain:
        dork += f" AND NOT page.domain:{domain}"
    return dork


def sha256_snapshot(timestamp: str, url: str, timeout: float = 25, max_bytes: int = 1_500_000) -> str | None:
    """SHA256 of one Wayback capture. urlscan.io pivots on this hash, not the CDX SHA1."""
    if not timestamp or not url:
        return None
    raw = f"https://web.archive.org/web/{timestamp}id_/{url}"
    req = urllib.request.Request(raw, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hasher = hashlib.sha256()
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return None
                hasher.update(chunk)
            if total == 0:
                return None
            return hasher.hexdigest()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def fetch_cdx(
    domain: str,
    match: str,
    limit: int,
    page_size: int,
    status: str | None,
    date_from: str | None,
    date_to: str | None,
    delay: float,
    timeout: float,
    on_page=None,
) -> list[dict]:
    if match == "host":
        url_prefix = f"{domain}/*"
    elif match == "domain":
        url_prefix = f"*.{domain}/*"
    else:
        raise ValueError(match)

    rows: list[dict] = []
    resume: str | None = None
    pages = 0

    while len(rows) < limit:
        params = {
            "url": url_prefix,
            "output": "json",
            "fl": "original,timestamp,statuscode,mimetype",
            "collapse": "urlkey",
            "showResumeKey": "true",
            "limit": str(min(page_size, limit - len(rows))),
        }
        if status:
            params["filter"] = f"statuscode:{status}"
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to
        if resume:
            params["resumeKey"] = resume

        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(CDX + "?" + query, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8", errors="replace").strip()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise SystemExit(f"CDX HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"CDX injoignable: {exc.reason}") from exc

        if not payload:
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Réponse CDX illisible: {exc}") from exc
        if not isinstance(data, list) or not data:
            break

        # First page starts with a header row. Later pages do not.
        start = 0
        if data and isinstance(data[0], list) and data[0][:1] == ["original"]:
            start = 1

        resume = None
        body = data[start:]
        if body and isinstance(body[-1], list) and len(body[-1]) == 1 and isinstance(body[-1][0], str):
            resume = body[-1][0]
            body = body[:-1]
        elif body and isinstance(body[-1], str):
            resume = body[-1]
            body = body[:-1]

        for item in body:
            if not isinstance(item, list) or len(item) < 4:
                continue
            original, timestamp, statuscode, mimetype = item[0], item[1], item[2], item[3]
            rows.append(
                {
                    "url": original,
                    "timestamp": str(timestamp),
                    "status": str(statuscode),
                    "mime": str(mimetype),
                }
            )
            if len(rows) >= limit:
                break

        pages += 1
        eprint(f"[*] CDX page {pages}: {len(rows)} URL")
        if on_page:
            on_page(pages, len(rows))
        if not resume or len(rows) >= limit:
            break
        if delay:
            time.sleep(delay)

    return rows


def collect(rows: list[dict], include_all: bool, min_len: int, sample_n: int) -> list[Hit]:
    grouped: dict[str, Hit] = {}
    for row in rows:
        name = filename_of(row["url"])
        if not name or not keep(name, include_all, min_len):
            continue
        key = name.lower()
        hit = grouped.get(key)
        if hit is None:
            hit = Hit(name=name, first=row["timestamp"], last=row["timestamp"])
            grouped[key] = hit
        hit.count += 1
        if row["timestamp"] < hit.first:
            hit.first = row["timestamp"]
        if row["timestamp"] > hit.last:
            hit.last = row["timestamp"]
        if len(hit.samples) < sample_n and row["url"] not in hit.samples:
            hit.samples.append(row["url"])

    hits = list(grouped.values())
    hits.sort(key=lambda h: (-h.score, -h.count, h.name.lower()))
    return hits


def emit_text(hits: list[Hit], domain: str, exclude_domain: bool, field: str) -> None:
    print(f"# {len(hits)} fichiers pour {domain}")
    print("# dork urlscan.io — ouvrir le lien, ou coller la requête dans la recherche")
    print()
    for hit in hits:
        dork = build_dork(hit.name, domain, exclude_domain, field)
        print(dork)
        print(f"  url: {search_url(dork)}")
        print(f"  score: {hit.score}  captures: {hit.count}  {hit.first} -> {hit.last}")
        for sample in hit.samples:
            print(f"  wayback: {sample}")
        print()


def emit_json(hits: list[Hit], domain: str, exclude_domain: bool, field: str) -> None:
    out = []
    for hit in hits:
        dork = build_dork(hit.name, domain, exclude_domain, field)
        out.append(
            {
                "filename": hit.name,
                "dork": dork,
                "urlscan": search_url(dork),
                "score": hit.score,
                "count": hit.count,
                "first": hit.first,
                "last": hit.last,
                "samples": hit.samples,
            }
        )
    json.dump({"domain": domain, "count": len(out), "pivots": out}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def emit_csv(hits: list[Hit], domain: str, exclude_domain: bool, field: str) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["filename", "dork", "urlscan", "score", "count", "first", "last", "sample"])
    for hit in hits:
        dork = build_dork(hit.name, domain, exclude_domain, field)
        writer.writerow(
            [
                hit.name,
                dork,
                search_url(dork),
                hit.score,
                hit.count,
                hit.first,
                hit.last,
                hit.samples[0] if hit.samples else "",
            ]
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Noms de fichiers Wayback -> dorks urlscan.io pour pivoter."
    )
    p.add_argument("domain", nargs="?", help="Domaine, ex. exemple.com")
    p.add_argument(
        "--match",
        choices=("domain", "host"),
        default="domain",
        help="domain = *.exemple.com (défaut), host = cet hôte seulement",
    )
    p.add_argument("--limit", type=int, default=20000, help="Nombre max d'URL CDX (défaut 20000)")
    p.add_argument("--page-size", type=int, default=5000, help="Taille de page CDX")
    p.add_argument("--status", default="200", help="Filtre status HTTP, vide = tous. Défaut 200")
    p.add_argument("--from", dest="date_from", help="Début AAAAMMJJ")
    p.add_argument("--to", dest="date_to", help="Fin AAAAMMJJ")
    p.add_argument("--all", action="store_true", help="Garder tous les noms, pas seulement les extensions utiles")
    p.add_argument("--min-len", type=int, default=6, help="Longueur minimale du nom (défaut 6)")
    p.add_argument("--top", type=int, default=100, help="Nombre de dorks à sortir (défaut 100)")
    p.add_argument("--samples", type=int, default=2, help="URLs Wayback d'exemple par fichier")
    p.add_argument(
        "--field",
        default="filename",
        help="Champ urlscan.io (filename, ou files.filename sur offre payante)",
    )
    p.add_argument(
        "--keep-domain",
        action="store_true",
        help="Ne pas exclure page.domain du domaine source",
    )
    p.add_argument("--format", choices=("text", "json", "csv"), default="text")
    p.add_argument("--delay", type=float, default=1.0, help="Pause entre pages CDX, en secondes")
    p.add_argument("--timeout", type=float, default=60.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    enable_windows_utf8()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.format == "text":
        print_banner()
    raw_domain = args.domain
    if not raw_domain:
        print(f"\n{Colors.CYAN}Domaine à pivoter :{Colors.RESET}")
        raw_domain = input(f"{Colors.YELLOW}>{Colors.RESET} ").strip()
    domain = registrable_hint(raw_domain or "")
    if not domain or "." not in domain:
        eprint("Domaine invalide.")
        return 2
    if args.limit < 1 or args.top < 1:
        eprint("--limit et --top doivent être >= 1")
        return 2

    status = args.status.strip() or None
    eprint(f"[*] Wayback CDX {args.match}:{domain}")
    rows = fetch_cdx(
        domain=domain,
        match=args.match,
        limit=args.limit,
        page_size=max(1, args.page_size),
        status=status,
        date_from=args.date_from,
        date_to=args.date_to,
        delay=max(0.0, args.delay),
        timeout=args.timeout,
    )
    hits = collect(rows, include_all=args.all, min_len=args.min_len, sample_n=max(1, args.samples))
    hits = hits[: args.top]
    eprint(f"[*] {len(rows)} URL, {len(hits)} dorks")

    exclude = not args.keep_domain
    if args.format == "json":
        emit_json(hits, domain, exclude, args.field)
    elif args.format == "csv":
        emit_csv(hits, domain, exclude, args.field)
    else:
        emit_text(hits, domain, exclude, args.field)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
