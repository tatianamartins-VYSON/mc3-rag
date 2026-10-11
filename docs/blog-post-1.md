# From 140 to 200/200: building a citation-exact RAG container on AMD ROCm

*By Tatiana Tenório Martins, accountant and court expert from Maceió, Brazil. Written for the AMD AI League (lablab.ai × AMD), Match 3.*

Accountants and court experts live by one rule: an answer without a source is worthless. When I joined Match 3 of the AMD AI League, the challenge matched that rule exactly: build a Docker container that reads a folder of messy documents (PDF, DOCX, XLSX, CSV, logs, code and images) and answers each question with the **exact value** and the **exact list of source files**. A question only scores when both are right.

Here is what I built, what broke and what got it to 200/200 on the official sample.

## The architecture

```
container start ─► resident server: loads LLM + VLM on the AMD GPU
--index  ─► defensive walk ─► parsers (pdf/docx/xlsx/csv/txt/log/py)
                           ─► Qwen2-VL-2B transcribes png/jpg
--query  ─► BM25 + identifier boost ─► second hop on rare IDs
          ─► Qwen2.5-7B extracts {answer, used chunks} ─► citations
```

- **Models:** `Qwen2.5-7B-Instruct` for text and `Qwen2-VL-2B-Instruct` for images. Both fit within the image-size budget, which is the limit that matters in this challenge (not VRAM).
- **Runtime:** PyTorch on ROCm with the mandated base image. The GPU is mandatory: if the backend finds no GPU, it aborts instead of falling back to CPU.

## What broke, and the fixes

| Round | Score | What was wrong |
|---|---|---|
| Contract-only stub (no model) | 80/200 | Proved index, socket and atomic writes |
| First real model run | 140/200 | Three answers cited one file too many |
| Citation ablation | 180/200 | Removed a log the question itself named |
| Protect files named by the question | **200/200** | Slowest question: 3.0 s |

**1. Cite by necessity, not by retrieval.** Dumping every retrieved file into the citations costs points. The model now returns only the chunk numbers it could not answer without. Then an **ablation test** runs: for each cited file, the question is asked again without that file. If the answer stays the same, the file is dropped.

**2. Never drop what the question names.** Ablation was too aggressive: it removed a production log that the question explicitly mentioned. Files referenced in the question are now protected.

**3. Superseded documents lose weight.** The corpus contains a datasheet marked `WITHDRAWN` next to its newer revision. Retrieval down-weights withdrawn and superseded files, and the prompt tells the model to prefer the latest revision.

**4. Multi-hop on rare identifiers.** Ticket numbers and part codes that appear in only a few files trigger a second retrieval hop. Product names that appear everywhere do not.

**5. Refusal by default.** Before asking the model anything, the app writes `{"answer": "", "citations": []}`. If anything fails, the output is still a valid refusal, never malformed JSON.

## Why this matters beyond the League

I am reusing the same pattern for **Vyson FiscalRAG**, a Portuguese tax assistant for Brazilian accountants. It answers questions such as "What is the ISS rate for software programming in Maceió?" with the exact rate, municipal code and law, each one cited. It runs Qwen2.5-7B with vLLM on an AMD Instinct GPU (ROCm), and the first production answer took 0.77 s.

Lessons for anyone starting on ROCm:

- Start from the official ROCm images (`rocm/pytorch`, `rocm/vllm`). They saved me days.
- Budget for the image size first, then pick the model.
- Test with `--network none`, because the grader has no internet access.
- Treat citations as a scoring problem in their own right, not as a side effect of retrieval.

**Code (open source):** https://github.com/tatianamartins-VYSON/mc3-rag
**Live demo (Vyson FiscalRAG):** https://tatianabot-dashboard.vercel.app/fiscal/perguntar

Thanks to AMD and lablab.ai for the AI League. #AMD #ROCm #lablab #RAG #AMDAILeague
