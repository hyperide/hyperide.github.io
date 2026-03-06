/**
 * Report Template Components v1 — hyperide.github.io/reports/shared
 *
 * Usage:
 *   <div data-section="header"></div>
 *   <div data-section="motivation"></div>
 *   ...
 *   <script>Report.init(REPORT_DATA);</script>
 *
 * Each <div data-section="X"> is auto-rendered by the matching renderer.
 * Custom inline sections (e.g. architecture SVG) are left untouched.
 */
window.Report = (function () {
  'use strict';

  var data = null;

  // ── Helpers ──

  function esc(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fileLink(path, lines) {
    var hash = lines ? '#' + lines : '';
    return data.repo + '/blob/' + data.headSha + '/' + path + hash;
  }

  function fileLinkHtml(f) {
    return '<a href="' + fileLink(f.path, f.lines || '') + '" target="_blank">' + esc(f.label || f.path.split('/').pop()) + '</a>';
  }

  function fileLinksHtml(files) {
    return files.map(fileLinkHtml).join(' &middot; ');
  }

  function badgeHtml(label, cls) {
    return '<span class="badge ' + cls + '">' + esc(label) + '</span>';
  }

  function severityClass(sev) {
    return sev === 'P1' ? 'badge-p1' : sev === 'P2' ? 'badge-p2' : 'badge-p3';
  }

  function statusClass(st) {
    return st === 'resolved' ? 'badge-resolved' : 'badge-accepted';
  }

  function toggleNext(ev) {
    var body = ev.currentTarget.nextElementSibling;
    if (body) body.classList.toggle('open');
  }

  // ── Renderers ──

  function renderHeader(el) {
    var d = data;
    var links = (d.links || []).map(function (l) {
      return '<a href="' + l.href + '">' + esc(l.label) + '</a>';
    }).join('');
    el.className = 'report-header';
    el.innerHTML =
      '<h1>' + esc(d.title) + '</h1>' +
      (d.subtitle ? '<p class="subtitle">' + esc(d.subtitle) + '</p>' : '') +
      '<div class="meta">' +
        '<span>' + esc(d.date) + '</span>' +
        (d.badge ? badgeHtml(d.badge.label, 'badge-' + d.badge.type) : '') +
        (d.branch ? '<span>Branch: <code>' + esc(d.branch) + '</code></span>' : '') +
        links +
      '</div>';
  }

  function renderMotivation(el) {
    if (!data.motivation) { el.style.display = 'none'; return; }
    el.className = 'motivation-block fade-in';
    el.innerHTML = data.motivation; // allows HTML (lists, bold, etc.)
  }

  function renderSummary(el) {
    var s = data.stats;
    el.className = 'summary-grid fade-in';
    el.innerHTML =
      '<div class="summary-card"><div class="value">' + s.files + '</div><div class="label">Files Changed</div></div>' +
      '<div class="summary-card green"><div class="value">+' + s.insertions.toLocaleString() + '</div><div class="label">Insertions</div></div>' +
      '<div class="summary-card red"><div class="value">&minus;' + s.deletions.toLocaleString() + '</div><div class="label">Deletions</div></div>' +
      '<div class="summary-card"><div class="value">' + s.commits + '</div><div class="label">Commits</div></div>';
  }

  function renderChangeGroup(items, cssClass) {
    return items.map(function (item) {
      return '<div class="change-item ' + (cssClass || '') + '">' +
        '<h4>' + esc(item.title) + '</h4>' +
        '<p>' + esc(item.desc) + '</p>' +
        (item.files && item.files.length ? '<div class="files">' + fileLinksHtml(item.files) + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function renderProductChanges(el) {
    var pc = data.productChanges;
    el.className = 'report-section fade-in';
    el.innerHTML =
      '<h2>Product Changes</h2>' +
      '<div class="product-header"><span class="emoji">&#10024;</span> What\'s New</div>' +
      renderChangeGroup(pc.new || [], '') +
      '<div class="product-header" style="margin-top:28px"><span class="emoji">&#128640;</span> Improvements</div>' +
      renderChangeGroup(pc.improvements || [], 'improvement') +
      '<div class="product-header" style="margin-top:28px"><span class="emoji">&#128295;</span> Fixes</div>' +
      renderChangeGroup(pc.fixes || [], 'fix');
  }

  function renderFileImpact(el) {
    var byDir = {};
    data.files.forEach(function (f) {
      if (!byDir[f.dir]) byDir[f.dir] = [];
      byDir[f.dir].push(f);
    });
    el.className = 'report-section fade-in';
    var html = '<h2>File Impact</h2>';
    Object.keys(byDir).sort().forEach(function (dir) {
      var files = byDir[dir];
      var totalAdd = files.reduce(function (s, f) { return s + f.add; }, 0);
      var totalDel = files.reduce(function (s, f) { return s + f.del; }, 0);
      html += '<div class="file-group">' +
        '<div class="file-group-header"><span class="arrow">&#9654;</span> ' + esc(dir) + '/ ' +
        '<span style="margin-left:auto;font-size:.75rem;color:var(--text-dim)">' + files.length + ' files ' +
        '<span style="color:var(--green)">+' + totalAdd + '</span> ' +
        '<span style="color:var(--red)">-' + totalDel + '</span></span></div>' +
        '<div class="file-group-items">' +
        files.map(function (f) {
          var name = f.path.split('/').pop();
          var badge = f.status === 'new' ? '<span class="file-badge new">NEW</span>'
            : f.status === 'deleted' ? '<span class="file-badge deleted">DEL</span>'
            : '<span class="file-badge modified">MOD</span>';
          return '<div class="file-item">' + badge +
            ' <a href="' + fileLink(f.path, '') + '" target="_blank">' + esc(name) + '</a>' +
            '<div class="file-stat"><span class="add">+' + f.add + '</span><span class="del">-' + f.del + '</span></div></div>';
        }).join('') +
        '</div></div>';
    });
    el.innerHTML = html;

    // Toggle file groups
    el.querySelectorAll('.file-group-header').forEach(function (hdr) {
      hdr.addEventListener('click', function () {
        var items = hdr.nextElementSibling;
        var arrow = hdr.querySelector('.arrow');
        var open = items.style.display === 'block';
        items.style.display = open ? 'none' : 'block';
        arrow.classList.toggle('open', !open);
      });
    });
  }

  function renderFindings(el) {
    el.className = 'report-section fade-in';
    var html = '<h2>Review Findings</h2><div class="finding-filters" id="rpt-finding-filters"></div><div id="rpt-findings-list"></div>';
    el.innerHTML = html;

    var ff = el.querySelector('#rpt-finding-filters');
    var fl = el.querySelector('#rpt-findings-list');
    ['All', 'P1', 'P2', 'P3'].forEach(function (s) {
      var btn = document.createElement('button');
      btn.textContent = s;
      if (s === 'All') btn.classList.add('active');
      btn.onclick = function () {
        ff.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        fl.querySelectorAll('.finding-card').forEach(function (card) {
          card.style.display = s === 'All' || card.dataset.severity === s ? '' : 'none';
        });
      };
      ff.appendChild(btn);
    });

    (data.findings || []).forEach(function (f) {
      var card = document.createElement('div');
      card.className = 'finding-card';
      card.dataset.severity = f.severity;
      var linesLabel = f.lines ? ' <code style="color:var(--accent2);font-size:.75rem">' + f.lines + '</code>' : '';
      card.innerHTML =
        '<div class="finding-header"><span class="badge ' + severityClass(f.severity) + '">' + f.severity + '</span>' +
        '<span class="badge ' + statusClass(f.status) + '">' + f.status + '</span>' +
        '<span>' + esc(f.title) + '</span></div>' +
        '<div class="finding-body">' +
        '<p><strong>File:</strong> <a href="' + fileLink(f.file, f.lines || '') + '" target="_blank">' + esc(f.file) + linesLabel + '</a></p>' +
        '<p style="margin-top:8px"><strong>Issue:</strong> ' + esc(f.desc) + '</p>' +
        '<p style="margin-top:8px"><strong>Resolution:</strong> ' + esc(f.fix) + '</p>' +
        '</div>';
      card.querySelector('.finding-header').addEventListener('click', toggleNext);
      fl.appendChild(card);
    });
  }

  function renderTimeline(el) {
    el.className = 'report-section fade-in';
    el.innerHTML = '<h2>Commit Timeline</h2><div class="timeline-wrap"><div class="timeline"></div></div>';
    var tl = el.querySelector('.timeline');
    (data.commits || []).forEach(function (c) {
      var item = document.createElement('div');
      item.className = 'timeline-item';
      // Extract short type from commit message (e.g., "feat", "fix", "docs")
      var typeMatch = c.message.match(/^(\w+)/);
      var shortMsg = c.message.replace(/^[\w]+(\([^)]*\))?:\s*/, '');
      item.innerHTML =
        '<div class="timeline-tooltip"><strong>' + esc(c.sha) + '</strong> &mdash; ' + esc(c.message) + '<br><span style="color:var(--text-dim)">' + esc(c.date) + '</span></div>' +
        '<div class="timeline-dot"></div>' +
        '<div class="timeline-sha">' + esc(c.sha) + '</div>' +
        '<div class="timeline-msg">' + esc(shortMsg) + '</div>';
      item.addEventListener('click', function () { window.open(data.repo + '/commit/' + c.sha, '_blank'); });
      tl.appendChild(item);
    });
  }

  function renderCodexReview(el) {
    if (!data.codexRounds || !data.codexRounds.length) { el.style.display = 'none'; return; }
    el.className = 'report-section fade-in';
    var html = '<h2>Codex Review Rounds</h2>';
    data.codexRounds.forEach(function (round) {
      html += '<div class="codex-round"><div class="codex-round-header">' +
        '<h3>' + esc(round.title) + '</h3>' +
        (round.desc ? '<p style="color:var(--text-dim);font-size:.85rem">' + esc(round.desc) + '</p>' : '') +
        '<div class="badges">' + (round.badges || []).map(function (b) { return badgeHtml(b.label, b.cls); }).join('') + '</div>' +
        '</div>';
      if (round.items && round.items.length) {
        html += '<div class="codex-round-items">';
        round.items.forEach(function (item) {
          var lnk = item.file ? '<a href="' + fileLink(item.file, item.lines || '') + '" target="_blank">' + esc(item.file) + (item.lines ? ' <code>' + item.lines + '</code>' : '') + '</a>' : '';
          html += '<div class="codex-item"><span class="badge ' + severityClass(item.severity) + '">' + item.severity + '</span>' +
            '<span class="badge ' + statusClass(item.status) + '">' + item.status + '</span>' +
            '<span class="title">' + esc(item.title) + '</span></div>' +
            '<div class="codex-detail">' +
            (lnk ? '<p>' + lnk + '</p>' : '') +
            '<p style="margin-top:4px">' + esc(item.resolution) + '</p></div>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    el.innerHTML = html;

    // Toggle detail on item click
    el.querySelectorAll('.codex-item').forEach(function (item) {
      item.addEventListener('click', function () {
        var detail = item.nextElementSibling;
        if (detail && detail.classList.contains('codex-detail')) {
          detail.classList.toggle('open');
        }
      });
    });
  }

  function renderADR(el) {
    if (!data.adrs || !data.adrs.length) { el.style.display = 'none'; return; }
    el.className = 'report-section fade-in';
    var html = '<h2>Architecture Decisions</h2>';
    data.adrs.forEach(function (adr) {
      html += '<div class="adr-card">' +
        '<div class="adr-header">' + esc(adr.title) + '<span style="color:var(--text-dim);font-size:.8rem">Click to expand</span></div>' +
        '<dl class="adr-body">' +
        '<dt>Problem</dt><dd>' + esc(adr.problem) + '</dd>' +
        '<dt>Options</dt><dd>' + esc(adr.options) + '</dd>' +
        '<dt>Decision</dt><dd>' + esc(adr.decision) + '</dd>' +
        '<dt>Rationale</dt><dd>' + esc(adr.rationale) + '</dd>' +
        '</dl></div>';
    });
    el.innerHTML = html;
    el.querySelectorAll('.adr-header').forEach(function (hdr) {
      hdr.addEventListener('click', toggleNext);
    });
  }

  // ── Section registry ──

  var renderers = {
    'header': renderHeader,
    'motivation': renderMotivation,
    'summary': renderSummary,
    'product-changes': renderProductChanges,
    'file-impact': renderFileImpact,
    'findings': renderFindings,
    'timeline': renderTimeline,
    'codex-review': renderCodexReview,
    'adr': renderADR,
  };

  // ── Fade-in observer ──

  function setupFadeIn() {
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('visible'); obs.unobserve(e.target); }
      });
    }, { threshold: 0.1 });
    document.querySelectorAll('.fade-in').forEach(function (el) { obs.observe(el); });
  }

  // ── Architecture tooltips — pinned to node, not following cursor ──

  function setupArchTooltips() {
    var containers = document.querySelectorAll('.arch-container');
    if (!containers.length) return;

    var tooltip = document.createElement('div');
    tooltip.className = 'arch-tooltip';
    document.body.appendChild(tooltip);
    var hideTimer = null;
    var activeNode = null;

    function show(node) {
      clearTimeout(hideTimer);
      activeNode = node;
      var href = node.dataset.href || '';
      var desc = node.dataset.desc || '';
      var lines = node.dataset.lines || '';
      var lineHash = lines ? '#' + lines : '';
      var linesLabel = lines ? ' <code style="color:var(--accent2)">' + lines + '</code>' : '';
      // Don't append lineHash if href already contains a fragment
      var hasFragment = href.indexOf('#') !== -1;
      var fullUrl = href.startsWith('http') ? href + (hasFragment ? '' : lineHash) : data.repo + '/blob/' + data.headSha + '/' + href + lineHash;
      var displayName = href.startsWith('http') ? href.replace(/#.*$/, '').split('/').pop() : href;
      tooltip.innerHTML =
        '<div style="padding-top:12px">' +
        '<strong>' + esc(displayName) + '</strong>' + linesLabel + '<br>' + desc +
        '<br><a href="' + fullUrl + '" target="_blank">View source &rarr;</a>' +
        '</div>';

      // Position pinned below the node
      var rect = node.getBoundingClientRect();
      tooltip.style.left = (rect.left + rect.width / 2 + window.scrollX) + 'px';
      // Overlap the node by a few px so there's no dead zone
      tooltip.style.top = (rect.bottom + window.scrollY) + 'px';
      tooltip.style.transform = 'translateX(-50%)';
      tooltip.classList.add('visible');
    }

    function scheduleHide() {
      hideTimer = setTimeout(function () {
        tooltip.classList.remove('visible');
        activeNode = null;
      }, 200);
    }

    // Delegate events from arch-container to handle nested SVG elements
    containers.forEach(function (container) {
      container.addEventListener('mouseover', function (ev) {
        var target = ev.target.closest('[data-href]');
        if (!target || !container.contains(target)) return;
        show(target);
      });
      container.addEventListener('mouseout', function (ev) {
        var from = ev.target.closest('[data-href]');
        var to = ev.relatedTarget ? (ev.relatedTarget.closest ? ev.relatedTarget.closest('[data-href]') : null) : null;
        // Only schedule hide if we actually left the node (not moving between children)
        if (from && from !== to) {
          scheduleHide();
        }
      });
    });

    tooltip.addEventListener('mouseenter', function () { clearTimeout(hideTimer); });
    tooltip.addEventListener('mouseleave', scheduleHide);
  }

  // ── Public API ──

  return {
    data: null,
    fileLink: fileLink,
    fileLinkHtml: fileLinkHtml,
    fileLinksHtml: fileLinksHtml,
    esc: esc,

    init: function (reportData) {
      data = reportData;
      this.data = data;

      // Render all data-section elements
      document.querySelectorAll('[data-section]').forEach(function (el) {
        var type = el.getAttribute('data-section');
        if (renderers[type]) {
          renderers[type](el);
        }
      });

      setupFadeIn();
      setupArchTooltips();
    }
  };
})();
