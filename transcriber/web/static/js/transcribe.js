import * as api from './api.js';
import {$, status} from './dom.js';

const POLL_MS = 1000;
let pollTimer = null;


function setBusy(busy) {
  $('transcribe').disabled = busy;
  $('bar').style.display = 'block';
}

export function watchProgress(onDone) {
  clearTimeout(pollTimer);
  setBusy(true);
  const poll = async () => {
    try {
      const progress = await api.getProgress();
      if (progress.error) throw new Error(progress.error);
      $('fill').style.width = (progress.percent || 0) + '%';
      status(progress.message || 'Transcribing…');
      if (progress.stage === 'done') {
        setBusy(false);
        if (progress.warning) status('Warning: ' + progress.warning);
        window.dispatchEvent(new CustomEvent('transcriber:transcribe-done', {detail: progress}));
        if (onDone) onDone(progress);
        return;
      }
      if (progress.stage === 'error') {
        setBusy(false);
        status('Error: ' + progress.error);
        return;
      }
      pollTimer = setTimeout(poll, POLL_MS);
    } catch (error) {
      setBusy(false);
      status('Error: ' + error.message);
    }
  };
  poll();
}

export async function transcribePath(path, onDone) {
  setBusy(true);
  try {
    const n = parseInt($('numspeakers').value, 10);
    const started = await api.transcribe(path, $('lang').value, Number.isInteger(n) ? n : null);
    if (started.error) throw new Error(started.error);
    watchProgress(onDone);
  } catch (error) {
    setBusy(false);
    status('Error: ' + error.message);
  }
}

export function openRecordingWorkspace(path, title, hasTranscript = false) {
  window.dispatchEvent(new CustomEvent('transcriber:transcribe-start', {
    detail: {path, title, hasTranscript},
  }));
}
