"""Full-library Pass 1 scope, CPU lyric worker and measured reporting.

The library set excludes the 25 pilots and the 300 MVP songs. Their cached
outputs are never reprocessed or modified by this module.
"""
from collections import Counter
from pathlib import Path
from statistics import mean, median
import csv
import datetime as dt
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import unicodedata

from . import core, lyric_backend, retention
from .common import StageError, atomic_write_bytes, digest, read, save

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'library'
GPU_STAGES = ('beats', 'separation', 'transcription', 'routing')
ROUTING_GPU_MIN_PER_SONG = 1.7


def excluded_ids():
    """Pilot and MVP track IDs. Both keep their existing outputs untouched."""
    pilots = {s['track_id'] for s in read(ROOT / 'pilot/selection.json')['songs']}
    mvp = {s['id'] for s in read(ROOT / 'mvp/sample.json')['songs']}
    if len(pilots) != 25 or len(mvp) != 300:
        raise StageError('Expected 25 pilots and 300 MVP songs, found %d and %d' % (len(pilots), len(mvp)))
    return pilots, mvp


def scope_songs(base_songs, pilots, mvp):
    """Pure, auditable library membership filter."""
    return [song for song in base_songs if song['id'] not in pilots and song['id'] not in mvp]


def dataset():
    """Eligible library songs, shortest first, with the v1 profile applied."""
    from .batch import atomic_csv
    FOLDER.mkdir(parents=True, exist_ok=True)
    base = read(ROOT / 'batch/manifest.json')
    pilots, mvp = excluded_ids()
    songs = []
    for song in scope_songs(base['songs'], pilots, mvp):
        if not Path(song['audio_path']).is_file():
            raise StageError('Library song audio is missing: ' + song['id'])
        songs.append({**song, 'beats': {'meter_policy': 'strict'},
                      'rhythm': {'grid_candidates': [16, 24, 32, 48]},
                      'separation': {'keep_stems': ['vocals', 'no_drums']}})
    songs.sort(key=lambda song: (song['duration_s'], song['id']))
    expected = base['batch_count'] - len(mvp)
    if len(songs) != expected:
        raise StageError('Expected %d library songs, found %d' % (expected, len(songs)))
    source = ROOT / 'batch/missing_audio.csv'
    if not source.is_file():
        raise StageError('The batch missing-audio inventory is required')
    atomic_write_bytes(FOLDER / 'missing_audio.csv', source.read_bytes())
    manifest = {'schema_version': 1, 'manifest_count': base['manifest_count'],
                'local_audio_count': base['local_audio_count'], 'pilot_skipped_count': len(pilots),
                'mvp_skipped_count': len(mvp), 'missing_audio_count': base['missing_audio_count'],
                'batch_count': len(songs), 'order': 'duration_s ascending, then track_id',
                'profile': 'v1 (meter policy strict, grids 16/24/32/48, keep vocals and no_drums)',
                'songs': songs}
    save(FOLDER / 'manifest.json', manifest)
    atomic_csv(FOLDER / 'sample.csv', [{k: s.get(k) for k in ('id', 'artist', 'title', 'duration_s')}
                                       for s in songs], ['id', 'artist', 'title', 'duration_s'])
    return songs, manifest


def storage_preflight(song_count, output_root):
    """Require 1.3 times the stated 169 MB/song remaining output estimate."""
    import shutil as _shutil
    progress_path = FOLDER / 'progress.json'
    progress = read(progress_path) if progress_path.exists() else {}
    states = progress.get('songs', {})
    pending = sum(states.get(song['id'], {}).get('status') not in ('done', 'failed', 'skipped')
                  for song in dataset()[0])
    if not states:
        pending = song_count
    free = _shutil.disk_usage(output_root if Path(output_root).exists() else ROOT).free
    bytes_per_song = 169_000_000
    projected = pending * bytes_per_song
    required = projected * 1.3
    record = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'remaining_songs': pending, 'assumed_output_bytes_per_song': bytes_per_song,
        'projected_remaining_bytes': projected, 'multiplier': 1.3,
        'minimum_free_bytes': required, 'free_bytes': free, 'passed': free >= required,
        'projection_source': 'Task B stated approximately 169 MB/song and 120 GB total'}
    save(FOLDER / 'storage_preflight.json', record)
    if not record['passed']:
        raise StageError('Library disk preflight failed: free space is below 1.3x projected remaining output')
    print('Disk preflight: %.1f GB free; %.1f GB projected; %.1f GB minimum required' %
          (free / 1e9, projected / 1e9, required / 1e9), flush=True)
    return record


def _snapshot_verify(snapshot_path, root, include_unlisted=False):
    snapshot = read(snapshot_path)
    expected = snapshot.get('files', {})
    actual = {p.relative_to(root).as_posix(): p for p in Path(root).rglob('*')
              if p.is_file() and p != Path(snapshot_path)}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected)) if include_unlisted else []
    changed = []
    for name, record in expected.items():
        path = actual.get(name)
        if path is None:
            continue
        if path.stat().st_size != record.get('size') or digest(path) != record.get('sha256'):
            changed.append(name)
    return {'total': len(expected), 'unchanged': len(expected) - len(missing) - len(changed),
            'missing': missing, 'extra': extra, 'changed': changed,
            'pass': not (missing or extra or changed)}


