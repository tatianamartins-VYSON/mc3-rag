#!/usr/bin/env python3
"""Score a run against sample-questions.json using the harness' normalisation.

    python3 eval.py --corpus sample-corpus --questions sample-questions.json [--backend stub]

Runs --index once, then one app.py process per question, exactly like the harness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE / "app" / "app.py"


def norm(s: str) -> str:
    return re.sub(r"[\s\-\._·]", "", (s or "").upper())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=HERE / "sample-corpus")
    ap.add_argument("--questions", type=Path, default=HERE / "sample-questions.json")
    ap.add_argument("--backend", default=os.environ.get("MC3_BACKEND", "hf"))
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="mc3-"))
    env = dict(os.environ, MC3_BACKEND=args.backend, MC2_OUTPUT_DIR=str(work / "out"),
               MC3_INDEX_DIR=str(work / "index"), MC3_SOCKET=str(work / "mc3.sock"))

    t0 = time.time()
    r = subprocess.run([sys.executable, str(APP), "--index", str(args.corpus.resolve())], env=env, capture_output=True, text=True)
    print(f"index: {time.time() - t0:.1f}s rc={r.returncode} {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")

    qs = json.loads(args.questions.read_text(encoding="utf-8"))["queries"]
    score = 0
    for q in qs:
        qid = f"query_{q['n']:02d}"
        t0 = time.time()
        subprocess.run([sys.executable, str(APP), "--corpus", str(args.corpus.resolve()), "--query-id", qid, "--query", q["query"]], env=env, capture_output=True, text=True)
        dt = time.time() - t0
        out = json.loads((work / "out" / f"{qid}_output.json").read_text(encoding="utf-8"))
        ok_ans = norm(out["answer"]) == norm(q["expected_answer"]) or norm(out["answer"]) in {norm(a) for a in q.get("answer_aliases", [])}
        ok_cit = sorted(out["citations"]) == sorted(q["expected_citations"])
        pts = 20 if (ok_ans and ok_cit) else 0
        score += pts
        flag = "OK " if pts else ("ans" if not ok_ans else "cit")
        print(f"{flag} {qid} {dt:4.1f}s  got={out['answer']!r} {out['citations']}  want={q['expected_answer']!r} {q['expected_citations']}")
    print(f"\nSCORE {score}/200   (backend={args.backend}, work={work})")
    # stop the server we spawned
    try:
        pid = int(subprocess.check_output(["pgrep", "-f", f"server.py"]).split()[0])
        os.kill(pid, 15)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
