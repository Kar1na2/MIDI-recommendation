"""Fail closed if a forbidden transcription/analysis dependency is requested."""
import importlib.abc
import sys

FORBIDDEN = ("basic_pitch", "scripts.transcription", "scripts.amt", "scripts.rhythm_layers",
             "scripts.analysis", "lib.note_similarity")


def forbidden(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN)


class DependencyPolicy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if forbidden(fullname):
            raise ImportError(f"Forbidden dependency in MIDI-first runtime: {fullname}")


def enforce():
    loaded = [name for name in sys.modules if forbidden(name)]
    if loaded:
        raise ImportError(f"Start a clean interpreter; forbidden dependencies already loaded: {loaded}")
    if not any(isinstance(finder, DependencyPolicy) for finder in sys.meta_path):
        sys.meta_path.insert(0, DependencyPolicy())
