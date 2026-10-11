#!/usr/bin/env bash
# Roda tudo de uma vez no notebook da AMD (GPU ROCm):
#   clona/atualiza o projeto, instala parsers, baixa os modelos e avalia as 10
#   perguntas de amostra com o backend real (hf).
#
# Uso, dentro do Terminal do notebook:
#   curl -fsSL https://raw.githubusercontent.com/tatianamartins-VYSON/mc3-rag/main/run_on_amd.sh | bash
#
# Para pular o download dos modelos numa segunda execução: SKIP_MODELS=1 bash run_on_amd.sh
set -euo pipefail

BRANCH="${BRANCH:-main}"
REPO="${REPO:-https://github.com/tatianamartins-VYSON/mc3-rag.git}"
DIR="$(basename "$REPO" .git)"
LLM_ID="${LLM_ID:-Qwen/Qwen2.5-7B-Instruct}"
VLM_ID="${VLM_ID:-Qwen/Qwen2-VL-2B-Instruct}"

# 1. onde persistir
if [ -d /persistent ]; then BASE=/persistent; elif [ -d /workspace ]; then BASE=/workspace; else BASE="$HOME"; fi
echo "== base dos modelos: $BASE"
# volumes de rede (RunPod /workspace) não aceitam chmod: o código fica no disco local
CODE="$HOME"; mkdir -p "$BASE/mc3-models" && cd "$CODE"

# 2. GPU e torch
python3 - <<'EOF'
import torch
print("torch", torch.__version__, "| gpu visível:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0), "| VRAM GiB:", round(torch.cuda.get_device_properties(0).total_memory/2**30, 1))
import os
if os.environ.get("ALLOW_CUDA") != "1":
    assert "+rocm" in torch.__version__, "torch não é a versão ROCm (para testar em NVIDIA, rode com ALLOW_CUDA=1)"
EOF
df -h "$BASE" | tail -1

# 3. código
if [ -d "$DIR" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" pull -q
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
[ -e models ] || ln -s "$BASE/mc3-models" models
echo "== projeto em $PWD (modelos em $BASE/mc3-models)"

# 5. modelos (antes das dependências: a CLI do hub não pode trocar a versão fixada)
if [ "${SKIP_MODELS:-0}" != "1" ]; then
  pip install -q "huggingface_hub[cli]==0.35.3"
  [ -f models/llm/config.json ] || hf download "$LLM_ID" --local-dir models/llm
  [ -f models/vlm/config.json ] || hf download "$VLM_ID" --local-dir models/vlm
fi
du -sh models/* 2>/dev/null || true

# 6a. dependências (sem tocar no torch)
# cópias antigas do hub (ex.: 2.x em outra pasta) têm prioridade sobre a versão fixada
for _ in 1 2 3; do pip uninstall -y -q huggingface_hub 2>/dev/null || true; done
pip install -q --no-deps --ignore-installed -r app/requirements.txt
python3 -c "import huggingface_hub as h; assert h.__version__=='0.35.3', h.__version__"
python3 -c "import os,torch; assert os.environ.get('ALLOW_CUDA')=='1' or '+rocm' in torch.__version__, 'pip trocou o torch'; print('torch ok:', torch.__version__)"

# 6. corpus de amostra: casos que o zip não carrega
sh setup.sh >/dev/null || true

# 7. avaliação real
export MC3_LLM="$PWD/models/llm" MC3_VLM="$PWD/models/vlm" MC3_BACKEND=hf
python3 eval.py --backend hf 2>&1 | tee eval_hf.log
echo
echo "== log salvo em $PWD/eval_hf.log  (mande este arquivo)"
