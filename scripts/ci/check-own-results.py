from pathlib import Path
import hashlib, html, json, re, sys, unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _report_html import Links, VisibleText  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


REJECTED_CLAIMS = [
    "protected storybook",
    "ready for approval",
    "catalogue snapshots",
    "publisher deployed-stage e2e",
    "automatic link refresh",
]


class ReportContract(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(
            (ROOT / "reports/2026-10-own-results-evidence.json").read_text()
        )

    def _all_rows(self):
        return [r for rows in self.data["reports"].values() for r in rows]

    def _assert_scoped_row(self, r):
        # Mixed pull request: only our named commits are published, never the other authors' history.
        self.assertTrue(r["mixedAuthors"])
        self.assertTrue(r["scopeCommits"])
        self.assertTrue(all(len(c) == 40 for c in r["scopeCommits"]))
        self.assertEqual(r["creditedCommitAuthors"], {"alex-mextner": len(r["scopeCommits"])})
        self.assertGreater(r["ownCommits"], len(r["scopeCommits"]))
        for key in ("commitAuthors", "changedFiles", "additions", "deletions"):
            self.assertNotIn(key, r)

    def _assert_whole_row(self, r):
        self.assertEqual(set(r["commitAuthors"]), {"alex-mextner"})
        expected = "pr-author" if r["author"] == "alex-mextner" else "commit-author"
        self.assertEqual(r["attribution"], expected)

    def test_only_owner_october_merges(self):
        for r in self._all_rows():
            self.assertIn(r["author"], ("alex-mextner", "smoothbricker"))
            self.assertEqual(r["state"], "MERGED")
            self.assertTrue(r["mergedAt"].startswith("2026-10-"))
            if r["author"] != "alex-mextner":
                self.assertEqual(r["mergedBy"], "alex-mextner")
            if r["attribution"] == "commit-scope":
                self._assert_scoped_row(r)
            else:
                self._assert_whole_row(r)

    def test_inventory_counts_add_up_per_repo(self):
        inv = self.data["inventory"]
        keys = {(r["repo"], r["pr"]) for r in self._all_rows()}
        summary = inv["excludedSummary"]
        for repo, merged in inv["mergedInOctober"].items():
            self.assertEqual(inv["credited"][repo] + summary["byRepository"][repo], merged)
            self.assertEqual(len({k for k in keys if k[0] == repo}), inv["credited"][repo])
        self.assertEqual(len(keys), sum(inv["credited"].values()))
        self.assertEqual(sum(summary["byRepository"].values()), summary["total"])
        self.assertEqual(sum(summary["byReason"].values()), summary["total"])
        self.assertRegex(summary["localAuditSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(inv["reviewedAt"], self.data["verifiedAt"])

    LOGIN_KEYS = ("author", "mergedBy", "login", "account")
    COUNT_KEYS = ("commitAuthors", "creditedCommitAuthors")

    def _logins(self, node):
        if isinstance(node, list):
            return set().union(*[self._logins(item) for item in node]) if node else set()
        if not isinstance(node, dict):
            return set()
        found = set()
        for key, value in node.items():
            if key in self.LOGIN_KEYS and isinstance(value, str):
                found.add(value)
            elif key in self.COUNT_KEYS and isinstance(value, dict):
                found |= set(value)
            found |= self._logins(value)
        return found

    def test_public_record_has_no_census_of_other_peoples_work(self):
        inv = self.data["inventory"]
        self.assertNotIn("excluded", inv)
        self.assertEqual(
            set(inv["excludedSummary"]),
            {"total", "byRepository", "byReason", "localAuditSha256", "note"},
        )
        # The only people who appear anywhere in the record are the owner and the alias account.
        self.assertEqual(self._logins(self.data) - {"alex-mextner", "smoothbricker"}, set())
        self.assertNotIn("partialScope", (ROOT / "reports/2026-10-own-results-evidence.json").read_text())
        # Per-person commit counts exist only for our own credited pull requests.
        for r in self._all_rows():
            if "commitAuthors" in r:
                self.assertEqual(set(r["commitAuthors"]), {"alex-mextner"})

    def test_images_match_recorded_evidence(self):
        for r in self.data["images"]:
            b = (ROOT / r["path"]).read_bytes()
            self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(hashlib.sha256(b).hexdigest(), r["sha256"])

    def test_rejected_catalogue_links_and_claims_are_absent(self):
        for slug in self.data["reports"]:
            text = (ROOT / "reports" / slug / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("pages.hyperide.ai", text.casefold())
            visible = VisibleText(text).text
            raw = " ".join(text.split()).casefold()
            for phrase in REJECTED_CLAIMS:
                self.assertNotIn(phrase, visible)
                self.assertNotIn(phrase, raw)

    def test_conloca_keeps_tested_preview_attribution_and_images(self):
        text = (ROOT / "reports/conloca-2026-10/index.html").read_text(encoding="utf-8")
        urls = Links(text).urls
        for url in [
            "https://site.pr489.conloca.com/getting-started/",
            "https://github.com/conloca/private/actions/runs/41626469902/job/125893569803",
            "https://github.com/conloca/private/actions/runs/40534284367",
            "images/theme-before.png",
            "images/theme-after.png",
        ]:
            self.assertIn(url, urls)
        self.assertIn("Work by Ultra / Mextner", text)

    def test_catalogue_receipts_are_historical_only(self):
        self.assertEqual(self.data["storybookPreviews"]["status"], "historical")

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
        style = ROOT / "reports/shared/october-own-20261106.css"
        self.assertTrue(style.is_file())
        for slug in self.data["reports"]:
            text = (ROOT / "reports" / slug / "index.html").read_text()
            self.assertIn("../shared/october-own-20261106.css", text)
            self.assertNotIn("<style>", text)
            self.assertIn('aria-current="page"', text)

    def _outcome_pr_links(self, slug):
        outcomes = self._page(slug).split('id="outcomes"', 1)[1].split("</section>", 1)[0]
        urls = Links(outcomes).urls
        return {int(m.group(1)) for u in urls if (m := re.search(r"/pull/(\d+)$", u))}

    def test_merged_pr_links_match_the_source_record(self):
        for slug, rows in self.data["reports"].items():
            found = self._outcome_pr_links(slug)
            # Only each group's headline pull requests are linked; coverage of the
            # rest is recorded per claim in the source record.
            headline = {pr for c in self.data["claims"] if c["report"] == slug for pr in c["prs"]}
            self.assertEqual(found, headline)
            self.assertLessEqual(found, {r["pr"] for r in rows})

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

    def _page(self, slug):
        return (ROOT / "reports" / slug / "index.html").read_text(encoding="utf-8")

    def test_static_header_summary_and_outcomes_survive_blocked_js(self):
        # Everything below is checked on the raw HTML, with scripts removed.
        for slug, minimum in (
            ("state-lib-2026-10", 7),
            ("conloca-2026-10", 7),
        ):
            text = self._page(slug)
            no_script = re.sub(r"<script\b.*?</script>", "", text, flags=re.S)
            header = re.search(r"<h1>(.+?)</h1>", no_script)
            self.assertIsNotNone(header, slug)
            self.assertIn("October 2026", header.group(1))
            self.assertIn('<p class="subtitle">', no_script)
            overview = no_script.split('id="overview"', 1)[1].split("</section>", 1)[0]
            self.assertGreaterEqual(overview.count('class="summary-card"'), 3)
            self.assertGreaterEqual(len(re.findall(r"<p>", overview)), 1)
            outcomes = no_script.split('id="outcomes"', 1)[1].split("</section>", 1)[0]
            self.assertGreaterEqual(outcomes.count('class="outcome"'), minimum, slug)
            for article in re.findall(r"<article\b.*?</article>", outcomes, re.S):
                self.assertIn('class="what"', article)
                # The outcome and its benefit stay visible; only technical detail is collapsed.
                what = re.search(r'<p class="what">(.*?)</p>', article, re.S).group(1)
                self.assertGreaterEqual(len(re.findall(r"\.(?:\s|$)", re.sub(r"<[^>]+>", "", what))), 2)
                self.assertRegex(article, r"<details>\s*<summary>Technical detail</summary>")
                self.assertNotIn("<details open", article)
                self.assertNotIn("Why it mattered", article)
            # Order on the page: nav, header, byline, overview, outcomes, figures, proof.
            positions = [no_script.index(m) for m in (
                'class="report-nav"', "<h1>", 'class="byline"', 'id="overview"',
                'id="outcomes"', 'id="figures"', 'id="proof"')]
            self.assertEqual(positions, sorted(positions))

    def test_static_header_matches_the_report_init_payload(self):
        # The header exists twice: static HTML for readers without JS and the
        # Report.init payload that the shared renderer writes over it.
        for slug in self.data["reports"]:
            text = self._page(slug)
            static = text.split('data-section="header"', 1)[1].split("</div></div>", 1)[0]
            payload = text.split("Report.init({", 1)[1]
            title = re.search(r'title: "(.*?)",', payload).group(1)
            subtitle = re.search(r'subtitle: "(.*?)",', payload).group(1)
            date = re.search(r'date: "(.*?)",', payload).group(1)
            self.assertIn(f"<h1>{html.escape(title, quote=True)}</h1>", static)
            self.assertIn(f'<p class="subtitle">{html.escape(subtitle, quote=True)}</p>', static)
            self.assertIn(f"<span>{date}</span>", static)
            links = re.findall(r'\{ label: "(.*?)", href: "(.*?)" \}', payload)
            self.assertTrue(links)
            for label, href in links:
                self.assertIn(f'<a href="{href}">{html.escape(label, quote=True)}</a>', static)
            self.assertEqual(static.count("<a href="), len(links))

    def test_nothing_is_hidden_behind_fade_in(self):
        for slug in self.data["reports"]:
            text = self._page(slug)
            self.assertNotIn("fade-in", text)
            self.assertNotIn("opacity:0", text.replace(" ", ""))
            self.assertNotIn("display:none", text.replace(" ", ""))
        css = (ROOT / "reports/shared/october-own-20261106.css").read_text()
        self.assertRegex(css, r"\.fade-in\s*\{[^}]*opacity:\s*1")

    def test_overview_counts_match_the_source_record(self):
        for slug, rows in self.data["reports"].items():
            text = VisibleText(self._page(slug)).text
            counts = {int(n) for n in re.findall(r"(\d+) merged pull requests", text)}
            self.assertLessEqual(counts, {len(rows)}, slug)
        con = self.data["reports"]["conloca-2026-10"]
        self.assertEqual(len(con), self.data["inventory"]["credited"]["conloca/private"])
        self.assertIn(f"{len(con)} merged pull requests are counted", VisibleText(self._page("conloca-2026-10")).text)
        # The top byline stays short; the method and login caveats live in the proof section.
        for slug in self.data["reports"]:
            text = self._page(slug)
            self.assertRegex(text, r'<p class="byline">\s*Ultra / Mextner · October 2026\s*</p>')
            proof = text.split('id="proof"', 1)[1]
            self.assertIn("Work by Ultra / Mextner", proof)
            self.assertIn("credited by commit authorship", proof)

    def test_claims_are_scoped_not_absolute(self):
        banned = [
            "cannot leak", "not lost", "no longer sign the user out", "never signs",
            "has not moved to its own repository", "not published to npm",
            "hosted ci could not start", "private pilot", "universal",
            "why it mattered", "0 high or critical",
        ]
        for slug in self.data["reports"]:
            text = VisibleText(self._page(slug)).text
            raw = " ".join(self._page(slug).split()).casefold()
            for phrase in banned:
                self.assertNotIn(phrase, text, (slug, phrase))
                self.assertNotIn(phrase, raw, (slug, phrase))
            # Source-length padding does not demonstrate quality.
            self.assertNotRegex(raw, r"\d+ lines")
            self.assertNotRegex(raw, r"\(\d+ lines\)")

    def test_headline_metrics_name_their_fixture_and_scope(self):
        text = VisibleText(self._page("state-lib-2026-10")).text
        self.assertIn("historical local measurements", text)
        self.assertIn("benchmark fixture", text)
        self.assertIn("not claims about the whole editor", text)
        for tile in re.findall(r'class="summary-card">\s*<div class="value">(.*?)</div>\s*<div class="label">(.*?)</div>', self._page("state-lib-2026-10")):
            self.assertRegex(tile[1], r"fixture|rows|\(#\d+\)")
        # The editor on main does not use state-lib, and the page says so.
        self.assertIn("does not import state-lib on main", text)

    def test_outcome_references_are_bounded(self):
        for slug in self.data["reports"]:
            outcomes = self._page(slug).split('id="outcomes"', 1)[1].split("</section>", 1)[0]
            for article in re.findall(r"<article\b.*?</article>", outcomes, re.S):
                refs = re.search(r'<p class="refs">(.*?)</p>', article, re.S).group(1)
                self.assertLessEqual(len(re.findall(r"/pull/\d+", refs)), 8)

    def test_styles_are_versioned_so_phone_tabs_refresh(self):
        # A new file name: a phone tab that cached the old stylesheet URL cannot reuse it.
        # The old file stays unchanged so already-cached old HTML keeps its styles.
        for slug in self.data["reports"]:
            self.assertIn("../shared/october-own-20261106.css", self._page(slug))
            self.assertNotIn("october-own.css", self._page(slug))
        self.assertTrue((ROOT / "reports/shared/october-own.css").is_file())
        self.assertIn(".report-nav", (ROOT / "reports/shared/october-own.css").read_text())

    def _articles(self, slug):
        pattern = r'<article class="outcome"[^>]*data-claim="([^"]+)"[^>]*>(.*?)</article>'
        return re.findall(pattern, self._page(slug), re.S)

    def _assert_claim_evidence(self, claim, checks, credited):
        cid = claim["id"]
        self.assertTrue(claim["evidence"], cid)
        for e in claim["evidence"]:
            if e["type"] == "ci-run":
                receipt = checks[e["run"]]
                self.assertEqual(receipt["conclusion"], "success")
                # A receipt must belong to one of the claim's own pull requests.
                self.assertIn(receipt.get("pr"), claim["prs"], (cid, e["run"]))
            else:
                self.assertIn(e["type"], ("pr-body", "tests", "diff"))
                self.assertIn(e["pr"], credited, cid)

    def _assert_article_matches_claim(self, claim, body, credited):
        refs = re.search(r'<p class="refs">(.*?)</p>', body, re.S).group(1)
        linked = {int(m) for m in re.findall(r"/pull/(\d+)\"", refs)}
        self.assertEqual(linked, set(claim["prs"]), claim["id"])
        self.assertTrue(set(claim["prs"]) <= set(claim["coveredPrs"]), claim["id"])
        self.assertTrue(set(claim["coveredPrs"]) <= credited, claim["id"])

    def test_every_substantive_claim_has_attributable_evidence(self):
        claims = {c["id"]: c for c in self.data["claims"]}
        checks = {c["run"]: c for c in self.data["checks"]}
        for slug, rows in self.data["reports"].items():
            credited = {r["pr"] for r in rows}
            articles = self._articles(slug)
            self.assertTrue(articles, slug)
            claimed = set()
            for cid, body in articles:
                claim = claims[cid]
                self.assertEqual(claim["report"], slug)
                self._assert_article_matches_claim(claim, body, credited)
                self._assert_claim_evidence(claim, checks, credited)
                claimed |= set(claim["coveredPrs"])
            self.assertEqual(claimed, credited, slug)

    def test_every_credited_pr_belongs_to_a_claim_group(self):
        by_group = {(c["report"], c["group"]): c for c in self.data["claims"]}
        for slug, rows in self.data["reports"].items():
            for row in rows:
                self.assertIn(row["pr"], by_group[(slug, row["group"])]["coveredPrs"])

    def test_before_after_figures_have_provenance(self):
        figures = self.data["figures"]
        for slug, rows in self.data["reports"].items():
            credited = {r["pr"] for r in rows}
            mine = [f for f in figures if f["report"] == slug]
            self.assertGreaterEqual(len(mine), 3, slug)
            table = self._page(slug).split('class="figures"', 1)[1].split("</table>", 1)[0]
            self.assertEqual(table.count("<tr>") - 1, len(mine), slug)
            for f in mine:
                self.assertIn(f["pr"], credited)
                self.assertTrue(f["before"] and f["after"] and f["before"] != f["after"])
                self.assertTrue(f["source"])
                for part in (f["measure"], f["before"], f["after"]):
                    self.assertIn(html.escape(part, quote=True), table)

    def test_check_receipts_match_the_pull_request_heads(self):
        for c in self.data["checks"]:
            if "pr" in c and "prHead" in c:
                self.assertIn(c["head_sha"], (c["prHead"], c["mergeSha"]))
                self.assertEqual(c["conclusion"], "success")

    def test_images_are_real_decodable_and_in_the_source_record(self):
        recorded = {r["path"] for r in self.data["images"]}
        for slug in self.data["reports"]:
            text = self._page(slug)
            media = json.loads((ROOT / "reports" / slug / "media-evidence.json").read_text(encoding="utf-8"))
            crops = {f"reports/{slug}/{i['path']}": i for i in media["images"]}
            for tag in re.findall(r"<img\b[^>]*>", text):
                src = re.search(r'src="([^"]+)"', tag).group(1)
                self.assertIn(f"reports/{slug}/{src}", crops, src)
                self.assertRegex(tag, r'alt="[^"]{12,}"')
                self.assertRegex(tag, r'width="\d+"')
                self.assertRegex(tag, r'height="\d+"')
                data = (ROOT / "reports" / slug / src).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertGreater(len(data), 5000)
            # Every screenshot is a crop in the media record and links to its hashed full-size original.
            links = re.findall(r'<a class="shot-link" href="([^"]+)"[^>]*>\s*<img src="([^"]+)"', text)
            self.assertEqual(len(links), len(re.findall(r"<img\b", text)), slug)
            for href, src in links:
                orig = crops[f"reports/{slug}/{src}"]["original"]
                self.assertEqual(href, orig["path"])
                if href.startswith("images/"):
                    self.assertIn(f"reports/{slug}/{href}", recorded, "an earlier original must stay in the source record")
                self.assertEqual(hashlib.sha256((ROOT / "reports" / slug / href).read_bytes()).hexdigest(), orig["sha256"])

    def test_figures_and_screenshots_are_not_report_screenshots(self):
        for row in self.data["images"]:
            self.assertNotIn("hyperide.github.io", row.get("sourceUrl", ""))

    def test_visible_attribution_names_the_alias_account(self):
        for slug in ("state-lib-2026-10", "conloca-2026-10"):
            text = self._page(slug)
            self.assertIn("smoothbricker", text)
            for row in self.data["reports"][slug]:
                if row["author"] == "smoothbricker":
                    self.assertIn(f"#{row['pr']}", text.split('Attribution:', 1)[1].split("</p>", 1)[0])

    def test_conloca_leads_with_product_results_not_counts_or_infrastructure(self):
        page = self._page("conloca-2026-10")
        con = VisibleText(page).text
        overview = VisibleText(page.split('id="overview"', 1)[1].split("</section>", 1)[0]).text
        self.assertGreater(len(overview), 200)
        self.assertNotIn("merged pull requests", overview)
        self.assertNotIn("160 files", con)
        for phrase in ("dependency review", "dompurify", "devenv", "pre-push", "anti-slop", "mirror sync", "hosted jobs"):
            self.assertNotIn(phrase, con, phrase)
        self.assertIn("typed project validation", con)
        measures = [f["measure"] for f in self.data["figures"] if f["report"] == "conloca-2026-10"]
        self.assertFalse([m for m in measures if "Anti-slop" in m or "test time" in m])

    def test_opener_or_squash_author_does_not_override_commit_ownership(self):
        # Credit follows the original pull request commits (complete pagination), not who opened
        # the pull request or whose name the squash commit carries.
        hyper_rows = [r for r in self._all_rows() if r["repo"] == "hyperide/hyper-saas"]
        for r in hyper_rows:
            if r["author"] != "alex-mextner":
                proof = r["commitProof"]
                self.assertRegex(proof["firstCommit"], r"^[0-9a-f]{40}$")
                self.assertEqual(proof["firstCommitLogin"], "alex-mextner")
                self.assertEqual(proof["allCommitLogins"], ["alex-mextner"])
                self.assertEqual(proof["commits"], sum(r["commitAuthors"].values()))
                self.assertEqual(r["attribution"], "commit-author")
        by_pr = {r["pr"]: r for r in hyper_rows}
        for pr in (802, 809, 833):
            self.assertEqual(by_pr[pr]["author"], "smoothbricker")
            self.assertEqual(set(by_pr[pr]["commitAuthors"]), {"alex-mextner"})
        # Pull requests whose implementation is not ours stay out; mixed #792 is not claimed whole.
        self.assertFalse({789, 792} & set(by_pr))

    def test_state_lib_credits_the_keyed_row_boundary_by_original_commit(self):
        state = VisibleText(self._page("state-lib-2026-10")).text
        self.assertIn("keyed-row patch boundary", state)
        self.assertIn("#802, #809 and #833 were opened from the smoothbricker account", state)
        self.assertIn("/pull/833", self._page("state-lib-2026-10"))

    def test_published_claims_stay_inside_proof(self):
        # No publication claims without matching proof in the record.
        for slug in ("state-lib-2026-10", "conloca-2026-10"):
            text = VisibleText(self._page(slug)).text
            self.assertNotIn("published to npm", text.replace("is not published to npm", ""))


if __name__ == "__main__":
    unittest.main()
