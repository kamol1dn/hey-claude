## What this changes

<!-- A sentence or two. -->

## Checks

- [ ] `python -m pytest` (or each `tests\test_*.py`) passes locally
- [ ] No personal data (names, paths, voice samples, tokens) in the diff

### Adding or changing a character pack (`characters/<id>/`)

- [ ] `tools\character_lab.py --check --pack <id>` is clean
- [ ] `preview.png` regenerated (`--previews --pack <id>`), and a contact sheet attached (`--shots`)
- [ ] **Original work:** not a copy, trace or look-alike of an existing mascot, character or logo
      (Clawd, Mochi or anyone else's), and no brand marks. A maintainer reviews this by eye.
- [ ] Licence in `character.json` is MIT (or compatible) and the author is credited
