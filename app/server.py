"""Resident model server for Mini-Challenge 3.

Started by the container CMD. Loads the answer LLM and the vision model ONCE on
the GPU, then serves requests over a unix socket so each per-question process
(app.py) stays a thin client well inside the 30 s budget.

Protocol: one JSON object per connection, newline-terminated.
    {"op": "ping"}
    {"op": "index", "corpus": "/app/corpus"}
    {"op": "answer", "corpus": "/app/corpus", "query": "..."}

Backends (env MC3_BACKEND):
    hf        transformers on ROCm (default inside the container)
    stub      no model; retrieval + regex heuristics. For local plumbing tests only.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parsers import walk  # noqa: E402
from prompts import SYSTEM, VISION, build_user  # noqa: E402
from retrieval import BM25Index, identifiers  # noqa: E402

SOCK = os.environ.get("MC3_SOCKET", "/tmp/mc3.sock")
INDEX_DIR = Path(os.environ.get("MC3_INDEX_DIR", "/app/index"))
BACKEND = os.environ.get("MC3_BACKEND", "hf")
LLM_PATH = os.environ.get("MC3_LLM", "/models/llm")
VLM_PATH = os.environ.get("MC3_VLM", "/models/vlm")
ABLATION_BUDGET_S = float(os.environ.get("MC3_ABLATION_BUDGET", "18"))
TOP_K = int(os.environ.get("MC3_TOP_K", "8"))


def log(msg: str) -> None:
    print(f"[server] {msg}", file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------
# model backends
# ----------------------------------------------------------------------------

class HFBackend:
    def __init__(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        assert torch.cuda.is_available(), "GPU not visible; refusing to run on CPU"
        self.device = "cuda"
        t0 = time.time()
        self.tok = AutoTokenizer.from_pretrained(LLM_PATH)
        self.llm = AutoModelForCausalLM.from_pretrained(LLM_PATH, torch_dtype=torch.bfloat16).to(self.device).eval()
        log(f"LLM loaded from {LLM_PATH} in {time.time() - t0:.1f}s")
        self.vlm = None
        self.vproc = None
        if Path(VLM_PATH).exists():
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

            t0 = time.time()
            self.vproc = AutoProcessor.from_pretrained(VLM_PATH)
            self.vlm = Qwen2VLForConditionalGeneration.from_pretrained(VLM_PATH, torch_dtype=torch.bfloat16).to(self.device).eval()
            log(f"VLM loaded from {VLM_PATH} in {time.time() - t0:.1f}s")

    def chat(self, system: str, user: str, max_new_tokens: int = 160) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        ids = self.tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.llm.generate(ids, max_new_tokens=max_new_tokens, do_sample=False, temperature=None, top_p=None)
        return self.tok.decode(out[0][ids.shape[-1]:], skip_special_tokens=True)

    def transcribe(self, image_path: Path) -> str:
        if self.vlm is None:
            return ""
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": VISION}]}]
        prompt = self.vproc.apply_chat_template(msgs, add_generation_prompt=True)
        inputs = self.vproc(text=[prompt], images=[img], return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.vlm.generate(**inputs, max_new_tokens=400, do_sample=False)
        return self.vproc.batch_decode(out[:, inputs["input_ids"].shape[-1]:], skip_special_tokens=True)[0]


class StubBackend:
    """No model. Picks the passage with the strongest identifier overlap and
    extracts a value with regexes. Exists so the plumbing can be tested on a
    machine without a GPU; it is NOT the submission backend."""

    def chat(self, system: str, user: str, max_new_tokens: int = 160) -> str:
        q = user.rsplit("QUESTION:", 1)[-1].strip()
        passages = re.findall(r"\[(\d+)\] file: (\S+).*?\n(.*?)(?=\n\[\d+\] file:|\nQUESTION:)", user, flags=re.S)
        ql = q.lower()
        best = None
        for n, _f, text in passages:
            s = 0
            for w in re.findall(r"[a-z0-9]+", ql):
                if len(w) > 2 and w in text.lower():
                    s += 1
            s += 5 * len(identifiers(q) & identifiers(text))
            if best is None or s > best[0]:
                best = (s, n, text)
        if best is None or best[0] == 0:
            return '{"answer": "", "used": [], "confidence": 0}'
        _, n, text = best
        val = ""
        m = re.search(r"\.{3,}\s*([0-9][0-9.]*)", text)
        if m:
            val = m.group(1)
        if not val:
            m = re.search(r"(Q[1-4]\s?FY\d{2,4}|REV-[A-Z0-9]+|[A-Z]{2,}-[A-Z]+-\d+-[A-Z]|E\d{4}|\d+\.\d+\.\d+|= ?(\d+))", text)
            val = (m.group(1) if m else "")
        return json.dumps({"answer": val, "used": [int(n)], "confidence": 0.3})

    def transcribe(self, image_path: Path) -> str:
        return ""


# ----------------------------------------------------------------------------
# engine
# ----------------------------------------------------------------------------

class Engine:
    def __init__(self) -> None:
        self.backend = StubBackend() if BACKEND == "stub" else HFBackend()
        self.index: BM25Index | None = None
        self.lock = threading.Lock()
        self._load_index()

    # ---- index -------------------------------------------------------------
    def build_index(self, corpus: Path) -> dict:
        t0 = time.time()
        chunks, images, manifest = walk(corpus)
        for rel in images:
            try:
                text = self.backend.transcribe(corpus / rel)
            except Exception as e:  # noqa: BLE001
                log(f"vision failed on {rel}: {e}")
                text = ""
            if text.strip():
                chunks.append(type(chunks[0])(f"{rel}#img", rel, "image", text.strip(), "image transcription")) if chunks else None
                if not chunks:
                    from parsers import Chunk

                    chunks.append(Chunk(f"{rel}#img", rel, "image", text.strip(), "image transcription"))
            for m in manifest:
                if m["file"] == rel:
                    m["status"] = "parsed" if text.strip() else "image-empty"
                    m["chunks"] = 1 if text.strip() else 0
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        (INDEX_DIR / "chunks.json").write_text(json.dumps([c.to_dict() for c in chunks], ensure_ascii=False), encoding="utf-8")
        (INDEX_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        self.index = BM25Index([c.to_dict() for c in chunks])
        stats = {"chunks": len(chunks), "files": len(manifest), "seconds": round(time.time() - t0, 1),
                 "unparsed": [m["file"] for m in manifest if m["status"] not in {"parsed"}]}
        log(f"index built: {stats}")
        return stats

    def _load_index(self) -> None:
        p = INDEX_DIR / "chunks.json"
        if p.exists():
            self.index = BM25Index(json.loads(p.read_text(encoding="utf-8")))
            log(f"index loaded: {len(self.index.chunks)} chunks")

    # ---- answer ------------------------------------------------------------
    def answer(self, corpus: Path, query: str) -> dict:
        if self.index is None:
            self.build_index(corpus)
        first = self.index.search(query, k=TOP_K)
        second = self.index.second_hop(query, first, k=3)
        passages = first + second
        if not passages:
            return {"answer": "", "citations": [], "confidence": 0.0}
        raw = self.backend.chat(SYSTEM, build_user(query, passages))
        parsed = self._parse_json(raw)
        ans = str(parsed.get("answer", "")).strip()
        used = parsed.get("used", []) or []
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        if not ans:
            return {"answer": "", "citations": [], "confidence": 0.0}
        files: list[str] = []
        for n in used:
            try:
                f = passages[int(n) - 1]["file"]
            except (ValueError, IndexError, TypeError):
                continue
            if f not in files:
                files.append(f)
        if not files:
            # model answered but cited nothing usable: fall back to the passage holding the value
            for p in passages:
                if _norm(ans) and _norm(ans) in _norm(p["text"]):
                    files.append(p["file"])
                    break
        if not files:
            return {"answer": "", "citations": [], "confidence": 0.0}
        files = self._necessary(query, passages, ans, files)
        return {"answer": ans, "citations": files, "confidence": round(conf, 2), "_passages": [p["cid"] for p in passages]}

    def _necessary(self, query: str, passages: list[dict], ans: str, files: list[str]) -> list[str]:
        """Citation by necessity, tested by ablation.

        For each cited file, ask again WITHOUT that file's passages. If the model still
        reaches the same value, the file was not necessary and is dropped. The harness
        scores the exact set, so one extra file costs the whole question. Each probe is one
        short generation (~1 s); the per-question budget is 30 s.
        """
        if len(files) < 2 or os.environ.get("MC3_NO_ABLATION") == "1":
            return files
        t0 = time.time()
        keep: list[str] = []
        for f in files:
            # a file the question itself names ("the production log shows...", "the
            # datasheet", "the asset label") is a source by definition, even when another
            # file repeats the fact: never probe it
            if _named_by_query(query, f):
                keep.append(f)
                continue
            if time.time() - t0 > ABLATION_BUDGET_S:
                keep.append(f)
                continue
            rest = [p for p in passages if p["file"] != f]
            if not rest:
                keep.append(f)
                continue
            try:
                probe = self._parse_json(self.backend.chat(SYSTEM, build_user(query, rest)))
                same = _norm(str(probe.get("answer", ""))) == _norm(ans)
            except Exception as e:  # noqa: BLE001
                log(f"ablation failed on {f}: {e}")
                same = False
            if not same:
                keep.append(f)
        # every file was individually redundant (two copies of the same fact): keep the one
        # the model listed first rather than citing nothing
        return keep or files[:1]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            fixed = re.sub(r",\s*}", "}", m.group(0))
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                return {}


_DOC_WORDS = {"log", "logs", "datasheet", "spec", "specs", "sheet", "spreadsheet", "csv", "xlsx",
              "roadmap", "plan", "planning", "label", "pinout", "photo", "image", "picture",
              "ticket", "database", "email", "memo", "report", "manual", "readme", "code", "script"}


def _named_by_query(query: str, file: str) -> bool:
    """True when the question refers to this file by name or by document type."""
    q = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3}
    parts = [w for w in re.split(r"[/._\-]+", file.lower()) if len(w) >= 3]
    for w in parts:
        if w in q:
            return True
        # "log" ~ "logged"/"logs", "label" ~ "labels"
        if w in _DOC_WORDS and any(x.startswith(w) or w.startswith(x) for x in q if len(x) >= 3):
            return True
    return False


def _norm(s: str) -> str:
    return re.sub(r"[\s\-\._·]", "", s.upper())


# ----------------------------------------------------------------------------
# socket loop
# ----------------------------------------------------------------------------

def serve(engine: Engine) -> None:
    if os.path.exists(SOCK):
        os.remove(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(8)
    log(f"listening on {SOCK} (backend={BACKEND})")
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle, args=(engine, conn), daemon=True).start()


def _handle(engine: Engine, conn: socket.socket) -> None:
    try:
        buf = b""
        while not buf.endswith(b"\n"):
            part = conn.recv(65536)
            if not part:
                break
            buf += part
        req = json.loads(buf.decode("utf-8") or "{}")
        op = req.get("op")
        if op == "ping":
            resp = {"ok": True, "backend": BACKEND, "indexed": engine.index is not None}
        elif op == "index":
            with engine.lock:
                resp = engine.build_index(Path(req["corpus"]))
        elif op == "answer":
            with engine.lock:
                resp = engine.answer(Path(req["corpus"]), req["query"])
        else:
            resp = {"error": f"unknown op {op}"}
    except Exception as e:  # noqa: BLE001
        log(traceback.format_exc())
        resp = {"error": f"{type(e).__name__}: {e}", "answer": "", "citations": [], "confidence": 0.0}
    try:
        conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        conn.close()


if __name__ == "__main__":
    serve(Engine())
