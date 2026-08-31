import {$} from './dom.js';
import {createFolder, loadLibrary} from './library.js';
import {state} from './state.js';
import {initWizard} from './wizard.js';

// Host-side helper that opens the library folder in an editor.
const OPENER = 'http://127.0.0.1:47833';

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

initWizard(loadLibrary);
loadLibrary();
