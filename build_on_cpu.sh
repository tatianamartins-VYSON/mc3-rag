#!/usr/bin/env bash
# Constrói e publica a imagem do Match 3 numa máquina SEM GPU (ex.: Droplet de CPU
# Ubuntu no devcloud). O build não precisa de GPU: só copia código, instala pacotes e
# grava os modelos. Requisitos: Ubuntu 22.04/24.04, ~120 GB de disco livre, root.
#
# Uso (como root):
#   export GHCR_USER=tatianamartins-vyson
#   export GHCR_TOKEN=...        # token com write:packages, cole só no terminal
#   curl -fsSL https://raw.githubusercontent.com/tatianamartins-VYSON/mc3-rag/main/build_on_cpu.sh | bash
set -euo pipefail

BRANCH="${BRANCH:-main}"
REPO="${REPO:-https://github.com/tatianamartins-VYSON/mc3-rag.git}"
DIR="$(basename "$REPO" .git)"
LLM_ID="${LLM_ID:-Qwen/Qwen2.5-7B-Instruct}"
VLM_ID="${VLM_ID:-Qwen/Qwen2-VL-2B-Instruct}"
: "${GHCR_USER:?defina GHCR_USER}"
: "${GHCR_TOKEN:?defina GHCR_TOKEN}"

echo "== disco disponível"; df -h / | tail -1

# disco extra (volume montado em /mnt/*): docker e modelos vão para lá
BIG="$(df -x tmpfs -x overlay --output=target,avail 2>/dev/null | awk 'NR>1 && $1 ~ /^\/mnt\// {print $2, $1}' | sort -rn | head -1 | awk '{print $2}')"
if [ -n "$BIG" ]; then
  echo "== usando volume extra em $BIG"
  mkdir -p "$BIG/docker" "$BIG/work" /etc/docker
  [ -f /etc/docker/daemon.json ] || echo "{\"data-root\": \"$BIG/docker\"}" > /etc/docker/daemon.json
  WORK="$BIG/work"
else
  WORK=/root
fi

echo "== 1/6 docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq ca-certificates curl git python3 python3-pip >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl restart docker 2>/dev/null || true
docker --version
docker info 2>/dev/null | grep -i "docker root dir" || true

echo "== 2/6 código"
cd "$WORK"
if [ -d "$DIR" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" pull -q
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

echo "== 3/6 modelos (~20 GB)"
python3 -m pip install -q --break-system-packages "huggingface_hub[cli]==0.35.3" 2>/dev/null || python3 -m pip install -q "huggingface_hub[cli]==0.35.3"
export PATH="$HOME/.local/bin:$PATH"
[ -f models/llm/config.json ] || hf download "$LLM_ID" --local-dir models/llm
[ -f models/vlm/config.json ] || hf download "$VLM_ID" --local-dir models/vlm
du -sh models/*

echo "== 4/6 build + 5/6 push (self-check pulado: sem GPU aqui)"
SKIP_SELFCHECK=1 bash build_and_push.sh

echo "== 6/6 pronto"
echo "Imagem: ghcr.io/${GHCR_USER}/mc3-rag:${TAG:-v1}"
echo "Agora torne o pacote público em https://github.com/${GHCR_USER}?tab=packages"
