"""Contract for the screenshots, original requests and recordings in the three reports.

Each report directory holds media-evidence.json (the public-safe record: hashes, crop coordinates,
source labels, message identity) next to index.html. The page and the record must agree.

Run: python3 scripts/ci/check-media-report.py
"""

import hashlib
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _report_html import Links, strip  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SLUGS = {"hyper": "hyper-2026-10", "conloca": "conloca-2026-10", "statelib": "state-lib-2026-10"}
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
RELATION = {"spec": "Recorded in spec", "pr": "Recorded in pull request", "task": "Recorded in task", "inferred": "Inferred link"}
# Full-message hashes recovered independently from the native Telegram archive (not from the research record).
INDEPENDENT_MESSAGE_SHA256 = {
    174500: "f07e16a579836f600f31a2b54f36dc693d2d8b0a67f7a9ce6b6999a07aad8e19",
    182966: "9eb8a44deb8c65e676ce2aebe44839463691ea0ab8f2bcaacf57659f6c6ccef6",
    182984: "243c6a1cf4c5ceef03dc4860a38cc6f75fb0976479de0f1a133d61f6768fcbad",
}
GOLDEN_TRANSLATIONS = {
    "req-hyp990-native-notification": "It covers everything, a huge amount of text, the text is to the left of the button, a lot of empty space. Some kind of non-standard notification. In the extension the standard VS Code ones should be used, and in SaaS the standard SaaS ones.",
    "req-hyp990-details-and-ai-context": "And think about how to reveal the long text with details. Either expand it right in the toast, or show more in a banner when Details is clicked, and in any case the AI must receive all the details.",
    "req-hyp1372-old-bug-back": "And another thing: an old bug has come back. When I click a component in Hyper Explorer, a split is not created.",
    "req-hyp1372-first-click": "By the way, the second click in a row does open the split, but the first click never opens the split for me. I tried several times.",
    "req-state-lib-compare": "We need to compare with and without it in more detail, using different criteria.",
    "req-state-lib-numbers": "All right, let's go. Also it would be good for it to give better results in numbers: to run faster and for the bundle to weigh less.",
}
# Requests the research could not recover: the report must not show a quote for them.
NOT_RECOVERED_OUTCOMES = {"c1-stale-context-cancellation", "theme-picker", "landing"}


