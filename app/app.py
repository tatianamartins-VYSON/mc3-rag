#!/usr/bin/env python3
"""Thin client for Mini-Challenge 3. The harness runs this script twice:

    python3 /app/app.py --index /app/corpus
    python3 /app/app.py --corpus /app/corpus --query-id query_01 --query "..."

All heavy work lives in server.py (resident on a unix socket). If the server is
not up, this script starts it and waits, so a cold container still answers.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get("MC2_OUTPUT_DIR", "/app/output"))
SOCK = os.environ.get("MC3_SOCKET", "/tmp/mc3.sock")
HERE = Path(__file__).resolve().parent


def _call(req: dict, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
    buf = b""
    while not buf.endswith(b"\n"):
        part = s.recv(65536)
        if not part:
            break
        buf += part
    s.close()
    return json.loads(buf.decode("utf-8") or "{}")


def _ensure_server(wait_s: float = 540) -> None:
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            if _call({"op": "ping"}, 5).get("ok"):
                return
        except (OSError, json.JSONDecodeError):
            pass
        if time.time() - t0 < 1:
            # first failure: start the server ourselves (detached)
            subprocess.Popen(
                [sys.executable, str(HERE / "server.py")],
                stdout=open("/tmp/mc3-server.out", "ab"),
                stderr=open("/tmp/mc3-server.err", "ab"),
                start_new_session=True,
            )
        time.sleep(1.0)
    raise RuntimeError("model server did not come up")


def _write(query_id: str, payload: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{query_id}_output.json"
    body = {
        "answer": str(payload.get("answer", "") or ""),
        "citations": [str(c) for c in (payload.get("citations") or [])],
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
    }
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)  # atomic: the harness never reads a half-written file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path)
    ap.add_argument("--corpus", type=Path)
    ap.add_argument("--query-id")
    ap.add_argument("--query")
    args = ap.parse_args()

    if args.index is not None:
        _ensure_server()
        stats = _call({"op": "index", "corpus": str(args.index)}, timeout=580)
        print(json.dumps(stats, ensure_ascii=False), file=sys.stderr)
        return 0

    if args.corpus is None or args.query is None or not args.query_id:
        ap.error("a query needs --corpus, --query-id and --query")

    # Always leave a valid refusal on disk first; overwrite with the real answer.
    _write(args.query_id, {"answer": "", "citations": [], "confidence": 0.0})
    try:
        _ensure_server(wait_s=20)
        resp = _call({"op": "answer", "corpus": str(args.corpus), "query": args.query}, timeout=26)
    except Exception as e:  # noqa: BLE001
        print(f"[app] falling back to refusal: {e}", file=sys.stderr)
        return 0
    _write(args.query_id, resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
