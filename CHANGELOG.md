# Changelog

## 1.0.0 (2026-11-25)

First public release of **Gwent: Trained Opponents**.

### Highlights
- **Native ActionScript 3 Neural Engine (`GwaiTransformer.as`):** Compact neural network (`318,280` parameters, `1.27 MB` weights, `9.13 MB` total mod bundle) running 100% locally inside `gwint_game.redswf`.
- **Smooth Frame-Sliced Execution:** Initial weight decoding and per-turn inference yield back to Witcher 3's render loop every `<= 10 ms`, keeping animations, weather effects, and cursor movement smooth (~1.5 to 3s per turn, zero UI freezing).
- **Distilled from a Large Self-Play + Search Teacher:** Trained across 4.5M+ simulated games and distilled ~50x for Scaleform. Reaches **`87.3%` (+335 Elo)** vs. the vanilla opponent on balanced competitive decks, **`77.0%`** on unseen generated decks, **`49.5%` (-3.5 Elo)** against the ~50x larger pure teacher, and **`38.3%` (+13.3 pp over vanilla)** across all 50 Hard campaign NPC decks.
- **Universal Story & Practice Coverage:** Works automatically across every Story and Practice opponent, plus a built-in Practice menu for 39+ Story, DLC, and Tournament NPC decks using your save's real collection.
- **Strictly Fair Play:** Uses only own hand, own remaining deck multiset, public board/graveyards/weather/leaders, publicly decoyed cards, and the last 28 public turn events plus exact immediate public action deltas.
- **Calibrated Difficulty Ladder:** Synced with **Gameplay -> Gwent Difficulty** (**Hard** `T=0.0`, **Normal** `T=2.6`, **Easy** `T=4.2`, all with exact match-winning terminal pass), plus a 1-click toggle back to the vanilla opponent anytime.
- **Self-Contained Mod Layout & Safe Fallback:** Lives cleanly inside `Mods/modW3GwentAI` (no `bin/config` edits) and falls back cleanly per turn to the vanilla opponent if another mod adds custom cards outside the 228-card catalog.
- **Compiled Verification:** Automated build checks verify exact embedded float32 weight SHA-256, literal-only AVM2 static initializers across all 9 `Gwai*` classes, **60/60 exact argmax parity** between PyTorch and compiled `GwaiTransformer.as`, and **`0.0`** frame-slice vs. sync drift.
