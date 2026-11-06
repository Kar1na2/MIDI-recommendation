"""Experimental per-stem alternatives. Writes new outputs; never replaces corpus MIDI.

Piano: ByteDance note+pedal model. Bass: monophonic pYIN. Drums: ADTOF.
Requires an isolated environment; see the experiment report for installation.
All exported times retain the input recording's timeline, including --start.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import contextlib
import time

import numpy as np
import pretty_midi
import soundfile as sf
from scipy.signal import resample_poly


def shift_midi(midi, seconds):
    for inst in midi.instruments:
        for note in inst.notes:
            note.start += seconds
            note.end += seconds
        for event in inst.control_changes + inst.pitch_bends:
            event.time += seconds


def bass_pyin(audio, sr, start):
    import librosa
    hop = 160
    f0, voiced, probability = librosa.pyin(
        audio, sr=sr, fmin=27.5, fmax=440, frame_length=2048,
        hop_length=hop, fill_na=np.nan,
    )
    midi_pitch = np.full(len(f0), -1, dtype=int)
    valid = np.isfinite(f0) & voiced & (probability >= .2)
    midi_pitch[valid] = np.rint(librosa.hz_to_midi(f0[valid])).astype(int)
    # Pitch tracking is not note detection: retain unvoiced gaps; do not invent
    # retriggers from a stable sustained pitch. This can miss repeated notes.
    midi = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(33, name='bass_pyin')
    first = 0
    for last in range(1, len(midi_pitch) + 1):
        if last < len(midi_pitch) and midi_pitch[last] == midi_pitch[first]:
            continue
        onset, offset = first * hop / sr, min(last * hop / sr, len(audio) / sr)
        pitch = int(midi_pitch[first])
        if 0 <= pitch <= 127 and offset-onset >= .05:
            inst.notes.append(pretty_midi.Note(85, pitch, start+onset, start+offset))
        first = last
    midi.instruments.append(inst)
    return midi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--engine', choices=['piano', 'pyin', 'adtof'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--duration', type=float, default=None)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.start < 0 or (args.duration is not None and args.duration <= 0):
        parser.error('start must be nonnegative and duration positive')
    if args.output.exists():
        parser.error('Output already exists; choose a new output path to preserve prior runs')
    if args.engine == 'piano' and (not args.checkpoint or not args.checkpoint.is_file()):
        parser.error('Piano requires a previously downloaded --checkpoint file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    info = sf.info(args.audio)
    stop = None if args.duration is None else int((args.start+args.duration)*info.samplerate)
    audio, sr = sf.read(args.audio, start=int(args.start*info.samplerate), stop=stop, dtype='float32', always_2d=True)
    audio = audio.mean(axis=1)
    if len(audio) == 0:
        parser.error('The requested window is empty')
    target_sr = 16000
    divisor = math.gcd(sr, target_sr)
    audio = resample_poly(audio, target_sr//divisor, sr//divisor).astype(np.float32)
    elapsed = time.monotonic()
    print(f'{args.engine}: {args.audio.name}, {args.start:.3f}-{args.start+len(audio)/target_sr:.3f}s', flush=True)
    extra = {}
    if args.engine == 'piano':
        import torch
        from piano_transcription_inference import PianoTranscription
        torch.set_num_threads(args.threads)
        model = PianoTranscription(checkpoint_path=str(args.checkpoint), device='cpu')
        result = model.transcribe(audio, None)
        from piano_transcription_inference.utilities import write_events_to_midi
        write_events_to_midi(0, result['est_note_events'], result['est_pedal_events'], str(args.output))
        midi = pretty_midi.PrettyMIDI(str(args.output))
        shift_midi(midi, args.start)
        with args.checkpoint.open('rb') as checkpoint_file:
            checkpoint_hash = hashlib.file_digest(checkpoint_file, 'sha256').hexdigest()
        extra = {'pedal_events': len(result['est_pedal_events']),
                 'checkpoint_sha256': checkpoint_hash}
    elif args.engine == 'pyin':
        midi = bass_pyin(audio, target_sr, args.start)
    else:
        import torch
        from adtof_pytorch import transcribe_to_midi
        torch.set_num_threads(args.threads)
        excerpt = args.output.with_suffix('.input.wav')
        try:
            sf.write(excerpt, audio, target_sr)
            transcribe_to_midi(str(excerpt), str(args.output), device='cpu')
            midi = pretty_midi.PrettyMIDI(str(args.output))
            shift_midi(midi, args.start)
        finally:
            with contextlib.suppress(FileNotFoundError):
                excerpt.unlink()
    midi.write(str(args.output))
    rows = sorted((n.start, n.end, n.pitch, pretty_midi.note_number_to_name(n.pitch), n.velocity)
                  for inst in midi.instruments for n in inst.notes)
    with args.output.with_suffix('.events.csv').open('w', newline='', encoding='utf-8') as fp:
        w = csv.writer(fp)
        w.writerow(['onset', 'offset', 'pitch', 'name', 'velocity'])
        w.writerows(rows)
    versions = {}
    for name in ['numpy', 'librosa', 'torch', 'pretty-midi', 'piano-transcription-inference', 'adtof-pytorch']:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    meta = {'input': str(args.audio), 'engine': args.engine, 'source_start': args.start,
            'duration': len(audio)/target_sr, 'absolute_timestamps': True,
            'note_count': len(rows), 'runtime_seconds': time.monotonic()-elapsed,
            'versions': versions, **extra}
    args.output.with_suffix('.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    print(json.dumps(meta, indent=2), flush=True)


if __name__ == '__main__':
    main()
