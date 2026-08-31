import * as api from './api.js';
import {$, status} from './dom.js';
import {createFolder, loadLibrary} from './library.js';
import {state} from './state.js';
import {transcribePath} from './transcribe.js';

// Host-side helper that opens the library folder in an editor.
const OPENER = 'http://127.0.0.1:47833';

const folder = () => $('folder').value;
const filename = () => $('filename').value;

function stored(path) {
  state.currentPath = path;
  status('Uloženo jako ' + path + ' (zatím nepřepsáno). Klikni na Přepsat.');
  $('transcribe').disabled = false;
  loadLibrary();
}

$('start').onclick = async () => {
  $('start').disabled = true;
  status('Nahrávám...');
  await api.startRecording();
  $('stop').disabled = false;
};

$('stop').onclick = async () => {
  $('stop').disabled = true;
  status('Ukládám nahrávku do knihovny...');
  const j = await api.stopRecording(folder(), filename());
  $('start').disabled = false;
  if (j.error) { status('Chyba: ' + j.error); return; }
  stored(j.path);
};

$('ytfetch').onclick = async () => {
  const url = $('yturl').value.trim();
  if (!url) return;
  $('ytfetch').disabled = true;
  status('Stahuji zvuk z YouTube...');
  const j = await api.fetchYoutube(url, folder(), filename());
  $('ytfetch').disabled = false;
  if (j.error) { status('Chyba: ' + j.error); return; }
  if (j.title && !filename()) $('filename').value = j.title;
  stored(j.path);
};

$('transcribe').onclick = () =>
  state.currentPath && transcribePath(state.currentPath, loadLibrary);

$('mkdir').onclick = createFolder;

$('openhere').onclick = async () => {
  try {
    await fetch(OPENER + '/open', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder: state.currentFolder}),
    });
  } catch (e) {
    alert('Nepodařilo se spojit s pomocníkem na hostu (transcriber-opener). Je spuštěný?');
  }
};

loadLibrary();
