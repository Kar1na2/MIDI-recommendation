"""Ordered stage execution, fingerprinted caches and failure reports."""
from dataclasses import asdict
import inspect
import importlib.metadata
from pathlib import Path

from . import beats, separation, quantize, patterns, lyrics, join, plots, tuning, layers, routing
from .backends import create
from .common import ModelUnavailable, StageError, digest, fingerprint, read, save, warning

STAGES = ("tuning", "beats", "separation", "transcription", "quantize", "layers",
          "patterns", "routing", "lyrics", "join")


class Runner:
    def __init__(self, song, output_root="outputs", force=(), device="cpu", backend="muscriptor",
                 steps=None, threshold=.5, confidence=.65, model=None, meter_policy=None,
                 onset_distance=.25, pitch_distance=2, contour_distance=1,
                 section_window=2, min_section_bars=4,
                 boundary_shuffles=1000,
                 yourmt3_python=None, yourmt3_root="output/amt/yourmt3", whisperx_python=None):
        self.song = song
        self.output = Path(output_root).resolve() / song["id"]
        self.output.mkdir(parents=True, exist_ok=True)
        self.force, self.device, self.backend = set(force), device, backend
        self.steps = steps if steps is not None else song.get("rhythm", {}).get("steps_per_bar", 16)
        if self.steps not in (16, 24, 32, 48):
            raise ValueError("rhythm.steps_per_bar must be 16, 24, 32, or 48")
        self.step_candidates = song.get("rhythm", {}).get("grid_candidates",
            None if 'steps_per_bar' in song.get('rhythm', {}) else [16, 24, 32, 48])
        self.grid_fit_tolerance_ms = float(song.get("rhythm", {}).get("grid_fit_tolerance_ms", 5.0))
        if self.step_candidates is not None and (not self.step_candidates or
                any(value not in (16, 24, 32, 48) for value in self.step_candidates)):
            raise ValueError("rhythm.grid_candidates must be a nonempty list from 16/24/32/48")
        self.threshold, self.confidence = threshold, confidence
        self.onset_distance, self.pitch_distance = onset_distance, pitch_distance
        self.contour_distance = contour_distance
        self.section_window, self.min_section_bars = section_window, min_section_bars
        self.boundary_shuffles = boundary_shuffles
        self.model = model or song.get("transcription", {}).get("muscriptor_size", "large")
        self.meter_policy = meter_policy or song.get("beats", {}).get("meter_policy", "strict")
        self.yourmt3_python, self.yourmt3_root = yourmt3_python, yourmt3_root
        self.whisperx_python = whisperx_python
        self.stage_records, self.warnings = {}, []
        self.model_instance = None
        self.source_hash = digest(song["audio_path"]) if Path(song["audio_path"]).is_file() else None
        self.transcription_audio_path = Path(song["audio_path"])
        self.code_hash = fingerprint({p.relative_to(Path(__file__).parent).as_posix(): digest(p)
                                      for p in Path(__file__).parent.rglob("*.py")})
        self.code_hash = fingerprint({"code": self.code_hash,
                                      "lock": digest(Path(__file__).parents[1] / "uv.lock")})

    def stage(self, name, function, inputs, artifacts=None):
        path = self.output / f"{name}.json"
        source_map = {
            "tuning": ("tuning.py", "common.py"),
            "beats": ("beats.py", "common.py"),
            "separation": ("separation.py", "common.py"),
            "transcription": ("backends/__init__.py", "backends/muscriptor.py" if self.backend == "muscriptor"
                              else "backends/yourmt3.py"),
            "quantize": ("quantize.py", "beats.py", "common.py"),
            "layers": ("layers.py", "patterns.py", "common.py"),
            "patterns": ("patterns.py", "reference.py", "period.py", "config.py", "note_metrics.py", "common.py"),
            "routing": ("routing.py", "separation.py", "common.py"),
            "lyrics": ("lyrics.py", "genius.py", "lrclib.py", "line_alignment.py", "separation.py", "common.py"),
            "join": ("join.py", "common.py"),
        }
        relevant = {name: digest(Path(__file__).parent / name)
                    for name in source_map.get(name, ("common.py",))}
        if name == "transcription":
            # General runner and report changes do not alter the inference call.
            relevant["runner_transcribe"] = fingerprint(inspect.getsource(type(self).transcribe))
            transcribe_code_hash = relevant["runner_transcribe"]
        # The separator code and its exact settings are separately fingerprinted;
        # unrelated lyric dependency additions must not rerun htdemucs.
        dependencies = None if name == "separation" else digest(Path(__file__).parents[1] / "uv.lock")
        key = fingerprint({"schema": 2, "code": relevant, "dependencies": dependencies,
                           "source": self.source_hash, "inputs": inputs})
        cached = read(path) if path.exists() else None
        def artifact_paths(data):
            selected = artifacts(data) if callable(artifacts) else artifacts
            return [Path(value).resolve() for value in (selected or [])]

        def artifact_key(value):
            try:
                return value.relative_to(self.output).as_posix()
            except ValueError:
                return str(value)

        cached_paths = artifact_paths(cached.get("data") if cached else None)
        cached_hashes = cached.get("artifact_sha256", {}) if cached else {}
        valid_artifacts = all(
            value.is_file() and cached_hashes.get(artifact_key(value)) == digest(value)
            for value in cached_paths
        ) and (not cached_paths or bool(cached_hashes))
        # The EOS change only affects failed decoding. Verified completed full-mix
        # inferences remain reusable; preparation records the original batch's
        # source hash, and MIDI hashes must still agree.
        if (name == 'transcription' and cached and cached.get('source_sha256') == self.source_hash
                and cached.get('status') in ('complete', 'ok_with_warning') and valid_artifacts
                and cached.get('data', {}).get('model') == self.model
                and cached['data'].get('input') == 'full_mix'
                and cached['data'].get('instrument_hints') == self.song.get('instrument_hints')
                and cached['data'].get('notes') and cached_paths):
            cached['cache_key'] = key
            cached['cache_migration_reason'] = 'verified source and MIDI; successful strict decode unchanged by EOS retry policy'
            save(path, cached)
        if (name == "transcription" and cached and cached.get("status") == "complete"
                and cached.get("data", {}).get("backend") == "muscriptor"
                and cached["data"].get("midi_path") and "midi_serialization" not in cached["data"]):
            cached["data"]["midi_serialization"] = {
                "velocity_default_if_unavailable": 100, "program_default_if_unavailable": 0,
                "defaults_are_not_model_predictions": True}
            note = ("MIDI serialization uses velocity 100 because MuScriptor events expose no velocity; "
                    "the note JSON retains velocity=null, so 100 is not a model prediction.")
            cached["data"].setdefault("warnings", []).append(note)
            cached.setdefault("warnings", []).append(note)
            save(path, cached)
        if (name == "separation" and cached and cached.get("status") == "complete"
                and cached.get("schema_version") == 1 and valid_artifacts
                and cached.get("data", {}).get("model") == "htdemucs"
                and cached["data"].get("shifts") == 0
                and cached["data"].get("overlap") == .25):
            prior_report = self.output / "report.json"
            prior_source = read(prior_report).get("source_sha256") if prior_report.exists() else None
            hashes_match = all(digest(p) == cached["data"].get("stem_sha256", {}).get(Path(p).stem)
                               for p in artifacts or [])
            if prior_source == self.source_hash and hashes_match:
                cached["data"]["source_sha256"] = self.source_hash
                cached["cache_key"] = key
                save(path, cached)
        can_migrate_transcription = bool(name == "transcription" and cached
                and "input_audio_sha256" not in inputs
                and cached.get("status") == "complete"
                and cached.get("schema_version") == 1
                and cached.get("data", {}).get("backend") == "muscriptor"
                and cached["data"].get("model") == self.model
                and cached["data"].get("input") == "full_mix"
                and cached["data"].get("instrument_hints") == self.song.get("instrument_hints")
                and cached["data"].get("notes")
                and Path(cached["data"].get("midi_path", "")).is_file()
                and (cached.get("transcribe_code_hash") == transcribe_code_hash
                     or (cached.get("transcribe_code_hash") is None and cached.get("cache_migrated")
                         and cached.get("cache_migrated_code_hash") == cached.get("cache_key"))))
        if can_migrate_transcription:
            prior_report = self.output / "report.json"
            prior_source = read(prior_report).get("source_sha256") if prior_report.exists() else None
            if prior_source == self.source_hash:
                cached["cache_key"] = key
                cached["cache_migrated"] = True
                cached["cache_migrated_code_hash"] = key
                cached["transcribe_code_hash"] = transcribe_code_hash
                save(path, cached)
        if cached and cached.get("cache_key") == key and name not in self.force and valid_artifacts:
            self.stage_records[name] = {**cached, "cache_hit": True}
            print(f"{self.song['id']}: {name} cached ({cached['status']})", flush=True)
            for message in cached.get("warnings", []):
                warning(message, self.warnings)
            if cached["status"] == "failed":
                error = ModelUnavailable if cached.get("model_unavailable") else StageError
                raise error(cached["reason"] + f" (cached; retry with --force {name})")
            return cached["data"]
        print(f"{self.song['id']}: {name} running", flush=True)
        try:
            data = function()
            produced_paths = artifact_paths(data)
            missing = [str(value) for value in produced_paths if not value.is_file()]
            if missing:
                raise StageError(f"Stage {name} completed without required artifacts: {missing}")
            record = {"schema_version": 1, "stage": name, "status": "ok_with_warning" if data.get("eos_warning") else "complete",
                      "cache_key": key, "warnings": data.get("warnings", []), "data": data}
            if produced_paths:
                record["artifact_sha256"] = {artifact_key(value): digest(value)
                                                for value in produced_paths}
            if name == "transcription":
                record["transcribe_code_hash"] = transcribe_code_hash
                record['source_sha256'] = self.source_hash
        except Exception as error:
            attempts = self.output / "lyric_attempts.json"
            extra_warnings = read(attempts).get("warnings", []) if name == "lyrics" and attempts.exists() else []
            record = {"schema_version": 1, "stage": name, "status": "failed", "cache_key": key,
                      "model_unavailable": isinstance(error, ModelUnavailable),
                      "reason": str(error), "warnings": extra_warnings + [f"{name} failed: {error}"]}
            save(path, record)
            self.stage_records[name] = record
            for message in record["warnings"]:
                warning(message, self.warnings)
            raise
        save(path, record)
        self.stage_records[name] = record
        for message in record["warnings"]:
            warning(message, self.warnings)
        return data

    def transcriber(self):
        if self.model_instance is None:
            self.model_instance = create(self.backend, device=self.device, model=self.model,
                                         yourmt3_python=self.yourmt3_python, yourmt3_root=self.yourmt3_root)
        return self.model_instance

    def audio_duration(self):
        if self.source_hash is None:
            raise StageError(f"Audio file missing: {self.song['audio_path']}")
        import soundfile as sf
        return sf.info(self.song["audio_path"]).duration

    def grid(self):
        def compute():
            path=self.output/'beats.json'
            prior=read(path).get('data',{}) if path.exists() else {}
            reused=prior.get('audio_sha256')==self.source_hash and prior.get('raw_grid') and prior.get('dbn_grid')
            detected=({k:prior[k] for k in ('raw_grid','dbn_grid','raw')} if reused else
                      beats.run(self.song["audio_path"], self.audio_duration(), self.device))
            result=beats.select_grid(detected, self.meter_policy,
                int(self.song.get("beats", {}).get("beats_per_bar", 4)))
            result.update(audio_sha256=self.source_hash,predictions_cached=bool(reused))
            return result
        return self.stage("beats", compute,
            {"model": "final0", "device": self.device, "meter_policy": self.meter_policy,
             "store_both_grids": True, "write_clicks": False,
             "beats_per_bar": self.song.get("beats", {}).get("beats_per_bar", 4)})
    def stems(self, require_all=False):
        folder = self.output / "stems"
        path=self.output/'separation.json'
        prior=read(path) if path.exists() else {}
        data=prior.get('data',{})
        if not require_all and data.get('retention'):
            report_path=self.output/'report.json'
            source=read(report_path).get('source_sha256') if report_path.exists() else None
            hashes=prior.get('artifact_sha256',{})
            if source==self.source_hash and hashes and all((self.output/key).is_file() and
                    digest(self.output/key)==value for key,value in hashes.items()):
                self.stage_records['separation']={**prior,'cache_hit':True}
                return data
        return self.stage("separation", lambda: separation.run(self.song["audio_path"], self.output, self.device),
                          {"model": "htdemucs", "shifts": 0, "device": self.device},
                          [folder / f"{s}.wav" for s in ("bass", "drums", "other", "vocals")])

    def transcribe(self):
        backend = self.transcriber()
        midi_path = self.output / "transcription.mid" if self.backend == "muscriptor" else None
        if midi_path:
            notes = backend.transcribe(Path(self.song["audio_path"]), self.song.get("instrument_hints"),
                                       midi_path=midi_path)
        else:
            notes = backend.transcribe(Path(self.song["audio_path"]), self.song.get("instrument_hints"))
        if not notes:
            raise StageError("Transcriber produced no notes; cannot analyze a silent prediction")
        return {"backend": self.backend, "model": self.model if self.backend == "muscriptor" else "YourMT3+ pinned comparison",
                "input": "full_mix", "instrument_hints": self.song.get("instrument_hints"),
                "notes": [asdict(n) for n in notes], "velocity_source": "unavailable_null",
                "event_diagnostics": getattr(backend, "event_diagnostics", {}),
                "eos_warning": getattr(backend, "event_diagnostics", {}).get("eos_warning", False),
                "eos_affected_chunks": getattr(backend, "event_diagnostics", {}).get("eos_affected_chunks", []),
                "instrument_hints_applied": getattr(backend, "event_diagnostics", {}).get(
                    "instrument_hints_applied", bool(self.song.get("instrument_hints"))),
                "instrument_hints_reason": getattr(backend, "event_diagnostics", {}).get("instrument_hints_reason"),
                "midi_path": str(midi_path) if midi_path else None,
                "midi_serialization": ({"velocity_default_if_unavailable": 100,
                                        "program_default_if_unavailable": 0,
                                        "defaults_are_not_model_predictions": True}
                                       if midi_path else None),
                "warnings": backend.warnings}

    def execute(self):
        self.stage_records = {}
        report = {"schema_version": "rhythm-lyrics/1", "song_id": self.song["id"],
                  "artist": self.song["artist"], "title": self.song["title"],
                  "audio_path": self.song["audio_path"], "source_sha256": self.source_hash,
                  "genius_url": self.song.get("genius_url"),
                  "genius_url_romanized": self.song.get("genius_url_romanized"),
                  "status": "running", "warnings": [], "lyric_sections": [],
                  "rhythmic_sections": [], "patterns": [], "pattern_runs": [],
                  "section_overlaps": [], "dominant_patterns": [], "instrument_quantization": []}
        results, failures = {}, {}
        prior_transcription = None
        prior_transcription_path = self.output / "transcription.json"
        prior_report_path = self.output / "report.json"
        if prior_transcription_path.is_file() and prior_report_path.is_file():
            previous_record = read(prior_transcription_path)
            previous_report = read(prior_report_path)
            if (previous_record.get("status") == "complete"
                    and previous_report.get("source_sha256") == self.source_hash
                    and previous_record.get("data", {}).get("input") == "full_mix"):
                prior_transcription = previous_record["data"].get("notes")

        def not_applicable(name, reason):
            record = {"schema_version": 1, "stage": name, "status": "not_applicable",
                      "reason": reason, "data": {"status": "not_applicable", "reason": reason}}
            save(self.output / f"{name}.json", record)
            self.stage_records[name] = record
            return record["data"]

        def run_independent(name, fn):
            try:
                results[name] = fn()
            except Exception as error:
                failures[name] = str(error)
                warning(f"{name} failed: {error}", self.warnings)

        # Measure source tuning before any grid family is selected or notes are transcribed.
        corrected_audio = self.output / "tuning_corrected.wav"
        prior_tuning = read(self.output / "tuning.json") if (self.output / "tuning.json").is_file() else None
        tuning_artifacts = lambda data: [corrected_audio] if data and data.get("corrected") else []
        run_independent("tuning", lambda: self.stage("tuning",
            lambda: tuning.run(self.song["audio_path"], corrected_audio),
            {"source_sha256": self.source_hash, "target_cents": tuning.TARGET_TUNING_CENTS,
             "analysis_sample_rate": tuning.ANALYSIS_SAMPLE_RATE}, tuning_artifacts))
        tuning_data = results.get("tuning")
        if tuning_data:
            self.transcription_audio_path = Path(tuning_data["transcription_audio_path"])
            report["tuning"] = tuning_data

        # Independent work must not be suppressed by a transcription failure.
        run_independent("beats", self.grid)
        if tuning_data:
            original_song = self.song
            if tuning_data.get("corrected"):
                self.song = {**self.song, "audio_path": str(self.transcription_audio_path)}
            transcribe_inputs = {"backend": self.backend, "model": self.model,
                                 "hints": self.song.get("instrument_hints"), "device": self.device}
            if tuning_data.get("corrected"):
                transcribe_inputs.update(input_audio_sha256=digest(self.transcription_audio_path),
                                         tuning=fingerprint(tuning_data))
            try:
                run_independent("transcription", lambda: self.stage("transcription", self.transcribe,
                    transcribe_inputs,
                    [self.output / "transcription.mid"] if self.backend == "muscriptor" else None))
            finally:
                self.song = original_song
        else:
            failures["transcription"] = "blocked_by:tuning"

        grid = results.get("beats")
        transcribed = results.get("transcription")
        lyrical = quantized = compressed = joined = None
        if grid:
            report.update(beat_times=grid["beat_times"], downbeat_times=grid["downbeat_times"],
                          estimated_bpm=grid["estimated_bpm"], beats_per_bar=grid["beats_per_bar"], bars=grid["bars"],
                          beat_bar_indices=grid["beat_bar_indices"], grid_flags=grid["flags"], grid_suspect=bool(grid["flags"]))
            report.update(raw_beats=grid["raw"], irregular_bars_before=grid["irregular_bars_before"],
                          irregular_bars_after=grid["irregular_bars_after"],
                          meter_policy=grid["meter_policy"], corrected_bars=grid["corrected_bars"],
                          unresolved_irregular_regions=grid["unresolved_irregular_regions"],
                          click_path=grid.get("click", {}).get("path"),
                          click_dbn_path=grid["dbn_click"]["path"] if grid.get("dbn_click") else None,
                          raw_dbn_bar_comparison=grid.get("raw_dbn_bar_comparison"))
            for flag in grid["flags"]:
                warning(f"Suspect grid: {flag}", self.warnings)
        full_mix_transcribed = transcribed
        route_config = self.song.get("transcription", {}).get("routes", {}).get("acoustic_piano")
        if transcribed and route_config:
            no_drums_path = self.output / "routed_sources" / "no_drums.wav"
            def build_route():
                separation_result = self.stage("separation",
                    lambda: separation.run_no_drums(self.transcription_audio_path, no_drums_path, self.device),
                    {"model": "htdemucs", "two_stems": "drums", "device": self.device,
                     "source_sha256": digest(self.transcription_audio_path)}, [no_drums_path])
                if not (self.output / "transcription_full_mix.mid").exists():
                    midi = self.output / "transcription.mid"
                    if midi.is_file():
                        import shutil
                        shutil.copy2(midi, self.output / "transcription_full_mix.mid")
                backend = self.transcriber()
                return self.stage("routing", lambda: {
                    **routing.apply_piano_route(backend, full_mix_transcribed["notes"],
                        separation_result["no_drums_path"], route_config.get("hints", ["piano"]),
                        target_instrument="acoustic_piano",
                        tuning_corrected=bool(tuning_data and tuning_data.get("corrected")),
                        full_mix_path=self.transcription_audio_path,
                        bass_hint=route_config.get("bass_hints", ["electric_bass"])),
                    "separation": separation_result},
                    {"full_mix_notes": fingerprint(full_mix_transcribed["notes"]),
                     "no_drums_sha256": separation_result["no_drums_sha256"],
                     "route": route_config, "model": self.model, "device": self.device,
                     "tuning": fingerprint(tuning_data) if tuning_data else None,
                     "backend": self.backend,
                     "backend_code": digest(Path(__file__).parent / "backends/muscriptor.py")
                                    if self.backend == "muscriptor" else None})
            run_independent("routing", build_route)
            routed = results.get("routing")
            if routed:
                transcribed = {**full_mix_transcribed, "notes": routed["merged_notes"],
                               "instrument_routes": [routed["route_summary"]],
                               "full_mix_note_count": len(full_mix_transcribed["notes"]),
                               "bass_sequence_comparison": routed["bass_sequence_comparison"]}
                if routed["route_summary"]["route_applied"] and self.backend == "muscriptor":
                    from .common import Note
                    self.transcriber()._write_midi([Note(**row) for row in transcribed["notes"]],
                                                   self.output / "transcription.mid")
                report["instrument_routes"] = transcribed["instrument_routes"]
                report["bass_sequence_comparison"] = routed["bass_sequence_comparison"]
                report["transcription_full_mix_note_count"] = transcribed["full_mix_note_count"]

        if transcribed:
            report.update(transcription_backend=transcribed["backend"], transcription_model=transcribed["model"],
                          transcription_input=transcribed["input"],
                          transcription_audio_path=tuning_data.get("transcription_audio_path") if tuning_data else None,
                          notes=transcribed["notes"], instrument_hints=transcribed["instrument_hints"],
                          instrument_hints_applied=transcribed.get("instrument_hints_applied"),
                          instrument_hints_reason=transcribed.get("instrument_hints_reason"),
                          transcription_event_diagnostics=transcribed.get("event_diagnostics"),
                          transcription_midi_path=transcribed.get("midi_path"),
                          midi_serialization=transcribed.get("midi_serialization"))
            if tuning_data and tuning_data.get("corrected"):
                comparison_path = self.output / "tuning_pitch_comparison.json"
                cached_comparison = read(comparison_path) if comparison_path.is_file() else None
                cached_has_baseline = False
                if cached_comparison:
                    has_fingerprints = (cached_comparison.get("before_transcription_fingerprint")
                                        and cached_comparison.get("after_transcription_fingerprint"))
                    cached_has_baseline = bool(
                        has_fingerprints and
                        cached_comparison["before_transcription_fingerprint"] !=
                        cached_comparison["after_transcription_fingerprint"])
                    if not has_fingerprints:
                        cached_has_baseline = (
                            cached_comparison.get("before_note_count") !=
                            cached_comparison.get("after_note_count") or
                            cached_comparison.get("pitch_changed_note_count", 0) > 0)
                if (cached_comparison and cached_has_baseline
                        and cached_comparison.get("source_sha256") == self.source_hash
                        and cached_comparison.get("corrected_audio_sha256") ==
                        tuning_data.get("corrected_audio_sha256")):
                    pitch_comparison = cached_comparison
                elif (prior_transcription is not None
                      and fingerprint(prior_transcription) != fingerprint(full_mix_transcribed["notes"])):
                    pitch_comparison = tuning.compare_note_pitches(prior_transcription,
                                                                   full_mix_transcribed["notes"])
                    pitch_comparison.update(source_sha256=self.source_hash,
                                            corrected_audio_sha256=tuning_data.get("corrected_audio_sha256"),
                                            before_transcription_fingerprint=fingerprint(prior_transcription),
                                            after_transcription_fingerprint=fingerprint(full_mix_transcribed["notes"]),
                                            comparison="original full-mix cache versus A440-corrected full-mix transcription")
                    save(comparison_path, pitch_comparison)
                else:
                    pitch_comparison = {"status": "no_original_transcription_baseline",
                                        "pitch_changed_note_count": None}
                report["tuning"]["transcription_pitch_change"] = pitch_comparison
            elif tuning_data:
                report["tuning"]["transcription_pitch_change"] = {
                    "status": "not_required_offset_within_25_cents", "pitch_changed_note_count": 0}
        # Songs without a Genius source have no lyric/join stage. Their rhythm
        # and timeline remain useful, so continue those stages independently.
        no_genius = not self.song.get("genius_url") and not self.song.get("has_vocals", False)
        if no_genius:
            reason = "no genius_url configured for this song"
            lyrical = not_applicable("lyrics", reason)
            not_applicable("join", reason)
            report.update(lyric_status="not_applicable", join_status="not_applicable",
                          lyric_labels="not_applicable",
                          boundary_agreement_baseline={
                              "status": "not_applicable",
                              "reason": "No lyric section boundaries are available for an instrumental song.",
                              "shuffles": self.boundary_shuffles},
                          boundary_agreement_within_one_bar_share=None,
                          most_boundaries_within_one_bar=None)
        # Lyrics need the measured grid; the vocal stem is needed only if synced
        # lyrics fail and WhisperX forced alignment is selected.
        elif grid:
            manual = self.song.get("manual_sections_csv")
            def run_lyrics():
                try:
                    return lyrics.run(self.song, self.output, grid, None,
                                      force="lyrics" in self.force, device=self.device,
                                      whisperx_python=self.whisperx_python,
                                      confidence_threshold=self.confidence)
                except StageError as error:
                    if "requires successful source separation and its vocals stem" not in str(error):
                        raise
                    stems = self.stems()
                    return lyrics.run(self.song, self.output, grid, stems,
                                      force="lyrics" in self.force, device=self.device,
                                      whisperx_python=self.whisperx_python,
                                      confidence_threshold=self.confidence)
            run_independent("lyrics", lambda: self.stage("lyrics", run_lyrics, {"song": self.song,
                "grid": fingerprint(grid),
                "stem_policy": "separation_only_for_diagnose_or_whisperx_fallback",
                "manual_sha256": digest(manual) if manual and Path(manual).exists() else None,
                "confidence": self.confidence, "aligner": self.whisperx_python}))
            lyrical = results.get("lyrics")
            if lyrical:
                report.update(lyric_sections=lyrical.get("sections", []),
                              genius_section_count=lyrical.get("genius_section_count"),
                              lyric_labels=lyrical.get("labels", "verified"),
                              lrclib_paragraphs=lyrical.get("lrclib_paragraphs", []),
                              paragraph_agreement=lyrical.get("paragraph_agreement"))
            else:
                attempted = self.output / "lrclib_alignment.json"
                if attempted.exists():
                    alignment = read(attempted)
                    report.update(genius_section_count=len(alignment.get("sections", [])),
                                  lyric_alignment_attempt=alignment.get("sections", []),
                                  lyric_labels="verified",
                                  lrclib_paragraphs=alignment.get("lrclib_paragraphs", []),
                                  paragraph_agreement=alignment.get("paragraph_agreement"))
        else:
            failures["lyrics"] = "blocked_by:beats"

        if grid and transcribed:
            run_independent("quantize", lambda: self.stage("quantize",
                lambda: quantize.quantize(transcribed["notes"], grid, self.steps,
                    self.step_candidates, self.grid_fit_tolerance_ms),
                {"notes": fingerprint(transcribed), "grid": fingerprint(grid), "steps": self.steps,
                 "step_candidates": self.step_candidates, "grid_fit_tolerance_ms": self.grid_fit_tolerance_ms}))
            quantized = results.get("quantize")
        else:
            failures["quantize"] = "blocked_by:" + ("beats" if not grid else "transcription")
        if quantized:
            for flag in quantized["flags"]:
                warning(f"Suspect grid: {flag}", self.warnings)
            report.update(grid_suspect=quantized["grid_suspect"], grid_flags=quantized["flags"],
                          tempo_hypotheses=quantized["tempo_hypotheses"], instrument_quantization=quantized["instrument_stats"],
                          quantized_notes=quantized["notes"], excluded_notes=quantized["excluded_notes"])
            report.update(requested_steps_per_bar=quantized["steps_per_bar"],
                          instrument_steps_per_bar=quantized.get("instrument_steps_per_bar", {}),
                          resolution_adjustments=quantized.get("resolution_adjustments", []),
                          collision_policy=quantized.get("collision_policy"))
            run_independent("layers", lambda: self.stage("layers",
                lambda: layers.split_notes(quantized["notes"], grid,
                    quantized.get("instrument_steps_per_bar", {})),
                {"quantized": fingerprint(quantized), "grid": fingerprint(grid)}))
            layer_data = results.get("layers")
            if layer_data:
                report["instrument_layers"] = layer_data["instrument_summary"]
                report["layer_half_bars"] = layer_data["half_bar_layers"]
                report["layer_policy"] = layer_data["policy"]
                report["layer_discarded_note_count"] = layer_data["discarded_note_count"]
            if layer_data:
                run_independent("patterns", lambda: self.stage("patterns",
                    lambda: patterns.run(quantized, grid, self.threshold, self.onset_distance,
                                         self.pitch_distance, self.section_window, self.min_section_bars,
                                         self.contour_distance, layer_data["pattern_notes"]),
                    {"quantized": fingerprint(quantized), "grid": fingerprint(grid),
                     "layers": fingerprint(layer_data),
                     "novelty_threshold": self.threshold, "onset_distance": self.onset_distance,
                     "pitch_distance": self.pitch_distance, "contour_distance": self.contour_distance,
                     "window": self.section_window,
                     "minimum_section_bars": self.min_section_bars}))
            else:
                failures["patterns"] = "blocked_by:layers"
            compressed = results.get("patterns")
        else:
            failures["patterns"] = "blocked_by:quantize"
        if compressed:
            report.update(patterns=compressed["patterns"], pattern_bars=compressed["bars"],
                          pattern_runs=compressed["runs"], rhythmic_sections=compressed["rhythmic_sections"],
                          instrument_compression=compressed["instrument_compression"],
                          irregular_bar_tokens=compressed["irregular_bars"])
            report.update(rhythmic_novelty_curve=compressed["novelty_curve"],
                          rhythm_clustering=compressed["clustering"],
                          rhythm_sectioning=compressed["sectioning"],
                          top_repeated_patterns=compressed["top_patterns"],
                          repeating_phrases=compressed["repeating_phrases"])
            report["two_bar_repeat_unit_check"] = compressed["two_bar_repeat_unit_check"]
            references = self.song.get("reference", {}).get("arpeggio_section")
            if references:
                if isinstance(references, dict):
                    references = [references]
                section_results = [patterns.arpeggio_acceptance(reference, grid, compressed,
                                                               quantized=quantized,
                                                               transcription_notes=transcribed["notes"],
                                                               layer_data=layer_data)
                                   for reference in references]
                report["arpeggio_acceptance"] = {
                    "sections": section_results,
                    "acceptance_threshold": .75,
                    "pass": all(section["pass"] for section in section_results),
                }
                report["pattern_compression"] = {"status": "ok" if report["arpeggio_acceptance"]["pass"] else "review",
                    "reason": "period acceptance met" if report["arpeggio_acceptance"]["pass"] else "reference period match rate below 75%; rings 2 and 3 remain independent",
                    "acceptance_metric": "period_match_rate"}
            lyric_count = sum(s.get("label") != "Instrumental" for s in
                              (lyrical or {}).get("sections", []))
            if lyric_count and len(compressed["rhythmic_sections"]) > 2 * lyric_count:
                message = (f"Rhythmic section count ({len(compressed['rhythmic_sections'])}) exceeds 2x "
                           f"lyric section count ({lyric_count}); inspect novelty peaks and meter boundaries.")
                report["rhythm_section_count_sanity_warning"] = message
                warning(message, self.warnings)
            for instrument in sorted({p["instrument"] for p in compressed["top_patterns"]}):
                print(f"Top repeated patterns — {instrument}:", flush=True)
                for item in [p for p in compressed["top_patterns"] if p["instrument"] == instrument]:
                    print(f"  {item['bar_count']} bars [{item['bar_ranges']}] {item['pattern_id']}: "
                          f"{item['onset_mini_notation']} {item['mini_notation']}", flush=True)
        else:
            failures["patterns"] = failures.get("patterns", "blocked_by:quantize")
        if no_genius:
            joined = None
        elif lyrical and compressed and grid:
            run_independent("join", lambda: self.stage("join", lambda: join.run(
                lyrical, compressed, grid, quantized["notes"], self.boundary_shuffles),
                {"lyrics": fingerprint(lyrical), "patterns": fingerprint(compressed),
                 "grid": fingerprint(grid), "quantized_notes": fingerprint(quantized["notes"]),
                 "boundary_shuffles": self.boundary_shuffles}))
            joined = results.get("join")
        else:
            failures["join"] = "blocked_by:" + ("lyrics" if not lyrical else "patterns")
        if joined:
            report.update(joined, genius_section_count=lyrical["genius_section_count"])
            try:
                plots.timeline(joined, compressed, grid, self.output / "timeline.png")
                report["timeline_path"] = str(self.output / "timeline.png")
            except Exception as error:
                failures["timeline"] = str(error)
            if not joined["most_boundaries_within_one_bar"]:
                warning("Most lyric boundaries are NOT within one bar of a rhythmic boundary; thresholds were not tuned to force agreement.", self.warnings)
            failed = any(s["status"] == "failed" for s in joined["lyric_sections"]) or "routing" in failures
            review = any(s["status"] == "review" for s in joined["lyric_sections"])
            report["status"] = "failed" if failed else "review" if review or quantized["grid_suspect"] else "complete"
        elif no_genius and compressed and grid:
            try:
                plots.timeline(None, compressed, grid, self.output / "timeline.png")
                report["timeline_path"] = str(self.output / "timeline.png")
            except Exception as error:
                failures["timeline"] = str(error)
            report["status"] = ("failed" if "routing" in failures else
                                "review" if quantized["grid_suspect"] else "complete")
        else:
            report["status"] = "failed"
        statuses = {}
        for stage_name in ("tuning", "beats", "separation", "transcription", "lyrics", "quantize", "layers", "patterns", "routing", "join"):
            cached = self.stage_records.get(stage_name)
            if cached:
                status = cached.get("status")
                statuses[stage_name] = {
                    "status": {"complete": "ok"}.get(status, status),
                    "reason": cached.get("reason") or ("verified cached result reused" if cached.get("cache_hit")
                                                         else "stage completed"),
                    "cache_hit": cached.get("cache_hit", False)}
            elif stage_name in failures:
                reason = failures[stage_name]
                statuses[stage_name] = {"status": reason if reason.startswith("blocked_by:") else "failed",
                                        "reason": reason}
            elif stage_name in results:
                statuses[stage_name] = {"status": "ok", "reason": None}
            elif stage_name == "separation":
                statuses[stage_name] = {"status": "skipped",
                                        "reason": "used only by diagnose or WhisperX lyrics fallback"}
            else:
                statuses[stage_name] = {"status": "skipped",
                    "reason": "no instrument route configured" if stage_name == "routing" else "not requested"}
        statuses["rhythm"] = statuses.get("patterns", {"status": "blocked_by:quantize", "reason": "not run"})
        statuses["report"] = {"status": "ok", "reason": "report generated"}
        statuses["timeline"] = ({"status": "ok", "reason": "timeline written"} if report.get("timeline_path")
                                else {"status": "failed" if "timeline" in failures else "blocked_by:join",
                                      "reason": failures.get("timeline", "join unavailable")})
        report["stages"] = statuses
        report["warnings"] = self.warnings
        report["stage_statuses"] = [{"stage": name, **record} for name, record in statuses.items()]
        report["acceptance"] = acceptance(report)
        save(self.output / "report.json", report)
        return report


