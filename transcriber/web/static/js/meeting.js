import * as api from './api.js';
import {$, el} from './dom.js';
import {showView} from './views.js';
import {getPreferences} from './customizations.js';
import {speakerNameEditor} from './speakers.js';
import {forgetRecentPath} from './library.js';

let segRows = [];
let meetingState = null;

export const hasOpenMeeting = () => meetingState !== null;
let progressTimer = null;
let progressRequest = 0;
let elapsedTimer = null;

const PAUSE_BREAK = 2.5;
const MAX_CHARS = 400;
const SUMMARY_LIST_FIELDS = ['chapters', 'decisions', 'action_items', 'open_questions', 'risks', 'speaker_contributions', 'changes_since_last'];
const hasSummary = summary => Boolean(summary && (
  summary.markdown ||
  summary.summary ||
  summary.follow_up ||
  SUMMARY_LIST_FIELDS.some(field => Array.isArray(summary[field]) && summary[field].length)
));
const SECTION_LABELS = {
  chapters: 'Chapters',
  decisions: 'Decisions',
  action_items: 'Action items',
  open_questions: 'Open questions',
  risks: 'Risks',
  speaker_contributions: 'Speaker contributions',
  follow_up: 'Follow-up draft',
};
const folderFor = path => path.slice(0, path.lastIndexOf('/'));
const formatTime = seconds => {
  const s = Math.max(0, Math.floor(seconds || 0));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
};
const seekTo = (audio, seconds) => {
  if (!audio.src) return;
  audio.currentTime = seconds || 0;
  audio.play().catch(() => {});
};
const paragraphLimit = () => ({short: 200, normal: MAX_CHARS, long: 800}[getPreferences()?.transcript?.paragraph_size] || MAX_CHARS);

function groupSegments(segments) {
  const blocks = [];
  segments.forEach(seg => {
    const last = blocks.at(-1);
    if (last && last.speaker === seg.speaker && seg.start - last.end <= PAUSE_BREAK && last.text.length < paragraphLimit()) {
      last.text += ' ' + seg.text.trim();
      last.end = seg.end;
    } else {
      blocks.push({start: seg.start, end: seg.end, speaker: seg.speaker, text: seg.text.trim()});
    }
  });
  return blocks;
}

function renderTranscript(wrap = $('meeting-artifact-body')) {
  const {audio, segments = []} = meetingState;
  wrap.replaceChildren();
  segRows = [];
  if (!segments.length) {
    wrap.appendChild(el('p', {className: 'empty', textContent: 'No timed transcript is available.'}));
    return;
  }
  groupSegments(segments).forEach(seg => {
    const row = el('div', {className: 'segrow'});
    const time = el('button', {className: 'segtime', type: 'button', textContent: formatTime(seg.start)});
    time.onclick = () => seekTo(audio, seg.start);
    row.append(time);
    if (seg.speaker) row.append(el('span', {className: 'segspeaker', textContent: seg.speaker}));
    row.append(el('span', {className: 'segtext', textContent: seg.text}));
    row.dataset.start = seg.start;
    row.dataset.end = seg.end;
    row.onclick = event => { if (event.target !== time) seekTo(audio, seg.start); };
    wrap.append(row);
    segRows.push(row);
  });
}

function evidenceLinks(item) {
  const evidence = item.evidence || (item.segment_start == null ? [] : [{start: item.segment_start}]);
  if (!evidence.length) return null;
  const wrap = el('div', {className: 'evidence-links'});
  evidence.forEach((source, index) => {
    const link = el('button', {className: 'evidence-link', type: 'button', textContent: index ? `Also ${formatTime(source.start)}` : `Source ${formatTime(source.start)}`});
    link.onclick = () => seekTo(meetingState.audio, source.start);
    wrap.append(link);
  });
  return wrap;
}

