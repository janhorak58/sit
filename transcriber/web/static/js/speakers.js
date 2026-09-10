// Naming the people in a transcript: one input per recognized label, applied
// to every cue of that speaker at once.

import * as api from './api.js';
import {el} from './dom.js';

function speakerCounts(segments) {
  const counts = new Map();
  (segments || []).forEach(segment => {
    const speaker = segment.speaker?.trim();
    if (speaker) counts.set(speaker, (counts.get(speaker) || 0) + 1);
  });
  return counts;
}

function cueTime(seconds) {
  if (!Number.isFinite(seconds)) return '';
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function cuePreview() {
  const title = el('h2', {textContent: ''});
  const intro = el('p', {className: 'speaker-cue-intro'});
  const cues = el('div', {className: 'speaker-cue-list'});
  const close = el('button', {className: 'icon', type: 'button', ariaLabel: 'Close speaker cues', textContent: '×'});
  const dialog = el('dialog', {className: 'speaker-cue-dialog', ariaLabel: 'Speaker cues'}, [
    el('header', {className: 'speaker-cue-dialog-head'}, [
      el('div', {}, [
        el('p', {className: 'eyebrow', textContent: 'SPEAKER SAMPLE'}),
        title,
      ]),
      close,
    ]),
    intro,
    cues,
  ]);
  close.onclick = () => dialog.close();
  dialog.onclick = event => {
    if (event.target === dialog) dialog.close();
  };
  return {
    dialog,
    show(speaker, segments) {
      const matching = (segments || []).map((segment, index) => ({segment, index}))
        .filter(({segment}) => segment.speaker?.trim() === speaker);
      title.textContent = speaker;
      intro.textContent = `${matching.length} ${matching.length === 1 ? 'cue' : 'cues'} detected for this speaker. Read their lines to identify them before renaming.`;
      cues.replaceChildren(...matching.map(({segment, index}) => el('article', {className: 'speaker-cue'}, [
        el('span', {className: 'speaker-cue-time', textContent: cueTime(segment.start) || `Cue ${index + 1}`}),
        el('p', {textContent: segment.text?.trim() || 'Empty cue'}),
      ])));
      dialog.showModal();
    },
  };
}

/**
 * Editor for the speaker labels of one transcript. Returns `null` when the
 * transcript has no recognized speakers to rename. `notice` seeds the status
 * line so a caller that re-renders after saving keeps the confirmation.
 */
export function speakerNameEditor({path, segments, onSaved, notice = ''}) {
  const counts = speakerCounts(segments);
  if (!counts.size) return null;
  const status = el('p', {className: 'status-line', textContent: notice});
  const rows = el('div', {className: 'speaker-rows'});
  const preview = cuePreview();
  const inputs = new Map();
  counts.forEach((count, speaker) => {
    const input = el('input', {value: speaker, ariaLabel: `Name for ${speaker}`, placeholder: speaker});
    const inspect = el('button', {
      className: 'speaker-preview',
      type: 'button',
      textContent: 'View cues',
      ariaLabel: `View cues for ${speaker}`,
    });
    inspect.onclick = () => preview.show(speaker, segments);
    inputs.set(speaker, input);
    rows.append(el('div', {className: 'speaker-row'}, [
      el('div', {className: 'speaker-row-meta'}, [
        el('span', {textContent: `${count} ${count === 1 ? 'cue' : 'cues'}`}),
        inspect,
      ]),
      input,
    ]));
  });
  const save = el('button', {className: 'small primary', type: 'button', textContent: 'Save speaker names'});
  save.onclick = async () => {
    const names = {};
    for (const [speaker, input] of inputs) {
      const value = input.value.trim();
      if (!value) { status.textContent = 'Every speaker needs a name.'; return; }
      if (value !== speaker) names[speaker] = value;
    }
    if (!Object.keys(names).length) { status.textContent = 'Nothing to rename.'; return; }
    save.disabled = true;
    status.textContent = 'Saving names…';
    const result = await api.renameSpeakers(path, names);
    save.disabled = false;
    if (result.error) { status.textContent = 'Could not save: ' + result.error; return; }
    status.textContent = 'Names saved across the whole transcript. The AI analysis is now marked outdated.';
    window.dispatchEvent(new Event('transcriber:library-changed'));
    if (onSaved) onSaved(result.segments, status.textContent);
  };
  return el('section', {className: 'speaker-naming'}, [
    el('header', {}, [
      el('b', {textContent: 'Who is speaking?'}),
      el('small', {textContent: 'Preview each person’s lines, then replace the detected labels with real names.'}),
    ]),
    rows,
    el('div', {className: 'speaker-naming-actions'}, [status, save]),
    preview.dialog,
  ]);
}
