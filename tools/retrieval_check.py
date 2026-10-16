"""Testa SEM GPU a parte que mais quebra num corpus maior: indexação e busca.

Para cada pergunta, confere se TODOS os arquivos que a resposta exige chegam aos trechos
que vão para o modelo (1º salto + 2º salto, como o servidor faz). Se um arquivo necessário
não chega, o modelo não tem como acertar — esse é o erro que a nota oficial não explica.

Uso:
    python3 tools/retrieval_check.py /tmp/hard/corpus /tmp/hard/questions.json
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from parsers import walk  # noqa: E402
from retrieval import BM25Index  # noqa: E402

TOP_K = int(os.environ.get("MC3_TOP_K", "8"))
corpus, qfile = Path(sys.argv[1]), Path(sys.argv[2])
t0 = time.time()
chunks, images, manifest = walk(corpus)
t_idx = time.time() - t0
idx = BM25Index([c.to_dict() for c in chunks])
bad = [m for m in manifest if m["status"] != "parsed"]
print(f"indexação: {len(manifest)} arquivos, {len(chunks)} trechos, {t_idx:.1f} s (sem as imagens, que dependem do VLM)")
for m in bad:
    print(f"   não lido (esperado nas armadilhas): {m['file']} -> {m['status']}")

ok = 0
qs = json.loads(qfile.read_text(encoding="utf-8"))["queries"]
for x in qs:
    t = time.time()
    first = idx.search(x["query"], k=TOP_K)
    second = idx.second_hop(x["query"], first, k=3)
    files = [p["file"] for p in first + second]
    dt = (time.time() - t) * 1000
    need = set(x["expected_citations"])
    falta = need - set(files)
    import re
    norm = lambda v: re.sub(r"[\s\-.·_°]", "", str(v)).upper()
    ans = x.get("expected_answer", "")
    tem_valor = (not ans) or any(norm(ans) in norm(p["text"]) for p in first + second)
    if ans and not tem_valor and any(f.endswith((".png", ".jpg")) for f in need):
        tem_valor = None  # depende da transcrição da imagem (VLM)
    passou = not falta and tem_valor is not False
    ok += passou
    pos = {f: (files.index(f) + 1 if f in files else None) for f in need}
    print(f"{'OK ' if passou else 'FALHA'} q{x['n']:02d} [{x['category']}] {dt:.0f} ms" + (f" posições={pos}" if need else " (sem resposta: o modelo deve recusar)") + (f" FALTOU: {sorted(falta)}" if falta else "") + ("" if tem_valor else " | valor não está nos trechos" if tem_valor is False else " | imagem: depende do VLM"))
print(f"\nbusca com os arquivos necessários: {ok}/{len(qs)} (com TOP_K={TOP_K})")