function briefItem(item, details = [], tone = '') {
  const article = el('article', {className: `brief-item ${tone}`.trim()}, [el('div', {className: 'brief-item-text', textContent: item.text})]);
  details.filter(Boolean).forEach(detail => article.append(el('p', {className: 'brief-item-detail', textContent: detail})));
  const evidence = evidenceLinks(item);
  if (evidence) article.append(evidence);
  return article;
}

function briefSection(title, items, render, tone) {
  if (!items?.length) return null;
  const list = el('div', {className: 'brief-list'});
  items.forEach((item, index) => list.append(render(item, index)));
  return el('section', {className: `brief-section ${tone}`}, [
    el('header', {}, [
      el('p', {className: 'brief-label', textContent: title}),
      el('span', {className: 'brief-count', textContent: String(items.length)}),
    ]),
    list,
  ]);
}

function renderSummary(wrap, transcriptAction = null) {
  const {summary} = meetingState;
  if (!hasSummary(summary)) {
    wrap.append(el('div', {className: 'empty', textContent: 'No AI analysis yet. Create it in Edit meeting.'}));
    return;
  }
  if (summary.markdown) {
    wrap.append(el('pre', {className: 'meeting-summary-markdown', textContent: summary.markdown}));
    return;
  }
  const sectionSpecs = [
    ['Timeline', summary.chapters, chapter => briefItem({text: chapter.title, evidence: chapter.evidence}, [chapter.summary], 'timeline-item'), 'timeline'],
    ['Decisions', summary.decisions, item => briefItem(item, [item.rationale && `Why: ${item.rationale}`, item.alternatives?.length && `Alternatives: ${item.alternatives.join(' · ')}`], 'decision-item'), 'decisions'],
    ['Action items', summary.action_items, item => briefItem(item, [[item.owner, item.deadline, item.priority && `${item.priority} priority`].filter(Boolean).join(' · ')], 'action-item'), 'actions'],
    ['Open questions', summary.open_questions, item => briefItem(item, [[item.owner && `Owner: ${item.owner}`, item.deadline && `By ${item.deadline}`].filter(Boolean).join(' · ')], 'question-item'), 'questions'],
    ['Risks', summary.risks, item => briefItem(item, [item.mitigation && `Mitigation: ${item.mitigation}`], 'risk-item'), 'risks'],
    ['Speaker contributions', summary.speaker_contributions, item => briefItem({text: item.speaker}, [
      item.commitments?.length && `Commitments: ${item.commitments.join(' · ')}`,
      item.decisions_proposed?.length && `Decisions proposed: ${item.decisions_proposed.join(' · ')}`,
      item.unanswered_asks?.length && `Unanswered asks: ${item.unanswered_asks.join(' · ')}`,
    ], 'speaker-item'), 'speakers'],
    ['Follow-up draft', summary.follow_up ? [summary.follow_up] : [], text => el('article', {className: 'brief-item follow-up-item', textContent: text}), 'follow-up'],
    ['What changed', summary.changes_since_last, text => el('article', {className: 'brief-item', textContent: text}), 'changes'],
  ];
  const sections = sectionSpecs
    .map(([title, items, render, tone]) => ({title, count: items?.length || 0, node: briefSection(title, items, render, tone)}))
    .filter(section => section.node);
  const headTop = el('div', {className: 'brief-head-top'}, [
    el('div', {}, [
      el('p', {className: 'brief-kicker', textContent: 'Meeting brief'}),
      el('h2', {textContent: 'What matters from this meeting'}),
    ]),
  ]);
  if (transcriptAction) headTop.append(transcriptAction);
  const head = el('section', {className: 'brief-head'}, [headTop]);
  head.append(el('p', {
    className: `meeting-summary-text ${summary.summary ? '' : 'is-placeholder'}`.trim(),
    textContent: summary.summary || 'Structured findings with direct links back to the recording.',
  }));
  const index = el('nav', {className: 'brief-index', ariaLabel: 'Analysis sections'});
  sections.forEach(section => {
    const button = el('button', {type: 'button', className: 'brief-index-item'}, [
      el('span', {className: 'brief-index-count', textContent: String(section.count)}),
      el('span', {textContent: section.title}),
    ]);
    button.onclick = () => window.scrollTo({
      top: window.scrollY + section.node.getBoundingClientRect().top - 96,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    });
    index.append(button);
  });
  wrap.append(el('div', {className: 'brief-shell'}, [
    head,
    index,
    el('div', {className: 'brief-sections'}, sections.map(section => section.node)),
  ]));
}

