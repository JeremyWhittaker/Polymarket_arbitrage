/* Research desk UI: sport tabs -> strategies -> paged trades, all served by the local API. */
(function () {
  var S = { index: [], tabs: [], tab: "all", slug: null, meta: null, q: "",
            period: "all", sport: "all", result: "all", tq: "", sort: "entry_ts", desc: false,
            offset: 0, limit: 100, open: null, page: null };
  var $ = function (id) { return document.getElementById(id); };
  var api = function (p) { return fetch(p).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); };
  var pct = function (v, d) { return (v == null || isNaN(v)) ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(d == null ? 1 : d) + "%"; };
  var usd = function (v) { return (v == null || isNaN(v)) ? "–" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 }); };
  var cents = function (v) { return (v == null || isNaN(v)) ? "–" : (v * 100).toFixed(1) + "¢"; };
  var sgn = function (v) { return v > 1e-7 ? "pos" : v < -1e-7 ? "neg" : "zero"; };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); };
  var when = function (ts) { if (!ts) return "–"; return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) + "Z"; };
  var vclass = function (v) { v = (v || "").toLowerCase(); return v.indexOf("profit") === 0 ? "profitable" : v.indexOf("promis") === 0 ? "promising" : v.indexOf("disput") === 0 ? "disputed" : "dead"; };

  function inTab(d) {
    if (S.tab === "all") return true;
    var c = d.sport_counts || {};
    if (S.tab === "other") { return Object.keys(c).some(function (k) { return ["baseball", "soccer", "american_football", "basketball", "tennis", "esports", "hockey"].indexOf(k) < 0; }); }
    return (c[S.tab] || 0) > 0;
  }

  function renderTabs() {
    $("tabs").innerHTML = S.tabs.map(function (t) {
      return '<button class="tab" role="tab" data-k="' + t.key + '" aria-selected="' + (t.key === S.tab) + '">' +
        esc(t.label) + "<span>" + (t.trades || 0).toLocaleString() + "</span></button>";
    }).join("");
    Array.prototype.forEach.call($("tabs").children, function (b) {
      b.onclick = function () {
        S.tab = b.dataset.k;
        S.sport = (S.tab === "all" || S.tab === "other") ? "all" : S.tab;
        renderTabs(); renderRail();
        var list = S.index.filter(inTab);
        if (list.length && !list.some(function (d) { return d.slug === S.slug; })) select(list[0].slug);
        else if (S.slug) loadStrategy(S.slug);
      };
    });
  }

  function renderRail() {
    var q = S.q.toLowerCase(), groups = {}, order = [];
    S.index.filter(inTab).forEach(function (d) {
      if (q && (d.title + " " + d.hypothesis).toLowerCase().indexOf(q) < 0) return;
      if (!groups[d.group]) { groups[d.group] = []; order.push(d.group); }
      groups[d.group].push(d);
    });
    $("rail").innerHTML = order.map(function (g) {
      return '<section class="rail-group"><h3>' + esc(g || "Other") + "</h3>" + groups[g].map(function (d) {
        var sp = (S.tab === "other" || S.tab === "all") ? "all" : S.tab;
        var h = (d.kpis && d.kpis[sp]) ? d.kpis[sp] : (d.headline || {});
        var best = h.holdout || h.dev || {};
        var n = S.tab === "all" ? d.n_total_trades : ((d.sport_counts || {})[S.tab] || d.n_total_trades);
        return '<button class="item" data-slug="' + esc(d.slug) + '" aria-pressed="' + (d.slug === S.slug) + '">' +
          "<span>" + esc(d.title) + '</span><span class="chip ' + vclass(d.verdict) + '">' + esc(d.verdict) + "</span>" +
          '<span class="sub">' + (n || 0).toLocaleString() + " trades · " +
          (best.roi == null ? "–" : '<span class="' + sgn(best.roi) + '">' + pct(best.roi) + "</span> " + (h.holdout ? "holdout" : "dev")) +
          "</span></button>";
      }).join("") + "</section>";
    }).join("") || '<p class="muted" style="padding:6px 2px">Nothing here yet.</p>';
    Array.prototype.forEach.call($("rail").querySelectorAll(".item"), function (b) {
      b.onclick = function () { select(b.dataset.slug); };
    });
  }

  function select(slug) {
    S.slug = slug; S.offset = 0; S.open = null; S.result = "all"; S.tq = ""; S.period = "all";
    renderRail(); loadStrategy(slug);
  }

  function loadStrategy(slug) {
    $("main").innerHTML = '<div class="loading">Loading strategy…</div>';
    var curSlug = slug;
    api("/api/strategy/" + slug).then(function (d) {
      if (S.slug !== curSlug) return;
      S.meta = d; renderStrategy(); loadTrades();
    });
  }

  function qs() {
    return "?period=" + encodeURIComponent(S.period) + "&sport=" + encodeURIComponent(S.sport) +
      "&result=" + encodeURIComponent(S.result) + "&q=" + encodeURIComponent(S.tq);
  }

  function loadTrades() {
    var curSlug = S.slug;
    var url = "/api/strategy/" + S.slug + "/trades" + qs() + "&offset=" + S.offset + "&limit=" + S.limit +
      "&sort=" + S.sort + "&desc=" + (S.desc ? "true" : "false");
    api(url).then(function (p) { if (S.slug === curSlug) { S.page = p; renderTable(); } });
    api("/api/strategy/" + S.slug + "/equity" + qs()).then(function(d) { if (S.slug === curSlug) drawChart(d); });
  }

  function updateKPIs() {
    if (!$("kpisBox") || !S.meta) return;
    var idxEntry = S.index.find(function(x) { return x.slug === S.slug; });
    var sp = (S.sport === "all" || S.sport === "other") ? "all" : S.sport;
    var kpis = (idxEntry && idxEntry.kpis && idxEntry.kpis[sp]) ? idxEntry.kpis[sp] : (S.meta.meta.headline || {});
    var dev = kpis.dev, hold = kpis.holdout;
    $("kpisBox").innerHTML =
      kpi("Development" + (S.meta.meta.periods && S.meta.meta.periods.dev ? " · " + esc(S.meta.meta.periods.dev) : ""), dev, sp === "all") +
      kpi("Holdout" + (S.meta.meta.periods && S.meta.meta.periods.holdout ? " · " + esc(S.meta.meta.periods.holdout) : "") + " (Exploratory)", hold, sp === "all") +
      '<div class="kpi"><div class="k">Trades</div><div class="v" id="kTrades">–</div><div class="ci" id="kTradesSub">' +
      (S.meta.meta.page_sampled || S.meta.meta.truncated ? "ledger sampled for size" : "every trade") + "</div></div>" +
      '<div class="kpi"><div class="k">P&L shown</div><div class="v" id="kPnl">–</div><div class="ci" id="kRoi">on the filtered trades</div></div>';
  }

  function renderStrategy() {
    var m = S.meta.meta;
    var sportOpts = ['<option value="all">All</option>'].concat(S.meta.sports.map(function (s) {
      return '<option value="' + esc(s) + '"' + (s === S.sport ? " selected" : "") + ">" + esc(s) + "</option>"; })).join("");
    $("main").innerHTML =
      '<section class="panel"><div class="pad">' +
      '<div style="display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center">' +
      '<span class="eyebrow">' + esc(m.group || "") + '</span><span class="chip ' + vclass(m.verdict) + '">' + esc(m.verdict) + "</span></div>" +
      '<h2 style="margin-top:6px;font-size:22px">' + esc(m.title) + "</h2>" +
      '<p class="lede">' + esc(m.hypothesis) + "</p>" +
      (m.mechanism ? '<p class="muted" style="margin-top:8px">' + esc(m.mechanism) + "</p>" : "") + "</div>" +
      '<dl class="rules"><div><dt>Entry</dt><dd>' + esc(m.entry_rule) + "</dd></div>" +
      "<div><dt>Exit</dt><dd>" + esc(m.exit_rule) + "</dd></div>" +
      "<div><dt>Costs</dt><dd>" + esc(m.cost_model) + "</dd></div></dl>" +
      '<div class="kpis" id="kpisBox"></div>' +
      '<div class="chartbox"><div class="chart-head"><span class="eyebrow">Equity curve</span>' +
      '<span class="eyebrow" id="chartNote"></span></div><canvas id="eq"></canvas></div>' +
      '<div class="controls">' +
      '<label class="f" for="fp">Period</label><select id="fp"><option value="all">All</option>' +
      S.meta.periods.map(function (p) { return '<option value="' + esc(p) + '"' + (p === S.period ? " selected" : "") + ">" + esc(p) + "</option>"; }).join("") + "</select>" +
      '<label class="f" for="fs">Sport</label><select id="fs">' + sportOpts + "</select>" +
      '<label class="f" for="fr">Result</label><select id="fr"><option value="all">All</option><option value="win">Winners</option><option value="loss">Losers</option><option value="void">Voids</option></select>' +
      '<input type="search" id="fq" placeholder="Search event, side, note" style="flex:1;min-width:150px">' +
      "</div>" +
      '<div class="table-scroll"><table><thead><tr>' +
      th("#", "id") + th("Date", "date") + '<th>Event</th><th>Side bought</th>' + th("Entry", "entry_price", 1) +
      th("Stake", "stake_usd", 1) + th("Fee", "fee_usd", 1) + "<th>Exit</th>" + th("Payout", "payout", 1) +
      th("P&L", "pnl_usd", 1) + th("ROI", "roi_deployed", 1) +
      '</tr></thead><tbody id="tb"><tr><td colspan="11" class="loading">Loading trades…</td></tr></tbody></table></div>' +
      '<div class="pager"><span id="pinfo"></span><span><button id="prev">Previous</button> <button id="next">Next</button></span></div>' +
      '<div class="notes">' +
      (m.review ? "<h3>What review found</h3><p>" + esc(m.review) + "</p>" : "") +
      (m.caveats && m.caveats.length ? '<h3 style="margin-top:12px">Caveats</h3><ul>' + m.caveats.map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("") + "</ul>" : "") +
      '<p class="paths">' + esc(m.report_path || "") + (m.code_path ? " · " + esc(m.code_path) : "") + "</p></div></section>";

    updateKPIs();
    $("fp").onchange = function () { S.period = this.value; S.offset = 0; loadTrades(); };
    $("fs").onchange = function () { S.sport = this.value; S.offset = 0; updateKPIs(); loadTrades(); };
    $("fr").onchange = function () { S.result = this.value; S.offset = 0; loadTrades(); };
    var t; $("fq").oninput = function () { var v = this.value; clearTimeout(t); t = setTimeout(function () { S.tq = v; S.offset = 0; loadTrades(); }, 250); };
    $("prev").onclick = function () { S.offset = Math.max(0, S.offset - S.limit); S.open = null; loadTrades(); };
    $("next").onclick = function () { S.offset = S.offset + S.limit; S.open = null; loadTrades(); };
    Array.prototype.forEach.call(document.querySelectorAll("th[data-sort]"), function (h) {
      h.onclick = function () {
        if (S.sort === h.dataset.sort) S.desc = !S.desc; else { S.sort = h.dataset.sort; S.desc = true; }
        S.offset = 0; loadTrades();
      };
    });
  }

  function th(label, sort, right) { return '<th class="' + (right ? "r" : "") + '" data-sort="' + sort + '" title="sort">' + label + "</th>"; }
  function kpi(label, h, isGlobal) {
    if (!h) return '<div class="kpi"><div class="k">' + label + '</div><div class="v">–</div><div class="ci">no bets in this window</div></div>';
    var extra = "";
    if (isGlobal && h.ci_lo != null) extra = " · CI " + pct(h.ci_lo) + " to " + pct(h.ci_hi);
    return '<div class="kpi"><div class="k">' + label + '</div><div class="v ' + sgn(h.roi) + '">' + pct(h.roi) + "</div>" +
      '<div class="ci">' + (h.bets || 0).toLocaleString() + " bets" + extra + "</div></div>";
  }

  function renderTable() {
    var p = S.page, c = {}; p.columns.forEach(function (n, i) { c[n] = i; });
    $("kTrades").textContent = p.total.toLocaleString();
    $("kPnl").textContent = usd(p.pnl); $("kPnl").className = "v " + sgn(p.pnl);
    $("kRoi").textContent = (p.roi == null ? "" : pct(p.roi) + " per $ deployed · ") + p.wins.toLocaleString() + " winners";
    $("pinfo").textContent = p.total ? (p.offset + 1).toLocaleString() + "–" + Math.min(p.offset + p.limit, p.total).toLocaleString() + " of " + p.total.toLocaleString() + " trades" : "no trades match";
    $("prev").disabled = p.offset <= 0; $("next").disabled = p.offset + p.limit >= p.total;
    $("tb").innerHTML = p.rows.map(function (r) {
      var open = S.open === r[c.id];
      var exitTxt = r[c.exit_kind] === "resolution"
        ? (r[c.exit_price] === 1 ? "won" : r[c.exit_price] === 0 ? "lost" : "void " + cents(r[c.exit_price]))
        : esc(r[c.exit_kind]) + " " + cents(r[c.exit_price]);
      var row = '<tr class="t" data-id="' + r[c.id] + '" aria-expanded="' + open + '">' +
        '<td class="num">' + r[c.id] + "</td><td class=\"num\">" + esc(r[c.date]) + "</td>" +
        '<td class="wrap">' + esc(r[c.event]) + '</td><td class="wrap">' + esc(r[c.side]) + "</td>" +
        '<td class="r num">' + cents(r[c.entry_price]) + '</td><td class="r num">' + usd(r[c.stake_usd]) + "</td>" +
        '<td class="r num">' + (r[c.fee_usd] ? usd(r[c.fee_usd]) : "–") + "</td><td>" + exitTxt + "</td>" +
        '<td class="r num">' + usd(r[c.payout]) + '</td><td class="r num ' + sgn(r[c.pnl_usd]) + '">' + usd(r[c.pnl_usd]) + "</td>" +
        '<td class="r num ' + sgn(r[c.roi_deployed]) + '">' + pct(r[c.roi_deployed]) + "</td></tr>";
      if (!open) return row;
      var shares = r[c.entry_price] ? (r[c.stake_usd] / r[c.entry_price]).toFixed(1) : "–";
      return row + '<tr class="detail"><td colspan="11"><div class="walk">' +
        "<div><h4>Signal</h4><p>" + esc(r[c.note] || "—") + "</p></div>" +
        '<div><h4>Entered</h4><p class="big">' + cents(r[c.entry_price]) + " per share</p><p>" + when(r[c.entry_ts]) +
        " · " + usd(r[c.stake_usd]) + " = " + shares + " shares" + (r[c.fee_usd] ? " · fee " + usd(r[c.fee_usd]) : " · no taker fee") + "</p></div>" +
        '<div><h4>Exited</h4><p class="big">' + esc(r[c.exit_kind]) + " at " + cents(r[c.exit_price]) + "</p><p>" +
        when(r[c.exit_ts]) + " · returned " + usd(r[c.payout]) + "</p></div>" +
        '<div><h4>Result</h4><p class="big ' + sgn(r[c.pnl_usd]) + '">' + usd(r[c.pnl_usd]) + " · " + pct(r[c.roi_deployed]) + "</p><p>" +
        esc(r[c.sport] || "") + (r[c.league] ? " · " + esc(r[c.league]) : "") + " · " + esc(r[c.period]) + "</p></div>" +
        "</div></td></tr>";
    }).join("") || '<tr><td colspan="11" class="loading">No trades match these filters.</td></tr>';
    Array.prototype.forEach.call($("tb").querySelectorAll("tr.t"), function (tr) {
      tr.onclick = function () { var id = Number(tr.dataset.id); S.open = S.open === id ? null : id; renderTable(); };
    });
  }

  function drawChart(d) {
    var cv = $("eq"); if (!cv) return;
    var css = getComputedStyle(document.documentElement);
    var ink3 = css.getPropertyValue("--ink-3").trim(), line = css.getPropertyValue("--line-strong").trim(),
      good = css.getPropertyValue("--good").trim(), bad = css.getPropertyValue("--bad").trim(),
      accent = css.getPropertyValue("--accent").trim();
    var dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = 210;
    cv.width = W * dpr; cv.height = H * dpr;
    var g = cv.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
    var pts = d.points || [];
    $("chartNote").textContent = pts.length ? "cumulative P&L · " + (d.n || 0).toLocaleString() + " trades" : "";
    if (!pts.length) return;
    var ys = pts.map(function (p) { return p[1]; });
    var lo = Math.min(0, Math.min.apply(null, ys)), hi = Math.max(0, Math.max.apply(null, ys));
    var padL = 60, padR = 12, padT = 12, padB = 20, span = (hi - lo) || 1, n = d.n || pts.length;
    var X = function (i) { return padL + (W - padL - padR) * (n < 2 ? .5 : i / (n - 1)); };
    var Y = function (v) { return padT + (H - padT - padB) * (1 - (v - lo) / span); };
    g.font = '11px "IBM Plex Mono", monospace'; g.textAlign = "right";
    [hi, (hi + lo) / 2, lo].forEach(function (v) {
      var y = Y(v); g.strokeStyle = line; g.globalAlpha = .5; g.beginPath(); g.moveTo(padL, y); g.lineTo(W - padR, y); g.stroke(); g.globalAlpha = 1;
      g.fillStyle = ink3; g.fillText((v < 0 ? "−$" : "$") + Math.abs(Math.round(v)).toLocaleString(), padL - 8, y + 3.5);
    });
    g.strokeStyle = ink3; g.globalAlpha = .85; g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(W - padR, Y(0)); g.stroke(); g.globalAlpha = 1;
    if (d.holdout_at != null && d.holdout_at > 0) {
      var hx = X(d.holdout_at); g.strokeStyle = accent; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(hx, padT); g.lineTo(hx, H - padB); g.stroke(); g.setLineDash([]);
      g.fillStyle = accent; g.textAlign = "left"; g.fillText("holdout →", hx + 5, padT + 10);
    }
    g.lineWidth = 2; g.strokeStyle = ys[ys.length - 1] >= 0 ? good : bad; g.beginPath();
    pts.forEach(function (p, i) { var x = X(p[0]), y = Y(p[1]); if (i === 0) g.moveTo(x, y); else g.lineTo(x, y); });
    g.stroke();
    if (d.dates && d.dates.length) {
      g.fillStyle = ink3; g.textAlign = "left"; g.fillText(d.dates[0], padL, H - 5);
      g.textAlign = "right"; g.fillText(d.dates[d.dates.length - 1], W - padR, H - 5);
    }
  }

  api("/api/index").then(function (d) {
    S.index = d.strategies; S.tabs = d.tabs;
    var trades = 0, dead = 0, prof = 0;
    S.index.forEach(function (x) { trades += x.n_total_trades || 0; if (vclass(x.verdict) === "dead") dead++; if (vclass(x.verdict) === "profitable") prof++; });
    document.getElementById("facts").innerHTML =
      '<div><b class="num">' + S.index.length + "</b>strategies</div>" +
      '<div><b class="num">' + trades.toLocaleString() + "</b>trades</div>" +
      '<div><b class="num">' + dead + "/" + S.index.length + "</b>dead after costs</div>" +
      '<div><b class="num">' + prof + "</b>profitable</div>";
    renderTabs(); renderRail();
    if (S.index.length) select(S.index[0].slug);
    document.getElementById("find").oninput = function () { S.q = this.value; renderRail(); };
    window.addEventListener("resize", function () { if (S.slug) api("/api/strategy/" + S.slug + "/equity" + qs()).then(drawChart); });
  }).catch(function (e) { document.getElementById("main").innerHTML = '<div class="loading">API error: ' + e.message + "</div>"; });
})();
