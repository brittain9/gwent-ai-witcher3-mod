# Build from source

For normal installation, use the prebuilt player download.
This page describes development builds from the source checkout.

For headless LLM or session-agent games against configured opponents, see the
[LLM player connector](llm-connector.md). It uses a separately built rules engine
and private inputs; it does not change the installed mod.

## Inputs

| Input | Requirement |
|---|---|
| Game | A local installation whose Gwent resources match the version pins |
| Python | Python 3.12+ |
| Java | JDK 11+ with source-file launching |
| Node.js | Version 24+ for inference/runtime checks |
| JPEXS | Complete pinned FFDec 26.4.0 distribution, including companion libraries |
| Policy | The inference-only `policies/` folder from the player download, or a compatible trusted Hard checkpoint |

Install the locked build environment with `uv sync --frozen`. PyTorch and NumPy
are used for checkpoint export and reference checks. [Tool downloads](tool-downloads.md) lists official distributions and
integrity checks. Put the complete FFDec distribution under
`.local/tools/ffdec/`, including `ffdec-cli.jar` and its libraries.

## Prepare local game inputs

From the source root:

```sh
python3 scripts/extract_game.py --game-dir "/path/to/The Witcher 3"
python3 scripts/decompile_game.py
python3 scripts/build_catalog.py --game-dir "/path/to/The Witcher 3"
```

Extraction creates `.local/vanilla/`; decompilation creates `.local/as3/`
with an attestation tying exported classes to the game input and decompiler.
Catalog generation writes `.runtime/catalog.json`. These commands read the
installed game and prepare local files without installing the mod.

The builder verifies the game resources, extraction/decompilation attestations
and tool pins. A changed game screen or incompatible card catalog requires a
reviewed build; modifying a manifest does not make it compatible.

## Build with inference weights

Use a verified inference bundle whose observation profile and numeric probes
match the shared model:

```sh
uv run python scripts/build_mod.py --policy-bundle "/path/to/player-download/policies"
```

The build uses one shared set of weights for all three difficulties. Easy and Normal
vary move selection; Hard is deterministic. Lookahead is off. The builder selects the Hard entry from a multi-tier bundle. Earlier bundles
that declare the old input profile must be re-exported with matching numeric
probes; they are rejected rather than silently reinterpreted. The checkpoint
build below selects the audited profile for the original shared model.

The bundle contains learned float32 tensors and restricted architecture/hash
metadata. The builder checks the local catalog identity, reconstructs the
sorted vocabulary and effect tables locally, and generates the ActionScript
headers. It verifies fictional reference positions before compilation and
again against the compiled/re-exported policy.

Additional checks decode the compiled weight chunks and compare the exact
float32 bytes, vocabulary, effects, tensor offsets and dimensions. Timer
bindings, direct class initialization and runtime source checks must pass.
These checks establish identified encoding/integration behavior, rather than
playing strength or full game-rules parity.

Default output is `.runtime/mod-build/`, including the modified screen,
`manifest.json` and `build.log`. Use `--output-dir` for another destination.
Prepare the final local package and check the game:

```sh
python3 scripts/install_mod.py --check --package .runtime/mod-build --game-dir "/path/to/The Witcher 3"
```

Follow [installation](install.md) for the install and removal commands.
Generated packages contain a modified game resource; the original-code
license does not grant rights to redistribute that game material.

## Checkpoint export and development tests

Install the locked development environment with `uv sync --frozen`. A trusted
checkpoint can also be built directly:

```sh
uv run python scripts/build_mod.py \
  --checkpoint "/path/to/hard.pt"
```

The controller uses temperatures 1 for Easy, 0.25 for Normal and 0 for Hard.
These settings change selection from one model; they do not train new models.

Checkpoint loading uses PyTorch Python serialization. Load only trusted files.
The checkpoint dictionary contains `identity` (catalog hash, sorted vocabulary,
update count), `model_config` (schema IDs and architecture) and `policy_state`.
The schema IDs are `witcher3-gwent-seat-view-v1` and
`witcher3-gwent-factorized-action-v1`.

Catalog identity hashes canonical JSON for `schema_version`, `cards`, `decks`
and `runtime_mappings`. It excludes extraction paths, while card/deck changes
still change compatibility. Export validates dimensions and finite tensors.

Run the development checks with:

```sh
uv run python -m unittest discover -s tests
node mod/ai/node/mod.test.js
```

Source-only checks explicitly skip tests whose local game/model inputs are
absent. A skipped native-input test is not a game acceptance pass. Real startup,
menus, selection transitions and installation behavior need their own checks
on the identified candidate and platform.
