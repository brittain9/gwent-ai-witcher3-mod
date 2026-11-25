# Installation & Controls

Everything is prebuilt and runs locally inside the game. You do not need Python, Java, Node.js, or any external app to play.

## Requirements

- **Safe mid-save:** Safe to install or remove at any point in a playthrough. It only replaces the Gwent opponent logic and never modifies save files.
- **The Witcher 3: Wild Hunt (Next-Gen / Remastered)** on Windows, Linux/Proton, or Steam Deck. Classic 1.32 is unsupported.
- Only one copy of this mod installed at a time.
- Other mods that replace `gwint_game.redswf` will conflict (script merging cannot combine two replacement Gwent screens). Script-only, visual, and general gameplay mods are compatible.

## Install

Close the game before installing or updating.

### Steam Workshop (Recommended)
1. Subscribe to **Gwent: Trained Opponents** on Steam Workshop and let Steam finish downloading.
2. Launch the game and check **Settings -> Gameplay** to make sure mods and Steam Workshop mods are enabled.
3. Load a save and play Gwent. The mod is enabled by default.

### Manual Install (GitHub Releases / Vortex)
1. Download the release `.zip` from GitHub Releases (or Nexus Mods when available).
2. Either install it in Vortex (**Install From File** -> enable & deploy), or extract `Mods/modW3GwentAI` directly into your Witcher 3 game folder (next to `bin/` and `content/`).
3. Make sure local mods are enabled under **Settings -> Gameplay**.

No manual settings XML or `bin/config` copying is needed.

## In-Game Controls

Open **Settings -> Mods -> Gwent: Trained Opponents** (use **Settings -> Mods**, not the main-menu online **MODS** button):

| Control | What it does |
| :--- | :--- |
| **Enable AI** | Turns the trained opponent on or off for all Story and Practice matches |
| **Gwent difficulty** | Syncs with **Gameplay -> Gwent Difficulty** (Easy / Normal / Hard) |
| **Practice opponent / deck** | Picks any of 39+ Story, DLC, or Tournament NPC decks for Practice |
| **Start practice match** | Starts a Practice match from the pause menu using your save's real collection (no quest rewards or wagers affected) |
| **Show AI status** | Shows current settings and decision breakdown from your latest match |
| **Show decision source** | Optional banner showing whether each move came from the network, fallback, or vanilla |
| **Record public play-by-play** | Optional local log of public actions and match results (excludes hidden cards and deck order; nothing uploads) |

## Verify It's Working

Play a match, then open **Show AI status** under **Settings -> Mods -> Gwent: Trained Opponents**. The latest-match section reports the active policy and how many moves came from the trained network.

## Update or Remove

- **Steam Workshop:** Let Steam update while the game is closed. Unsubscribe to remove, then restart the game.
- **Vortex:** Replace with the new archive and deploy, or disable/remove and deploy.
- **Manual install:** Delete `Mods/modW3GwentAI` while the game is closed.

## Troubleshooting

If you hit a script compilation error on launch, check for a duplicate install or another mod that modifies `gwint_game.redswf`. When reporting an issue, include your mod version, Witcher 3 version, install method, and a screenshot of **Show AI status** after the match.

[Build from source](building.md) covers the separate developer build workflow.
