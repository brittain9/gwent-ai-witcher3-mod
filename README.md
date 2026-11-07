# Gwent: A Worthy Opponent

A neural-network opponent trained through **self-play reinforcement learning**
for the Gwent minigame in **The Witcher 3: Wild Hunt**. It makes its decisions
locally, using its own hand and the public board, while preserving the game's
cards, rules, NPC decks, rewards and collection progression.

The policy learned from simulated games against itself, with match results
providing the learning signal. During play, the trained network runs inside
the game's Gwent screen. It needs no server or AI subscription.
[How it works](docs/how-it-works.md) explains the policy and information it uses.

## Install

1. Download the player ZIP and its checksum from the
   [latest release](https://github.com/brittain9/gwent-ai-witcher3-mod/releases/latest).
2. Install **Python 3.12+** if needed, extract the whole ZIP to a writable folder,
   and close the game and REDlauncher.
3. On **Windows**, open `Install.cmd`. On **Linux**, run `sh Install.sh`.
   Select the game folder containing `bin` and `content` when asked.
4. Start the game, load a save, and open **Options > Mods > Gwent: A Worthy Opponent**.
   Turn on **Enable AI**.

First setup downloads verified build tools when needed and creates the mod
from your installed game's resources. Models are included in the player
download; PyTorch and training tools are not needed. The finished opponent
runs inside the game. Keep the extracted folder: it holds your installation
receipt and rollback backups.

[Installation, compatibility and removal](docs/install.md) ·
[Build from source](docs/building.md) · [Changelog](CHANGELOG.md)

## Playing

- **Enable AI** selects the trained opponent in both story and practice matches.
  Turn it off to use the stock opponent.
- The mod has **Easy, Medium and Hard** policies. Story matches follow the
  game's Gwent difficulty; its middle setting may be labeled Normal.
- Practice lets you choose an opponent and AI difficulty separately, using
  your existing collection. Native NPC deck selection stays with the game.
- Moves run when computation finishes, with no artificial thinking delay.
- Coaching, analysis and **Stronger story opponents** are optional and off
  by default. Troubleshooting controls are grouped at the end of the menu.

The network sees its own hand, public cards, scores and card counts. It does
not read your hidden hand or either deck's order. Difficulty selects different
trained policies, rather than granting hidden information or changing cards.

## Compatibility and limits

Setup checks the exact Gwent resources required by the mod. The supported
resource set is from the Remastered edition; executable patch labels alone
do not determine compatibility. Classic 1.32 is unsupported. Another mod that
replaces the Gwent screen can conflict.

Trained play has been observed on Windows and Linux through Steam/Proton.
The AI is fallible; there is no claim of optimal play or a calibrated human
win rate. A strange-looking move needs the complete decision context to judge.
Your collection and the NPC's native deck still affect each match.

## Diagnose a problem

**Show AI verification** reports the requested selection and recorded decision
sources. **Show decision source** displays network and fallback messages.
**Record play-by-play** saves public actions, settled boards and match results
locally, including the selected tier and model identity in new traces.

Both debug controls are off by default. Recording excludes hidden hands, deck
order and unrevealed choices, and nothing uploads automatically. See
[troubleshooting](docs/install.md#troubleshooting) for reports and rollback.

## License

Original code, documentation and the inference weights in the player download
use the [MIT license](LICENSE). The game and external tools retain their own
rights and licenses. See [NOTICE](NOTICE.md).
