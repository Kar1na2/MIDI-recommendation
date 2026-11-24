"""Behavioral, contamination and entry-point enforcement regression tests."""
import ast
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .analysis import analyze, compare_windows, dtw, features, groups, recurrence
from .backend import select, verify
from .evaluation import match, score
from .fixtures import generate
from .policy import forbidden
from .phase3 import annotation_summary, pairwise_agreement, prepare, reviewed_metrics, suggest_offset
from .phase3c import audit_midi_messages, overlap_pair_count, overlap_regions, pitch_name, render_reconstruction
from .schema import from_midi
from .store import (ANALYSIS_VERSION, BACKEND, CHECKPOINT_SHA, SCHEMA, digest, fingerprint,
                    implementation_digest, load_run, save)


def note(pitch, onset, duration=.2, track=0, drum=False, identifier=0):
    return {"id": identifier, "pitch": pitch, "onset": onset, "offset": onset + duration,
            "duration": duration, "track": track, "is_drum": drum, "in_source_window": True}


def document(notes, end=12, drum=False):
    for i, n in enumerate(notes):
        n["id"] = i
    return {"run_id": "test", "source_window": [0, end], "notes": notes,
            "tracks": [{"track": 0, "program": 0, "is_drum": drum, "name": "test"}]}


class SymbolicTests(unittest.TestCase):
    def test_chords_do_not_chain_attacks(self):
        self.assertEqual([len(g) for g in groups([note(60, 0), note(64, .02), note(67, .04)])], [2, 1])

    def test_tempo_transposition_and_arbitrary_offset(self):
        pitches = [60, 64, 67, 72, 67, 64]
        notes = [note(p, .31 + i * .37, .24) for i, p in enumerate(pitches)]
        notes += [note(p + 2, 4.173 + i * .49, .24 * .49 / .37) for i, p in enumerate(pitches)]
        sequence = groups(document(notes)["notes"])
        match = dtw(features(sequence[:6], "notes"), features(sequence[6:], "notes"))
        self.assertLess(match["cost"], 1e-8)
        found = recurrence(sequence, "notes")
        self.assertTrue(any(abs(m["spans"][1][0] - 4.173) < 1e-8 for m in found))

    def test_inserted_note_alignment(self):
        a = groups([note(p, i * .4) for i, p in enumerate((60, 64, 67, 72, 67, 64))])
        b = groups([note(p, t) for p, t in ((62, 0), (66, .4), (69, .8), (70, 1.), (74, 1.2), (69, 1.6), (66, 2.))])
        result = dtw(features(a, "notes"), features(b, "notes"))
        self.assertLess(result["cost"], .22)
        self.assertEqual(len(result["alignment"]), 7)

    def test_rhythm_match_does_not_imply_notes_match(self):
        a = groups([note(p, i * .5) for i, p in enumerate((60, 64, 67, 72))])
        b = groups([note(p, i * .5) for i, p in enumerate((72, 70, 65, 60))])
        self.assertEqual(dtw(features(a, "rhythm"), features(b, "rhythm"))["cost"], 0)
        self.assertGreater(dtw(features(a, "notes"), features(b, "notes"))["cost"], .2)
        self.assertGreater(dtw(features(a, "contour"), features(b, "contour"))["cost"], .2)

    def test_overlap_and_chord_evidence(self):
        result = analyze(document([note(p, 0, 2) for p in (60, 64, 67)]))
        self.assertEqual(len(result["tracks"][0]["polyphonic_overlap"]), 3)
        self.assertEqual(result["chord_progression_candidates"][0]["labels"][0], {"root_pitch_class": 0, "quality": "major"})
        self.assertIsNone(result["tracks"][0]["contour"])

    def test_drums_never_become_harmony(self):
        result = analyze(document([note(p, i * .2, drum=True) for i, p in enumerate((36, 38, 42) * 3)], drum=True))
        track = result["tracks"][0]
        self.assertIsNone(track["contour"])
        self.assertFalse(track["arpeggio_candidates"])
        self.assertFalse(track["note_sequence_candidates"])
        self.assertFalse(result["chord_progression_candidates"])

    def test_beat_grid_does_not_quantize_notes(self):
        n = note(60, .317, .271)
        result = analyze(document([n]), bpm=120)
        self.assertAlmostEqual(result["tracks"][0]["analytical_events"][0]["beat"], .634)
        self.assertEqual(n["onset"], .317)
        self.assertIsNone(analyze(document([n]))["beat_grid"]["bpm"])

    def test_carry_in_does_not_invent_attack(self):
        doc = document([note(60, 5, 3), note(64, 6.2), note(67, 12.2)], end=18)
        result = compare_windows(doc, (6, 12), (12, 18))
        self.assertEqual(result["tracks"][0]["attack_counts"], [1, 1])


