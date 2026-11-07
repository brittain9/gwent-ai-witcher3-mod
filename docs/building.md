# Build from source

The standalone source build and clean source-only correctness checks passed
on Linux on 2026-11-03. A trained checkpoint remains a separate required
input: cloning this source preview alone does not produce a playable opponent.

## Required inputs

| Input | Requirement |
|---|---|
| Game | A local installation whose Gwent resources match the builder's pins |
| Python | Python 3.12 or newer and `uv`; Python dependencies are declared in `pyproject.toml` and locked in `uv.lock` |
| Node.js | Runs the ActionScript policy replay and runtime checks; development has used Node 26.13 |
| Java | A JDK with source-file launching, Java 11 or newer |
| JPEXS | The pinned FFDec distribution, including its `lib` directory; the builder currently pins 26.4.0 |
| Model | A compatible policy checkpoint supplied separately, with matching catalog identity and observation/action schemas |

Get FFDec from the [official JPEXS release](https://github.com/jindrapetrik/jpexs-decompiler/releases/tag/version26.4.0)
and unpack the full distribution under `.local/tools/ffdec/`, so
`.local/tools/ffdec/ffdec-cli.jar` and its companion libraries are present.
External tools and game files are not bundled in this source preview.

## Prepare local game inputs

Run from the repository root:

```sh
uv sync --frozen
uv run python scripts/extract_game.py --game-dir "/path/to/The Witcher 3"
uv run python scripts/decompile_game.py
uv run python scripts/build_catalog.py --game-dir "/path/to/The Witcher 3"
```

Extraction reads game resources and creates `.local/vanilla/`. Decompilation
creates `.local/as3/` with an attestation tying the exported classes to their
input screen and tool. Catalog generation writes `.runtime/catalog.json`.
These are local generated inputs, not source files to redistribute. None of
these commands installs the mod or changes the game.

The builder deliberately refuses changed inputs. Installing a different FFDec
version, changing the extracted screen or hand-editing generated manifests
does not make a compatible build. Re-review the relevant pins and behavior
before supporting a different game or tool version.

## Compile the mod

Use a trusted compatible checkpoint; checkpoint loading uses PyTorch's Python
serialization. The default model location is `.runtime/models/hard.pt`.
Alternatively, supply its path explicitly:

```sh
uv run python scripts/build_mod.py --checkpoint "/path/to/checkpoint.pt"
```

To use distinct checkpoints for the three tiers:

```sh
uv run python scripts/build_mod.py \
  --checkpoint "/path/to/hard.pt" \
  --tier-checkpoint easy="/path/to/easy.pt" \
  --tier-checkpoint normal="/path/to/normal.pt" \
  --tier-checkpoint hard="/path/to/hard.pt"
```

Without overrides, every tier uses the supplied checkpoint. The tier names do
not themselves train different models. Temperature defaults to zero; an explicit
`--easy-temperature` can enable sampling on Easy. The exporter validates the
checkpoint's catalog hash, vocabulary, schemas and model dimensions.

### Checkpoint format

A checkpoint is a PyTorch dictionary with these required sections:

| Section | Required content |
|---|---|
| `identity` | `catalog_sha256`, the sorted `card_ids` vocabulary, and a nonnegative integer `updates` |
| `model_config` | `observation_schema_id`, `action_schema_id`, `observation_size`, `card_count`, `width` and `blocks` |
| `policy_state` | The policy/value network's state dictionary, matching the declared architecture |

The schema IDs are `witcher3-gwent-seat-view-v1` and
`witcher3-gwent-factorized-action-v1`. The catalog identity comes from
`learning.catalog_identity.catalog_identity(catalog)`: a SHA-256 of canonical
JSON containing `schema_version`, `cards`, `decks` and `runtime_mappings`.
It excludes extraction provenance such as absolute filesystem paths. It is
not the hash of the complete catalog file. Changing card or deck content still
changes compatibility; moving an otherwise identical installation does not.

### What the build verifies

The standalone build does not require or include the development simulator.
For each distinct checkpoint, its fixture generator creates 200 synthetic
positions covering the observation and action schemas. Python and ActionScript
must agree on encoding, logits, value outputs and chosen actions. These are
controlled schema tests, not game records or proof of rules or gameplay parity.

The build also compiles the patched screen with FFDec and re-exports it for
verification. It repeats policy checks against the compiled export and checks
timer bindings and class initialization. A successful manifest is required for
packaging. These checks do not execute the actual game or establish playing
strength.

Default output is `.runtime/mod-build/`, including the patched screen,
`manifest.json` and `build.log`. Use `--output-dir` for another destination.
The [installer](install.md) accepts that build directory, assembles the bundle
and checks the game before installation. Keep generated packages local: they
contain a modified game resource, outside the source license.

## Verification status

The standalone build has passed compilation/re-export, initialization and
per-checkpoint synthetic replay checks with three local models. A separate
source-only export passed its correctness suite without game assets, generated
records, models or simulator source. Install/uninstall also passed in an
isolated vanilla-shaped fixture using pinned local resources and a stub
executable; this does not constitute a new live-game launch.

Live integration of the preceding build has been tested on Steam/Proton.
The prepared preview still needs its own native playtest, and installation on
another machine and Windows remains unverified. Source checks are configured
for CI but have not yet run on GitHub.
