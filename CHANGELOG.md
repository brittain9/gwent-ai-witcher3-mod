# Changelog

## 0.1.1 — 2026-11-07

### Changed

- Renamed the mod to Gwent: Trained Opponents across settings, installer
  messages and package metadata.
- Rewrote the introduction with the equal-deck stock-AI comparison and
  explained the difficulty and design goals. The trained policies are unchanged.
- Added model statistics and identified the supported Remastered edition.

## 0.1.0 — 2026-11-07

### Added

- Downloadable setup with three trained inference policies, verified tool
  downloads and local preparation from an installed game.
- Gwent: A Worthy Opponent menu, one Enable AI switch, Easy/Medium/Hard
  practice selection, and clearly grouped optional assistance and diagnostics.
- Selection and model identity in public traces, including requested and
  effective tiers and story/practice mode.
- Install/removal process guards and checked receipts with rollback backups.

### Fixed

- Story verification now reports the requested selection and native difficulty.
- Stock-story turns are identified separately from trained-policy fallbacks.
- Compiled model checks verify actual embedded tensors and encoding tables.

### Notes

- The trained policies make decisions locally using their own seat's view.
- The game retains its cards, rules, NPC decks, rewards and collection.
- Setup requires compatible Remastered resources and Python 3.12+.
