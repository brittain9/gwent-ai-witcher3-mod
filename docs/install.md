# Installation and controls

There is no public downloadable mod package yet. These instructions describe
the installer and the local build workflow; they do not point to a released
artifact. [Building](building.md) requires your own game installation and a
compatible trained checkpoint supplied separately.

## Compatibility

Live trained play and recording have been checked on Linux with Steam/Proton
and remastered 5.01 game inputs. Windows gameplay, other stores and a clean
installation on another machine remain unverified. Classic 1.32 lacks the
script annotation support this mod uses.

The installer checks the resources it depends on. A different version or card
mod can fail those checks even when the version label looks similar. Do not
bypass a mismatch: the replacement screen must be built and tested against
the actual resources. Another mod that replaces `gwint_game.redswf`, the Gwent
screen, can conflict; script merging cannot combine two replacement screens.

## Install a locally built package

Run these commands from the repository root. Close the game and launcher first.
Replace the example game path with the directory containing `bin` and `content`.

```sh
uv run python scripts/install_mod.py --check --package .runtime/mod-build --game-dir "/path/to/The Witcher 3"
uv run python scripts/install_mod.py --package .runtime/mod-build --game-dir "/path/to/The Witcher 3"
```

The installer accepts either a successful build directory containing
`manifest.json` or an assembled package containing `package-manifest.json`.
It verifies hashes, checks dependencies and conflicts, backs up files it will
replace, and records a receipt. It also archives and invalidates the generated
script cache so the next start compiles the current mod set. It preserves
shipped scripts and does not write save files. Keep the receipt and backups.

Only one edition can be installed at a time. Uninstall an existing edition
before replacing it. On first launch, retain any script compilation error text
and close the game if compilation fails.

## Choose the opponent

Load a save and open **Options > Mods > Gwent**.

| Control | Effect |
|---|---|
| Enable trained AI | Selects trained AI when on, stock AI when off, in story and practice matches |
| Gameplay > Gwent difficulty | Easy, Normal or Hard; selects native NPC deck level and the story-match policy tier |
| Practice opponent / deck | Selects the native opponent collection for a practice match |
| Practice AI difficulty | Selects Easy, Normal or Hard for trained practice independently of native deck level |
| Start practice match (loaded game only) | Opens deck selection from a loaded game's pause menu, outside combat and dialogue |
| Stronger story opponents | Optional raises: named and tournament opponents to Hard, generic opponents to at least Normal; never lowers the chosen tier |

Practice uses your current collection. It restores the saved manager, result
and tutorial state on completion or cancellation; it does not grant an NPC
quest reward or wager. Native achievement callbacks remain enabled, but actual
achievement delivery has not been verified. Use a separate save slot during
experimental testing.

There is no think-time scale or added wait. First-use model loading, inference,
game animations and message banners still take real time. Optional coaching
and analysis are off by default. The win-chance meter exposes the AI's estimate
from its own view; leave assistance off to judge an unaided game.

## Diagnose a problem

Turn on **[Debug] Show decision source** and, for a recorded test,
**[Debug] Record play-by-play** before starting a match.

- `network Nms` means the network chose the action. The time covers the serving
  path, including first-use loading; it is not total animation time.
- `greedy-fallback` means inference failed and a simple rule chose the action.
- `fallback (vanilla AI plays this turn)` means an integration step failed and
  the stock AI handled the turn.

A missing banner alone does not identify a missing bundle. The master switch
or debug signal may be off. **Show AI verification (current / latest match)**
reports decision-source counts and the first integration error, even with
on-screen source signals off. Counts identify reported choices; the public
trace confirms actions that actually completed.

Read the installed identity and diagnostic settings with:

```sh
uv run python scripts/install_mod.py --report --game-dir "/path/to/The Witcher 3"
```

On Windows, or if automatic settings discovery fails, add
`--documents "/path/to/Documents/The Witcher 3"`.
To export the recorded public timeline, point the reader at the active settings
file, usually `dx12user.settings` for DirectX 12:

```sh
uv run python scripts/harvest_playtrace.py "/path/to/dx12user.settings"
```

Exports go under `.runtime/playtraces/`. Complete, active and interrupted games
remain distinct. The recording excludes hidden hands, deck order and private
choices, so it cannot fully replay hidden draws or prove that a strange-looking
move was wrong. Review a report before sharing it; installer output can contain
local filesystem paths. No upload runs automatically.

## Uninstall

With the game and launcher closed:

```sh
uv run python scripts/install_mod.py --uninstall
```

Uninstall verifies the receipt and backups, restores replaced files and removes
only the files it installed. It refuses unexpected edits rather than overwriting
them. Backups, saved settings and saves remain. Uninstall also invalidates the
generated script cache so removed mod code is not retained in it.
