"""Explicit model choice; MuScriptor is always the default."""
from .muscriptor import MuScriptorBackend
from .yourmt3 import YourMT3Backend


def create(name="muscriptor", **kwargs):
    if name == "muscriptor":
        return MuScriptorBackend(device=kwargs.get("device", "cpu"),
                                 model=kwargs.get("model", "medium"))
    if name == "yourmt3":
        return YourMT3Backend(device=kwargs.get("device", "cpu"),
                              python=kwargs.get("yourmt3_python"),
                              root=kwargs.get("yourmt3_root", "output/amt/yourmt3"))
    raise ValueError(f"Unknown backend: {name}; no fallback")
