"""
Step 3: Batch convert audio files to MIDI using Spotify's Basic Pitch.

Scans an input directory for audio files (.mp3, .wav, .flac, .ogg, .m4a),
converts each to MIDI, and saves the result to an output directory.

Install:
  pip install basic-pitch

Usage:
  python convert_to_midi.py --input-dir ./audio --output-dir ./midi
  python convert_to_midi.py --input-dir ./audio --output-dir ./midi --skip-existing
  python convert_to_midi.py --input-file song.mp3 --output-dir ./midi

Options:
  --onset-threshold    Note onset sensitivity, 0.0-1.0 (default: 0.5)
                       Lower = more notes detected, higher = stricter
  --frame-threshold    Frame-level confidence, 0.0-1.0 (default: 0.3)
                       Lower = longer sustained notes, higher = tighter
  --min-note-length    Minimum note duration in ms (default: 58)
  --skip-existing      Skip files that already have a .mid in output-dir
"""

import argparse
import sys
import time
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".aac"}


def find_audio_files(input_path: Path) -> list[Path]:
    """Collect all audio files from a directory (non-recursive by default)."""
    if input_path.is_file():
        if input_path.suffix.lower() in AUDIO_EXTENSIONS:
            return [input_path]
        print(f"Unsupported format: {input_path.suffix}")
        return []

    files = sorted(
        f for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )
    return files


def convert_single(
    audio_path: Path,
    output_dir: Path,
    onset_threshold: float,
    frame_threshold: float,
    min_note_len_ms: float,
) -> bool:
    """Convert one audio file to MIDI. Returns True on success."""
    # Lazy import so startup is fast and errors are clear
    try:
        from basic_pitch.inference import predict_and_save
    except ImportError:
        print("basic-pitch is not installed. Run: pip install basic-pitch")
        sys.exit(1)

    midi_name = audio_path.stem + ".mid"
    output_file = output_dir / midi_name

    try:
        predict_and_save(
            audio_path_list=[audio_path],
            output_directory=output_dir,
            save_midi=True,
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=min_note_len_ms,
        )

        # basic_pitch names output as <stem>_basic_pitch.mid
        generated = output_dir / f"{audio_path.stem}_basic_pitch.mid"
        if generated.exists() and generated != output_file:
            generated.rename(output_file)

        return output_file.exists()

    except Exception as e:
        print(f"  ERROR converting {audio_path.name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Batch convert audio files to MIDI via Basic Pitch"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input-dir", type=Path, help="Directory of audio files")
    input_group.add_argument("--input-file", type=Path, help="Single audio file")

    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Directory to save MIDI files",
    )
    parser.add_argument("--onset-threshold", type=float, default=0.5)
    parser.add_argument("--frame-threshold", type=float, default=0.3)
    parser.add_argument("--min-note-length", type=float, default=58.0,
                        help="Minimum note duration in ms")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip if .mid already exists in output dir")
    args = parser.parse_args()

    input_path = args.input_dir or args.input_file
    if not input_path.exists():
        print(f"Input path does not exist: {input_path}")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    audio_files = find_audio_files(input_path)
    if not audio_files:
        print("No audio files found.")
        sys.exit(0)

    print(f"Found {len(audio_files)} audio file(s)\n")

    success = 0
    skipped = 0
    failed = 0

    for i, audio in enumerate(audio_files, 1):
        midi_out = args.output_dir / (audio.stem + ".mid")

        if args.skip_existing and midi_out.exists():
            print(f"[{i}/{len(audio_files)}] SKIP (exists): {audio.name}")
            skipped += 1
            continue

        print(f"[{i}/{len(audio_files)}] Converting: {audio.name} ... ", end="", flush=True)
        start = time.time()

        ok = convert_single(
            audio, args.output_dir,
            args.onset_threshold, args.frame_threshold, args.min_note_length,
        )

        elapsed = time.time() - start
        if ok:
            print(f"done ({elapsed:.1f}s)")
            success += 1
        else:
            failed += 1

    print(f"\nResults: {success} converted, {skipped} skipped, {failed} failed")
    print(f"MIDI files saved to: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()