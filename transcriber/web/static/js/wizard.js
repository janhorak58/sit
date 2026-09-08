import * as api from './api.js';
import {$, el} from './dom.js';
import {elapsedSeconds, initRecording, recording, setRecording} from './recording.js';
import {setWorkspace, state, updateWorkspacePaths, workspaceFromPath} from './state.js';
import {transcribePath, watchProgress} from './transcribe.js';

let suggestedFolder = '';
let unlockedStep = 1;
let suggestionRequest = 0;

function go(step) {
  if (step > unlockedStep) return;
  document.querySelectorAll('.stage').forEach(node => node.classList.toggle('active', node.dataset.stage === String(step)));
  document.querySelectorAll('.step').forEach(node => {
    const number = Number(node.dataset.step);
    const active = number === step;
    node.classList.toggle('active', active);
    node.classList.toggle('done', number < unlockedStep || number < step);
    node.disabled = number > unlockedStep;
    node.setAttribute('aria-selected', String(active));
  });
}

function unlockThrough(step) {
  unlockedStep = Math.max(unlockedStep, step);
}

function setSourceState(hasRecording) {
  $('source-locked').hidden = !hasRecording;
  $('source-choices').hidden = hasRecording;
  $('source-status').hidden = hasRecording;
}

function setTranscriptActions(hasTranscript) {
  $('diarize').disabled = !hasTranscript;
  $('to-summary').disabled = !hasTranscript;
  $('workspace-view-mode').disabled = !hasTranscript;
  $('workspace-view-mode').title = hasTranscript ? 'Otevřít hotový přepis' : 'Prohlížení bude dostupné po přepisu';
}

function setBadge(data) {
  const badge = $('asr-badge');
  badge.className = 'engine ' + (data.available ? 'remote' : 'local');
  badge.querySelector('b').textContent = data.label;
}

function openLibrary(folder) {
  state.currentFolder = folder || '';
  window.dispatchEvent(new CustomEvent('transcriber:open-folder', {detail: state.currentFolder}));
}

function renamedPath(path, name) {
  return path ? path.replace(/[^/]+$/, name + path.slice(path.lastIndexOf('.'))) : null;
}

function defaultRecordingName() {
  const now = new Date();
  const date = now.toLocaleDateString('cs-CZ', {day: 'numeric', month: 'numeric', year: 'numeric'});
  const time = now.toLocaleTimeString('cs-CZ', {hour: '2-digit', minute: '2-digit'});
  return `Nahrávka ${date} ${time}`;
}

const NEW_PROJECT = '__new__';

function selectProject(name) {
  $('project').value = name || '';
  const custom = name === NEW_PROJECT;
  $('new-project-field').hidden = !custom;
  document.querySelectorAll('#project-picker .project-tile').forEach(tile => {
    const selected = Boolean(name) && tile.dataset.project === name;
    tile.classList.toggle('selected', selected);
    tile.setAttribute('aria-pressed', String(selected));
  });
  if (custom) $('new-project').focus();
  updateSuggestion();
}

function projectTile(name, label, hint) {
  const tile = el('button', {className: 'project-tile', type: 'button'}, [
    el('b', {textContent: label}),
    el('small', {textContent: hint}),
  ]);
  tile.dataset.project = name;
  tile.setAttribute('aria-pressed', 'false');
  tile.onclick = () => selectProject(name);
  return tile;
}

function renderProjects(projects) {
  $('project-picker').replaceChildren(
    ...projects.map(name => projectTile(name, name, 'Existující projekt')),
    projectTile(NEW_PROJECT, '＋ Nový projekt', 'Založí novou složku'),
  );
  const selected = $('project').value;
  if (selected && projects.includes(selected)) selectProject(selected);
  $('sidebar-projects').replaceChildren(...(projects.length
    ? projects.map(name => {
      const link = Object.assign(document.createElement('a'), {href: '#library', textContent: name});
      link.onclick = event => { event.preventDefault(); openLibrary(name); };
      return link;
    })
    : [Object.assign(document.createElement('span'), {className: 'side-empty', textContent: 'Zatím bez projektů'})]));
}

async function refreshProjects() {
  const {projects = []} = await api.projects();
  renderProjects(projects);
}

function chosenProject() {
  const picked = $('project').value;
  return picked === NEW_PROJECT ? $('new-project').value.trim() : picked;
}