class EvaluationTests(unittest.TestCase):
    def test_wrong_pitch_false_and_missing_notes(self):
        ref = {"label_source": "synthetic_midi_render", "complete_notes": True,
               "notes": [note(60, .5), note(64, 1.)]}
        result = score(ref, document([note(72, .5), note(64, 1.01), note(70, 1.5)]))
        self.assertEqual(result["note_onset"]["true_positive"], 1)
        self.assertEqual(result["note_onset"]["false_positive"], 2)
        self.assertEqual(result["note_onset"]["missing"], 1)
        self.assertEqual(result["pitch_correctness_given_onset_match"], .5)
        self.assertEqual(result["note_offset_after_matched_onset_pitch_program"]["missing"], 1)

    def test_instrument_program_mismatch_is_not_a_note_match(self):
        ref = {"label_source": "synthetic_midi_render", "complete_notes": True,
               "notes": [{**note(60, .5), "program": 0}]}
        pred = document([{**note(60, .5), "program": 24}])
        result = score(ref, pred)
        self.assertEqual(result["note_onset_pitch_program"]["f1"], 0)
        self.assertEqual(result["attack_onset"]["f1"], 1)

    def test_silence_false_detections_are_penalized(self):
        ref = {"label_source": "synthetic_midi_render", "complete_notes": True, "notes": []}
        self.assertIsNone(score(ref, document([]))["note_onset"]["f1"])
        self.assertEqual(score(ref, document([note(60, .5)]))["note_onset"]["precision"], 0)

    def test_one_to_one_matching(self):
        self.assertEqual(len(match([note(60, 0)], [note(60, .01), note(60, .02)])), 1)

    def test_unreviewed_labels_rejected(self):
        with self.assertRaises(ValueError):
            score({"label_source": "prediction", "complete_notes": True, "notes": []}, document([]))

    def test_all_required_fixtures_are_independent_midi_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = generate(Path(tmp) / "fixtures")
            self.assertEqual(len(rows), 6)
            for row in rows:
                reference = json.loads(Path(row["reference"]).read_text())
                self.assertEqual(reference["label_source"], "synthetic_midi_render")
                self.assertEqual(row["audio_sha256"], digest(row["audio"]))


class Phase3ValidationTests(unittest.TestCase):
    def test_reference_crop_metadata_and_pending_report_are_created_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            mix_hash = digest("liked-audio/Silo - meaningful love - instrumental [6rQCPgrLJMQavladOGpwZt].mp3")
            root = prepare(Path(tmp) / "version")
            manifest = json.loads((root / "reference-manifest.json").read_text())
            self.assertEqual(manifest["excerpts"]["reference"]["duration_seconds"], 30.)
            self.assertEqual(manifest["excerpts"]["reference"]["sample_rate"], 48000)
            self.assertEqual(manifest["excerpts"]["other_stem"]["sample_rate"], 22050)
            self.assertEqual(digest(manifest["sources"]["original_instrumental_mix"]["path"]), mix_hash)
            self.assertIn("pending_manual_download", (root / "report.html").read_text())
            self.assertTrue((root / "external/README.txt").is_file())
            annotation = json.loads((root / "human-annotations.json").read_text())
            self.assertFalse(annotation["complete_notes"])


