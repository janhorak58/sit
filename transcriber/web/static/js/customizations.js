import * as api from './api.js';
import {$} from './dom.js';

let current = null;
export const getPreferences = () => current;
const LOCAL_KEY = 'sit.customizations';
const FALLBACK = {
  profile: 'meeting',
  recording: {default_project: '', language: 'cs', microphone: '', live_enabled: true, live_chunk_seconds: '10', speaker_count: '', auto_diarize: true},
  transcript: {show_timestamps: true, show_speakers: true, paragraph_size: 'normal', default_view: 'both'},
  brief: {detail: 'standard', language: 'same', compare_previous: true, user_prompt: '', sections: {chapters: true, decisions: true, action_items: true, open_questions: true, risks: true, speaker_contributions: true, follow_up: true}},
  privacy: {processing: 'auto', clear_live_drafts: true},
  export: {format: 'markdown', include_audio_links: false},
};

function localPreferences() {
  try { return {...FALLBACK, ...JSON.parse(localStorage.getItem(LOCAL_KEY))}; }
  catch { return structuredClone(FALLBACK); }
}

const profiles = {
  meeting: {language: 'cs', speakers: '', detail: 'standard', compare: true},
  interview: {language: 'cs', speakers: '2', detail: 'detailed', compare: false},
  lecture: {language: 'cs', speakers: '1', detail: 'detailed', compare: false},
  notes: {language: 'cs', speakers: '1', detail: 'short', compare: false},
};

function at(object, path, value) {
  const keys = path.split('.');
  const leaf = keys.pop();
  const target = keys.reduce((node, key) => node[key], object);
  target[leaf] = value;
}

function readForm() {
  const next = structuredClone(current);
  document.querySelectorAll('#customizations-form [data-path]').forEach(field => {
    const value = field.type === 'checkbox' ? field.checked : field.value;
    at(next, field.dataset.path, value);
  });
  return next;
}

function fillForm(data) {
  document.querySelectorAll('#customizations-form [data-path]').forEach(field => {
    const value = field.dataset.path.split('.').reduce((node, key) => node?.[key], data);
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value ?? '';
  });
}

function applyProfile() {
  const profile = profiles[$('custom-profile').value];
  if (!profile) return;
  $('custom-language').value = profile.language;
  $('custom-speakers').value = profile.speakers;
  $('custom-brief-detail').value = profile.detail;
  $('custom-compare').checked = profile.compare;
}

function fillMicrophones(result, selected = '') {
  const select = $('custom-microphone');
  select.replaceChildren(new Option('System default', ''));
  (result.microphones || []).forEach(device => {
    const suffix = device.default ? ' — system default' : '';
    select.append(new Option(device.label + suffix, device.id));
  });
  if (selected && ![...select.options].some(option => option.value === selected)) {
    select.append(new Option('Previously selected microphone (not connected)', selected));
  }
}

export async function loadCustomizations() {
  const [result, devices] = await Promise.all([api.preferences(), api.recordingDevices()]);
  current = result.error ? localPreferences() : result;
  fillMicrophones(devices.error ? {} : devices, current.recording.microphone);
  fillForm(current);
  $('customization-status').textContent = result.error ? 'Saved locally until the backend is restarted.' : '';
  window.dispatchEvent(new CustomEvent('transcriber:preferences-changed', {detail: current}));
}

export function initCustomizations() {
  $('custom-profile').onchange = applyProfile;
  $('customizations-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('customizations-save');
    button.disabled = true;
    $('customization-status').textContent = 'Saving…';
    const next = readForm();
    const result = await api.savePreferences(next);
    button.disabled = false;
    current = result.error ? next : result;
    localStorage.setItem(LOCAL_KEY, JSON.stringify(current));
    fillForm(current);
    $('customization-status').textContent = result.error
      ? 'Saved locally. Restart SIT to persist settings for AI briefs.'
      : 'Saved. New recordings and briefs use these choices.';
    window.dispatchEvent(new CustomEvent('transcriber:preferences-changed', {detail: current}));
  };
}
