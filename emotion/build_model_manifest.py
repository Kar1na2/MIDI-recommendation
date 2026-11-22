"""Write a model manifest from verified downloads and documented model cards."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DETAILS = {
    'clapAI/roberta-base-multilingual-sentiment': {
        'channel': 'words', 'role': 'T1', 'license': 'Apache-2.0',
        'noncommercial': False, 'languages': ['en', 'ja', 'ko', 'zh', 'ar', 'de', 'es', 'fr', 'hi', 'id', 'it', 'ms', 'pt', 'ru', 'tr', 'vi'],
        'coverage_note': 'Model card reports 16+ languages and training on mixed-source review and sentiment datasets; lyric-domain validity is unverified.',
        'card': 'https://huggingface.co/clapAI/roberta-base-multilingual-sentiment',
        'training_data': 'https://huggingface.co/datasets/clapAI/MultiLingualSentiment',
        'weight': 'model.safetensors'},
    'intfloat/multilingual-e5-base': {
        'channel': 'words', 'role': 'T2', 'license': 'MIT', 'noncommercial': False,
        'languages': ['en', 'ja', 'ko', 'zh'],
        'coverage_note': 'Model card reports XLM-R coverage of 100 languages, with lower-resource degradation possible; not trained as an emotion classifier.',
        'card': 'https://huggingface.co/intfloat/multilingual-e5-base', 'weight': 'model.safetensors'},
    'laion/clap-htsat-unfused': {
        'channel': 'background', 'role': 'M2', 'license': 'Apache-2.0', 'noncommercial': False,
        'languages': ['en'], 'coverage_note': 'English text prompts; music audio is not language-restricted.',
        'card': 'https://huggingface.co/laion/clap-htsat-unfused', 'weight': 'pytorch_model.bin'},
    'audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim': {
        'channel': 'voice', 'role': 'V1', 'license': 'CC-BY-NC-SA-4.0', 'noncommercial': True,
        'languages': ['en'], 'coverage_note': 'English speech; singing and cross-language use are unvalidated.',
        'card': 'https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim',
        'weight': 'model.safetensors'},
}


def main():
    meta = {m['id']: m for m in json.loads((HERE/'validation/candidate_metadata.json').read_text(encoding='utf-8'))}
    downloads = json.loads((HERE/'validation/downloaded_models.json').read_text(encoding='utf-8'))
    models = []
    for entry in downloads:
        detail = DETAILS[entry['id']]
        weight = detail['weight']
        local = next(f for f in entry['files'] if f['path'] == weight)
        remote = next(f for f in meta[entry['id']]['files'] if f['name'] == weight)
        if not remote['lfs_sha256'] or local['sha256'] != remote['lfs_sha256']:
            raise ValueError(f'Weight hash mismatch for {entry["id"]}')
        models.append({**entry, **detail, 'weight_sha256': local['sha256']})
    essentia = json.loads((HERE/'validation/downloaded_essentia.json').read_text(encoding='utf-8'))
    models.append({'id': 'Essentia emomusic-msd-musicnn', 'role': 'M1', 'channel': 'background',
                   'revision': 'emomusic-msd-musicnn-2 + msd-musicnn-1', 'license': 'CC-BY-NC-SA-4.0',
                   'noncommercial': True, 'languages': ['audio, language independent'],
                   'coverage_note': 'Music regressors; vocal language transfer and separation artifacts unvalidated.',
                   'card': 'https://essentia.upf.edu/models.html', 'files': essentia})
    models.append({'id': 'fixed prosody feature composite', 'role': 'V2', 'channel': 'voice',
                   'revision': 'defined in preregistration', 'license': 'not applicable',
                   'noncommercial': False, 'languages': ['audio, language independent'],
                   'coverage_note': 'Heuristic, uncalibrated composite; not a trained emotion model.', 'files': []})
    separation = HERE/'validation'/'demucs_model.json'
    if separation.exists():
        models.append(json.loads(separation.read_text(encoding='utf-8')))
    result = {'schema_version': 1, 'models': models,
              'license_note': 'Noncommercial flags apply to the named model weights; other dependencies and training data may have separate terms.'}
    (HERE/'models.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Wrote {len(models)} model entries; HF weight SHA-256 values verified against upstream LFS metadata.')


if __name__ == '__main__':
    main()