class Phase3BViewOnlyTests(unittest.TestCase):
    def test_screenshot_comparison_is_qualitative_and_does_not_infer_note_times(self):
        from .phase3b import SCHEMA, initial_review
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save(root / "comparison.json", {"reference_audio_sha256": "abc", "models": {}})
            review = initial_review(root)
            self.assertEqual(review["schema"], SCHEMA)
            self.assertEqual(review["focus_window_seconds"], [6., 18.])
            self.assertEqual(review["alignment"]["anchors"], [])
            self.assertIn("unknown", review["alignment"]["status"])
            self.assertEqual(review["verified_audio_notes"], [])
            self.assertIn("not verified against original audio", review["songscription_reference"]["visible_observations"][0]["evidence_class"])

    def test_report_has_annotation_workflow_screenshot_slots_and_keeps_model_outputs(self):
        from .phase3b import decorate_report, ensure_workspace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "external/screenshots").mkdir(parents=True)
            (root / "external/screenshots/page-1.jpg").write_bytes(b"test screenshot bytes")
            raw_midi = root / "raw.mid"
            raw_midi.write_bytes(b"immutable raw model output")
            raw_hash = digest(raw_midi)
            (root / "report.html").write_text('<h2>Inputs and evaluation state</h2>', encoding="utf-8")
            model = {"tracks": [{"track": 0, "name": "Piano", "program": 0, "is_drum": False}],
                     "notes": [{"track": 0, "pitch": 60, "onset": 7., "offset": 7.03,
                                "duration": .03, "model_key": "yourmt3_mix", "is_drum": False,
                                "id": 1, "instrument": "Piano"}]}
            save(root / "comparison.json", {"reference_audio_sha256": "abc", "models": {"yourmt3_mix": model}})
            ensure_workspace(root)
            result = decorate_report(root)
            page = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn("page-1.jpg", page)
            for required in ("p3b-kind", "p3b-evidence", "incorrect_octave", "incorrect_onset_duration",
                             "missing_instrument_layer", "repeated_arpeggio_quality", "p3b-export",
                             "p3b-anchor-add", "source-time alignment unknown", "p3b-audio"):
                self.assertIn(required, page)
            self.assertIn("human_verified_against_original_audio", page)
            self.assertEqual(result["screenshot_count"], 1)
            self.assertEqual(result["alignment"], "unknown_manual_anchors_needed")
            self.assertEqual(digest(raw_midi), raw_hash)
            self.assertTrue((root / "MANUAL_COMPARISON_6-18S.md").is_file())

    def test_manual_report_does_not_invent_numeric_accuracy(self):
        from .phase3b import initial_review, report_markdown
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save(root / "comparison.json", {"reference_audio_sha256": "abc", "models": {}})
            report = report_markdown(initial_review(root))
            self.assertIn("No note-level claim is marked verified", report)
            self.assertIn("Not ready", report)
            self.assertNotIn("accuracy: 92", report.lower())

    def test_pairwise_agreement_is_not_termed_accuracy(self):
        from .phase3 import pairwise_agreement
        a = [{"onset": 6., "offset": 6.2, "pitch": 60, "is_drum": False}]
        b = [{"onset": 6.01, "offset": 6.19, "pitch": 60, "is_drum": False}]
        result = pairwise_agreement(a, b)
        self.assertEqual(result["matched_events"], 1)
        self.assertIn("neither transcription is reference truth", result["interpretation"])

    def test_partial_annotations_never_generate_accuracy_metrics(self):
        annotation = {"complete_notes": False, "verified_notes": [], "reviews": [], "review_issue_enum": []}
        summary = annotation_summary(annotation)
        self.assertEqual(summary["review_count"], 0)
        self.assertIsNone(reviewed_metrics(annotation, {"model": {"notes": []}})["metrics"])

    def test_complete_human_labels_enable_tolerance_based_event_scores(self):
        annotation = {"complete_notes": True, "verified_notes": [
            {"pitch": 60, "onset": 6., "offset": 6.3, "program": 0}], "reviews": []}
        model = {"notes": [{"pitch": 60, "onset": 6.02, "offset": 6.28, "duration": .26,
                            "program": 24, "instrument": "wrong program", "is_drum": False} ]}
        result = reviewed_metrics(annotation, {"model": model})["metrics"]["model"]
        self.assertEqual(result["note_onset_pitch_precision_recall_f1"]["f1"], 1)
        self.assertAlmostEqual(result["mean_onset_error_seconds"], .02)
        self.assertEqual(result["instrument_attribution"]["instrument_errors"], 1)

    def test_alignment_suggestion_is_one_global_offset_and_diagnostic_only(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            rate = 22050
            audio = np.zeros(4 * rate, np.float32)
            for onset in (.6, 1.6, 2.6):
                i = round(onset * rate)
                audio[i:i + 1000] = np.hanning(2000)[:1000] * .8
            path = Path(tmp) / "pulses.wav"
            sf.write(path, audio, rate)
            notes = [{"onset": x, "pitch": 60} for x in (.5, 1.5, 2.5)]
            result = suggest_offset(path, notes, search_seconds=.4)
            self.assertAlmostEqual(result["suggested_global_offset_seconds"], .1, delta=.03)
            self.assertEqual(result["role"], "diagnostic suggestion only; this must not be treated as truth or applied silently")


class Phase3CInspectionTests(unittest.TestCase):
    def test_overlap_counts_use_positive_duration_half_open_intersections(self):
        rows = [note(60, 0., 2., identifier=0), note(64, .5, .5, identifier=1),
                note(67, 1., 1., identifier=2)]
        self.assertEqual(overlap_pair_count(rows), {(0, 1), (0, 2)})
        regions = overlap_regions(rows, (0.75, 1.75))
        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0]["start"], regions[0]["end"]), (.75, 1.75))

    def test_raw_message_audit_applies_tempo_changes_and_reports_explicit_programs(self):
        import mido
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tempo.mid"
            midi = mido.MidiFile(ticks_per_beat=480)
            meta = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000),
                                  mido.MetaMessage("set_tempo", tempo=1000000, time=480)])
            events = mido.MidiTrack([mido.Message("program_change", program=4),
                                    mido.Message("note_on", note=60, velocity=100, time=240),
                                    mido.Message("note_off", note=60, time=480)])
            midi.tracks.extend([meta, events]); midi.save(path)
            audit = audit_midi_messages(path)
            self.assertEqual((audit["note_on_count"], audit["note_off_count"]), (1, 1))
            self.assertEqual(audit["tracks"][1]["program_changes"][0]["program"], 4)
            self.assertEqual((audit["events"][0]["onset"], audit["events"][0]["offset"]), (.25, 1.))
            events.pop(); midi.save(path)
            with self.assertRaisesRegex(ValueError, "Unterminated"):
                audit_midi_messages(path)

    def test_pitch_names_match_midi_standard(self):
        self.assertEqual([pitch_name(p) for p in (48, 60, 61, 69, 72)], ["C3", "C4", "C#4", "A4", "C5"])

    def test_reconstruction_obeys_event_pitch_and_duration_and_flags_omitted_drums(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synth.wav"
            model = {"input": {"duration_seconds": 1.}, "stats": {"percussion_event_count": 1},
                     "notes": [{"onset": .2, "offset": .6, "pitch": 69, "velocity": 100, "is_drum": False},
                               {"onset": .3, "offset": .5, "pitch": 36, "velocity": 100, "is_drum": True}]}
            result = render_reconstruction(model, path)
            audio, rate = sf.read(path)
            self.assertEqual(rate, 24000)
            self.assertEqual(result["rendered_pitched_notes"], 1)
            self.assertEqual(result["percussion_notes_omitted"], 1)
            self.assertLess(float(np.max(np.abs(audio[:round(.19*rate)]))), 1e-5)
            segment = audio[round(.3*rate):round(.5*rate)]
            spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
            found = np.fft.rfftfreq(len(segment), 1/rate)[int(np.argmax(spectrum))]
            self.assertAlmostEqual(found, 440., delta=6.)
            self.assertLess(float(np.max(np.abs(audio[round(.61*rate):]))), 1e-5)

    def test_http_endpoint_serves_wav_and_byte_ranges(self):
        import json
        import threading
        import urllib.error
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        import numpy as np
        import soundfile as sf
        from .phase3c import InspectionHandler
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sf.write(root / "reference.wav", np.zeros(100, np.float32), 1000, subtype="PCM_16")
            sf.write(root / "reference_other.wav", np.zeros(50, np.float32), 500, subtype="PCM_16")
            shots = root / "external" / "screenshots"
            shots.mkdir(parents=True)
            (shots / "score.png").write_bytes(b"PNG image test fixture")
            old_root = InspectionHandler.root
            InspectionHandler.root = root
            server = ThreadingHTTPServer(("127.0.0.1", 0), InspectionHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                response = urlopen(base + "/reference.wav")
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), "audio/wav")
                self.assertEqual(response.read(4), b"RIFF")
                partial = urlopen(Request(base + "/reference.wav", headers={"Range": "bytes=0-39"}))
                self.assertEqual(partial.status, 206)
                self.assertEqual(partial.headers["Content-Range"], f"bytes 0-39/{(root / 'reference.wav').stat().st_size}")
                self.assertEqual(len(partial.read()), 40)
                health = json.loads(urlopen(base + "/api/health").read())
                self.assertTrue(health["ok"])
                self.assertEqual(health["duration_seconds"], .1)
                stem = urlopen(base + "/reference_other.wav")
                self.assertEqual(stem.headers.get_content_type(), "audio/wav")
                screenshot = urlopen(base + "/external/screenshots/score.png")
                self.assertEqual(screenshot.headers.get_content_type(), "image/png")
                self.assertEqual(screenshot.read(), b"PNG image test fixture")
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urlopen(base + "/external/screenshots/%2e%2e%2freference.wav")
                self.assertEqual(blocked.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
                InspectionHandler.root = old_root


class EnforcementTests(unittest.TestCase):
    def test_forbidden_imports_are_blocked_even_when_installed(self):
        for name in ("basic_pitch", "basic_pitch.inference", "scripts.rhythm_layers.patterns", "scripts.amt.transcribe"):
            with self.subTest(name=name), self.assertRaisesRegex(ImportError, "Forbidden"):
                importlib.import_module(name)

    def test_backend_registry_has_no_fallback(self):
        for name in ("basic-pitch", "auto", "songscription", "unknown"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "no fallback"):
                select(name)

    def test_basic_pitch_not_in_primary_dependency_manifest_or_lock(self):
        for filename in ("pyproject.toml", "uv.lock"):
            content = (Path(__file__).parents[2] / filename).read_text(encoding="utf-8").lower()
            self.assertNotIn("basic-pitch", content)
            self.assertNotIn("basic_pitch", content)

    def test_production_import_graph_has_no_legacy_edges(self):
        for path in Path(__file__).parent.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            for item in ast.walk(ast.parse(path.read_text())):
                imports = [a.name for a in item.names] if isinstance(item, ast.Import) else (
                    [item.module or ""] if isinstance(item, ast.ImportFrom) else [])
                for name in imports:
                    self.assertFalse(forbidden(name), (path, name))

    def test_production_commands_help_in_clean_process(self):
        for name in ("run", "analysis", "fixtures", "evaluation", "separation", "regression"):
            result = subprocess.run([sys.executable, "-B", "-m", f"scripts.midi_pipeline.{name}", "--help"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_preloaded_forbidden_module_is_rejected(self):
        code = "import sys,types; sys.modules['basic_pitch']=types.ModuleType('basic_pitch'); import scripts.midi_pipeline"
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already loaded", result.stderr)

    def test_old_cache_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            save(Path(tmp) / "run.json", {"schema_version": 1, "engine": "historical", "status": "complete"})
            with self.assertRaisesRegex(ValueError, "current"):
                load_run(tmp)

    def test_source_and_processing_changes_invalidate_identity(self):
        base = {"source_sha256": "a", "separation_model_version": "v1", "checkpoint_sha256": CHECKPOINT_SHA,
                "analysis_version": ANALYSIS_VERSION, "processing": {"start": 6}}
        for key in base:
            changed = {**base, key: "changed"}
            self.assertNotEqual(fingerprint(base), fingerprint(changed))

    def test_run_and_reader_share_the_production_code_identity(self):
        from .run import implementation_digest as run_digest
        self.assertEqual(run_digest(), implementation_digest())

    def test_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            identity = {"backend": BACKEND, "checkpoint_sha256": CHECKPOINT_SHA, "analysis_version": ANALYSIS_VERSION}
            (folder / "raw.mid").write_bytes(b"native")
            save(folder / "notes.json", {"schema": SCHEMA})
            save(folder / "run.json", {"schema": SCHEMA, "status": "prediction_available_unvalidated",
                 "identity": identity, "run_id": fingerprint(identity),
                 "artifacts": {"raw.mid": digest(folder / "raw.mid"), "notes.json": digest(folder / "notes.json")}})
            (folder / "raw.mid").write_bytes(b"old")
            with self.assertRaisesRegex(ValueError, "(current|hash mismatch)"):
                load_run(folder)

    def test_source_manifest_rejects_modified_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((FileNotFoundError, ValueError)):
                verify(Path(tmp))

    def test_failure_is_explicit_and_never_falls_back(self):
        from .run import execute
        class Broken:
            device, batch_size, seed, source_lock = "cpu", 1, 0, "test"
            def transcribe(self, *args):
                raise RuntimeError("deliberate backend failure")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = generate(root / "fixtures")
            with self.assertRaisesRegex(RuntimeError, "deliberate"):
                execute(Broken(), rows[0]["audio"], "fixture", "mix", "none", output_root=root / "runs")
            status = json.loads(next((root / "runs").glob("*/run.json")).read_text())
            self.assertEqual(status["status"], "transcription_unavailable")
            self.assertFalse(status["artifacts"])

    def test_native_midi_roundtrip_clock_tracks_and_no_confidence(self):
        import pretty_midi
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.mid"
            midi = pretty_midi.PrettyMIDI()
            for program in (0, 32):
                inst = pretty_midi.Instrument(program)
                inst.notes = [pretty_midi.Note(100, 60, .317, 1.271)]
                midi.instruments.append(inst)
            midi.write(str(path))
            before = digest(path)
            identity = {"source_audio_id": "test", "source_sha256": "source", "stem": "mix",
                        "separation_model_version": "test-separation"}
            result = from_midi(path, identity, "test", 6, 18)
            self.assertEqual(digest(path), before)
            self.assertEqual([t["program"] for t in result["tracks"]], [0, 32])
            self.assertAlmostEqual(result["notes"][0]["onset"] - result["notes"][0]["native_onset"], 6)
            self.assertNotIn("confidence", result["notes"][0])


if __name__ == "__main__":
    unittest.main()
