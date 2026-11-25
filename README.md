# Gwent: Trained Opponents

**A tougher, smarter, strictly fair Gwent opponent running 100% locally inside The Witcher 3.**

Your Witcher 3 Gwent opponents are now trained on over **4.5 million games of self-play**.

**Gwent: Trained Opponents** replaces the vanilla Gwent logic across **every Story and Practice opponent** with a dedicated neural network running locally inside the game. It plays like a real Gwent player (card advantage, Spy and Decoy timing, Round 2 passes, Scorch and Weather discipline, sharp Round 3 endgames), never cheats or peeks at your hand, and installs in **1 click on Steam Workshop**.

- **Plays fair:** Never reads your hand or deck order. It only sees what a human opponent can legally see: its own hand, its own remaining deck, the public board, graveyards, and public play history.
- **100% local & offline (9.1 MB):** Everything runs directly inside Witcher 3's Gwent screen. No internet connection, no background app, and no external `.exe`.
- **Feels native to Witcher 3:** Keeps all original cards, rules, NPC decks, quests, and rewards untouched. Syncs with **Gameplay -> Gwent Difficulty** (**Easy / Normal / Hard**), includes a 1-click toggle back to vanilla anytime, and adds a **Practice** menu so you can play against 39+ Story, DLC, and Tournament NPC decks using your save's real collection.
- **Safe with mod packs:** Self-contained in `Mods/modW3GwentAI` with no `bin/config` edits. If another overhaul mod adds custom cards outside the base+DLC catalog, that turn cleanly falls back to the vanilla opponent instead of crashing.

---

## Install

### Steam Workshop (Recommended)
1. Subscribe to **Gwent: Trained Opponents** on Steam Workshop.
2. Launch **The Witcher 3: Wild Hunt (Next-Gen / Remastered)** and make sure mods and Steam Workshop mods are enabled under **Settings -> Gameplay**.
3. Load a save and play Gwent. The mod is enabled by default and follows your **Gwent Difficulty** setting.

### Manual Install (GitHub Releases / Vortex)
1. Download the latest `v1.0.0` `.zip` from GitHub Releases.
2. Install it in Vortex (**Install From File**), or extract `Mods/modW3GwentAI` straight into your Witcher 3 folder.

See [Installation & Controls](docs/install.md) for the full mod menu reference, troubleshooting, and removal steps.

> **Compatibility:** Safe to install or remove mid-playthrough (does not modify save files). Requires The Witcher 3 Next-Gen / Remastered (Classic 1.32 is unsupported). Works on Windows, Linux/Proton, and Steam Deck. Conflicts with other mods that replace `gwint_game.redswf`; compatible with script-only, visual, and general gameplay mods.

---

## How Strong Is It?

Tested in paired, seat-swapped full matches (canceling coin-flip and draw luck) using the exact model embedded in the mod:

| Witcher 3 Difficulty | vs. Vanilla Opponent (balanced decks) | Unseen 20-Deck Suite vs. Vanilla | Feel In-Game |
| :--- | :---: | :---: | :--- |
| **Hard** | **`87.3%`** (+335 Elo) | **`77.0%`** | Tight card advantage, Spy/Decoy discipline, smart passes, ruthless Round 3 closing |
| **Normal** | **`~81.0%`** (+255 Elo) | **`~75%`** | Noticeably smarter than vanilla without punishing every small slip |
| **Easy** | **`~70%`** (+150 Elo) | **`~67%`** | Clean play without goofy vanilla blunders (or disable in 1 click anytime for vanilla) |

**All 50 Hard Campaign NPC Decks** (exact vanilla NPC decks, no boosted cards, 196 matched deals):
- **Vanilla opponent:** `25.0%`
- **Gwent: Trained Opponents (Hard):** **`38.3%`** (**+13.3 percentage points** over vanilla)

Full methodology, confidence intervals, and limits are in [Benchmarks & Evaluation](docs/evaluation.md).

---

## Under the Hood

Steam Workshop only allows cooked game assets (no `.exe` or `.dll` companions), and Witcher 3's Gwent screen runs in **Scaleform ActionScript 3**, a single-threaded Flash VM with no GPU and no math libraries. To get strong play inside that budget without freezing the game:

1. **Train a large self-play teacher first:** We trained a large multi-model self-play and endgame-search teacher from scratch over **4.5+ million simulated games** across balanced decks, generated decks, and all 50 Hard campaign rosters, with strict seat-visibility boundaries so hidden opponent cards never enter the deployed player.
2. **Distill it down ~50x for Scaleform (`318k` parameters, `1.27 MB`):** Using knowledge distillation from the teacher's move evaluations, we compressed that play into a compact neural network sized specifically for Witcher 3's Flash VM. It keeps **93-94% top-1 move agreement** and lands within **3.5 Elo** (`49.5%` over 400 games) of the much larger pure teacher while shrinking over 50x.
3. **Run it frame-sliced inside ActionScript 3:** `GwaiTransformer.as` executes the network directly inside `gwint_game.redswf`, yielding back to Witcher 3's render loop every `<= 10 ms` so animations, weather effects, and cursor movement stay smooth (~1.5 to 3 seconds per move, zero UI freezes), paired with an exact solver for mathematically guaranteed winning passes. Every build verifies compiled SWF output directly against PyTorch (**60/60 exact argmax agreement**, `0.0` slice-vs-sync drift).

Read more in [How it works](docs/how-it-works.md).

---

## Roadmap

The built-in `318k` network already delivers the bulk of the strength jump (`+335 Elo` over vanilla) with 1-click Steam Workshop convenience. For a future optional add-on (via GitHub Releases / Nexus Mods), we plan to add a native companion bridge so players who want even faster turns (`<0.5s`) and live search on the full 16.2M model can opt in while keeping the built-in opponent as an automatic fallback.

---

[How it works](docs/how-it-works.md) · [Evaluation](docs/evaluation.md) · [Installation](docs/install.md) · [Build from source](docs/building.md) · [Changelog](CHANGELOG.md) · [License](LICENSE)
