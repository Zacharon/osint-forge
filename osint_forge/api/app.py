"""Read-only HTTP API over the ontology store.

A plain WSGI application, so it needs no extra packages and runs under any WSGI
server (``wsgiref`` for local use, gunicorn or uWSGI in production). Writes go
through the Python store, not HTTP, until authentication exists.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote

from osint_forge.api.projection import events_feed
from osint_forge.ontology import OBJECT_TYPES, OntologyError, OntologyObject, OntologyStore

JSON_HEADERS = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Access-Control-Allow-Origin", "*"),
    ("X-Content-Type-Options", "nosniff"),
]
MAX_LIMIT = 500
TYPE_ROUTES = {"/events": "event", "/claims": "claim", "/assessments": "assessment"}
_STATUS_TEXT = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


def public_object(obj: OntologyObject) -> dict[str, Any]:
    """Object as served over HTTP. Provenance metadata can hold extractor detail, so it is dropped."""
    body = obj.to_dict()
    body["provenance"] = [{k: v for k, v in ref.items() if k != "metadata"} for ref in body["provenance"]]
    return body


def _int(query: dict[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = query.get(key)
    if raw in (None, ""):
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        raise ApiError(400, "invalid-parameter", f"{key} must be an integer") from None


def _bbox(query: dict[str, str]) -> tuple[float, float, float, float] | None:
    raw = query.get("bbox")
    if not raw:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in raw.split(","))
    except ValueError:
        raise ApiError(400, "invalid-parameter", "bbox must be minLon,minLat,maxLon,maxLat") from None
    if not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise ApiError(400, "invalid-parameter", "bbox is out of range or inverted")
    return min_lon, min_lat, max_lon, max_lat


def _object_type(query: dict[str, str]) -> str | None:
    value = query.get("type") or None
    if value and value not in OBJECT_TYPES:
        raise ApiError(400, "invalid-parameter", f"unknown object type: {value}")
    return value


class ForgeApi:
    """WSGI callable: ``ForgeApi(store)``."""

    def __init__(self, store: OntologyStore) -> None:
        self.store = store

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO") or "/"
        query = {k: v[-1] for k, v in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()}
        if method == "OPTIONS":
            return self._respond(start_response, 204, None, [("Access-Control-Allow-Methods", "GET, OPTIONS")])
        try:
            if method != "GET":
                raise ApiError(405, "method-not-allowed", "This API is read-only; use GET.")
            status, body = 200, self.route(path, query)
        except ApiError as exc:
            status, body = exc.status, {"error": exc.error, "message": exc.message}
        except OntologyError as exc:
            status, body = 400, {"error": "invalid-request", "message": str(exc)}
        except Exception:  # noqa: BLE001 - never leak stack traces
            status, body = 500, {"error": "internal-error", "message": "The request could not be completed."}
        extra = [("Cache-Control", "public, max-age=30")] if path == "/api/events" and status == 200 else [("Cache-Control", "no-store")]
        return self._respond(start_response, status, body, extra)

    def route(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/events":
            return events_feed(self.store, query)
        if path == "/objects":
            return self._list(query, _object_type(query))
        if path in TYPE_ROUTES:
            return self._list(query, TYPE_ROUTES[path])
        if path == "/search":
            if not (query.get("q") or "").strip():
                raise ApiError(400, "invalid-parameter", "q is required")
            return self._list(query, _object_type(query))
        if path.startswith("/objects/"):
            return self._object_route(path[len("/objects/"):], query)
        raise ApiError(404, "not-found", "No such route.")

    def _list(self, query: dict[str, str], object_type: str | None) -> dict[str, Any]:
        limit = _int(query, "limit", 100, 1, MAX_LIMIT)
        objects = self.store.list_objects(
            object_type=object_type,
            text=(query.get("q") or "").strip() or None,
            bbox=_bbox(query),
            limit=limit,
        )
        return {"objects": [public_object(o) for o in objects], "count": len(objects), "limit": limit}

    def _object_route(self, rest: str, query: dict[str, str]) -> dict[str, Any]:
        object_id, _, view = unquote(rest).partition("/")
        obj = self.store.get_object(object_id)
        if obj is None:
            raise ApiError(404, "not-found", f"unknown object: {object_id}")
        if view == "":
            return {"object": public_object(obj)}
        if view == "history":
            return {"id": object_id, "revisions": self.store.object_history(object_id)}
        if view == "links":
            types = [t for t in (query.get("type") or "").split(",") if t]
            links = self.store.links_for(object_id, direction=query.get("direction") or "both", link_types=types)
            return {"id": object_id, "links": links, "count": len(links)}
        if view == "neighborhood":
            return self.store.neighborhood(
                object_id,
                depth=_int(query, "depth", 1, 0, 4),
                max_objects=_int(query, "max", 200, 1, MAX_LIMIT),
            )
        raise ApiError(404, "not-found", "No such route.")

    @staticmethod
    def _respond(start_response: Callable[..., Any], status: int, body: Any, extra: list[tuple[str, str]]) -> list[bytes]:
        start_response(f"{status} {_STATUS_TEXT[status]}", JSON_HEADERS + extra)
        if body is None:
            return [b""]
        return [json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")]


def main(argv: list[str] | None = None) -> None:
    from wsgiref.simple_server import make_server

    from osint_forge.ontology import PostgresOntologyStore, SQLiteOntologyStore

    parser = argparse.ArgumentParser(description="Serve the read-only Forge API.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--db", help="SQLite ontology database path")
    target.add_argument("--database-url", help="PostgreSQL connection string")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    store = SQLiteOntologyStore(args.db) if args.db else PostgresOntologyStore(args.database_url)
    with make_server(args.host, args.port, ForgeApi(store)) as server:
        print(f"Forge API on http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
