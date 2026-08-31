// Thin wrappers over the JSON API. Every call resolves to the parsed body;
// recoverable failures arrive as `{error: "..."}`.

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

async function get(url) {
  return (await fetch(url)).json();
}

export const startRecording = () => post('/start');
export const stopRecording = (folder, filename) => post('/stop', {folder, filename});
export const fetchYoutube = (url, folder, filename) => post('/youtube', {url, folder, filename});
export const transcribe = (path, language, num_speakers) =>
  post('/transcribe', {path, language, num_speakers});
export const getProgress = () => get('/progress');

export const browse = folder => get('/library/browse?path=' + encodeURIComponent(folder));
export const readFile = path => get('/library/file?path=' + encodeURIComponent(path));
export const mkdir = folder => post('/library/mkdir', {folder});
export const remove = path => post('/library/delete', {path});
export const move = (folder, name, to_folder, to_name, wav_path) =>
  post('/library/move', {folder, name, to_folder, to_name, wav_path});

export const downloadUrl = path => '/download?path=' + encodeURIComponent(path);
