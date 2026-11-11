import { seekInWindow, segmentBounds } from './model.mjs';

export class TimelinePlayer {
  constructor(audio, changed, failed, frames = { request: f => requestAnimationFrame(f), cancel: n => cancelAnimationFrame(n) }) {
    Object.assign(this, { audio, changed, failed, frames, duration: 0, position: 0, window: null, intent: 0, frame: 0, starting: false, blocked: false });
    audio.addEventListener('loadedmetadata', () => {
      if (Math.abs(audio.duration - this.duration) > 0.35) {
        this.blocked = true; this.pause();
        failed(`The MP3 duration (${audio.duration.toFixed(2)} s) does not match the canonical song duration (${this.duration.toFixed(2)} s). Playback is disabled; the rings have not been rescaled.`);
      } else { audio.currentTime = this.position; this.emit(); }
    });
    audio.addEventListener('pause', () => this.emit());
    audio.addEventListener('play', () => this.emit());
    audio.addEventListener('ended', () => this.finish());
    audio.addEventListener('error', () => { this.blocked = true; this.pause(); failed(`Original MP3 playback failed (media error ${audio.error?.code ?? 'unknown'}). No alternative audio source has been substituted.`); });
    audio.addEventListener('timeupdate', () => { if (!audio.paused && audio.currentTime >= this.end) this.finish(); });
  }
  get bounds() { return segmentBounds(this.duration, this.window); }
  get end() { return this.bounds[1]; }
  emit() { this.changed({ duration: this.duration, position: this.position, window: this.window, playing: !this.audio.paused, starting: this.starting, blocked: this.blocked }); }
  load(song) {
    this.pause(); this.blocked = false; this.duration = song.duration; this.position = 0; this.window = null;
    if (!song.audioUrl) { this.blocked = true; this.audio.removeAttribute('src'); this.emit(); this.failed('Original audio preview is unavailable: this recording is not verified on the audio host.'); return; }
    this.audio.src = song.audioUrl; this.audio.preload = 'metadata'; this.audio.load(); this.emit();
  }
  pause() {
    this.intent++; this.starting = false;
    if (!this.audio.paused && this.duration) this.position = seekInWindow(this.audio.currentTime, this.duration, this.window);
    this.audio.pause(); this.frames.cancel(this.frame); this.frame = 0; this.emit();
  }
  select(section) { this.pause(); this.window = section; this.seek(section ? section.start : 0); }
  seek(time) {
    this.position = seekInWindow(time, this.duration, this.window);
    if (this.audio.readyState >= 1 && !this.blocked) this.audio.currentTime = this.position;
    if (this.position >= this.end && !this.audio.paused) this.finish();
    else this.emit();
  }
  skip(seconds) { this.seek(this.position + seconds); }
  async metadata() {
    if (this.audio.readyState >= 1) return;
    await new Promise((resolve, reject) => {
      const cleanup = () => { clearTimeout(timer); this.audio.removeEventListener('loadedmetadata', loaded); this.audio.removeEventListener('error', error); this.audio.removeEventListener('abort', error); };
      const loaded = () => { cleanup(); resolve(); };
      const error = () => { cleanup(); reject(new Error('The original MP3 could not load. No alternate recording was used.')); };
      const timer = setTimeout(() => { cleanup(); reject(new Error('The original MP3 did not load within 15 seconds. Playback remains unavailable.')); }, 15000);
      this.audio.addEventListener('loadedmetadata', loaded); this.audio.addEventListener('error', error); this.audio.addEventListener('abort', error);
    });
  }
  async play() {
    if (!this.audio.paused || this.starting) { this.pause(); return; }
    if (this.blocked || !this.duration) return;
    const request = ++this.intent; this.starting = true; this.emit();
    try {
      await this.metadata();
      if (request !== this.intent || this.blocked) return;
      if (this.position >= this.end - .02) this.position = this.bounds[0];
      this.audio.currentTime = this.position;
      await this.audio.play();
      if (request !== this.intent) { this.audio.pause(); return; }
      this.tick();
    } catch (error) { if (request === this.intent) { this.pause(); this.failed(error.message); } }
    finally { if (request === this.intent) { this.starting = false; this.emit(); } }
  }
  tick() {
    if (this.audio.paused) return;
    this.position = seekInWindow(this.audio.currentTime, this.duration, this.window);
    if (this.audio.currentTime >= this.end - .005) { this.finish(); return; }
    this.emit(); this.frame = this.frames.request(() => this.tick());
  }
  finish() {
    this.pause(); this.position = this.end;
    if (this.audio.readyState >= 1 && !this.blocked) this.audio.currentTime = Math.min(this.end, this.audio.duration);
    this.emit();
  }
}