function isOwnProgress(progress) {
  return meetingState && (progress.source_path === meetingState.path || progress.saved_path === meetingState.path || progress.source_path === meetingState.wavPath);
}

function progressCard(progress) {
  const active = isOwnProgress(progress) && ['transcribing', 'diarizing', 'merging', 'analyzing'].includes(progress.stage);
  const completed = isOwnProgress(progress) && ['done', 'error'].includes(progress.stage);
  const finishedAnalysis = progress.stage === 'done' && progress.operation === 'summarize' && hasSummary(meetingState.summary);
  if ((!active && !completed) || finishedAnalysis) return null;
  const label = progress.operation === 'summarize' ? 'AI analysis' : progress.operation === 'diarize' ? 'Speaker recognition' : 'Transcript';
  const elapsed = active && meetingState.progressStartedAt ? Math.floor((Date.now() - meetingState.progressStartedAt) / 1000) : null;
  const message = (progress.message || '') + (elapsed != null ? ` (${formatTime(elapsed)} elapsed)` : '');
  const indeterminate = active && progress.operation === 'summarize';
  const heading = active ? `${label} in progress` : progress.stage === 'error' ? `${label} failed` : `${label} finished`;
  let stop = null;
  if (active) {
    stop = el('button', {className: 'small quiet', type: 'button', textContent: 'Stop this step'});
    stop.onclick = async () => {
      stop.disabled = true;
      const result = await api.cancelJob();
      if (result.error) { stop.disabled = false; alert('Could not stop: ' + result.error); return; }
      await refreshProgress();
    };
  }
  return el('section', {className: `meeting-progress ${progress.stage === 'error' ? 'is-error' : ''}`}, [
    el('div', {}, [el('b', {textContent: heading}), el('span', {textContent: message})]),
    active ? el('div', {className: `meeting-progress-bar ${indeterminate ? 'indeterminate' : ''}`}, [el('i', indeterminate ? {} : {style: `width:${progress.percent || 5}%`})]) : null,
    stop,
  ].filter(Boolean));
}

function refreshErrorCard() {
  if (!meetingState?.refreshError) return null;
  return el('section', {className: 'meeting-progress is-error'}, [
    el('div', {}, [el('b', {textContent: 'Could not refresh'}), el('span', {textContent: meetingState.refreshError})]),
  ]);
}

function progressSlot() {
  const slot = el('div', {className: 'meeting-progress-slot'});
  const card = progressCard(meetingState.progress || {});
  if (card) slot.append(card);
  const errorCard = refreshErrorCard();
  if (errorCard) slot.append(errorCard);
  return slot;
}

function updateProgressDisplay() {
  const slot = document.querySelector('.meeting-progress-slot');
  if (!slot || !meetingState) return;
  const card = progressCard(meetingState.progress || {});
  const errorCard = refreshErrorCard();
  slot.replaceChildren(...(card ? [card] : []), ...(errorCard ? [errorCard] : []));
}

function setOperationButtons(disabled) {
  document.querySelectorAll('.editor-action-row button').forEach(button => { button.disabled = disabled; });
}

