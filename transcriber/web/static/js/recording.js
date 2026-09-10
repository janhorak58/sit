// Single source of truth for "is a recording running right now", plus the
// near-real-time draft transcription session that runs alongside it. One
// poller drives the sidebar indicator (visible in every view) and the
// recording/draft panel inside the workspace, so none of them can disagree.

import * as api from './api.js';
import {$} from './dom.js';

const ACTIVE_INTERVAL = 250;
const IDLE_INTERVAL = 5000;

function defaultLive() {
  return {session_id: null, state: 'idle', segments: [], audio_seconds: 0, processed_seconds: 0, error: null, language: ''};
}

export const recording = {active: false, startedAt: null, maxSeconds: null, available: false, levels: null, live: defaultLive()};

// The meter is the user's proof that the selected microphone is being heard.
const WAVE_BARS = 28;
const SILENT_PEAK = 0.004;
const SILENT_SECONDS = 4;
let quietSince = null;

function renderWave() {
  const wave = $('record-wave');
  const note = $('record-meter-note');
  if (!wave) return;
  if (wave.children.length !== WAVE_BARS) {
    wave.replaceChildren(...Array.from({length: WAVE_BARS}, () => document.createElement('i')));
  }
  const bars = recording.levels?.bars || [];
  [...wave.children].forEach((bar, index) => {
    const level = Number(bars[index]) || 0;
    // sqrt keeps quiet speech visible without letting peaks saturate.
    bar.style.height = Math.max(6, Math.round(Math.sqrt(level) * 100)) + '%';
  });
  if (!recording.active) { quietSince = null; return; }
  const peak = Number(recording.levels?.peak) || 0;
  if (peak > SILENT_PEAK) quietSince = null;
  else if (quietSince === null) quietSince = Date.now();
  const quietFor = quietSince === null ? 0 : (Date.now() - quietSince) / 1000;
  wave.classList.toggle('silent', quietFor > SILENT_SECONDS);
  note.textContent = quietFor > SILENT_SECONDS
    ? 'No sound is reaching the recording — check the selected microphone.'
    : 'Microphone and system audio are being recorded.';
}

let timerId = null;
// Requests can resolve out of order (slow poll from a session that has since
// been cancelled/restarted). `token` is bumped per dispatch, `appliedToken`
// tracks the newest response actually applied; anything older is dropped.
let token = 0;
let appliedToken = 0;

function clock(seconds) {
  const safe = Math.max(0, Math.floor(seconds));
  return String(Math.floor(safe / 60)).padStart(2, '0') + ':' + String(Math.floor(safe % 60)).padStart(2, '0');
}

export function elapsedSeconds() {
  if (!recording.startedAt) return 0;
  const seconds = Math.floor((Date.now() - recording.startedAt) / 1000);
  return recording.maxSeconds ? Math.min(seconds, recording.maxSeconds) : seconds;
}

/** Is there any session-in-progress state worth fast-polling and showing? */
function isLive() {
  return recording.active || recording.available || recording.live.state === 'listening' || recording.live.state === 'transcribing';
}

function render() {
  const indicator = $('recording-indicator');
  indicator.hidden = !recording.active;
  if (recording.active) $('recording-indicator-timer').textContent = clock(elapsedSeconds());
  renderWave();
}

function publish() {
  render();
  window.dispatchEvent(new CustomEvent('transcriber:recording-changed', {detail: {...recording, live: {...recording.live}}}));
}

function normalizeLive(live) {
  return {
    session_id: live?.session_id ?? null,
    state: live?.state || 'idle',
    segments: Array.isArray(live?.segments) ? live.segments : [],
    audio_seconds: Number(live?.audio_seconds) || 0,
    processed_seconds: Number(live?.processed_seconds) || 0,
    error: live?.error ?? null,
    language: live?.language ?? '',
  };
}

/**
 * Reflect a locally known transition immediately, without waiting for a poll.
 * Used for start/cancel/stop: each one ends whatever session came before, so
 * the draft is reset and any in-flight status response for the old session
 * is invalidated (it can never resurrect stale text after this call).
 */
export function setRecording(active, startedAt = Date.now()) {
  recording.active = active;
  recording.startedAt = active ? startedAt : null;
  recording.available = false;
  recording.levels = null;
  recording.live = defaultLive();
  appliedToken = token;
  publish();
  schedule();
}

let polling = false;

async function poll() {
  if (polling) return;
  polling = true;
  const myToken = ++token;
  try {
    const status = await api.recordingStatus();
    if (myToken <= appliedToken || status.error) return;
    appliedToken = myToken;
    recording.active = Boolean(status.recording);
    recording.startedAt = status.recording ? status.started_at : null;
    recording.maxSeconds = status.max_seconds ?? recording.maxSeconds;
    recording.available = Boolean(status.available);
    recording.levels = status.levels || null;
    recording.live = normalizeLive(status.live);
    publish();
  } finally {
    polling = false;
  }
}

/**
 * Serialized poll loop: each cycle waits for the previous request (and any
 * network failure) to settle before scheduling the next one, so requests
 * never overlap and a transient error can never wedge the loop.
 */
let generation = 0;

function schedule() {
  clearTimeout(timerId);
  const myGeneration = ++generation;
  const tick = async () => {
    if (myGeneration !== generation) return;
    try { await poll(); } catch { /* network hiccup: retry on next tick */ }
    if (myGeneration !== generation) return;
    timerId = setTimeout(tick, isLive() ? ACTIVE_INTERVAL : IDLE_INTERVAL);
  };
  timerId = setTimeout(tick, isLive() ? ACTIVE_INTERVAL : IDLE_INTERVAL);
}

export async function initRecording(onIndicatorClick) {
  $('recording-indicator').onclick = onIndicatorClick;
  try { await poll(); } catch { /* schedule() will retry initial connection failure */ }
  publish();
  schedule();
  // The tick keeps every visible timer/draft view moving between polls.
  setInterval(() => {
    render();
    if (isLive()) window.dispatchEvent(new Event('transcriber:recording-tick'));
  }, 1000);
}
