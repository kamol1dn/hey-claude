# models/

`hey_claude.onnx` here is the shared "hey claude" wake-word model. hey-claude
downloads it on first start from the GitHub release `wake-model-v1` and keeps
it only if its SHA-256 matches `heyclaude/wake_models.py`. It's the default
`wake.model`; a file you put here yourself (an older Colab model) is never
overwritten.

`*.onnx` and `*.tflite` are git-ignored: models trained with your own voice
(Settings → Wake word, or Colab) must not end up in this public repository.
Personal models from the Settings wizard live in `state/wake_models/` instead.

Licence of the model: [LICENSE](LICENSE) (CC BY-NC-SA 4.0). How it's made and
measured: [docs/train-hey-claude.md](../docs/train-hey-claude.md) and README
"Wake word".
