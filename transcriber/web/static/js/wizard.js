import * as api from './api.js';
import {$, el} from './dom.js';
import {elapsedSeconds, initRecording, recording, setRecording} from './recording.js';
import {setWorkspace, state, updateWorkspacePaths, workspaceFromPath} from './state.js';
import {transcribePath, watchProgress} from './transcribe.js';

const LIVE_TOGGLE_KEY = 'shit.live-enabled';
function loadLiveToggle() {
  return localStorage.getItem(LIVE_TOGGLE_KEY) !== '0';
}
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
  const pending = recording.active || recording.available;
  const hideStarters = hasRecording || pending;
  $('source-locked').hidden = !hasRecording || pending;
  $('source-choices').hidden = hideStarters;
  $('source-status').hidden = hideStarters;
  $('record-lang-field').hidden = hideStarters;
  $('live-toggle-field').hidden = hideStarters;
}

function setTranscriptActions(hasTranscript) {
  $('diarize').disabled = !hasTranscript;
  $('to-summary').disabled = !hasTranscript;
  $('workspace-view-mode').disabled = !hasTranscript;
  $('workspace-view-mode').title = hasTranscript ? 'Open the finished transcript' : 'Viewing will be available after transcription';
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
  const date = now.toLocaleDateString('en-US', {day: 'numeric', month: 'numeric', year: 'numeric'});
  const time = now.toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit'});
  return `Recording ${date} ${time}`;
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
    ...projects.map(name => projectTile(name, name, 'Existing project')),
    projectTile(NEW_PROJECT, '＋ New project', 'Creates a new folder'),
  );
  const selected = $('project').value;
  if (selected && projects.includes(selected)) selectProject(selected);
  $('sidebar-projects').replaceChildren(...(projects.length
    ? projects.map(name => {
      const link = Object.assign(document.createElement('a'), {href: '#library', textContent: name});
      link.onclick = event => { event.preventDefault(); openLibrary(name); };
      return link;
    })
    : [Object.assign(document.createElement('span'), {className: 'side-empty', textContent: 'No projects yet'})]));
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
  $('workspace-path').textContent = workspace.folder || 'Library root';
  $('stored-path').textContent = workspace.path ? `Saved as ${workspace.path}.` : 'The recording will be saved into this folder.';
  $('transcribe').textContent = workspace.transcriptPath ? 'Restart transcription →' : 'Start transcription →';
  $('summarize').textContent = 'Create / regenerate summary';
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
  $('source-status').textContent = 'Ready to receive a recording.';
  $('status').textContent = 'Ready to transcribe.';
  $('summary-status').textContent = '';
  $('fill').style.width = '0%';
  $('record-lang').value = 'cs';
  $('record-lang').disabled = false;
  $('live-toggle').checked = loadLiveToggle();
  $('live-toggle').disabled = false;
  setSourceState(false);
  setTranscriptActions(false);
  go(1);
}

function stored(path) {
  updateWorkspacePaths(path, null);
  setSourceState(true);
  setTranscriptActions(false);
  $('stored-path').textContent = `Saved as ${path}.`;
  unlockThrough(2);
  go(2);
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

const LIVE_STATE_LABEL = {
  idle: 'Ready to listen.',
  listening: 'Listening…',
  transcribing: 'Transcribing the recorded segment…',
  error: 'Live transcription failed.',
  stopped: 'Recording finished.',
};

const EMPTY_DRAFT_HINT = 'Listening… the first transcript will appear in a few seconds.';

function isNearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 48;
}

function sameSeg(a, b) {
  return a.start === b.start && a.end === b.end && a.text === b.text;
}

function segRow(seg) {
  return el('div', {className: 'segrow'}, [
    el('span', {className: 'segtime', textContent: formatTime(seg.start)}),
    el('span', {className: 'segtext', textContent: seg.text}),
  ]);
}

let renderedDraft = [];

/**
 * Patches the transcript DOM instead of rebuilding it every tick: unchanged
 * content is a no-op, a growing tail patches the last row and appends new
 * ones, and only a genuinely different history (new/reset session) rebuilds.
 */
