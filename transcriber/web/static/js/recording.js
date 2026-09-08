// Single source of truth for "is a recording running right now".
// One poller drives both the sidebar indicator (visible in every view) and the
// recording panel inside the workspace, so the two can never disagree.

import * as api from './api.js';
import {$} from './dom.js';

const ACTIVE_INTERVAL = 1000;
const IDLE_INTERVAL = 5000;

export const recording = {active: false, startedAt: null, maxSeconds: null};

let timerId = null;

function clock(seconds) {
  const safe = Math.max(0, seconds);
  return String(Math.floor(safe / 60)).padStart(2, '0') + ':' + String(safe % 60).padStart(2, '0');
}

export function elapsedSeconds() {
  if (!recording.startedAt) return 0;
  const seconds = Math.floor((Date.now() - recording.startedAt) / 1000);
  return recording.maxSeconds ? Math.min(seconds, recording.maxSeconds) : seconds;
}

function render() {
  const indicator = $('recording-indicator');
  indicator.hidden = !recording.active;
  if (recording.active) $('recording-indicator-timer').textContent = clock(elapsedSeconds());
}

function publish() {
  render();
  window.dispatchEvent(new CustomEvent('transcriber:recording-changed', {detail: {...recording}}));
}

/** Reflect a locally known transition immediately, without waiting for a poll. */
export function setRecording(active, startedAt = Date.now()) {
  recording.active = active;
  recording.startedAt = active ? startedAt : null;
  publish();
  schedule();
}

async function poll() {
  const status = await api.recordingStatus();
  if (status.error) return;
  const changed = status.recording !== recording.active;
  recording.active = Boolean(status.recording);
  recording.startedAt = status.recording ? status.started_at : null;
  recording.maxSeconds = status.max_seconds ?? recording.maxSeconds;
  if (changed) {
    publish();
    schedule();
  } else render();
}

function schedule() {
  clearInterval(timerId);
  timerId = setInterval(async () => {
    await poll();
  }, recording.active ? ACTIVE_INTERVAL : IDLE_INTERVAL);
}

export async function initRecording(onIndicatorClick) {
  $('recording-indicator').onclick = onIndicatorClick;
  await poll();
  publish();
  schedule();
  // The tick keeps every visible timer moving between polls.
  setInterval(() => {
    render();
    if (recording.active) window.dispatchEvent(new Event('transcriber:recording-tick'));
  }, 1000);
}
