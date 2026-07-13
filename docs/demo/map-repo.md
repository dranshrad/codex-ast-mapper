# Demo: map a repo into a token budget

```bash
poetry run codex-ast-mapper --dir . --lang python --max-tokens 800
```

Play the recording:

```bash
asciinema play docs/demo/map-repo.cast
```

Expected: hyper-dense XML plus a Tiktoken budget table (e.g. `796 / 800`, within budget).
