// Self-check for meeting.js groupSegments: `node tests/group_segments.mjs`.
// Sourced from the real file so the check cannot drift from the shipped code.
import assert from 'assert';
import {readFileSync} from 'fs';
import {dirname, join} from 'path';
import {fileURLToPath} from 'url';

const file = join(dirname(fileURLToPath(import.meta.url)), '../transcriber/web/static/js/meeting.js');
const src = readFileSync(file, 'utf8');
const body = src.slice(src.indexOf('const PAUSE_BREAK'), src.indexOf('function renderTranscript'));
const groupSegments = new Function(body + '; return groupSegments;')();

assert.deepStrictEqual(
  groupSegments([
    {start: 0, end: 2, speaker: null, text: ' A.'},
    {start: 2, end: 4, speaker: null, text: ' B.'},
    {start: 10, end: 12, speaker: null, text: ' Po pauze.'},
    {start: 12, end: 14, speaker: 'Jan', text: ' Jiný mluvčí.'},
  ]).map(b => [b.start, b.end, b.speaker, b.text]),
  [[0, 4, null, 'A. B.'], [10, 12, null, 'Po pauze.'], [12, 14, 'Jan', 'Jiný mluvčí.']],
);

const many = Array.from({length: 200}, (_, i) => ({start: i, end: i + 1, speaker: null, text: 'x'.repeat(20)}));
assert.ok(groupSegments(many).length > 1, 'paragraphs must break, not grow forever');

console.log('groupSegments ok');
