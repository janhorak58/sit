ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:25.08-py3@sha256:ace9a848c0ae543317e3c4763b6b4248961c47902625abfe3c77a0fb931c50fb
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    DIARIZE_MODEL=pyannote/speaker-diarization-3.1 \
    DIARIZE_DEVICE=cuda \
    PYANNOTE_METRICS_ENABLED=0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /service
COPY requirements.txt .
# Install the complete pinned closure without replacing NVIDIA's GB10 torch.
# Constrain NumPy to the base image too: changing its ABI breaks torch interop.
RUN python -c "import numpy; print('numpy==' + numpy.__version__)" > constraints.txt \
    && pip install --no-cache-dir --no-deps -c constraints.txt -r requirements.txt
# pyannote 3.3.2 uses SemVer for Python package versions. NVIDIA's valid PEP 440
# prerelease version needs packaging.Version; retain all compatibility warnings.
COPY pyannote_version.py .
RUN python -c "from importlib.metadata import distribution; from shutil import copyfile; \
package = distribution('pyannote.audio'); \
assert package.version == '3.3.2', package.version; \
copyfile('pyannote_version.py', package.locate_file('pyannote/audio/utils/version.py'))"
# torch.from_numpy is what fails when numpy was swapped; fail the build, not startup.
RUN python -c "import numpy, torch, torchaudio, pyannote.audio; \
from pyannote.audio.models.blocks.sincnet import SincNet; \
from pyannote.audio.utils.version import check_version; \
check_version('torch', '2.3.1', torch.__version__, 'Model'); \
torch.from_numpy(numpy.zeros(1, dtype='float32')); SincNet(); \
print('torch', torch.__version__, 'cuda', torch.version.cuda, 'numpy', numpy.__version__, 'torchaudio', torchaudio.__version__, 'pyannote.audio', pyannote.audio.__version__)"
COPY app.py .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
