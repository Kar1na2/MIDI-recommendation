"""Pin round-2 model identities, hashes, licenses and non-commercial flags.

Reads only local files already downloaded by download_candidates.py plus the
round-1 manifest for carried-over models. No network access, no song data.
"""
import hashlib
import json
import sys
from pathlib import Path

if sys.executable != '/home/usr/venvs/midi/bin/python':
    raise RuntimeError('Wrong interpreter')

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = HERE / 'model_cache'

CARRIED_ROLES = {'T1', 'T2', 'M1', 'M2', 'V1', 'V2'}

NEW = {
    'T3': {
        'channel': 'words', 'dimensions': ['valence'],
        'license': 'Apache-2.0', 'noncommercial': False,
        'languages': ['en', 'ja', 'ko', 'zh', 'ar', 'de', 'es', 'fr', 'hi', 'id', 'it', 'nl', 'pt', 'ru', 'tr', 'vi'],
        'family': 'XLM-RoBERTa base, supervised 3-class sentiment',
        'distinct_from_T1': 'Different training corpora and label pipeline from clapAI MultiLingualSentiment; '
                            'both are transformer encoders, recorded as a shared architecture family.',
        'coverage_note': 'Supervised multilingual sentiment; lyric-domain and singing validity unverified.',
        'card': 'https://huggingface.co/Horizon-Labs/multilingual-sentiment-base',
        'trust_eligible': True,
    },
    'L1': {
        'channel': 'words', 'dimensions': ['valence', 'arousal'],
        'license': 'Apache-2.0', 'noncommercial': False,
        'languages': ['en', 'ja', 'ko', 'zh', 'and 100+ reported by the model card'],
        'family': 'Qwen3 4B instruct, decoder-only LLM',
        'coverage_note': 'Instruct LLM rated on section text only, with no title or artist, so it rates text '
                         'rather than recalling a known song. Runs locally in fp16 on the 4060 Ti.',
        'card': 'https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507',
        'trust_eligible': True,
    },
    'M3': {
        'channel': 'background', 'dimensions': ['valence', 'arousal'],
        'license': 'CC-BY-NC-4.0', 'noncommercial': True,
        'languages': ['audio, language independent'],
        'family': 'MERT-v1-95M self-supervised music encoder, int8 ONNX regressor head',
        'training_data': 'DEAM/MediaEval valence-arousal annotations',
        'distinct_from_M1': 'Different dataset (DEAM) and different backbone (MERT) from M1 (emoMusic on '
                            'MusiCNN) and from M2 (CLAP HTSAT), so agreement is not built in by a shared embedding.',
        'shared_backbone': False,
        'coverage_note': 'Trained on 45 s excerpts of Western popular music; 4 s window transfer is unvalidated.',
        'card': 'https://huggingface.co/huyleit/mert-v1-95m-music-emotion-int8',
        'trust_eligible': True,
    },
    'V3': {
        'channel': 'voice', 'dimensions': ['valence', 'arousal'],
        'license': 'MERaLiON Public Licence (SER v1), non-commercial research terms',
        'noncommercial': True,
        'languages': ['en', 'zh', 'ms', 'ta', 'id', 'th', 'vi'],
        'family': 'Whisper encoder plus ECAPA, dimensional SER head',
        'training_data': 'Multilingual SER corpora distinct from the MSP-Podcast data used by V1',
        'distinct_from_V1': 'Different training corpora and a different encoder family from the audEERING '
                            'wav2vec2 MSP-Podcast model.',
        'output_mapping': 'Native dimensional output (valence, arousal, dominance); dominance is recorded but '
                          'unused. No categorical-to-dimensional mapping is required.',
        'coverage_note': 'Speech emotion recognition; singing voice and separation artifacts are out of distribution.',
        'card': 'https://huggingface.co/MERaLiON/MERaLiON-SER-v1',
        'trust_eligible': True,
    },
}


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def hashed_files(role: str):
    folder = CACHE / role
    entries = []
    for path in sorted(p for p in folder.rglob('*') if p.is_file()):
        entries.append({'path': str(path.relative_to(folder)),
                        'sha256': file_digest(path),
                        'size': path.stat().st_size})
    if not entries:
        raise FileNotFoundError('No local weights found for ' + role)
    return entries


def main():
    status = json.loads((HERE / 'model_download_status.json').read_text(encoding='utf-8'))
    round1 = json.loads((ROOT / 'emotion/models.json').read_text(encoding='utf-8'))

    models = []
    for role, detail in NEW.items():
        smoke = json.loads((HERE / ('smoke_' + role + '.json')).read_text(encoding='utf-8'))
        if smoke.get('status') != 'passed':
            raise RuntimeError(role + ' smoke test has not passed; refusing to pin it')
        entry = status[role]
        if entry.get('status') != 'downloaded':
            raise RuntimeError(role + ' is not downloaded')
        models.append({'role': role, 'id': entry['repo'], 'revision': entry['revision'],
                       'round': 2, 'files': hashed_files(role),
                       'smoke_test': {'status': smoke['status'], 'details': smoke.get('details'),
                                      'input': smoke.get('input')},
                       **detail})

    carried = []
    for model in round1['models']:
        role = model.get('role')
        if role in CARRIED_ROLES:
            copy = dict(model)
            copy['round'] = 1
            copy['carried_from_round1'] = True
            if role == 'T2':
                copy['trust_eligible'] = False
                copy['trust_note'] = ('Kept for reference only. Excluded from round-2 trust decisions for words '
                                      'valence; its e5 arousal anchors remain reported as low-confidence.')
            elif role == 'V2':
                copy['trust_eligible'] = False
                copy['trust_note'] = 'Explanation-only in round 2. It is not treated as an emotion model.'
            else:
                copy['trust_eligible'] = True
            carried.append(copy)
        elif role is None and 'demucs' in json.dumps(model).lower():
            copy = dict(model)
            copy['round'] = 1
            copy['carried_from_round1'] = True
            carried.append(copy)

    result = {
        'schema_version': 2,
        'round': 2,
        'models': carried + models,
        'channel_membership': {
            'words_valence': ['T1', 'T3', 'L1'],
            'words_arousal': ['L1', 'T2_e5_anchors'],
            'background_valence': ['M1', 'M2', 'M3'],
            'background_arousal': ['M1', 'M2', 'M3'],
            'voice_valence': ['V1', 'V3'],
            'voice_arousal': ['V1', 'V3'],
        },
        'excluded_from_trust': {
            'T2': 'reference only',
            'V2': 'explanation-only prosody composite',
        },
        'license_note': ('Non-commercial flags apply to the named model weights. M1, V1, V3 and the separation '
                         'model carry non-commercial terms, so round-2 outputs are research-use only.'),
    }
    out = HERE / 'models.json'
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + chr(10), encoding='utf-8')
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (HERE / 'models_sha256.txt').write_text(digest + chr(10), encoding='utf-8')
    print('Wrote ' + str(len(result['models'])) + ' model entries (' + str(len(models)) + ' new, '
          + str(len(carried)) + ' carried).')
    print('models.json SHA-256: ' + digest)


if __name__ == '__main__':
    main()
