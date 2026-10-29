const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const node = id => {
  if (!elements.has(id)) elements.set(id, { textContent: '', disabled: false });
  return elements.get(id);
};
const saved = new Map();
let ticker, fetches = 0;
const context = vm.createContext({
  EventTarget, Event, URL, AbortController, Map, Date, setTimeout, clearTimeout,
  setInterval: fn => { ticker = fn; },
  document: { querySelector: node, getElementById: id => node('#' + id), addEventListener() {} },
  localStorage: { getItem: k => saved.get(k) || null, setItem: (k, v) => saved.set(k, v), removeItem: k => saved.delete(k) },
  fetch: async () => { fetches++; return { ok: true, json: async () => ({ videoId: 'LQ_be1KSonc', title: 'Test', channel: 'Artist' }) }; }
});
vm.runInContext(fs.readFileSync('public/video-bridge.js', 'utf8') + '\nthis.Bridge=VideoAudioBridge;', context);
(async () => {
  const audio = new context.Bridge({ addEventListener() {}, pause() {}, load() {} });
  let position = 10, paused = 0, ended = 0;
  audio.player = { getCurrentTime: () => position, seekTo: n => position = n, getDuration: () => 120, pauseVideo: () => paused++ };
  audio.readTrim('LQ_be1KSonc');
  audio.setBoundary('start');
  assert.equal(audio.trim.start, 10);
  position = 20;
  audio.setBoundary('end');
  audio.readTrim('LQ_be1KSonc');
  assert.equal(audio.trim.start, 10);
  assert.equal(audio.trim.end, 20);
  audio.currentTime = 0;
  assert.equal(position, 10);
  assert.equal(audio.elapsedSinceStart, 0, 'Previous uses elapsed time inside the selected range');
  position = 14;
  assert.equal(audio.elapsedSinceStart, 4);
  position = 25;
  audio.setBoundary('start');
  assert.equal(audio.trim.start, 10, 'Invalid reversed interval rejected');
  audio.addEventListener('ended', () => ended++);
  audio._paused = false;
  position = 21;
  ticker(); ticker();
  assert.equal(ended, 1);
  assert.equal(paused, 1);
  audio.currentTime = 0;
  assert.equal(audio.trimEnded, false);
  assert.equal(position, 10);
  audio.readTrim('abcdefghijk');
  assert.equal(audio.trim.start, 0);
  await audio.resolve('https://example.org/api/v1/tracks/dz-1/stream');
  await audio.resolve('https://example.org/api/v1/tracks/dz-1/stream');
  assert.equal(fetches, 1);
  let resolveOld;
  const loads = [];
  const raced = new context.Bridge({ addEventListener() {}, pause() {}, load() {} });
  raced.player = { loadVideoById: v => loads.push(v.videoId), unMute() {}, pauseVideo() {}, getCurrentTime: () => 0, playVideo: () => loads.push('play') };
  raced.ensurePlayer = async () => raced.player;
  raced.resolve = () => new Promise(resolve => { resolveOld = resolve; });
  raced.src = 'https://example.org/api/v1/tracks/dz-2/stream';
  const automatic = raced.play();
  raced.manualId = 'abcdefghijk';
  await raced.play();
  resolveOld({ videoId: 'LQ_be1KSonc', title: 'Old', channel: 'Old' });
  await automatic;
  assert.deepEqual(loads, ['abcdefghijk'], 'Late automatic match cannot replace manual choice');
  assert.equal(node('#video-meta').textContent, 'Выбранное видео · Запись выбрана тобой');
  let ready;
  raced.ensurePlayer = () => new Promise(resolve => { ready = resolve; });
  const resume = raced.play();
  raced.pause();
  ready(raced.player);
  await resume;
  assert.deepEqual(loads, ['abcdefghijk'], 'Pause cancels a pending resume');
  console.log('PASS: boundaries, previous, cache, late-search race, pause during pending resume');
})();
