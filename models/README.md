# models/

Put the trained wake-word model here as `hey_claude.onnx`. That's the default
`wake.model` in `config.example.yaml`, so hey-claude picks it up on its next
start. Without it, hey-claude uses openWakeWord's stock "hey jarvis".

`*.onnx` and `*.tflite` here are git-ignored: the model is partly trained on
recordings of your voice, and this repository is public. Training and
measuring it: [docs/train-hey-claude.md](../docs/train-hey-claude.md).