function safeSegment(value) {
  return value.trim().replace(/[\\/:*?"<>|]/g, '-').replace(/\s+/g, ' ');
}

async function updateSuggestion() {
  const request = ++suggestionRequest;
  const project = chosenProject();
  const name = safeSegment($('filename').value);
  if (!project) { $('folder-preview').textContent = '—'; return; }
  const result = await api.suggestFolder(project);
  if (request !== suggestionRequest || result.error) return;
  suggestedFolder = [result.folder, name].filter(Boolean).join('/');
  $('folder-preview').textContent = suggestedFolder || '—';
}

function applyWorkspace() {
  const workspace = state.workspace;
  if (!workspace) return false;
  $('workspace-title').textContent = workspace.name;
  $('workspace-path').textContent = workspace.folder || 'Kořen knihovny';
  $('stored-path').textContent = workspace.path ? `Uloženo jako ${workspace.path}.` : 'Nahrávka se uloží do této složky.';
  $('transcribe').textContent = workspace.transcriptPath ? 'Spustit přepis znovu →' : 'Spustit přepis →';
  $('summarize').textContent = 'Vytvořit / přegenerovat souhrn';
  setSourceState(Boolean(workspace.path));
  setTranscriptActions(Boolean(workspace.transcriptPath));
  unlockedStep = workspace.transcriptPath ? 3 : workspace.path ? 2 : 1;
  go(unlockedStep);
  return true;
}

function resetWorkspaceView() {
  unlockedStep = 1;
  $('audiofile').value = '';
  $('source-status').hidden = false;
  $('source-status').textContent = 'Připraveno přijmout nahrávku.';
  $('status').textContent = 'Připraveno k přepisu.';
  $('summary-status').textContent = '';
  $('fill').style.width = '0%';
  setSourceState(false);
  setTranscriptActions(false);
  go(1);
}

function stored(path) {
  updateWorkspacePaths(path, null);
  setSourceState(true);
  setTranscriptActions(false);
  $('stored-path').textContent = `Uloženo jako ${path}.`;
  unlockThrough(2);
  go(2);
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

function syncRecordingPanel() {
  const live = $('recording-live');
  const limitReached = recording.maxSeconds && elapsedSeconds() >= recording.maxSeconds;
  live.hidden = !recording.active;
  $('start').disabled = recording.active;
  if (!recording.active) return;
  live.querySelector('b').textContent = limitReached
    ? 'Nahrávání ukončeno (dosažen limit)'
    : 'Nahrávání běží';
  const seconds = elapsedSeconds();
  $('timer').textContent =
    String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
}

function transcriptionDone(result) {
  updateWorkspacePaths(state.currentPath, result.saved_path);
  setBadge({available: result.backend === 'spark', label: result.backend === 'spark' ? 'Pracovní Spark' : 'Lokální model'});
  unlockThrough(3);
  setTranscriptActions(true);
  go(result.operation === 'diarize' ? 2 : 3);
  $('transcribe').textContent = 'Spustit přepis znovu →';
  $('summarize').textContent = 'Vytvořit / přegenerovat souhrn';
  window.dispatchEvent(new Event('transcriber:library-changed'));
}


/** Entering "Nová nahrávka": fresh default name, last-used project preselected. */
export async function prepareNewRecording() {
  $('filename').value = defaultRecordingName();
  $('new-project').value = '';
  await refreshProjects();
  const last = state.workspace?.project;
  const tiles = [...document.querySelectorAll('#project-picker .project-tile')];
  const existing = tiles.filter(tile => tile.dataset.project !== NEW_PROJECT);
  // No projects yet: the only sensible target is a brand new one.
  if (!existing.length) { selectProject(NEW_PROJECT); return; }
  selectProject(existing.some(tile => tile.dataset.project === last) ? last : null);
}

export function initWizard() {
  document.querySelectorAll('.step').forEach(button => {
    button.onclick = () => go(Number(button.dataset.step));
  });
  api.asrStatus().then(setBadge).catch(() => setBadge({available: false, label: 'Lokální model'}));
  refreshProjects();
  applyWorkspace();

  $('new-project').addEventListener('input', updateSuggestion);
  $('filename').addEventListener('input', updateSuggestion);
  $('create-meeting').onsubmit = async event => {
    event.preventDefault();
    await updateSuggestion();
    const project = chosenProject();
    const name = $('filename').value.trim();
    const folder = suggestedFolder;
    if (!project) { alert('Vyber projekt, nebo založ nový.'); return; }
    if (!name || !folder) return;
    $('create-workspace').disabled = true;
    const existing = await api.browse(folder);
    if (!existing.error) {
      $('create-workspace').disabled = false;
      alert('Nahrávka s tímto názvem už v projektu je. Zvol jiný název, nebo ji otevři v knihovně.');
      return;
    }
    const result = await api.mkdir(folder);
    $('create-workspace').disabled = false;
    if (result.error) { alert('Chyba: ' + result.error); return; }
    setWorkspace({project, folder: result.folder || folder, name});
    resetWorkspaceView();
    $('workspace-title').textContent = name;
    $('workspace-path').textContent = result.folder || folder;
    location.hash = '#workspace';
    window.dispatchEvent(new Event('transcriber:library-changed'));
    refreshProjects();
  };

  $('workspace-rename').onclick = () => {
    const workspace = state.workspace;
    if (!workspace?.name) return;
    window.dispatchEvent(new CustomEvent('transcriber:rename-meeting', {
      detail: {
        folder: workspace.folder,
        name: workspace.name,
        wavPath: workspace.path,
        onSuccess: (_folder, name) => {
          setWorkspace({
            ...workspace,
            name,
            path: renamedPath(workspace.path, name),
            transcriptPath: renamedPath(workspace.transcriptPath, name),
          });
          $('workspace-title').textContent = name;
        },
      },
    }));
  };

  $('workspace-library').onclick = () => openLibrary(state.workspace?.folder);
  $('to-transcript').onclick = () => go(2);
  $('start').onclick = async () => {
    $('start').disabled = true;
    const result = await api.startRecording();
    if (result.error) { $('start').disabled = false; alert('Chyba: ' + result.error); return; }
    setRecording(true);
  };
  $('cancel-record').onclick = async () => {
    if (!confirm('Opravdu zrušit nahrávání? Záznam se nezachová.')) return;
    $('cancel-record').disabled = true;
    await api.cancelRecording();
    $('cancel-record').disabled = false;
    setRecording(false);
  };
  $('stop').onclick = async () => {
    if (!state.workspace) return;
    $('stop').disabled = true;
    const result = await api.stopRecording(state.workspace.folder, state.workspace.name);
    $('stop').disabled = false;
    if (result.error) { alert('Chyba: ' + result.error); return; }
    setRecording(false);
    stored(result.path);
  };
  $('audiofile').onchange = async () => {
    const file = $('audiofile').files[0];
    if (!file || !state.workspace) return;
    $('audiofile').disabled = true;
    $('source-status').textContent = 'Nahrávám a převádím soubor…';
    const result = await api.uploadAudio(file, state.workspace.folder, state.workspace.name);
    $('audiofile').disabled = false;
    if (result.error) { $('source-status').textContent = 'Chyba: ' + result.error; return; }
    stored(result.path);
  };
  $('transcribe').onclick = () => {
    if (!state.currentPath) return;
    if (state.transcriptPath && !confirm('Stávající přepis i rozpoznání mluvčích se nahradí. Pokračovat?')) return;
    transcribePath(state.currentPath);
  };
  $('diarize').onclick = async () => {
    if (!state.currentPath || !state.transcriptPath) return;
    if (!confirm('Dosavadní přiřazení a názvy mluvčích se nahradí. Pokračovat?')) return;
    const n = parseInt($('numspeakers').value, 10);
    const started = await api.diarize(state.currentPath, Number.isInteger(n) ? n : null);
    if (started.error) { $('status').textContent = 'Chyba: ' + started.error; return; }
    watchProgress();
  };
  $('to-summary').onclick = () => go(3);
  window.addEventListener('transcriber:transcribe-done', event => transcriptionDone(event.detail));
  window.addEventListener('transcriber:transcribe-start', async event => {
    const detail = typeof event.detail === 'string' ? {path: event.detail} : event.detail;
    workspaceFromPath(detail.path, detail.title, detail.hasTranscript);
    applyWorkspace();
    location.hash = '#workspace';
    go(2);
    const progress = await api.getProgress();
    if (
      ['transcribing', 'diarizing', 'merging'].includes(progress.stage)
      && progress.source_path === detail.path
    ) watchProgress();
  });
  $('summarize').onclick = async () => {
    if (!state.transcriptPath) return;
    $('summarize').disabled = true;
    $('summary-status').textContent = 'Připravuji souhrn…';
    const result = await api.summarize(state.transcriptPath, state.workspace?.project || '');
    $('summarize').disabled = false;
    if (result.error) { $('summary-status').textContent = 'Chyba: ' + result.error; return; }
    $('summary-status').textContent = 'Souhrn byl obnoven. Výsledek otevřeš v režimu Prohlížet.';
    $('summarize').textContent = 'Vytvořit / přegenerovat souhrn';
    window.dispatchEvent(new Event('transcriber:library-changed'));
  };
  window.addEventListener('transcriber:library-changed', refreshProjects);

  window.addEventListener('transcriber:recording-changed', syncRecordingPanel);
  window.addEventListener('transcriber:recording-tick', syncRecordingPanel);
  initRecording(() => {
    if (state.workspace) applyWorkspace();
    location.hash = '#workspace';
    go(1);
  }).then(() => {
    if (recording.active && state.workspace) {
      applyWorkspace();
      location.hash = '#workspace';
      unlockedStep = 1;
      go(1);
    }
    syncRecordingPanel();
  });
  api.getProgress().then(progress => {
    if (!['transcribing', 'diarizing', 'merging'].includes(progress.stage) || !progress.source_path) return;
    workspaceFromPath(progress.source_path);
    applyWorkspace();
    unlockThrough(2);
    location.hash = '#workspace';
    go(2);
    watchProgress();
  });
}
