// Thin wrappers over the JSON API. Every call resolves to the parsed body;
// recoverable failures arrive as `{error: "..."}`.

async function body(r) {
  const j = await r.json().catch(() => ({}));
  if (!r.ok) return {error: j.error || j.detail || r.status + ' ' + r.statusText};
  return j;
}

async function post(url, data) {
  return body(await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data || {}),
  }));
}

async function get(url) {
  return body(await fetch(url));
}

export const startRecording = (language, live, microphone = '') => post('/start', {language, live, microphone});
export const recordingStatus = () => get('/recording/status');
export const recordingDevices = () => get('/recording/devices');
export const stopRecording = (folder, filename) => post('/stop', {folder, filename});
export const cancelRecording = () => post('/recording/cancel');
export const uploadAudio = async (file, folder, filename) => {
  const q = new URLSearchParams({folder, filename, ext: file.name.split('.').pop() || ''});
  return body(await fetch('/upload?' + q, {method: 'POST', body: file}));
};
export const transcribe = (path, language, num_speakers) =>
  post('/transcribe', {path, language, num_speakers});
export const diarize = (path, num_speakers) => post('/diarize', {path, num_speakers});
export const getProgress = () => get('/progress');
export const asrStatus = () => get('/asr/status');
export const diagnostics = () => get('/asr/diagnostics');
export const projects = () => get('/projects');
export const suggestFolder = project => post('/projects/suggest-folder', {project});
export const summarize = (path, project, preferences) => post('/summaries', {path, project, preferences});
export const preferences = () => get('/preferences');
export const savePreferences = async preferences => body(await fetch('/preferences', {
  method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({preferences}),
}));
export const connections = () => get('/connections');
export const saveConnections = async connections => body(await fetch('/connections', {
  method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({connections}),
}));
export const testSsh = connections => post('/connections/ssh-test', {connections});

export const browse = folder => get('/library/browse?path=' + encodeURIComponent(folder));
export const readMeeting = path => get('/library/meeting?path=' + encodeURIComponent(path));
export const readSummaryJson = path => get('/library/summary-json?path=' + encodeURIComponent(path));
export const updateTranscript = async (path, segments) => body(await fetch('/library/transcript', {
  method: 'PUT',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({path, segments}),
}));
export const renameSpeakers = async (path, names) => body(await fetch('/library/speakers', {
  method: 'PUT',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({path, names}),
}));
export const mkdir = folder => post('/library/mkdir', {folder});
export const renameFolder = (path, name) => post('/library/folder/rename', {path, name});
export const deleteFolder = path => post('/library/folder/delete', {path});
export const remove = path => post('/library/delete', {path});
export const move = (folder, name, to_folder, to_name, wav_path) =>
  post('/library/move', {folder, name, to_folder, to_name, wav_path});

export const audioUrl = path => '/library/audio?path=' + encodeURIComponent(path);
