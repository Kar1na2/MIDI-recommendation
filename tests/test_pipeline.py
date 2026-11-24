"""Musical/timebase behavior, source precedence and cache/failure contracts."""
import csv
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pipeline.beats import build_grid, compare_bar_grids, grid_steps, regularize_grid
from pipeline.common import Note, StageError, ModelUnavailable, read, save
from pipeline.genius import normalize_header, parse_html, fetch as genius_fetch
from pipeline.line_alignment import align_lines, romanize_hangul
from pipeline.lrclib import parse_lrc, parse_paragraphs
from pipeline.lyrics import timed_sections, manual_sections, decorate, run as lyric_run, paragraph_agreement
from pipeline.patterns import (arpeggio_acceptance, encode_bar, run as compress,
                               smallest_period, simplify_tokens, normalized_onset_distance)
from pipeline.quantize import quantize
from pipeline.join import run as join
from pipeline.runner import Runner, acceptance
from pipeline import __main__ as pipeline_cli


def grid(duration=8):
    return build_grid([i * .5 for i in range(duration * 2 + 1)],
                      list(range(0, duration + 1, 2)), duration)


def note(t, pitch=60, instrument="piano", end=None):
    return asdict(Note(t, t + .08 if end is None else end, pitch, None, instrument))


def source_sections():
    return [{"label": label, "number": number, "performers": [],
             "genius_section_index": i, "lines": lines}
            for i, (label, number, lines) in enumerate([
                ("Verse", 1, ["one verse"]), ("Pre-Chorus", 1, ["build it up"]),
                ("Chorus", 1, ["sing again", "all together"]),
                ("Verse", 2, ["second story"]), ("Pre-Chorus", 2, ["build it up"]),
                ("Chorus", 2, ["sing again", "all together"])])]


