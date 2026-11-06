"""Known-time synthetic MIDI plus editable XLSX integrity/round-trip tests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import mido
from openpyxl import load_workbook

from .review_midi import MatchConfig, compare_notes, parse_midi, parse_pitch, write_midi
from .review_workbook import add_songscription, build_workbook, import_review, verify_workbook
from .store import digest


def event(i, start=0., end=.4, pitch=60, track=0, program=0):
    return {"id":i,"event_id":f"yourmt3_mix:test:{i}","source_key":"yourmt3_mix","source_model":"YourMT3+ mix",
            "pitch":pitch,"onset":start,"offset":end,"duration":end-start,"track":track,"program":program,
            "channel":track,"is_drum":False,"velocity":100,"instrument":f"Part {track}","midi_track":track+1,
            "source_stem":"original mix","sustain_end":end}


def raw_fixture():
    notes=[event(0,.123456789,.723456789),event(1,.123456789,.523456789,64),event(2,1.,1.3,60),event(3,2.,2.7,48,1,32)]
    return {"schema":"midi-review-raw/1","clock":"source_recording_seconds","focus":[6.,18.],"context":[0.,30.],
            "reference_sha256":"synthetic_audio_hash","notes":notes,"basic_pitch_used":False,
            "sources":{"yourmt3_mix":{"midi":{"controls":[],"warnings":[]},"raw_midi_path":"synthetic.mid",
                "alignment_offset":0.,"alignment_provenance":"known synthetic source clock"}}}


class MidiTimingTests(unittest.TestCase):
    def parse(self,midi):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"test.mid";midi.save(path);return parse_midi(path,"synthetic")

    def test_tempo_map_integrates_across_track_boundaries_and_ticks(self):
        midi=mido.MidiFile(ticks_per_beat=480)
        conductor=mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=500000),mido.MetaMessage("set_tempo",tempo=1000000,time=960)])
        notes=mido.MidiTrack([mido.Message("note_on",note=60,velocity=100,time=480),mido.Message("note_off",note=60,time=960)])
        midi.tracks.extend([conductor,notes]);parsed=self.parse(midi)
        self.assertEqual(parsed["notes"][0]["onset"],.5)
        self.assertEqual(parsed["notes"][0]["offset"],2.)

    def test_polyphony_simultaneous_repeated_and_overlapping_same_pitch_fifo(self):
        midi=mido.MidiFile(ticks_per_beat=480)
        track=mido.MidiTrack();midi.tracks.append(track)
        track.extend([mido.Message("note_on",note=60,velocity=80),mido.Message("note_on",note=64,velocity=90),
                      mido.Message("note_on",note=60,velocity=70,time=120),mido.Message("note_off",note=60,time=120),
                      mido.Message("note_on",note=60,velocity=0,time=120),mido.Message("note_off",note=64)])
        ns=self.parse(midi)["notes"]
        self.assertEqual(len(ns),3)
        self.assertEqual([(n["pitch"],n["onset"],n["offset"]) for n in ns],[(60,0.,.25),(64,0.,.375),(60,.125,.375)])

    def test_multiple_tracks_channels_and_programs(self):
        midi=mido.MidiFile()
        for channel,program in ((0,0),(2,32)):
            midi.tracks.append(mido.MidiTrack([mido.Message("program_change",channel=channel,program=program),mido.Message("note_on",channel=channel,note=60,velocity=100),mido.Message("note_off",channel=channel,note=60,time=480)]))
        ns=self.parse(midi)["notes"]
        self.assertEqual({n["channel"] for n in ns},{0,2});self.assertEqual({n["program"] for n in ns},{0,32})

    def test_sustain_does_not_overwrite_key_release(self):
        midi=mido.MidiFile(ticks_per_beat=480)
        midi.tracks.append(mido.MidiTrack([mido.Message("control_change",control=64,value=127),mido.Message("note_on",note=60,velocity=90),mido.Message("note_off",note=60,time=480),mido.Message("control_change",control=64,value=0,time=480)]))
        n=self.parse(midi)["notes"][0];self.assertEqual(n["offset"],.5);self.assertEqual(n["sustain_end"],1.)

    def test_missing_and_orphan_note_off_are_reported_without_fabrication(self):
        midi=mido.MidiFile();midi.tracks.append(mido.MidiTrack([mido.Message("note_off",note=61),mido.Message("note_on",note=60,velocity=100),mido.MetaMessage("end_of_track",time=480)]))
        result=self.parse(midi);self.assertFalse(result["notes"])
        self.assertEqual({w["issue"] for w in result["warnings"]},{"orphan_note_off","missing_note_off_excluded"})

    def test_type_two_independent_clocks_are_rejected(self):
        midi=mido.MidiFile(type=2);midi.tracks.append(mido.MidiTrack())
        with self.assertRaisesRegex(ValueError,"type-2"):self.parse(midi)

    def test_nonpositive_tempo_is_rejected(self):
        midi=mido.MidiFile();midi.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo",tempo=0)]))
        with self.assertRaisesRegex(ValueError,"tempo must be positive"):self.parse(midi)

    def test_submillisecond_resolution_and_note_name_parsing(self):
        midi=mido.MidiFile(ticks_per_beat=10000);midi.tracks.append(mido.MidiTrack([mido.Message("note_on",note=60,velocity=100,time=1),mido.Message("note_off",note=60,time=3)]))
        n=self.parse(midi)["notes"][0];self.assertAlmostEqual(n["onset"],.00005);self.assertAlmostEqual(n["offset"],.0002)
        self.assertEqual(parse_pitch("F#4"),66);self.assertEqual(parse_pitch("Db4"),61)


class MatchingTests(unittest.TestCase):
    def test_unique_pitch_and_timing_match(self):
        a=event(0,6.,6.5);b=event(1,6.02,6.52)
        rows=compare_notes([a],[b]);self.assertEqual(rows[0]["status"],"Agreement");self.assertEqual(rows[0]["reference_start"],6.)

    def test_large_onset_and_pitch_disagreements(self):
        self.assertEqual(compare_notes([event(0,6.,6.5)],[event(1,6.7,7.2)])[0]["status"],"Timing disagreement")
        self.assertEqual(compare_notes([event(0,6.,6.5)],[event(1,6.01,6.51,61)])[0]["status"],"Pitch disagreement")

    def test_missing_notes_both_sides(self):
        rows=compare_notes([event(0,6.,6.4)],[event(1,10.,10.4)])
        self.assertEqual({r["status"] for r in rows},{"YourMT3+ only","Songscription only"})

    def test_ambiguous_duplicate_candidates_are_never_forced(self):
        rows=compare_notes([event(0,6.,6.5)],[event(1,6.,6.5),event(2,6.01,6.51)])
        self.assertEqual(len(rows),3);self.assertTrue(all(r["status"].startswith("Ambiguous") for r in rows))
        self.assertTrue(all(not (r["a"] and r["b"]) for r in rows))

    def test_instrument_context_and_one_to_one_unique_pairs(self):
        a=[event(0,6.,6.5,60,0,0),event(1,6.,6.5,60,1,32)]
        b=[event(3,6.,6.5,60,1,32),event(4,6.,6.5,60,0,0)]
        self.assertTrue(all(r["status"].startswith("Ambiguous") for r in compare_notes(a,b)))
        rows=compare_notes(a,b,MatchConfig(instrument="program"));self.assertEqual(len(rows),2)
        self.assertTrue(all(r["a"]["program"]==r["b"]["program"] for r in rows))

    def test_external_midi_needs_explicit_alignment_offset_and_preserves_native_clock(self):
        raw=raw_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"songscription.mid";midi=mido.MidiFile(ticks_per_beat=480)
            track=mido.MidiTrack([mido.MetaMessage("track_name",name="Piano"),mido.Message("program_change",program=0),
                mido.Message("note_on",note=60,velocity=91,time=5760),mido.Message("note_off",note=60,time=384)])
            midi.tracks.append(track);midi.save(path)
            with self.assertRaisesRegex(ValueError,"alignment"):
                add_songscription(deepcopy(raw),path,None,"")
            add_songscription(raw,path,.25,"checked against reference excerpt start")
        note=next(n for n in raw["notes"] if n["source_key"]=="songscription")
        self.assertAlmostEqual(note["onset"],6.25);self.assertAlmostEqual(note["native_onset"],6.)
        self.assertEqual(raw["sources"]["songscription"]["alignment_offset"],.25)


class WorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.raw=raw_fixture();self.path=build_workbook(self.raw,self.root/"export")

    def tearDown(self):self.temp.cleanup()

    def test_export_numeric_chronological_duration_simultaneous_metadata_and_pending(self):
        check=verify_workbook(self.path,self.raw);self.assertEqual(check["event_count"],4)
        wb=load_workbook(self.path,data_only=True);ws=wb["All Notes"]
        self.assertEqual(ws["A2"].value,ws["A3"].value);self.assertAlmostEqual(ws["A2"].value,.123456789)
        self.assertAlmostEqual(ws["E2"].value,.6);self.assertEqual(wb["Model Comparison"]["N2"].value,"Pending Songscription MIDI")
        self.assertEqual(wb["All Notes"]["X2"].value,2)
        self.assertIn("Near-Simultaneous",ws["X1"].value)
        self.assertGreater(len(load_workbook(self.path)["All Notes"].data_validations.dataValidation),0)
        self.assertTrue((self.path.parent/"raw_yourmt3_notes.csv").is_file())
        self.assertTrue((self.path.parent/"WORKBOOK_REVIEW_GUIDE.md").is_file())

    def test_excel_tables_own_their_filters_without_duplicate_sheet_filters(self):
        main="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with ZipFile(self.path) as archive:
            for index in range(1,5):
                sheet=ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
                self.assertIsNone(sheet.find(f"{main}autoFilter"))
            view=ET.fromstring(archive.read("xl/worksheets/sheet5.xml")).find(f"{main}sheetViews/{main}sheetView")
            self.assertEqual([node.attrib.get("pane") for node in view.findall(f"{main}selection")],
                             ["topRight","bottomLeft","bottomRight"])
            self.assertEqual(sum(name.startswith("xl/tables/table") for name in archive.namelist()),4)

    def test_excel_double_serialization_does_not_false_fail_integrity(self):
        raw=raw_fixture();raw["notes"][0].update(onset=6.189583333333333,offset=6.71,duration=6.71-6.189583333333333)
        path=build_workbook(raw,self.root/"precision")
        self.assertEqual(verify_workbook(path,raw)["status"],"passed")

    def test_manual_pitch_onset_offset_role_and_free_text_roundtrip_without_raw_mutation(self):
        original=digest(self.path.parent/"raw-events.json")
        wb=load_workbook(self.path);ws=wb["All Notes"]
        ws["K2"]="Incorrect";ws["L2"]="F#4";ws["M2"]=.223456789;ws["N2"]=.923456789;ws["O2"]="Arpeggio";ws["P2"]="Audible transition reviewed"
        wb.save(self.path)
        document=import_review(self.path,self.root/"corrected",midi=True)
        n=next(n for n in document["notes"] if n["event_id"]==self.raw["notes"][0]["event_id"])
        self.assertEqual(n["pitch"],66);self.assertAlmostEqual(n["onset"],.223456789);self.assertAlmostEqual(n["duration"],.7)
        self.assertEqual(n["musical_role"],"Arpeggio");self.assertEqual(len(document["review_provenance"]["changes"]),6)
        self.assertEqual(original,digest(self.path.parent/"raw-events.json"));self.assertEqual(load_workbook(self.path)["All Notes"]["P2"].value,"Audible transition reviewed")
        parsed=parse_midi(self.root/"corrected/corrected.mid","corrected");self.assertEqual(len(parsed["notes"]),4)
        target=next(n for n in parsed["notes"] if n["pitch"]==66);self.assertAlmostEqual(target["onset"],.223456789,delta=1/60000)

    def test_missing_manual_note_false_positive_and_draft_coverage(self):
        wb=load_workbook(self.path);ws=wb["Review & Corrections"]
        data={"C2":"yourmt3_mix","D2":"Missing Note","E2":"A4","F2":6.5,"G2":6.9,"I2":"Melody","M2":"Verified",
              "B3":self.raw["notes"][0]["event_id"],"D3":"False Positive","M3":"Verified"}
        for cell,value in data.items():ws[cell]=value
        wb.save(self.path);doc=import_review(self.path,self.root/"corrected")
        self.assertEqual(len(doc["notes"]),4);self.assertTrue(any(n["event_id"]=="manual:manual-0001" for n in doc["notes"]))
        metrics=json.loads((self.root/"corrected/review-metrics.json").read_text());self.assertIsNone(metrics["accuracy"])

    def test_negative_nonnumeric_and_nonfinite_times_are_rejected(self):
        for i,value in enumerate((-.2,"6.500",True)):
            wb=load_workbook(self.path);wb["All Notes"]["M2"]=value;wb.save(self.path)
            with self.subTest(value=value),self.assertRaises(ValueError):import_review(self.path,self.root/f"bad{i}")

    def test_negative_duration_and_changed_raw_fields_are_rejected(self):
        wb=load_workbook(self.path);wb["All Notes"]["N2"]=.01;wb.save(self.path)
        with self.assertRaisesRegex(ValueError,"duration"):import_review(self.path,self.root/"bad")
        wb["All Notes"]["N2"]=None;wb["All Notes"]["A2"]=9.;wb.save(self.path)
        with self.assertRaisesRegex(ValueError,"Raw source cell"):import_review(self.path,self.root/"bad2")

    def test_fractional_program_and_channel_in_manual_addition_are_rejected(self):
        for cell,value in (("K2",1.5),("L2",2.5)):
            wb=load_workbook(self.path);ws=wb["Review & Corrections"]
            for target,v in {"C2":"yourmt3_mix","D2":"Missing Note","E2":"A4","F2":6.5,"G2":6.9,"K2":0,"L2":0,"M2":"Verified"}.items():ws[target]=v
            ws[cell]=value;wb.save(self.path)
            with self.subTest(cell=cell),self.assertRaisesRegex(ValueError,"integer"):import_review(self.path,self.root/f"fraction-{cell}")

    def test_verified_false_positive_cannot_conflict_with_verified_note_label(self):
        wb=load_workbook(self.path);ws=wb["Review & Corrections"]
        for row,action in ((2,"Correct"),(3,"False Positive")):
            ws.cell(row,2,self.raw["notes"][0]["event_id"]);ws.cell(row,3,"yourmt3_mix")
            ws.cell(row,4,action);ws.cell(row,13,"Verified")
        wb.save(self.path)
        with self.assertRaisesRegex(ValueError,"Conflicting verified false-positive"):
            import_review(self.path,self.root/"conflict")

    def test_corrected_midi_rejects_notes_below_tick_resolution(self):
        doc={"notes":[{**event(0,0.,.000001),"event_id":"tiny"}],"controls":[]}
        with self.assertRaisesRegex(ValueError,"below.*tick resolution"):
            write_midi(doc,self.root/"tiny.mid")

    def test_summary_exposes_live_review_coverage_and_manual_comparison_inputs(self):
        wb=load_workbook(self.path,data_only=False);summary=wb["Summary"]
        formulas={summary.cell(r,1).value:summary.cell(r,2).value for r in range(2,summary.max_row+1)}
        self.assertIn("COUNTIF",formulas["Declared complete intervals"])
        self.assertIn("Pending",str(load_workbook(self.path,data_only=True)["Model Comparison"]["N2"].value))

    def test_annotations_follow_ids_after_row_sort(self):
        wb=load_workbook(self.path);ws=wb["All Notes"]
        values=[[c.value for c in row] for row in ws.iter_rows(min_row=2)]
        for row,values_ in enumerate(reversed(values),2):
            for col,value in enumerate(values_,1):ws.cell(row,col,value)
        ws["L2"]=50;wb.save(self.path);doc=import_review(self.path,self.root/"sorted")
        n=next(n for n in doc["notes"] if n["event_id"]==self.raw["notes"][-1]["event_id"]);self.assertEqual(n["pitch"],50)

    def test_carry_review_preserves_manual_fields(self):
        wb=load_workbook(self.path);wb["All Notes"]["P2"]="keep this";wb["All Notes"]["M2"]=.2;wb.save(self.path)
        new=build_workbook(deepcopy(self.raw),self.root/"next-version",carry=self.path)
        values=load_workbook(new,data_only=True);self.assertEqual(values["All Notes"]["P2"].value,"keep this")
        self.assertAlmostEqual(values["All Notes"]["Q2"].value,.523456789)

    def test_verified_labels_only_score_complete_declared_coverage(self):
        wb=load_workbook(self.path);ws=wb["Review & Corrections"]
        for row,n in enumerate(self.raw["notes"][:2],2):
            ws.cell(row,2,n["event_id"]);ws.cell(row,4,"Correct");ws.cell(row,13,"Verified")
        settings=wb["Settings & Sources"];settings["H2"]=0.;settings["I2"]=.9;settings["J2"]="Complete";wb.save(self.path)
        import_review(self.path,self.root/"evaluated")
        metrics=json.loads((self.root/"evaluated/review-metrics.json").read_text())
        self.assertAlmostEqual(metrics["coverage_seconds"],.9);self.assertEqual(metrics["accuracy"]["f1"],1.)

    def test_external_comparison_sign_and_earliest_reference(self):
        raw=deepcopy(self.raw)
        external=deepcopy(raw["notes"][0]);external.update(event_id="songscription:test:1",source_key="songscription",source_model="Songscription",onset=.133456789,offset=.733456789)
        raw["notes"].append(external);raw["sources"]["songscription"]=deepcopy(raw["sources"]["yourmt3_mix"])
        path=build_workbook(raw,self.root/"both");ws=load_workbook(path,data_only=True)["Model Comparison"]
        self.assertAlmostEqual(ws["A2"].value,.123456789);self.assertAlmostEqual(ws["K2"].value,10.)


if __name__=="__main__":unittest.main()
