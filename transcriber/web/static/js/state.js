// Shared UI state: the recording selected for transcription and the folder
// currently open in the library browser.
export const state = {
  currentPath: null,
  transcriptPath: null,
  currentFolder: '',
};

export const childPath = name =>
  (state.currentFolder ? state.currentFolder + '/' : '') + name;