def verify_protected_v1():
    """Read-only integrity verification with no writes under analysis_v2/."""
    snapshot_path = ROOT / 'analysis_v2/v1_snapshot.json'
    snapshot = read(snapshot_path)
    records = snapshot['files']
    sample = read(ROOT / 'mvp/sample.json')['songs']
    pilots = read(ROOT / 'pilot/selection.json')['songs']
    ids = {'aespa-attitude', 'meaningful-love'}
    for song in sample + pilots:
        ids.update(str(song[key]) for key in ('id', 'track_id', 'cached_reference') if song.get(key))
    roots = [ROOT / 'mvp', ROOT / 'pilot'] + [ROOT / 'outputs' / sid for sid in sorted(ids)]
    found = {p.relative_to(ROOT).as_posix(): p for base in roots if base.exists()
             for p in base.rglob('*') if p.is_file()}
    for name in ('BATCH_REPORT.md', 'docs/ATTITUDE_RESULTS.md', 'docs/MEANINGFUL_LOVE_RESULTS.md'):
        path = ROOT / name
        if path.exists():
            found[name] = path
    missing = sorted(set(records) - set(found))
    extra = sorted(set(found) - set(records))
    changed = []
    for name, record in records.items():
        path = found.get(name)
        if path and (path.stat().st_size != record['size'] or digest(path) != record['sha256']):
            changed.append(name)
    return {'total': len(records), 'unchanged': len(records) - len(missing) - len(changed),
            'missing': missing, 'extra': extra, 'changed': changed,
            'pass': not (missing or extra or changed)}


def _powercfg_timeout_seconds(output, setting_label):
    """Read one AC timeout from the block for its named power setting."""
    import re
    marker = f'({setting_label})'
    if marker not in output:
        return None
    block = output.split(marker, 1)[1].split('Power Setting GUID:', 1)[0]
    match = re.search(r'Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)', block)
    return int(match.group(1), 16) if match else None


def machine_state():
    power = subprocess.run(['powercfg.exe', '/query', 'SCHEME_CURRENT', 'SUB_SLEEP'],
                           capture_output=True, text=True, timeout=30)
    if power.returncode:
        raise StageError('powercfg.exe /query failed during machine preflight')
    values = {
        'sleep_after_ac_seconds': _powercfg_timeout_seconds(power.stdout, 'Sleep after'),
        'hibernate_after_ac_seconds': _powercfg_timeout_seconds(power.stdout, 'Hibernate after'),
    }
    command = ("$p=@('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired',"
               "'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending');"
               "$r=@($p|Where-Object{Test-Path -LiteralPath $_}).Count -gt 0;"
               "$s=Get-ItemProperty -LiteralPath 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' "
               "-Name PendingFileRenameOperations -ErrorAction SilentlyContinue;"
               "$f=[bool]$s.PendingFileRenameOperations;"
               "ConvertTo-Json -Compress -InputObject @{reboot_required=($r -or $f);"
               "windows_update_or_cbs_reboot_required=$r;pending_file_rename_operations=$f}")
    pending = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                             capture_output=True, text=True, timeout=30)
    if pending.returncode:
        raise StageError('Windows pending-restart check failed')
    values.update(json.loads(pending.stdout))
    values['sleep_change_command'] = 'powercfg.exe /change standby-timeout-ac 0'
    return values


