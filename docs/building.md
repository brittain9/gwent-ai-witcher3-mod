# Build from source

For normal installation, use the prebuilt player package from Steam Workshop or
GitHub Releases. This page describes development builds from the source checkout.

## Inputs

| Input | Requirement |
|---|---|
| Game | A local *The Witcher 3: Wild Hunt* Next-Gen installation with matching `gwint_game.redswf` |
| Python | Python 3.12+ (with PyTorch & NumPy for checkpoint export) |
| Java | JRE/JDK 17+ (for JPEXS FFDec) |
| Node.js | Version 20+ (for compiled ActionScript 3 parity checks) |
| JPEXS | Complete pinned FFDec 26.4.0 distribution (`ffdec-cli.jar` + libraries) under `.local/tools/ffdec/` |
| Weights | A trained card-entity Set-Transformer checkpoint (`best.pt`) |

[Tool downloads](tool-downloads.md) lists official tool distributions and
integrity checks.

## Prepare local game inputs

From the source root:

```sh
python3 scripts/extract_game.py --game-dir "/path/to/The Witcher 3"
python3 scripts/decompile_game.py
python3 scripts/build_catalog.py --game-dir "/path/to/The Witcher 3"
```

Extraction creates `.local/vanilla/`, decompilation creates `.local/as3/`, and
catalog generation writes `.runtime/catalog.json`. These commands read the
installed game locally without modifying your Witcher 3 installation.

## Build the native ActionScript 3 package

`scripts/build_transformer_mod.py` exports the trained float32 weights into
chunked base64 ActionScript 3, compiles `GwaiTransformer.as`,
`GwaiCapture.as`, `GwaiSharedController.as`, and `GwaiPlayTrace.as` into
`gwint_game.redswf` via FFDec, runs automated compiled-SWF parity & static-init
verification, and packs a self-contained `Mods/modW3GwentAI` bundle:

```sh
python3 scripts/build_transformer_mod.py \
  --checkpoint "/path/to/best.pt" \
  --output-dir .runtime/package-transformer-v1
```

### Build verification checks

Every build verifies:
1. All 9 `Gwai*` ActionScript 3 classes compile cleanly into `gwint_game.swf`
   and `gwint_game.redswf` with zero dangling legacy references.
2. All 9 compiled `Gwai*` static initializers are literal-only (preventing
   Scaleform AVM2 class-init failures inside the game engine).
3. Re-exported compiled `GwaiTransformer.as` + weights execute in Node.js under
   forced multi-frame slicing (`stepEval(job, 1)` across ~21 slices/turn) and
   match PyTorch reference inference on 60 real game positions with `60/60`
   argmax agreement and `0.0` numerical drift between frame-sliced and
   synchronous execution.
4. Difficulty ladder temperatures are bound to one shared model (`Hard = 0.0`,
   `Normal = 2.6`, `Easy = 4.2`) with exact match-winning terminal pass enabled.

The output folder contains the complete Steam Workshop / Vortex layout:

```text
Mods/modW3GwentAI/content/
  blob0.bundle
  metadata.store
  scripts/local/gwai_settings.ws
  scripts/local/gwai_playtrace.ws
```

Generated packages contain a modified game resource (`gwint_game.redswf`); the
repository's open-source license covers original mod source code only and does
not grant rights to redistribute copyrighted game assets.
