# Mandated base image. Checked by layer identity at evaluation.
FROM rocm/pytorch:rocm10.0_ubuntu26.04_py3.14_pytorch_release_2.13.0

WORKDIR /app

# Weights are shipped in the image: no network at evaluation time.
# Download them BEFORE building (see README, step "pesos"):
#   models/llm  -> Qwen/Qwen2.5-7B-Instruct      (~15 GiB bf16)
#   models/vlm  -> Qwen/Qwen2-VL-2B-Instruct     (~4.5 GiB)
COPY models/ /models/

# --no-deps so pip cannot replace the ROCm torch with a CUDA build.
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /app/requirements.txt \
 && python3 -c "import torch; v=torch.__version__; print(v); assert '+rocm' in v, 'torch is no longer the ROCm build'"

COPY app/ /app/
RUN mkdir -p /app/corpus /app/output /app/index

ENV MC3_BACKEND=hf \
    MC3_LLM=/models/llm \
    MC3_VLM=/models/vlm \
    MC3_SOCKET=/tmp/mc3.sock \
    MC3_INDEX_DIR=/app/index \
    PYTHONUNBUFFERED=1

# The resident model server starts with the container and keeps it alive.
CMD ["python3", "/app/server.py"]