def preflight(songs, output_root):
    """All stop-gates and read-only snapshot checks before any stage begins."""
    report = (ROOT / 'analysis_v2/REPORT.md').read_text(encoding='utf-8')
    diagnosis = (ROOT / 'analysis_v2/TIMEOUT_DIAGNOSIS.md').read_text(encoding='utf-8')
    integrity = read(ROOT / 'analysis_v2/v1_integrity.json')
    if not integrity.get('pass') or integrity.get('unchanged') != integrity.get('total'):
        raise StageError('Task A v1 integrity check failed; library stages were not started')
    if 'Deviation: pilot cohort added' not in report:
        raise StageError('Task A v2 report is not final')
    if 'Proposed fix' not in diagnosis or 'not applied' not in diagnosis:
        raise StageError('Timeout diagnosis is missing or incomplete')
    v1 = verify_protected_v1()
    if not v1['pass']:
        raise StageError('Protected v1 files changed before the library run')
    mvp325 = _snapshot_verify(ROOT / 'mvp_325/snapshot.json', ROOT / 'mvp_325',
                             include_unlisted=True)
    if not mvp325['pass']:
        raise StageError('Protected mvp_325 files changed before the library run')
    from huggingface_hub import get_token, whoami
    from .genius import _access_token
    from .genius_queue import request as genius_request
    token = get_token()
    if not token:
        raise StageError('Hugging Face authentication is missing')
    try:
        whoami(token=token)
    except Exception as error:
        raise StageError('Hugging Face authentication check failed: ' + type(error).__name__) from error
    genius_token = _access_token()
    if not genius_token:
        raise StageError('Genius access token is missing')
    try:
        account = genius_request('/account', {}, genius_token)
        if not account.get('user'):
            raise StageError('Genius account endpoint did not return an authenticated user')
    except Exception as error:
        raise StageError('Genius authentication check failed: ' + type(error).__name__) from error
    gpu = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=30)
    if gpu.returncode:
        raise StageError('nvidia-smi failed during environment preflight')
    machine = machine_state()
    disk = storage_preflight(len(songs), output_root)
    row = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'python_executable': sys.executable, 'python_imports': {
            'muscriptor': True, 'beat_this': True, 'madmom': True, 'huggingface_hub': True},
        'hugging_face_authenticated': True, 'genius_authenticated': True,
        'genius_token_printed': False, 'nvidia_smi': 'passed',
        'gpu': gpu.stdout.splitlines()[2:6], 'disk': disk, 'machine': machine,
        'task_a_v1_integrity': integrity, 'protected_v1_snapshot': v1,
        'protected_mvp_325_snapshot': mvp325,
        'timeout_fix': 'chunk timing and token-count heartbeat logging; 45-minute retry only after a library transcription timeout',
        'timeout_diagnosis': 'confirmed cumulative deadline can cut transcription short after tuning, beats and separation',
        'stages_started': False}
    save(FOLDER / 'preflight.json', row)
    print(json.dumps({'environment': 'passed', 'hf_auth': True, 'genius_auth': True,
        'nvidia_smi': 'passed', 'v1_integrity': '%d/%d' % (v1['unchanged'], v1['total']),
        'mvp_325_files': len(mvp325['extra']) + len(mvp325['missing']) + len(mvp325['changed']),
        'sleep_after_ac_seconds': machine['sleep_after_ac_seconds'],
        'hibernate_after_ac_seconds': machine['hibernate_after_ac_seconds'],
        'pending_restart': machine['reboot_required'],
        'sleep_change_command': machine['sleep_change_command'] if machine['sleep_after_ac_seconds'] else None,
        'disk_free_gb': round(disk['free_bytes'] / 1e9, 1),
        'projected_remaining_gb': round(disk['projected_remaining_bytes'] / 1e9, 1),
        'minimum_free_gb': round(disk['minimum_free_bytes'] / 1e9, 1)}, sort_keys=True), flush=True)
    return row


def text_language(text, identifier):
    import re
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    letters = [c for c in text if c.isalpha()]
    counts = Counter()
    for char in letters:
        name = unicodedata.name(char, '')
        if 'HANGUL' in name:
            counts['hangul'] += 1
        elif 'HIRAGANA' in name or 'KATAKANA' in name:
            counts['kana'] += 1
        elif 'CJK UNIFIED' in name:
            counts['han'] += 1
        else:
            counts['other'] += 1
    language, confidence = identifier.classify(text[:20000])
    original, winning = language, float(confidence)
    n = max(1, len(letters))
    script = None
    if counts['hangul'] / n >= .05:
        script = 'ko'
    elif counts['kana'] >= 2 and counts['kana'] / n >= .02:
        script = 'ja'
    elif counts['han'] / n >= .05:
        script = 'ja' if language == 'ja' else 'zh'
    if script:
        language = script
        if script != original:
            confidence = dict(identifier.rank(text[:20000])).get(script, 0.)
    return language, float(confidence), {
        'script_counts': dict(counts), 'langid_language': original, 'script_choice': script,
        'langid_winning_probability': winning, 'confidence_definition_version': 2,
        'confidence_basis': 'langid normalized probability for selected language, not calibrated'}


def language_text(track_id, folder):
    path = folder / 'lrclib.json'
    if path.exists():
        value = read(path)
        text = value.get('plainLyrics') or value.get('plain_lyrics') or value.get('syncedLyrics') or value.get('synced_lyrics')
        if text and text.strip():
            return text, 'lrclib'
    from . import genius
    for path in (ROOT / 'data/genius_html' / (track_id + '.html'), folder / 'genius.html'):
        if path.exists():
            try:
                sections = genius.parse_html(path.read_text(encoding='utf-8'))
                return '\n'.join(line for section in sections for line in section['lines']), 'genius'
            except Exception:
                continue
    return None, None


_language_identifier = None
_whisper_model = None