async function refreshMeeting() {
  const request = progressRequest;
  const [meeting, summary] = await Promise.all([api.readMeeting(meetingState.path), api.readSummaryJson(meetingState.path)]);
  if (request !== progressRequest || !meetingState) return;
  if (meeting.error) {
    meetingState.refreshError = 'Could not refresh this meeting: ' + meeting.error;
    updateProgressDisplay();
    return;
  }
  meetingState.refreshError = null;
  meetingState.segments = meeting.segments || [];
  meetingState.meeting = meeting;
  meetingState.summary = summary.error || !hasSummary(summary) ? null : summary;
  renderCurrentMode();
}

async function refreshProgress() {
  if (!meetingState) return;
  const request = ++progressRequest;
  try {
    const progress = await api.getProgress();
    if (request !== progressRequest || !meetingState) return;
    meetingState.progress = progress;
    const active = isOwnProgress(progress) && ['transcribing', 'diarizing', 'merging', 'analyzing'].includes(progress.stage);
    if (active && !meetingState.progressStartedAt) meetingState.progressStartedAt = Date.now();
    syncElapsedTimer(active);
    updateProgressDisplay();
    setOperationButtons(active);
    if (active) {
      clearTimeout(progressTimer);
      progressTimer = setTimeout(refreshProgress, 1000);
    } else if (isOwnProgress(progress) && progress.stage === 'done' && meetingState.lastProgressStage !== 'done') {
      await refreshMeeting();
      if (meetingState) meetingState.lastProgressStage = 'done';
    }
  } catch {
    // Transient failure (e.g. backend restarting mid-poll): retry instead of
    // getting permanently stuck on the last successfully rendered state.
    if (!meetingState) return;
    clearTimeout(progressTimer);
    progressTimer = setTimeout(refreshProgress, 1500);
  }
}

