const WORKSPACE_KEY = 'transcriber.workspace';

function loadWorkspace() {
  try { return JSON.parse(localStorage.getItem(WORKSPACE_KEY)) || null; }
  catch { return null; }
}

const restored = loadWorkspace();

export const state = {
  currentPath: restored?.path || null,
  transcriptPath: restored?.transcriptPath || null,
  currentFolder: '',
  workspace: restored,
};

export function setWorkspace(workspace) {
  state.workspace = {
    project: workspace.project || '',
    folder: workspace.folder || '',
    name: workspace.name || 'Meeting',
    path: workspace.path || null,
    transcriptPath: workspace.transcriptPath || null,
  };
  state.currentPath = state.workspace.path;
  state.transcriptPath = state.workspace.transcriptPath;
  state.currentFolder = state.workspace.folder;
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(state.workspace));
  return state.workspace;
}

export function updateWorkspacePaths(path, transcriptPath = state.transcriptPath) {
  if (!state.workspace) return;
  setWorkspace({...state.workspace, path, transcriptPath});
}

export function workspaceFromPath(path, title = '', hasTranscript = false) {
  const parts = String(path || '').split('/').filter(Boolean);
  const file = parts.pop() || '';
  if (parts[parts.length - 1] === 'audio') parts.pop();
  const folder = parts.join('/');
  const name = title || file.replace(/\.[^.]+$/, '') || 'Meeting';
  const transcriptPath = hasTranscript ? [folder, `${name}.txt`].filter(Boolean).join('/') : null;
  return setWorkspace({project: parts[0] || '', folder, name, path, transcriptPath});
}


const replacePathPrefix = (value, oldPath, newPath) => {
  if (!value || value !== oldPath && !value.startsWith(oldPath + '/')) return value;
  return newPath + value.slice(oldPath.length);
};

export function remapWorkspaceFolder(oldPath, newPath) {
  if (!state.workspace) return;
  const folder = replacePathPrefix(state.workspace.folder, oldPath, newPath);
  if (folder === state.workspace.folder) return;
  setWorkspace({
    ...state.workspace,
    project: folder.split('/')[0] || '',
    folder,
    path: replacePathPrefix(state.workspace.path, oldPath, newPath),
    transcriptPath: replacePathPrefix(state.workspace.transcriptPath, oldPath, newPath),
  });
}

export function clearWorkspaceWithin(path) {
  if (!state.workspace || state.workspace.folder !== path && !state.workspace.folder.startsWith(path + '/')) return;
  state.workspace = null;
  state.currentPath = null;
  state.transcriptPath = null;
  localStorage.removeItem(WORKSPACE_KEY);
}

export const childPath = name =>
  (state.currentFolder ? state.currentFolder + '/' : '') + name;
