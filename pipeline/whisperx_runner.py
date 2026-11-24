"""WhisperX process in an optional, separately pinned Torch 2.8 environment."""
import argparse
import math

from .common import StageError, read, save
from .line_alignment import normalize


def align(vocals, source_lines, language, device):
    import whisperx
    import soundfile as sf
    duration = sf.info(vocals).duration
    model, metadata = whisperx.load_align_model(language_code=language, device=device)
    audio = whisperx.load_audio(vocals)
    # CTC alignment uses the actual vocal waveform and known Genius text.
    # No uniformly spaced invented line/section timestamps are supplied.
    text = " ".join(source_lines)
    result = whisperx.align([{"start": 0., "end": duration, "text": text}],
                            model, metadata, audio, device,
                            return_char_alignments=True,
                            interpolate_method="ignore")
    chars = [c for s in result["segments"] for c in s.get("chars", []) if normalize(c.get("char", ""))]
    expected = "".join(normalize(line).replace(" ", "") for line in source_lines)
    actual = "".join(normalize(c["char"]).replace(" ", "") for c in chars)
    if expected != actual:
        raise StageError("WhisperX character sequence does not preserve source text; no proportional timing fallback")
    rows, cursor = [], 0
    for line in source_lines:
        size = len(normalize(line).replace(" ", ""))
        selected = chars[cursor:cursor + size]
        cursor += size
        timed = [c for c in selected if c.get("start") is not None and c.get("end") is not None
                 and math.isfinite(c["start"]) and math.isfinite(c["end"])]
        scores = [c["score"] for c in timed if c.get("score") is not None]
        rows.append({"text": line, "start_s": min(c["start"] for c in timed) if timed else None,
                     "end_s": max(c["end"] for c in timed) if timed else None,
                     "confidence": (sum(scores) / len(scores) if scores else 0.) * len(timed) / max(1, size)})
    return {"language": language, "alignment_model": metadata.get("model_name", "language default"),
            "lines": rows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    data = read(args.request)
    if not data.get('language') or not data.get('lines'):
        import whisperx
        audio=whisperx.load_audio(data['vocals'])
        model=whisperx.load_model('small',device='cpu',compute_type='int8')
        detected=model.transcribe(audio,batch_size=1,language=data.get('language'))
        language=detected['language']
        if data.get('lines'):
            result=align(data['vocals'],data['lines'],language,'cpu')
        else:
            aligner,metadata=whisperx.load_align_model(language_code=language,device='cpu')
            aligned=whisperx.align(detected['segments'],aligner,metadata,audio,'cpu',interpolate_method='ignore')
            sections=[]
            for index,row in enumerate(aligned.get('segments',[])):
                words=[w for w in row.get('words',[]) if w.get('score') is not None]
                confidence=float(sum(w['score'] for w in words)/len(words)) if words else 0.
                valid=bool(confidence>=.65 and row['end']>row['start'])
                sections.append({'label':'Unlabeled','display_label':f'Phrase {index+1}',
                    'number':index+1,'raw_start_s':float(row['start']),'raw_end_s':float(row['end']),
                    'timing_source':'whisperx','validated':valid,'status':'complete' if valid else 'review',
                    'match_confidence':confidence,'flags':[] if valid else ['low_confidence'],
                    'failure_reasons':[] if valid else ['low_confidence'],'genius_section_index':None})
            result={'language':language,'sections':sections,'transcription_model':'small','device':'cpu'}
    else:
        result=align(data['vocals'],data['lines'],data['language'],'cpu')
    save(args.output,result)


if __name__ == "__main__":
    main()
