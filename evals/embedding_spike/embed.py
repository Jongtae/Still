"""Pinned, local-only multilingual-e5-small inference for the #849 experiment.

Adopt the model card's prefix/pooling/normalisation recipe using official ORT
and tokenizers APIs. No downloads, transformer stack, or runtime integration.
Heavy imports happen only after both caller-supplied SHA256 pins match.
"""
import hashlib
import os
from pathlib import Path


MODEL_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
MODEL_PINS = {
    'onnx/model.onnx': 'ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665',
    'onnx/model_qint8_avx512_vnni.onnx': 'dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88',
    'onnx/model_O4.onnx': '4654c156f3e4171abc9c716cdb771bf9116455d15ac1aab364aeeede0e3205b0',
}
TOKENIZER_PIN = '0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39'


def _verify(path, expected):
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError(f'SHA256 mismatch for {path}: expected {expected}, got {actual}')


class Embedder:
    """Batch CPU embeddings; callers explicitly supply trusted file digests."""

    def __init__(self, model_dir, *, model_sha256, tokenizer_sha256,
                 model_file='onnx/model.onnx', batch_size=16, threads=2):
        if batch_size < 1 or threads < 1:
            raise ValueError('batch_size and threads must be positive')
        directory = Path(model_dir).resolve(strict=True)
        model = (directory / model_file).resolve(strict=True)
        tokenizer = (directory / 'tokenizer.json').resolve(strict=True)
        if not model.is_relative_to(directory) or not tokenizer.is_relative_to(directory):
            raise ValueError('Model and tokenizer must be inside model_dir')
        _verify(model, model_sha256)
        _verify(tokenizer, tokenizer_sha256)
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.session.disable_fallback()
        if self.session.get_providers() != ['CPUExecutionProvider']:
            raise RuntimeError('Expected CPUExecutionProvider only')
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=self.tokenizer.token_to_id('<pad>'), pad_token='<pad>')
        self.np, self.batch_size = np, batch_size

    def _encode(self, texts, prefix):
        texts = list(texts)
        np = self.np
        batches = []
        for start in range(0, len(texts), self.batch_size):
            encoded = self.tokenizer.encode_batch([prefix + text for text in texts[start:start + self.batch_size]])
            arrays = {
                'input_ids': np.asarray([item.ids for item in encoded], dtype=np.int64),
                'attention_mask': np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
                'token_type_ids': np.asarray([item.type_ids for item in encoded], dtype=np.int64),
            }
            feeds = {item.name: arrays[item.name] for item in self.session.get_inputs()}
            hidden = self.session.run(['last_hidden_state'], feeds)[0].astype(np.float32)
            mask = arrays['attention_mask'][..., None].astype(np.float32)
            pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)
            pooled /= np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
            batches.append(pooled)
        return np.concatenate(batches) if batches else np.empty((0, 384), dtype=np.float32)

    def embed_queries(self, texts):
        return self._encode(texts, 'query: ')

    def embed_passages(self, texts):
        return self._encode(texts, 'passage: ')
