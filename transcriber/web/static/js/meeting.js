// Meeting detail overlay: audio-synced transcript with renameable speaker
// labels, plus the structured summary (decisions / actions / open questions)
// when one has been generated. Opened from a library item that has segments.
import * as api from './api.js';
import {openRecordingWorkspace} from './transcribe.js';
import {$, el} from './dom.js';
import {showView} from './views.js';

let segRows = [];

const folderFor = path => path.slice(0, path.lastIndexOf('/'));

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

function seekTo(audio, seconds) {
  if (!audio.src) return;
  audio.currentTime = seconds || 0;
  audio.play().catch(() => {});
}

async function renameSpeaker(path, oldName, audio) {
  const name = prompt('New name for ' + oldName + ':', oldName);
  if (!name || name === oldName) return;
  const j = await api.renameSpeaker(path, oldName, name);
  if (j.error) { alert('Error: ' + j.error); return; }
  renderTranscript(path, j.segments, audio);
}

// Whisper emits a segment every couple of seconds; one row each is unreadable.
// Merge consecutive segments of the same speaker into paragraphs, breaking on a
// long pause or once a paragraph gets bulky. Seeking lands on the paragraph start.
const PAUSE_BREAK = 2.5;
const MAX_CHARS = 400;

function groupSegments(segments) {
  const blocks = [];
  segments.forEach(seg => {
    const last = blocks[blocks.length - 1];
    const merge = last
      && last.speaker === seg.speaker
      && seg.start - last.end <= PAUSE_BREAK
      && last.text.length < MAX_CHARS;
    if (merge) {
      last.text += ' ' + seg.text.trim();
      last.end = seg.end;
    } else {
      blocks.push({start: seg.start, end: seg.end, speaker: seg.speaker, text: seg.text.trim()});
    }
  });
  return blocks;
}

function renderTranscript(path, segments, audio) {
  const wrap = $('meeting-transcript');
  wrap.replaceChildren();
  segRows = [];
  if (!segments || !segments.length) {
    wrap.appendChild(el('div', {className: 'empty', textContent: 'No segments (older transcript).'}));
    return;
  }
  groupSegments(segments).forEach(seg => {
    const time = el('button', {className: 'segtime', textContent: formatTime(seg.start)});
    time.onclick = () => seekTo(audio, seg.start);
    const rowChildren = [time];
    if (seg.speaker) {
      const speaker = el('button', {className: 'segspeaker', textContent: seg.speaker, title: 'Rename speaker'});
      speaker.onclick = e => { e.stopPropagation(); renameSpeaker(path, seg.speaker, audio); };
      rowChildren.push(speaker);
    }
    rowChildren.push(el('span', {className: 'segtext', textContent: seg.text}));
    const row = el('div', {className: 'segrow'}, rowChildren);
    row.dataset.start = seg.start;
    row.dataset.end = seg.end;
    row.onclick = () => seekTo(audio, seg.start);
    wrap.appendChild(row);
    segRows.push(row);
  });
}

function summarySection(wrap, title, items, render) {
  if (!items || !items.length) return;
  wrap.appendChild(el('p', {className: 'side-label', textContent: title}));
  items.forEach(item => wrap.appendChild(render(item)));
}

function renderSummary(data) {
  const wrap = $('meeting-summary');
  wrap.replaceChildren();
  if (!data || !data.markdown && !data.summary && !(data.decisions || []).length && !(data.action_items || []).length && !(data.open_questions || []).length) {
    wrap.appendChild(el('div', {className: 'empty', textContent: 'No summary yet.'}));
    return;
  }
  if (data.markdown) {
    wrap.appendChild(el('pre', {className: 'meeting-summary-markdown', textContent: data.markdown}));
    return;
  }
  if (data.summary) wrap.appendChild(el('p', {className: 'meeting-summary-text', textContent: data.summary}));
  summarySection(wrap, 'Decisions', data.decisions, d => el('div', {className: 'meeting-item', textContent: d.text}));
  summarySection(wrap, 'Tasks', data.action_items, a => el('div', {
    className: 'meeting-item',
    textContent: a.text + (a.owner ? ' — ' + a.owner : '') + (a.deadline ? ' (' + a.deadline + ')' : ''),
  }));
  summarySection(wrap, 'Open questions', data.open_questions, q => el('div', {className: 'meeting-item', textContent: q.text}));
}

function highlightActive(audio) {
  const t = audio.currentTime;
  segRows.forEach(row => {
    const active = t >= Number(row.dataset.start) && t < Number(row.dataset.end);
    row.classList.toggle('active', active);
    if (active) row.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  });
}

// Provenance line: engine, model, diarization. Older meeting.json files carry
// only `backend`, so every field is optional.
function renderMeta(meeting) {
  const asr = meeting.asr || {};
  const diar = meeting.diarization || {};
  const parts = [];
  const where = asr.where || (meeting.backend === 'spark' ? 'Remote Spark' : meeting.backend ? 'Locally' : null);
  if (where) parts.push('Transcription: ' + where + (asr.model ? ' — ' + asr.model : '') + (asr.device ? ' (' + asr.device + ')' : ''));
  if (diar.applied) parts.push('Speakers: ' + (diar.model || 'recognized'));
  else if (diar.applied === false) parts.push('Speakers: not recognized');
  if (meeting.language) parts.push('Language: ' + meeting.language);
  if (meeting.duration) parts.push('Duration: ' + Math.round(meeting.duration / 60) + ' min');
  if (meeting.created_at) parts.push(new Date(meeting.created_at).toLocaleString('en-US'));
  $('meeting-meta').textContent = parts.join(' · ');
  $('meeting-meta').title = diar.note || '';
}

export async function openMeeting(path, wavPath, title) {
  const audio = $('meeting-audio');
  $('meeting-title').textContent = title;
  audio.src = wavPath ? api.audioUrl(wavPath) : '';
  audio.ontimeupdate = () => highlightActive(audio);

  const [meeting, summary] = await Promise.all([
    api.readMeeting(path),
    api.readSummaryJson(path),
  ]);
  renderMeta(meeting);
  const editMode = $('meeting-edit-mode');
  editMode.onclick = () => {
    closeMeeting();
    openRecordingWorkspace(wavPath, title, true);
  };
  editMode.disabled = !wavPath;
  editMode.title = wavPath ? 'Open transcription and summary tools' : 'Editing requires the original recording';
  $('meeting-rename').onclick = () => {
    const folder = folderFor(path);
    window.dispatchEvent(new CustomEvent('transcriber:rename-meeting', {
      detail: {
        folder,
        name: title,
        wavPath,
        onSuccess: (_folder, name) => openMeeting(
          `${folder}/${name}.txt`,
          wavPath ? `${folder}/audio/${name}.wav` : null,
          name,
        ),
      },
    }));
  };
  renderTranscript(path, meeting.segments, audio);
  renderSummary(summary);

  location.hash = '#meeting';
  showView('meeting');
}

export function closeMeeting() {
  const audio = $('meeting-audio');
  audio.pause();
  audio.removeAttribute('src');
}

$('meeting-back').onclick = () => { location.hash = '#archive'; };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && location.hash === '#meeting') location.hash = '#archive';
});
