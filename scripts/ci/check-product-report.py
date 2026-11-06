"""Contract for the HyperIDE product report (reports/hyper-2026-10, 6 August to 6 November 2026).

The page and reports/hyper-2026-10/evidence.json must agree: every spec quote, source link,
task identity, pull request state and credit statement on the page comes from the manifest,
and the manifest says which results are merged, which are implemented and checked on a cited
branch without being merged, and which are task-only. Gaps and spec coverage sit in a collapsed
appendix and are not presented as product results.

Run: python3 scripts/ci/check-product-report.py
"""

import hashlib
import html
import json
import re
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _report_html import Links, VisibleText, strip  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SLUG = "hyper-2026-10"
PAGE = ROOT / "reports" / SLUG / "index.html"
MANIFEST = ROOT / "reports" / SLUG / "evidence.json"
PERIOD = "6 August to 6 November 2026"
REPO = "https://github.com/hyperide/hyper-saas"
EXCLUDED_PRS = {789, 792}  # #789 is another contributor's work, #792 is mixed: neither is claimed
# The earlier stylesheets stay byte-identical so pages cached with them keep their layout.
# To change layout, add a new versioned file; do not edit these two. If one of them must change
# for a reason outside this report, recompute: sha256sum reports/shared/october-own*.css
OLD_CSS_SHA256 = {
    "reports/shared/october-own.css": "fd6f0d1e37cb770c52a39821a9942324d449051b051ca897ef9f023e444710f7",
    "reports/shared/october-own-20261106.css": "082465b0256a5c23982fb1bc27702bea4042fcbc394aed74ff84dd013edd32fa",
}
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
STATE_CLASS = {"todo": "todo", "in-progress": "progress", "in-review": "review", "done": "done"}
CSS_PATH = ROOT / "reports/shared/product-report-20261106.css"
# Topics the owner rejected for this report. Each phrase is checked against the visible text only.
REJECTED_TOPICS = (
    "protected storybook", "ready for approval", "catalogue snapshots", "publisher deployed-stage e2e", "automatic link refresh",
    "storybook",  # separate owner and not part of this report
    "vs code marketplace", "open vsx", "extension 0.1.7", "published to",  # release publication is not a product result
    "credential", "git push",  # credential handling
    "dependency audit", "npm audit", "bun audit",  # dependency audits
    "pre-commit", "pre-push", "shell hook",  # hooks
    "review infrastructure", "release notes",
)
# The report states results and limits, not the history of attempts or operational blockers.
REJECTED_NARRATIVE = ("failed attempt", "attempt failed", "we tried", "did not work", "was reverted", "abandoned", "blocked by", "quota",
                      "refused by the reviewer", "could not start")


def plain_quote(text):
    return " ".join(text.replace("**", "").replace("`", "").split())


class ProductReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = PAGE.read_text(encoding="utf-8")
        cls.data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.urls = Links(cls.page).urls
        cls.no_script = re.sub(r"<script\b.*?</script>", "", cls.page, flags=re.S)
        cls.text = VisibleText(cls.page).raw
        cls.articles = {
            m.group(1): m.group(0)
            for m in re.finditer(r'<article class="outcome[^"]*" id="([^"]+)".*?</article>', cls.no_script, re.S)
        }

    # ------------------------------------------------------------ period, header, first screen
    def test_url_preserved_and_period_is_explicit_in_title_header_date(self):
        self.assertTrue(PAGE.is_file())
        self.assertIn(f"<title>HyperIDE product report: {PERIOD}</title>", self.page)
        self.assertEqual(re.search(r"<h1>(.+?)</h1>", self.no_script).group(1), f"HyperIDE product report: {PERIOD}")
        self.assertIn(f"<span>{PERIOD}</span>", self.no_script)
        self.assertIn(f'date: "{PERIOD}"', self.page)
        self.assertIn(f'<p class="byline">Ultra / Mextner · {PERIOD}</p>', self.no_script)
        self.assertEqual(self.data["period"], {"from": "2026-08-06", "to": "2026-11-06"})
        self.assertNotIn("October", re.search(r"<title>(.*?)</title>", self.page).group(1))
        self.assertNotIn("October", re.search(r"<h1>(.*?)</h1>", self.no_script).group(1))

    def test_static_header_matches_the_report_init_payload(self):
        static = self.page.split('data-section="header"', 1)[1].split("</div></div>", 1)[0]
        payload = self.page.split("Report.init({", 1)[1]
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

    def test_first_screen_is_static_and_ordered(self):
        overview = self.no_script.split('id="overview"', 1)[1].split("</section>", 1)[0]
        self.assertGreaterEqual(overview.count('class="summary-card"'), 3)
        self.assertGreaterEqual(len(re.findall(r"<p>", overview)), 4)
        self.assertGreater(len(strip(overview)), 1200)
        # The first screen names the corrected product behavior, not the report's own metadata.
        text = strip(overview)
        for needle in ("refused and rolled back with a warning", "lands as an inline style", "A second Fill-color pick replaces the color",
                       "real pixel size", "real source file", "Open Folder", "Implemented and checked on their own branches, not merged",
                       "not declared complete"):
            self.assertIn(needle, text)
        marks = ['class="report-nav"', "<h1>", 'class="byline"', 'id="overview"', 'id="architecture"', 'id="outcomes"',
                 'id="style-editor"', 'id="task-only"', 'id="canvas-contract"', 'id="state"', 'id="figures"', 'id="coverage"',
                 'id="other-specs"', 'id="proof"']
        positions = [self.no_script.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))

    def test_nothing_is_hidden_and_details_start_closed(self):
        self.assertNotIn("fade-in", self.page)
        self.assertNotIn("display:none", self.page.replace(" ", ""))
        self.assertNotIn("<details open", self.page)
        for oid, art in self.articles.items():
            self.assertRegex(art, r"<details class=\"evidence\"><summary>Evidence: ", oid)

    # ------------------------------------------------------------ outcomes agree with the manifest
    def test_every_manifest_outcome_has_one_article_with_its_text(self):
        ids = [o["id"] for o in self.data["outcomes"]]
        self.assertEqual(sorted(ids), sorted(self.articles))
        self.assertEqual(len(ids), len(set(ids)))
        for o in self.data["outcomes"]:
            art = self.articles[o["id"]]
            self.assertIn(f'data-status="{o["status"]}"', art)
            self.assertEqual(strip(re.search(r'<span class="status-label">(.*?)</span>', art).group(1)), o["statusLabel"])
            self.assertEqual(strip(re.search(r"<h3>(.*?)</h3>", art).group(1)), o["title"])
            paragraphs = re.findall(r'<p class="what"><strong>(Before|Now)\.</strong> (.*?)</p>', art, re.S)
            self.assertEqual([p[0] for p in paragraphs], ["Before", "Now"], o["id"])
            self.assertEqual(strip(paragraphs[0][1]), " ".join(o["before"].split()), o["id"])
            self.assertEqual(strip(paragraphs[1][1]), " ".join(o["now"].split()), o["id"])
            self.assertGreaterEqual(len(re.findall(r"\.(?:\s|$)", o["before"])), 2, o["id"])
            self.assertGreaterEqual(len(re.findall(r"\.(?:\s|$)", o["now"])), 2, o["id"])
            self.assertIn(o["scope"].replace("`", ""), strip(art), o["id"])

    def test_each_outcome_shows_its_primary_requirement_outside_the_collapsed_block(self):
        for o in self.data["outcomes"]:
            art = self.articles[o["id"]]
            visible = art.split('<details class="evidence">', 1)[0]
            if o.get("requirementTask"):
                self.assertIn('class="requirement task-requirement"', visible, o["id"])
                self.assertIn(f">{o['requirementTask']}</a>", visible, o["id"])
                self.assertIn("No spec paragraph is claimed", visible, o["id"])
                self.assertEqual(o["quotes"], [], o["id"])
            else:
                primary = [q for q in o["quotes"] if q["primary"]]
                self.assertEqual(len(primary), 1, o["id"])
                self.assertEqual(len(re.findall(r"<blockquote\b", visible)), 1, o["id"])
                self.assertIn(plain_quote(primary[0]["text"]), strip(visible), o["id"])

    def test_every_outcome_names_tasks_and_pull_requests_with_status(self):
        for o in self.data["outcomes"]:
            art = self.articles[o["id"]]
            self.assertTrue(o["tasks"], o["id"])
            self.assertTrue(o["verification"], o["id"])
            self.assertTrue(o["caveats"], o["id"])
            self.assertIn("<h4>Verification</h4>", art)
            self.assertIn("<h4>Caveats</h4>", art)
            for p in o["pullRequests"]:
                self.assertIn(f"{REPO}/pull/{p['number']}", art, o["id"])
                if p["state"] == "merged":
                    self.assertIn(f"as <a href=\"{REPO}/commit/{p['mergeSha']}\"><code>{p['mergeSha'][:8]}</code></a>", art)
                else:
                    self.assertIn("open", strip(art), o["id"])
                    self.assertIn("not merged", strip(art), o["id"])

    # ------------------------------------------------------------ quotes: exact revision, lines, language
    def _all_quotes(self):
        for o in self.data["outcomes"]:
            for q in o["quotes"]:
                yield o, q

    def test_quotes_carry_spec_file_revision_section_lines_and_exact_links(self):
        n = 0
        for o, q in self._all_quotes():
            n += 1
            self.assertRegex(q["revision"], r"^[0-9a-f]{40}$")
            a, b = q["lines"]
            self.assertLessEqual(a, b)
            frag = f"#L{a}" if a == b else f"#L{a}-L{b}"
            self.assertEqual(q["link"], f"{REPO}/blob/{q['revision']}/{q['file']}{frag}")
            self.assertTrue(q["section"] and q["text"] and q["role"])
            self.assertTrue(q["file"].startswith(("docs/", "packages/state-lib/docs/")), q["file"])
            self.assertTrue(q["file"].endswith(".md"))
            art = self.articles[o["id"]]
            self.assertIn(f'href="{q["link"]}">{q["revision"][:8]}, ', art, (o["id"], q["link"]))
            self.assertIn(f"<code>{q['file']}</code>", art)
            self.assertIn(html.escape(q["section"], quote=True), art, (o["id"], q["section"]))
            shown = f"line {a}" if a == b else f"lines {a}-{b}"
            self.assertIn(f">{q['revision'][:8]}, {shown}</a>", art, o["id"])
            self.assertIn(f'lang="{q["language"]}">', art)
            self.assertIn(plain_quote(q["text"]), strip(art), (o["id"], q["text"][:40]))
        self.assertEqual(n, self.data["verification"]["quotes"]["count"])

    def test_quote_verification_is_recorded_for_every_quote(self):
        v = self.data["verification"]
        total = sum(len(o["quotes"]) for o in self.data["outcomes"])
        self.assertTrue(v["quotes"]["allFound"])
        self.assertEqual(v["quotes"]["count"], total)
        self.assertEqual(v["quotes"]["checkedAt"], "2026-11-06")
        self.assertFalse(v["testsRerun"])
        self.assertEqual(self.data["mainRevision"], "5c26b9c68060e520e555774d37141b11d2aa12aa")
        self.assertEqual(self.data["masterSpecOnMain"], "Rev 0.3.8")

    def test_russian_quotes_keep_the_original_and_a_labelled_translation(self):
        ru = [(o, q) for o, q in self._all_quotes() if q["language"] == "ru"]
        self.assertGreaterEqual(len(ru), 6)
        for o, q in ru:
            self.assertRegex(q["text"], CYRILLIC)
            self.assertTrue(q.get("translation"))
            self.assertNotRegex(q["translation"], CYRILLIC)
            art = self.articles[o["id"]]
            block = re.search(
                r'<blockquote lang="ru">' + re.escape(html.escape(q["text"], quote=True)).replace("&#x27;", "(?:&#x27;|')") + r"</blockquote>"
                r'<p class="translation"><span class="tag">Translation, not source text</span> (.*?)</p>', art, re.S)
            self.assertIsNotNone(block, (o["id"], q["text"][:30]))
            self.assertEqual(strip(block.group(1)), plain_quote(q["translation"]))
        for o, q in self._all_quotes():
            if q["language"] == "en":
                self.assertNotRegex(q["text"], CYRILLIC)
                self.assertNotIn("translation", q)
        # Every translation paragraph on the page belongs to a Russian quote, and each is labelled.
        translations = re.findall(r'<p class="translation">(.*?)</p>', self.no_script, re.S)
        self.assertTrue(all(t.startswith('<span class="tag">Translation, not source text</span>') for t in translations))
        self.assertEqual(len(re.findall(r'<blockquote lang="ru">', self.no_script)), len(translations))
        # The translation never sits inside the quoted source text.
        for m in re.finditer(r"<blockquote lang=\"ru\">(.*?)</blockquote>", self.no_script, re.S):
            self.assertNotIn("Translation", m.group(1))

    def test_russian_task_title_is_marked_and_translated(self):
        ub = [t for o in self.data["outcomes"] for t in o["tasks"] if t["key"] == "UB-113"]
        self.assertTrue(ub)
        for t in ub:
            self.assertRegex(t["title"], CYRILLIC)
        self.assertIn('<cite class="source-title" lang="ru">', self.page)
        self.assertIn("Translation, not source text: “state-lib: shared mechanism for drafts, saving and protection against late responses”", self.text)

    # ------------------------------------------------------------ tasks
    def test_tasks_have_verified_identity_and_links(self):
        seen = set()
        for o in self.data["outcomes"]:
            art = self.articles[o["id"]]
            for t in o["tasks"]:
                self.assertRegex(t["key"], r"^(HYP|UB)-\d+$")
                self.assertRegex(t["uuid"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
                self.assertEqual(t["url"], f"https://tasks.glide.vc/issues/{t['uuid']}")
                self.assertIn(t["state"], ("todo", "in-progress", "in-review", "done"))
                self.assertTrue(t["title"].strip())
                self.assertIn(f'<a href="{t["url"]}">{t["key"]}</a>: <cite class="source-title"', art)
                self.assertIn(f"UUID <code>{t['uuid']}</code>", art)
                self.assertIn(f'<span class="state state-{STATE_CLASS[t["state"]]}">{t["state"]}</span>', art)
                seen.add((t["key"], t["uuid"], t["state"], t["title"]))
        keys = {}
        for key, uuid, state, title in seen:
            keys.setdefault(key, set()).add((uuid, state, title))
        self.assertTrue(all(len(v) == 1 for v in keys.values()), "one task identity per key")
        self.assertNotIn("HYP-1507", {k for k in keys})

    def test_open_or_unresolved_tasks_are_labelled_as_such(self):
        tasks = {t["key"]: t for o in self.data["outcomes"] for t in o["tasks"]}
        self.assertEqual(tasks["HYP-1588"]["state"], "todo")
        self.assertEqual(tasks["HYP-1536"]["state"], "in-progress")
        self.assertEqual(tasks["HYP-1574"]["state"], "todo")
        for key in ("HYP-1232", "HYP-1309", "HYP-1735"):
            self.assertEqual(tasks[key]["state"], "todo")
        self.assertIn("no implementing commit exists", self.text)
        self.assertIn("HYP-1507 used in the pull request bodies no longer resolves in the tracker", self.text)

    # ------------------------------------------------------------ merged versus awaiting approval
    def _assert_merged_pull_requests_are_in_the_period(self, o):
        start, end = self.data["period"]["from"], self.data["period"]["to"]
        for p in o["pullRequests"]:
            if p["state"] == "merged":
                self.assertRegex(p["mergeSha"], r"^[0-9a-f]{40}$")
                self.assertTrue(start <= p["mergedAt"][:10] <= end, (o["id"], p["number"]))

    def _assert_pull_request_state(self, o, expected):
        prs = o["pullRequests"]
        if expected == "none":
            self.assertEqual(prs, [], o["id"])
        elif expected == "some merged":
            self.assertTrue(any(p["state"] == "merged" for p in prs), o["id"])
        else:
            self.assertTrue(prs, o["id"])
            self.assertEqual({p["state"] for p in prs}, {expected}, o["id"])

    def _assert_label_phrases(self, o, must, must_not):
        label = o["statusLabel"].lower()
        for phrase in must:
            self.assertIn(phrase, label, o["id"])
        for phrase in must_not:
            self.assertNotIn(phrase, label, o["id"])

    def test_status_kinds_are_separated_by_real_pull_request_state(self):
        # status -> (state every pull request must have, label phrases that must / must not appear)
        rules = {
            "implemented-branch": ("open", ("implemented and checked on the cited branch", "not merged"), ("merged to main", "awaiting")),
            "pilot": ("open", ("open pull request",), ("merged to main",)),
            "task-only": ("merged", ("task-only", "no spec claim"), ()),
            "merged-library": ("merged", ("not used by the shipped editor",), ()),
            "info": ("none", (), ()),
            "open-gap": ("none", (), ()),
            "merged": ("some merged", ("merged",), ()),
        }
        for o in self.data["outcomes"]:
            expected, must, must_not = rules[o["status"]]
            self._assert_merged_pull_requests_are_in_the_period(o)
            self._assert_pull_request_state(o, expected)
            self._assert_label_phrases(o, must, must_not)
        merged = [o for o in self.data["outcomes"] if o["status"] == "merged"]
        self.assertTrue(all(o["statusLabel"].lower().startswith("merged") for o in merged))

    def test_status_legend_covers_every_status_used(self):
        self.assertLessEqual({o["status"] for o in self.data["outcomes"]}, set(self.data["statusKinds"]))

    def test_first_screen_tiles_name_corrected_product_behavior_not_counters(self):
        tiles = self.data["tiles"]
        self.assertGreaterEqual(len(tiles), 3)
        for t in tiles:
            self.assertIn(f'<div class="value">{html.escape(t["value"])}</div><div class="label">{html.escape(t["label"])}</div>', self.no_script)
            self.assertNotRegex(t["value"], r"\d|Rev|speedup|awaiting")
            self.assertRegex(t["label"], r"\(#\d+")
        self.assertNotIn("0 speedups", self.text)

    def test_canvas_contract_stack_is_labelled_not_merged_everywhere_it_is_named(self):
        canvas = [o for o in self.data["outcomes"] if o["family"] == "canvas-contract"]
        self.assertEqual([o["id"] for o in canvas], self.data["families"]["canvas-contract"])
        self.assertTrue(canvas)
        for o in canvas:
            self.assertEqual(o["status"], "implemented-branch")
            self.assertTrue(o["boundary"])
            self.assertIn(f"<strong>Boundary.</strong>", self.articles[o["id"]])
            for p in o["pullRequests"]:
                self.assertEqual(p["state"], "open")
        intro = self.no_script.split('id="canvas-contract"', 1)[1].split("</section>", 1)[0]
        for needle in ("is not on main", "None is merged", "commit authorship", "reviewable proposal pending the affected Part owner's re-sign"):
            self.assertIn(needle, html.unescape(intro).replace("&#x27;", "'"))
        c2 = next(o for o in canvas if o["id"] == "c2-verified-move")
        self.assertEqual([p["reviewDecision"] for p in c2["pullRequests"]], ["CHANGES_REQUESTED"])
        self.assertIn("Review changes were requested on an earlier head", strip(self.articles["c2-verified-move"]))
        self.assertIn("conflicts with its base", strip(self.articles["c2-verified-move"]))

    def test_state_library_claims_stay_honest(self):
        by_id = {o["id"]: o for o in self.data["outcomes"]}
        self.assertEqual(by_id["s1-operation-authority-in-style-editor"]["status"], "implemented-branch")
        self.assertEqual(by_id["s2-measured-pilot-no-speedup"]["status"], "pilot")
        for oid in ("s1-operation-authority-in-style-editor", "s2-measured-pilot-no-speedup"):
            self.assertTrue(all(p["state"] == "open" for p in by_id[oid]["pullRequests"]))
        s1, s2, s3 = (strip(self.articles[k]) for k in ("s1-operation-authority-in-style-editor", "s2-measured-pilot-no-speedup", "s3-state-library-merged-not-adopted"))
        self.assertIn("no latency, memory, code-size or adoption win is claimed, so there is no shipped speedup", s1)
        self.assertIn("conflicts with main", s1)
        self.assertIn("equally localized native version reaches 831 ms", s2)
        self.assertIn("No performance gain is shipped", s2)
        self.assertIn("in a controlled fixture", s2)
        self.assertIn("none is shown in this report as the editor", s2)
        self.assertIn("imports state-lib", s3)
        self.assertIn("HYPER_STATE_SPIKE=1", s3)
        self.assertIn("The operation scope has no user-visible effect until #827 merges", s3)
        for banned in ("faster typing", "improved performance", "performance win", "is faster"):
            self.assertNotIn(banned, self.text.casefold())
        self.assertIn("on main no client, server, shared or extension file imports it", self.text)
        # Authorship of the state spec is not described through the squash author.
        self.assertNotIn("first added by a colleague", self.text)

    def test_retro_is_described_as_tooling_and_w2_w3_stay_open(self):
        by_id = {o["id"]: o for o in self.data["outcomes"]}
        self.assertEqual(by_id["r1-retro-identity"]["status"], "info")
        self.assertEqual(by_id["r2-canvas-preview-w2-w3"]["status"], "open-gap")
        r2 = strip(self.articles["r2-canvas-preview-w2-w3"])
        for needle in ("Not delivered in this window", "still `pathname", "HYP-1574", "W-2", "W-3"):
            self.assertIn(needle.replace("`", ""), r2)
        self.assertIn("agent tooling", strip(self.articles["r1-retro-identity"]))
        self.assertIn("this report does not count that tooling work as product results", strip(self.articles["r1-retro-identity"]))

    def test_master_spec_is_not_declared_complete(self):
        proof = strip(self.no_script.split('id="proof"', 1)[1])
        self.assertIn("The Master Styles spec is not declared complete", proof)
        self.assertIn("Open pull requests are labelled as not merged", proof)
        self.assertIn("not declared complete", self.text)
        self.assertIn("Rev 0.3.8", self.text)
        self.assertEqual(len(self.data["masterSpecNotMerged"]), 2)

    # ------------------------------------------------------------ results versus coverage appendix
    def test_main_results_hold_only_completed_outcomes(self):
        main_html, appendix_html = self.no_script.split('id="coverage"', 1)
        appendix_html = appendix_html.split('id="proof"', 1)[0]
        main_ids = set(re.findall(r'<article class="outcome[^"]*" id="([^"]+)"', main_html))
        appendix_ids = set(re.findall(r'<article class="outcome[^"]*" id="([^"]+)"', appendix_html))
        by_id = {o["id"]: o for o in self.data["outcomes"]}
        self.assertEqual(main_ids | appendix_ids, set(by_id))
        self.assertFalse(main_ids & appendix_ids)
        self.assertEqual(appendix_ids, set(self.data["families"]["coverage"]))
        for oid in main_ids:
            self.assertIn(by_id[oid]["status"], ("merged", "task-only", "implemented-branch"), oid)
            self.assertTrue(by_id[oid]["pullRequests"], oid)
        # Gaps, measurements without a win and tooling documents are coverage notes, never results.
        for oid in ("r1-retro-identity", "r2-canvas-preview-w2-w3", "s2-measured-pilot-no-speedup", "s3-state-library-merged-not-adopted", "a6-spec-status-corrections"):
            self.assertIn(oid, appendix_ids)
        main_text = strip(main_html)
        for phrase in ("still open", "no proven speedup", "not started", "Not delivered", "Open task-only work"):
            self.assertNotIn(phrase, main_text)

    def test_the_coverage_appendix_is_one_collapsed_block_with_exact_links(self):
        appendix = self.no_script.split('<section class="report-section" id="coverage">', 1)[1].split("</section>", 1)[0]
        self.assertTrue(appendix.lstrip().startswith("<h2>Specification coverage and boundaries</h2>"))
        self.assertEqual(appendix.count('<details class="appendix">'), 1)
        self.assertIn("<summary>Show how each spec relates to the work</summary>", appendix)
        self.assertIn('id="other-specs"', appendix)
        for group in ("master-spec", "retro", "state"):
            self.assertIn(f'id="coverage-{group}"', appendix)
        self.assertIn("Open task-only work", appendix)
        for o in self.data["outcomes"]:
            if o["family"] == "coverage":
                for q in o["quotes"]:
                    self.assertIn(q["link"], appendix)

    def test_incomplete_branches_are_never_called_ready(self):
        for phrase in ("awaiting team approval", "Ready, awaiting approval", "ready for approval"):
            self.assertNotIn(phrase, self.page)
        for o in self.data["outcomes"]:
            if o["status"] == "implemented-branch":
                self.assertEqual({p["state"] for p in o["pullRequests"]}, {"open"})
                self.assertIn("Implemented and checked on the cited branch", o["statusLabel"])
        # Proof that is only an author report is called recorded proof, not a new run.
        for o in self.data["outcomes"]:
            for bullet in o["verification"]:
                if re.search(r"Author[- ]report", bullet):
                    self.fail((o["id"], bullet))

    # ------------------------------------------------------------ task-only labels
    def test_task_only_results_are_labelled_and_have_no_spec_claim(self):
        task_only = [o for o in self.data["outcomes"] if o["family"] == "task-only"]
        self.assertEqual(len(task_only), 3)
        for o in task_only:
            self.assertEqual(o["status"], "task-only")
            self.assertEqual(o["quotes"], [])
            self.assertIn(o["requirementTask"], [t["key"] for t in o["tasks"]])
            self.assertIn("no spec claim", o["statusLabel"].lower())
        self.assertEqual(len(self.data["taskOnly"]), 10)
        table = self.no_script.split('class="figures report-table taskonly-table"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("<tr>") - 1, len(self.data["taskOnly"]))
        for row in self.data["taskOnly"]:
            for p in row["pullRequests"]:
                self.assertEqual(p["state"], "merged")
                self.assertIn(f"{REPO}/pull/{p['number']}", table)
            for t in row["tasks"]:
                self.assertIn(f'href="{t["url"]}">{t["key"]}</a>', table)
        for item in self.data["openTaskOnly"]:
            self.assertEqual(item["pullRequest"]["state"], "open")
        self.assertIn("Task-only, and not counted as delivered", self.text)
        self.assertIn("The report claims no spec requirement for them", self.text)

    # ------------------------------------------------------------ authorship and scope
    def _assert_credit(self, o, p):
        credit = p["credit"]
        if credit["kind"] == "scoped":
            self.assertTrue(credit["commits"])
            for sha in credit["commits"]:
                self.assertRegex(sha, r"^[0-9a-f]{40}$")
                self.assertIn(f"{REPO}/commit/{sha}", self.articles[o["id"]])
        else:
            self.assertEqual(credit["kind"], "whole")
            self.assertIsInstance(credit["commits"], int)
            self.assertGreaterEqual(credit["commits"], 1)
        if p["openedFromAnotherAccount"]:
            self.assertEqual(credit["kind"], "scoped", p["number"])

    def test_every_pull_request_has_a_whole_or_scoped_credit(self):
        for o in self.data["outcomes"]:
            for p in o["pullRequests"]:
                self._assert_credit(o, p)

    def test_other_contributors_work_is_not_claimed_anywhere(self):
        text = self.page + MANIFEST.read_text(encoding="utf-8")
        numbers = {p["number"] for o in self.data["outcomes"] for p in o["pullRequests"]}
        for row in self.data["taskOnly"]:
            numbers |= {p["number"] for p in row["pullRequests"]}
        self.assertFalse(numbers & EXCLUDED_PRS)
        for n in EXCLUDED_PRS:
            self.assertNotIn(f"/pull/{n}\"", text)
            self.assertNotIn(f"#{n}", self.text)
        for name in ("25c37539", "smoothbricker", "smoothbricks"):
            self.assertNotIn(name, text.casefold())

    def test_authorship_statements_and_narrow_credit_are_visible(self):
        proof = strip(self.no_script.split('id="proof"', 1)[1])
        self.assertIn("credited by commit authorship and not by who opened a pull request", proof)
        self.assertIn("only the listed commits are credited", proof)
        # Narrow credit on the mixed pull requests, as the canvas results state it.
        c5 = strip(self.articles["c5-edit-component-drag-undo"])
        self.assertIn("In #794 only commit fda9e4a9 is claimed", c5)
        self.assertIn("the Undo-guard idea is not claimed", c5)
        c4 = next(o for o in self.data["outcomes"] if o["id"] == "c4-move-scope-and-selection")
        p823 = next(p for p in c4["pullRequests"] if p["number"] == 823)
        self.assertEqual(len(p823["credit"]["commits"]), 3)

    def test_manifest_has_no_census_of_other_peoples_work(self):
        raw = MANIFEST.read_text(encoding="utf-8")
        accounts = set(re.findall(r'"(?:author|login|account|openedBy|commitAuthors)":\s*"([^"]+)"', raw))
        self.assertLessEqual(accounts, {"alex-mextner"})
        self.assertNotIn("commitAuthors", raw)

    # ------------------------------------------------------------ rejected topics and links
    def test_no_rejected_links_topics_or_failure_narrative(self):
        low = self.text.casefold()
        self.assertNotIn("pages.hyperide.ai", self.page.casefold())
        for phrase in REJECTED_TOPICS + REJECTED_NARRATIVE:
            self.assertNotIn(phrase, low, phrase)

    def test_all_links_stay_on_repository_task_tracker_or_the_site(self):
        external = r"^https://(github\.com/hyperide/hyper-saas/(pull/\d+|commit/[0-9a-f]{40}|blob/[0-9a-f]{40}/[^#\s]+(#L\d+(-L\d+)?)?)|tasks\.glide\.vc/issues/[0-9a-f-]{36})$"
        for url in self.urls:
            if url.startswith("#"):
                continue
            if url.startswith("https://"):
                self.assertRegex(url, external, url)
                continue
            # Relative links must resolve to a real file, or to a directory that holds an index.html.
            target = (PAGE.parent / url.split("#", 1)[0]).resolve()
            self.assertTrue(target.is_file() or (target / "index.html").is_file(), url)

    def test_pull_request_links_are_all_in_the_manifest(self):
        known = {p["number"] for o in self.data["outcomes"] for p in o["pullRequests"]}
        known |= {p["number"] for r in self.data["taskOnly"] for p in r["pullRequests"]}
        known |= {i["pullRequest"]["number"] for i in self.data["openTaskOnly"]}
        known |= {608, 609}  # spec-only documents listed under other specs
        for url in self.urls:
            m = re.search(r"/pull/(\d+)$", url)
            if m:
                self.assertIn(int(m.group(1)), known, url)

    def test_merge_dates_on_the_page_match_the_manifest(self):
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        merged = [(o["id"], p) for o in self.data["outcomes"] for p in o["pullRequests"] if p["state"] == "merged"]
        for oid, p in merged:
            y, mo, d = map(int, p["mergedAt"][:10].split("-"))
            self.assertIn(f"merged {d} {months[mo - 1]} {y} as", strip(self.articles[oid]), (oid, p["number"]))

    # ------------------------------------------------------------ architecture, other specs, tables
    def test_architecture_before_after_table_is_complete(self):
        table = self.no_script.split('id="architecture"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(len(re.findall(r'<th scope="col">', table)), 4)
        rows = re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[1], re.S)
        self.assertEqual(len(rows), 6)
        for row in rows:
            cells = re.findall(r'<td data-label="([^"]+)">(.*?)</td>', row, re.S)
            self.assertEqual([c[0] for c in cells], ["Layer, spec section", "Before", "On main now", "Candidate, not merged"])
            self.assertTrue(all(strip(c[1]) for c in cells))
        text = strip(table)
        for needle in ("§9.2a", "§8.1", "§5.2", "§3.8", "§14.6a", "Part 16"):
            self.assertIn(needle, text)

    def test_other_spec_table_covers_every_banner_with_exact_links(self):
        specs = self.data["otherSpecs"]
        self.assertEqual(len(specs), 10)
        table = self.no_script.split('class="figures report-table specs-table"', 1)[1].split("</table>", 1)[0]
        self.assertEqual(table.count("<tr>") - 1, 10)
        for s in specs:
            self.assertEqual(s["link"], f"{REPO}/blob/{s['revision']}/{s['file']}#L1")
            self.assertIn(f'href="{s["link"]}">{s["revision"][:8]}, line 1</a>', table)
            self.assertIn(plain_quote(s["banner"]), strip(table))
            self.assertRegex(s["banner"], r"SUPERSEDED|ARCHIVED|Retained for history|superseded by the master styles spec")
        self.assertEqual(table.count(">Archived<"), 3)
        self.assertEqual(table.count(">Superseded<"), 7)
        self.assertIn("No window commit found, so no delivery is claimed", strip(table))

    def test_tables_stack_on_phones_with_labels(self):
        for cls in ("arch-table", "specs-table", "taskonly-table"):
            table = self.no_script.split(f'class="figures report-table {cls}"', 1)[1].split("</table>", 1)[0]
            heads = re.findall(r'<th scope="col">(.*?)</th>', table)
            for row in re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[1], re.S):
                labels = re.findall(r'<td data-label="([^"]+)">', row)
                self.assertEqual([html.unescape(h) for h in heads], [html.unescape(l) for l in labels], cls)

    # ------------------------------------------------------------ images
    def test_images_are_real_decodable_distinct_pairs_in_the_manifest(self):
        recorded = {r["path"]: r for r in self.data["images"]}
        tags = re.findall(r"<img\b[^>]*>", self.no_script)
        self.assertEqual(len(tags), len(recorded))
        self.assertGreaterEqual(len(tags), 4)
        for tag in tags:
            src = re.search(r'src="([^"]+)"', tag).group(1)
            r = recorded[f"reports/{SLUG}/{src}"]
            data = (PAGE.parent / src).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(hashlib.sha256(data).hexdigest(), r["sha256"])
            self.assertEqual(struct.unpack(">II", data[16:24]), (r["width"], r["height"]))
            self.assertEqual(len(data), r["bytes"])
            self.assertGreater(len(data), 50000)
            self.assertRegex(tag, r'alt="[^"]{40,}"')
            self.assertIn(f'width="{r["width"]}"', tag)
            self.assertIn(f'height="{r["height"]}"', tag)
            self.assertRegex(r["sourceRevision"], r"^[0-9a-f]{40}$")
            self.assertEqual(r["kind"], "real-editor-screenshot")
            for fixture in ("scripts/style-state-pilot", "state-bench", "field-subscriptions", "feedback-capability", "/evidence/fields", "parent.png"):
                self.assertNotIn(fixture, r["sourcePath"])
            self.assertNotIn("hyperide.github.io", r["sourcePath"])
        crops = re.findall(r'<a class="crop [^"]+" href="([^"]+)">\s*<img src="([^"]+)"', self.no_script)
        self.assertEqual(len(crops), len(tags))
        self.assertTrue(all(h == s for h, s in crops))
        by_fig = {}
        for r in self.data["images"]:
            by_fig.setdefault(r["figure"], {})[r["side"]] = r
        self.assertEqual(len(by_fig), 2)
        for fig, sides in by_fig.items():
            self.assertEqual(set(sides), {"before", "after"})
            self.assertNotEqual(sides["before"]["sha256"], sides["after"]["sha256"], fig)
            self.assertEqual(sides["before"]["pr"], sides["after"]["pr"])

    def test_screenshots_are_labelled_with_their_status_and_limits(self):
        figs = self.no_script.split('id="figures"', 1)[1].split('id="proof"', 1)[0]
        pairs = re.findall(r'<div class="figure-pair kind-([\w-]+)" id="([^"]+)" data-pr="(\d+)">.*?<span class="status-label">(.*?)</span>', figs, re.S)
        self.assertEqual([(p[0], p[1], p[2]) for p in pairs], [("merged", "fig-explorer-empty-state", "721"), ("implemented-branch", "fig-inspector-width-write", "827")])
        self.assertIn("task-only", pairs[0][3])
        self.assertIn("not merged", pairs[1][3])
        self.assertIn("Implemented and checked on the cited branch", pairs[1][3])
        text = strip(figs)
        self.assertIn("It is not a speed result and does not test stale responses or rollback", text)
        self.assertIn("Benchmark fixture captures are not shown", text)
        self.assertIn("Only real before and after pairs", text)

    # ------------------------------------------------------------ styles and mobile flow
    def test_versioned_styles_leave_the_earlier_files_unchanged(self):
        for rel, digest in OLD_CSS_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), digest, rel)
        head = self.page.split("</head>", 1)[0]
        order = [head.index(f'href="../shared/{n}"') for n in ("styles.css", "october-own-20261106.css", "product-report-20261106.css")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("<style>", self.page)
        css = self.css
        self.assertIn("@media (max-width: 650px)", css)
        for needle in (".crop.crop-explorer", ".crop.crop-inspector", "figure.requirement", ".outcome .state-todo", ".kind-implemented-branch", ".report-table td"):
            self.assertIn(needle, css)
        self.assertNotIn("product-report", (ROOT / "reports/shared/october-own-20261106.css").read_text())

    def test_every_status_kind_used_has_a_style(self):
        css = self.css
        for kind in {o["status"] for o in self.data["outcomes"]}:
            self.assertIn(f".kind-{kind}", css, kind)

    def test_mobile_flow_markup_keeps_quotes_and_evidence_readable(self):
        # Long quotes and paths wrap; evidence lists are plain lists inside <details>, not wide tables.
        css = self.css
        self.assertIn("overflow-wrap: anywhere", css)
        for art in self.articles.values():
            evidence = art.split('<details class="evidence">', 1)[1]
            self.assertNotIn("<table", evidence)
        self.assertEqual(len(re.findall(r"<table\b", self.no_script)), 3)

    def test_all_reports_index_names_the_period(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        card = index.split('<a href="reports/hyper-2026-10/">', 1)[1].split("</a>", 1)[0]
        self.assertIn(f"HyperIDE product report: {PERIOD}", card)
        self.assertIn(f"<span>{PERIOD}</span>", card)
        self.assertNotIn("October", card)

    def test_nav_marks_the_current_report_and_links_the_siblings(self):
        nav = re.search(r'<nav class="report-nav".*?</nav>', self.no_script, re.S).group(0)
        self.assertIn('<a href="../hyper-2026-10/" aria-current="page">HyperIDE</a>', nav)
        self.assertIn('href="../conloca-2026-10/"', nav)
        self.assertIn('href="../state-lib-2026-10/"', nav)

    def test_descriptive_link_labels_preserve_visible_text(self):
        for attrs, content in re.findall(r"<a\b([^>]*)>(.*?)</a>", self.no_script, re.S):
            label = re.search(r'aria-label="([^"]*)"', attrs)
            visible = strip(content)
            if label and visible and not visible.startswith("#"):
                self.assertIn(visible, label.group(1))


if __name__ == "__main__":
    unittest.main()
