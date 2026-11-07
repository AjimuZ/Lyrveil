"""HTTPS test server for tests/docker/Models.Tests.ps1 (T-DCK-11): serves generated model files on the download network.

At start it creates a test CA and a certificate for the host name "models-server" with openssl, writes the CA to
/shared/ca.pem and then a test catalog to /shared/catalog.json (last, so the health check can wait for it), and
serves on port 8443:

    /files/<name>     a generated file, with "Range: bytes=N-" support; resume.gguf closes the connection halfway
                      through its first request without a range, so the client has to resume
    /redirect/https   302 to /files/redirected.gguf on this server
    /redirect/http    302 to an http:// URL, which the client must refuse; nothing listens there

Every request is printed as one line, "REQUEST <path> range=<Range header or ->", so the test can read them with
"docker compose logs". The files are generated, never weights, and the catalog's URLs all point at this server.
"""

import hashlib
import json
import os
import re
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "models-server"
PORT = 8443
SHARED = Path("/shared")
WORK = Path("/tmp/models-server")
MIB = 1 << 20

OPENSSL_CONFIG = f"""
[req]
distinguished_name = dn
prompt = no
[dn]
CN = lyrveil-models-test
[ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[leaf]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:{HOST}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


def generated(seed: str, size: int) -> bytes:
    """Returns size bytes derived from seed, the same on every run."""
    blocks = []
    total = 0
    counter = 0
    while total < size:
        block = hashlib.sha256(f"{seed}:{counter}".encode()).digest() * 64
        blocks.append(block)
        total += len(block)
        counter += 1
    return b"".join(blocks)[:size]


FILES = {
    "resume.gguf": generated("resume", 3 * MIB + 123),
    "image.safetensors": generated("image", 2 * MIB + 45),
    "redirected.gguf": generated("redirected", MIB + 6),
    "bad-hash.gguf": generated("bad-hash", MIB),
    "bad-size.gguf": generated("bad-size", MIB),
}
# Files whose first request without a range is cut halfway; the set records which ones were cut already.
CUT_ONCE = {"resume.gguf"}
_cut: set[str] = set()
_lock = threading.Lock()


def entry(profile_id: str, file: str, path: str, content: bytes, **fields: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": profile_id,
        "display_name": f"Test {profile_id}",
        "file": file,
        "url": f"https://{HOST}:{PORT}{path}",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "license": "Test-License-1.0",
        "license_url": f"https://{HOST}:{PORT}/license",
        "min_tier": "reduced",
        "default_tiers": [],
    }
    item.update(fields)
    return item


def catalog() -> dict[str, object]:
    both = ["reduced", "target"]
    return {
        "schema_version": 1,
        "text": [
            entry("resume-model", "test-resume.gguf", "/files/resume.gguf", FILES["resume.gguf"], default_tiers=both),
            entry("redirect-model", "test-redirect.gguf", "/redirect/https", FILES["redirected.gguf"]),
            entry("http-redirect-model", "test-http-redirect.gguf", "/redirect/http", FILES["redirected.gguf"]),
            entry(
                "bad-hash-model",
                "test-bad-hash.gguf",
                "/files/bad-hash.gguf",
                FILES["bad-hash.gguf"],
                sha256=hashlib.sha256(b"other content").hexdigest(),
            ),
            entry(
                "bad-size-model",
                "test-bad-size.gguf",
                "/files/bad-size.gguf",
                FILES["bad-size.gguf"],
                bytes=len(FILES["bad-size.gguf"]) + 1000,
            ),
        ],
        "image": [
            entry(
                "image-model",
                "test-image.safetensors",
                "/files/image.safetensors",
                FILES["image.safetensors"],
                default_tiers=both,
            )
        ],
    }


def make_certificates() -> tuple[Path, Path]:
    """Creates the test CA and the server certificate in WORK, copies the CA to /shared, and returns (cert, key)."""
    WORK.mkdir(parents=True, exist_ok=True)
    config = WORK / "openssl.cnf"
    config.write_text(OPENSSL_CONFIG, encoding="ascii")

    def openssl(*arguments: str) -> None:
        subprocess.run(["openssl", *arguments], check=True, cwd=WORK, capture_output=True)

    openssl("req", "-x509", "-config", str(config), "-extensions", "ca", "-newkey", "rsa:2048", "-nodes",
            "-keyout", "ca.key", "-out", "ca.pem", "-days", "2")
    openssl("req", "-new", "-config", str(config), "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key",
            "-out", "server.csr", "-subj", f"/CN={HOST}")
    openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-set_serial", "2",
            "-days", "2", "-extfile", str(config), "-extensions", "leaf", "-out", "server.pem")
    (SHARED / "ca.pem").write_bytes((WORK / "ca.pem").read_bytes())
    return WORK / "server.pem", WORK / "server.key"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # The REQUEST lines replace the default access log.
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        print(f"REQUEST {self.path} range={range_header or '-'}", flush=True)
        if self.path == "/redirect/https":
            self._redirect(f"https://{HOST}:{PORT}/files/redirected.gguf")
        elif self.path == "/redirect/http":
            self._redirect(f"http://{HOST}:8080/files/redirected.gguf")
        elif self.path.startswith("/files/") and self.path[len("/files/"):] in FILES:
            self._file(self.path[len("/files/"):], range_header)
        else:
            self.send_error(404)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, name: str, range_header: str | None) -> None:
        body = FILES[name]
        start = 0
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-", range_header)
            if not match:
                self.send_error(400)
                return
            start = int(match.group(1))
            if start >= len(body):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(body)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body) - start))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with _lock:
            cut = name in CUT_ONCE and not range_header and name not in _cut
            if cut:
                _cut.add(name)
        if cut:
            # Half of the announced body, then the connection closes: the client sees an early end of stream.
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body[start:])


def main() -> None:
    (SHARED / "catalog.json").unlink(missing_ok=True)
    cert, key = make_certificates()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    temporary = SHARED / "catalog.json.tmp"
    temporary.write_text(json.dumps(catalog(), indent=2), encoding="utf-8")
    os.replace(temporary, SHARED / "catalog.json")
    print(f"models-server listening on {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
