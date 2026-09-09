ARG BASE_IMAGE=nvcr.io/nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    CANARY_MODEL=nemo-canary-1b-v2 \
    CANARY_DEVICE=cuda

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Own venv: the image's python3 is externally managed (PEP 668).
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# get_available_providers() lists CUDA even when its .so cannot be loaded, so a
# CUDA/cuDNN mismatch would only surface at startup (or worse, as a silent 20x
# slower CPU fallback). Resolve the provider library's dependencies instead:
# that needs no GPU in the builder and catches exactly that mismatch. libcuda
# is the driver stub, injected at runtime by the NVIDIA container toolkit.
RUN CAPI=$(python -c "import onnxruntime, pathlib; print(pathlib.Path(onnxruntime.__file__).parent / 'capi')") \
    && ldd "$CAPI/libonnxruntime_providers_cuda.so" > /tmp/ldd \
    && cat /tmp/ldd \
    && if grep 'not found' /tmp/ldd | grep -qv 'libcuda\.so\.1'; then \
           echo 'CUDA provider has unresolved libraries; base image CUDA/cuDNN does not match onnxruntime-gpu' >&2; \
           exit 1; \
       fi \
    && python -c "import onnxruntime as ort; \
providers = ort.get_available_providers(); \
assert 'CUDAExecutionProvider' in providers, providers; \
print('onnxruntime', ort.__version__, providers)"

COPY app.py .

EXPOSE 8000
# Model load pulls ~4 GB on first start, hence the generous start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
