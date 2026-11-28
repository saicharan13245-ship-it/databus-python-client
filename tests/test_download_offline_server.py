import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import requests

import databusclient.api.download as dl
import pytest

from databusclient.api.download import download as api_download

DUMMY_FILE_PATH = "/testuser/testgroup/testartifact/2026-01-01/sample.ttl"
DUMMY_COLLECTION_PATH = "/testuser/collections/sample"
DUMMY_PAYLOAD = b"<http://example.org/s> <http://example.org/p> <http://example.org/o> .\n"
DUMMY_SHA256 = hashlib.sha256(DUMMY_PAYLOAD).hexdigest()


class _DummyDatabusHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy HTTP server logs during pytest runs
        return

    def _base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def do_HEAD(self):
        if self.path == DUMMY_FILE_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "text/turtle")
            self.send_header("Content-Length", str(len(DUMMY_PAYLOAD)))
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == DUMMY_COLLECTION_PATH:
            query = "SELECT ?file WHERE { ?file a <http://dataid.dbpedia.org/ns/core#Part> . }"
            body = query.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/sparql")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == DUMMY_FILE_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "text/turtle")
            self.send_header("Content-Length", str(len(DUMMY_PAYLOAD)))
            self.end_headers()
            self.wfile.write(DUMMY_PAYLOAD)
            return

        # Optional JSON-LD metadata for checksum resolution
        if self.path == "/testuser/testgroup/testartifact/2026-01-01":
            file_url = f"{self._base_url()}{DUMMY_FILE_PATH}"
            doc = {
                "@graph": [
                    {
                        "@id": file_url,
                        "file": file_url,
                        "sha256sum": DUMMY_SHA256,
                    }
                ]
            }
            body = json.dumps(doc).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/ld+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        # Consume request body so the client connection closes cleanly
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            self.rfile.read(content_length)

        if self.path == "/sparql":
            file_url = f"{self._base_url()}{DUMMY_FILE_PATH}"
            payload = {
                "head": {"vars": ["file"]},
                "results": {
                    "bindings": [
                        {"file": {"type": "uri", "value": file_url}}
                    ]
                },
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/sparql-results+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()
class _LocalSPARQLWrapper:
    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url
        self._query = ""
        self._headers: dict[str, str] = {}

    def setMethod(self, method: str) -> None:
        self._method = method

    def setQuery(self, query: str) -> None:
        self._query = query

    def setReturnFormat(self, fmt) -> None:
        self._fmt = fmt

    def setCustomHttpHeaders(self, headers: dict[str, str]) -> None:
        self._headers.update(headers)

    def query(self):
        resp = requests.post(
            self.endpoint_url,
            data={"query": self._query},
            headers=self._headers,
            timeout=10,
        )
        resp.raise_for_status()

        class _Result:
            def convert(self_inner):
                return resp.json()

        return _Result()


@pytest.fixture
def dummy_databus_server(monkeypatch):
    monkeypatch.setattr(dl, "SPARQLWrapper", _LocalSPARQLWrapper)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DummyDatabusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)




def test_download_collection_offline(dummy_databus_server, tmp_path: Path):
    base_url = dummy_databus_server
    collection_url = f"{base_url}{DUMMY_COLLECTION_PATH}"
    sparql_endpoint = f"{base_url}/sparql"

    api_download(str(tmp_path), sparql_endpoint, [collection_url])

    downloaded_files = list(tmp_path.rglob("sample.ttl"))
    assert len(downloaded_files) == 1
    assert downloaded_files[0].read_bytes() == DUMMY_PAYLOAD


def test_download_sparql_query_offline(dummy_databus_server, tmp_path: Path):
    base_url = dummy_databus_server
    sparql_endpoint = f"{base_url}/sparql"
    query = "SELECT ?file WHERE { ?file a <http://dataid.dbpedia.org/ns/core#Part> . }"

    api_download(str(tmp_path), sparql_endpoint, [query])

    downloaded_files = list(tmp_path.rglob("sample.ttl"))
    assert len(downloaded_files) == 1
    assert downloaded_files[0].read_bytes() == DUMMY_PAYLOAD