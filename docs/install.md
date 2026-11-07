# Installation and controls

The player download includes the three trained policies and setup tools.
Setup reads your installed game, verifies its Gwent resources, and creates the
modified screen locally. Once installed, the opponent runs inside the game.

## Requirements

- An x64 Windows or Linux installation of The Witcher 3 with matching
  Remastered Gwent resources. Classic 1.32 is unsupported.
- Python 3.12 or newer.
- Internet access for the first setup, unless the required tools are already
  installed or cached. Setup can use an existing JDK 11+ and Node.js 24+;
  otherwise it downloads verified tools. [Tool details](tool-downloads.md).

PyTorch, model training, a Java service and an AI subscription are not needed
to play. Build tools are used only during preparation. Other mods that replace
`gwint_game.redswf`, the Gwent screen, conflict; script merging cannot combine
two replacement screens. The installer reports detected conflicts before writing.

## Install the player download

1. Download the player ZIP and `.sha256` from the release page.
2. Extract the entire ZIP to a writable folder. Keep it after installation:
   `.runtime` will hold the receipt and backups needed for removal.
3. Close The Witcher 3 and REDlauncher.
4. Run `Install.cmd` on Windows, or `sh Install.sh` on Linux.
5. Enter the game folder containing `bin` and `content` when asked. For Steam,
   **Manage > Browse local files** opens that folder.

Setup verifies the download and game, prepares the mod, checks its compiled
policy data, then installs it with backups. Progress and errors remain in the
terminal. If a compatibility check fails, retain the error and resolve the
cause before retrying; do not bypass the pins. A permission error in the game
folder may require an elevated terminal, using the same extracted mod folder.

You can also use PowerShell or a Linux terminal directly:

```powershell
py -3 scripts/setup_player.py --game-dir "C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3"
```

```sh
python3 scripts/setup_player.py --game-dir "/path/to/The Witcher 3"
```

On Windows, use `python` instead of `py -3` if Python is installed without
the launcher. `--check` checks the download and compatible game without
installing. `--prepare-only` prepares a package without changing the game.
`--offline` uses supported installed tools and verified cached downloads.

On first launch, retain any script compilation error and close the game if
compilation fails. Load a save and open
**Settings > Mods > Gwent: Trained Opponents**.

## Controls

| Control | Effect |
|---|---|
| Enable AI | Trained AI when on, stock AI when off, for story and practice |
| Gameplay > Gwent difficulty | Chooses native NPC deck level and story policy; Medium corresponds to the game's middle setting |
| Practice opponent | Chooses the native opponent collection for practice |
| Practice AI difficulty | Easy, Medium or Hard, independently of native deck level |
| Start practice match | Button that opens deck selection from a loaded game's pause menu, outside combat and dialogue |
| Stronger story opponents | Optional minimum policy tiers for named/tournament and generic opponents; never lowers the selected tier |

Practice uses your current collection and restores its temporary manager,
result and tutorial state on completion or cancellation. It does not grant an
NPC quest reward or wager. Native achievement callbacks remain enabled;
actual achievement delivery has not been verified.

Coaching and analysis controls are optional and off by default. The win-chance
meter shows the AI's own estimate. There is no think-time scale or added wait;
loading, computation, game animations and banners still take real time.

## Troubleshooting

Open the troubleshooting group at the end of the mod menu:

- **Show AI status (current / latest match)** opens a report with current settings
  and the latest recorded match, including source counts even when banners are off.
- **Show decision source** displays `network`, `greedy-fallback` or a stock
  handoff. A fallback means the trained policy did not complete that decision.
- **Record public play-by-play** saves public events locally. New records include
  mode, opponent identifier, requested/effective tier and model hashes.

A missing banner alone does not prove that the mod is absent. The main switch
or signal setting may be off. Check verification after an actual match.

From the extracted download folder, obtain a settings report:

```powershell
py -3 scripts/setup_player.py --report --game-dir "C:\path\to\The Witcher 3" --documents "$HOME\Documents\The Witcher 3"
```

Use the actual Documents location, including OneDrive redirection if present.
To export the public timeline from the active settings file:

```powershell
py -3 scripts/harvest_playtrace.py "C:\path\to\Documents\The Witcher 3\dx12user.settings"
```

Reports can contain local filesystem paths; review them before sharing.
The public recorder excludes hidden cards and deck order, so it cannot fully
reconstruct a deal or prove that a tactical choice was wrong. No upload runs
automatically.

## Update and remove

Keep the current extracted folder and its backups. To prepare an update while
the current version remains installed, extract the new download into a separate
folder. From that new folder, name the folder used for the current installation:

```powershell
py -3 scripts/setup_player.py --prepare-only --installed-from "C:\path\to\current mod folder" --game-dir "C:\path\to\The Witcher 3"
```

```sh
python3 scripts/setup_player.py --prepare-only --installed-from "/path/to/current mod folder" --game-dir "/path/to/The Witcher 3"
```

Setup verifies the old receipt and all installed files before preparing the new
package. It leaves the current installation and its receipt in the old folder.
After preparation succeeds, remove the old version using its original folder,
then run the new folder's install launcher. Removal is always explicit.

`--installed-from` is available only with `--check` or `--prepare-only`.
Preparing again in the folder that owns the current installation needs only
`--prepare-only`, without naming another folder.

Close the game and REDlauncher, then run `Uninstall.cmd` or `sh Uninstall.sh`
from the folder used to install. Alternatively:

```sh
python3 scripts/setup_player.py --uninstall
```

The installer verifies its receipt and installed hashes, restores backed-up
files and removes only its installed files. It refuses unexpected edits.
Backups and saves remain. Generated script caches are invalidated so the
game compiles the current mod set on its next start.
