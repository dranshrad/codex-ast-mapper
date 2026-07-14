# Demo: map a repo into a token budget

```bash
poetry run codex-ast-mapper --dir . --lang python --max-tokens 800
# optional: squeeze further to watch prune drop modules
poetry run codex-ast-mapper --dir . --lang python --max-tokens 400
```

Play the recording:

```bash
asciinema play docs/demo/map-repo.cast
```

Regenerate the README GIF (requires [`agg`](https://github.com/asciinema/agg)):

```bash
agg --speed 1.5 --font-size 14 --theme monokai docs/demo/map-repo.cast docs/demo/map-repo.gif
```

Expected: hyper-dense XML plus a Tiktoken budget table (e.g. `796 / 800`, within budget), then a tighter budget where modules in the map drop under prune pressure.