def norm(s):
    return " ".join(s.split())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MediaReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages, cls.ev, cls.raw = {}, {}, {}
        for key, slug in SLUGS.items():
            d = ROOT / "reports" / slug
            text = (d / "index.html").read_text(encoding="utf-8")
            cls.raw[key] = text
            cls.pages[key] = re.sub(r"<script\b.*?</script>", "", text, flags=re.S)
            cls.ev[key] = json.loads((d / "media-evidence.json").read_text(encoding="utf-8"))

    def _dir(self, key):
        return ROOT / "reports" / SLUGS[key]

    # ------------------------------------------------------------ original requests
    def test_every_request_has_origin_fields(self):
        total = 0
        for key, ev in self.ev.items():
            for r in ev["originalRequests"]:
                total += 1
                self.assertEqual(r["author"], "Ultra / Mextner")
                self.assertEqual(r["language"], "ru")
                datetime.fromisoformat(r["dateUtc"].replace("Z", "+00:00"))
                self.assertTrue(r["sourceLabel"] and r["senderBasis"] and r["messageIdentity"], r["id"])
                self.assertRegex(r["messageSha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(r["excerptSha256"], hashlib.sha256(r["ru"].encode()).hexdigest(), r["id"])
                self.assertIn(r["relationKind"], RELATION)
                self.assertTrue(r["relation"], r["id"])
                self.assertIsNone(r["permalink"], "no link is invented for a private message")
                if r["sourceLabel"].startswith("Telegram"):
                    self.assertTrue({"tg", "telegramMessageId", "botMessageId"} & set(r["ids"]), r["id"])
                    self.assertIn("incoming Telegram message from the owner's account", r["senderBasis"])
                if r.get("excerptIsWholeMessage"):
                    self.assertEqual(r["excerptSha256"], r["messageSha256"], r["id"])
                known = INDEPENDENT_MESSAGE_SHA256.get(r["ids"].get("telegramMessageId"))
                if known:
                    self.assertEqual(r["messageSha256"], known, r["id"])
        self.assertGreaterEqual(total, 25)

    def test_request_ids_are_unique_and_placed_in_their_report(self):
        seen = set()
        for key, ev in self.ev.items():
            for r in ev["originalRequests"]:
                self.assertNotIn(r["id"], seen)
                seen.add(r["id"])
                self.assertEqual(r["report"], key)
                self.assertIn(f'id="{r["id"]}"', self.pages[key], r["id"])

    def test_page_shows_labelled_russian_and_english_exactly_as_recorded(self):
        for key, ev in self.ev.items():
            page = self.pages[key]
            self.assertEqual(len(re.findall(r'class="owner-request"', page)), len(ev["originalRequests"]), key)
            for r in ev["originalRequests"]:
                block = re.search(r'<div class="owner-request" id="%s".*?<p class="request-link">.*?</p>(?:<p class="request-note">.*?</p>)?' % re.escape(r["id"]), page, re.S)
                self.assertIsNotNone(block, r["id"])
                b = block.group(0)
                self.assertIn('<p class="request-label">Original request (RU)</p>', b)
                self.assertIn('<p class="request-label">English translation</p>', b)
                ru = re.search(r'<blockquote lang="ru">(.*?)</blockquote>', b, re.S).group(1)
                en = re.search(r'<blockquote lang="en">(.*?)</blockquote>', b, re.S).group(1)
                self.assertEqual(norm(html.unescape(re.sub(r"<br>", "\n", ru))), norm(r["ru"]), r["id"])
                self.assertEqual(norm(html.unescape(en)), norm(r["en"]), r["id"])
                self.assertIn(f'<strong>{RELATION[r["relationKind"]]}.</strong>', b)
                head = re.search(r'<h[45] id="%s-h">(.*?)</h[45]>' % re.escape(r["id"]), b).group(1)
                self.assertEqual("(excerpt)" in head, not r["excerptIsWholeMessage"], r["id"])
                note = re.search(r'<p class="request-note">(.*?)</p>', b, re.S)
                shown_window = r["windowNote"] and ("window" in r["windowNote"] or "period" in r["windowNote"])
                if shown_window:
                    self.assertIsNotNone(note, r["id"])
                    self.assertIn(r["windowNote"][0].upper() + r["windowNote"][1:] + ".", html.unescape(note.group(1)), r["id"])
                if not (r["omitted"] or shown_window):
                    self.assertIsNone(note, r["id"])
                if r["omitted"]:
                    self.assertIsNotNone(note, r["id"])
                    self.assertIn(r["omitted"], html.unescape(note.group(1)), r["id"])
                self.assertIn(r["messageSha256"], b)
                self.assertIn(r["messageIdentity"], html.unescape(b))
                d = datetime.fromisoformat(r["dateUtc"].replace("Z", "+00:00"))
                self.assertIn(f"{d.day} {d.strftime('%B %Y')}, {d.strftime('%H:%M')} UTC", b)

    def test_translations_are_complete_and_golden(self):
        for key, ev in self.ev.items():
            for r in ev["originalRequests"]:
                self.assertRegex(r["ru"], CYRILLIC, r["id"])
                self.assertNotRegex(r["en"], CYRILLIC, r["id"])
                self.assertNotRegex(r["en"], r"source (row )?ends|cut off|…$", r["id"])
                ratio = len(r["en"]) / len(r["ru"])
                if "[" not in r["en"]:
                    self.assertTrue(0.5 < ratio < 2.6, (r["id"], ratio))
                if r["pastedEnglishBugText"]:
                    self.assertIn("[English bug text, as pasted from the bug database:]", r["en"], r["id"])
                    self.assertRegex(r["relation"], r"owner's wording")
        by_id = {r["id"]: r for ev in self.ev.values() for r in ev["originalRequests"]}
        for rid, text in GOLDEN_TRANSLATIONS.items():
            self.assertEqual(by_id[rid]["en"], text, rid)

    def test_unrecovered_requests_are_not_quoted_or_invented(self):
        used = {r["outcome"] for ev in self.ev.values() for r in ev["originalRequests"]}
        self.assertFalse(used & NOT_RECOVERED_OUTCOMES)
        for key, ev in self.ev.items():
            for r in ev["originalRequests"]:
                self.assertNotIn("HYP-1480", r["relation"] + r["en"])
                if r["dateUtc"].startswith(("2026-10-15", "2026-10-16")):
                    # The 15 and 16 October owner conversation was not recovered from any message store; only the two ChatGPT retrievals exist.
                    self.assertIn("ChatGPT", r["sourceLabel"], r["id"])
                    self.assertEqual(r["messageIdentity"], "no message id recovered", r["id"])
        hyper = self.pages["hyper"]
        c1 = re.search(r'<article class="outcome[^"]*" id="c1-stale-context-cancellation".*?</article>', hyper, re.S).group(0)
        self.assertNotIn("owner-request", c1)
        conloca = self.pages["conloca"]
        landing = conloca.split('id="landing"', 1)[1].split("</article>", 1)[0]
        approval = conloca.split('id="approval"', 1)[1].split('id="proof"', 1)[0]
        self.assertNotIn("owner-request", landing)
        self.assertNotIn("owner-request", approval)

    def test_window_notes_match_each_reports_own_period(self):
        # HyperIDE covers 6 August to 6 November 2026; StateLib and Conloca cover October 2026.
        for key, ev in self.ev.items():
            for r in ev["originalRequests"]:
                note = r.get("windowNote") or ""
                if "before the 6 August window" in note:
                    self.assertEqual(key, "hyper", r["id"])
                    self.assertLess(r["dateUtc"], "2026-08-06", r["id"])
                if "before the October period" in note:
                    self.assertIn(key, ("statelib", "conloca"), r["id"])
                    self.assertLess(r["dateUtc"], "2026-10-01", r["id"])

    def test_the_chatgpt_requests_carry_no_invented_link(self):
        for r in self.ev["statelib"]["originalRequests"]:
            if "ChatGPT" in r["sourceLabel"]:
                self.assertEqual(r["messageIdentity"], "no message id recovered")
                self.assertEqual(r["ids"], {})
                self.assertIn("no message link was recovered", r["senderBasis"])

    # ------------------------------------------------------------ screenshots
    def _assert_png(self, path, sha, width, height, size=None):
        data = Path(path).read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), sha, path)
        self.assertEqual(struct.unpack(">II", data[16:24]), (width, height), path)
        if size is not None:
            self.assertEqual(len(data), size)
            self.assertGreater(len(data), 3000)

    def _assert_box(self, im, n, b):
        x0, y0, x1, y1 = im["roi"]
        self.assertEqual(b["n"], n)
        bx0, by0, bx1, by1 = b["original_xyxy"]
        self.assertTrue(x0 <= bx0 < bx1 <= x1 and y0 <= by0 < by1 <= y1, (im["id"], n))
        cx0, cy0, cx1, cy1 = b["crop_xyxy"]
        self.assertTrue(0 <= cx0 < cx1 <= im["width"] and 0 <= cy0 < cy1 <= im["height"], (im["id"], n))
        projected = ((bx0 - x0) * im["scale"], (by0 - y0) * im["scale"], (bx1 - x0) * im["scale"], (by1 - y0) * im["scale"])
        for got, want in zip((cx0, cy0, cx1, cy1), projected):
            # The box is clamped 3 to 4 px inside the crop edge so that its outline stays visible.
            self.assertLessEqual(abs(got - want), 4, (im["id"], n))
        self.assertTrue(b["note"], (im["id"], n))

    def _assert_crop(self, d, im):
        self._assert_png(d / im["path"], im["sha256"], im["width"], im["height"], im["bytes"])
        o = im["original"]
        self._assert_png(d / o["path"], o["sha256"], o["width"], o["height"])
        x0, y0, x1, y1 = im["roi"]
        self.assertTrue(0 <= x0 < x1 <= o["width"] and 0 <= y0 < y1 <= o["height"], im["id"])
        self.assertEqual((round((x1 - x0) * im["scale"]), round((y1 - y0) * im["scale"])), (im["width"], im["height"]), im["id"])
        self.assertGreaterEqual(im["width"], 600, f"{im['id']} would not be readable on a phone")
        self.assertTrue(im["boxes"], im["id"])
        for n, b in enumerate(im["boxes"], 1):
            self._assert_box(im, n, b)
        for text in (im["alt"], im["caption"]):
            self.assertGreaterEqual(len(text), 25, im["id"])
        self.assertNotIn("/home/", json.dumps(im))
        self.assertNotIn("/Users/", json.dumps(im))

    def test_image_hashes_sizes_and_crop_coordinates(self):
        for key, ev in self.ev.items():
            for im in ev["images"]:
                self._assert_crop(self._dir(key), im)

    def _assert_pair(self, pair, ims):
        self.assertEqual(sorted(i["role"] for i in ims), ["after", "before"], pair)
        b, a = [next(i for i in ims if i["role"] == r) for r in ("before", "after")]
        for field in ("roi", "scale", "outcome"):
            self.assertEqual(b[field], a[field], (pair, field))
        self.assertEqual((b["width"], b["height"]), (a["width"], a["height"]), pair)
        self.assertNotEqual(b["sha256"], a["sha256"], pair)
        self.assertNotEqual(b["original"]["sha256"], a["original"]["sha256"], pair)
        self.assertTrue(b["chip"].startswith("Before") and a["chip"].startswith("After"), pair)

    @staticmethod
    def _real_pairs(images):
        by_pair = {}
        for im in images:
            if im["pair"] and not im["pair"].endswith("sequence"):
                by_pair.setdefault(im["pair"], []).append(im)
        return by_pair

    def test_before_after_pairs_share_region_scale_and_provenance(self):
        for ev in self.ev.values():
            for pair, ims in self._real_pairs(ev["images"]).items():
                self._assert_pair(pair, ims)

    def _assert_group_labelling(self, g, images):
        kinds = {images[c]["role"] for c in g["crops"]}
        pairs = [images[c]["pair"] for c in g["crops"]]
        if g["kind"] == "sequence":
            self.assertTrue(all(k.startswith("step-") for k in kinds), g["id"])
            self.assertTrue(all(p is None or p.endswith("sequence") for p in pairs), g["id"])
        if g["kind"] == "single":
            self.assertTrue(kinds <= {"single-after", "context"}, g["id"])
            self.assertFalse(any(pairs), g["id"])

    def test_single_captures_and_sequences_are_not_labelled_as_before_after(self):
        for ev in self.ev.values():
            images = {i["id"]: i for i in ev["images"]}
            for g in ev["groups"]:
                self._assert_group_labelling(g, images)
        hyper_groups = {g["id"]: g for g in self.ev["hyper"]["groups"]}
        for gid in ("pr805-sequence", "pr705-sequence"):
            self.assertEqual(hyper_groups[gid]["kind"], "sequence")
            block = re.search(r'<div class="shot-group shot-sequence[^"]*" id="%s".*?<div class="shot-grid' % gid, self.pages["hyper"], re.S).group(0)
            self.assertIn("It is not a before and after", block)
        expected = {"single": ("pr665-warning", "pr738-warning", "pr823-restored", "pr811-restored"),
                    "pair": ("pr721-explorer", "pr827-write", "pr794-edit-component", "pr811-order")}
        for kind, ids in expected.items():
            for gid in ids:
                self.assertEqual(hyper_groups[gid]["kind"], kind, gid)

    def test_721_after_is_the_final_wording_and_the_interim_capture_is_labelled(self):
        ims = {i["id"]: i for i in self.ev["hyper"]["images"]}
        self.assertEqual(ims["pr721-after-explorer"]["original"]["sha256"], "db843126c805832bf876915eb9da80778cea6d6945c48428215161bda2fc1fdf")
        self.assertEqual(ims["pr721-before-explorer"]["original"]["sha256"], "4a5a405a057a77084867df53d0dd164473ab072b5ffe792eb14059be17b8a5da")
        interim = ims["pr721-after-interim-explorer"]
        self.assertEqual(interim["original"]["sha256"], "f85b2341c4df27f1a61addffe90e8a07f4f7085e8efc9ba3d8936782abe2377d")
        self.assertIsNone(interim["pair"])
        self.assertEqual(interim["chip"], "Interim after")
        self.assertIn("not the merged copy", interim["caption"])
        page = self.pages["hyper"]
        self.assertIn("No folder opened. Opening folder will close all current editors", html.unescape(page))

    def test_794_legend_numbers_every_mark(self):
        for im in self.ev["hyper"]["images"]:
            if im["id"].startswith("pr794-"):
                fig = re.search(r'<figure class="shot [^"]*" id="fig-%s">.*?</figure>' % re.escape(im["id"]), self.pages["hyper"], re.S).group(0)
                self.assertEqual(len(re.findall(r"<li>", fig)), len(im["boxes"]), im["id"])
                self.assertEqual([b["n"] for b in im["boxes"]], list(range(1, len(im["boxes"]) + 1)), im["id"])

    def test_every_page_screenshot_has_alt_caption_legend_and_a_working_original_link(self):
        for key, ev in self.ev.items():
            page, d = self.pages[key], self._dir(key)
            figs = re.findall(r'<figure class="shot [^"]*" id="fig-([^"]+)">(.*?)</figure>', page, re.S)
            self.assertEqual(sorted(f[0] for f in figs), sorted(i["id"] for i in ev["images"]), key)
            for fid, body in figs:
                im = next(i for i in ev["images"] if i["id"] == fid)
                alt = html.unescape(re.search(r'alt="([^"]*)"', body).group(1))
                self.assertEqual(alt, im["alt"])
                self.assertIn(f'width="{im["width"]}" height="{im["height"]}"', body)
                self.assertIn('loading="lazy"', body)
                hrefs = re.findall(r'href="([^"]+)"', body)
                self.assertEqual(set(hrefs), {im["original"]["path"]}, fid)
                self.assertTrue((d / hrefs[0]).is_file())
                self.assertIn(f'Full-size original ({im["original"]["width"]}×{im["original"]["height"]} PNG)', body)
                self.assertIn(html.escape(im["caption"], quote=False), body.replace("&#x27;", "'"))
                self.assertEqual(len(re.findall(r"<li>", body)), len(im["boxes"]))
                self.assertIn(f'<span class="chip', body)
                self.assertNotIn("aria-label", body.split("<figcaption>", 1)[0], "the image alt text must name the link")
            self.assertEqual(len(re.findall(r"<img\b", page)), len(ev["images"]), key)

    def test_no_bottom_gallery_and_statelib_fixtures_are_not_editor_proof(self):
        for key in SLUGS:
            for tail in re.findall(r"<section class=\"report-section\" id=\"(?:screenshots|figures)\".*?</section>", self.pages[key], re.S):
                self.assertNotIn("<img", tail)
        paths = json.dumps(self.ev["statelib"]["images"])
        for banned in ("statelib-chromium", "statelib-webkit", "pilot-", "ub113", "synthetic"):
            self.assertNotIn(banned, paths)
        self.assertEqual({i["id"] for i in self.ev["statelib"]["images"]}, {"statelib-caption-before", "statelib-caption-after"})

    def test_theme_pair_names_its_two_environments(self):
        ims = {i["id"]: i for i in self.ev["conloca"]["images"]}
        self.assertIn("production", ims["conloca-theme-before"]["chip"])
        self.assertIn("PR preview", ims["conloca-theme-after"]["chip"])
        self.assertEqual(ims["conloca-theme-before"]["roi"], ims["conloca-theme-after"]["roi"])
        text = strip(self.pages["conloca"])
        self.assertIn("The before image is production and the after image is the preview of an open pull request that is not merged", text)

    # ------------------------------------------------------------ recordings
    def _assert_video_file(self, d, v, part):
        magic = b"ftyp" if part == "mp4" else b"\x1a\x45\xdf\xa3"
        f = d / v[part]["path"]
        data = f.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), v[part]["sha256"], v["id"])
        self.assertEqual(len(data), v[part]["bytes"])
        self.assertEqual(data[4:8] if part == "mp4" else data[:4], magic, (v["id"], part))
        if os.environ.get("CI"):
            self.assertTrue(shutil.which("ffprobe"), "ffprobe must be installed in CI so that the MP4 files are really decoded")
        if not shutil.which("ffprobe"):
            return
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height:format=duration", "-of", "json", str(f)]
        out = json.loads(subprocess.check_output(cmd))
        stream = out["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (v[part]["width"], v[part]["height"]), v["id"])
        self.assertAlmostEqual(float(out["format"]["duration"]), v[part]["durationSeconds"], delta=0.1)
        self.assertEqual(stream["codec_name"], "h264" if part == "mp4" else "vp9")

    def _assert_video_markup(self, d, v, page):
        poster = (d / v["poster"]["path"]).read_bytes()
        self.assertEqual(poster[:3], b"\xff\xd8\xff")
        self.assertEqual(hashlib.sha256(poster).hexdigest(), v["poster"]["sha256"])
        m = re.search(r'<video controls playsinline preload="metadata" poster="%s"[^>]*>.*?</video>' % re.escape(v["poster"]["path"]), page, re.S)
        self.assertIsNotNone(m, v["id"])
        self.assertNotIn("autoplay", m.group(0))
        self.assertIn(f'<source src="{v["mp4"]["path"]}" type="video/mp4"><source src="{v["webm"]["path"]}" type="video/webm">', m.group(0))

    def test_recordings_are_playable_files_with_posters_and_honest_labels(self):
        ev = self.ev["conloca"]
        page = self.pages["conloca"]
        d = self._dir("conloca")
        self.assertEqual(len(ev["videos"]), 4)
        for v in ev["videos"]:
            for part in ("mp4", "webm"):
                self._assert_video_file(d, v, part)
            self._assert_video_markup(d, v, page)
        self.assertNotIn("autoplay", page)
        self.assertEqual(len(re.findall(r"<video\b", page)), 4)
        self.assertEqual(page.count("Recorded production browser check</span>"), 4)
        self.assertEqual(page.count("It is not a CI or end-to-end test run and not full acceptance"), 4)

    def test_the_316_clip_is_a_cropped_derived_copy_and_the_original_is_not_published(self):
        v = next(v for v in self.ev["conloca"]["videos"] if v["id"] == "conloca-pr316-new-branch-field")
        self.assertEqual(v["derived"]["cropXYWH"], [182, 40, 300, 230])
        self.assertEqual((v["mp4"]["width"], v["mp4"]["height"]), (600, 460))
        self.assertEqual(v["derived"]["sourceSha256"], "ce25011c07367aff2de27d53927b8212f55af2815cf55b72c731146268f84c86")
        published = [p.name for p in (ROOT / "reports").rglob("*.mp4")]
        self.assertNotIn("conloca-pr316-sigma-caret-production.mp4", published)
        for p in (ROOT / "reports").rglob("*.mp4"):
            self.assertNotEqual(sha(p), v["derived"]["sourceSha256"])

    def test_no_job_page_is_presented_as_a_recording_and_no_e2e_recording_is_claimed(self):
        for key, page in self.pages.items():
            links = re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
            for href, label in links:
                if "/actions/runs/" in href:
                    self.assertNotRegex(strip(label), r"(?i)record|video|trace|playback", (key, href))
            text = strip(page)
            self.assertNotRegex(text, r"(?i)\b(e2e|end-to-end) recording (of|shows|is hosted|available)")
        hyper = strip(self.pages["hyper"])
        self.assertIn("No hosted end-to-end recording or trace exists for these results", hyper)
        self.assertIn("a CI job page is not a recording", hyper)
        self.assertEqual(self.ev["hyper"]["videos"], [])
        self.assertEqual(self.ev["statelib"]["videos"], [])

    # ------------------------------------------------------------ static content and privacy
    def test_everything_is_in_the_static_html_without_javascript(self):
        for key, page in self.pages.items():
            ev = self.ev[key]
            self.assertEqual(len(re.findall(r'class="owner-request"', page)), len(ev["originalRequests"]))
            self.assertEqual(len(re.findall(r"<img\b", page)), len(ev["images"]))
            self.assertEqual(len(re.findall(r"<video\b", page)), len(ev["videos"]))
            self.assertNotIn("display:none", page.replace(" ", ""))
            self.assertNotIn("<details open", page)

    def test_media_css_is_linked_and_versioned(self):
        for key in SLUGS:
            head = self.raw[key].split("</head>", 1)[0]
            self.assertIn('href="../shared/media-proof-20261106.css"', head)
            self.assertLess(head.index("october-own-20261106.css"), head.index("media-proof-20261106.css"))
        css = (ROOT / "reports/shared/media-proof-20261106.css").read_text()
        for needle in ("@media (max-width: 650px)", ".request-cols", "grid-template-columns: minmax(0, 1fr)", ".shot img", ".clip video"):
            self.assertIn(needle, css)

    def test_public_record_and_pages_hold_no_private_identifiers(self):
        blobs = [json.dumps(e, ensure_ascii=False) for e in self.ev.values()] + list(self.raw.values())
        banned = ("5153477378", "/Users/", "/home/ultra", "notion.com", "tg-ctl", "history-archive", "AUTH_KEY", "pages.hyperide.ai", "Bearer ", "ghp_", "user-attachments/assets", "session_id")
        for blob in blobs:
            for b in banned:
                self.assertNotIn(b, blob, b)

    def test_links_on_the_pages_stay_on_allowed_origins(self):
        allowed = ("https://github.com/", "https://tasks.glide.vc/", "https://conloca.com", "https://site.pr489.conloca.com/", "https://linear.app/")
        for key, page in self.pages.items():
            for url in Links(page).urls:
                if url.startswith("http"):
                    self.assertTrue(url.startswith(allowed), (key, url))


if __name__ == "__main__":
    unittest.main()
