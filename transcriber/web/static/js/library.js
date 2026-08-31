import * as api from './api.js';
import {$, el} from './dom.js';
import {childPath, state} from './state.js';
import {transcribePath} from './transcribe.js';

async function viewFile(path) {
  const j = await api.readFile(path);
  $('out').value = j.text || '';
  $('dl').style.display = 'inline';
  $('dl').href = api.downloadUrl(path);
}

async function deletePath(path, label) {
  if (!confirm('Opravdu smazat ' + label + '?')) return;
  await api.remove(path);
  loadLibrary();
}

async function movePrompt(name, wavPath) {
  const current = childPath(name);
  const dest = prompt('Nová cesta včetně složky (např. Podcasty/Keto/nazev), bez přípony:', current);
  if (!dest || dest === current) return;
  const idx = dest.lastIndexOf('/');
  const toFolder = idx === -1 ? '' : dest.slice(0, idx);
  const toName = idx === -1 ? dest : dest.slice(idx + 1);
  const j = await api.move(state.currentFolder, name, toFolder, toName, wavPath);
  if (j.error) { alert('Chyba: ' + j.error); return; }
  loadLibrary();
}

export function openFolder(folder) {
  state.currentFolder = folder;
  loadLibrary();
}

export async function createFolder() {
  const name = prompt('Název nové složky:');
  if (!name) return;
  await api.mkdir(childPath(name));
  loadLibrary();
}

function renderCrumbs() {
  const wrap = $('crumbs');
  wrap.innerHTML = '';
  const root = el('span', {textContent: 'Knihovna'});
  root.onclick = () => openFolder('');
  wrap.appendChild(root);
  const parts = state.currentFolder ? state.currentFolder.split('/') : [];
  let acc = '';
  parts.forEach(p => {
    acc = acc ? acc + '/' + p : p;
    wrap.appendChild(el('span', {className: 'sep', textContent: ' / '}));
    const seg = el('span', {textContent: p});
    const target = acc;
    seg.onclick = () => openFolder(target);
    wrap.appendChild(seg);
  });
}

function renderFolder(name) {
  const row = el('span', {className: 'folderrow', textContent: '📁 ' + name});
  row.onclick = () => openFolder(childPath(name));
  return el('div', {className: 'browserow'}, [el('div', {className: 'left'}, [row])]);
}

function renderItem(item) {
  const left = [];
  if (item.txt) {
    const path = childPath(item.name) + '.txt';
    const view = el('span', {className: 'libfile', textContent: item.name});
    view.onclick = () => viewFile(path);
    left.push(view);
  } else {
    left.push(el('span', {className: 'pending', textContent: item.name + ' (nepřepsáno)'}));
  }

  const right = [];
  if (!item.txt && item.wav) {
    const btn = el('button', {className: 'small accent', textContent: 'Přepsat'});
    btn.onclick = () => {
      state.currentPath = item.wav_path;
      transcribePath(item.wav_path, loadLibrary);
    };
    right.push(btn);
  }
  const rename = el('button', {className: 'icon', textContent: '✎', title: 'Přejmenovat / přesunout'});
  rename.onclick = () => movePrompt(item.name, item.wav_path);
  right.push(rename);
  if (item.txt) {
    const delTxt = el('button', {className: 'icon', textContent: '🗑T', title: 'Smazat přepis'});
    delTxt.onclick = () => deletePath(childPath(item.name) + '.txt', 'přepis');
    right.push(delTxt);
  }
  if (item.wav) {
    const delWav = el('button', {className: 'icon', textContent: '🗑A', title: 'Smazat nahrávku'});
    delWav.onclick = () => deletePath(item.wav_path, 'nahrávku');
    right.push(delWav);
  }

  return el('div', {className: 'browserow'}, [
    el('div', {className: 'left'}, left),
    el('div', {className: 'row'}, right),
  ]);
}

export async function loadLibrary() {
  renderCrumbs();
  const data = await api.browse(state.currentFolder);
  const wrap = $('library');
  wrap.innerHTML = '';
  if (data.error || (!data.subfolders.length && !data.items.length)) {
    wrap.appendChild(el('div', {className: 'empty', textContent: 'Prázdná složka.'}));
    return;
  }
  data.subfolders.forEach(name => wrap.appendChild(renderFolder(name)));
  data.items.forEach(item => wrap.appendChild(renderItem(item)));
}
