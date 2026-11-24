"""audEERING's documented dimensional Wav2Vec2 regression architecture."""
import torch
from transformers.models.wav2vec2.modeling_wav2vec2 import Wav2Vec2Model, Wav2Vec2PreTrainedModel


class RegressionHead(torch.nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = torch.nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = torch.nn.Dropout(config.final_dropout)
        self.out_proj = torch.nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features):
        return self.out_proj(self.dropout(torch.tanh(self.dense(self.dropout(features)))))


class EmotionModel(Wav2Vec2PreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.wav2vec2 = Wav2Vec2Model(config)
        self.classifier = RegressionHead(config)
        self.post_init()

    def forward(self, input_values):
        return self.classifier(self.wav2vec2(input_values)[0].mean(dim=1))
