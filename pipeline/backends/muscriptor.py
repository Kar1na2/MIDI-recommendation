"""MuScriptor raw event API; instrument hints condition decoding directly."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sys
import time
import warnings

from ..common import ModelUnavailable, Note, StageError, atomic_path


@contextmanager
def logged_generation(model, emit):
    """Timestamp raw decoder progress while preserving the generated token stream."""
    original = model.generate
    chunk_index = 0

    def wrapped(*args, **kwargs):
        nonlocal chunk_index
        index = chunk_index
        chunk_index += 1
        started = time.monotonic()
        count = 0
        emit({'event': 'chunk_start', 'chunk': index, 'seek_s': index * 5.0})
        try:
            for token in original(*args, **kwargs):
                count += 1
                if count % 100 == 0:
                    emit({'event': 'chunk_heartbeat', 'chunk': index,
                          'tokens_emitted': count, 'elapsed_s': time.monotonic() - started})
                yield token
        except BaseException as error:
            emit({'event': 'chunk_end', 'chunk': index, 'tokens_emitted': count,
                  'elapsed_s': time.monotonic() - started, 'outcome': 'error',
                  'error_type': type(error).__name__})
            raise
        else:
            emit({'event': 'chunk_end', 'chunk': index, 'tokens_emitted': count,
                  'elapsed_s': time.monotonic() - started, 'outcome': 'decoder_returned'})

    model.generate = wrapped
    try:
        yield
    finally:
        model.generate = original


class MuScriptorBackend:
    def __init__(self, device="cpu", model="medium"):
        self.device, self.variant = device, model
        self.model = None
        self.warnings = []
        self.preflight = {}

    def load(self):
        if self.model is None:
            try:
                import torch
                from huggingface_hub import get_token, whoami, try_to_load_from_cache
                from muscriptor import TranscriptionModel
                token = get_token()
                if not token:
                    raise PermissionError("No Hugging Face login or HF_TOKEN is configured")
                whoami(token=token)
                repo = f"MuScriptor/muscriptor-{self.variant}"
                cached = try_to_load_from_cache(repo, "model.safetensors")
                cache_hit = isinstance(cached, str)
                if torch.cuda.is_available() and self.device.startswith("cuda"):
                    free, total = torch.cuda.mem_get_info(torch.device(self.device))
                    gpu = torch.cuda.get_device_name(torch.device(self.device))
                    self.preflight = {"huggingface_authenticated": True,
                                      "checkpoint_cached": cache_hit, "gpu_name": gpu,
                                      "free_vram_bytes": int(free), "total_vram_bytes": int(total)}
                    print(f"MuScriptor preflight: GPU={gpu}; free_VRAM_GiB={free / 1024**3:.2f}; "
                          f"checkpoint_cached={cache_hit}", flush=True)
                else:
                    self.preflight = {"huggingface_authenticated": True,
                                      "checkpoint_cached": cache_hit, "gpu_name": None}
                    print(f"MuScriptor preflight: GPU=unavailable; checkpoint_cached={cache_hit}", flush=True)
                self.model = TranscriptionModel.load_model(self.variant, device=self.device)
            except Exception as error:
                # Avoid echoing token-bearing HTTP diagnostics.
                kind = type(error).__name__
                gated = isinstance(error, PermissionError) or kind in {
                    "GatedRepoError", "RepositoryNotFoundError", "HfHubHTTPError", "HTTPError"}
                if gated:
                    reason = (f"Accept the terms at https://huggingface.co/MuScriptor/"
                              f"muscriptor-{self.variant} and run `hf auth login` (or set HF_TOKEN).")
                elif "OutOfMemory" in kind:
                    reason = (f"MuScriptor {self.variant} ran out of memory while loading or transcribing. "
                              "No smaller model was selected; change transcription.muscriptor_size explicitly.")
                else:
                    reason = (f"MuScriptor {self.variant} could not load ({kind}). "
                              "Check connectivity and installation; no model fallback was attempted.")
                raise ModelUnavailable(
                    f"MuScriptor {self.variant} preflight/load failed ({kind}). {reason}"
                ) from error

    def transcribe(self, audio_path, hints=None, midi_path=None):
        self.warnings = []
        self.chunk_events = []
        try:
            return self._transcribe(audio_path, hints, midi_path, strict_eos=True)
        except Exception as error:
            if "did not emit EOS" not in str(error):
                raise
            import re
            match = re.search(r"chunk (\d+) \(seek=([\d.]+)s\)", str(error))
            affected = [{"chunk": int(match[1]), "start_s": float(match[2])}] if match else []
            self.warnings.append("eos_warning: strict EOS failed; retrying the entire song once without strict EOS: " + str(error))
            notes = self._transcribe(audio_path, hints, midi_path, strict_eos=False)
            for message in self.warnings:
                for chunk, seek in re.findall(r"chunk (\d+) \(seek=([\d.]+)s\)", message):
                    row = {"chunk": int(chunk), "start_s": float(seek)}
                    if row not in affected:
                        affected.append(row)
            self.event_diagnostics.update(eos_warning=True, eos_affected_chunks=affected,
                                          eos_retry_count=1, strict_eos=False)
            return notes

    def _transcribe(self, audio_path, hints=None, midi_path=None, strict_eos=True):
        self.load()
        from muscriptor.events import NoteStartEvent, NoteEndEvent
        from muscriptor.tokenizer.mt3 import resolve_instrument_names
        instruments = resolve_instrument_names(hints) if hints else None
        pending, notes = {}, []
        unpaired_ends = invalid_duration = 0
        def log_event(event):
            row = {'timestamp_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                   'attempt': 'strict' if strict_eos else 'non_strict', **event}
            self.chunk_events.append(row)
            print('MUSCRIPTOR_CHUNK ' + json.dumps(row, sort_keys=True), file=sys.stderr, flush=True)

        with logged_generation(self.model._model, log_event), warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            for event in self.model.transcribe(Path(audio_path), instruments=instruments,
                                               batch_size=1, use_sampling=False,
                                               prelude_forcing=True, no_eos_is_ok=not strict_eos):
                if isinstance(event, NoteStartEvent):
                    pending[event.index] = event
                elif isinstance(event, NoteEndEvent):
                    start = pending.pop(event.start_event.index, None)
                    if start is None:
                        unpaired_ends += 1
                        continue
                    if float(event.end_time) <= float(start.start_time):
                        invalid_duration += 1
                        continue
                    # The model does not estimate calibrated velocities. Keep null.
                    notes.append(Note(float(start.start_time), float(event.end_time),
                                      int(start.pitch), None, str(start.instrument)))
                else:
                    from muscriptor.events import ProgressEvent
                    if isinstance(event, ProgressEvent):
                        width = 24
                        filled = round(width * event.completed / max(1, event.total))
                        print(f"\rMuScriptor [{'#' * filled}{'-' * (width - filled)}] "
                              f"{event.completed}/{event.total}", end="", flush=True)
            print(flush=True)
            self.warnings.extend(str(w.message) for w in captured)
        self.event_diagnostics = {"unpaired_start_events": len(pending),
                                  "unpaired_end_events": unpaired_ends,
                                  "invalid_duration_events": invalid_duration,
                                  "preflight": self.preflight,
                                  "chunk_events": list(getattr(self, "chunk_events", [])),
                                  "instrument_hints_applied": instruments is not None,
                                  "instrument_hints_reason": None if instruments is not None else
                                  ("not configured" if not hints else "backend API does not expose conditioning")}
        if invalid_duration:
            self.warnings.append(f"Discarded {invalid_duration} paired note events with nonpositive duration; no duration was invented.")
        if midi_path is not None:
            self._write_midi(notes, midi_path)
            if notes:
                self.warnings.append(
                    "MIDI serialization uses velocity 100 because MuScriptor events expose no velocity; "
                    "the note JSON retains velocity=null, so 100 is not a model prediction.")
        return sorted(notes, key=lambda n: (n.onset_s, n.instrument, n.pitch))

    @staticmethod
    def _write_midi(notes, path):
        import pretty_midi
        midi = pretty_midi.PrettyMIDI()
        by_instrument = {}
        for note in notes:
            track = by_instrument.setdefault(note.instrument, pretty_midi.Instrument(
                program=0, is_drum="drum" in note.instrument.casefold(), name=note.instrument))
            track.notes.append(pretty_midi.Note(
                velocity=note.velocity if note.velocity is not None else 100,
                pitch=note.pitch, start=note.onset_s, end=note.offset_s))
        midi.instruments.extend(by_instrument.values())
        path = Path(path)
        temporary = atomic_path(path)
        try:
            midi.write(str(temporary))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