class GeniusTests(unittest.TestCase):
    def test_header(self):
        self.assertEqual(normalize_header("[Pre-Chorus: Karina]")["label"], "Pre-Chorus")
        self.assertEqual(normalize_header("[Chorus 2: Karina, Winter & Ningning]")["performers"], ["Karina", "Winter", "Ningning"])
        self.assertEqual(normalize_header("[pre chorus]")["label"], "Pre-Chorus")

    def test_inline_markup_and_repeated_headers(self):
        html = '<div data-lyrics-container="true">[Pre-<span>Chorus: Karina</span>]<br>Build <a>it</a> up<br>[Chorus]<br>Again<br>[Chorus]<br>Again</div>'
        rows = parse_html(html)
        self.assertEqual(rows[0]["lines"], ["Build it up"])
        self.assertEqual([r["number"] for r in rows], [1, 1, 2])
        self.assertEqual(rows[0]["performers"], ["Karina"])

    def test_empty_instrumental_header_is_preserved(self):
        rows = parse_html('<div data-lyrics-container="true">[Intro]<br>[Verse]<br>Hello</div>')
        self.assertEqual(rows[0]["label"], "Intro")
        self.assertEqual(rows[0]["lines"], [])

    def test_saved_html_fixture_uses_page_parser(self):
        fixture = Path(__file__).parent / "fixtures" / "genius_structure.html"
        rows = parse_html(fixture.read_text(encoding="utf-8"))
        self.assertEqual([r["label"] for r in rows], ["Verse", "Pre-Chorus", "Chorus"])
        self.assertEqual(rows[1]["performers"], ["Example Performer"])
        self.assertEqual(rows[2]["lines"], ["PLACEHOLDER REPEATED CHORUS TEXT"])
        self.assertEqual(len(rows), 3)
        self.assertNotIn("PAGE HEADER BLOCK THAT MUST NOT BECOME A SECTION",
                         [line for section in rows for line in section["lines"]])

    def test_bracketed_page_title_and_header_block_are_not_sections(self):
        rows = parse_html('<div data-lyrics-container="true">[Artist - Song Lyrics]<br>'
                          'TITLE HEADER PLACEHOLDER<br>[Verse]<br>LYRIC PLACEHOLDER<br>'
                          '[Chorus]<br>CHORUS PLACEHOLDER</div>')
        self.assertEqual([row["label"] for row in rows], ["Verse", "Chorus"])
        self.assertEqual(rows[0]["lines"], ["LYRIC PLACEHOLDER"])

    def test_manually_saved_html_uses_same_parser_without_api_token(self):
        fixture = Path(__file__).parent / "fixtures" / "genius_structure.html"
        with TemporaryDirectory() as folder:
            saved = Path(folder) / "manual.html"
            saved.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
            with patch("pipeline.genius._manual_paths", return_value=[
                    ("https://genius.com/Fixture-lyrics", saved, False)]):
                result = genius_fetch({"id": "fixture", "genius_url": "https://genius.com/Fixture-lyrics"}, folder)
        self.assertEqual(result["retrieval"], "manual_html")
        self.assertEqual(result["sections"][0]["lines"], ["PLACEHOLDER LYRIC TEXT ONE"])

    def test_genius_403_is_not_retried(self):
        from pipeline.genius import _request_page
        with patch("requests.get") as request:
            request.return_value.status_code = 403
            with self.assertRaises(PermissionError):
                _request_page("https://genius.com/blocked")
        self.assertEqual(request.call_count, 1)

    def test_env_token_accepts_raw_or_bearer_without_exposing_it(self):
        from pipeline.genius import _access_token
        with TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text('GENIUS_ACCESS_TOKEN="Bearer fixture-secret"\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(_access_token(env_file), "fixture-secret")
                env_file.write_text("GENIUS_ACCESS_TOKEN=fixture-secret\n", encoding="utf-8")
                self.assertEqual(_access_token(env_file), "fixture-secret")

    def test_changed_source_url_never_reuses_other_pages_html(self):
        with TemporaryDirectory() as folder:
            html = '<div data-lyrics-container="true">[Verse]<br>Text</div>'
            root = Path(folder)
            data = root / "data" / "genius_html"
            data.mkdir(parents=True)
            original_cwd = Path.cwd()
            try:
                import os
                os.chdir(root)
                with patch.dict("os.environ", {"GENIUS_ACCESS_TOKEN": "test-token"}), \
                     patch("pipeline.genius._api_url", side_effect=[
                         "https://genius.com/First-lyrics", "https://genius.com/Second-lyrics"]), \
                     patch("pipeline.genius._request_page", return_value=html) as get:
                    song = {"id": "fixture-song", "artist": "Example", "title": "Fixture",
                            "genius_url": "https://genius.com/First-lyrics"}
                    genius_fetch(song, folder)
                    genius_fetch(song, folder)
                    song["genius_url"] = "https://genius.com/Second-lyrics"
                    genius_fetch(song, folder, force=True)
            finally:
                os.chdir(original_cwd)
            self.assertEqual(get.call_count, 2)


class AlignmentTests(unittest.TestCase):
    def test_repeated_chorus_keeps_distinct_occurrences(self):
        source = ["first verse", "sing again", "all together", "second story", "sing again", "all together"]
        target = ["noise", "First, VERSE!", "sing again", "all together", "second story", "sing again", "all together"]
        matches = align_lines(source, target)
        self.assertEqual([r["target_index"] for r in matches], [1, 2, 3, 4, 5, 6])

    def test_missing_lines_are_not_lookup_reused(self):
        matches = align_lines(["chorus same", "new verse", "chorus same"], ["chorus same", "new verse", "unrelated words", "chorus same"])
        self.assertEqual([r["target_index"] for r in matches], [0, 1, 3])

    def test_romanization_preserves_english(self):
        text = romanize_hangul("안녕 Hello")
        self.assertIn("Hello", text)
        self.assertTrue(align_lines([text], ["안녕 Hello"], romanize=True))

    def test_japanese_romanized_repeated_chorus_stays_ordered(self):
        source = ["明日へ進もう", "ずっと一緒に", "明日へ進もう", "ずっと一緒に"]
        timed = ["あしたへすすもう", "ずっといっしょに", "あしたへすすもう", "ずっといっしょに"]
        matches = align_lines(source, timed, language="ja")
        self.assertEqual([m["source_index"] for m in matches], [0, 1, 2, 3])
        self.assertEqual([m["target_index"] for m in matches], [0, 1, 2, 3])
        self.assertTrue(all(m["match_method"] == "romanized" for m in matches))

    def test_lrc_duplicate_tags_and_offset(self):
        rows = parse_lrc("[offset:100]\n[00:01.50][00:20.5]Again\n[ar:aespa]\n[00:03.00]Second")
        self.assertEqual([r["start_s"] for r in rows], [1.6, 3.1, 20.6])


class QuantizationTests(unittest.TestCase):
    def test_bar_edge_and_signed_error(self):
        q = quantize([note(.13), note(1.99), note(2.26)], grid(), 16)
        self.assertEqual([(n["bar"], n["step"]) for n in q["notes"]], [(0, 1), (1, 0), (1, 2)])
        self.assertAlmostEqual(q["notes"][0]["quantization_error_ms"], 5)
        self.assertAlmostEqual(q["notes"][1]["quantization_error_ms"], -10)

    def test_triplets_and_variable_tempo(self):
        g = build_grid([0, .4, .9, 1.5, 2.2, 2.8, 3.5, 4.3, 5.2], [0, 2.2, 5.2], 5.2)
        t = grid_steps(g, 12)[4][0]
        q = quantize([note(t)], g, 12)
        self.assertEqual(q["notes"][0]["step"], 4)
        self.assertAlmostEqual(q["notes"][0]["quantization_error_ms"], 0)

    def test_quarter_step_threshold(self):
        q = quantize([note(.06), note(.185)], grid())
        self.assertTrue(q["instrument_stats"][0]["grid_suspect"])

    def test_variable_meter_flag_and_pickup(self):
        g = build_grid([x * .5 for x in range(16)], [.5, 2.5, 4., 6.], 7.5)
        self.assertIsNone(g["beats_per_bar"])
        self.assertIn("nonconstant_beats_per_bar", g["flags"])
        q = quantize([note(.1)], g)
        self.assertEqual(q["excluded_notes"][0]["reason"], "pickup_or_outside_audio")

    def test_strict_meter_fixes_only_resolvable_region(self):
        raw = build_grid([x * .5 for x in range(17)], [0, 1.5, 4., 6.], 8.)
        grid = regularize_grid(raw, "strict", 4)
        self.assertEqual(grid["raw_downbeat_times"], [0., 1.5, 4., 6.])
        self.assertEqual(grid["bars_after_regularization"], [4, 4, 4])
        self.assertEqual(len(grid["corrected_bars"]), 1)
        self.assertEqual(grid["corrected_bars"][0]["before_beat_index"], 3)
        self.assertEqual(grid["corrected_bars"][0]["after_beat_index"], 4)
        self.assertFalse(grid["irregular_bars_after"])

    def test_strict_meter_marks_unresolvable_region(self):
        raw = build_grid([x * .5 for x in range(13)], [0, 1.5, 3.5, 5.5], 6.)
        grid = regularize_grid(raw, "strict", 4)
        self.assertEqual(len(grid["unresolved_irregular_regions"]), 1)
        self.assertTrue(all(row["status"] == "unresolved" for row in grid["irregular_bars_after"]))

    def test_short_tail_grid_does_not_invent_measured_downbeat(self):
        g = build_grid([0, .5, 1., 1.5, 2., 2.5], [0., 2.], 2.8)
        self.assertFalse(g["bars"][-1]["complete"])
        self.assertNotIn(g["bars"][-1]["end_s"], g["downbeat_times"])

    def test_dbn_policy_and_comparison_keep_timestamps_explicit(self):
        beat_times = [i * .5 for i in range(11)]
        raw = build_grid(beat_times, [0., 2., 4.], 5.)
        dbn = build_grid(beat_times, [0., 1.95, 4.], 5.)
        comparison = compare_bar_grids(raw, dbn)
        first = comparison["different_bars"][0]
        self.assertEqual(first["raw_bar"], 1)
        self.assertEqual(first["raw_start"], "00:00.000")
        self.assertEqual(first["dbn_end"], "00:01.950")
        self.assertIn("end_boundary_shift", first["reasons"])
        selected = regularize_grid(dbn, "dbn", 4)
        self.assertEqual(selected["meter_policy"], "dbn")
        self.assertEqual(selected["model"], "beat_this_dbn")

    def test_invalid_grid_and_notes_fail(self):
        with self.assertRaises(StageError):
            build_grid([0, .5, .4], [0, .4], 1.)
        with self.assertRaises(ValueError):
            Note(1., .5, 60, None, "piano")


class RepetitionTests(unittest.TestCase):
    def test_irregular_bars_are_tokens_not_pattern_matches_or_runs(self):
        song_grid = grid(6)
        song_grid["bars"][1]["beats_per_bar"] = 2
        song_grid["bars"][1]["beat_times"] = [2., 3., 4.]
        notes = [{"bar": bar, "step": 0, "onset_s": start + .1,
                  "pitch": 60, "instrument": "piano"}
                 for bar, start in enumerate((0., 2., 4.))]
        result = compress({"notes": notes, "steps_per_bar": 16}, song_grid)
        odd = next(row for row in result["bars"] if row["bar"] == 1)
        self.assertEqual(odd["token"], "odd[2]")
        self.assertEqual(odd["pattern_id"], "odd[2]")
        self.assertEqual(odd["pattern_type"], "irregular_bar")
        self.assertEqual([run["start_bar"] for run in result["runs"]], [0, 2])
        self.assertEqual([s["contains_irregular_bars"] for s in result["rhythmic_sections"]],
                         [False, True, False])
        self.assertEqual(result["rhythmic_sections"][1]["irregular_bars"][0]["token"], "odd[2]")
        self.assertEqual(result["instrument_compression"][0]["regular_bars"], 2)

    def test_within_bar_arpeggio_on_changed_roots(self):
        notes = []
        for figure, root in enumerate((60, 62, 65, 67)):
            for j, interval in enumerate((0, 4, 7, 12)):
                notes.append({**note((figure * 4 + j) / 8, root + interval), "step": figure * 4 + j})
        bar = encode_bar(notes)
        self.assertEqual(bar["within_bar"]["repeats"], 4)
        self.assertEqual(bar["within_bar"]["root_offsets"], [0, 2, 5, 7])
        self.assertEqual(bar["mini_notation"], "[0 4 7 12]*4")

    def test_rests_break_incorrect_subfigure_repeat(self):
        base, count = smallest_period(tuple("x...x...x...x..."))
        self.assertEqual(("".join(base), count), ("x...", 4))
        self.assertEqual(smallest_period(tuple("x...x..x"))[1], 1)

    def test_transposed_bars_run_length_and_change_boundary(self):
        notes = [note(bar * 2 + j * .125, 60 + 2 * bar + p)
                 for bar in range(3) for j, p in enumerate((0, 4, 7, 12))]
        notes += [note(6., 48, "bass"), note(7., 48, "bass")]
        result = compress(quantize(notes, grid()), grid())
        piano = [r for r in result["runs"] if r["instrument"] == "piano"]
        self.assertEqual([r["bar_count"] for r in piano[:3]], [1, 1, 1])
        regular = [r for r in result["bars"] if r["instrument"] == "piano" and r.get("exact_id")]
        self.assertEqual(len({r["shape_id"] for r in regular}), 1)
        self.assertEqual(len({r["exact_id"] for r in regular}), 3)
        self.assertEqual(len(result["rhythmic_sections"]), 1)  # four-bar minimum on this short fixture
        self.assertTrue(all(s["start_s"] in grid()["downbeat_times"] for s in result["rhythmic_sections"]))

    def test_tolerant_cluster_ids_and_multibar_phrase_detection(self):
        from pipeline.patterns import cluster, edit_distance
        a, b, silence = "x...x...x...x...", "xx..x...x...x...", "................"
        onset = cluster([(True, a), (True, b), (False, silence)],
                        lambda left, right: normalized_onset_distance(left[1], right[1]), .25, "o")
        self.assertEqual(onset[(True, a)][0], onset[(True, b)][0])
        self.assertNotEqual(onset[(True, a)][0], onset[(False, silence)][0])
        self.assertEqual(edit_distance((1, 2, 3), (1, 2, 3, 4)), 1)
        notes = [note(bar * 2 + step * .125, 60 + interval)
                 for bar in range(8) for step, interval in enumerate((0, 4, 7, 12))]
        result = compress(quantize(notes, grid(16)), grid(16))
        self.assertTrue(any(p["length_bars"] in (2, 4) and p["occurrence_count"] >= 2
                            for p in result["repeating_phrases"]))

    def test_pattern_notation_uses_modal_cluster_exemplar(self):
        g = grid(10)
        notes = []
        for bar in range(5):
            interval = 4 if bar == 0 else 5
            notes.extend([{"instrument": "piano", "bar": bar, "step": step,
                           "onset_s": bar * 2 + step * .125, "pitch": pitch}
                          for step, pitch in ((0, 60), (4, 60 + interval), (6, 67),
                                              (8, 72), (10, 74), (12, 76), (14, 79), (15, 84))])
        result = compress({"notes": notes, "steps_per_bar": 16}, g)
        self.assertEqual(len({row["pattern_id"] for row in result["bars"]}), 1)
        self.assertIn("0 ~3 5", result["patterns"][0]["mini_notation"])
        self.assertEqual(result["patterns"][0]["representative_bar"], 1)

    def test_mini_notation_collapses_rests_repeats_and_stacks_chords(self):
        self.assertEqual(simplify_tokens(["~", "x", "~"]), "x")
        self.assertEqual(simplify_tokens(["x", "~"] * 7 + ["x"]), "[x ~]*8")
        self.assertEqual(simplify_tokens(["a", "b", "c", "d"] * 4), "[a b c d]*4")
        self.assertEqual(simplify_tokens(["a", "~", "~", "b"]), "a ~2 b")
        chord = encode_bar([{"step": 1, "onset_s": .1, "pitch": p} for p in (60, 64, 67)])
        self.assertEqual(chord["mini_notation"], "~ [0,4,7] ~14")
        self.assertEqual(chord["onset_mini_notation"], "~ x ~14")

    def test_arpeggio_acceptance_counts_pitch_clusters_over_regular_bars(self):
        g = grid(8)
        data = {"bars": [
            {"instrument": "synth_lead", "bar": 0, "pattern_id": "o1:m1", "pitch_pattern_id": "m1"},
            {"instrument": "synth_lead", "bar": 1, "pattern_id": "o1:m1", "pitch_pattern_id": "m1"},
            {"instrument": "synth_lead", "bar": 2, "pattern_id": "o1:m1", "pitch_pattern_id": "m1"},
            {"instrument": "synth_lead", "bar": 3, "pattern_id": "o2:m2", "pitch_pattern_id": "m2"}],
            "pitch_pattern_definitions": [
                {"pitch_pattern_id": "m1", "mini_notation": "[0 4 7 12]*4"},
                {"pitch_pattern_id": "m2", "mini_notation": "[0 3 7 12]*4"}]}
        result = arpeggio_acceptance({"start_s": 0, "end_s": 8, "instrument": "synth"}, g, data)
        self.assertEqual(result["dominant_cluster_bar_count"], 3)
        self.assertEqual(result["dominant_cluster_share"], .75)
        self.assertEqual(result["simplified_mini_notation"], "[0 4 7 12]*4")
        self.assertFalse(result["pass"])  # IDs alone cannot establish direct period matches.

    def test_arpeggio_acceptance_reports_each_instrument_group_per_bar(self):
        g = grid(8)
        data = {"bars": [
            {"instrument": "acoustic_piano", "bar": i, "pattern_id": "o:m",
             "pitch_pattern_id": "m1" if i < 3 else "m2", "pattern_type": "regular_bar"}
            for i in range(4)] + [
            {"instrument": "drums", "bar": i, "pattern_id": "d:m",
             "pitch_pattern_id": "d1", "pattern_type": "regular_bar"}
            for i in range(4)],
            "pitch_pattern_definitions": [
                {"pitch_pattern_id": "m1", "mini_notation": "[0,4,7]"},
                {"pitch_pattern_id": "m2", "mini_notation": "[0,3,7]"},
                {"pitch_pattern_id": "d1", "mini_notation": "x"}]}
        result = arpeggio_acceptance({"start_s": 0, "end_s": 8,
            "instruments": ["piano", "drums"], "primary_instrument": "piano"}, g, data)
        self.assertEqual(result["dominant_pitch_pattern_cluster"], "acoustic_piano:m1")
        self.assertEqual(result["dominant_cluster_share"], .75)
        self.assertFalse(result["pass"])  # Legacy dominant share is retained but no longer decides acceptance.
        self.assertEqual(result["per_instrument_group"][1]["dominant_cluster_bar_count"], 4)
        self.assertEqual(result["cluster_ids_per_bar"][0]["pitch_pattern_cluster_ids"]["drums"], "drums:d1")

    def test_one_onset_bars_are_sustain_and_not_ranked(self):
        g = grid(8)
        notes = [{"instrument": "piano", "bar": bar, "step": 2, "onset_s": bar * 2 + .1, "pitch": 60}
                 for bar in range(4)]
        result = compress({"notes": notes, "steps_per_bar": 16}, g)
        self.assertTrue(all(row["pattern_type"] == "sustain" for row in result["bars"]))
        self.assertEqual(result["top_patterns"], [])
        self.assertEqual(result["patterns"], [])

    def test_novelty_sections_respect_minimum_and_odd_boundaries(self):
        from pipeline.patterns import novelty_sections
        g = grid(24)
        features = {bar: ({"A"} if bar < 8 else {"B"}) for bar in range(12)}
        sections, curve = novelty_sections(g, features, {}, 2, 4, .3)
        self.assertEqual([s["start_bar"] for s in sections], [0, 8])
        self.assertTrue(all(s["end_bar"] - s["start_bar"] >= 4 for s in sections))
        odd = {2: {"bar": 2, "token": "odd[2]"}}
        sections, _ = novelty_sections(g, features, odd, 2, 4, .3)
        self.assertTrue(any(s["contains_irregular_bars"] for s in sections))
        self.assertTrue(any(s["end_bar"] - s["start_bar"] < 4 for s in sections))
        self.assertTrue(curve)

    def test_drums_keep_class_under_transposition(self):
        a = encode_bar([{**note(0., 36, "drums"), "step": 0}], percussion=True)
        b = encode_bar([{**note(0., 38, "drums"), "step": 0}], percussion=True)
        self.assertNotEqual(a["pitch_pattern"], b["pitch_pattern"])


class LyricTimingTests(unittest.TestCase):
    def test_lrclib_plain_paragraphs_align_to_synced_lines(self):
        plain = "First line\nFirst repeat\n\nSecond line\nSecond repeat"
        synced = [{"text": "First line", "start_s": 1.}, {"text": "First repeat", "start_s": 2.},
                  {"text": "Second line", "start_s": 5.}, {"text": "Second repeat", "start_s": 6.}]
        rows = parse_paragraphs(plain, synced, 10.)
        self.assertEqual(rows, [{"index": 0, "start_s": 1., "end_s": 5., "line_count": 2},
                                {"index": 1, "start_s": 5., "end_s": 10., "line_count": 2}])
        self.assertEqual(parse_paragraphs("One line\nTwo line", synced, 10.), [])

    def test_genius_unavailable_uses_unverified_lrclib_paragraph_segments(self):
        timed = [{"text": "repeat this", "start_s": 1.}, {"text": "other", "start_s": 3.},
                 {"text": "repeat this", "start_s": 5.}]
        lrc = {"lines": timed, "plain_lyrics": "repeat this\n\nother\n\nrepeat this",
               "lrclib_paragraphs": [{"index": 0, "start_s": 1., "end_s": 3., "line_count": 1},
                                     {"index": 1, "start_s": 3., "end_s": 5., "line_count": 1},
                                     {"index": 2, "start_s": 5., "end_s": 8., "line_count": 1}]}
        with TemporaryDirectory() as folder, \
             patch("pipeline.genius.fetch", side_effect=StageError("HTTP 403")), \
             patch("pipeline.lrclib.fetch", return_value=lrc):
            result = lyric_run({"genius_url": "https://genius.com/example-lyrics"}, folder, grid(), {})
            artifact = read(Path(folder) / "lyrics.json")
        self.assertEqual(result["labels"], "unverified")
        self.assertEqual(result["sections"][0]["timing_source"], "lrclib_paragraphs")
        self.assertEqual([s["group"] for s in result["sections"]], ["A", "B", "A"])
        self.assertTrue(result["sections"][0]["likely_chorus"])
        self.assertEqual(artifact["labels"], "unverified")

    def test_genius_boundaries_record_paragraph_agreement_without_moving_times(self):
        sections = [{"raw_start_s": 0.}, {"raw_start_s": 4.1}, {"raw_start_s": 7.9}]
        before = [s["raw_start_s"] for s in sections]
        paragraphs = [{"start_s": 0.}, {"start_s": 4.}]
        lines = [{"start_s": t} for t in (0., 2., 4., 6., 8.)]
        summary = paragraph_agreement(sections, paragraphs, lines)
        self.assertEqual([s["paragraph_agreement"] for s in sections], [False, True, False])
        self.assertEqual(summary, {"agreements": 1, "boundaries": 3, "rate": 1/3})
        self.assertEqual([s["raw_start_s"] for s in sections], before)

    def test_prechorus_chorus_order_and_end_rule(self):
        source = source_sections()
        text = [line for s in source for line in s["lines"]]
        lines = [{"text": t, "start_s": i * 2.} for i, t in enumerate(text)]
        matches = align_lines(text, text)
        rows = timed_sections(source, lines, matches, "lrclib", 20)
        self.assertEqual(rows[1]["raw_end_s"], rows[2]["raw_start_s"])
        self.assertLess(rows[1]["raw_start_s"], rows[2]["raw_start_s"])
        self.assertEqual(rows[4]["raw_end_s"], rows[5]["raw_start_s"])

    def test_missing_section_start_not_interpolated(self):
        source = source_sections()
        rows = timed_sections(source, [], [], "lrclib", 20)
        self.assertIsNone(rows[1]["raw_start_s"])
        self.assertIn("no_matched_lines", rows[1]["flags"])

    def test_manual_always_wins_and_invalid_manual_never_falls_back(self):
        source = source_sections()
        with TemporaryDirectory() as folder:
            p = Path(folder) / "truth.csv"
            with p.open("w", newline="") as f:
                w = csv.writer(f); w.writerow(["label", "start_s", "end_s"])
                for i, s in enumerate(source):
                    w.writerow([s["label"], 2 * i, 2 * (i + 1)])
            song = {"manual_sections_csv": str(p)}
            with patch("pipeline.genius.fetch", return_value={"source_url": "fixture", "sections": source}), patch("pipeline.lrclib.fetch") as fetch:
                result = lyric_run(song, folder, grid(12), {}, False)
                fetch.assert_not_called()
            self.assertTrue(all(s["match_confidence"] == 1. for s in result["sections"]))
            p.write_text("label,start_s,end_s\nChorus,0,3\n")
            with self.assertRaises(StageError):
                manual_sections(p, source, 12)

    def test_collapsed_snap_is_flagged_not_hidden(self):
        rows = [{"label": "Pre-Chorus", "number": 1, "raw_start_s": 2.1,
                 "raw_end_s": 2.3, "flags": []}]
        self.assertIn("collapsed_after_downbeat_snap", decorate(rows, grid())[0]["flags"])

    def test_manual_truth_survives_blocked_genius(self):
        with TemporaryDirectory() as folder:
            p = Path(folder) / "manual.csv"
            p.write_text("label,start_s,end_s\nVerse,0,4\nChorus,4,8\n")
            with patch("pipeline.genius.fetch", side_effect=StageError("HTTP 403")), patch("pipeline.lrclib.fetch") as automatic:
                result = lyric_run({"manual_sections_csv": str(p)}, folder, grid(), {})
            automatic.assert_not_called()
            self.assertEqual(result["sections"][1]["raw_start_s"], 4.)
            self.assertEqual(result["sections"][1]["match_confidence"], 1.)
            self.assertIsNone(result["genius_section_count"])

    def test_fallback_reason_survives_failed_whisperx(self):
        with TemporaryDirectory() as folder:
            source = source_sections()
            parsed = {"source_url": "fixture", "sections": source, "romanized_only": False}
            with patch("pipeline.lyric_backend.cached_source", return_value=parsed), patch("pipeline.lrclib.fetch", side_effect=StageError("duration mismatch")), patch("pipeline.lyrics.subprocess.run") as process:
                process.return_value.returncode = 1
                process.return_value.stderr = "aligner unavailable"
                with self.assertRaises(StageError):
                    lyric_run({"language": "ko"}, folder, grid(), {"stems": {"vocals": "fixture.wav"}})
            attempts = read(Path(folder) / "lyric_attempts.json")
            self.assertIn("duration mismatch", attempts["warnings"][0])
            self.assertEqual(attempts["next_source"], "whisperx")


class CacheAndFailureTests(unittest.TestCase):
    def test_import_preflight_failure_starts_no_stages(self):
        def require_module(name):
            if name == "beat_this":
                raise ModuleNotFoundError("beat_this")
            return object()
        with patch.object(pipeline_cli.importlib, "import_module", side_effect=require_module), \
             patch.object(pipeline_cli, "Runner") as runner, \
             patch.object(pipeline_cli, "load_songs") as songs:
            self.assertEqual(pipeline_cli.main(["run", "--song", "fixture"]), 1)
        runner.assert_not_called()
        songs.assert_not_called()

    def test_cached_stage_skips_and_force_recomputes(self):
        with TemporaryDirectory() as folder:
            song = {"id": "fixture", "audio_path": "missing", "artist": "a", "title": "t"}
            runner = Runner(song, folder)
            calls = []
            fn = lambda: calls.append(1) or {"value": 3}
            runner.stage("beats", fn, {})
            runner.stage("beats", fn, {})
            self.assertEqual(len(calls), 1)
            runner.force.add("beats")
            runner.stage("beats", fn, {})
            self.assertEqual(len(calls), 2)

    def test_missing_artifact_rebuilds(self):
        with TemporaryDirectory() as folder:
            runner = Runner({"id": "f", "audio_path": "missing"}, folder)
            calls = []
            artifact = Path(folder) / "missing.wav"
            def fn():
                calls.append(1)
                artifact.write_bytes(b"complete fixture output")
                return {}
            runner.stage("separation", fn, {}, [artifact])
            artifact.unlink()
            runner.stage("separation", fn, {}, [artifact])
            self.assertEqual(len(calls), 2)

    def test_model_failure_report_has_no_fabricated_sections(self):
        with TemporaryDirectory() as folder:
            manual = Path(folder) / "sections.csv"
            manual.write_text("label,start_s,end_s\nVerse,0,4\n", encoding="utf-8")
            song = {"id": "f", "audio_path": "missing", "artist": "a", "title": "t",
                    "manual_sections_csv": str(manual)}
            runner = Runner(song, folder)
            fake_grid = {**grid(), "raw": {}, "irregular_bars_before": [], "irregular_bars_after": [],
                         "meter_policy": "strict", "corrected_bars": [],
                         "unresolved_irregular_regions": [], "click": {"path": "click.wav"}}
            with patch("pipeline.tuning.run", return_value={"transcription_audio_path": "missing",
                       "source_sha256": None, "offset_cents": 0., "corrected": False}), \
                 patch.object(runner, "grid", return_value=fake_grid), patch.object(runner, "stems", return_value={}), \
                 patch.object(runner, "transcribe", side_effect=ModelUnavailable("gated weights")):
                result = runner.execute()
            self.assertEqual(result["stages"]["transcription"]["status"], "failed")
            self.assertEqual(result["stages"]["lyrics"]["status"], "not_applicable")
            self.assertEqual(result["stages"]["quantize"]["status"], "blocked_by:transcription")
            self.assertEqual(read(runner.output / "lyrics.json")["status"], "not_applicable")
            self.assertEqual(result["acceptance"]["all_genius_sections_in_order"], "not_applicable")

    def test_instrumental_keeps_rhythm_timeline_with_not_applicable_lyrics_and_join(self):
        with TemporaryDirectory() as folder:
            song = {"id": "instrumental", "audio_path": "missing", "artist": "a", "title": "t"}
            runner = Runner(song, folder)
            fake_grid = {**grid(), "raw": {}, "irregular_bars_before": [], "irregular_bars_after": [],
                         "meter_policy": "strict", "corrected_bars": [],
                         "unresolved_irregular_regions": [], "click": {"path": "click.wav"},
                         "dbn_click": None, "raw_dbn_bar_comparison": None}
            transcription = {"backend": "muscriptor", "model": "small", "input": "full_mix",
                             "instrument_hints": None, "notes": [note(.1)]}
            # A successful mocked transcription must publish its required artifact.
            (runner.output / "transcription.mid").write_bytes(b"synthetic MIDI fixture")
            with patch("pipeline.tuning.run", return_value={"transcription_audio_path": "missing",
                       "source_sha256": None, "offset_cents": 0., "corrected": False}), \
                 patch.object(runner, "grid", return_value=fake_grid), \
                 patch.object(runner, "transcribe", return_value=transcription), \
                 patch("pipeline.plots.timeline") as timeline:
                result = runner.execute()
            self.assertEqual(result["stages"]["lyrics"]["status"], "not_applicable")
            self.assertEqual(result["stages"]["join"]["status"], "not_applicable")
            self.assertEqual(result["stages"]["patterns"]["status"], "ok")
            self.assertEqual(result["stages"]["timeline"]["status"], "ok")
            self.assertEqual(result["stages"]["routing"]["status"], "skipped")
            self.assertTrue(result["stages"]["routing"]["reason"])
            timeline.assert_called_once()
            self.assertIsNone(timeline.call_args.args[0])

    def test_instrumental_acceptance_marks_lyric_checks_unavailable_and_reports_arpeggio(self):
        result = acceptance({"lyric_status": "not_applicable",
            "arpeggio_acceptance": {"pass": False, "dominant_cluster_share": 0.,
                                    "dominant_cluster_bar_count": 0,
                                    "regular_section_bar_count": 5}})
        self.assertEqual(result["all_genius_sections_in_order"], "not_applicable")
        self.assertIsNone(result["most_boundaries_within_one_bar"])
        self.assertFalse(result["arpeggio_pitch_cluster_threshold_met"])
        self.assertEqual(result["arpeggio_regular_bar_count"], 5)

    def test_raw_boundary_distance_not_snapped_zero(self):
        g = grid()
        notes = [note(0), note(2), note(4), note(6)]
        p = compress(quantize(notes, g), g)
        lyrical = {"sections": [{"label": "Verse", "raw_start_s": 3., "raw_end_s": 5.,
                                 "snapped_start_s": 2., "snapped_end_s": 4.}]}
        result = join(lyrical, p, g)
        self.assertEqual(result["lyric_sections"][0]["start_boundary_distance_bars"], 1.5)

    def test_boundary_agreement_has_reproducible_random_baseline_and_voice_coverage(self):
        g = grid(12)
        voice = [note(.2, 65, "voice", .7), note(4.2, 64, "voice", 4.8)]
        q = quantize(voice, g)
        p = compress(q, g)
        lyrics = {"sections": [{"label": "Verse", "raw_start_s": 0., "raw_end_s": 4.},
                               {"label": "Chorus", "raw_start_s": 4., "raw_end_s": 12.}]}
        first = join(lyrics, p, g, q["notes"], baseline_shuffles=1000, seed=7)
        second = join(lyrics, p, g, q["notes"], baseline_shuffles=1000, seed=7)
        self.assertEqual(first["boundary_agreement_baseline"], second["boundary_agreement_baseline"])
        self.assertEqual(first["boundary_agreement_baseline"]["shuffles"], 1000)
        self.assertEqual(first["lyric_sections"][0]["voice_note_coverage"]["note_count"], 1)
        self.assertLessEqual(first["lyric_sections"][0]["voice_note_coverage"]["regular_bar_coverage"], 1.)


if __name__ == "__main__":
    unittest.main()
