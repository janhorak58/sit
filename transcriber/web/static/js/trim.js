import * as api from './api.js';
import {$, el} from './dom.js';
import {state} from './state.js';

// Matches TRIM_MIN_SECONDS on the server.
const MIN_KEEP_SECONDS = 1;
// Moving the end handle cues the player this far before it, to hear the cut.
const END_PREVIEW_SECONDS = 3;

let duration = 0;
let loadedPath = null;
let hint = '';

const formatTime = seconds => {
  const s = Math.max(0, Math.floor(seconds || 0));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
};
const percent = seconds => (duration ? Math.min(100, Math.max(0, seconds / duration * 100)) : 0) + '%';
const bounds = () => [Number($('trim-start').value), Number($('trim-end').value)];
const setStatus = text => { $('trim-status').textContent = text; };

function render() {
  const [start, end] = bounds();
  $('trim-mask-start').style.width = percent(start);
  $('trim-mask-end').style.width = percent(duration - end);
  $('trim-start-label').textContent = formatTime(start);
  $('trim-end-label').textContent = formatTime(end);
  $('trim-kept-label').textContent = formatTime(end - start);
  $('trim-apply').disabled = !duration || (start <= 0 && end >= duration);
}

function setStart(seconds) {
  $('trim-start').value = Math.min(seconds, Number($('trim-end').value) - MIN_KEEP_SECONDS);
  render();
}

function setEnd(seconds) {
  $('trim-end').value = Math.max(seconds, Number($('trim-start').value) + MIN_KEEP_SECONDS);
  render();
}

function seek(seconds) {
  const audio = $('trim-audio');
  if (audio.src) audio.currentTime = Math.max(0, seconds);
}

async function load() {
  const path = state.currentPath;
  if (!path || path === loadedPath) return;
  loadedPath = path;
  duration = 0;
  render();
  setStatus('Reading the recording…');
  const result = await api.recordingLevels(path);
  if (path !== state.currentPath) return;
  if (result.error) {
    loadedPath = null;
    setStatus('Error: ' + result.error);
    return;
  }
  duration = result.duration;
  for (const input of [$('trim-start'), $('trim-end')]) input.max = duration;
  $('trim-start').value = 0;
  $('trim-end').value = duration;
  $('trim-bars').replaceChildren(...result.levels.map(level => el('span', {style: `height: ${Math.max(2, level * 100)}%`})));
  // Cache-busted: the same URL served the untrimmed file a moment ago.
  $('trim-audio').src = api.audioUrl(path) + '&v=' + Date.now();
  $('trim-playhead').style.left = '0%';
  render();
  setStatus(hint);
}

/** A different workspace or a fresh recording must not show the old take. */
export function resetTrim() {
  loadedPath = null;
  duration = 0;
  $('trim-panel').open = false;
  $('trim-audio').pause();
  $('trim-audio').removeAttribute('src');
  $('trim-bars').replaceChildren();
  render();
  setStatus(hint);
}

async function apply() {
  const path = state.currentPath;
  const [start, end] = bounds();
  if (!path) return;
  const follow = state.transcriptPath
    ? '\n\nTranscript lines in the removed parts are deleted, the rest move to the new times.'
    : '';
  if (!confirm(`Keep ${formatTime(start)} to ${formatTime(end)} and permanently delete the rest of the recording?${follow}`)) return;
  $('trim-audio').pause();
  $('trim-apply').disabled = true;
  setStatus('Trimming…');
  const result = await api.trimRecording(path, start, end);
  if (result.error) {
    render();
    setStatus('Error: ' + result.error);
    return;
  }
  loadedPath = null;
  await load();
  setStatus(`Trimmed. The recording is now ${formatTime(result.duration)} long.`
    + (result.transcript_shifted ? ' Transcript times follow the cut. Any AI analysis is marked outdated.' : ''));
  window.dispatchEvent(new Event('transcriber:recording-trimmed'));
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

export function initTrim() {
  hint = $('trim-status').textContent;
  $('trim-panel').addEventListener('toggle', () => { if ($('trim-panel').open) load(); });
  $('trim-start').addEventListener('input', () => setStart(Number($('trim-start').value)));
  $('trim-end').addEventListener('input', () => setEnd(Number($('trim-end').value)));
  $('trim-start').addEventListener('change', () => seek(Number($('trim-start').value)));
  $('trim-end').addEventListener('change', () => seek(Number($('trim-end').value) - END_PREVIEW_SECONDS));
  $('trim-set-start').onclick = () => setStart($('trim-audio').currentTime);
  $('trim-set-end').onclick = () => setEnd($('trim-audio').currentTime);
  $('trim-apply').onclick = apply;
  $('trim-wave').addEventListener('click', event => {
    if (!duration || event.target.classList.contains('trim-handle')) return;
    const box = $('trim-wave').getBoundingClientRect();
    seek((event.clientX - box.left) / box.width * duration);
    $('trim-audio').play().catch(() => {});
  });
  $('trim-audio').addEventListener('timeupdate', () => {
    $('trim-playhead').style.left = percent($('trim-audio').currentTime);
  });
  render();
}
