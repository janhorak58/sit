# Pyannote diarizer

GPU-backed HTTP service for `pyannote/speaker-diarization-3.1`.

## Host prerequisites

On either `marlene` or `charlene`, verify Docker can expose an NVIDIA GPU:

```sh
nvidia-smi
docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi
```

The host needs an NVIDIA driver, Docker Engine with Compose, and NVIDIA Container Toolkit. The repository does not define hardware differences between the two hosts; deploy to the one where both checks succeed.

Before first start, accept the model conditions at <https://huggingface.co/pyannote/speaker-diarization-3.1> and <https://huggingface.co/pyannote/segmentation-3.0>, then create a read-only Hugging Face token.

## Deploy

Copy the `diarizer/` directory to the selected host, then run there:

```sh
cd diarizer
cp .env.example .env
chmod 600 .env
# Edit .env and replace hf_replace_me.
docker compose build
docker compose up -d --wait --wait-timeout 600
docker compose logs -f diarizer
```

The first start downloads the model into the persistent `huggingface-cache` volume. Later starts reuse it. The token is passed only at runtime; never put it in the Dockerfile or commit `.env`.

The service binds to `127.0.0.1:8000` by default. Set `DIARIZER_BIND=0.0.0.0` only when host firewall or a trusted private network restricts access.

For the Charlene deployment, set `DIARIZER_PORT=8299` in `.env` and use port
8299 in the verification commands below. When upgrading an existing deployment,
also set `DIARIZE_MODEL=pyannote/speaker-diarization-3.1`: an existing `.env`
overrides the new Compose default. Do not overwrite your existing `HF_TOKEN`.

## Verify

Wait until model preload completes, then:

```sh
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models
curl --fail-with-body \
  -F 'file=@/absolute/path/to/short-test.wav;type=audio/wav' \
  -F num_speakers=2 \
  http://127.0.0.1:8000/v1/audio/diarizations
```

A ready service reports `"device":"cuda"` and `"loaded":true`. The inference response contains a `segments` array with `start`, `end`, and `speaker` fields.

## Runtime compatibility

The pinned NGC image supplies GB10-enabled PyTorch and NumPy 1.26.4.
`requirements.txt` freezes the complete Python dependency closure except torch
and its bundled CUDA dependencies, which must remain from the image. Docker
installs this closure with `--no-deps` and constrains NumPy to the image version.
pyannote.audio 3.3.2 and pyannote.core 5.0.0 work with NumPy 1.x; pyannote 4.x
requires NumPy 2.x and is not compatible with this image's torch/NumPy bridge.
The build checks `torch.from_numpy`, constructs SincNet, and exercises checkpoint
version checking with the image's actual torch version before succeeding.

`pyannote_version.py` vendors the small [upstream version-checking module](https://github.com/pyannote/pyannote-audio/blob/3.3.2/pyannote/audio/utils/version.py),
with its SemVer parser replaced by `packaging.version.Version`. Docker installs
the complete module into the pinned pyannote 3.3.2 package rather than applying a
context-sensitive patch. NVIDIA uses Python prerelease versions such as
`2.8.0a0+34c6371d24`, which SemVer rejects. Compatibility warnings remain enabled;
torch's version is not changed. Parser regressions are in `tests/test_version.py`.

`app.py` decodes uploads with `ffmpeg` into a mono 16 kHz waveform; torchcodec
is not needed. Model loading keeps PyTorch's weights-only mode enabled and
temporarily allowlists the four types used by the legacy model checkpoints.

After changing dependency pins, check the full ARM64 resolution:

```sh
uv pip compile --python-version 3.12 --python-platform aarch64-unknown-linux-gnu requirements.txt
```

Do not install that command's upstream torch/CUDA output into the NGC image.
Rebuild and exercise a real audio upload on the target GPU as well.

## Operations

```sh
docker compose ps
docker compose logs --tail=200 diarizer
docker compose restart diarizer
docker compose pull --ignore-buildable
docker compose build --pull
docker compose up -d
```

To access a loopback-bound deployment remotely, create a tunnel from your workstation
and point the transcriber at the forwarded port with `SPARK_DIARIZER_URL`:

```sh
# With DIARIZER_PORT=8299 on Charlene:
ssh -N -o ExitOnForwardFailure=yes -L 8299:127.0.0.1:8299 horak1@charlene.cxi.tul.cz
# transcriber .env
# SPARK_DIARIZER_URL=http://127.0.0.1:8299/v1/audio/diarizations
```
