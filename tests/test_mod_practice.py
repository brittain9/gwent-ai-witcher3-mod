from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts import mod_fun, mod_practice


class PracticeCatalogTests(unittest.TestCase):
    def test_menu_and_script_select_the_same_complete_opponent_catalog(self) -> None:
        mod_practice.verify()
        self.assertEqual(len(mod_practice.DECKS), len(set(mod_practice.DECKS)))
        self.assertEqual(
            mod_practice.DECKS[:5],
            ("NKHard", "NilfHard", "ScoiaHard", "NMLHard", "SkelHard"),
        )
        named = {
            name
            for name, kind in mod_fun.DECK_CATEGORIES.items()
            if kind in ("named", "tournament")
        }
        self.assertEqual(set(mod_practice.DECKS[5:]), named)
        self.assertNotIn("tutorialDeck", mod_practice.DECKS)

    def test_every_named_opponent_has_actual_decks_in_the_local_game_catalog(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        catalog_path = root / ".runtime/catalog.json"
        if not catalog_path.is_file():
            self.skipTest("requires a locally generated game catalog")
        local_catalog = json.loads(catalog_path.read_text())
        catalog = {deck["collection"] for deck in local_catalog["decks"]}
        for deck in mod_practice.DECKS[5:]:
            self.assertIn(deck, catalog)


if __name__ == "__main__":
    unittest.main()