function renderDraftSegments(segments, provisionalLast) {
  const wrap = $('live-draft-segments');
  const unchanged = segments === renderedDraft || (
    segments.length === renderedDraft.length && segments.every((seg, index) => sameSeg(seg, renderedDraft[index]))
  );
  if (unchanged && wrap.childElementCount > 0) {
    if (wrap.lastElementChild?.classList.contains('segrow')) wrap.lastElementChild.classList.toggle('is-provisional', Boolean(provisionalLast));
    return;
  }
  const stick = isNearBottom(wrap);
  const prevLen = renderedDraft.length;
  const sameHead = prevLen > 0 && segments.length >= prevLen
    && segments.slice(0, prevLen - 1).every((seg, index) => sameSeg(seg, renderedDraft[index]));
  renderedDraft = segments;
  if (!segments.length) {
    wrap.replaceChildren(el('div', {className: 'empty', textContent: EMPTY_DRAFT_HINT}));
  } else if (sameHead) {
    wrap.lastElementChild?.classList.remove('is-provisional');
    const lastRow = wrap.children[prevLen - 1];
    if (lastRow) {
      lastRow.querySelector('.segtime').textContent = formatTime(segments[prevLen - 1].start);
      lastRow.querySelector('.segtext').textContent = segments[prevLen - 1].text;
    }
    for (let i = prevLen; i < segments.length; i++) wrap.appendChild(segRow(segments[i]));
  } else {
    wrap.replaceChildren(...segments.map(segRow));
  }
  if (segments.length) wrap.lastElementChild.classList.toggle('is-provisional', Boolean(provisionalLast));
  if (stick) wrap.scrollTop = wrap.scrollHeight;
  $('jump-latest').hidden = stick || !segments.length;
}

/** A saved session must not leak its draft or language into another workspace. */
function syncLiveDraft() {
  const live = recording.live;
  const visible = recording.active || recording.available;
  if (!visible) return;
  const provisional = ['listening', 'transcribing'].includes(live.state);
  let message = LIVE_STATE_LABEL[live.state] || '';
  const lag = Math.max(0, Math.round(live.audio_seconds - live.processed_seconds));
  if (provisional && lag > 5) message += ` (${lag}s behind)`;
  if (live.error) message += live.state === 'error' ? ` ${live.error}` : ' (temporary transcription error, continuing)';
  $('live-draft-status').textContent = message;
  $('record-session').classList.toggle('is-error', live.state === 'error');
  renderDraftSegments(live.segments, provisional);
  $('record-transcript').hidden = !live.session_id;
  if (live.session_id) {
    $('record-lang').value = live.language;
    $('lang').value = live.language;
  }
}

function syncRecordingPanel() {
  const session = $('record-session');
  const pending = recording.active || recording.available;
  const limitReached = recording.maxSeconds && recording.active && elapsedSeconds() >= recording.maxSeconds;
  $('step-panel-1').querySelector('.stage-copy').hidden = pending;
  session.hidden = !pending;
  session.classList.toggle('is-waiting', pending && !recording.active);
  setSourceState(Boolean(state.workspace?.path));
  $('start').disabled = pending;
  $('record-lang').disabled = pending;
  $('live-toggle').disabled = pending;
  if (pending) {
    $('record-toolbar-label').textContent = !recording.active
      ? 'Recording finished, waiting to save'
      : limitReached ? 'Recording finished (limit reached)' : 'Recording in progress';
    const seconds = recording.active ? elapsedSeconds() : recording.live.audio_seconds;
    $('timer').textContent = formatTime(seconds);
    syncLiveDraft();
  }
}

function transcriptionDone(result) {
  updateWorkspacePaths(state.currentPath, result.saved_path);
  setBadge({available: result.backend === 'spark', label: result.backend === 'spark' ? 'Remote Spark' : 'Local model'});
  unlockThrough(3);
  setTranscriptActions(true);
  go(result.operation === 'diarize' ? 2 : 3);
  $('transcribe').textContent = 'Restart transcription →';
  $('summarize').textContent = 'Create / regenerate summary';
  window.dispatchEvent(new Event('transcriber:library-changed'));
}


