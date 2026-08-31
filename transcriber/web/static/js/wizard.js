import * as api from './api.js';
import {$} from './dom.js';
import {state} from './state.js';
import {transcribePath} from './transcribe.js';

let source = 'recording';
let startedAt = 0;
let timerId;
let suggestedFolder = '';

function go(step) {
  document.querySelectorAll('.stage').forEach(node => node.classList.toggle('active', node.dataset.stage === String(step)));
  document.querySelectorAll('.step').forEach(node => {
    const number = Number(node.dataset.step);
    node.classList.toggle('active', number === step);
    node.classList.toggle('done', number < step);
    if (number <= step) node.disabled = false;
  });
}

function setBadge(data) {
  const badge = $('asr-badge');
  badge.className = 'engine ' + (data.available ? 'remote' : 'local');
  badge.querySelector('b').textContent = data.label;
}
function renderProjects(projects) {
  $('sidebar-projects').replaceChildren(...(projects.length
    ? projects.map(name => Object.assign(document.createElement('a'), {href: '#archive', textContent: name}))
    : [Object.assign(document.createElement('span'), {className: 'side-empty', textContent: 'Zatím bez projektů'})]));
}

function tick() {
  const seconds = Math.floor((Date.now() - startedAt) / 1000);
  $('timer').textContent = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
}

async function prepareProjectStep(kind) {
  source = kind;
  $('stop').hidden = kind !== 'recording';
  $('save-youtube').hidden = kind === 'recording';
  go(2);
  const projects = await api.projects();
  const names = projects.projects || [];
  $('project-list').replaceChildren(...names.map(name => Object.assign(document.createElement('option'), {value: name})));
  renderProjects(names);
}

async function updateSuggestion() {
  const project = $('project').value.trim();
  if (!project) return;
  const j = await api.suggestFolder(project);
  if (!j.error && (!$('folder').value || $('folder').value === suggestedFolder)) {
    suggestedFolder = j.folder;
    $('folder').value = j.folder;
  }
}

function stored(path) {
  state.currentPath = path;
  $('stored-path').textContent = 'Uloženo jako ' + path + '.';
  go(3);
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

export function initWizard(loadLibrary) {
  window.addEventListener('transcriber:library-changed', loadLibrary);
  api.asrStatus().then(setBadge).catch(() => setBadge({available: false, label: 'Lokální model'}));
  api.projects().then(({projects = []}) => renderProjects(projects));
  $('start').onclick = async () => {
    $('start').disabled = true;
    const j = await api.startRecording();
    if (j.error) { $('start').disabled = false; alert('Chyba: ' + j.error); return; }
    startedAt = Date.now();
    tick();
    timerId = setInterval(tick, 1000);
    $('recording-live').hidden = false;
  };
  $('to-project').onclick = () => prepareProjectStep('recording');
  $('ytfetch').onclick = () => {
    if (!$('yturl').value.trim()) return;
    prepareProjectStep('youtube');
  };
  $('project').addEventListener('change', updateSuggestion);
  $('project').addEventListener('blur', updateSuggestion);
  $('back-record').onclick = () => go(1);

  $('stop').onclick = async () => {
    $('stop').disabled = true;
    const j = await api.stopRecording($('folder').value, $('filename').value);
    $('stop').disabled = false;
    if (j.error) { alert('Chyba: ' + j.error); return; }
    clearInterval(timerId);
    stored(j.path);
  };
  $('save-youtube').onclick = async () => {
    $('save-youtube').disabled = true;
    const j = await api.fetchYoutube($('yturl').value.trim(), $('folder').value, $('filename').value);
    $('save-youtube').disabled = false;
    if (j.error) { alert('Chyba: ' + j.error); return; }
    if (j.title && !$('filename').value) $('filename').value = j.title;
    stored(j.path);
  };
  $('transcribe').onclick = async () => {
    if (!state.currentPath) return;
    await transcribePath(state.currentPath, result => {
      state.transcriptPath = result.saved_path;
      setBadge({available: result.backend === 'spark', label: result.backend === 'spark' ? 'Pracovní Spark' : 'Lokální model'});
      loadLibrary();
      go(4);
    });
  };
  $('summarize').onclick = async () => {
    $('summarize').disabled = true;
    $('summary-status').textContent = 'Připravuji souhrn…';
    const j = await api.summarize(state.transcriptPath, $('project').value);
    $('summarize').disabled = false;
    if (j.error) { $('summary-status').textContent = 'Chyba: ' + j.error; return; }
    $('output-title').textContent = 'Souhrn';
    $('out').value = j.summary;
    $('dl').href = api.downloadUrl(j.path);
    $('summary-status').textContent = 'Souhrn je uložený.';
    loadLibrary();
    api.projects().then(({projects = []}) => renderProjects(projects));
  };
  $('new-recording').onclick = () => window.location.reload();
}