function syncElapsedTimer(active) {
  if (active && !elapsedTimer) elapsedTimer = setInterval(updateProgressDisplay, 1000);
  if (!active && elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}

function renderView() {
  const body = $('meeting-body');
  const summary = el('section', {className: 'meeting-summary card'});
  summary.append(progressSlot());
  if (meetingState.summary && meetingState.meeting.analysis_stale) summary.append(el('p', {className: 'analysis-stale', textContent: 'Analysis is outdated after transcript edits. Regenerate it in Edit meeting.'}));
  const transcript = el('button', {className: 'brief-transcript-action quiet', type: 'button', textContent: 'Open transcript'});
  transcript.onclick = () => openArtifact('TRANSCRIPT', 'Full transcript', renderTranscript);
  renderSummary(summary, hasSummary(meetingState.summary) ? transcript : null);
  if (!hasSummary(meetingState.summary)) summary.append(transcript);
  body.replaceChildren(summary);
}

function input(label, control) {
  return el('label', {}, [el('span', {textContent: label}), control]);
}

function editorSection(title, description, children) {
  return el('section', {className: 'editor-section card'}, [
    el('header', {}, [el('h2', {textContent: title}), el('p', {textContent: description})]),
    ...children,
  ]);
}

function openArtifact(kicker, title, render) {
  $('meeting-artifact-kicker').textContent = kicker;
  $('meeting-artifact-title').textContent = title;
  const body = $('meeting-artifact-body');
  body.replaceChildren();
  render(body);
  $('meeting-artifact-dialog').showModal();
}

function existingOutput(text, state, action, onAction) {
  const children = [
    el('span', {className: `output-state-dot ${state}`, ariaHidden: 'true'}),
    el('div', {}, [el('b', {textContent: text}), el('small', {textContent: state === 'ready' ? 'Available now' : state === 'stale' ? 'Available, but based on an older transcript' : 'Not generated yet'})]),
  ];
  if (onAction) {
    const button = el('button', {className: 'small quiet', type: 'button', textContent: action});
    button.onclick = onAction;
    children.push(button);
  }
  return el('div', {className: 'existing-output'}, children);
}

async function runOperation(progress, request) {
  progressRequest += 1;
  clearTimeout(progressTimer);
  meetingState.lastProgressStage = null;
  meetingState.progressStartedAt = Date.now();
  meetingState.progress = {...progress, percent: progress.percent || 5};
  syncElapsedTimer(true);
  updateProgressDisplay();
  setOperationButtons(true);
  try {
    const result = await request();
    if (result.error) throw new Error(result.error);
    if (result.summary) await refreshMeeting();
    else await refreshProgress();
  } catch (error) {
    syncElapsedTimer(false);
    meetingState.progress = {...progress, stage: 'error', error: error.message, message: error.message};
    updateProgressDisplay();
    setOperationButtons(false);
  }
}

function renderSpeakerResult(wrap) {
  const counts = new Map();
  let unassigned = 0;
  meetingState.segments.forEach(segment => {
    const speaker = segment.speaker?.trim();
    if (!speaker) unassigned += 1;
    else counts.set(speaker, (counts.get(speaker) || 0) + 1);
  });
  if (!counts.size) {
    wrap.append(el('p', {className: 'empty', textContent: 'No speakers have been recognized yet.'}));
    return;
  }
  wrap.append(el('p', {className: 'artifact-intro', textContent: `${counts.size} recognized ${counts.size === 1 ? 'speaker' : 'speakers'} across ${meetingState.segments.length} cues.`}));
  counts.forEach((count, speaker) => wrap.append(el('article', {className: 'speaker-result'}, [
    el('span', {className: 'segspeaker', textContent: speaker}),
    el('b', {textContent: `${count} ${count === 1 ? 'cue' : 'cues'}`}),
  ])));
  if (unassigned) wrap.append(el('p', {className: 'artifact-note', textContent: `${unassigned} cues remain without a speaker.`}));
}

function localBriefPreferences() {
  const base = getPreferences()?.brief || {};
  return {detail: base.detail || 'standard', language: base.language || 'same', compare_previous: base.compare_previous !== false, user_prompt: base.user_prompt || '', sections: {...base.sections}};
}

function renderEditor() {
  meetingState.editorTab ||= 'content';
  const body = $('meeting-body');
  const progress = progressCard(meetingState.progress || {});
  const transcriptRows = el('div', {className: 'editor-cues'});
  (meetingState.segments || []).forEach((seg, index) => {
    const time = el('button', {className: 'segtime', type: 'button', textContent: formatTime(seg.start)});
    time.onclick = () => seekTo(meetingState.audio, seg.start);
    const speaker = el('input', {className: 'editor-speaker', value: seg.speaker || '', placeholder: 'Speaker', ariaLabel: `Speaker for cue ${index + 1}`});
    const text = el('textarea', {className: 'editor-text', value: seg.text, ariaLabel: `Transcript cue ${index + 1}`});
    transcriptRows.append(el('article', {className: 'editor-cue'}, [time, speaker, text]));
  });
  const saveText = el('button', {className: 'small primary', type: 'button', textContent: 'Save transcript edits'});
  saveText.onclick = async () => {
    const segments = [...transcriptRows.querySelectorAll('.editor-cue')].map(cue => ({text: cue.querySelector('textarea').value, speaker: cue.querySelector('input').value || null}));
    if (segments.some(seg => !seg.text.trim())) { saveText.textContent = 'Every cue needs text'; return; }
    saveText.disabled = true;
    const result = await api.updateTranscript(meetingState.path, segments);
    saveText.disabled = false;
    if (result.error) { saveText.textContent = `Could not save: ${result.error}`; return; }
    meetingState.segments = result.segments;
    meetingState.meeting.analysis_stale = true;
    saveText.textContent = 'Saved — AI analysis is marked outdated';
    window.dispatchEvent(new Event('transcriber:library-changed'));
  };

  const language = el('select', {id: 'edit-language'});
  [['cs', 'Czech'], ['en', 'English'], ['', 'Detect automatically']].forEach(([value, text]) => language.append(el('option', {value, textContent: text})));
  language.value = meetingState.meeting.language || 'cs';
  const speakers = el('input', {type: 'number', min: '1', value: meetingState.meeting.num_speakers || '', placeholder: 'Detect automatically'});
  const regenerateTranscript = el('button', {className: 'small primary', type: 'button', textContent: 'Regenerate transcript'});
  regenerateTranscript.disabled = !meetingState.wavPath;
  regenerateTranscript.onclick = () => {
    if (!confirm('Replace the current transcript, speaker labels, and AI analysis?')) return;
    runOperation({
      stage: 'transcribing', operation: 'transcribe', message: 'Starting transcript regeneration…',
      source_path: meetingState.wavPath, saved_path: meetingState.path,
    }, () => api.transcribe(meetingState.wavPath, language.value, Number(speakers.value) || null));
  };
  const speakerCount = el('input', {type: 'number', min: '1', value: meetingState.meeting.num_speakers || '', placeholder: 'Detect automatically'});
  const rerunSpeakers = el('button', {className: 'small primary', type: 'button', textContent: 'Recognize speakers again'});
  rerunSpeakers.disabled = !meetingState.wavPath || !meetingState.segments.length;
  rerunSpeakers.onclick = () => {
    if (!confirm('Replace current speaker labels?')) return;
    runOperation({
      stage: 'diarizing', operation: 'diarize', message: 'Starting speaker recognition…',
      source_path: meetingState.wavPath, saved_path: meetingState.path,
    }, () => api.diarize(meetingState.wavPath, Number(speakerCount.value) || null));
  };

  const brief = localBriefPreferences();
  const detail = el('select', {}); [['short', 'Short'], ['standard', 'Standard'], ['detailed', 'Detailed']].forEach(([value, text]) => detail.append(el('option', {value, textContent: text}))); detail.value = brief.detail;
  const briefLanguage = el('select', {}); [['same', 'Transcript language'], ['cs', 'Czech'], ['en', 'English']].forEach(([value, text]) => briefLanguage.append(el('option', {value, textContent: text}))); briefLanguage.value = brief.language;
  const prompt = el('textarea', {className: 'analysis-prompt', value: brief.user_prompt, placeholder: 'Optional instructions for this analysis…'});
  const sections = el('div', {className: 'analysis-sections'});
  Object.entries(brief.sections).forEach(([key, enabled]) => {
    const checkbox = el('input', {type: 'checkbox', checked: enabled});
    checkbox.dataset.section = key;
    sections.append(el('label', {className: 'setting-toggle'}, [checkbox, el('span', {textContent: SECTION_LABELS[key] || key.replaceAll('_', ' ')})]));
  });
  const regenerateAnalysis = el('button', {className: 'small primary', type: 'button', textContent: meetingState.summary ? 'Regenerate AI analysis' : 'Generate AI analysis'});
  regenerateAnalysis.disabled = !meetingState.segments.length;
  regenerateAnalysis.onclick = () => {
    const preferences = {brief: {...brief, detail: detail.value, language: briefLanguage.value, user_prompt: prompt.value, sections: Object.fromEntries([...sections.querySelectorAll('input')].map(box => [box.dataset.section, box.checked]))}};
    runOperation({
      stage: 'analyzing', operation: 'summarize', message: 'Starting AI analysis…',
      source_path: meetingState.path, saved_path: meetingState.path,
    }, () => api.summarize(meetingState.path, folderFor(meetingState.path).split('/')[0] || '', preferences));
  };

  const transcriptOutput = existingOutput(
    `${meetingState.segments.length} timed transcript cues`,
    meetingState.segments.length ? 'ready' : 'empty',
    'View current transcript',
    meetingState.segments.length ? () => openArtifact('TRANSCRIPT', 'Current transcript', renderTranscript) : null,
  );
  const speakerNames = new Set(meetingState.segments.map(segment => segment.speaker).filter(Boolean));
  const speakerOutput = existingOutput(
    speakerNames.size ? `${speakerNames.size} recognized ${speakerNames.size === 1 ? 'speaker' : 'speakers'}` : 'No recognized speakers',
    speakerNames.size ? 'ready' : 'empty',
    'View current result',
    speakerNames.size ? () => openArtifact('SPEAKERS', 'Speaker recognition', renderSpeakerResult) : null,
  );
  const analysisOutput = existingOutput(
    meetingState.summary ? (meetingState.meeting.analysis_stale ? 'AI analysis is outdated' : 'AI analysis is ready') : 'No AI analysis',
    meetingState.summary ? (meetingState.meeting.analysis_stale ? 'stale' : 'ready') : 'empty',
    'View current analysis',
    meetingState.summary ? () => openArtifact('AI ANALYSIS', 'Current analysis', renderSummary) : null,
  );
  const nameEditor = speakerNameEditor({
    path: meetingState.path,
    segments: meetingState.segments,
    notice: meetingState.speakerNotice || '',
    onSaved: (segments, message) => {
      meetingState.segments = segments;
      meetingState.meeting.analysis_stale = true;
      meetingState.speakerNotice = message;
      renderEditor();
    },
  });
  meetingState.speakerNotice = '';
  const tabs = [
    ['content', 'Text & speakers', 'Transcript and speakers', 'Correct individual cues and speaker names. Saving marks the AI analysis as outdated.', [transcriptOutput, transcriptRows, el('div', {className: 'editor-action-row'}, [saveText])]],
    ['transcript', 'Transcript', 'Regenerate transcript', 'Run transcription again with different input parameters. This replaces the transcript and marks the analysis as outdated.', [existingOutput(`${meetingState.segments.length} transcript cues`, meetingState.segments.length ? 'ready' : 'empty', 'View current transcript', meetingState.segments.length ? () => openArtifact('TRANSCRIPT', 'Current transcript', renderTranscript) : null), el('div', {className: 'editor-options'}, [input('Language', language), input('Expected speakers', speakers)]), el('div', {className: 'editor-action-row'}, [regenerateTranscript])]],
    ['speakers', 'Speakers', 'Speaker recognition', 'Name the recognized speakers, or re-label the transcript without running speech recognition again.', [speakerOutput, ...(nameEditor ? [nameEditor] : []), el('div', {className: 'editor-options single'}, [input('Expected speakers', speakerCount)]), el('div', {className: 'editor-action-row'}, [rerunSpeakers])]],
    ['analysis', 'AI analysis', 'AI analysis', 'Choose the shape of this meeting analysis, then run it in the background.', [analysisOutput, el('div', {className: 'editor-options'}, [input('Detail', detail), input('Language', briefLanguage)]), input('Instructions for AI', prompt), sections, el('div', {className: 'editor-action-row'}, [regenerateAnalysis])]],
  ];
  const panels = tabs.map(([id, , title, description, children]) => {
    const panel = editorSection(title, description, children);
    panel.dataset.editorTab = id;
    panel.hidden = id !== meetingState.editorTab;
    return panel;
  });
  const navigation = el('nav', {className: 'editor-tabs', ariaLabel: 'Meeting editing sections'});
  const selectTab = id => {
    meetingState.editorTab = id;
    panels.forEach(panel => { panel.hidden = panel.dataset.editorTab !== id; });
    [...navigation.children].forEach(button => {
      const active = button.dataset.tab === id;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
  };
  tabs.forEach(([id, label]) => {
    const tab = el('button', {className: 'editor-tab', type: 'button', textContent: label});
    tab.dataset.tab = id;
    tab.setAttribute('role', 'tab');
    tab.onclick = () => selectTab(id);
    navigation.append(tab);
  });
  selectTab(meetingState.editorTab);
  body.replaceChildren(progressSlot(), el('div', {className: 'meeting-editor'}, [navigation, ...panels]));
  const busy = isOwnProgress(meetingState.progress || {}) && ['transcribing', 'diarizing', 'merging', 'analyzing'].includes(meetingState.progress.stage);
  if (busy) setOperationButtons(true);
}

function renderCurrentMode() {
  if (!meetingState) return;
  const edit = $('meeting-edit');
  const editing = meetingState.mode === 'edit';
  edit.textContent = editing ? 'Back to analysis' : 'Edit meeting';
  edit.onclick = () => { meetingState.mode = editing ? 'view' : 'edit'; renderCurrentMode(); };
  if (editing) renderEditor(); else renderView();
}

// Where the work ran, by backend id. Meetings recorded before the rename
// stored the old prose ("Pracovní Spark") in `where`; deriving the name here
// keeps every meeting, old or new, on today's wording.
const ENGINE_NAMES = {spark: 'Remote engine', local: 'On this computer'};

function renderMeta(meeting) {
  const asr = meeting.asr || {};
  const diar = meeting.diarization || {};
  const parts = [];
  const where = ENGINE_NAMES[asr.backend || meeting.backend] || null;
  if (where) parts.push(`Transcription: ${where}${asr.model ? ` — ${asr.model}` : ''}`);
  if (diar.applied) parts.push(`Speakers: ${diar.model || 'recognized'}`);
  if (meeting.language) parts.push(`Language: ${meeting.language}`);
  if (meeting.duration) {
    const minutes = Math.round(meeting.duration / 60);
    parts.push(`Duration: ${minutes >= 1 ? `${minutes} min` : `${Math.round(meeting.duration)} s`}`);
  }
  $('meeting-meta').textContent = parts.join(' · ');
}

function highlightActive(audio) {
  const time = audio.currentTime;
  segRows.forEach(row => row.classList.toggle('active', time >= Number(row.dataset.start) && time < Number(row.dataset.end)));
}

export async function openMeeting(path, wavPath, title) {
  progressRequest += 1;
  clearTimeout(progressTimer);
  const audio = $('meeting-audio');
  $('meeting-title').textContent = title;
  audio.src = wavPath ? api.audioUrl(wavPath) : '';
  audio.ontimeupdate = () => highlightActive(audio);
  const [meeting, summary] = await Promise.all([api.readMeeting(path), api.readSummaryJson(path)]);
  if (meeting.error) {
    forgetRecentPath(path);
    window.dispatchEvent(new Event('transcriber:library-changed'));
    alert('This meeting could not be opened: ' + meeting.error);
    location.hash = '#archive';
    return;
  }
  meetingState = {path, wavPath, audio, meeting, segments: meeting.segments || [], summary: summary.error || !hasSummary(summary) ? null : summary, mode: 'view', progress: {}};
  renderMeta(meeting);
  $('meeting-rename').onclick = () => {
    const folder = folderFor(path);
    // A meeting in its own folder takes that folder's name with it, so the
    // reopen path comes from the server's answer, not from the old folder.
    const onSuccess = (newFolder, name) => {
      const prefix = newFolder ? newFolder + '/' : '';
      openMeeting(`${prefix}${name}.txt`, wavPath ? `${prefix}audio/${name}.wav` : null, name);
    };
    window.dispatchEvent(new CustomEvent('transcriber:rename-meeting', {detail: {folder, name: title, wavPath, onSuccess}}));
  };
  renderCurrentMode();
  location.hash = '#meeting';
  showView('meeting');
  refreshProgress();
}

export function closeMeeting() {
  progressRequest += 1;
  clearTimeout(progressTimer);
  syncElapsedTimer(false);
  $('meeting-audio').pause();
  $('meeting-audio').removeAttribute('src');
  $('meeting-artifact-dialog').close();
  meetingState = null;
  $('meeting-body').replaceChildren();
}

$('meeting-back').onclick = () => { location.hash = '#archive'; };
$('meeting-artifact-close').onclick = () => $('meeting-artifact-dialog').close();
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || location.hash !== '#meeting') return;
  if (document.querySelector('dialog[open]')) return;
  const target = event.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) return;
  location.hash = '#archive';
});
