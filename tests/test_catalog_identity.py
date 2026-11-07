import copy
import unittest

from learning.catalog_identity import catalog_identity


class CatalogIdentityTests(unittest.TestCase):
    def test_provenance_paths_do_not_change_model_identity_but_gameplay_does(self):
        catalog = {
            "schema_version": 1,
            "cards": [{"id": 9001, "power": 4}],
            "decks": [],
            "source_manifest": {"path": "first"},
        }
        expected = catalog_identity(catalog)
        relocated = copy.deepcopy(catalog)
        relocated["source_manifest"]["path"] = "second"
        relocated["source_hashes"] = {"manifest": "different"}
        self.assertEqual(catalog_identity(relocated), expected)
        for field, value in [
            ("cards", [{"id": 9001, "power": 5}]),
            ("decks", [{"cards": [9001]}]),
            ("runtime_mappings", {"types": {"unit": 9}}),
            ("schema_version", 2),
        ]:
            changed = copy.deepcopy(catalog)
            changed[field] = value
            self.assertNotEqual(catalog_identity(changed), expected)
