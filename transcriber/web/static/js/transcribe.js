import * as api from './api.js';
import {$, status} from './dom.js';

const POLL_MS = 1000;

function showTranscript(text, savedPath) {
  $('output-title').textContent = 'Přepis';
  $('out').value = text;
  $('dl').style.display = 'inline';
  $('dl').href = api.downloadUrl(savedPath);
}

// Kick off a transcription and poll /progress until it settles.
export async function transcribePath(path, onDone) {
  $('transcribe').disabled = true;
  $('bar').style.display = 'block';
  const n = parseInt($('numspeakers').value, 10);
  await api.transcribe(path, $('lang').value, Number.isInteger(n) ? n : null);

  const poll = setInterval(async () => {
    const j = await api.getProgress();
    $('fill').style.width = j.percent + '%';
    status(j.message);
    if (j.stage === 'done') {
      clearInterval(poll);
      showTranscript(j.text, j.saved_path);
      $('transcribe').disabled = false;
      if (onDone) onDone(j);
    } else if (j.stage === 'error') {
      clearInterval(poll);
      status('Chyba: ' + j.error);
      $('transcribe').disabled = false;
    }
  }, POLL_MS);
}
