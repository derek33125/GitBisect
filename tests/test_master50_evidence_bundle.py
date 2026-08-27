from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.audit_first_bad_relevance import CANONICAL_FIRST_BAD
from tools.export_master50_evidence_bundle import (
    PROFILE_EXCLUSIONS,
    first_bad_map,
    select_issues,
)


ROOT = Path(__file__).resolve().parents[1]


class MasterFiftyEvidenceBundleTests(unittest.TestCase):
    def test_profile_cohort_is_exactly_fifty(self) -> None:
        profiles = json.loads((ROOT / "tools" / "lm_bisect_profiles.json").read_text(encoding="utf-8"))
        issues = select_issues(profiles)
        self.assertEqual(len(issues), 50)
        self.assertTrue(set(issues).isdisjoint(PROFILE_EXCLUSIONS))
        self.assertTrue(set(CANONICAL_FIRST_BAD).issubset(issues))

    def test_scoped_first_bads_keep_canonical_shas(self) -> None:
        profiles = json.loads((ROOT / "tools" / "lm_bisect_profiles.json").read_text(encoding="utf-8"))
        mapping = first_bad_map(profiles, ROOT / "web-presentation-data" / "data" / "site-data.json")
        for issue, sha in CANONICAL_FIRST_BAD.items():
            self.assertEqual(mapping[issue], sha)


if __name__ == "__main__":
    unittest.main()
