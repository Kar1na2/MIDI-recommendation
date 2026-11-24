// Run the actual player logic with a controlled audio clock and a minimal DOM.
// This regression covers the distinction between passage and record progress.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    value: '0', textContent: '', attrs: {}, listeners: {}, paused: true,
    currentTime: 0, readyState: 1,
    classList: {toggle() {}},
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(key, fn) { this.listeners[key] = fn; },
    pause() { this.paused = true; },
  });
  return nodes.get(id);
}
const context = vm.createContext({
  document: {getElementById: node},
  matchMedia: () => ({matches: false, addEventListener() {}}),
  requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  fetch: () => new Promise(() => {}),
  console,
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../preview/rhythm-layers/app.js'), 'utf8'), context);
const run = code => vm.runInContext(code, context);
const angle = () => Number(node('stylus').attrs.transform.match(/rotate\(([^ ]+)/)[1]);
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
run("song = {views: {main: {duration: 300, layers: [{depth: 1, duration: 300, segments: []}]}, other: {duration: 240, layers: [{depth: 1, duration: 240, segments: []}]}}}; clipStart = 60; clipEnd = 90; position = 60;");
run('progress(0)'); near(angle(), 72); assert.equal(node('windowProgress').value, '0');
run('progress(.5)'); near(angle(), 90); assert.equal(node('windowProgress').value, '500');
assert.equal(node('songPosition').textContent, '1:15 / 5:00');
run('progress(1)'); near(angle(), 108);

// A longer passage must travel at the same record speed per second.
run('clipStart = 120; clipEnd = 180; progress(1 / 60)'); near(angle(), 121 / 300 * 360);
run('clipStart = 60; clipEnd = 90; progress(1 / 30)'); near(angle(), 61 / 300 * 360);

// Seeking/restart remain passage-relative but move the needle in song time.
node('windowProgress').value = '500'; node('windowProgress').listeners.input(); near(angle(), 90);
node('restartWindow').listeners.click(); near(angle(), 72); near(node('windowAudio').currentTime, 60);

// Pause freezes the actual clock; reaching the end stops at that arc boundary.
run('audio.paused = false; audio.currentTime = 83; pause()'); near(angle(), 83 / 300 * 360);
assert.equal(node('windowAudio').paused, true);
run('audio.paused = false; audio.currentTime = 90.2; tick()'); near(angle(), 108);
assert.equal(node('windowAudio').paused, true);
assert.equal(node('windowProgress').value, '1000');
near(node('windowAudio').currentTime, 90);

// WAV rings can have a different recording duration; needle and arcs must agree.
run("view = 'other'; clipStart = 30; clipEnd = 42; progress(0)"); near(angle(), 45);
run('progress(1)'); near(angle(), 63);
run('clipStart = 230; clipEnd = 240; progress(1)'); near(angle(), 360);
console.log('Player timeline checks passed: absolute needle, constant speed, seeking, restart, pause, end, and WAV clock.');
