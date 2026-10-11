#!/usr/bin/env bash
# Constrói a imagem do Match 3 na base ROCm, roda o self-check oficial e publica
# no GitHub Container Registry. Rodar na máquina com GPU AMD e Docker.
#
# Pré-requisitos (feitos por run_on_amd.sh): models/llm e models/vlm baixados.
#
# Uso:
#   export GHCR_USER=tatianamartins-vyson          # minúsculas
#   export GHCR_TOKEN=...                           # token com write:packages (não comitar!)
#   bash build_and_push.sh                          # tag padrão v1
#   TAG=v2 bash build_and_push.sh
set -euo pipefail

cd "$(dirname "$0")"
TAG="${TAG:-v1}"
GHCR_USER="${GHCR_USER:-tatianamartins-vyson}"
IMAGE="ghcr.io/${GHCR_USER}/mc3-rag:${TAG}"

[ -f models/llm/config.json ] || { echo "models/llm ausente. Rode run_on_amd.sh antes."; exit 1; }
[ -f models/vlm/config.json ] || { echo "models/vlm ausente. Rode run_on_amd.sh antes."; exit 1; }

echo "== 1/5 build $IMAGE (puxa a base ROCm de ~29 GiB na primeira vez)"
docker build -t "$IMAGE" .

echo "== 2/5 torch dentro da imagem precisa ser ROCm"
docker run --rm "$IMAGE" python3 -c "import torch; v=torch.__version__; print(v); assert '+rocm' in v"

echo "== 3/5 tamanho descompactado (limite 60 GiB)"
docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "%.1f GiB\n", $1/1024/1024/1024}'

if [ "${SKIP_SELFCHECK:-0}" = "1" ]; then
  echo "== 4/5 self-check PULADO (máquina sem GPU). Rode-o numa MI300X antes do prazo se possível."
else
  echo "== 4/5 self-check oficial (sem rede, sem DAC_OVERRIDE)"
  sh setup.sh >/dev/null || true
  python3 selfcheck.py "$IMAGE" sample-corpus
fi

echo "== 5/5 publicar no GHCR"
if [ -z "${GHCR_TOKEN:-}" ]; then
  echo "GHCR_TOKEN não definido. Exporte e rode de novo, ou faça o push manualmente:"
  echo "  echo \$GHCR_TOKEN | docker login ghcr.io -u $GHCR_USER --password-stdin && docker push $IMAGE"
  exit 0
fi
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker push "$IMAGE"
echo
echo "Publicado: $IMAGE"
echo "Cole este endereço no campo 'Mini Desafio 3 Imagem' do formulário do lablab."
echo "Depois torne o pacote público em: https://github.com/users/${GHCR_USER}/packages/container/mc3-rag/settings"
