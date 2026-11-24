import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.common import atomic_write_audio, atomic_write_bytes, read
from pipeline.runner import Runner


class BatchIntegrityTests(unittest.TestCase):
    def test_replaced_or_partial_artifact_is_not_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "source.wav"
            audio.write_bytes(b"source identity")
            runner = Runner({"id": "integrity", "artist": "test", "title": "test",
                             "audio_path": str(audio)}, output_root=root / "outputs")
            artifact = runner.output / "artifact.wav"
            calls = []

            def compute():
                calls.append(True)
                atomic_write_bytes(artifact, b"complete artifact")
                return {"value": len(calls)}

            runner.stage("integrity", compute, {"setting": 1}, [artifact])
            runner.stage("integrity", compute, {"setting": 1}, [artifact])
            self.assertEqual(len(calls), 1)
            self.assertTrue(runner.stage_records["integrity"]["cache_hit"])
            self.assertIn("artifact.wav", read(runner.output / "integrity.json")["artifact_sha256"])
            artifact.write_bytes(b"partial replacement")
            runner.stage("integrity", compute, {"setting": 1}, [artifact])
            self.assertEqual(len(calls), 2)
            self.assertEqual(artifact.read_bytes(), b"complete artifact")

    def test_interrupted_audio_writer_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "click.wav"
            target.write_bytes(b"old complete audio")

            def interrupted_write(path, *_args, **_kwargs):
                Path(path).write_bytes(b"partial audio")
                raise RuntimeError("writer interrupted")

            with patch("soundfile.write", interrupted_write):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    atomic_write_audio(target, None, 8000)
            self.assertEqual(target.read_bytes(), b"old complete audio")
            self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