def detect_language(song, folder, lyrics):
    """Task A's text/script ID, or its explicit CPU Whisper stem fallback."""
    global _language_identifier, _whisper_model
    target = folder / 'language_detection.json'
    if target.exists():
        return read(target)
    if lyrics.get('source') == 'instrumental' or song.get('instrumental') is True:
        result = {'track_id': song['id'], 'language': 'instrumental',
                  'method': 'explicit_v1_instrumental', 'confidence': None}
    else:
        try:
            if _language_identifier is None:
                from langid.langid import LanguageIdentifier, model
                _language_identifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)
            text, source = language_text(song['id'], folder)
            if text:
                language, confidence, evidence = text_language(text, _language_identifier)
                result = {'track_id': song['id'], 'language': language,
                          'method': 'unicode_scripts+langid_1.1.6', 'confidence': confidence,
                          'text_source': source, **evidence}
            else:
                separation = core.data(folder / 'separation.json')
                stem = separation.get('stems', {}).get('vocals')
                if not stem or not Path(stem).is_file():
                    raise StageError('Task A language ID has no cached lyrics or vocal stem')
                import torch
                import whisper
                torch.set_num_threads(2)
                if _whisper_model is None:
                    _whisper_model = whisper.load_model('base', device='cpu',
                        download_root=str(FOLDER / 'model_cache' / 'whisper'))
                audio = whisper.load_audio(stem)[:30 * 16000]
                audio = whisper.pad_or_trim(audio)
                mel = whisper.log_mel_spectrogram(audio, n_mels=_whisper_model.dims.n_mels)
                _, probabilities = _whisper_model.detect_language(mel)
                language = max(probabilities, key=probabilities.get)
                result = {'track_id': song['id'], 'language': language,
                    'method': 'whisper_base_cpu_vocal_first30s', 'confidence': float(probabilities[language]),
                    'top_languages': sorted(probabilities.items(), key=lambda pair: -pair[1])[:5],
                    'confidence_basis': 'Whisper language-token posterior, not calibrated'}
        except Exception as error:
            result = {'track_id': song['id'], 'language': 'unknown', 'method': 'unresolved',
                      'confidence': None, 'error': type(error).__name__ + ': ' + str(error)}
    save(target, result)
    return result


def prepare():
    """Build the library manifest and refresh earlier non-MVP caches on CPU."""
    from .batch import new_progress, write_progress
    songs, manifest = dataset()
    pilots, mvp = excluded_ids()
    previous = read(ROOT / 'batch/progress.json')
    prior_states = previous.get('songs', {})
    stale = [s for s in songs if prior_states.get(s['id'], {}).get('status') == 'done']
    ids = {s['id'] for s in songs}
    if len(stale) != 60:
        raise StageError('Expected 60 earlier completed non-MVP songs; found %d' % len(stale))
    progress_path = FOLDER / 'progress.json'
    if progress_path.exists():
        raise StageError('library/progress.json already exists; use the resumable library commands')
    progress = new_progress(manifest, songs, 'cuda', ROOT / 'outputs')
    progress['status'] = 'prepared'
    progress['prior_completed_non_mvp_count'] = len(stale)
    old_ids = {song['id'] for song in read(ROOT / 'batch/manifest.json')['songs']}
    if not ids <= old_ids:
        raise StageError('Library manifest includes IDs absent from the earlier stopped batch')
    log_events = []
    log_path = ROOT / 'batch/run_log.jsonl'
    if log_path.exists():
        for line in log_path.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (event.get('song_id') in {s['id'] for s in stale}
                    and event.get('stage') in GPU_STAGES and event.get('status') != 'cached'
                    and event.get('pass', 1) == 1):
                log_events.append(event)
    progress['gpu_seconds_pass1'] = sum(float(e.get('seconds', 0)) for e in log_events)
    audit = []
    configs = {s['id']: s for s in songs}
    for index, song in enumerate(stale, 1):
        sid = song['id']
        folder = ROOT / 'outputs' / sid
        trans = folder / 'transcription.json'
        midi = folder / 'transcription.mid'
        beat_path = folder / 'beats.json'
        if not trans.is_file() or not midi.is_file() or not beat_path.is_file():
            raise StageError('Earlier completed song lacks a cached beat/transcription/MIDI artifact: ' + sid)
        trans_hash, midi_hash = digest(trans), digest(midi)
        beat_data = core.data(beat_path)
        both_grids = bool(beat_data.get('raw_grid') and beat_data.get('dbn_grid'))
        lyrics_path = folder / 'lyrics.json'
        lyrical = core.data(lyrics_path) if lyrics_path.is_file() else {'source': None}
        detection = detect_language(song, folder, lyrical)
        if lyrics_path.is_file() and lyrical.get('status') != 'failed':
            record = read(lyrics_path)
            payload = record.get('data', record)
            payload['language'] = detection.get('language', 'unknown')
            if 'data' in record:
                record['data'] = payload
            else:
                record = payload
            save(lyrics_path, record)
        song['language'] = detection.get('language', 'unknown')
        core.recompute(song, refresh_beats=not both_grids, evaluate_grids=False,
                       use_routing=False, baseline_shuffles=0)
        record = read(folder / 'report.json')
        data = record.get('data', record)
        data['language'] = song['language']
        if 'data' in record:
            record['data'] = data
        else:
            record = data
        save(folder / 'report.json', record)
        retention.apply(folder, song['separation']['keep_stems'])
        if digest(trans) != trans_hash or digest(midi) != midi_hash:
            raise StageError('Cached transcription or MIDI changed during CPU refresh: ' + sid)
        progress['songs'][sid] = {**prior_states[sid], 'status': 'done',
            'duration_s': song['duration_s'], 'last_stage': 'rhythm',
            'result_status': data.get('status', 'ok'), 'prior_batch_reused': True,
            'pass2_status': 'not_run_on_hold'}
        audit.append({'track_id': sid, 'title': song['title'],
            'transcription_action': 'reuse; hash verified',
            'transcription_sha256': trans_hash, 'midi_sha256': midi_hash,
            'beats_action': 'reuse both grids' if both_grids else 'CPU refresh to add raw and DBN grids',
            'downstream_action': 'recompute quantize, layers, patterns, rhythm and join from cached notes',
            'routing_action': 'not run; Pass 2 on hold',
            'language': detection.get('language', 'unknown'),
            'audit_reason': 'older output provenance cannot prove current v1 downstream settings'})
        if index % 10 == 0:
            save(progress_path, progress)
            print('Refreshed %d/%d earlier non-MVP songs on CPU' % (index, len(stale)), flush=True)
    write_progress(progress_path, progress)
    from .batch import atomic_csv
    atomic_csv(FOLDER / 'stale_cache_audit.csv', audit, list(audit[0]))
    write_report()
    print(json.dumps({'eligible_songs': len(songs), 'earlier_non_mvp_done': len(stale),
                      'new_pass1_songs': len(songs)-len(stale),
                      'cpu_recomputed_downstream_stages': len(stale)*5,
                      'preserved_transcriptions': len(stale)}, sort_keys=True), flush=True)
    return progress


