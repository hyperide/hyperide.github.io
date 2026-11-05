from pathlib import Path
from html.parser import HTMLParser
import hashlib, json, unittest

ROOT = Path(__file__).resolve().parents[2]


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.urls = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.urls.append(dict(attrs).get("href", ""))


class ReportContract(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(
            (ROOT / "reports/2026-10-own-results-evidence.json").read_text()
        )

    def test_only_owner_october_merges(self):
        for rows in self.data["reports"].values():
            for r in rows:
                self.assertEqual(r["author"], "alex-mextner")
                self.assertEqual(r["state"], "MERGED")
                self.assertTrue(r["mergedAt"].startswith("2026-10-"))

    def test_images_match_recorded_evidence(self):
        for r in self.data["images"]:
            b = (ROOT / r["path"]).read_bytes()
            self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(hashlib.sha256(b).hexdigest(), r["sha256"])

    def test_live_catalogue_links_not_just_pr_links(self):
        urls = Links((ROOT / "reports/conloca-2026-10/index.html").read_text()).urls
        base = "https://pages.hyperide.ai/alex-mextner/conloca-storybook-previews/"
        for segment in ["main", "pr-404", "pr-489"]:
            self.assertIn(base + segment + "/", urls)

    def test_new_workflows_not_presented_as_merged(self):
        data = self.data["storybookPreviews"]
        self.assertEqual(data["workflowStatus"], "implemented-awaiting-merge-approval")
        self.assertEqual(data["authenticatedProductionBrowserVerified"], False)
        for r in data["pullRequests"]:
            self.assertEqual(r["author"], "alex-mextner")
            self.assertEqual(r["state"], "OPEN")
            self.assertFalse(r["isDraft"])

    def test_all_ci_links_have_source_receipts(self):
        receipts = {r["url"] for r in self.data.get("checks", [])}
        for slug in self.data["reports"]:
            for url in Links((ROOT / "reports" / slug / "index.html").read_text()).urls:
                if "/actions/runs/" in url:
                    self.assertIn(url, receipts)

    def test_pr810_combined_scope_is_supported(self):
        detail = self.data.get("sourceScopes", {}).get("hyperide/hyper-saas#810", {})
        self.assertIn("server/routes/git.ts", detail.get("files", []))
        self.assertIn(
            "packages/state-lib/toolkit/src/async-resource.ts", detail.get("files", [])
        )

    def test_report_styles_have_one_scoped_source(self):
        style = ROOT / "reports/shared/october-own.css"
        self.assertTrue(style.is_file())
        for slug in self.data["reports"]:
            text = (ROOT / "reports" / slug / "index.html").read_text()
            self.assertIn("../shared/october-own.css", text)
            self.assertNotIn("<style>", text)
            self.assertIn('aria-current="page"', text)

    def test_merged_pr_links_match_the_source_record(self):
        import re

        for slug, rows in self.data["reports"].items():
            text = (ROOT / "reports" / slug / "index.html").read_text()
            merged = text.split('id="results"', 1)[1].split("</section>", 1)[0]
            found = {
                int(m.group(1))
                for u in Links(merged).urls
                if (m := re.search(r"/pull/(\d+)$", u))
            }
            self.assertEqual(found, {r["pr"] for r in rows})

    def test_approval_preview_e2e_is_exact_head(self):
        preview = self.data["approvalPreview"]
        checks = [row for row in self.data["checks"] if row.get("job") == 125893569803]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["head_sha"], preview["headRefOid"])
        for row in self.data["checks"]:
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["conclusion"], "success")

    def test_images_have_the_actual_dimensions(self):
        import struct

        for row in self.data["images"]:
            width, height = struct.unpack(
                ">II", (ROOT / row["path"]).read_bytes()[16:24]
            )
            self.assertEqual((row["width"], row["height"]), (width, height))

    def test_approval_preview_ci_receipt_is_exact_head(self):
        preview = self.data["approvalPreview"]
        receipts = [
            row
            for row in self.data["checks"]
            if row["name"] == "CI" and row["head_sha"] == preview["headRefOid"]
        ]
        self.assertEqual(len(receipts), 1)

    def test_descriptive_link_labels_preserve_visible_text(self):
        import re

        for slug in self.data["reports"]:
            text = (ROOT / "reports" / slug / "index.html").read_text(encoding="utf-8")
            for attrs, content in re.findall(r"<a\b([^>]*)>(.*?)</a>", text, re.S):
                label = re.search(r'aria-label="([^"]*)"', attrs)
                visible = " ".join(re.sub(r"<[^>]*>", "", content).split())
                if label and visible and not visible.startswith("#"):
                    self.assertIn(visible, label.group(1))


if __name__ == "__main__":
    unittest.main()
