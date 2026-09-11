import {$} from './dom.js';
import {initCustomizations, loadCustomizations} from './customizations.js';
import {createFolder, initLibrary, loadDashboard, loadLibrary} from './library.js';
import {closeMeeting, hasOpenMeeting, openMeeting} from './meeting.js';
import {initSettings, loadSettings} from './settings.js';
import {state} from './state.js';
import {initViews} from './views.js';
import {initWizard, prepareNewRecording} from './wizard.js';

$('mkdir').onclick = createFolder;
$('openhere').onclick = () => {
  alert('This build cannot open the folder on your computer\'s file system automatically. Browse to your SIT library folder yourself to view these files on disk.');
};

$('workspace-view-mode').onclick = () => {
  const workspace = state.workspace;
  if (!workspace?.transcriptPath) return;
  openMeeting(workspace.transcriptPath, workspace.path, workspace.name);
};

initLibrary();
initCustomizations();
loadCustomizations();
initSettings();
initWizard();
initViews(view => {
  if (view === 'new') prepareNewRecording();
  if (view === 'dashboard') loadDashboard();
  if (view === 'library') loadLibrary();
  if (view === 'settings') loadSettings();
  if (view === 'workspace' && !state.workspace) location.hash = '#new';
  if (view === 'customizations') loadCustomizations();
  // Reloading on #meeting, or arriving with the back button, has no meeting
  // loaded and a cleared body, so the user would face an empty shell.
  if (view === 'meeting' && !hasOpenMeeting()) location.hash = '#archive';
  if (view !== 'meeting') closeMeeting();
});

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');
