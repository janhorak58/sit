# ASR service

GPU-backed, OpenAI-compatible HTTP service for Whisper large-v3-turbo
(ONNX weights from [onnx-community/whisper-large-v3-turbo](https://huggingface.co/onnx-community/whisper-large-v3-turbo)).
The directory, Compose project and `CANARY_*` variables keep their original
name, so existing deployments and their model cache volume stay valid.

`nemo-canary-1b-v2` remains a one-line switch (`CANARY_MODEL`). It scores better
on FLEURS Czech: 7.86 % WER versus 11.33 % for `whisper-large-v3`
([arXiv 2509.14128](https://arxiv.org/abs/2509.14128), Table B). The client runs
the same default model locally as its fallback, on CPU; this service is the
fast tier for it.

Inference runs through `onnx-asr` on ONNX Runtime — the same library the client
uses locally — so there is one inference stack in the project, and no PyTorch
dependency in this image.

## Host prerequisites

```sh
nvidia-smi
docker run --rm --gpus all nvcr.io/nvidia/cuda:13.0.3-base-ubuntu24.04 nvidia-smi
```

The host needs an NVIDIA driver for CUDA 13 (580+), Docker Engine with Compose, and
the NVIDIA Container Toolkit. The model weights are public: no Hugging Face
token is required, and `HF_TOKEN` only raises download rate limits.

VRAM: the float32 weights take about 3.3 GB, int8 (`CANARY_QUANTIZATION=int8`)
about 1.1 GB. Batched windows add activations on top.

## Deploy

Copy the `canary/` directory to the GPU host, then run there:

```sh
cd canary
cp .env.example .env
docker compose build
docker compose up -d --wait --wait-timeout 900
docker compose logs -f canary
```

The first start downloads ~3.3 GB of weights into the persistent
`huggingface-cache` volume; later starts reuse it. The service binds to
`127.0.0.1:8208`, which is what the client's SSH tunnel expects. Set
`CANARY_BIND=0.0.0.0` only when a host firewall or trusted private network
restricts access.

When upgrading an existing deployment, set
`CANARY_MODEL=onnx-community/whisper-large-v3-turbo` in its `.env`: an existing
`.env` overrides the new default. Then rebuild and restart as below.

## Verify

On the host:

```sh
curl -fsS http://127.0.0.1:8208/health
curl -fsS http://127.0.0.1:8208/v1/models
curl --fail-with-body \
  -F 'file=@/absolute/path/to/short-czech.wav;type=audio/wav' \
  -F model=onnx-community/whisper-large-v3-turbo \
  -F language=cs \
  -F response_format=verbose_json \
  http://127.0.0.1:8208/v1/audio/transcriptions
```

The service starts without the model, so `/health` first reports
`"loaded":false`. After the transcription request it reports `"device":"cuda"`
and `"loaded":true`. The transcription
response carries `duration`, `text`, and a `segments` array of
`start`/`end`/`text` — exactly the `verbose_json` subset the client stitches
chunks from. If `/health` reports `"device":"cuda"` but throughput looks like
CPU, check `docker compose logs canary` for ONNX Runtime provider warnings; the
image build fails when the CUDA provider is unavailable, so a CPU fallback can
only come from a runtime driver mismatch.

## Client wiring

The client posts 15-minute 32 kbps mp3 chunks and probes `/v1/models` before
each run; an unreachable service silently falls back to the local model on CPU.

```sh
# On the workstation:
ssh -N -o ExitOnForwardFailure=yes -L 8208:127.0.0.1:8208 user@gpu-host
# transcriber .env
# SPARK_WHISPER_URL=http://127.0.0.1:8208/v1/audio/transcriptions
# SPARK_WHISPER_MODEL=onnx-community/whisper-large-v3-turbo
```

`SPARK_WHISPER_MODEL` is echoed back in `meeting.json` provenance; the service
serves whatever `CANARY_MODEL` it loaded regardless of the requested name.

## Tuning

| Variable | Default | Effect |
| --- | --- | --- |
| `CANARY_MODEL` | `onnx-community/whisper-large-v3-turbo` | Any `onnx-asr` model name, e.g. `nemo-canary-1b-v2` |
| `CANARY_QUANTIZATION` | empty (float32) | `int8` halves VRAM, costs some accuracy |
| `CANARY_WINDOW_SECONDS` | `30` | Max speech window sent to the model; Whisper reads at most 30 s per pass |
| `CANARY_MIN_SILENCE_MS` | `1000` | Shortest pause the VAD cuts at; shorter cuts leave scraps the model misreads |
| `CANARY_BATCH_SIZE` | `8` | Windows per forward pass; trades VRAM for throughput |
| `CANARY_DEFAULT_LANGUAGE` | `cs` | Used when a request omits `language` |
| `CANARY_DEVICE` | `cuda` | `cpu` is for debugging only |
| `IDLE_EXIT_SECONDS` | `900` | The process exits after this long without a request and Compose restarts it without the model, which returns its GPU memory. The next request waits for the model to load. `0` keeps the model loaded for good. |
| `PRELOAD_MODEL` | `1` | Load the model at startup. Applies only with `IDLE_EXIT_SECONDS=0`. |

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
