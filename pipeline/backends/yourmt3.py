"""Comparison-only YourMT3+, run in an explicitly chosen compatible interpreter."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from ..common import ModelUnavailable, Note


class YourMT3Backend:
    def __init__(self, device="cpu", python=None, root="output/amt/yourmt3"):
        self.device, self.python, self.root = device, python or sys.executable, str(Path(root).resolve())
        self.warnings = []

    def transcribe(self, audio_path, hints=None):
        if hints:
            message = "YourMT3+ comparison does not support instrument conditioning; hints were not applied."
            print(f"WARNING: {message}", flush=True)
            self.warnings.append(message)
        with tempfile.TemporaryDirectory(prefix="yourmt3-comparison-") as folder:
            output = Path(folder)
            command = [self.python, "-m", "pipeline.backends.yourmt3_runner",
                       "--audio", str(Path(audio_path).resolve()), "--root", self.root,
                       "--device", self.device, "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode:
                raise ModelUnavailable("YourMT3+ comparison failed: " + result.stderr[-1200:])
            payload = json.loads((output / "notes.json").read_text())
            self.warnings.extend(payload.get("warnings", []))
            return [Note(**n) for n in payload["notes"]]
