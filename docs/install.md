# Installation and controls

The prebuilt player package includes the finished modified Gwent screen and
trained model weights. The opponent runs locally inside the game.
Python, Java, Node.js and model-training tools are not required to play.

## Requirements

- The Witcher 3: Wild Hunt — Remastered, with compatible Gwent resources.
  Classic 1.32 is unsupported.
- One active installation of this mod. Remove an older manual installation
  before switching to a mod manager or Workshop subscription.

Other mods replacing `gwint_game.redswf`, the Gwent screen, can conflict.
Script merging cannot combine two replacement screens.

## Install

Close the game and REDlauncher before installation or updates.

When a **Steam Workshop** listing is available, subscribe and let Steam finish
its download. In the game, check **Settings > Gameplay**: mods and Steam
Workshop mods must be enabled.

When a **Nexus** listing is available, choose **Mod Manager Download**, install the
mod in Vortex, then enable and deploy it. If you downloaded the native ZIP
from GitHub, use Vortex's **Install From File** instead. The archive uses the
same layout for both routes. Local mods must be enabled in the game's
**Settings > Gameplay** options.

Start the game and open **Settings > Mods > Gwent: Trained Opponents**.
**Enable AI** is on by default. Use **Gameplay > Gwent difficulty** to choose
the challenge. No separate settings XML or configuration-file copying is needed.
After loading a save, **Gwent difficulty** also appears in this mod's menu.
Both controls change the same game setting. This second control is in the
current source on `main`; it awaits a new player package.

The main-menu **MODS** button opens the game's separate online mod browser.
Use **Settings > Mods** for this mod's controls.

## Controls

| Control | Effect |
|---|---|
| Enable AI | Trained AI when on, stock AI when off, for story and practice |
| Gwent difficulty (shared with Gameplay) | The game's existing setting chooses the native NPC deck level and controller difficulty for both story and practice |
| Practice opponent / deck | Chooses the native opponent collection for practice |
| Start practice match | Opens deck selection from a loaded game's pause menu, outside combat and dialogue |
| Show AI status | Shows current settings and the latest recorded match |
| Show decision source | Optional network, fallback and stock-handoff messages |
| Record public play-by-play | Optional local recording of public actions and results |

The player mod has no coaching, hints, win-chance meter or postgame analysis.
There is no added thinking delay; loading, computation, game animations and
banners still take real time.

Practice uses your current collection and restores its temporary manager,
result and tutorial state on completion or cancellation. It does not grant
an NPC quest reward or wager. Native achievement callbacks remain enabled;
actual achievement delivery has not been verified.

## Check that the trained opponent is playing

Play a match, then open **Show AI status**. Its current-settings section and
latest-match section are separate. The latest match reports the selected
policy and decision sources, so enabling the mod alone is not treated as proof
that the network played.

**Show decision source** displays `network`, `greedy-fallback` or a stock
handoff. A fallback means the trained policy did not complete that decision.
A missing banner alone does not prove that the mod is absent: the banner
setting may be off.

## Troubleshooting

Retain any script compilation error and close the game if compilation fails.
Check for a second installation or another Gwent-screen replacement before
retrying. Do not replace the game's own bundles to install this mod.

For a report, include the mod version, game version, installation method and
an **AI status** screenshot after the affected match. Optional public recording
stores actions, settled boards, results and policy identity in the game's
settings. It excludes hidden cards, deck order and unrevealed choices. Nothing
uploads automatically. Review local paths before sharing files.

Developers can export a recorded timeline with the source utility
`scripts/harvest_playtrace.py`. These optional development tools use Python;
they are not needed for installation or play.

## Update and remove

For Workshop, let Steam update the subscribed item while the game is closed.
Unsubscribe to remove it, then restart the game. Steam may retain cached
content separately from the subscription state.

For Vortex, replace the installed version with the new download, then deploy.
To remove it, disable and remove it in Vortex, then deploy the change. Keep
unrelated mods enabled or disabled according to your own setup.

## Upgrade from the earlier Python installer

The older installer owns its files through the receipt and backups in its
original extracted folder. Close the game and REDlauncher, then run
**Uninstall.cmd** on Windows or **sh Uninstall.sh** on Linux from that folder.
Alternatively, from that same folder:

```sh
python3 scripts/setup_player.py --uninstall
```

The old installer checks its receipt and installed hashes, restores backups
and removes its owned files, including the former settings XML. Keep that
folder until removal succeeds. Then install the prebuilt package through
your chosen manager. Installing over the old files can leave duplicate
versions or restore the old version when a manager later removes its own copy.

[Build from source](building.md) describes the separate developer workflow.
