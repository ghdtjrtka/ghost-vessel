# Presets

A **preset** = one avatar as a self-contained bundle of **data**: avatar clips +
font + persona (role) + theme + voice + emotion map. The engine (this repo) is the
free/open part; presets are the content you can keep private or sell (Gumroad).

```
presets/
  active.txt            # id of the active preset (one line)
  mina-default/         # the built-in private default (gitignored assets)
  _template/            # starter — copy this to make/sell your own
  <your-preset>/        # dropped-in packs
```

## How it works
- The engine reads `presets/active.txt`, loads `presets/<id>/preset.json`, and serves
  the resolved config to the frontend (`GET /preset`) and the list (`GET /presets`).
- Switching presets swaps **font · theme · voice · avatar clips · emotion map**, and
  (at install) injects the preset's `role.md` into the agent's system prompt.
- Presets run **no code** — installing a bought pack can't execute anything.

## Business model
- **Engine → GitHub** (open source). **Presets → Gumroad** (paid packs). Same shape
  as RPG Maker + asset packs or VTube Studio + models.
- Gotchas before you sell: (1) bundle only redistributable/commercial fonts (OFL);
  (2) own the rights to your avatar's likeness + check your image generator's resale
  terms; (3) keep it SFW (GitHub + Gumroad policies).

See `_template/README.md` to build one.
