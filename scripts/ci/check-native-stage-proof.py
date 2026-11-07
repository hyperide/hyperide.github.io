"""Contract for the 7 November native-stage proof block on the Conloca October report.

The block and reports/conloca-2026-10/native-stage-proof.json must agree. The October period and the
October counts stay untouched; the November addendum is a separate, dated block.

Run: python3 scripts/ci/check-native-stage-proof.py
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _report_html import Links, VisibleText  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "reports/conloca-2026-10"
HEAD = "9d469170dbe5504704dae7b6a32d4825cdb11765"
RUN = "https://github.com/conloca/private/actions/runs/42375596312"
ARTIFACT = "https://github.com/conloca/private/actions/runs/42375596312/artifacts/13576130686"
STAGES = ["https://site.pr404.conloca.com/", "https://app.pr404.conloca.com/", "https://storybook.pr404.conloca.com/"]


class NativeStageProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (DIR / "index.html").read_text(encoding="utf-8")
        cls.rec = json.loads((DIR / "native-stage-proof.json").read_text(encoding="utf-8"))
        cls.block = cls.raw.split('id="native-stage-proof"', 1)[1].split("</section>", 1)[0]
        cls.text = VisibleText(cls.block).raw

    def test_three_direct_stage_links_and_nothing_else_for_stages(self):
        urls = [u for u in Links(self.block).urls if "pr404.conloca.com" in u]
        self.assertEqual(urls, STAGES)
        self.assertEqual(self.rec["stageUrls"], STAGES)
        for label in ("Landing", "CMS app", "Storybook"):
            self.assertIn(label, self.text)

    def test_run_and_artifact_links_are_distinct_and_exact(self):
        urls = Links(self.block).urls
        self.assertIn(RUN, urls)
        self.assertIn(ARTIFACT, urls)
        self.assertNotEqual(self.rec["runUrl"], self.rec["artifactUrl"])
        self.assertEqual((self.rec["runUrl"], self.rec["artifactUrl"]), (RUN, ARTIFACT))
        self.assertEqual(self.rec["runId"], 42375596312)
        self.assertEqual(self.rec["artifactId"], 13576130686)
        self.assertIn("https://github.com/conloca/private/pull/404", urls)

    def test_artifact_is_labelled_private_and_temporary_and_not_embedded(self):
        low = self.text.casefold()
        for word in ("access-restricted", "temporary", "2026-11-21", "not full product acceptance", "deployed on pr404; not merged"):
            self.assertIn(word, low)
        self.assertIn("not a permanent video archive", low)
        self.assertNotRegex(self.block, r"<video\b|<source\b|<iframe\b|<img\b|\.webm|\.png|\.zip")
        self.assertRegex(self.rec["kind"], r"private")

    def test_record_holds_exact_head_build_hashes_and_no_private_payload(self):
        r = self.rec
        self.assertEqual(r["head"], HEAD)
        self.assertEqual(r["build"], "ce4948f9558870dce11f07102a643e2807c73395")
        self.assertEqual(r["verifiedAt"][:10], "2026-11-07")
        self.assertEqual((r["artifactSize"], r["artifactExpiresAt"]), (519359, "2026-11-21T13:18:03Z"))
        self.assertEqual(r["artifactSha256"], "85cef4edb76cfb7f3292c93c060e57e1e9cdb6625ea5738be4cc5d5ce1859cf5")
        v = {x["id"]: x for x in r["videos"]}
        self.assertEqual(len(v), 2)
        self.assertEqual((v["cms"]["width"], v["cms"]["height"], v["cms"]["durationSeconds"], v["cms"]["frames"]), (1280, 800, 5.6, 140))
        self.assertEqual((v["app"]["width"], v["app"]["height"], v["app"]["durationSeconds"], v["app"]["frames"]), (390, 844, 4.88, 122))
        self.assertEqual(v["cms"]["sha256"], "7c7739c6526730c3ea906b852e7f2ce7a0d7df39d218a6147954b6cea2257f2a")
        self.assertEqual(v["app"]["sha256"], "1fcf67580aec5acd7bd74c70b20d565b229f5d1f9c9f92ea53e2baa181105e00")
        self.assertEqual(set(r), {"purpose", "verifiedAt", "head", "build", "stageUrls", "runUrl", "runId", "artifactId", "artifactUrl", "artifactSha256", "artifactSize", "artifactExpiresAt", "videos", "kind"})
        blob = json.dumps(r)
        for banned in ("cookie", "storageState", "/home/", "token", "catalogue"):
            self.assertNotIn(banned, blob)

    def test_october_period_and_counts_survive_and_november_is_separate(self):
        page = self.raw
        for needle in ("October 2026", "Conloca: October 2026"):
            self.assertIn(needle, page)
        ev = {s: json.loads((ROOT / "reports" / s / "media-evidence.json").read_text()) for s in ("hyper-2026-10", "state-lib-2026-10", "conloca-2026-10")}
        self.assertEqual(sum(len(e["images"]) for e in ev.values()), 36)
        self.assertEqual(sum(len(e["originalRequests"]) for e in ev.values()), 26)
        self.assertEqual(sum(len(e["videos"]) for e in ev.values()), 4)
        mine = ev["conloca-2026-10"]
        self.assertEqual(len(re.findall(r'class="owner-request"', page)), len(mine["originalRequests"]))
        self.assertEqual(len(re.findall(r"<img\b", page)), len(mine["images"]))
        self.assertEqual(len(re.findall(r"<video\b", page)), len(mine["videos"]))
        self.assertNotIn("keeps no video or trace artifact", page)
        proof = page.split('id="proof"', 1)[1]
        self.assertIn("four clips", VisibleText(proof).raw)
        self.assertIn("production browser checks", VisibleText(proof).raw)
        self.assertNotIn("November 2026 · Ultra", page)
        self.assertEqual(self.block.count("class=\"outcome\""), 0)

    def test_block_is_inside_the_static_html_and_before_proof_link_list(self):
        self.assertLess(self.raw.index('id="coverage"'), self.raw.index('id="native-stage-proof"'))
        self.assertIn('href: "#native-stage-proof"', self.raw)
        self.assertNotIn("display:none", self.block.replace(" ", ""))
        self.assertNotIn("<details open", self.block)


if __name__ == "__main__":
    unittest.main()