def acceptance(report):
    if report.get("lyric_status") == "not_applicable":
        result = {"all_genius_sections_in_order": "not_applicable",
                  "all_sections_have_timing_and_confidence": "not_applicable",
                  "prechorus_chorus_separate_and_contiguous": "not_applicable",
                  "boundaries_snapped_to_downbeats": "not_applicable",
                  "genius_paragraph_agreement_rate": None,
                  "most_boundaries_within_one_bar": None,
                  "diagnose_20s": "not_run_by_run_command"}
        arpeggio = report.get("arpeggio_acceptance")
        if arpeggio:
            if "sections" in arpeggio:
                sections = arpeggio["sections"]
                result.update(arpeggio_pitch_cluster_threshold_met=all(item.get("pass", False)
                                                                       for item in sections),
                              arpeggio_section_results=sections)
            else:
                result.update(arpeggio_pitch_cluster_threshold_met=arpeggio["pass"],
                              arpeggio_pitch_cluster_share=arpeggio["dominant_cluster_share"],
                              arpeggio_dominant_cluster_bar_count=arpeggio["dominant_cluster_bar_count"],
                              arpeggio_regular_bar_count=arpeggio["regular_section_bar_count"])
        return result
    sections = report.get("lyric_sections") or report.get("lyric_alignment_attempt", [])
    original = [s for s in sections if s.get("genius_section_index") is not None]
    order = [s["genius_section_index"] for s in original]
    count = report.get("genius_section_count")
    all_sections = count is not None and sorted(set(order)) == list(range(count)) and order == sorted(order)
    threshold = report.get("confidence_threshold", .65)
    valid_timing = bool(original) and all(s.get("timing_source") and s.get("match_confidence") is not None and
                                         s.get("match_confidence") >= threshold and
                                         s.get("raw_start_s") is not None and s.get("raw_end_s") is not None
                                         and "first_source_line_unmatched" not in s.get("flags", [])
                                         and "no_matched_lines" not in s.get("flags", []) for s in original)
    pre = [i for i, s in enumerate(original) if s["label"] == "Pre-Chorus"]
    separate = bool(pre)
    if report.get("lyric_labels") == "unverified":
        separate = False
    for i in pre:
        following = original[i + 1] if i + 1 < len(original) else None
        a = original[i]
        separate &= bool(following and following["label"] == "Chorus" and a["raw_start_s"] is not None and
                         following["raw_start_s"] is not None and a["raw_start_s"] < following["raw_start_s"] and
                         a["raw_end_s"] == following["raw_start_s"])
    db = report.get("downbeat_times", [])
    snapped = bool(original) and all(s.get("snapped_start_s") in db and s.get("snapped_end_s") in db and
                                    s["snapped_start_s"] < s["snapped_end_s"] for s in original)
    return {"all_genius_sections_in_order": all_sections, "all_sections_have_timing_and_confidence": valid_timing,
            "prechorus_chorus_separate_and_contiguous": separate,
            "boundaries_snapped_to_downbeats": snapped,
            "genius_paragraph_agreement_rate": report.get("paragraph_agreement", {}).get("rate")
                if isinstance(report.get("paragraph_agreement"), dict) else None,
            "most_boundaries_within_one_bar": report.get("most_boundaries_within_one_bar"),
            "diagnose_20s": "not_run_by_run_command"}
