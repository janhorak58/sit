import * as api from './api.js';
import {$, el} from './dom.js';

// Known remote failures mapped to the one thing that actually fixes them.
const HINTS = [
  [/vllm\[audio\]/i, 'Spark is missing vLLM audio support — install `pip install vllm[audio]` and restart the server. Until then everything will run locally.'],
  [/Maximum file size|audio_filesize_mb/i, 'The server is rejecting large files. Transcription is sent in 15-minute mp3 chunks; if the limit still hits, raise vLLM `--max-audio-filesize-mb`.'],
  [/Connection refused|ConnectError|timed out/i, 'The endpoint is not responding — Spark is not running, uses a different port, or the path is blocked by a VPN.'],
  [/404|Not Found/i, 'The endpoint exists, but the transcription path does not — check SPARK_WHISPER_URL in .env.'],
  [/model/i, 'Model not found — the name in SPARK_WHISPER_MODEL must match one from /v1/models.'],
];

function hintFor(detail) {
  const found = HINTS.find(([re]) => re.test(detail || ''));
  return found ? found[1] : null;
}

function row(label, value, state) {
  return el('div', {className: 'setrow' + (state ? ' ' + state : '')}, [
    el('span', {className: 'setkey', textContent: label}),
    el('span', {className: 'setval', textContent: value === null || value === undefined || value === '' ? '—' : String(value)}),
  ]);
}

function card(title, children) {
  return el('section', {className: 'card setcard'}, [el('h3', {textContent: title}), ...children]);
}

export async function loadSettings() {
  const wrap = $('settings-body');
  wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Testing engine…'}));
  const j = await api.diagnostics();
  if (j.error) {
    wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Error: ' + j.error}));
    return;
  }
  const remote = j.remote || {};
  const upload = remote.upload || {};
  const diarization = j.diarization || {};
  const diarizationRemote = diarization.remote || {};
  const ok = upload.ok;
  const hint = ok ? null : hintFor(upload.detail || remote.last_error);

  const cards = [
    card('Remote engine (Spark)', [
      row('Status', ok ? 'Working — transcriptions go to Spark' : 'Not working — transcribing locally', ok ? 'ok' : 'bad'),
      row('Endpoint', remote.url),
      row('Model', remote.model),
      row('/v1/models response', remote.probe && remote.probe.available ? 'OK' : 'unavailable', remote.probe && remote.probe.available ? 'ok' : 'bad'),
      row('Upload test (1s of audio)', upload.detail),
      row('Last transcription error', remote.last_error),
      ...(hint ? [el('p', {className: 'sethint', textContent: '→ ' + hint})] : []),
    ]),
    card('Local model (fallback)', [
      row('Model', (j.local || {}).model),
      row('Device', (j.local || {}).device),
      row('Precision', (j.local || {}).compute_type),
    ]),
    card('Speaker recognition', [
      row(
        'Primary engine',
        diarizationRemote.available ? 'Remote Spark' : 'Spark unavailable — local fallback',
        diarizationRemote.available ? 'ok' : 'bad',
      ),
      row('Spark model', diarizationRemote.model),
      row('Spark device', diarizationRemote.device),
      row('Local fallback', diarization.local_model),
      row(
        'HF_TOKEN for fallback',
        diarization.hf_token ? 'set' : 'missing',
        diarization.hf_token ? 'ok' : 'bad',
      ),
      ...(diarizationRemote.last_error ? [el('p', {
        className: 'sethint',
        textContent: '→ Spark diarization: ' + diarizationRemote.last_error,
      })] : []),
    ]),
  ];
  wrap.replaceChildren(...cards);
}

$('settings-refresh').onclick = loadSettings;