def finish():
    """Wait for the CPU worker's final lyric and join pass."""
    path = FOLDER / 'lyrics_progress.json'
    pid_path = FOLDER / 'lyrics.pid'
    while not path.exists() or read(path).get('status') != 'complete':
        if path.exists() and str(read(path).get('status', '')).startswith('stopped'):
            raise StageError('Library CPU lyric worker stopped: ' + str(read(path).get('stop_reason')))
        if not pid_path.exists():
            raise StageError('Library CPU lyric worker PID is missing before completion')
        pid = int(pid_path.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError as error:
            raise StageError('Library CPU lyric worker exited before completion') from error
        state = Path('/proc/%d/stat' % pid)
        if state.exists() and state.read_text().split()[2] == 'Z':
            raise StageError('Library CPU lyric worker exited unexpectedly')
        if (FOLDER / 'stop.requested').exists():
            raise StageError('Library CPU lyric worker received a stop request')
        time.sleep(2)
    save(FOLDER / 'lyrics_finalized.json', {'status': 'complete',
        'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')})
    v1 = verify_protected_v1()
    mvp325 = _snapshot_verify(ROOT / 'mvp_325/snapshot.json', ROOT / 'mvp_325',
                             include_unlisted=True)
    result = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
              'v1': v1, 'mvp_325': mvp325,
              'pass': v1['pass'] and mvp325['pass']}
    save(FOLDER / 'integrity_final.json', result)
    if not result['pass']:
        raise StageError('Final protected v1 or mvp_325 integrity verification failed')
    write_report()


def worker():
    """CPU lyric pass: LRCLIB, queued Genius, CPU WhisperX, vocal activity."""
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    from .mvp import cpu_failure_stop_reason
    songs = dataset()[0]
    progress_file = FOLDER / 'lyrics_progress.json'
    states = read(progress_file).get('songs', {}) if progress_file.exists() else {}
    while True:
        changed = False
        for song in songs:
            tid = song['id']
            out = ROOT / 'outputs' / tid
            existing = states.get(tid, {})
            if existing.get('status') in ('complete', 'failed'):
                continue
            if existing.get('status') == 'pending_genius' and existing.get('retry_at', 0) > time.time():
                continue
            if not (out / 'beats.json').exists() or not (out / 'stems/vocals.wav').exists():
                continue
            try:
                result = lyric_backend.run(song, out, core.data(out / 'beats.json'),
                                           {'vocals': str(out / 'stems/vocals.wav')})
                detected = detect_language(song, out, result)
                record = read(out / 'lyrics.json')
                payload = record.get('data', record)
                payload['language'] = detected.get('language', 'unknown')
                if 'data' in record:
                    record['data'] = payload
                else:
                    record = payload
                save(out / 'lyrics.json', record)
                states[tid] = {'status': result['status'], 'source': result['source'],
                               'language': detected.get('language', 'unknown'),
                               'language_method': detected.get('method'), 'updated_at': time.time()}
                if result['status'] == 'pending_genius':
                    states[tid]['retry_at'] = time.time() + 300
            except Exception as error:
                states[tid] = {'status': 'failed', 'failure_category': 'lyrics_cpu',
                               'error': type(error).__name__ + ': ' + str(error),
                               'updated_at': time.time()}
            save(progress_file, {'songs': states, 'updated_at': time.time()})
            changed = True
            reason = cpu_failure_stop_reason(states)
            if reason:
                save(progress_file, {'status': 'stopped_repeated_error', 'stop_reason': reason,
                                     'songs': states, 'updated_at': time.time()})
                atomic_write_bytes(FOLDER / 'stop.requested', reason.encode())
                return
        batch = read(FOLDER / 'progress.json') if (FOLDER / 'progress.json').exists() else {}
        for song in songs:
            tid = song['id']
            state = batch.get('songs', {}).get(tid, {})
            out = ROOT / 'outputs' / tid
            if state.get('status') != 'done' or states.get(tid, {}).get('status') != 'complete':
                continue
            if (out / 'retention.json').exists():
                continue
            try:
                # Pass 2 routing is on hold, so joins finalize from the full mix.
                core.recompute(song, refresh_beats=False, evaluate_grids=False,
                               use_routing=False, baseline_shuffles=0)
                retention.apply(out, song['separation']['keep_stems'])
            except Exception as error:
                states[tid] = {'status': 'failed', 'failure_category': 'lyrics_or_retention_finalization',
                               'error': type(error).__name__ + ': ' + str(error)}
                save(progress_file, {'songs': states, 'updated_at': time.time()})
        terminal = (bool(batch.get('songs')) and
                    all(s.get('status') in ('done', 'failed', 'skipped') for s in batch['songs'].values()))
        if terminal:
            break
        if (FOLDER / 'stop.requested').exists():
            save(progress_file, {'status': 'stopped', 'songs': states, 'updated_at': time.time()})
            return
        if batch.get('status', 'running') not in ('running', 'starting', 'finalizing_cpu_lyrics_and_join'):
            break
        time.sleep(5 if not changed else 1)
    save(progress_file, {'status': 'complete', 'songs': states, 'updated_at': time.time()})


