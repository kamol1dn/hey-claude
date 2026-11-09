# The "hey claude" wake word

There are three ways to get a "hey claude" model. Most people need only the
first.

| | what you do | time | where the model lives |
|---|---|---|---|
| **1. The shared default** | nothing | a 0.4 MB download on first start | `models\hey_claude.onnx` |
| **2. Personal tune-up** (optional) | Settings → Wake word → *Tune it to my voice…* | ~5 min of recording, ~10–15 min of the PC working | `state\wake_models\` |
| **3. Colab** (advanced) | record clips, run a notebook | 1–2.5 h, mostly waiting | wherever you put it |

If no model loads (no network on first start, a broken file), hey-claude falls
back to openWakeWord's stock **"hey jarvis"**: the island says "hey jarvis" and
the log says why.

## 1. The shared default model

hey-claude ships one "hey claude" model for everyone, trained once on
**synthetic voices only**: 54 Kokoro voices (American, British, and voices
built for Japanese, Chinese, Hindi, French, Italian, Spanish and Portuguese,
reading English, which gives accented English), random blends of two voices,
and, since v2, Piper's multi-speaker LibriTTS-R voice for half of every set:
904 speakers learned from LibriVox audiobook readers, ordinary people on
ordinary microphones, which sound far more like real users than Kokoro's
studio voices. Speeds 0.75–1.35, pitch ±3 semitones, and several pronunciations:
"hey claude", "klod", "clod", "klawd", "klode", and Russian/Uzbek-style ones
(a short pure "o", a final "d" said as "t"). No real person's recordings
went into it, so it doesn't lean towards anyone's accent, and the repository
stays free of personal voice data.

On first start, if `wake.model` is the default `models/hey_claude.onnx` and the
file isn't there, hey-claude downloads it from the GitHub release
`wake-model-v2` and keeps it only if its SHA-256 matches the one in
`heyclaude/wake_models.py`. A file you put there yourself (an older Colab
model) is never overwritten. What it measured: README, "Wake word".

Licence: CC BY-NC-SA 4.0, because the negatives come from openWakeWord's
feature dataset; see [models/LICENSE](../models/LICENSE).

### How it was trained (maintainers)

```bash
.venv\Scripts\python -m heyclaude.wake_train default --out state\m2a\hey_claude.onnx
.venv\Scripts\python tools\stage_wake_release.py state\m2a\hey_claude.onnx
```

The first command runs the whole local pipeline on this PC (native Windows,
no WSL): it downloads openWakeWord's negative features (resumable ranged
downloads, sizes shown) and Piper's LibriTTS-R voice (75 MB, pinned revision,
SHA-256 checked; needs `pip install "piper-tts>=1.8"`), synthesises ~19,000
clips with Kokoro and Piper (half each; 8 Kokoro voices and 45 of the 904
Piper speakers are held out of every training set), mixes in
noise and synthetic room reverb, computes openWakeWord embeddings, trains a
small classifier head (numpy), picks the threshold from the held-out voices and
the 10.7 h validation set, calibrates it to 0.5, and exports ONNX in
openWakeWord's format. Synthesis and the feature models use the GPU when it's
under 70% busy (hey-claude's own load excluded), otherwise the CPU. The second
command copies the model to `dist\<tag>\` (`--tag wake-model-v2`), writes its
SHA-256 into the code and prints the `gh release create` command; it publishes
nothing. To compare two models on the same held-out voices and validation set:

```bash
.venv\Scripts\python tools\wake_compare.py dist\wake-model-v1\hey_claude.onnx state\m2c\hey_claude.onnx
```

## 2. Personal tune-up in Settings

**Settings → Wake word → Tune it to my voice…** opens a wizard. Use it if the
shared model often misses *you*, or if you want a different phrase.

1. **Phrase.** "hey claude" by default; any 2–3 word phrase works. It warns
   about phrases that are too short or too common ("hello", "okay").
2. **Record.** About 25 clips (16 at least), each with a cue: normal, quiet,
   loud, fast, slow, far away... A live level meter shows the mic, every clip
   is checked (too quiet, clipped, too short), and **Redo** re-records the last
   one. Listening is paused while it records. Every 4th clip is held out of
   training to measure the result honestly.
3. **Room sound (optional, 1 minute).** Talk normally or leave the TV/fan on:
   these become "not the wake word" examples from your own room.
4. **Train.** One button, a progress bar and a time estimate that corrects
   itself as it goes. The first time it downloads openWakeWord's negative
   features (it shows the size; interrupted downloads resume). It also learns
   from every false wake you marked with **not me** (below).
5. **Test and apply.** It shows the hit rate on your held-out clips and the
   false wakes per hour on 10.7 h of other people's audio. *Test* runs the new
   model on the mic so you can see it fire. *Use this model* switches to it
   immediately, no restart; *Undo* goes back to the previous model (the
   last 20 changes are kept in `state\wake_models\history.json`).

Your clips stay in `state\wake_samples\`, the room recording in
`state\wake_room\`, the models in `state\wake_models\`. Nothing leaves the PC.

**"That wasn't me."** When it wakes by mistake, click the **not me** chip on
the island while it's listening (or right-click the island → *That wasn't me*,
for 2 minutes after a wake). The ~2.5 s that triggered it are saved to
`state\wake_negatives\`, the request is dropped, and the next tune-up trains
on them as negatives.

From a terminal, the same thing is
`.venv\Scripts\python -m heyclaude.wake_train personal` (it uses the clips in
`state\wake_samples\`; `--room`, `--false-wakes`, `--phrase` as in the wizard).

## 3. Advanced: your own model in Colab

openWakeWord's full recipe (Piper voices, the whole 2,000 h negative set, a
GPU for an hour) only runs on Linux, so on this PC it means Colab. It can give
the best quality for one voice. The steps below record your clips, train in
Colab, and drop the model into `models\`.

### 3.1 Record your clips

Close or pause hey-claude first (tray → Listening off), or use the tray item,
which pauses it for you:

- **Tray → Record wake samples…** opens a console window with the recorder, or
- in a terminal:

  ```bash
  .venv\Scripts\python -m heyclaude --record-wake-samples
  ```

It asks for 40 clips, one at a time: press **Enter**, say **"hey claude"**
the way the screen says (quiet, loud, fast, slow, from across the room, tired, as
the start of a sentence...), and it saves the clip trimmed to your voice. `r` +
Enter re-records the last one, `q` + Enter stops early (20 is the minimum, 30–40
is better). `--auto` records without Enter presses, with a short gap between
clips, which is handy for the far-away ones. A different count:
`--record-wake-samples 30`.

Say it the way you'll really say it. Don't put on an American accent for the
recording if you won't use it every day.

The clips go to `state\wake_samples\` (git-ignored, never committed):

- `hey_claude_000_normal.wav`, `hey_claude_001_quiet.wav`, ... (16 kHz mono)
- every 4th clip (003, 007, 011...) is **held out**: it isn't used for
  training, so the hit rate on those clips is an honest one.
- `hey_claude_voice.zip`: the training clips, ready to upload to Colab.

Running it again adds clips after the existing ones. To redo one, delete the
wav and run `.venv\Scripts\python -m heyclaude.wake_samples --pack` to rebuild
the zip.

### 3.2 Train in Colab

The notebook linked from openWakeWord's README, and the official
`automatic_model_training.ipynb`, **no longer run on current Colab** (Python
3.12, dead download links; openWakeWord issue #296). Use the patched copy from
openWakeWord PR #359, which runs the trainer in its own Python 3.11
environment. Its author reports a full run on an L4 in about 30 minutes; a
free T4 is roughly 2–3x slower.

1. Open
   **https://colab.research.google.com/github/Oreozisbored/openWakeWord/blob/modernize-training-notebook/notebooks/automatic_model_training.ipynb**
   and sign in to Google.
2. **Runtime → Change runtime type → T4 GPU** (or L4 with Colab Pro) → Save.
3. Run **Steps 1–4** as they are (setup, hardware, model downloads, data
   downloads: ~20 min, mostly the 16 GB feature file).
4. **Step 5, Training Configuration**, set the form fields:

   | field | value |
   |---|---|
   | `target_wake_word` | `hey claude` |
   | `number_of_examples` | `15000` |
   | `number_of_training_steps` | `20000` |
   | `false_activation_penalty` | `1500` |

   Run it. (The model file will be called `hey_claude.onnx`.)
5. **Add a cell after Step 5** (hover under the cell → **+ Code**), paste this,
   and run it. It adds the extra spellings and the look-alike phrases, then asks
   for a file: pick `state\wake_samples\hey_claude_voice.zip` from step 1.

   ```python
   # hey-claude: several spellings, look-alike negatives, and your own voice.
   # Run after Step 5 and before Step 7.
   import glob, os, shutil, zipfile, yaml
   from google.colab import files

   SPELLINGS = ["hey claude", "hey klod", "hey clod", "hey klawd", "hey klode"]
   NEGATIVES = ["hey cloud", "hey clyde", "hey claudia", "hey clara", "hey lord",
                "hey call", "they claw", "hey jarvis", "okay google"]
   COPIES = 20            # each of your clips is used this many times (each copy is augmented differently)

   yaml_path = globals().get("yaml_file") or f"/content/{model_clean_name}.yaml"
   cfg = yaml.safe_load(open(yaml_path))
   cfg["target_phrase"] = SPELLINGS
   cfg["custom_negative_phrases"] = NEGATIVES
   yaml.safe_dump(cfg, open(yaml_path, "w"))

   up = files.upload()                       # choose hey_claude_voice.zip
   os.makedirs("/content/my_voice", exist_ok=True)
   for name in up:
       zipfile.ZipFile(name).extractall("/content/my_voice")
   mine = sorted(glob.glob("/content/my_voice/*.wav"))
   pos = os.path.join(cfg["output_dir"], cfg["model_name"], "positive_train")
   os.makedirs(pos, exist_ok=True)
   for k in range(COPIES):
       for p in mine:
           shutil.copy(p, os.path.join(pos, f"me{k:02d}_{os.path.basename(p)}"))
   print(f"model {cfg['model_name']}: phrases {cfg['target_phrase']}")
   print(f"{len(mine)} of your clips x {COPIES} = {len(mine) * COPIES} files in {pos}")
   ```

   It should print `21+ of your clips x 20 = ...`. Without the zip (Cancel
   the upload) it still trains on the spellings alone.
6. **Step 6, Test Pronunciation** (optional, recommended): type each spelling
   into `test_phrase` and listen. Keep the ones that sound like you saying
   it. If one sounds wrong (e.g. "klode" comes out as "clothed"), delete it
   from `SPELLINGS`, rerun the cell from step 5 (choose the zip again), and go
   on.
7. Run **Step 7, Train the Model**. This is the long one (~40 min on an L4,
   1–2 h on a T4). Keep the tab open; Colab disconnects idle background tabs.
8. Run **Steps 8–9**. Step 9 downloads `hey_claude.onnx` (and a `.tflite`
   you don't need).

**About the spellings.** Piper reads text with English rules, so the spelling
decides the sound. Several spellings in one model cover the ways the vowel and
the "d" come out:

| spelling | sounds like | why |
|---|---|---|
| `hey claude` | American "klawd" | the standard one |
| `hey klod` | short, rounded "o", Russian "Клод" | how a Russian or Uzbek speaker often says it |
| `hey clod` | the same, spelled the English way | Piper sometimes reads `klod` oddly; this backs it up |
| `hey klawd` | long open "aw" | British / careful pronunciation |
| `hey klode` | "klohd", a long "o" | a common non-native variant |

A final "d" that comes out as a "t" ("klot") is covered by your own clips
rather than a spelling, since "hey clot" is too far from what others will say.

**About the negatives.** `NEGATIVES` are phrases synthesised as "not the wake
word" so the model learns the difference. "hey cloud" is there because
"cloud" comes up a lot in tech lectures. If you find that it misses you when
you say it quickly, try training again without "hey cloud".

**Files you get:** `hey_claude.onnx` (around 1 MB). It's partly trained on your
voice. `models\*.onnx` is git-ignored, since this repo is public. Commit it
yourself only if you're fine with that.

### 3.3 Use it

Put the file at `D:\coding\hey-claude\models\hey_claude.onnx` and restart
hey-claude (tray → Quit, then `run.cmd`). That path
is the default `wake.model`, so nothing else needs changing. The log then says

```
[wake] hey claude (D:\coding\hey-claude\models\hey_claude.onnx), threshold 0.5
```

and the idle island shows **hey claude**. If the file is missing or broken, it
falls back to hey_jarvis at 0.5 and says so in `state\logs\app.log`.

Another file name or place: set it in Settings… → Wake word → Model
(Browse…), or in `config.yaml`:

```yaml
wake:
  model: models/hey_claude.onnx   # relative = inside the hey-claude folder; absolute paths work too
  threshold: 0.5                  # step 4 picks this
