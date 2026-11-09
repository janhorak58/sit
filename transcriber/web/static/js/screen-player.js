// The recorded screen plays muted beside the meeting audio. The audio stays
// the clock: the video follows its play, pause, seeks and speed.
import * as api from './api.js';
import {$} from './dom.js';

// Below this the two players are left alone: constant seeking would stutter.
const MAX_DRIFT = 0.3;

let offset = 0;
let request = 0;

function follow() {
  const audio = $('meeting-audio');
  const video = $('meeting-screen');
  if (video.hidden || !video.src) return;
  const time = audio.currentTime - offset;
  const outside = time < 0 || (Number.isFinite(video.duration) && time >= video.duration);
  if (outside) {
    // The screen was not recorded yet (or any more) at this point of the audio.
    if (!video.paused) video.pause();
    const edge = time < 0 ? 0 : video.duration;
    if (Math.abs(video.currentTime - edge) > MAX_DRIFT) video.currentTime = edge;
    return;
  }
  if (Math.abs(video.currentTime - time) > MAX_DRIFT) video.currentTime = time;
  video.playbackRate = audio.playbackRate;
  if (audio.paused && !video.paused) video.pause();
  if (!audio.paused && video.paused) video.play().catch(() => {});
}

export async function loadScreen(wavPath) {
  const video = $('meeting-screen');
  const ticket = ++request;
  clearScreen();
  if (!wavPath) return;
  const info = await api.screenInfo(wavPath);
  if (ticket !== request || info.error || !info.url) return;
  offset = info.offset || 0;
  // Cache-busted: a trim rewrites the file under the same URL.
  video.src = info.url + '&v=' + Date.now();
  video.hidden = false;
}

export function clearScreen() {
  const video = $('meeting-screen');
  video.pause();
  video.removeAttribute('src');
  video.load();
  video.hidden = true;
  offset = 0;
}

const audio = $('meeting-audio');
for (const name of ['play', 'pause', 'seeked', 'ratechange', 'timeupdate']) audio.addEventListener(name, follow);
$('meeting-screen').addEventListener('loadedmetadata', follow);
$('meeting-screen').addEventListener('dblclick', event => {
  const video = event.currentTarget;
  if (document.fullscreenElement) document.exitFullscreen();
  else video.requestFullscreen?.();
});