def start_lyrics():
    pid_file = FOLDER / 'lyrics.pid'
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text())
            cmdline = Path('/proc/%d/cmdline' % pid)
            if cmdline.exists() and b'pipeline.library' in cmdline.read_bytes():
                os.kill(pid, 0)
                return
        except (ProcessLookupError, ValueError):
            pass
    FOLDER.mkdir(parents=True, exist_ok=True)
    log = (FOLDER / 'lyrics.log').open('ab', buffering=0)
    process = subprocess.Popen(['/home/usr/venvs/midi/bin/python', '-m', 'pipeline.library', 'lyrics-worker'],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               start_new_session=True,
                               env={**os.environ, 'CUDA_VISIBLE_DEVICES': '', 'OMP_NUM_THREADS': '4',
                                    'MKL_NUM_THREADS': '4'})
    log.close()
    atomic_write_bytes(pid_file, str(process.pid).encode())


def stop_lyrics():
    path = FOLDER / 'lyrics.pid'
    if path.exists():
        try:
            pid = int(path.read_text())
            cmdline = Path('/proc/%d/cmdline' % pid)
            if cmdline.exists() and b'pipeline.library' in cmdline.read_bytes():
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
        path.unlink(missing_ok=True)


def pace_warning(events, songs, window=20, limit=2.5):
    """Rolling transcription pace over the most recent freshly computed songs."""
    durations = {song['id']: float(song['duration_s']) for song in songs}
    relevant = [e for e in events if e.get('stage') == 'transcription'
                and e.get('status') in ('complete', 'failed')
                and e.get('song_id') in durations]
    successful = []
    seen = set()
    completed_ids = {e['song_id'] for e in relevant if e.get('status') == 'complete'}
    for event in reversed(relevant):
        sid = event['song_id']
        if sid in completed_ids and sid not in seen:
            successful.append(sid)
            seen.add(sid)
        if len(successful) == window:
            break
    recent_ids = list(reversed(successful))
    if len(recent_ids) < window:
        return None
    recent = [e for e in relevant if e.get('song_id') in set(recent_ids)]
    audio = sum(durations[sid] for sid in recent_ids)
    seconds = sum(float(e.get('seconds', 0)) for e in recent)
    if not audio:
        return None
    ratio = seconds / audio
    if ratio > limit:
        return {'window_songs': window, 'seconds_per_audio_second': ratio, 'limit': limit,
                'last_song_id': recent_ids[-1],
                'note': 'Transcription pace warning only; the run continues.'}
    return None


def check_transcription_pace(events, songs):
    warning = pace_warning(events, songs)
    if warning:
        path = FOLDER / 'pace_warnings.json'
        history = read(path).get('warnings', []) if path.exists() else []
        if not any(row.get('last_song_id') == warning['last_song_id'] for row in history):
            history.append(warning)
            save(path, {'warnings': history})
        return warning
    return None


def run_events():
    log = FOLDER / 'run_log.jsonl'
    events = []
    if log.exists():
        for line in log.read_text(encoding='utf-8').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def routing_candidates(songs, progress):
    """Songs whose full-mix transcription emitted acoustic_piano. Pass 2 is on hold."""
    from .batch import atomic_csv
    rows = []
    for song in songs:
        out = ROOT / 'outputs' / song['id']
        path = out / 'transcription.json'
        if progress.get('songs', {}).get(song['id'], {}).get('status') != 'done' or not path.exists():
            continue
        notes = core.data(path).get('notes', [])
        piano = sum(1 for n in notes if n.get('instrument') == 'acoustic_piano')
        if piano:
            rows.append({'track_id': song['id'], 'artist': song['artist'], 'title': song['title'],
                         'duration_s': round(float(song['duration_s']), 3),
                         'acoustic_piano_note_count': piano,
                         'trigger': 'full_mix MuScriptor emitted acoustic_piano notes',
                         'projected_routing_gpu_minutes': ROUTING_GPU_MIN_PER_SONG,
                         'status': 'not_run; Pass 2 on hold pending user decision'})
    atomic_csv(FOLDER / 'routing_candidates.csv', rows,
               ['track_id', 'artist', 'title', 'duration_s', 'acoustic_piano_note_count',
                'trigger', 'projected_routing_gpu_minutes', 'status'])
    return rows