```

## Measuring a model and picking the threshold

You need a few hours of audio where nobody says "hey claude": lectures,
podcasts, YouTube, calls. Any audio or video files work (mp4, mkv, m4a, mp3,
wav...), or a folder of them; ffmpeg decodes them. 3 hours is the minimum for the
"< 1 false wake per 3 hours" target; 6–10 hours gives a steadier number.

```bash
.venv\Scripts\python tools\wake_eval.py --negatives "D:\path\to\lectures"
```

For the shared default model, measure it on your own voice and lectures the
same way (record clips with `--record-wake-samples` first; they're only
scored). It scores your clips and the long audio (CPU only, several
processes; roughly 1–2 minutes per hour of audio). Then it prints one row per
threshold:

```
threshold   hit (all)   hit (held out)   false wakes   per hour
  0.45        100.0%      100.0%              1        0.15
  0.50        100.0%      100.0%              0        0.00
  0.55         97.5%      100.0%              0        0.00 <- recommended
  ...
Recommended threshold: 0.55  (both targets met)
```

- **hit**: share of your clips it wakes on (all clips, and only the held-out
  ones it never trained on). Target: ≥ 95%.
- **false wakes / per hour**: wakes in audio without the phrase. Target:
  under 0.33 per hour (fewer than 1 per 3 hours). It lists where they happened
  (`lecture3.mp4 @ 1:02:13`) so you can listen to what fooled it.
- The recommendation is the middle of the thresholds that meet the false-wake
  target with the best hit rate, which leaves margin both ways.

When both targets are met, write the threshold to `config.yaml`:

```bash
.venv\Scripts\python tools\wake_eval.py --negatives "D:\path\to\lectures" --apply
```

(or type it into Settings… → Wake word → Threshold, which applies without a
restart). Every run also leaves a JSON report in `state\wake_eval\`.

Other options: `--model other.onnx` to test another model, `--max-hours 5`,
`--jobs 2` to leave more CPU free, `--positives some\folder`.

### When it misses the targets

| what you see | do this |
|---|---|
| hit rate < 95%, false wakes fine | record 20 more clips (more of the way you missed: quiet, fast, far), retrain, or lower the threshold |
| hit rate fine, too many false wakes | listen to the listed spots. Retrain with `false_activation_penalty` 2500 and add the fooling words to `NEGATIVES` |
| both bad | retrain with `number_of_examples` 25000 and `number_of_training_steps` 30000 |
| held-out hit rate much lower than "all" | the model memorised your training clips: record more variety |

## While it's talking

hey-claude ignores the wake word while it's speaking, and for 0.7 s after
(`wake.suppress_while_speaking`, `wake.speaking_tail_s`), so its own voice
("I'm Claude...") or the echo can't wake it. Clicking the island or the hotkey
still cuts it off. Ignored triggers are logged as
`[wake] ignored a trigger while speaking`. To interrupt by voice instead (the
old behaviour), set `suppress_while_speaking: false`; M4 plans echo
cancellation for a proper spoken barge-in.

## Local training

Local training is built in now (sections 1 and 2): native Windows, no WSL, no
Colab. It's lighter than openWakeWord's full recipe (a sample of the negative
features instead of all 16 GB, and Kokoro plus one multi-speaker Piper voice
instead of many Piper voices), so the Colab route
stays here as the "most data" option.
