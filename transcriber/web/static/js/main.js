import {$} from './dom.js';
import {createFolder, initLibrary, loadDashboard, loadLibrary} from './library.js';
import {closeMeeting, openMeeting} from './meeting.js';
import {loadSettings} from './settings.js';
import {state} from './state.js';
import {initViews} from './views.js';
import {initWizard, prepareNewRecording} from './wizard.js';

const OPENER = 'http://127.0.0.1:47833';

$('mkdir').onclick = createFolder;
$('openhere').onclick = async () => {
  try {
    await fetch(OPENER + '/open', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder: state.currentFolder}),
    });
  } catch {
    alert('Nepodařilo se spojit s pomocníkem na hostu (transcriber-opener). Je spuštěný?');
  }
};

$('workspace-view-mode').onclick = () => {
  const workspace = state.workspace;
  if (!workspace?.transcriptPath) return;
  openMeeting(workspace.transcriptPath, workspace.path, workspace.name);
};

initLibrary();
initWizard();
initViews(view => {
  if (view === 'new') prepareNewRecording();
  if (view === 'dashboard') loadDashboard();
  if (view === 'library') loadLibrary();
  if (view === 'settings') loadSettings();
  if (view === 'workspace' && !state.workspace) location.hash = '#new';
  if (view !== 'meeting') closeMeeting();
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