def summary_fields():
    with (ROOT / 'mvp/summary.csv').open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream).fieldnames)


def write_report():
    if not (FOLDER / 'progress.json').exists():
        return
    from .batch import atomic_csv
    progress = read(FOLDER / 'progress.json')
    songs = dataset()[0]
    by_id = {s['id']: s for s in songs}
    lyric_progress = (read(FOLDER / 'lyrics_progress.json').get('songs', {})
                      if (FOLDER / 'lyrics_progress.json').exists() else {})
    fields = summary_fields()
    rows, bytes_used = [], []
    for song in songs:
        tid = song['id']
        state = progress['songs'].get(tid, {})
        out = ROOT / 'outputs' / tid
        report = read(out / 'report.json') if (out / 'report.json').exists() else {}
        auto = report.get('auto_choice', {})
        stats = report.get('instrument_quantization', [])
        boundary = report.get('boundary_evaluations', {}).get('raw', {})
        auto_boundary = report.get('boundary_evaluations', {}).get('auto', {})
        status = state.get('status', 'pending')
        if status == 'done':
            status = report.get('status', 'ok')
        lyric_state = lyric_progress.get(tid, {})
        if state.get('status') == 'done' and lyric_state.get('status') == 'pending_genius':
            status = 'pending_genius'
        failure = state.get('failure_category') or (
            lyric_state.get('failure_category') if lyric_state.get('status') == 'failed' else '')
        values = {'track_id': tid, 'title': song['title'], 'status': status,
                  'failure_category': failure or '',
                  'tuning_offset_cents': report.get('tuning', {}).get('offset_cents'),
                  'raw_irregular_bars': auto.get('raw_irregular_bars'),
                  'dbn_irregular_bars': auto.get('dbn_irregular_bars'),
                  'auto_would_choose': auto.get('chosen_grid'), 'bpm': report.get('estimated_bpm'),
                  'grid_steps_per_instrument': json.dumps(report.get('instrument_steps_per_bar', {}),
                                                          ensure_ascii=False) if report else '',
                  'median_within_30ms_share': median(s['within_30ms_share'] for s in stats) if stats else None,
                  'instrument_pattern_metrics': json.dumps(report.get('instrument_pattern_metrics', {}),
                                                           ensure_ascii=False) if report else '',
                  'rhythmic_sections': len(report.get('rhythmic_sections', [])) if report else None,
                  'ring1_source': report.get('ring1_source') or lyric_state.get('source'),
                  'sections_validated': report.get('sections_validated'),
                  'sections_total': report.get('sections_total'),
                  'language': report.get('language') or lyric_state.get('language') or 'unknown',
                  'boundary_agreement': boundary.get('observed_share_within_one_bar'),
                  'baseline_mean': boundary.get('baseline_mean_share'),
                  'p': boundary.get('one_sided_empirical_p'),
                  'auto_boundary_agreement': auto_boundary.get('observed_share_within_one_bar'),
                  'auto_baseline_mean': auto_boundary.get('baseline_mean_share'),
                  'auto_p': auto_boundary.get('one_sided_empirical_p'),
                  'error': state.get('error') or lyric_state.get('error') or ''}
        rows.append({k: values.get(k) for k in fields})
        if state.get('status') == 'done':
            bytes_used.append(sum(p.stat().st_size for p in out.rglob('*') if p.is_file()))
    atomic_csv(FOLDER / 'summary.csv', rows, fields)
    events = run_events()
    gpu = Counter()
    for event in events:
        if event.get('stage') in GPU_STAGES and event.get('status') != 'cached':
            gpu[event.get('pass', 1)] += event.get('seconds', 0)
    gpu[1] = float(progress.get('gpu_seconds_pass1', gpu[1]))
    gpu[2] = float(progress.get('gpu_seconds_pass2', gpu[2]))
    warning = pace_warning(events, songs)
    warning_history = (read(FOLDER / 'pace_warnings.json').get('warnings', [])
                       if (FOLDER / 'pace_warnings.json').exists() else [])
    integrity = read(FOLDER / 'integrity_final.json') if (FOLDER / 'integrity_final.json').exists() else None
    candidates = routing_candidates(songs, progress)
    counts = Counter(row['status'] for row in rows)
    categories = Counter(row['failure_category'] for row in rows if row['failure_category'])
    finished = ('ok', 'ok_with_warning', 'pending_genius')
    done = sum(1 for row in rows if row['status'] in finished)
    failed = sum(1 for row in rows if row['status'] == 'failed')
    remaining = len(rows) - done - failed
    ratio = progress.get('measured_seconds_per_audio_second')
    remaining_audio = sum(float(by_id[row['track_id']]['duration_s']) for row in rows
                          if row['status'] not in finished and row['status'] != 'failed')
    eta = remaining_audio * ratio / 3600 if ratio else None
    ring1 = Counter(row['ring1_source'] or 'pending' for row in rows if row['status'] in finished)
    languages = Counter(row['language'] or 'unknown' for row in rows if row['status'] in finished)
    validated = sum(int(row['sections_validated'] or 0) for row in rows)
    sections = sum(int(row['sections_total'] or 0) for row in rows)
    disk_gb = sum(bytes_used) / 1e9 if bytes_used else 0.0
    per_song_mb = mean(bytes_used) / 1e6 if bytes_used else None
    measured_gpu_hours = gpu[1] / 3600
    eta_text = ('%.2f hours' % eta) if eta is not None else 'collecting measured pace'
    disk_text = ''
    if per_song_mb:
        disk_text = ('; mean %.0f MB/song; projected %.0f GB for all %d'
                     % (per_song_mb, per_song_mb * len(rows) / 1000, len(rows)))
    validation_text = (' (%.1f%%)' % (100 * validated / sections)) if sections else ''
    status_line = progress.get('status')
    heading = ('Pass 1 is finished for this set.' if status_line == 'complete'
               else 'This report refreshes during the run; it is not a completion claim while songs remain.')
    pace_text = json.dumps(warning) if warning else 'none (rolling 20-song pace within 2.5 s per audio second)'
    failures = '\n'.join('- ' + row['track_id'] + ' - ' + row['failure_category'] + ': ' + str(row['error'])
                         for row in rows if row['failure_category']) or 'None recorded.'
    document = (
        '# Full-library Pass 1 (v1 profile)\n\n'
        'Status: **' + str(status_line) + '**. ' + heading + '\n'
        'Pass 2 routing is **on hold** and was not run.\n\n'
        'Scope: ' + str(len(rows)) + ' eligible songs. The 25 pilots and 300 MVP songs are skipped '
        'and their outputs are untouched.\n'
        'Songs with no local audio: see [missing_audio.csv](missing_audio.csv).\n\n'
        '## Progress\n\n'
        '- Done: **' + str(done) + '**; failed: **' + str(failed) + '**; remaining: **' + str(remaining) + '**.\n'
        '- Status counts: ' + json.dumps(dict(counts), sort_keys=True) + '\n'
        '- Measured ETA for remaining Pass 1 work: ' + eta_text + '.\n'
        '- Measured GPU-stage hours: Pass 1 **%.2f**; Pass 2 **%.2f** (routing on hold).\n'
        % (measured_gpu_hours, gpu[2] / 3600) +
        '- The user projection for this set was about 64 h; measured so far: **%.2f h**.\n' % measured_gpu_hours +
        '- Disk used by completed songs: **%.1f GB**' % disk_gb + disk_text + '.\n'
        '- Transcription pace warning: ' +
        (json.dumps(warning_history[-1]) + ' (historical warning; the run continued)'
         if warning_history else pace_text) + '.\n'
        '- Pace-warning events recorded: ' + str(len(warning_history)) + '.\n\n'
        '## Lyrics and language\n\n'
        '- Ring-1 source mix: ' + json.dumps(dict(ring1), sort_keys=True) + '\n'
        '- Language mix: ' + json.dumps(dict(languages), sort_keys=True) + '\n'
        '- Sections validated / total: **' + str(validated) + '/' + str(sections) + '**' + validation_text + '.\n\n'
        '## Protected output integrity\n\n'
        + ('- v1: **%d/%d unchanged**; mvp_325: **%d files verified** (pass).\n\n' %
           (integrity['v1']['unchanged'], integrity['v1']['total'],
            integrity['mvp_325']['total'])
           if integrity and integrity.get('pass') else
           '- Final v1 and mvp_325 snapshot verification will be recorded when Pass 1 finalizes.\n\n')
        + '## Failures by category\n\n'
        + json.dumps(dict(categories), sort_keys=True) + '\n\n'
        + failures + '\n\n'
        '## Pass 2 routing candidates (not run)\n\n'
        + str(len(candidates)) + ' completed songs emitted acoustic_piano on the full mix and would '
        'trigger routing, projected at about **%.2f GPU hours**. '
        % (len(candidates) * ROUTING_GPU_MIN_PER_SONG / 60) +
        'See [routing_candidates.csv](routing_candidates.csv).\n\n'
        'Flat song data: [summary.csv](summary.csv). Progress state: [progress.json](progress.json).\n')
    atomic_write_bytes(FOLDER / 'FULL_RUN_REPORT.md', document.encode('utf-8'))


if __name__ == '__main__':
    if sys.argv[1:] == ['lyrics-worker']:
        worker()
    elif sys.argv[1:] == ['report']:
        write_report()
    elif sys.argv[1:] == ['dataset']:
        print(json.dumps({'songs': len(dataset()[0])}))
    else:
        raise SystemExit('Use lyrics-worker, report or dataset')

