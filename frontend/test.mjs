import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCSV, buildCatalog, buildSong, songAngle } from './model.mjs';
import { TimelinePlayer } from './player.mjs';

const fixture = () => ({
  song: { id: 'test', title: 'Test only', artist: 'Test fixture', duration: 100, summary: { status: 'ok' } },
  files: {
    report: { schema_version: 'core-mvp/1', ring1_source: 'lrclib', lyric_sections: [{ display_label: 'Unlabeled 1', label: 'Unlabeled', labels: 'unverified', raw_start_s: 7, raw_end_s: 20 }], rhythmic_sections: [{ rhythmic_section_id: 'r1', start_s: 0, end_s: 100, start_bar: 0, end_bar: 3, active_pattern_ids: ['p'] }], patterns: [{ instrument: 'voice__line' }] },
    beats: { data: { chosen_grid: 'raw', bars: [{ bar: 0, start_s: 0, end_s: 4, beats_per_bar: 4 }, { bar: 1, start_s: 4, end_s: 8, beats_per_bar: 4 }, { bar: 2, start_s: 8, end_s: 12, beats_per_bar: 4 }] } },
    lyrics: { data: {} },
    patterns: { data: { patterns: [{ pattern_id: 'p', instrument: 'piano__line', mini_notation: '0 4' }], runs: [{ pattern_id: 'p', start_bar: 0, end_bar: 3 }], bars: [0, 1, 2].flatMap(bar => [{ bar, instrument: 'piano__line', pattern_id: 'p', pattern_type: 'regular_bar', bar_root_pitch: bar === 2 ? 62 : 60, active: true }, { bar, instrument: 'voice__line', pattern_id: `voice${bar}`, active: true }]) } }
  }
});
test('CSV honors quoted commas, newlines, Unicode, and JSON strings', () => {
  assert.deepEqual(parseCSV('\uFEFFid,title,value\r\na,"hi,\n世界","{""x"": 2}"\r\n'), [{ id: 'a', title: 'hi,\n世界', value: '{"x": 2}' }]);
  assert.throws(() => parseCSV('id,title\na,"broken'), /unterminated/);
  assert.throws(() => buildCatalog('id,artist,title,duration_s\na,A,T,10', 'track_id,grid_steps_per_instrument,instrument_pattern_metrics\na,broken,', 1), /Invalid grid/);
});
test('outer ring keeps raw, unverified labels and explicitly unassigned gaps', () => {
  const { song, files } = fixture(); const outer = buildSong(song, files).rings[0];
  assert.deepEqual(outer.segments.map(s => [s.label, s.start, s.end]), [['Unassigned timing', 0, 7], ['Vocal segment 1', 7, 20], ['Unassigned timing', 20, 100]]);
  assert.equal(outer.segments[1].labelUnverified, true);
});
test('inner ring presents section windows, not per-bar windows', () => {
  const { song, files } = fixture(); const result = buildSong(song, files);
  const inner = result.rings[2].segments.filter(s => !s.unassigned);
  assert.deepEqual(inner.map(s => [s.start, s.end]), [[0, 100]]);
  assert.equal(result.rings[1].hasVoice, true);
});
test('unavailable patterns, wrong grid, and out-of-recording endpoints are explicit', () => {
  const { song, files } = fixture(); files.patterns.data.patterns = []; files.patterns.data.runs = [];
  assert.match(buildSong(song, files).rings[2].error, /0 pattern definitions and 0 runs/);
  files.beats.data.chosen_grid = 'auto'; assert.match(buildSong(song, files).rings[1].error, /not the required v1 raw grid/);
  files.report.lyric_sections[0].raw_end_s = 110;
  const section = buildSong(song, files).rings[0].segments.find(s => !s.unassigned);
  assert.equal(section.clipped, true); assert.equal(section.sourceEnd, 110); assert.equal(section.end, 100);
});
class Clock extends EventTarget {
  paused = true; readyState = 1; currentTime = 0; duration = 100;
  load() {} pause() { this.paused = true; this.dispatchEvent(new Event('pause')); }
  async play() { this.paused = false; this.dispatchEvent(new Event('play')); }
}
test('real player uses absolute song time, bounded skips/seeking, pause, and exact window end', async () => {
  const audio = new Clock(); let state; const failures = [];
  const player = new TimelinePlayer(audio, s => { state = s; }, e => failures.push(e), { request: () => 1, cancel: () => {} });
  player.load({ id: 'test', duration: 100 }); player.select({ start: 30, end: 50 });
  assert.equal(state.position, 30); assert.equal(songAngle(state.position, state.duration), 108);
  await player.play(); audio.currentTime = 35; player.tick();
  assert.equal(state.position, 35); assert.ok(Math.abs(songAngle(state.position, state.duration) - 126) < 1e-9);
  player.skip(-5); assert.equal(audio.currentTime, 30);
  player.seek(90); assert.equal(state.position, 50); assert.equal(audio.paused, true);
  await player.play(); assert.equal(audio.currentTime, 30);
  audio.currentTime = 33.125; player.pause(); assert.equal(state.position, 33.125);
  await player.play(); assert.equal(audio.currentTime, 33.125);
  audio.currentTime = 50.01; player.tick(); assert.equal(audio.currentTime, 50); assert.equal(state.position, 50); assert.equal(state.playing, false);
  player.select(null); assert.equal(state.position, 0); assert.equal(state.window, null);
  assert.deepEqual(failures, []);
});