/** Entering "New recording": fresh default name, last-used project preselected. */
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
  $('live-toggle').checked = loadLiveToggle();
  api.asrStatus().then(setBadge).catch(() => setBadge({available: false, label: 'Local model'}));
  refreshProjects();
  applyWorkspace();
  $('live-draft-segments').addEventListener('scroll', () => {
    const wrap = $('live-draft-segments');
    $('jump-latest').hidden = $('record-session').hidden || isNearBottom(wrap) || !wrap.querySelector('.segrow');
  });
  $('jump-latest').onclick = () => {
    const wrap = $('live-draft-segments');
    wrap.scrollTop = wrap.scrollHeight;
    $('jump-latest').hidden = true;
  };

  $('new-project').addEventListener('input', updateSuggestion);
  $('filename').addEventListener('input', updateSuggestion);
  $('create-meeting').onsubmit = async event => {
    event.preventDefault();
    await updateSuggestion();
    const project = chosenProject();
    const name = $('filename').value.trim();
    const folder = suggestedFolder;
    if (!project) { alert('Pick a project, or create a new one.'); return; }
    if (!name || !folder) return;
    $('create-workspace').disabled = true;
    const existing = await api.browse(folder);
    if (!existing.error) {
      $('create-workspace').disabled = false;
      alert('A recording with this name already exists in the project. Choose a different name, or open it in the library.');
      return;
    }
    const result = await api.mkdir(folder);
    $('create-workspace').disabled = false;
    if (result.error) { alert('Error: ' + result.error); return; }
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
    const result = await api.startRecording($('record-lang').value, $('live-toggle').checked);
    if (result.error) { $('start').disabled = false; alert('Error: ' + result.error); return; }
    setRecording(true);
  };
  $('live-toggle').onchange = () => localStorage.setItem(LIVE_TOGGLE_KEY, $('live-toggle').checked ? '1' : '0');
  $('cancel-record').onclick = async () => {
    if (!confirm('Really cancel the recording? The take will not be kept.')) return;
    $('cancel-record').disabled = true;
    const result = await api.cancelRecording();
    $('cancel-record').disabled = false;
    if (result.error) { alert('Error: ' + result.error); return; }
    setRecording(false);
  };
  $('stop').onclick = async () => {
    if (!state.workspace) return;
    $('stop').disabled = true;
    const result = await api.stopRecording(state.workspace.folder, state.workspace.name);
    $('stop').disabled = false;
    if (result.error) { alert('Error: ' + result.error); return; }
    // The live session's chosen language carries over into the automatic
    // full pass, even though setRecording(false) below resets the draft.
    const language = recording.live.session_id ? recording.live.language : $('record-lang').value;
    setRecording(false);
    stored(result.path);
    $('lang').value = language;
    transcribePath(result.path);
  };
  $('audiofile').onchange = async () => {
    const file = $('audiofile').files[0];
    if (!file || !state.workspace) return;
    $('audiofile').disabled = true;
    $('source-status').textContent = 'Uploading and converting the file…';
    const result = await api.uploadAudio(file, state.workspace.folder, state.workspace.name);
    $('audiofile').disabled = false;
    if (result.error) { $('source-status').textContent = 'Error: ' + result.error; return; }
    stored(result.path);
  };
  $('transcribe').onclick = () => {
    if (!state.currentPath) return;
    if (state.transcriptPath && !confirm('The existing transcript and speaker recognition will be replaced. Continue?')) return;
    transcribePath(state.currentPath);
  };
  $('diarize').onclick = async () => {
    if (!state.currentPath || !state.transcriptPath) return;
    if (!confirm('The existing speaker assignments and names will be replaced. Continue?')) return;
    const n = parseInt($('numspeakers').value, 10);
    const started = await api.diarize(state.currentPath, Number.isInteger(n) ? n : null);
    if (started.error) { $('status').textContent = 'Error: ' + started.error; return; }
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
    $('summary-status').textContent = 'Preparing summary…';
    const result = await api.summarize(state.transcriptPath, state.workspace?.project || '');
    $('summarize').disabled = false;
    if (result.error) { $('summary-status').textContent = 'Error: ' + result.error; return; }
    $('summary-status').textContent = 'Summary restored. Open it in View mode.';
    $('summarize').textContent = 'Create / regenerate summary';
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
    if ((recording.active || recording.available) && state.workspace) {
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
