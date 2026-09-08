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

export const startRecording = () => post('/start');
export const recordingStatus = () => get('/recording/status');
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
export const summarize = (path, project) => post('/summaries', {path, project});

export const browse = folder => get('/library/browse?path=' + encodeURIComponent(folder));
export const readMeeting = path => get('/library/meeting?path=' + encodeURIComponent(path));
export const readSummaryJson = path => get('/library/summary-json?path=' + encodeURIComponent(path));
export const renameSpeaker = (path, oldName, newName) =>
  post('/library/rename-speaker', {path, old: oldName, new: newName});
export const mkdir = folder => post('/library/mkdir', {folder});
export const renameFolder = (path, name) => post('/library/folder/rename', {path, name});
export const deleteFolder = path => post('/library/folder/delete', {path});
export const remove = path => post('/library/delete', {path});
export const move = (folder, name, to_folder, to_name, wav_path) =>
  post('/library/move', {folder, name, to_folder, to_name, wav_path});

export const audioUrl = path => '/library/audio?path=' + encodeURIComponent(path);
