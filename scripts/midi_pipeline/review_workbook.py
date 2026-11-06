"""Export editable note tables; import explicit corrections without changing raw AMT."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import openpyxl
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.workbook.properties import CalcProperties

from .review_midi import MatchConfig, compare_notes, note_name, number, parse_midi, parse_pitch, valid_note, write_midi
from .store import digest, load_run, save, fingerprint

PHASE3 = Path("output/midi_pipeline/phase3/meaningful-love-v1")
OUTPUT = Path("output/midi_pipeline/phase3/meaningful-love-workbook-v3")
STATUSES = ["Unreviewed", "Correct", "Incorrect", "Missing Context", "Uncertain"]
ROLES = ["Unknown", "Melody", "Arpeggio", "Chord Tone", "Pad", "Bass", "Ostinato", "Other"]
ACTIONS = ["Correct", "Missing Note", "False Positive", "Pitch Correction", "Onset Correction", "Offset Correction", "Instrument Correction", "Role Label", "Observation"]
ALL_HEADERS = ["Start Time (seconds)", "Note Name", "MIDI Pitch Number", "End Time (seconds)", "Duration (seconds)", "Source Model", "Instrument / Track", "Velocity", "Stem / Audio Source", "Event ID", "Manual Review Status", "Corrected Pitch", "Corrected Start Time", "Corrected End Time", "Musical Role", "Reviewer Notes", "Corrected Duration (seconds)", "Evaluation Interval", "MIDI Program", "MIDI Channel", "Drum", "Sustain End (seconds)", "MIDI Track", "Near-Simultaneous Notes (±0.005 s)"]
COMPARISON_HEADERS = ["Reference Start Time", "YourMT3+ Note", "YourMT3+ Start", "YourMT3+ End", "YourMT3+ Duration", "Songscription Note", "Songscription Start", "Songscription End", "Songscription Duration", "Pitch Difference (semitones)", "Onset Difference (milliseconds)", "Offset Difference (milliseconds)", "Duration Difference (milliseconds)", "Match Status", "Manual Verdict", "Reviewer Notes", "YourMT3+ Event ID", "Songscription Event ID", "YourMT3+ Track", "Songscription Track", "Instrument Context", "Absolute Onset Difference (ms)", "Absolute Offset Difference (ms)", "Absolute Duration Difference (ms)", "Ambiguous Candidate IDs", "Evaluation Interval"]
REVIEW_HEADERS = ["Annotation ID", "Original Event ID", "Source Model", "Action", "MIDI Pitch / Note Name", "Start Time (seconds)", "End Time (seconds)", "Duration (seconds)", "Musical Role", "Instrument", "MIDI Program", "MIDI Channel", "Verification", "Reviewer Notes", "Link Status", "Drum"]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def local_sources(root):
    root = Path(root).resolve()
    manifest, index = _read(root / "reference-manifest.json"), _read(root / "local-runs.json")
    sources, notes = {}, []
    for key, entry in index["runs"].items():
        path = root / entry["path"]
        run, document = load_run(path)
        excerpt = root / ("reference.wav" if key == "yourmt3_mix" else "reference_other.wav")
        if digest(excerpt) != entry["source_excerpt_sha256"] or digest(excerpt) != run["identity"]["source_sha256"]:
            raise ValueError("Excerpt hash differs from the executed AMT input")
        original = manifest["sources"]["original_instrumental_mix" if key == "yourmt3_mix" else "existing_instrumental_other"]
        if digest(original["path"]) != original["sha256"]:
            raise ValueError("Original audio changed since Phase 3 preparation")
        parsed = parse_midi(path / "raw.mid", key, document["native_to_source_offset"])
        # Check the independent tempo-map parse against every canonical source event.
        buckets = defaultdict(deque)
        for n in parsed["notes"]:
            signature = (n["pitch"], n["program"], n["is_drum"], round(n["onset"], 9), round(n["offset"], 9))
            buckets[signature].append(n)
        for raw in document["notes"]:
            signature = (raw["pitch"], raw["program"], raw["is_drum"], round(raw["onset"], 9), round(raw["offset"], 9))
            if not buckets[signature]:
                raise ValueError(f"MIDI/canonical timestamp mismatch: {key}/{raw['id']}")
            m = buckets[signature].popleft()
            n = deepcopy(raw)
            n.update(event_id=f"{key}:{run['run_id'][:16]}:{raw['id']}", source_key=key,
                     source_model="YourMT3+ mix" if key == "yourmt3_mix" else "YourMT3+ Other",
                     channel=m["channel"], midi_track=m["track"], sustain_end=m["sustain_end"])
            notes.append(n)
        if any(buckets.values()):
            raise ValueError("Additional MIDI events absent from canonical JSON")
        sources[key] = {"run": run, "document": document, "midi": {k:v for k,v in parsed.items() if k != "notes"},
                        "raw_midi_path": str(path / "raw.mid"), "excerpt": str(excerpt), "original_audio": original,
                        "alignment_offset": document["native_to_source_offset"], "alignment_provenance": entry["clock_offset_provenance"]}
    return {"schema": "midi-review-raw/1", "clock": "source_recording_seconds", "focus": [6., 18.],
            "context": [0., 30.], "reference_sha256": manifest["external_demo_audio_sha256"],
            "sources": sources, "notes": notes, "basic_pitch_used": False}


def add_songscription(raw, path, offset, alignment_note):
    if offset is None or not alignment_note:
        raise ValueError("Supply an explicit --songscription-offset (including 0) and --alignment-note after checking the reference audio")
    parsed = parse_midi(path, "songscription", offset)
    for n in parsed["notes"]:
        valid_note(n)
        n.update(source_model="Songscription", source_stem="shared reference mix", source_sha256=raw["reference_sha256"],
                 native_onset=n["onset"]-offset, native_offset=n["offset"]-offset, midi_track=n["track"])
    raw["notes"].extend(parsed.pop("notes"))
    raw["sources"]["songscription"] = {"midi": parsed, "raw_midi_path": str(Path(path).resolve()),
        "alignment_offset": offset, "alignment_provenance": alignment_note,
        "model_lineage": "external user-supplied export; underlying Songscription model unverified"}


def _csv(path, notes):
    fields = ["event_id", "source_key", "source_model", "onset", "offset", "duration", "pitch", "instrument", "track", "program", "channel", "is_drum", "velocity", "source_stem", "sustain_end"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(notes)


def _text(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"  # Never execute model names or reviewer text as formulas.


def _sheet(wb, name, headers, widths):
    ws = wb.create_sheet(name); ws.append(headers)
    ws.freeze_panes = "B2"; ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 90
    ws.row_dimensions[1].height = 44
    for i, header in enumerate(headers, 1):
        cell = ws.cell(1, i); cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="263D56"); cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[cell.column_letter].width = widths[i-1] if i <= len(widths) else 24
    return ws


def _table(ws, name):
    tab = Table(displayName=name, ref=f"A1:{openpyxl.utils.get_column_letter(ws.max_column)}{max(2, ws.max_row)}")
    tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=False)
    ws.add_table(tab)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial", size=10, color="222222")
            cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[row[0].row].height = 19


def _list(ws, col, options, end=5000):
    dv = DataValidation(type="list", formula1='"'+','.join(options)+'"', allow_blank=True)
    dv.errorTitle = "Choose a listed value"; dv.error = "Use a value from the dropdown."; dv.showErrorMessage = True
    ws.add_data_validation(dv); dv.add(f"{col}2:{col}{end}")


def _rule(ws, range_, formula, color):
    ws.conditional_formatting.add(range_, FormulaRule(formula=[formula], stopIfTrue=True, fill=PatternFill("solid", fgColor=color)))


def _integer_cell(value, label, maximum):
    value = number(value, label)
    if value != int(value) or not 0 <= value <= maximum:
        raise ValueError(f"{label} must be an integer from 0 to {maximum}")
    return int(value)


def _write_guide(output, primary, external):
    guide=f'''# Meaningful Love MIDI review workbook

The workbook contains {len(external)} total note rows from the existing YourMT3+ mix and Other-stem runs, preserving the complete 0–30-second context. Times are numeric seconds on the original-song clock. The main review interval is 6.000–18.000 seconds. Display rounds to three decimals; stored event times are not rounded or beat-quantized. `All Notes` source columns stay unchanged. Edit amber review cells instead. The sidecar `raw-events.json` holds source hashes, native MIDI tempo maps, track/channel/control data, parser warnings, run IDs, and checkpoint identity.

## Sheets

- `All Notes`: one row per original MIDI event in chronological order. A–J and S–W are source fields, E and Q are duration calculations, K–P are reviewer fields, and X counts events with an onset within 5 ms. Filter X to find near-simultaneous notes, then filter Evaluation Interval and Drum to focus the table. Stable Event IDs associate edits with source notes.
- `Model Comparison`: YourMT3+ mix versus Songscription. Songscription is currently pending. Matching uses configurable onset, offset, and duration tolerances plus pitch; instrument context is advisory by default. Only unique reciprocal candidates are resolved. Ambiguous groups stay unresolved. Timing differences are Songscription minus YourMT3+. Agreement is not accuracy.
- `Review & Corrections`: independent verified/draft notes and labels. Use `Missing Note` to add a note without editing model rows. `False Positive` removes a prediction only when verified. Rows remain linked by event IDs.
- `Summary`: event counts, available model comparisons, manual review counts, and declared review coverage. Accuracy is evaluated by `import-review` only for explicitly complete intervals with verified labels; see `review-metrics.json` in the import output.
- `Settings & Sources`: matching tolerances, audio/source provenance, MIDI parsing warnings, and interval coverage declarations. Set `Complete` only after reviewing every pitched note in that interval.

## Reproduction

From the repository root in WSL, use the existing AMT environment. The workbook dependency is `openpyxl`; MIDI parsing uses `mido`.

```sh
output/amt/env/bin/python -B -m unittest scripts.midi_pipeline.test_review_workbook scripts.midi_pipeline.test_pipeline -v
output/amt/env/bin/python -B -m scripts.midi_pipeline.review_workbook export --output output/midi_pipeline/phase3/meaningful-love-workbook-v4
```

Exports refuse to overwrite a version directory. To compare a Songscription MIDI, first check it against the exact `reference.wav`, then pass a reviewed global offset (including explicit `0`) and provenance note:

```sh
output/amt/env/bin/python -B -m scripts.midi_pipeline.review_workbook export --songscription-midi output/midi_pipeline/phase3/meaningful-love-v1/external/songscription.mid --songscription-offset 0 --alignment-note "Checked export against the shared reference.wav start" --output output/midi_pipeline/phase3/meaningful-love-workbook-v3
```

To import edits from this workbook into new corrected JSON and MIDI outputs, run:

```sh
output/amt/env/bin/python -B -m scripts.midi_pipeline.review_workbook import-review --workbook {output.as_posix()}/meaningful_love_midi_review.xlsx --output output/midi_pipeline/phase3/meaningful-love-reviewed-v1 --model {primary} --midi
```

The importer checks raw event cells and sidecar hash, validates editable times, preserves event IDs, and records correction provenance. It never changes raw AMT MIDI or model weights. Corrected MIDI uses absolute seconds encoded at a 16.7-microsecond tick resolution and is not snapped to beats. Partial annotations remain partial; model agreement and listening notes alone do not become ground truth. Basic Pitch is not imported or used.
'''
    (output/"WORKBOOK_REVIEW_GUIDE.md").write_text(guide,encoding="utf-8")


def _cache_formulas(path, caches):
    """Store independently computed initial formula values; Excel still recalculates.

    openpyxl writes formulas but has no calculation engine. Only known formulas
    created here are cached; importer never trusts cached correction durations.
    """
    tmp = path.with_suffix(".cached.xlsx")
    with ZipFile(path) as src, ZipFile(tmp, "w", ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename in caches:
                root = ET.fromstring(data)
                for cell in root.findall(".//m:c", NS):
                    if cell.attrib["r"] not in caches[item.filename]:
                        continue
                    value = caches[item.filename][cell.attrib["r"]]
                    v = cell.find("m:v", NS)
                    if v is None:
                        v = ET.SubElement(cell, "{"+NS["m"]+"}v")
                    if isinstance(value, str):
                        cell.set("t", "str"); v.text = value
                    else:
                        cell.attrib.pop("t", None)
                        v.text = str(value)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            dst.writestr(item, data)
    tmp.replace(path)


def build_workbook(raw, output, config=None, primary="yourmt3_mix", carry=None):
    config = config or MatchConfig(); config.validate()
    if primary not in raw["sources"]:raise ValueError("Unknown primary comparison source")
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    raw["notes"].sort(key=lambda n: (n["onset"], n["source_key"], n["track"], n["pitch"], n["event_id"]))
    raw["matching"] = asdict(config); raw["primary_comparison_source"] = primary
    save(output / "raw-events.json", raw)
    _csv(output / "raw_yourmt3_notes.csv", [n for n in raw["notes"] if n["source_key"].startswith("yourmt3")])
    external = "songscription" in raw["sources"]
    if external:
        _csv(output / "songscription_notes.csv", [n for n in raw["notes"] if n["source_key"] == "songscription"])
    wb = Workbook(); wb.remove(wb.active)
    wb.calculation = CalcProperties(calcId=191029, fullCalcOnLoad=True, forceFullCalc=True)
    ws = _sheet(wb, "All Notes", ALL_HEADERS, [19,12,14,19,18,21,28,11,31,38,22,18,21,21,17,46,22,20,14,14,10,22,22,34])
    cache = {}; allcache = {}
    for row, n in enumerate(raw["notes"], 2):
        data = [n["onset"], note_name(n["pitch"]), n["pitch"], n["offset"], f"=D{row}-A{row}", n["source_model"], f"{n['track']}: {n['instrument']}", n["velocity"], n.get("source_stem", "shared reference mix"), n["event_id"], "Unreviewed", None, None, None, "Unknown", None,
            f'=IF(AND(M{row}="",N{row}=""),"",IF(N{row}="",D{row},N{row})-IF(M{row}="",A{row},M{row}))', "6–18 s" if 6 <= n["onset"] < 18 else "Context", n["program"], n["channel"], n["is_drum"], n.get("sustain_end"), n.get("midi_track", n["track"]),f'=COUNTIFS($A$2:$A${len(raw["notes"])+1},">="&A{row}-0.005,$A$2:$A${len(raw["notes"])+1},"<="&A{row}+0.005)']
        ws.append(data); allcache[f"E{row}"] = n["duration"]; allcache[f"Q{row}"] = ""
        allcache[f"X{row}"] = sum(abs(m["onset"]-n["onset"])<=.005+1e-12 for m in raw["notes"])
        for col in (1,4,5,13,14,17,22):
            ws.cell(row,col).number_format = "0.000"
        for col in range(11,17):
            ws.cell(row,col).fill = PatternFill("solid", fgColor="FFF5D6")
        ws.cell(row,11).border = Border(left=Side(style="medium", color="BE9C51"))
    _table(ws, "AllNoteEvents")
    _list(ws,"K",STATUSES); _list(ws,"O",ROLES)
    _rule(ws,f"K2:P{ws.max_row}",'$K2="Correct"',"E0EFDE")
    _rule(ws,f"K2:P{ws.max_row}",'$K2="Incorrect"',"F9DDDA")
    _rule(ws,f"Q2:Q{ws.max_row}",'AND(ISNUMBER(Q2),Q2<=0)',"F9DDDA")
    _rule(ws,f"A2:A{ws.max_row}",'AND($A2>=6,$A2<18)',"E7EEF8")
    cache["xl/worksheets/sheet1.xml"] = allcache
    comp = _sheet(wb, "Model Comparison", COMPARISON_HEADERS, [20,17,18,18,18,20,19,19,20,19,23,23,23,29,19,45,38,38,28,28,26,23,23,23,55,20])
    a = [n for n in raw["notes"] if n["source_key"] == primary]
    b = [n for n in raw["notes"] if n["source_key"] == "songscription"]
    pairs = compare_notes(a,b,config) if external else []
    compcache = {}
    for row, pair in enumerate(pairs, 2):
        x,y = pair["a"],pair["b"]
        data = [pair["reference_start"],note_name(x["pitch"]) if x else None,x["onset"] if x else None,x["offset"] if x else None,x["duration"] if x else None,note_name(y["pitch"]) if y else None,y["onset"] if y else None,y["offset"] if y else None,y["duration"] if y else None]
        both = x is not None and y is not None
        data += [y["pitch"]-x["pitch"] if both else None, (y["onset"]-x["onset"])*1000 if both else None,(y["offset"]-x["offset"])*1000 if both else None,(y["duration"]-x["duration"])*1000 if both else None,pair["status"],"Unreviewed",None,x["event_id"] if x else None,y["event_id"] if y else None,x["instrument"] if x else None,y["instrument"] if y else None,"Same program" if both and x["program"]==y["program"] else "Unverified / differs",f'=IF(K{row}="","",ABS(K{row}))',f'=IF(L{row}="","",ABS(L{row}))',f'=IF(M{row}="","",ABS(M{row}))',", ".join(pair["candidate_event_ids"]),"6–18 s" if 6<=pair["reference_start"]<18 else "Context"]
        comp.append(data)
        for col in (1,3,4,5,7,8,9,11,12,13,22,23,24):
            comp.cell(row,col).number_format="0.000"
        for target,source in (("V","K"),("W","L"),("X","M")):
            compcache[f"{target}{row}"] = abs(comp[f"{source}{row}"].value) if both else ""
    if not external:
        comp.append([None]*13+["Pending Songscription MIDI","Unreviewed","Import an aligned MIDI to calculate comparison values."])
    _table(comp,"ModelEventComparison"); _list(comp,"O",["Unreviewed","Accepted","Rejected","Uncertain"])
    for row in comp.iter_rows(min_row=2, min_col=15, max_col=16):
        for cell in row: cell.fill=PatternFill("solid",fgColor="FFF5D6")
    _rule(comp,f"A2:P{comp.max_row}",'OR($O2="Rejected",$N2="YourMT3+ only",$N2="Songscription only")',"F9DDDA")
    _rule(comp,f"A2:P{comp.max_row}",'AND($O2<>"Rejected",$N2="Agreement")',"E0EFDE")
    _rule(comp,f"A2:P{comp.max_row}",'OR($N2="Pitch disagreement",$N2="Timing disagreement",LEFT($N2,9)="Ambiguous")',"FFF0CB")
    cache["xl/worksheets/sheet2.xml"] = compcache
    review = _sheet(wb,"Review & Corrections",REVIEW_HEADERS,[21,38,22,25,21,21,21,20,18,28,16,16,18,50,30,12])
    reviewcache = {}
    for row in range(2,202):
        review.append([f"manual-{row-1:04d}",None,None,None,None,None,None,f'=IF(OR(F{row}="",G{row}=""),"",G{row}-F{row})',"Unknown",None,None,None,"Draft",None,f'=IF(D{row}="","",IF(B{row}="","Unmatched manual event","Linked event"))',None])
        reviewcache[f"H{row}"]=""; reviewcache[f"O{row}"]=""
        for col in (6,7,8): review.cell(row,col).number_format="0.000"
        for col in list(range(1,8))+list(range(9,15))+[16]: review.cell(row,col).fill=PatternFill("solid",fgColor="FFF5D6")
    _table(review,"ManualAnnotations"); _list(review,"C",list(raw["sources"])); _list(review,"D",ACTIONS); _list(review,"I",ROLES); _list(review,"M",["Draft","Verified","Uncertain"]); _list(review,"P",["TRUE","FALSE"])
    _rule(review,"H2:H5000",'AND(ISNUMBER(H2),H2<=0)',"F9DDDA")
    cache["xl/worksheets/sheet3.xml"] = reviewcache
    summary = _sheet(wb,"Summary",["Metric / scope","Value","Interpretation"],[48,30,95])
    summaryrows = [
        ["Context start (seconds)",0,"Original-song absolute clock"],["Context end (seconds)",30,"No timing reset at 6 seconds"],
        ["Primary review start (seconds)",6,"Blue timestamps flag notes starting in 6–18 seconds"],["Primary review end (seconds)",18,"End boundary excluded for onset-based counts"],
        ["Comparison source",primary,"Other-stem predictions stay separate; comparison uses this source only"],
        ["Songscription", "Available" if external else "Pending", "Agreement is not note accuracy"],
    ]
    for key in raw["sources"]:
        ns=[n for n in raw["notes"] if n["source_key"]==key]
        summaryrows += [[f"{key}: notes (0–30 s)",len(ns),"Includes drum events; every original event is retained"], [f"{key}: pitched onsets (6–18 s)",sum(not n["is_drum"] and 6<=n["onset"]<18 for n in ns),"Counts notes, not inferred audible parts"]]
    last = len(raw["notes"])+1
    summaryrows += [["Reviewed correct events",f'=COUNTIFS(\'All Notes\'!K2:K{last},"Correct")',"Live count across both local sources"],["Reviewed incorrect events",f'=COUNTIFS(\'All Notes\'!K2:K{last},"Incorrect")',"Incorrect alone does not delete a note; use False Positive annotation"],["Remaining unreviewed events",f'=COUNTIFS(\'All Notes\'!K2:K{last},"Unreviewed")',"Missing Context and Uncertain remain distinct review statuses"],["Verified manual annotations",'=COUNTIFS(\'Review & Corrections\'!M2:M5000,"Verified",\'Review & Corrections\'!D2:D5000,"<>")',"Only Verified annotations contribute manual reference labels"]]
    focused=[p for p in pairs if 6<=p["reference_start"]<18]
    for label, predicate in [("Matched event pairs (6–18 s)",lambda p:p["a"] is not None and p["b"] is not None),("Pitch disagreements (6–18 s)",lambda p:p["status"]=="Pitch disagreement"),("YourMT3+ only (6–18 s)",lambda p:p["status"]=="YourMT3+ only"),("Songscription only (6–18 s)",lambda p:p["status"]=="Songscription only"),("Ambiguous events (6–18 s)",lambda p:p["status"].startswith("Ambiguous"))]:
        summaryrows.append([label,sum(predicate(p) for p in focused) if external else "Pending","Comparison, not accuracy; Reference Start is earliest available onset"])
    for field in ("onset","offset","duration"):
        values=[abs(p["b"][field]-p["a"][field])*1000 for p in focused if p["a"] is not None and p["b"] is not None]
        for method,fn in (("Mean",statistics.mean),("Median",statistics.median)):
            summaryrows.append([f"{method} absolute {field} difference (ms)",fn(values) if values else ("Pending" if not external else "No resolved pairs"),"Songscription minus YourMT3+ before taking absolute values; focus window only"])
    coverage_range="'Settings & Sources'!$J$2:$J$51"
    summaryrows += [
        ["Manual reference coverage",f'=IF(COUNTIF({coverage_range},"Complete")=0,"Not yet reviewed","Declared; validate with import-review")',"Complete intervals are declared on Settings & Sources; partial rows are not exhaustive labels"],
        ["Declared complete intervals",f'=COUNTIF({coverage_range},"Complete")',"import-review validates times and merges overlapping intervals for coverage metrics"],
        ["Earliest reviewed start (seconds)",f'=IF(COUNTIF({coverage_range},"Complete")=0,"Pending",_xlfn.MINIFS(\'Settings & Sources\'!$H$2:$H$51,{coverage_range},"Complete"))',"Declared interval boundary; import rejects missing/invalid times"],
        ["Latest reviewed end (seconds)",f'=IF(COUNTIF({coverage_range},"Complete")=0,"Pending",_xlfn.MAXIFS(\'Settings & Sources\'!$I$2:$I$51,{coverage_range},"Complete"))',"Gaps are not reviewed; see the individual coverage rows"],
        ["Accuracy metrics","Pending verified coverage","import-review writes review-metrics.json for declared complete, verified portions"],
        ["Raw/source fields","All Notes A:J and S:W","Import rejects changed raw fields; K:P are manual, E/Q/X are derived"],
        ["Source/control metadata","raw-events.json","Source hashes, tempo maps, channels, CC64 sustain, warnings and run/checkpoint identities"],
        ["Matching controls","Settings & Sources","Apply matching changes by exporting a new version with --config; cells document current export settings"]]
    sumcache={}
    for row,data in enumerate(summaryrows,2):
        summary.append(data)
        if isinstance(data[1],str) and data[1].startswith("="):
            sumcache[f"B{row}"]=(len(raw["notes"]) if data[0].startswith("Remaining") else
                                  "Not yet reviewed" if data[0]=="Manual reference coverage" else
                                  "Pending" if data[0].startswith(("Earliest reviewed","Latest reviewed")) else 0)
        if "seconds" in data[0] or "difference (ms)" in data[0]: summary.cell(row,2).number_format="0.000"
    _table(summary,"ReviewSummary")
    cache["xl/worksheets/sheet4.xml"]=sumcache
    settings=_sheet(wb,"Settings & Sources",["Setting / provenance","Value","Meaning"],[37,75,95])
    settingsrows=[["Schema","midi-review-workbook/1",""],["Raw sidecar","raw-events.json","Required for import integrity; keep with workbook"],["Raw sidecar SHA-256",digest(output/"raw-events.json"),"Read-only source identity"],["Primary comparison source",primary,""],["Reference WAV SHA-256",raw["reference_sha256"],"Verified against executed inputs and preserved original audio hashes"],["Clock","source_recording_seconds","Original recording starts at zero; all source times remain absolute"],["Duration definition","physical key-release minus note-on","Sustain End is separate; pedal release may prolong sound"],["Onset tolerance (seconds)",config.onset,"Exact-pitch reciprocal candidates"],["Offset tolerance (seconds)",config.offset,"Used with duration and onset to resolve agreement"],["Duration tolerance (seconds)",config.duration,""],["Candidate onset window (seconds)",config.candidate_onset,"Same-pitch timing-disagreement search"],["Candidate duration window (seconds)",config.candidate_duration,""],["Instrument mode",config.instrument,"advisory preserves program context; program/family enforce compatibility"],["Pairing policy","Unique reciprocal candidates only","Non-unique candidate sets stay ambiguous; IDs are not matched by row order"],["Difference sign","Songscription minus YourMT3+","Reference Start is min(pair onsets), not ground truth"],["Review workflow","Edit amber cells; keep source columns","False Positive removes only with an explicit Verified annotation"],["Coverage policy","Complete means ALL pitched notes reviewed","Only verified note labels inside complete intervals enable accuracy; not full-song accuracy"]]
    for key,source in raw["sources"].items():
        settingsrows.extend([[f"{key}: source",source["raw_midi_path"],"Historical raw MIDI stays unchanged"],[f"{key}: alignment seconds",source["alignment_offset"],source["alignment_provenance"]],[f"{key}: parser diagnostics",json.dumps(source["midi"]["warnings"]),"Missing offs are excluded and reported, never given fabricated end times"]])
    for row in settingsrows: settings.append(row)
    for row in settings.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value,str): _text(c,c.value)
            if c.column in (2,3):
                c.alignment=Alignment(wrap_text=True,vertical="center")
        settings.row_dimensions[row[0].row].height=max(20,14*max(math.ceil(len(str(c.value or ""))/int(settings.column_dimensions[c.column_letter].width)) for c in row))
    for col,header in enumerate(["Coverage ID","Start (seconds)","End (seconds)","Coverage Status","Notes"],7):
        settings.cell(1,col,header);settings.column_dimensions[openpyxl.utils.get_column_letter(col)].width=24 if col!=11 else 45
        settings.cell(1,col).font=Font(name="Arial",size=10,bold=True,color="FFFFFF")
        settings.cell(1,col).fill=PatternFill("solid",fgColor="263D56")
        settings.cell(1,col).alignment=Alignment(wrap_text=True,horizontal="center",vertical="center")
    for row in range(2,52):
        settings.cell(row,7,f"coverage-{row-1:03d}")
        for col in (8,9):settings.cell(row,col).number_format="0.000"
        for col in range(8,12):settings.cell(row,col).fill=PatternFill("solid",fgColor="FFF5D6")
    _list(settings,"J",["Draft","Complete"])
    _rule(settings,"H2:J51",'AND($J2="Complete",OR(NOT(ISNUMBER($H2)),NOT(ISNUMBER($I2)),$H2<0,$I2<=$H2,$I2>30))',"F9DDDA")
    if carry:
        _carry_reviews(wb,load_workbook(carry))
        # Recompute the few cached values that manual carry-forward changes.
        for row in range(2,ws.max_row+1):
            m,n=ws.cell(row,13).value,ws.cell(row,14).value
            allcache[f"Q{row}"]="" if m is None and n is None else (ws.cell(row,4).value if n is None else number(n))-(ws.cell(row,1).value if m is None else number(m))
        for row in range(2,summary.max_row+1):
            label=summary.cell(row,1).value
            for prefix,status in (("Reviewed correct","Correct"),("Reviewed incorrect","Incorrect"),("Remaining unreviewed","Unreviewed")):
                if label.startswith(prefix):sumcache[f"B{row}"]=sum(ws.cell(r,11).value==status for r in range(2,ws.max_row+1))
            complete=[r for r in range(2,52) if settings.cell(r,10).value=="Complete"]
            if label=="Manual reference coverage":sumcache[f"B{row}"]="Declared; validate with import-review" if complete else "Not yet reviewed"
            if label=="Declared complete intervals":sumcache[f"B{row}"]=len(complete)
            if label=="Earliest reviewed start (seconds)" and complete:sumcache[f"B{row}"]=min(number(settings.cell(r,8).value) for r in complete)
            if label=="Latest reviewed end (seconds)" and complete:sumcache[f"B{row}"]=max(number(settings.cell(r,9).value) for r in complete)
            if label=="Verified manual annotations":sumcache[f"B{row}"]=sum(review.cell(r,13).value=="Verified" and bool(review.cell(r,4).value) for r in range(2,review.max_row+1))
    path=output/"meaningful_love_midi_review.xlsx"
    wb.save(path); _cache_formulas(path,cache)
    save(output/"comparison-data.json",{"status":"available" if external else "Pending", "matching":asdict(config),"rows":pairs,"interpretation":"Model agreement is not accuracy"})
    save(output/"matching-config.json",asdict(config))
    save(output/"integrity-check.json",verify_workbook(path,raw))
    _write_guide(output,primary,raw["notes"])
    return path


def _carry_reviews(wb, old):
    previous={old["All Notes"].cell(r,10).value:[old["All Notes"].cell(r,c).value for c in range(11,17)] for r in range(2,old["All Notes"].max_row+1)}
    for row in range(2,wb["All Notes"].max_row+1):
        if wb["All Notes"].cell(row,10).value in previous:
            for col,value in enumerate(previous[wb["All Notes"].cell(row,10).value],11): _text(wb["All Notes"].cell(row,col),value)
    for row in range(2,old["Review & Corrections"].max_row+1):
        for col in list(range(1,8))+list(range(9,15))+[16]: _text(wb["Review & Corrections"].cell(row,col),old["Review & Corrections"].cell(row,col).value)
    for row in range(2,52):
        for col in range(7,12): _text(wb["Settings & Sources"].cell(row,col),old["Settings & Sources"].cell(row,col).value)
    comparisons={(old["Model Comparison"].cell(r,17).value,old["Model Comparison"].cell(r,18).value):
                 [old["Model Comparison"].cell(r,c).value for c in (15,16)]
                 for r in range(2,old["Model Comparison"].max_row+1)}
    for row in range(2,wb["Model Comparison"].max_row+1):
        key=(wb["Model Comparison"].cell(row,17).value,wb["Model Comparison"].cell(row,18).value)
        if key in comparisons and key!=(None,None):
            for col,value in zip((15,16),comparisons[key]):_text(wb["Model Comparison"].cell(row,col),value)


def verify_workbook(path,raw):
    wb=load_workbook(path,data_only=False); values=load_workbook(path,data_only=True)
    expected=["All Notes","Model Comparison","Review & Corrections","Summary","Settings & Sources"]
    if wb.sheetnames!=expected: raise ValueError("Missing or unexpected worksheets")
    ws=wb["All Notes"]
    if ws["A1"].value!=ALL_HEADERS[0] or ws.freeze_panes!="B2":raise ValueError("Header/freeze configuration")
    starts=[ws.cell(r,1).value for r in range(2,ws.max_row+1)]
    expected_starts=sorted(n["onset"] for n in raw["notes"])
    # Excel serializes doubles to ~15 significant digits. Do not mistake its
    # last-bit representation change for timing quantization or a sort failure.
    if starts != sorted(starts) or len(starts)!=len(expected_starts) or any(abs(a-b)>1e-10 for a,b in zip(starts,expected_starts)):
        raise ValueError("Events are not chronologically sorted")
    if ws.max_row-1!=len(raw["notes"]):raise ValueError("Notes merged or missing")
    immutable=_check_original_rows(wb,raw)
    for row in range(2,ws.max_row+1):
        for col in (1,4,5):number(values["All Notes"].cell(row,col).value)
        if abs(values["All Notes"].cell(row,5).value-(ws.cell(row,4).value-ws.cell(row,1).value))>1e-10:raise ValueError("Incorrect duration")
        simultaneous=sum(abs(n["onset"]-ws.cell(row,1).value)<=.005+1e-12 for n in raw["notes"])
        if values["All Notes"].cell(row,24).value!=simultaneous:raise ValueError("Incorrect near-simultaneous event count")
    if values["Settings & Sources"]["B4"].value != digest(Path(path).parent/"raw-events.json"):
        raise ValueError("Source sidecar metadata mismatch")
    return {"status":"passed","sheets":expected,"event_count":len(immutable),"numeric_timestamps":True,
            "chronological":True,"duration_checks":len(immutable),"simultaneous_events_preserved":True,
            "frozen_header_and_start_column":True,"source_metadata":True,"basic_pitch_used":False,
            "engine_note":"Saved XLSX reopened with openpyxl; formula caches checked independently. Native Excel/Google Sheets UI not automated."}


def _check_original_rows(wb,raw):
    expected={n["event_id"]:n for n in raw["notes"]}; found={}
    ws=wb["All Notes"]
    if [ws.cell(1,c).value for c in range(1,len(ALL_HEADERS)+1)]!=ALL_HEADERS:raise ValueError("All Notes headers changed")
    for row in range(2,ws.max_row+1):
        key=ws.cell(row,10).value
        if not key:continue
        if key not in expected or key in found:raise ValueError("Unknown or duplicate event ID")
        n=expected[key]
        original={1:n["onset"],2:note_name(n["pitch"]),3:n["pitch"],4:n["offset"],6:n["source_model"],7:f"{n['track']}: {n['instrument']}",8:n["velocity"],9:n.get("source_stem","shared reference mix"),19:n["program"],20:n["channel"],21:n["is_drum"],22:n.get("sustain_end"),23:n.get("midi_track",n["track"])}
        for col,value in original.items():
            actual=ws.cell(row,col).value
            equal=abs(number(actual)-value)<1e-10 if isinstance(value,(int,float)) and not isinstance(value,bool) else actual==value
            if not equal:raise ValueError(f"Raw source cell changed: {ws.cell(row,col).coordinate}; use review fields")
        found[key]=row
    if set(found)!=set(expected):raise ValueError("Original event rows missing; do not delete source rows")
    return found


def import_review(path,output,raw_path=None,model="yourmt3_mix",midi=False):
    path=Path(path); wb=load_workbook(path,data_only=False)
    raw_path=Path(raw_path) if raw_path else path.parent/"raw-events.json"
    if digest(raw_path)!=wb["Settings & Sources"]["B4"].value:raise ValueError("Raw sidecar hash mismatch")
    raw=_read(raw_path); rows=_check_original_rows(wb,raw)
    if model not in raw["sources"]:raise ValueError("Unknown correction model")
    result={n["event_id"]:deepcopy(n) for n in raw["notes"] if n["source_key"]==model}
    original={n["event_id"]:n for n in raw["notes"]}
    annotations=[]; changes=[]; touched={}; removed=set(); verified_actions=defaultdict(set)
    def change(n,field,value,where):
        key=(n["event_id"],field)
        if key in touched and touched[key]!=value:raise ValueError(f"Conflicting corrections: {key}")
        touched[key]=value
        if n.get(field)!=value:
            changes.append({"event_id":n["event_id"],"field":field,"original":n.get(field),"corrected":value,"cell":where})
            n[field]=value
    for key,row in rows.items():
        ws=wb["All Notes"]; status=ws.cell(row,11).value or "Unreviewed"
        if status not in STATUSES:raise ValueError("Invalid manual review status")
        role=ws.cell(row,15).value or "Unknown"
        if role not in ROLES:raise ValueError("Invalid musical role")
        for col in (12,13,14):
            value=ws.cell(row,col).value
            if value is not None:
                parse_pitch(value) if col==12 else number(value,ws.cell(row,col).coordinate)
        test_note=deepcopy(original[key])
        for col,field in ((12,"pitch"),(13,"onset"),(14,"offset")):
            value=ws.cell(row,col).value
            if value is not None:test_note[field]=parse_pitch(value) if col==12 else number(value)
        valid_note(test_note)
        if key not in result:continue
        n=result[key]
        n.update(manual_review_status="Unreviewed",musical_role="Unknown",reviewer_notes=None)
        for field,value,col in (("manual_review_status",status,11),("musical_role",role,15),("reviewer_notes",ws.cell(row,16).value,16)):
            change(n,field,value,f"All Notes!{ws.cell(row,col).coordinate}")
        for col,field in ((12,"pitch"),(13,"onset"),(14,"offset")):
            value=ws.cell(row,col).value
            if value is not None:change(n,field,parse_pitch(value) if col==12 else number(value),f"All Notes!{ws.cell(row,col).coordinate}")
    review=wb["Review & Corrections"]; seen=set()
    for row in range(2,review.max_row+1):
        vals=[review.cell(row,c).value for c in range(1,17)]
        aid,linked,source,action,pitch,start,end,_,role,instrument,program,channel,verification,comment,_,drum=vals
        if not action:
            if any(v is not None for v in (linked,pitch,start,end,instrument,program,channel,comment)):raise ValueError(f"Manual row {row} needs an Action")
            continue
        if not aid or aid in seen:raise ValueError("Manual annotation IDs must be unique and nonempty")
        seen.add(aid)
        if action not in ACTIONS or verification not in ("Draft","Verified","Uncertain"):raise ValueError("Invalid annotation action/verification")
        if linked and linked not in original:raise ValueError("Manual annotation references unknown event")
        if linked and source and source!=original[linked]["source_key"]:raise ValueError("Annotation source and original event disagree")
        if not linked and not source:raise ValueError("Unmatched annotation needs Source Model key")
        if source and source not in raw["sources"]:raise ValueError("Unknown manual annotation Source Model")
        if role and role not in ROLES:raise ValueError("Invalid role")
        for value,label in ((start,"manual start"),(end,"manual end")):
            if value is not None:number(value,label)
        if start is not None and start<0 or end is not None and end<0 or start is not None and end is not None and end<=start:raise ValueError("Invalid manual timing")
        if pitch is not None:pitch=parse_pitch(pitch)
        if program is not None:program=_integer_cell(program,"MIDI Program",127)
        if channel is not None:channel=_integer_cell(channel,"MIDI Channel",15)
        if drum is not None and not (isinstance(drum,bool) or isinstance(drum,str) and drum.upper() in ("TRUE","FALSE")):
            raise ValueError("Drum must be TRUE or FALSE")
        item={"annotation_id":aid,"original_event_id":linked,"source_key":original[linked]["source_key"] if linked else source,"action":action,"verification":verification,"comment":comment,"row":row,"pitch":pitch,"onset":start,"offset":end,"musical_role":role,"instrument":instrument,"program":program,"channel":channel,"drum":drum}
        annotations.append(item)
        if verification!="Verified" or item["source_key"]!=model:continue
        if action=="Observation":continue
        if linked:
            prior=verified_actions[linked]
            if (action=="False Positive" and prior-{"False Positive"}) or (action!="False Positive" and "False Positive" in prior):
                raise ValueError("Conflicting verified false-positive and note annotations")
            prior.add(action)
        if action=="Missing Note":
            if linked:raise ValueError("Missing Note must be a new, unmatched event")
            if any(v is None for v in (pitch,start,end)):raise ValueError("Missing Note needs pitch, start and end")
            key=f"manual:{aid}"; n={"id":key,"event_id":key,"source_key":model,"source_model":"Manual review","track":-1,"instrument":instrument or "Manual part","program":program or 0,"channel":channel or 0,"is_drum":str(drum).upper()=="TRUE","velocity":100,"pitch":pitch,"onset":start,"offset":end,"musical_role":role or "Unknown","reviewer_notes":comment,"manual_review_status":"Correct","annotation_id":aid}
            result[key]=n;changes.append({"event_id":key,"action":"manual_addition","annotation":item})
        else:
            if not linked:raise ValueError("This annotation action needs Original Event ID")
            n=result[linked]
            if action=="False Positive":removed.add(linked); changes.append({"event_id":linked,"action":"removed_false_positive","annotation":item});continue
            required={"Pitch Correction":"pitch","Onset Correction":"onset","Offset Correction":"offset","Instrument Correction":"instrument"}.get(action)
            fields={"pitch":pitch,"onset":start,"offset":end,"instrument":instrument,"program":program,"channel":channel}
            if required and fields[required] is None:raise ValueError(f"{action} requires {required}")
            for field,value in fields.items():
                if value is not None:change(n,field,value,f"Review & Corrections!row {row}")
            if role:change(n,"musical_role",role,f"Review & Corrections!I{row}")
    for key in removed:result.pop(key)
    notes=list(result.values())
    tracks={}; source=raw["sources"][model]
    for n in notes:
        valid_note(n);n["duration"]=n["offset"]-n["onset"]
        n["in_source_window"]=0<=n["onset"]<30
        signature=(n["track"],n["instrument"],n["program"],n["is_drum"])
        if signature not in tracks:tracks[signature]={"track":len(tracks),"name":n["instrument"],"program":n["program"],"is_drum":n["is_drum"]}
        n["original_track"]=n["track"];n["track"]=tracks[signature]["track"]
    notes.sort(key=lambda n:(n["onset"],n["track"],n["pitch"],n["event_id"]))
    workbook_sha=digest(path)
    provenance={"workbook_sha256":workbook_sha,"raw_sidecar_sha256":digest(raw_path),"created_utc":datetime.now(timezone.utc).isoformat(),"changes":changes,"annotations":annotations,"source_metadata":source,"raw_outputs_changed":False}
    document={"schema":"reviewed-midi-notes/1","run_id":fingerprint({"workbook":workbook_sha,"model":model}),"clock":"source_recording_seconds","source_window":[0.,30.],"tracks":list(tracks.values()),"notes":notes,"controls":source["midi"]["controls"],"review_provenance":provenance,"label_source":"partial_human_review","complete_notes":False}
    metrics=_review_metrics(wb,raw,annotations,model)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    save(output/"corrected-notes.json",document);save(output/"review-metrics.json",metrics)
    if midi:write_midi(document,output/"corrected.mid")
    return document


def _review_metrics(wb,raw,annotations,model):
    intervals=[]
    ws=wb["Settings & Sources"]
    for row in range(2,ws.max_row+1):
        if ws.cell(row,10).value=="Complete":
            start,end=number(ws.cell(row,8).value),number(ws.cell(row,9).value)
            if not 0<=start<end<=30:raise ValueError("Invalid complete review interval")
            intervals.append([start,end])
    intervals.sort(); merged=[]
    for start,end in intervals:
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    original={n["event_id"]:n for n in raw["notes"]}
    refs={}
    for item in annotations:
        if item["verification"]!="Verified" or item["source_key"]!=model or item["action"] in ("False Positive","Observation","Role Label"):continue
        key=item["original_event_id"] or f"manual:{item['annotation_id']}"
        n=deepcopy(refs.get(key,original.get(key,{})))
        for field in ("pitch","onset","offset"):
            if item[field] is not None:n[field]=item[field]
        if not all(field in n for field in ("pitch","onset","offset")):raise ValueError("Verified reference needs complete pitch/start/end")
        n.setdefault("program",0);n.setdefault("velocity",100);n.setdefault("channel",0);valid_note(n);refs[key]=n
    in_coverage=lambda n:any(start<=n["onset"]<end for start,end in merged)
    ref=[n for n in refs.values() if in_coverage(n) and not n.get("is_drum",False)]
    pred=[n for n in raw["notes"] if n["source_key"]==model and in_coverage(n) and not n.get("is_drum",False)]
    base={"model":model,"reviewed_intervals":merged,"coverage_seconds":sum(e-s for s,e in merged),"verified_reference_notes":len(ref),"scope":"only pitched onsets inside explicitly Complete intervals; source prediction vs verified human labels, not corrected output vs itself"}
    if not merged or not ref:return {**base,"status":"Pending verified complete coverage and reference notes","accuracy":None}
    # Maximum-cardinality one-to-one onset+pitch matching, deterministic shortest edges.
    edges={i:sorted([j for j,y in enumerate(pred) if x["pitch"]==y["pitch"] and abs(x["onset"]-y["onset"])<=.05],key=lambda j:abs(x["onset"]-pred[j]["onset"])) for i,x in enumerate(ref)}
    assigned={}
    def augment(i,seen):
        for j in edges[i]:
            if j not in seen:
                seen.add(j)
                if j not in assigned or augment(assigned[j],seen):assigned[j]=i;return True
        return False
    for i in edges:augment(i,set())
    count=len(assigned); precision=count/len(pred) if pred else None;recall=count/len(ref)
    errors={field:[abs(ref[i][field]-pred[j][field]) for j,i in assigned.items()] for field in ("onset","offset")}
    errors["duration"]=[abs((ref[i]["offset"]-ref[i]["onset"])-pred[j]["duration"]) for j,i in assigned.items()]
    return {**base,"status":"evaluated_reviewed_coverage_only","accuracy":{"onset_pitch_tolerance_seconds":.05,"precision":precision,"recall":recall,"f1":2*count/(len(pred)+len(ref)),"true_positive":count,"false_positive":len(pred)-count,"missed":len(ref)-count,**{f"mean_absolute_{field}_error_seconds":statistics.mean(values) if values else None for field,values in errors.items()}}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("export");p.add_argument("--phase3-root",type=Path,default=PHASE3);p.add_argument("--output",type=Path,default=OUTPUT);p.add_argument("--songscription-midi",type=Path);p.add_argument("--songscription-offset",type=float);p.add_argument("--alignment-note");p.add_argument("--config",type=Path);p.add_argument("--primary",default="yourmt3_mix");p.add_argument("--carry-review",type=Path)
    p=sub.add_parser("import-review");p.add_argument("--workbook",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--raw-events",type=Path);p.add_argument("--model",default="yourmt3_mix");p.add_argument("--midi",action="store_true")
    p=sub.add_parser("verify");p.add_argument("--workbook",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="export":
        raw=local_sources(args.phase3_root)
        if args.songscription_midi:add_songscription(raw,args.songscription_midi,args.songscription_offset,args.alignment_note)
        path=build_workbook(raw,args.output,MatchConfig(**_read(args.config)) if args.config else MatchConfig(),args.primary,args.carry_review)
        print(json.dumps({"workbook":str(path),"events":len(raw["notes"]),"integrity":"passed","songscription":"Available" if args.songscription_midi else "Pending"}))
    elif args.command=="import-review":
        doc=import_review(args.workbook,args.output,args.raw_events,args.model,args.midi)
        print(json.dumps({"output":str(args.output),"notes":len(doc["notes"]),"corrections":len(doc["review_provenance"]["changes"])}))
    else:
        result=verify_workbook(args.workbook,_read(args.workbook.parent/"raw-events.json"))
        save(args.workbook.parent/"integrity-check.json",result);print(json.dumps(result))


if __name__=="__main__":main()
