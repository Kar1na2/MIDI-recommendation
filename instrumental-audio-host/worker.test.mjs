import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './worker.mjs';

const id = '7FT7a5DV7Mk6Mc9ZRJnX70';
const url = `https://audio.example/v1/${id}/bass.wav`;
const object = (extra = {}) => ({
  size: 10, httpEtag: '"abc"',
  writeHttpMetadata(headers) { headers.set('Content-Type', 'audio/wav'); },
  ...extra,
});

test('serves a byte-range as a stream with correct headers', async () => {
  const env = { AUDIO: { async get(key, options) {
    assert.equal(key, `songs/${id}/bass.wav`);
    assert.equal(options.range.get('Range'), 'bytes=2-4');
    return object({ range: { offset: 2, length: 3 }, body: new Uint8Array([2, 3, 4]) });
  } } };
  const response = await worker.fetch(new Request(url, { headers: { Range: 'bytes=2-4' } }), env);
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('Content-Range'), 'bytes 2-4/10');
  assert.equal(response.headers.get('Content-Length'), '3');
  assert.equal(response.headers.get('Content-Type'), 'audio/wav');
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [2, 3, 4]);
});

test('HEAD returns metadata without a body', async () => {
  const env = { AUDIO: { async head() { return object(); } } };
  const response = await worker.fetch(new Request(url, { method: 'HEAD' }), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Length'), '10');
  assert.equal((await response.arrayBuffer()).byteLength, 0);
});

test('does not expose writes or bucket listing', async () => {
  assert.equal((await worker.fetch(new Request(url, { method: 'POST' }), {})).status, 405);
  assert.equal((await worker.fetch(new Request('https://audio.example/'), {})).status, 404);
  assert.equal((await worker.fetch(new Request(`https://audio.example/v1/${id}/vocals.wav`), {})).status, 404);
});

test('OPTIONS exposes the Range headers needed by browser playback', async () => {
  const response = await worker.fetch(new Request(url, { method: 'OPTIONS' }), {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('Access-Control-Allow-Origin'), '*');
  assert.match(response.headers.get('Access-Control-Expose-Headers'), /Content-Range/);
});
