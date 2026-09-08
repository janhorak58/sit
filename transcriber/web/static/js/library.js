import * as api from './api.js';
import {$, el} from './dom.js';
import {openMeeting} from './meeting.js';
import {childPath, clearWorkspaceWithin, remapWorkspaceFolder, state} from './state.js';
import {openRecordingWorkspace} from './transcribe.js';

const RECENT_KEY = 'transcriber.recent';
let listing = {subfolders: [], items: []};
let moveTarget = null;

function remember(item, path) {
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch {}
  recent = recent.filter(entry => entry.path !== path);
  recent.unshift({path, wavPath: item.wav_path, title: item.name, folder: state.currentFolder});
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent.slice(0, 6)));
}

async function viewItem(item) {
  const path = childPath(item.name) + '.txt';
  remember(item, path);
  await openMeeting(path, item.wav_path, item.name);
}

async function deletePath(path, label) {
  if (!confirm('Opravdu smazat ' + label + '?')) return;
  const result = await api.remove(path);
  if (result.error) { alert('Chyba: ' + result.error); return; }
  loadLibrary();
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

function updateRecentFolder(oldPath, newPath = null) {
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch {}
  recent = recent.flatMap(entry => {
    if (entry.folder !== oldPath && !entry.folder?.startsWith(oldPath + '/')) return [entry];
    if (!newPath) return [];
    const replace = value => value ? newPath + value.slice(oldPath.length) : value;
    return [{...entry, folder: replace(entry.folder), path: replace(entry.path), wavPath: replace(entry.wavPath)}];
  });
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
}

function updateRecentItem(folder, oldName, newFolder, newName) {
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch {}
  recent = recent.map(entry => {
    if (entry.folder !== folder || entry.title !== oldName) return entry;
    const prefix = newFolder ? newFolder + '/' : '';
    return {
      ...entry,
      folder: newFolder,
      title: newName,
      path: prefix + newName + '.txt',
      wavPath: entry.wavPath ? prefix + 'audio/' + newName + '.wav' : null,
    };
  });
  localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
}

async function renameFolder(name) {
  const next = prompt('Nový název složky:', name);
  if (!next || next === name) return;
  const oldPath = childPath(name);
  const result = await api.renameFolder(oldPath, next.trim());
  if (result.error) { alert('Chyba: ' + result.error); return; }
  remapWorkspaceFolder(oldPath, result.folder);
  updateRecentFolder(oldPath, result.folder);
  loadLibrary();
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

async function deleteFolder(name) {
  const path = childPath(name);
  if (!confirm(`Smazat složku „${name}“ včetně všech nahrávek, přepisů a souhrnů?`)) return;
  const result = await api.deleteFolder(path);
  if (result.error) { alert('Chyba: ' + result.error); return; }
  clearWorkspaceWithin(path);
  updateRecentFolder(path);
  loadLibrary();
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

function openMoveDialog(item) {
  moveTarget = {...item, folder: state.currentFolder};
  $('move-title').textContent = item.name;
  $('move-folder').parentElement.hidden = false;
  $('move-folder').value = state.currentFolder;
  $('move-name').value = item.name;
  $('move-dialog').showModal();
}

export function openFolder(folder) {
  state.currentFolder = folder || '';
  $('library-search').value = '';
  location.hash = '#library';
  loadLibrary();
}

export async function createFolder() {
  const name = prompt('Název nové složky:');
  if (!name) return;
  const result = await api.mkdir(childPath(name));
  if (result.error) { alert('Chyba: ' + result.error); return; }
  loadLibrary();
  window.dispatchEvent(new Event('transcriber:library-changed'));
}

function renderCrumbs() {
  const wrap = $('crumbs');
  wrap.replaceChildren();
  const root = el('button', {className: 'crumb', textContent: 'Knihovna'});
  root.onclick = () => openFolder('');
  wrap.appendChild(root);
  const parts = state.currentFolder ? state.currentFolder.split('/') : [];
  let acc = '';
  parts.forEach(part => {
    acc = acc ? acc + '/' + part : part;
    wrap.appendChild(el('span', {className: 'sep', textContent: '/'}));
    const segment = el('button', {className: 'crumb', textContent: part});
    const target = acc;
    segment.onclick = () => openFolder(target);
    wrap.appendChild(segment);
  });
}

function renderFolder(name) {
  const open = el('button', {className: 'folder-card', type: 'button'}, [
    el('span', {className: 'folder-mark', textContent: '↳'}),
    el('span', {className: 'folder-copy'}, [el('b', {textContent: name}), el('small', {textContent: 'Otevřít složku'})]),
    el('span', {className: 'folder-arrow', textContent: '→'}),
  ]);
  open.onclick = () => openFolder(childPath(name));

  const more = el('details', {className: 'item-more folder-more'});
  const trigger = el('summary', {textContent: '•••', title: `Akce složky ${name}`});
  trigger.setAttribute('aria-label', `Akce složky ${name}`);
  const menu = el('div', {className: 'item-menu'});
  const rename = el('button', {className: 'menu-action', type: 'button', textContent: 'Přejmenovat'});
  rename.onclick = () => renameFolder(name);
  const remove = el('button', {className: 'danger-action', type: 'button', textContent: 'Smazat složku'});
  remove.onclick = () => deleteFolder(name);
  menu.append(rename, remove);
  more.append(trigger, menu);

  return el('div', {className: 'folder-entry'}, [open, more]);
}

function renderItem(item) {
  const open = el('button', {className: 'item-open', type: 'button'});
  open.append(
    el('span', {className: 'item-icon', textContent: item.txt ? 'TXT' : 'WAV'}),
    el('span', {className: 'item-copy'}, [
      el('b', {textContent: item.name}),
      el('small', {textContent: item.txt ? (item.summary ? 'Přepis · souhrn' : 'Přepis bez souhrnu') : 'Nahrávka čeká na přepis'}),
    ]),
  );
  open.disabled = !item.txt;
  if (item.txt) open.onclick = () => viewItem(item);

  const actions = el('div', {className: 'item-actions'});
  if (item.wav) {
    const edit = el('button', {className: 'small accent', type: 'button', textContent: 'Upravit'});
    edit.onclick = () => openRecordingWorkspace(item.wav_path, item.name, item.txt);
    actions.appendChild(edit);
  }

  const more = el('details', {className: 'item-more'});
  const trigger = el('summary', {textContent: '•••', title: `Akce meetingu ${item.name}`});
  trigger.setAttribute('aria-label', `Akce meetingu ${item.name}`);
  const menu = el('div', {className: 'item-menu'});
  const move = el('button', {className: 'menu-action', type: 'button', textContent: 'Přesunout'});
  move.onclick = () => {
    more.open = false;
    openMoveDialog(item);
  };
  menu.appendChild(move);
  if (item.txt) {
    const del = el('button', {className: 'danger-action', type: 'button', textContent: 'Smazat přepis'});
    del.onclick = () => deletePath(childPath(item.name) + '.txt', 'přepis');
    menu.appendChild(del);
  }
  if (item.summary) {
    const del = el('button', {className: 'danger-action', type: 'button', textContent: 'Smazat souhrn'});
    del.onclick = () => deletePath(item.summary_path, 'souhrn');
    menu.appendChild(del);
  }
  if (item.wav) {
    const del = el('button', {className: 'danger-action', type: 'button', textContent: 'Smazat nahrávku'});
    del.onclick = () => deletePath(item.wav_path, 'nahrávku');
    menu.appendChild(del);
  }
  more.append(trigger, menu);
  actions.appendChild(more);

  return el('article', {className: 'library-item'}, [open, actions]);
}

function renderListing(query = '') {
  const needle = query.trim().toLocaleLowerCase('cs');
  const folders = listing.subfolders.filter(name => name.toLocaleLowerCase('cs').includes(needle));
  const items = listing.items.filter(item => item.name.toLocaleLowerCase('cs').includes(needle));
  const wrap = $('library');
  wrap.replaceChildren();
  if (!folders.length && !items.length) {
    wrap.appendChild(el('div', {className: 'empty library-empty', textContent: needle ? 'Nic takového v této složce není.' : 'Tato složka je prázdná.'}));
    return;
  }
  if (folders.length) {
    wrap.appendChild(el('p', {className: 'browser-label', textContent: 'Složky'}));
    const grid = el('div', {className: 'folder-grid'});
    folders.forEach(name => grid.appendChild(renderFolder(name)));
    wrap.appendChild(grid);
  }
  if (items.length) {
    wrap.appendChild(el('p', {className: 'browser-label', textContent: 'Meetingy'}));
    const list = el('div', {className: 'item-list'});
    items.forEach(item => list.appendChild(renderItem(item)));
    wrap.appendChild(list);
  }
}

export async function loadLibrary() {
  renderCrumbs();
  const data = await api.browse(state.currentFolder);
  if (data.error) {
    listing = {subfolders: [], items: []};
    $('library').replaceChildren(el('div', {className: 'empty library-empty', textContent: 'Složku se nepodařilo načíst: ' + data.error}));
    return;
  }
  listing = data;
  renderListing($('library-search').value);
}

export async function loadDashboard() {
  const {projects = []} = await api.projects();
  const projectWrap = $('dashboard-projects');
  projectWrap.replaceChildren();
  if (!projects.length) {
    projectWrap.appendChild(el('a', {className: 'empty-project', href: '#new', textContent: 'Zatím bez projektů — založit první meeting →'}));
  } else {
    projects.forEach(name => {
      const button = el('button', {className: 'project-card', type: 'button'}, [
        el('span', {textContent: name.slice(0, 2).toUpperCase()}),
        el('div', {}, [el('b', {textContent: name}), el('small', {textContent: 'Otevřít projekt'})]),
        el('i', {textContent: '→'}),
      ]);
      button.onclick = () => openFolder(name);
      projectWrap.appendChild(button);
    });
  }

  let recent = [];
  try { recent = JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch {}
  $('recent-section').hidden = !recent.length;
  const recentWrap = $('dashboard-recent');
  recentWrap.replaceChildren();
  recent.forEach(entry => {
    const button = el('button', {className: 'recent-item', type: 'button'}, [
      el('span', {className: 'item-icon', textContent: 'TXT'}),
      el('span', {className: 'item-copy'}, [el('b', {textContent: entry.title}), el('small', {textContent: entry.folder || 'Knihovna'})]),
      el('span', {textContent: '→'}),
    ]);
    button.onclick = () => { state.currentFolder = entry.folder || ''; openMeeting(entry.path, entry.wavPath, entry.title); };
    recentWrap.appendChild(button);
  });
}

export function initLibrary() {
  $('library-search').addEventListener('input', event => renderListing(event.target.value));
  window.addEventListener('transcriber:open-folder', event => openFolder(event.detail));
  window.addEventListener('transcriber:rename-meeting', event => {
    const {folder, name, wavPath, onSuccess} = event.detail;
    moveTarget = {folder, name, wav_path: wavPath, onSuccess};
    $('move-title').textContent = 'Přejmenovat meeting';
    $('move-folder').parentElement.hidden = true;
    $('move-folder').value = folder;
    $('move-name').value = name;
    $('move-dialog').showModal();
    $('move-name').focus();
  });
  window.addEventListener('transcriber:library-changed', () => {
    if (location.hash === '#library') loadLibrary();
    loadDashboard();
  });
  $('move-cancel').onclick = $('move-close').onclick = () => $('move-dialog').close();
  $('move-form').onsubmit = async event => {
    event.preventDefault();
    if (!moveTarget) return;
    const toFolder = $('move-folder').value.trim();
    const toName = $('move-name').value.trim();
    const result = await api.move(moveTarget.folder, moveTarget.name, toFolder, toName, moveTarget.wav_path);
    if (result.error) { alert('Chyba: ' + result.error); return; }
    const finished = moveTarget;
    $('move-dialog').close();
    moveTarget = null;
    updateRecentItem(finished.folder, finished.name, toFolder, toName);
    finished.onSuccess?.(toFolder, toName);
    loadLibrary();
    window.dispatchEvent(new Event('transcriber:library-changed'));
  };
}
