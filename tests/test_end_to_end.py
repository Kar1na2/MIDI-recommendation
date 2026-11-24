"""Synthetic integration: real files, stage caches, report join, plots and diagnose.

Model outputs are injected from known synthetic notes. This tests engineering
contracts only and is never counted as ATTITUDE transcription acceptance.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pipeline.common import Note, digest, read
from pipeline.runner import Runner
from pipeline.diagnose import run as diagnose
from tests.test_pipeline import grid


class SyntheticBackend:
    warnings = []

    def transcribe(self, audio_path, hints):
        return [Note(bar * 2 + j * .125, bar * 2 + j * .125 + .1,
                     60 + (bar % 3) * 2 + pitch, None, "piano")
                for bar in range(10) for j, pitch in enumerate((0, 4, 7, 12))]


class SyntheticIntegrationTests(unittest.TestCase):
    def test_all_stages_join_and_twenty_second_diagnostic(self):
        import numpy as np
        import soundfile as sf
        with TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / "original.wav"
            rate = 16000
            sf.write(audio, .1 * np.sin(2 * np.pi * 220 * np.arange(20 * rate) / rate), rate)
            stems = root / "results" / "synthetic" / "stems"
            stems.mkdir(parents=True)
            paths = {}
            for part in ("bass", "drums", "other", "vocals"):
                path = stems / f"{part}.wav"
                path.write_bytes(audio.read_bytes())
                paths[part] = str(path)
            manual = root / "sections.csv"
            manual.write_text("label,start_s,end_s\nVerse,0,4\nPre-Chorus,4,8\nChorus,8,20\n")
            html = '<div data-lyrics-container="true">[Verse]<br>Verse text<br>[Pre-Chorus: Person]<br>Build it up<br>[Chorus]<br>Sing it out</div>'
            song = {"id": "synthetic", "artist": "Fixture", "title": "Synthetic integration only",
                    "audio_path": str(audio), "genius_url": "https://genius.com/Fixture-lyrics",
                    "manual_sections_csv": str(manual)}
            runner = Runner(song, root / "results")
            runner.backend = "synthetic"
            runner.model_instance = SyntheticBackend()
            separated = {"stems": paths, "stem_sha256": {k: digest(p) for k, p in paths.items()}}
            raw_grid = grid(20)
            def save_click(_audio, _grid, target):
                Path(target).write_bytes(b"click-fixture")
                return {"path": str(target)}
            genius_rows = [
                {"label": label, "number": number, "performers": [],
                 "genius_section_index": i, "lines": lines}
                for i, (label, number, lines) in enumerate([
                    ("Verse", 1, ["Verse text"]), ("Pre-Chorus", 1, ["Build it up"]),
                    ("Chorus", 1, ["Sing it out"])])]
            with patch("pipeline.beats.run", return_value={"raw": {"beat_times": raw_grid["beat_times"],
                  "downbeat_times": raw_grid["downbeat_times"], "observed_beats_per_bar": raw_grid["observed_beats_per_bar"],
                  "dbn_comparison": {"available": True}}, "raw_grid": raw_grid, "dbn_grid": raw_grid}), \
                 patch("pipeline.beats.write_click", side_effect=save_click), \
                 patch("pipeline.separation.run", return_value=separated), \
                 patch("pipeline.tuning.run", return_value={"transcription_audio_path": str(audio),
                       "source_sha256": "fixture", "offset_cents": 0., "corrected": False}), \
                 patch("pipeline.lyrics.genius.fetch", return_value={"source_url": song["genius_url"],
                   "romanized_only": False, "sections": genius_rows}):
                report = runner.execute()
                self.assertNotEqual(report["status"], "failed")
                self.assertTrue(report["acceptance"]["all_genius_sections_in_order"])
                self.assertTrue(report["acceptance"]["prechorus_chorus_separate_and_contiguous"])
                self.assertTrue(report["acceptance"]["boundaries_snapped_to_downbeats"])
                self.assertTrue((runner.output / "timeline.png").stat().st_size > 1000)
                result = diagnose(runner, 0., 20.)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(len(result["table"]), 5)
                self.assertTrue((Path(result["piano_roll_path"])).stat().st_size > 1000)
                self.assertTrue(read(runner.output / "report.json")["acceptance"]["diagnose_20s"])
                # A rerun genuinely uses cached measured/model stages.
                with patch("pipeline.beats.run", side_effect=AssertionError("cached")), patch.object(runner.model_instance, "transcribe", side_effect=AssertionError("cached")):
                    cached = runner.execute()
                    self.assertNotEqual(cached["status"], "failed")
                    self.assertEqual(diagnose(runner, 0., 20.)["status"], "complete")


if __name__ == "__main__":
    unittest.main()
