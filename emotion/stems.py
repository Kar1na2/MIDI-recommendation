"""Regenerate no-vocals stems for the registered cohort, with explicit failures."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMOTION = ROOT/'emotion'


def songs():
    with (ROOT/'mvp_325/summary.csv').open(encoding='utf-8-sig', newline='') as stream:
        selected = {row['track_id']: row for row in csv.DictReader(stream)}
    random = json.loads((ROOT/'mvp/sample.json').read_text(encoding='utf-8'))['songs']
    pilot = json.loads((ROOT/'pilot/selection.json').read_text(encoding='utf-8'))['songs']
    sources = {row['track_id']: row for row in random + pilot}
    if set(selected) != set(sources):
        raise RuntimeError('Cohort and audio source manifests do not agree')
    return [{**row, 'audio_path': sources[track]['audio_path']} for track, row in selected.items()]


def library_active():
    listing = subprocess.run(['ps', '-eo', 'args='], check=True, capture_output=True, text=True).stdout
    return any(('pipeline.batch' in line or 'pipeline.library' in line) and 'python' in line
               for line in listing.splitlines())


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def one(song):
    track = song['track_id']
    if not track.isalnum():
        raise ValueError('Unsafe track id')
    audio = Path(song['audio_path']).resolve()
    if not audio.is_relative_to((ROOT/'liked-audio').resolve()) or not audio.is_file():
        raise FileNotFoundError('The recorded local audio path is unavailable or outside liked-audio')
    dest = EMOTION/'stems'/track
    dest.mkdir(parents=True, exist_ok=True)
    output, metadata = dest/'instrumental.flac', dest/'separation.json'
    if output.exists() and metadata.exists():
        existing = json.loads(metadata.read_text(encoding='utf-8'))
        if existing.get('status') == 'complete' and existing.get('instrumental_sha256') == sha256(output):
            return existing
        raise RuntimeError('Existing output does not match successful metadata; refusing overwrite')
    work = EMOTION/'scratch'/'separation'/track
    if work.exists():
        raise RuntimeError('Prior separation scratch exists; inspect before retrying')
    work.mkdir(parents=True)
    started = time.monotonic()
    result = {'track_id': track, 'cohort': song['cohort'], 'audio_path': str(audio),
              'status': 'failed', 'model': 'htdemucs', 'shifts': 0, 'overlap': 0.25,
              'command': 'demucs --two-stems vocals -n htdemucs --shifts 0 --overlap 0.25'}
    env = {**os.environ, 'TORCH_HOME': str(EMOTION/'model_cache'/'torch'), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(EMOTION/'vendor') + os.pathsep + str(ROOT)}
    try:
        with (work/'demucs.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, str(EMOTION/'pinned_demucs.py'), '--two-stems', 'vocals', '-n', 'htdemucs',
                                        '-d', 'cuda', '--shifts', '0', '--overlap', '0.25', '-o', str(work), str(audio)],
                                       cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None:
                if library_active():
                    process.terminate()
                    process.wait(timeout=30)
                    raise RuntimeError('Library processing started; emotion separation stopped and needs queue support')
                if time.monotonic() - started > 3600:
                    process.terminate()
                    process.wait(timeout=30)
                    raise TimeoutError('Demucs exceeded 3600 seconds')
                time.sleep(2)
        if process.returncode:
            raise RuntimeError(f'Demucs failed with exit {process.returncode}; see {work / "demucs.log"}')
        candidates = list(work.rglob('no_vocals.wav'))
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one no_vocals.wav, found {len(candidates)}')
        temporary = dest/'instrumental.tmp.flac'
        process = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(candidates[0]),
                                  '-c:a', 'flac', '-y', str(temporary)], capture_output=True, text=True, timeout=600)
        if process.returncode or not temporary.is_file():
            raise RuntimeError(f'FLAC conversion failed: {process.stderr[-1000:]}')
        import soundfile as sf
        if sf.info(temporary).frames <= 0:
            raise RuntimeError('FLAC output has no samples')
        temporary.replace(output)
        result.update(status='complete', instrumental_sha256=sha256(output),
                      audio_duration_s=sf.info(output).duration, size_bytes=output.stat().st_size)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result['wall_seconds'] = time.monotonic() - started
    metadata.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    if result['status'] == 'complete':
        shutil.rmtree(work)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--song', action='append', help='Optional registered track ID; may be repeated')
    args = parser.parse_args()
    selected = songs()
    if args.song:
        requested = set(args.song)
        selected = [row for row in selected if row['track_id'] in requested]
        if {row['track_id'] for row in selected} != requested:
            raise ValueError('One or more requested songs are outside the registered cohort')
    for row in selected:
        if library_active():
            raise RuntimeError('Library processing detected: no unqueued GPU work started')
        try:
            result = one(row)
        except Exception as exc:
            result = {'track_id': row['track_id'], 'cohort': row['cohort'], 'status': 'failed',
                      'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
