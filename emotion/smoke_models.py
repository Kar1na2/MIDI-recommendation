"""Run isolated smoke tests on fabricated signals, never on song inputs."""
import argparse
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'model_cache'


def main(kind):
    if kind == 'essentia':
        from essentia.standard import TensorflowPredictMusiCNN, TensorflowPredict2D
        root = CACHE / 'essentia'
        emb = TensorflowPredictMusiCNN(graphFilename=str(root/'msd-musicnn-1.pb'), output='model/dense/BiasAdd')
        head = TensorflowPredict2D(graphFilename=str(root/'emomusic-msd-musicnn-2.pb'), output='model/Identity')
        result = head(emb(np.zeros(4 * 16000, dtype=np.float32)))
    else:
        import torch
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(2)
        if kind in ('T1', 'T2'):
            path = CACHE / ('clapAI--roberta-base-multilingual-sentiment' if kind == 'T1' else 'intfloat--multilingual-e5-base')
            tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
            model_cls = AutoModelForSequenceClassification if kind == 'T1' else AutoModel
            model = model_cls.from_pretrained(path, local_files_only=True).eval()
            tokens = tokenizer('query: a synthetic test sentence', return_tensors='pt')
            with torch.inference_mode():
                out = model(**tokens)
            result = (out.logits if kind == 'T1' else out.last_hidden_state).numpy()
        elif kind == 'M2':
            from transformers import ClapModel, ClapProcessor
            path = CACHE / 'laion--clap-htsat-unfused'
            processor = ClapProcessor.from_pretrained(path, local_files_only=True)
            model = ClapModel.from_pretrained(path, local_files_only=True).eval()
            data = processor(text=['synthetic test music'], audios=np.zeros(4*48000, dtype=np.float32),
                             sampling_rate=48000, return_tensors='pt', padding=True)
            with torch.inference_mode():
                result = model(**data).logits_per_audio.numpy()
        elif kind == 'V1':
            from transformers import Wav2Vec2FeatureExtractor
            from transformers.models.wav2vec2.modeling_wav2vec2 import Wav2Vec2Model, Wav2Vec2PreTrainedModel

            class Head(torch.nn.Module):
                def __init__(self, config):
                    super().__init__()
                    self.dense = torch.nn.Linear(config.hidden_size, config.hidden_size)
                    self.dropout = torch.nn.Dropout(config.final_dropout)
                    self.out_proj = torch.nn.Linear(config.hidden_size, config.num_labels)

                def forward(self, x):
                    return self.out_proj(self.dropout(torch.tanh(self.dense(self.dropout(x)))))

            class EmotionModel(Wav2Vec2PreTrainedModel):
                def __init__(self, config):
                    super().__init__(config)
                    self.wav2vec2 = Wav2Vec2Model(config)
                    self.classifier = Head(config)
                    self.post_init()

                def forward(self, input_values):
                    return self.classifier(self.wav2vec2(input_values)[0].mean(dim=1))

            path = CACHE / 'audeering--wav2vec2-large-robust-12-ft-emotion-msp-dim'
            extractor = Wav2Vec2FeatureExtractor.from_pretrained(path, local_files_only=True)
            model, info = EmotionModel.from_pretrained(path, local_files_only=True, output_loading_info=True)
            if info['missing_keys'] or info['unexpected_keys'] or info['mismatched_keys']:
                raise RuntimeError(f'Incomplete V1 model load: {info}')
            model.eval()
            data = extractor(np.zeros(16000, dtype=np.float32), sampling_rate=16000, return_tensors='pt')
            with torch.inference_mode():
                result = model(data.input_values).numpy()
        else:
            raise ValueError(kind)
    if not np.isfinite(result).all() or not result.size:
        raise RuntimeError(f'{kind} gave nonfinite or empty output')
    print(json.dumps({'model': kind, 'status': 'ok', 'shape': list(result.shape), 'input': 'synthetic'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('model', choices=['T1', 'T2', 'M2', 'V1', 'essentia'])
    main(parser.parse_args().model)
