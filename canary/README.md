# Canary ASR

GPU-backed, OpenAI-compatible HTTP service for `nvidia/canary-1b-v2`
(ONNX weights from [istupakov/canary-1b-v2-onnx](https://huggingface.co/istupakov/canary-1b-v2-onnx)).

Why Canary for this deployment: on FLEURS Czech it reaches 7.86 % WER versus
11.33 % for `whisper-large-v3` and 11.01 % for `parakeet-tdt-0.6b-v3`
([arXiv 2509.14128](https://arxiv.org/abs/2509.14128), Table B), at roughly 5x
whisper-large-v3's throughput. The client runs the same model locally as its
fallback, on CPU at roughly 3x realtime; this service is the fast tier for it.

Inference runs through `onnx-asr` on ONNX Runtime — the same library the client
uses locally — so there is one inference stack in the project, and no NeMo or
PyTorch dependency in this image.

## Host prerequisites

```sh
nvidia-smi
docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.3-base-ubuntu24.04 nvidia-smi
```

The host needs an NVIDIA driver for CUDA 13 (580+), Docker Engine with Compose, and
the NVIDIA Container Toolkit. The model weights are public: no Hugging Face
token is required, and `HF_TOKEN` only raises download rate limits.

VRAM: about 4 GB with the default full-precision weights, about 2 GB with
`CANARY_QUANTIZATION=int8`.

## Deploy

Copy the `canary/` directory to the GPU host, then run there:

```sh
cd canary
cp .env.example .env
docker compose build
docker compose up -d --wait --wait-timeout 900
docker compose logs -f canary
```

The first start downloads ~4 GB of weights into the persistent
`huggingface-cache` volume; later starts reuse it. The service binds to
`127.0.0.1:8208`, which is what the client's SSH tunnel expects. Set
`CANARY_BIND=0.0.0.0` only when a host firewall or trusted private network
restricts access.

## Verify

Wait until model preload completes, then on the host:

```sh
curl -fsS http://127.0.0.1:8208/health
curl -fsS http://127.0.0.1:8208/v1/models
curl --fail-with-body \
  -F 'file=@/absolute/path/to/short-czech.wav;type=audio/wav' \
  -F model=nemo-canary-1b-v2 \
  -F language=cs \
  -F response_format=verbose_json \
  http://127.0.0.1:8208/v1/audio/transcriptions
```

A ready service reports `"device":"cuda"` and `"loaded":true`. The transcription
response carries `duration`, `text`, and a `segments` array of
`start`/`end`/`text` — exactly the `verbose_json` subset the client stitches
chunks from. If `/health` reports `"device":"cuda"` but throughput looks like
CPU, check `docker compose logs canary` for ONNX Runtime provider warnings; the
image build fails when the CUDA provider is unavailable, so a CPU fallback can
only come from a runtime driver mismatch.

## Client wiring

The client posts 15-minute 32 kbps mp3 chunks and probes `/v1/models` before
each run; an unreachable service silently falls back to local Canary on CPU.

```sh
# On the workstation:
ssh -N -o ExitOnForwardFailure=yes -L 8208:127.0.0.1:8208 user@gpu-host
# transcriber .env
# SPARK_WHISPER_URL=http://127.0.0.1:8208/v1/audio/transcriptions
# SPARK_WHISPER_MODEL=nemo-canary-1b-v2
```

`SPARK_WHISPER_MODEL` is echoed back in `meeting.json` provenance; the service
serves whatever `CANARY_MODEL` it loaded regardless of the requested name.

## Tuning

| Variable | Default | Effect |
| --- | --- | --- |
| `CANARY_MODEL` | `nemo-canary-1b-v2` | Any `onnx-asr` model name, e.g. `nemo-parakeet-tdt-0.6b-v3` |
| `CANARY_QUANTIZATION` | empty (float32) | `int8` halves VRAM, costs some accuracy |
| `CANARY_WINDOW_SECONDS` | `30` | Max speech window sent to the model; Canary drifts on long windows |
| `CANARY_BATCH_SIZE` | `8` | Windows per forward pass; trades VRAM for throughput |
| `CANARY_DEFAULT_LANGUAGE` | `cs` | Used when a request omits `language` |
| `CANARY_DEVICE` | `cuda` | `cpu` is for debugging only |

Silence never reaches the model: Silero VAD (CPU) cuts speech into windows
first, which is also what keeps segment timestamps meaningful.

## Operations

```sh
docker compose ps
docker compose logs --tail=200 canary
docker compose restart canary
docker compose build --pull
docker compose up -d
```

## Tests

```sh
pip install -r requirements-dev.txt
pytest tests
```

The tests exercise the HTTP contract (probe, verbose_json shape, empty/undecodable
uploads, CUDA fallback detection) with the model stubbed out; they need no GPU.
