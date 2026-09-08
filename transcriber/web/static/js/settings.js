import * as api from './api.js';
import {$, el} from './dom.js';

// Known remote failures mapped to the one thing that actually fixes them.
const HINTS = [
  [/vllm\[audio\]/i, 'Na Sparku chybí audio podpora vLLM — doinstaluj `pip install vllm[audio]` a restartuj server. Do té doby poběží všechno lokálně.'],
  [/Maximum file size|audio_filesize_mb/i, 'Server odmítá velké soubory. Přepis se posílá po 15minutových mp3 kusech; pokud limit hlásí i na ně, zvyš na vLLM `--max-audio-filesize-mb`.'],
  [/Connection refused|ConnectError|timed out/i, 'Endpoint neodpovídá — Spark neběží, jiný port, nebo cestu blokuje VPN.'],
  [/404|Not Found/i, 'Endpoint existuje, ale cesta k transkripci ne — zkontroluj SPARK_WHISPER_URL v .env.'],
  [/model/i, 'Model se nenašel — jméno v SPARK_WHISPER_MODEL musí odpovídat tomu z /v1/models.'],
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
  wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Testuji engine…'}));
  const j = await api.diagnostics();
  if (j.error) {
    wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Chyba: ' + j.error}));
    return;
  }
  const remote = j.remote || {};
  const upload = remote.upload || {};
  const diarization = j.diarization || {};
  const diarizationRemote = diarization.remote || {};
  const ok = upload.ok;
  const hint = ok ? null : hintFor(upload.detail || remote.last_error);

  const cards = [
    card('Vzdálený engine (Spark)', [
      row('Stav', ok ? 'Funguje — přepisy jdou na Spark' : 'Nefunguje — přepisuje se lokálně', ok ? 'ok' : 'bad'),
      row('Endpoint', remote.url),
      row('Model', remote.model),
      row('Odpověď /v1/models', remote.probe && remote.probe.available ? 'OK' : 'nedostupné', remote.probe && remote.probe.available ? 'ok' : 'bad'),
      row('Test uploadu (1 s audia)', upload.detail),
      row('Poslední chyba při přepisu', remote.last_error),
      ...(hint ? [el('p', {className: 'sethint', textContent: '→ ' + hint})] : []),
    ]),
    card('Lokální model (fallback)', [
      row('Model', (j.local || {}).model),
      row('Zařízení', (j.local || {}).device),
      row('Přesnost', (j.local || {}).compute_type),
    ]),
    card('Rozpoznávání mluvčích', [
      row(
        'Primární engine',
        diarizationRemote.available ? 'Pracovní Spark' : 'Spark nedostupný — lokální fallback',
        diarizationRemote.available ? 'ok' : 'bad',
      ),
      row('Spark model', diarizationRemote.model),
      row('Spark zařízení', diarizationRemote.device),
      row('Lokální fallback', diarization.local_model),
      row(
        'HF_TOKEN pro fallback',
        diarization.hf_token ? 'nastavený' : 'chybí',
        diarization.hf_token ? 'ok' : 'bad',
      ),
      ...(diarizationRemote.last_error ? [el('p', {
        className: 'sethint',
        textContent: '→ Spark diarizace: ' + diarizationRemote.last_error,
      })] : []),
    ]),
  ];
  wrap.replaceChildren(...cards);
}

$('settings-refresh').onclick = loadSettings;
