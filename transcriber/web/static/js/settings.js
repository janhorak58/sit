import * as api from './api.js';
import {$, el} from './dom.js';

let currentConnections = null;

// Known remote failures mapped to the one thing that actually fixes them.
const HINTS = [
  [/vllm\[audio\]/i,
    'The remote transcription service cannot process audio right now, so this computer is transcribing instead.',
    'The server is missing vLLM audio support: run pip install vllm[audio] there and restart it.'],
  [/Maximum file size|audio_filesize_mb/i,
    'The remote service rejected a recording for being too large.',
    'Transcription is sent in 15-minute chunks; if the limit still hits, raise the server\'s --max-audio-filesize-mb setting.'],
  [/Connection refused|ConnectError|timed out/i,
    'This computer cannot reach the configured remote address.',
    'Check the SSH tunnel, VPN, host, and port.'],
  [/404|Not Found/i,
    'The remote server was reached, but it does not recognize the configured address.',
    'The configured API path does not exist on that server.'],
  [/model/i,
    'The remote server does not have the configured model available.',
    'The configured model name must match one the server actually serves.'],
];

function hintFor(detail) {
  const found = HINTS.find(([re]) => re.test(detail || ''));
  return found ? {plain: found[1], advanced: found[2]} : null;
}

function hintNode(hint) {
  return el('div', {}, [
    el('p', {className: 'sethint', textContent: '→ ' + hint.plain}),
    el('details', {className: 'advanced-options'}, [
      el('summary', {textContent: 'Technical detail'}),
      el('div', {className: 'advanced-body'}, [el('p', {textContent: hint.advanced})]),
    ]),
  ]);
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

function at(object, path, value) {
  const keys = path.split('.');
  const leaf = keys.pop();
  // Create missing branches: the form must be readable even when the backend
  // never answered and there is no loaded config to clone.
  const target = keys.reduce((node, key) => (node[key] ??= {}), object);
  target[leaf] = value;
}

function fillConnectionForm(connections) {
  document.querySelectorAll('#connections-form [data-connection-path]').forEach(field => {
    const value = field.dataset.connectionPath.split('.').reduce((node, key) => node?.[key], connections);
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else if (field.type === 'number') field.value = value ? String(value) : '';
    else field.value = value ?? '';
  });
}

function showEndpoints(endpoints) {
  Object.entries(endpoints || {}).forEach(([name, url]) => {
    const target = $('endpoint-' + name);
    if (target) target.textContent = url || 'No remote endpoint — local processing only';
  });
}

// The backend never sends stored secrets back, so an untouched field means
// "keep what is stored"; clearing one needs its explicit checkbox.
function showSecrets(connections) {
  $('hf-token-state').textContent = connections?.diarization?.hf_token_set
    ? 'A token is stored. Type a new one to replace it.'
    : 'No token stored. The local pyannote fallback needs one for gated models.';
  $('llm-api-key-state').textContent = connections?.llm?.api_key_set
    ? 'A key is stored. Type a new one to replace it.'
    : 'No key stored. Set one if the server answers 401 Unauthorized.';
  document.querySelectorAll('#connections-form [data-connection-secret]').forEach(field => {
    field.value = '';
  });
  $('forget-hf-token').checked = false;
  $('forget-llm-api-key').checked = false;
}

function readConnectionForm() {
  const next = currentConnections ? structuredClone(currentConnections) : {};
  delete next.diarization?.hf_token_set;
  delete next.llm?.api_key_set;
  document.querySelectorAll('#connections-form [data-connection-path]').forEach(field => {
    at(next, field.dataset.connectionPath, field.type === 'checkbox' ? field.checked : field.value);
  });
  document.querySelectorAll('#connections-form [data-connection-secret]').forEach(field => {
    at(next, field.dataset.connectionSecret, field.value.trim());
  });
  next.forget_hf_token = $('forget-hf-token').checked;
  next.forget_llm_api_key = $('forget-llm-api-key').checked;
  return next;
}

function tunnelCards(ssh) {
  if (!ssh?.enabled) {
    return [card('SSH tunnels', [
      row('Status', 'Disabled — endpoints are used directly'),
    ])];
  }
  if (!ssh.hosts?.length) {
    return [card('SSH tunnels', [row('Status', 'No tunnel started', 'bad')])];
  }
  return ssh.hosts.map(tunnel => card('SSH · ' + tunnel.host, [
    row('Status', tunnel.state === 'running' ? 'Tunnel process running' : 'Not connected', tunnel.state === 'running' ? 'ok' : 'bad'),
    row('Services', (tunnel.services || []).join(', ')),
    ...(tunnel.error ? [el('p', {className: 'sethint', textContent: '→ ' + tunnel.error})] : []),
  ]));
}

async function loadConnectionForm() {
  const status = $('connections-status');
  const result = await api.connections();
  if (result.error) {
    status.textContent = 'Could not load connections: ' + result.error;
    return;
  }
  currentConnections = result.connections;
  fillConnectionForm(currentConnections);
  showEndpoints(result.endpoints);
  showSecrets(currentConnections);
  status.textContent = result.tunnels?.enabled
    ? 'Managed SSH is enabled. Saving restarts its tunnels.'
    : 'Enable managed SSH to connect automatically when SIT starts.';
}

async function loadDiagnostics() {
  const wrap = $('settings-body');
  wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Testing services…'}));
  const j = await api.diagnostics();
  if (j.error) {
    wrap.replaceChildren(el('p', {className: 'status-line', textContent: 'Error: ' + j.error}));
    return;
  }
  const remote = j.remote || {};
  const upload = remote.upload || {};
  const diarization = j.diarization || {};
  const diarizationRemote = diarization.remote || {};
  const analysis = j.analysis || {};
  const ok = upload.ok;
  const hint = ok ? null : hintFor(upload.detail || remote.last_error);

  const cards = [
    ...tunnelCards(j.ssh),
    card('Remote transcription', [
      row('Status', ok ? 'Working — using the remote engine' : 'Unavailable — using the engine on this computer', ok ? 'ok' : 'bad'),
      row('Address', remote.url),
      row('Model', remote.model),
      row('Server has this model', remote.probe && remote.probe.available ? 'Yes' : 'Could not confirm', remote.probe && remote.probe.available ? 'ok' : 'bad'),
      row('Connection test', upload.detail),
      row('Last error', remote.last_error),
      ...(hint ? [hintNode(hint)] : []),
    ]),
    card('Transcription on this computer (fallback)', [
      row('Model', (j.local || {}).model),
      row('Device', (j.local || {}).device),
      row('Precision', (j.local || {}).compute_type),
    ]),
    card('Speaker recognition', [
      row(
        'Primary engine',
        diarizationRemote.available ? 'Remote engine' : 'Remote engine unavailable — using local fallback',
        diarizationRemote.available ? 'ok' : 'bad',
      ),
      row('Remote model', diarizationRemote.model),
      row('Remote device', diarizationRemote.device),
      row('Local fallback model', diarization.local_model),
      row(
        'Access token for local fallback',
        diarization.hf_token ? 'Saved' : 'Not set',
        diarization.hf_token ? 'ok' : 'bad',
      ),
      ...(diarizationRemote.last_error ? [el('p', {
        className: 'sethint',
        textContent: '→ Remote speaker recognition: ' + diarizationRemote.last_error,
      })] : []),
    ]),
    card('AI analysis', [
      row('Status', analysis.available ? 'Running — analysis calls will reach it' : 'Not reachable — analysis requests will fail', analysis.available ? 'ok' : 'bad'),
      row('Endpoint', analysis.url),
      row('Model', analysis.model),
      row('API key', analysis.api_key ? 'Saved' : 'Not set', analysis.api_key ? 'ok' : 'bad'),
      ...(analysis.last_error ? [el('p', {className: 'sethint', textContent: '→ ' + analysis.last_error})] : []),
    ]),
  ];
  wrap.replaceChildren(...cards);
}

export async function loadSettings() {
  await Promise.all([loadConnectionForm(), loadDiagnostics()]);
}

// One save covers the whole panel; the per-service buttons differ only in
// which result they report back inline.
async function applyConnections() {
  const result = await api.saveConnections(readConnectionForm());
  if (result.error) return result;
  currentConnections = result.connections;
  fillConnectionForm(currentConnections);
  showEndpoints(result.endpoints);
  showSecrets(currentConnections);
  // The sidebar engine badge is resolved from these endpoints.
  window.dispatchEvent(new CustomEvent('transcriber:connections-changed', {detail: result.connections}));
  return result;
}

function tunnelErrorFor(diagnostics, name) {
  const host = (diagnostics.ssh?.hosts || []).find(entry => (entry.services || []).includes(name));
  return host && host.state !== 'running' ? host.error || 'SSH tunnel is not running.' : null;
}

function serviceReport(diagnostics, name) {
  const tunnel = tunnelErrorFor(diagnostics, name);
  if (tunnel) return {ok: false, text: tunnel};
  if (name === 'transcription') {
    const upload = diagnostics.remote?.upload || {};
    return upload.ok
      ? {ok: true, text: 'Working — ' + upload.detail}
      : {ok: false, text: upload.detail || 'Endpoint did not accept a test upload.'};
  }
  if (name === 'diarization') {
    const remote = diagnostics.diarization?.remote || {};
    return remote.available
      ? {ok: true, text: 'Working — ' + [remote.model, remote.device].filter(Boolean).join(' on ')}
      : {ok: false, text: remote.last_error || 'Endpoint is not responding; speakers fall back to the local model.'};
  }
  const analysis = diagnostics.analysis || {};
  return analysis.available
    ? {ok: true, text: 'Working — ' + analysis.model}
    : {ok: false, text: analysis.last_error || 'Endpoint is not responding.'};
}

// `pending` drives the spinner next to whichever button is working.
function showResult(name, state, text) {
  const target = $('result-' + name);
  target.className = 'connection-result' + (state ? ' ' + state : '');
  target.textContent = text;
}

function busy(button, name, text) {
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  showResult(name, 'pending', text);
}

function idle(button) {
  button.disabled = false;
  button.removeAttribute('aria-busy');
}

// A stale backend answers 404 for endpoints this build added; say so instead
// of showing a bare "Not Found".
function explain(message) {
  const text = String(message);
  return /not found/i.test(text)
    ? text + ' — the running backend is older than this page. Restart SIT.'
    : text;
}

export function initSettings() {
  $('settings-refresh').onclick = async () => {
    const button = $('settings-refresh');
    button.disabled = true;
    await loadDiagnostics();
    button.disabled = false;
  };
  $('ssh-test').onclick = async () => {
    const button = $('ssh-test');
    busy(button, 'ssh', 'Signing in over SSH…');
    try {
      const result = await api.testSsh(readConnectionForm());
      if (result.error) {
        showResult('ssh', 'bad', explain(result.error));
        return;
      }
      showResult('ssh', result.ok ? 'ok' : 'bad', result.detail || 'The backend returned no detail.');
    } catch (error) {
      showResult('ssh', 'bad', 'The SSH test could not run: ' + explain(error.message || error));
    } finally {
      idle(button);
    }
  };
  document.querySelectorAll('[data-connection-test]').forEach(button => {
    const name = button.dataset.connectionTest;
    button.onclick = async () => {
      busy(button, name, 'Opening the tunnel and testing…');
      try {
        const saved = await applyConnections();
        if (saved.error) {
          showResult(name, 'bad', explain(saved.error));
          return;
        }
        showResult(name, 'pending', 'Configuration saved. Testing the service…');
        const diagnostics = await api.diagnostics();
        if (diagnostics.error) {
          showResult(name, 'bad', explain(diagnostics.error));
          return;
        }
        const report = serviceReport(diagnostics, name);
        showResult(name, report.ok ? 'ok' : 'bad', report.text);
        await loadDiagnostics();
      } catch (error) {
        showResult(name, 'bad', 'The test could not run: ' + explain(error.message || error));
      } finally {
        idle(button);
      }
    };
  });
  $('connections-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('connections-save');
    const status = $('connections-status');
    const names = ['transcription', 'diarization', 'llm'];
    const say = (text, pending = false) => {
      status.className = 'status-line' + (pending ? ' pending' : '');
      status.textContent = text;
    };
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    say('Saving and opening tunnels…', true);
    names.forEach(name => showResult(name, 'pending', 'Waiting for the tunnel…'));
    try {
      const result = await applyConnections();
      if (result.error) {
        say(explain(result.error));
        names.forEach(name => showResult(name, 'bad', 'Not tested — the configuration was rejected.'));
        return;
      }
      const broken = (result.tunnels?.hosts || []).filter(host => host.state !== 'running');
      broken.forEach(host => (host.services || []).forEach(
        name => showResult(name, 'bad', host.host + ': ' + (host.error || 'SSH tunnel is not running.')),
      ));
      say(broken.length ? 'Some SSH tunnels are down. Testing the rest…' : 'Tunnels are up. Testing every service…', true);
      names.filter(name => !broken.some(host => (host.services || []).includes(name)))
        .forEach(name => showResult(name, 'pending', 'Testing the service…'));
      const diagnostics = await api.diagnostics();
      if (diagnostics.error) {
        say(explain(diagnostics.error));
        return;
      }
      const working = names.filter(name => {
        const report = serviceReport(diagnostics, name);
        showResult(name, report.ok ? 'ok' : 'bad', report.text);
        return report.ok;
      });
      say(working.length === names.length
        ? 'All three services are working.'
        : working.length
          ? `Working: ${working.join(', ')}. The rest failed — see the reason on each card.`
          : 'No service is reachable — see the reason on each card.');
      await loadDiagnostics();
    } catch (error) {
      say('Setup could not run: ' + explain(error.message || error));
    } finally {
      idle(button);
    }
  };
}
