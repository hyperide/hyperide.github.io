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

  // ── Specs & Plans viewer ──

  function renderMarkdown(text) {
    // 1. Protect code blocks
    var blocks = [];
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_, lang, code) {
      var idx = blocks.length;
      blocks.push('<pre><code>' + code.trimEnd().replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code></pre>');
      return '\u0000' + idx + '\u0000';
    });
    // 2. Inline code
    text = text.replace(/`([^`\n]+)`/g, function (_, c) {
      return '<code>' + c.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</code>';
    });
    // 3. Headers
    text = text.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
    text = text.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    text = text.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    text = text.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    // 4. Bold / italic
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
    // 5. Links
    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // 6. HR
    text = text.replace(/^---+$/gm, '<hr>');
    // 7. Blockquotes
    text = text.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
    // 8. Unordered lists
    text = text.replace(/((?:^[-*] .+\n?)+)/gm, function (block) {
      return '<ul>' + block.replace(/^[-*] (.+)$/gm, '<li>$1</li>') + '</ul>';
    });
    // 9. Tables
    text = text.replace(/((?:\|.*\|\n?)+)/g, function (table) {
      var rows = table.trim().split('\n');
      var html = '<table>';
      var pastSep = false;
      rows.forEach(function (row) {
        if (/^\|[-:| ]+\|$/.test(row)) { pastSep = true; return; }
        var cells = row.replace(/^\||\|$/g, '').split('|');
        var tag = !pastSep ? 'th' : 'td';
        html += '<tr>' + cells.map(function (c) { return '<' + tag + '>' + c.trim() + '</' + tag + '>'; }).join('') + '</tr>';
      });
      return html + '</table>';
    });
    // 10. Paragraphs
    text = text.split(/\n\n+/).map(function (block) {
      block = block.trim();
      if (!block) return '';
      if (/^(<h\d|<ul|<ol|<pre|<table|<blockquote|<hr|\u0000)/.test(block)) return block;
      return '<p>' + block.replace(/\n/g, '<br>') + '</p>';
    }).join('\n');
    // 11. Restore code blocks
    blocks.forEach(function (html, i) { text = text.split('\u0000' + i + '\u0000').join(html); });
    return text;
  }

  function renderSpecs(el) {
    if (!data.specs || !data.specs.length) { el.style.display = 'none'; return; }

    // Inject styles once
    if (!document.getElementById('rpt-specs-style')) {
      var s = document.createElement('style');
      s.id = 'rpt-specs-style';
      s.textContent = [
        '.spec-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-top:16px}',
        '.spec-card{background:rgba(88,166,255,.05);border:1px solid rgba(88,166,255,.2);border-radius:10px;padding:16px 18px;cursor:pointer;transition:border-color .2s,transform .15s}',
        '.spec-card:hover{border-color:var(--accent1);transform:translateY(-2px)}',
        '.spec-card[data-type="plan"]{background:rgba(16,185,129,.04);border-color:rgba(16,185,129,.25)}',
        '.spec-card[data-type="plan"]:hover{border-color:var(--green)}',
        '.spec-card[data-type="plan"] .spec-card-id{color:var(--green)}',
        '.spec-card-id{font-size:.72rem;color:var(--accent1);font-weight:700;margin-bottom:6px}',
        '.spec-card-title{font-size:.95rem;color:var(--text);font-weight:600;margin-bottom:4px}',
        '.spec-card-meta{font-size:.8rem;color:var(--text-dim)}',
        '.spec-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:1000;opacity:0;pointer-events:none;transition:opacity .2s;display:flex;align-items:flex-start;justify-content:center;padding:40px 20px;backdrop-filter:blur(4px)}',
        '.spec-modal-overlay.open{opacity:1;pointer-events:auto}',
        '.spec-modal{background:#161b22;border:1px solid var(--border);border-radius:14px;width:100%;max-width:820px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden}',
        '.spec-modal-header{display:flex;align-items:center;padding:14px 20px;border-bottom:1px solid var(--border);gap:12px;flex-shrink:0}',
        '.spec-modal-header h3{margin:0;font-size:1rem;color:var(--text);flex:1}',
        '.spec-modal-close{background:none;border:none;color:var(--text-dim);font-size:1.4rem;cursor:pointer;padding:2px 8px;border-radius:6px;line-height:1}',
        '.spec-modal-close:hover{background:var(--card);color:var(--text)}',
        '.spec-modal-tabs{display:flex;gap:6px;padding:10px 20px;border-bottom:1px solid var(--border);overflow-x:auto;flex-shrink:0}',
        '.spec-modal-tab{padding:5px 12px;border-radius:6px;font-size:.82rem;cursor:pointer;border:1px solid transparent;color:var(--text-dim);background:none;white-space:nowrap}',
        '.spec-modal-tab:hover{background:var(--card);color:var(--text)}',
        '.spec-modal-tab.active{border-color:var(--accent1);color:var(--accent1);background:rgba(88,166,255,.08)}',
        '.spec-modal-tab[data-type="plan"].active{border-color:var(--green);color:var(--green);background:rgba(16,185,129,.08)}',
        '.spec-modal-body{flex:1;overflow-y:auto;padding:24px 28px;font-size:.9rem}',
        '.spec-modal-body .loading{color:var(--text-dim);font-style:italic}',
        '.spec-modal-nav{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-top:1px solid var(--border);flex-shrink:0}',
        '.spec-modal-nav button{background:none;border:1px solid var(--border);color:var(--text-dim);padding:5px 14px;border-radius:6px;cursor:pointer;font-size:.85rem}',
        '.spec-modal-nav button:hover:not(:disabled){border-color:var(--accent1);color:var(--text)}',
        '.spec-modal-nav button:disabled{opacity:.3;cursor:default}',
        '.spec-modal-counter{color:var(--text-dim);font-size:.85rem}',
        '.spec-modal-body h1,.spec-modal-body h2{color:var(--text);border-bottom:1px solid var(--border);padding-bottom:8px;margin:24px 0 12px}',
        '.spec-modal-body h3,.spec-modal-body h4{color:var(--text);margin:16px 0 8px}',
        '.spec-modal-body h1{font-size:1.35rem}.spec-modal-body h2{font-size:1.1rem}.spec-modal-body h3{font-size:1rem}',
        '.spec-modal-body p{color:var(--text-dim);margin:8px 0;line-height:1.65}',
        '.spec-modal-body pre{background:rgba(0,0,0,.4);border:1px solid var(--border);border-radius:8px;padding:14px 16px;overflow-x:auto;margin:12px 0}',
        '.spec-modal-body pre code{color:#e6edf3;font-size:.82rem;background:none;padding:0;border-radius:0}',
        '.spec-modal-body code{background:rgba(110,118,129,.15);color:#79c0ff;padding:2px 5px;border-radius:4px;font-size:.87em}',
        '.spec-modal-body ul,.spec-modal-body ol{color:var(--text-dim);padding-left:20px;margin:8px 0}',
        '.spec-modal-body li{margin:4px 0;line-height:1.5}',
        '.spec-modal-body a{color:var(--accent1)}',
        '.spec-modal-body table{width:100%;border-collapse:collapse;margin:12px 0;font-size:.88rem}',
        '.spec-modal-body th{background:rgba(88,166,255,.08);color:var(--text);padding:8px 12px;border:1px solid var(--border);text-align:left}',
        '.spec-modal-body td{color:var(--text-dim);padding:7px 12px;border:1px solid var(--border)}',
        '.spec-modal-body blockquote{border-left:3px solid var(--accent1);padding-left:14px;color:var(--text-dim);margin:12px 0}',
        '.spec-modal-body strong{color:var(--text)}',
        '.spec-modal-body hr{border:none;border-top:1px solid var(--border);margin:20px 0}',
      ].join('');
      document.head.appendChild(s);
    }

    el.className = 'report-section fade-in';
    el.innerHTML = '<h2>Specs &amp; Plans</h2><p style="color:var(--text-dim);font-size:.9rem;margin-bottom:4px">Click any card to read the full spec or plan. Use ← → to navigate, Esc to close.</p><div class="spec-grid"></div>';

    var grid = el.querySelector('.spec-grid');
    var specs = data.specs;

    specs.forEach(function (spec, i) {
      var card = document.createElement('div');
      card.className = 'spec-card';
      card.setAttribute('data-type', spec.type || 'spec');
      card.innerHTML =
        '<div class="spec-card-id">' + esc(spec.id) + '</div>' +
        '<div class="spec-card-title">' + esc(spec.title) + '</div>' +
        '<div class="spec-card-meta">' + (spec.lines ? spec.lines + ' lines' : '') + (spec.date ? ' &middot; ' + esc(spec.date) : '') + '</div>';
      card.addEventListener('click', function () { openModal(i); });
      grid.appendChild(card);
    });

    // Build modal
    var overlay = document.createElement('div');
    overlay.className = 'spec-modal-overlay';
    overlay.innerHTML =
      '<div class="spec-modal">' +
        '<div class="spec-modal-header">' +
          '<h3 id="rpt-spec-title"></h3>' +
          '<a id="rpt-spec-link" href="#" target="_blank" style="color:var(--accent1);font-size:.82rem;flex-shrink:0">View on GitHub &#8599;</a>' +
          '<button class="spec-modal-close" aria-label="Close">&times;</button>' +
        '</div>' +
        '<div class="spec-modal-tabs" id="rpt-spec-tabs"></div>' +
        '<div class="spec-modal-body" id="rpt-spec-body"><p class="loading">Loading…</p></div>' +
        '<div class="spec-modal-nav">' +
          '<button id="rpt-spec-prev">&#8592; Prev</button>' +
          '<span class="spec-modal-counter" id="rpt-spec-counter"></span>' +
          '<button id="rpt-spec-next">Next &#8594;</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    var tabsEl = overlay.querySelector('#rpt-spec-tabs');
    specs.forEach(function (spec, i) {
      var tab = document.createElement('button');
      tab.className = 'spec-modal-tab';
      tab.setAttribute('data-type', spec.type || 'spec');
      tab.textContent = spec.id + ' — ' + spec.title;
      tab.addEventListener('click', function () { openModal(i); });
      tabsEl.appendChild(tab);
    });

    var currentIdx = 0;

    function openModal(idx) {
      currentIdx = idx;
      var spec = specs[idx];
      overlay.classList.add('open');
      overlay.querySelector('#rpt-spec-title').textContent = spec.title;
      var ghUrl = data.repo + '/blob/' + data.headSha + '/' + spec.file;
      overlay.querySelector('#rpt-spec-link').href = ghUrl;
      overlay.querySelectorAll('.spec-modal-tab').forEach(function (t, i) { t.classList.toggle('active', i === idx); });
      overlay.querySelector('#rpt-spec-prev').disabled = idx === 0;
      overlay.querySelector('#rpt-spec-next').disabled = idx === specs.length - 1;
      overlay.querySelector('#rpt-spec-counter').textContent = (idx + 1) + ' / ' + specs.length;
      var body = overlay.querySelector('#rpt-spec-body');
      if (spec._content) { body.innerHTML = renderMarkdown(spec._content); return; }
      body.innerHTML = '<p class="loading">Loading…</p>';
      var rawUrl = spec.rawUrl || ('https://raw.githubusercontent.com/' + data.repo.replace('https://github.com/', '') + '/' + data.headSha + '/' + spec.file);
      fetch(rawUrl)
        .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
        .then(function (text) { spec._content = text; body.innerHTML = renderMarkdown(text); })
        .catch(function () { body.innerHTML = '<p style="color:var(--red)">Failed to load. <a href="' + ghUrl + '" target="_blank">View on GitHub &#8599;</a></p>'; });
    }

    overlay.querySelector('.spec-modal-close').addEventListener('click', function () { overlay.classList.remove('open'); });
    overlay.querySelector('#rpt-spec-prev').addEventListener('click', function () { if (currentIdx > 0) openModal(currentIdx - 1); });
    overlay.querySelector('#rpt-spec-next').addEventListener('click', function () { if (currentIdx < specs.length - 1) openModal(currentIdx + 1); });
    overlay.addEventListener('click', function (ev) { if (ev.target === overlay) overlay.classList.remove('open'); });
    document.addEventListener('keydown', function (ev) {
      if (!overlay.classList.contains('open')) return;
      if (ev.key === 'Escape') overlay.classList.remove('open');
      if (ev.key === 'ArrowRight' && currentIdx < specs.length - 1) openModal(currentIdx + 1);
      if (ev.key === 'ArrowLeft' && currentIdx > 0) openModal(currentIdx - 1);
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
    'specs': renderSpecs,
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

    // Auto-wrap bare SVG rect[data-href] + adjacent <text> into <g>
    // so hover works on both the box and its label
    containers.forEach(function (c) {
      var rects = c.querySelectorAll('rect[data-href]');
      rects.forEach(function (rect) {
        // Skip if already inside a <g> with data-href
        if (rect.parentElement && rect.parentElement.tagName === 'g' && rect.parentElement.hasAttribute('data-href')) return;
        var next = rect.nextElementSibling;
        var svgNs = 'http://www.w3.org/2000/svg';
        var g = document.createElementNS(svgNs, 'g');
        // Move data attributes to the group
        ['href', 'lines', 'desc'].forEach(function (attr) {
          var val = rect.getAttribute('data-' + attr);
          if (val) { g.setAttribute('data-' + attr, val); rect.removeAttribute('data-' + attr); }
        });
        rect.parentNode.insertBefore(g, rect);
        g.appendChild(rect);
        // Grab adjacent text(s) that belong to this box
        while (g.nextElementSibling && g.nextElementSibling.tagName === 'text') {
          var txt = g.nextElementSibling;
          // Check if text is visually inside the rect (by y-coordinate)
          var ry = parseFloat(rect.getAttribute('y') || 0);
          var rh = parseFloat(rect.getAttribute('height') || 0);
          var ty = parseFloat(txt.getAttribute('y') || 0);
          if (ty >= ry && ty <= ry + rh + 4) {
            g.appendChild(txt);
          } else {
            break;
          }
        }
      });
    });

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
