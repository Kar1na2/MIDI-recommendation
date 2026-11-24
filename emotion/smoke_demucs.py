"""Load the explicit Demucs checkpoint without processing song audio."""
import json

from pinned_demucs import get_pinned_model


def main():
    model = get_pinned_model('htdemucs')
    expected = {'vocals', 'drums', 'bass', 'other'}
    if set(model.sources) != expected:
        raise RuntimeError('Unexpected HTDemucs source names')
    print(json.dumps({'model': 'htdemucs', 'status': 'loaded', 'input': 'none'}))


if __name__ == '__main__':
    main()
