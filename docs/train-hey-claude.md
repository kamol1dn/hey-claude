# Training a "hey claude" wake word

v1 uses openWakeWord's stock `hey_jarvis` model. A custom model is a small
ONNX file trained on synthetic speech, no recordings of you required.

## Route 1: the openWakeWord Colab notebook (easiest, ~1 hour, free GPU)

1. Open the automatic training notebook from the openWakeWord repo:
   `notebooks/automatic_model_training.ipynb`
   (https://github.com/dscripka/openWakeWord). The README links a ready
   Colab copy.
2. Set `target_word = "hey claude"`. Spell it the way it sounds if it
   misfires (e.g. `"hey klawd"`); the notebook generates thousands of clips
   with Piper TTS voices, so phonetic spelling matters more than the real one.
3. Keep the defaults first time (`number_of_examples ≈ 30000`,
   `number_of_training_steps ≈ 10000`). It downloads negative data (speech,
   music, noise) and room impulse responses for augmentation.
4. Download the resulting `hey_claude.onnx`.

Run this in Colab rather than locally: the user's GPU is kept free for
Premiere, and a local CPU run takes many hours.

## Route 2: local, CPU-only (slow but private)

Clone openWakeWord and follow `docs/custom_models.md`: generate positives with
`piper-sample-generator`, negatives from the provided feature files, then
`python -m openwakeword.train --training_config my_model.yaml --train_model`.
Expect an overnight run on CPU.

## Using it

```yaml
# config.yaml
wake:
  model: D:\coding\hey-claude\models\hey_claude.onnx
  threshold: 0.5
```

Then tune `threshold`. Leave the app running for an evening with
`python -m heyclaude` and watch the console. Raise the threshold if it wakes on
its own (TV, calls). Lower it if you have to repeat yourself. Adding 10–20 real
recordings of your own voice as extra positives (the notebook supports a folder
of wavs) helps a lot with an accent the TTS voices don't cover.

The idle pill shows the wake model's file name ("hey claude" for
`hey_claude.onnx`), so it updates by itself.
