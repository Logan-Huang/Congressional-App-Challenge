// Water Quality Tracker front end. Plain JavaScript, no build step, no libraries.
//
// Rules this file follows:
//  * Text from the API is only ever added with textContent (never innerHTML), so
//    odd characters in EPA data can't break or hijack the page.
//  * Status is never shown by color alone: every status has its own icon shape and words.
//  * Charts are small hand-built SVGs, each with a text/table alternative.
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var form = document.getElementById("lookup-form");
  var input = document.getElementById("q");
  var qLabel = document.getElementById("q-label");
  var errorBox = document.getElementById("q-error");
  var submitBtn = document.getElementById("submit");
  var results = document.getElementById("results");

  var mode = "zip";
  var runId = 0; // bumped on every search so late answers from an old search are ignored

  // ---------- tiny DOM helpers ----------

  // el("div", {class: "x", text: "hi"}, [children]) -> element. Uses textContent for text.
  function el(tag, props, kids) {
    var node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      if (k === "text") node.textContent = props[k];
      else if (k === "class") node.className = props[k];
      else node.setAttribute(k, props[k]);
    });
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(kid); });
    return node;
  }

  function svg(tag, attrs, kids) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(kid); });
    return node;
  }

  // Icon paths (24x24, stroked). Static strings from this file only.
  var ICONS = {
    green: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M7.8 12.4l3 3 5.6-6.2"],
    yellow: ["M12 3.5l9.5 16.5h-19z", "M12 10v4.5", "M12 17.4v.1"],
    red: ["M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3z", "M12 7.5v5.5", "M12 16.5v.1"],
    info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 11v5.2", "M12 7.8v.1"],
    phone: ["M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"],
    mail: ["M3 6h18v12H3z", "M3 7l9 6.5L21 7"],
    place: ["M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z", "M12 7.8a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4z"],
    chevron: ["M6 9l6 6 6-6"],
    neutral: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M8.5 12h7"]
  };

  function icon(name, cls) {
    var s = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", "class": cls || "" });
    (ICONS[name] || []).forEach(function (d) { s.appendChild(svg("path", { d: d })); });
    return s;
  }

  // ---------- small formatting helpers ----------

  function fmtInt(n) { return Number(n).toLocaleString("en-US"); }

  function fmtNum(n, digits) {
    var v = Number(n);
    return v.toLocaleString("en-US", { maximumFractionDigits: digits == null ? 1 : digits });
  }

  function fmtPhone(raw) {
    var d = String(raw || "").replace(/\D/g, "");
    if (d.length === 10) return "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6);
    return d.length ? d : "";
  }

  // Only let http(s) links through. WHY: link values come from data files.
  function safeUrl(u) { return /^https:\/\//i.test(String(u || "")) ? u : null; }

  function statusIconName(status) { return status === "red" ? "red" : status === "yellow" ? "yellow" : "green"; }

  // ---------- search form ----------

  function setMode(next) {
    mode = next;
    document.getElementById("mode-" + next).checked = true;
    if (next === "zip") {
      input.setAttribute("inputmode", "numeric");
      input.setAttribute("maxlength", "5");
      input.setAttribute("autocomplete", "postal-code");
      input.setAttribute("placeholder", "Enter a 5-digit zip code");
      qLabel.textContent = "Enter your zip code";
    } else {
      input.setAttribute("inputmode", "text");
      input.setAttribute("maxlength", "200");
      input.setAttribute("autocomplete", "street-address");
      input.setAttribute("placeholder", "e.g. 123 Main St, Flint, MI");
      qLabel.textContent = "Enter your street address, with city and state";
    }
  }

  function showFieldError(msg) {
    errorBox.hidden = !msg;
    errorBox.textContent = msg || "";
    if (msg) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  // Returns an error message, or "" if the value looks fine.
  function checkInput(m, value) {
    if (m === "zip") return /^\d{5}$/.test(value) ? "" : "Please enter a 5-digit zip code.";
    if (value.length < 5) return "Please enter a street address, like 123 Main St, Flint, MI.";
    if (value.length > 200) return "That address is too long. Please shorten it.";
    return "";
  }

  document.querySelectorAll('input[name="mode"]').forEach(function (r) {
    r.addEventListener("change", function () {
      setMode(r.value);
      input.value = "";
      showFieldError("");
      input.focus();
    });
  });

  // Zip box: drop anything that isn't a digit as people type or paste.
  input.addEventListener("input", function () {
    if (mode === "zip") {
      var cleaned = input.value.replace(/\D/g, "").slice(0, 5);
      if (cleaned !== input.value) input.value = cleaned;
    }
    if (!errorBox.hidden) showFieldError("");
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    search(mode, input.value.trim(), true);
  });

  document.querySelectorAll(".chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      setMode(chip.getAttribute("data-mode"));
      input.value = chip.getAttribute("data-q");
      search(mode, input.value, true);
    });
  });

  // ---------- URL state (?zip=... or ?address=...) ----------

  function urlFor(m, value) { return "?" + m + "=" + encodeURIComponent(value); }

  function readUrl() {
    var p = new URLSearchParams(window.location.search);
    if (p.get("zip")) return { mode: "zip", value: p.get("zip").trim() };
    if (p.get("address")) return { mode: "address", value: p.get("address").trim() };
    return null;
  }

  window.addEventListener("popstate", function () {
    var q = readUrl();
    if (q) { setMode(q.mode); input.value = q.value; search(q.mode, q.value, false); }
    else { runId++; results.replaceChildren(); input.value = ""; showFieldError(""); document.title = "Water Quality Tracker"; setBusy(false); }
  });

  // ---------- search flow ----------

  // One place that turns the busy state on/off, so no code path can leave the button stuck.
  function setBusy(busy) {
    submitBtn.disabled = busy;
    results.setAttribute("aria-busy", busy ? "true" : "false");
  }

  // Give up on a request after this long so the page never spins forever.
  var REQUEST_TIMEOUT_MS = 15000;

  function fetchWithTimeout(url) {
    if (typeof AbortController === "undefined") return fetch(url);
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, REQUEST_TIMEOUT_MS);
    return fetch(url, { signal: ctl.signal }).then(
      function (r) { clearTimeout(timer); return r; },
      function (e) { clearTimeout(timer); throw e; });
  }

  function search(m, value, push) {
    var problem = checkInput(m, value);
    showFieldError(problem);
    if (problem) { input.focus(); return; }

    var myRun = ++runId;
    if (push && window.location.search !== urlFor(m, value)) {   // same search twice: no extra Back step
      try { history.pushState(null, "", urlFor(m, value)); } catch (err) { /* file:// etc: skip */ }
    }
    setBusy(true);
    showLoading();
    results.scrollIntoView({ behavior: "smooth", block: "start" });

    fetchWithTimeout("/api/lookup?" + m + "=" + encodeURIComponent(value))
      .then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
      })
      .then(function (r) {
        if (myRun !== runId) return;
        if (!r.ok) { showFieldError(r.data.error || "Please check what you entered."); showMessage("Please check your search", r.data.error || "That doesn't look right."); return; }
        if (!r.data.found) { showMessage("No results", r.data.message || "We couldn't find water data for that search."); return; }
        renderResults(r.data, myRun);
      })
      .catch(function () {
        if (myRun !== runId) return;
        showMessage("We couldn't reach the server", "Check your internet connection and try again.");
      })
      .then(function () {
        // A newer search or Back to the empty page owns the busy state after this one goes stale.
        if (myRun !== runId) return;
        setBusy(false);
      });
  }

  // ---------- loading + message states ----------

  function skelBlock(lines) {
    var box = el("div");
    box.appendChild(el("div", { class: "skel skel-title" }));
    for (var i = 0; i < lines; i++) box.appendChild(el("div", { class: "skel skel-line", style: "width:" + (92 - i * 14) + "%" }));
    box.appendChild(el("div", { class: "skel skel-block" }));
    return box;
  }

  function showLoading() {
    var wrap = el("div");
    wrap.appendChild(el("div", { class: "loading-note", role: "status" }, [el("span", { class: "spinner" }), el("span", { text: "Checking EPA records..." })]));
    var verdict = el("div", { class: "card" });
    var head = el("div", { class: "status-row" }, [el("div", { class: "skel skel-circle" }), el("div", { style: "flex:1" }, [el("div", { class: "skel skel-title" })])]);
    verdict.appendChild(head);
    verdict.appendChild(el("div", { class: "skel skel-line", style: "width:70%;margin-top:22px" }));
    verdict.appendChild(el("div", { class: "skel skel-line", style: "width:90%" }));
    verdict.appendChild(el("div", { class: "skel skel-line", style: "width:80%" }));
    wrap.appendChild(verdict);
    var grid = el("div", { class: "grid" });
    for (var i = 0; i < 4; i++) grid.appendChild(el("div", { class: "card" }, [skelBlock(2)]));
    wrap.appendChild(grid);
    results.replaceChildren(wrap);
    submitBtn.disabled = true;
  }

  function showMessage(title, text) {
    submitBtn.disabled = false;
    results.setAttribute("aria-busy", "false");
    var card = el("div", { class: "card message reveal" }, [
      el("div", { class: "m-icon" }, [icon("info")]),
      el("h3", { text: title }),
      el("p", { text: text }),
      el("p", { text: "You can try one of the example searches above." })
    ]);
    results.replaceChildren(card);
  }

  // ---------- results ----------

  var SOURCE_NOTES = {
    live: "Live from EPA records",
    cache: "Saved copy of recent EPA records",
    fallback: "Saved sample data (live EPA records were not available)"
  };

  function renderResults(data, myRun) {
    var q = data.query || {};
    var top = data.systems[0];
    var frag = el("div");

    var what = q.type === "address"
      ? (q.matched_address ? "Results for " + q.matched_address : "Results for your address")
      : "Results for zip code " + (data.zip || q.input || "");
    frag.appendChild(el("div", { class: "results-head" }, [
      el("strong", { text: what }),
      el("span", { text: SOURCE_NOTES[data.source] || "EPA records" })
    ]));

    frag.appendChild(verdictCard(top, q));
    var cards = detailGrid(top);
    frag.appendChild(cards.grid);

    if (data.systems.length > 1) frag.appendChild(othersList(data.systems.slice(1), q, myRun));

    results.replaceChildren(frag);
    document.title = top.name + " | Water Quality Tracker";
    // Move keyboard/screen-reader focus to the answer (preventScroll: search() already scrolled).
    var heading = results.querySelector(".status-label");
    if (heading) { heading.setAttribute("tabindex", "-1"); try { heading.focus({ preventScroll: true }); } catch (err) { /* old browser */ } }
    loadDetails(top, q, myRun, function (d) { fillDetailCards(cards, top, d); }, function () { cards.removeAll(); });
  }

  function verdictCard(sys, q) {
    var hasMap = Array.isArray(sys.boundary) && sys.boundary.length > 0;
    var card = el("article", { class: "card verdict reveal " + sys.status + (hasMap ? " has-map" : "") });
    var main = el("div");

    main.appendChild(el("div", { class: "status-row" }, [
      el("div", { class: "status-icon" }, [icon(statusIconName(sys.status))]),
      el("h2", { class: "status-label", text: sys.status_label })
    ]));
    main.appendChild(el("h3", { class: "sys-name", text: sys.name }));

    var meta = [];
    if (sys.city || sys.state) meta.push([sys.city, sys.state].filter(Boolean).join(", "));
    if (sys.population_served) meta.push("Serves about " + fmtInt(sys.population_served) + " people");
    if (meta.length) main.appendChild(el("p", { class: "sys-meta", text: meta.join(" · ") }));

    main.appendChild(el("div", { class: "chip-row" }, [
      el("span", { class: "tag" }, [icon("place"), document.createTextNode(sys.match_label)])
    ]));

    var sentences = el("div", { class: "sentences" });
    (sys.sentences || []).forEach(function (s) { sentences.appendChild(el("p", { text: s })); });
    main.appendChild(sentences);
    card.appendChild(main);

    if (hasMap) card.appendChild(mapBox(sys, q));
    return card;
  }

  // Draw the service-area outline with a pin at the searched point.
  function mapBox(sys, q) {
    var W = 280, H = 210, PAD = 18;
    var pts = [];
    sys.boundary.forEach(function (ring) { ring.forEach(function (p) { pts.push(p); }); });
    var havePin = typeof q.lat === "number" && typeof q.lon === "number";
    if (havePin) pts.push([q.lon, q.lat]);
    var minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
    pts.forEach(function (p) {
      minLon = Math.min(minLon, p[0]); maxLon = Math.max(maxLon, p[0]);
      minLat = Math.min(minLat, p[1]); maxLat = Math.max(maxLat, p[1]);
    });
    // WHY cos(lat): a degree of longitude is shorter than a degree of latitude away from the equator.
    var k = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    var w = Math.max((maxLon - minLon) * k, 1e-6), h = Math.max(maxLat - minLat, 1e-6);
    var scale = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / h);
    var offX = (W - w * scale) / 2, offY = (H - h * scale) / 2;
    function px(p) { return [offX + (p[0] - minLon) * k * scale, offY + (maxLat - p[1]) * scale]; }

    var d = sys.boundary.map(function (ring) {
      return ring.map(function (p, i) { var xy = px(p); return (i ? "L" : "M") + xy[0].toFixed(1) + " " + xy[1].toFixed(1); }).join("") + "Z";
    }).join("");

    var label = "Outline of the water system's service area" + (havePin ? ", with a pin at your search location" : "");
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": label }, [
      svg("path", { d: d, "class": "map-area", "fill-rule": "evenodd" })
    ]);
    if (havePin) {
      var c = px([q.lon, q.lat]);
      s.appendChild(svg("circle", { cx: c[0], cy: c[1], r: 9, "class": "map-pin-ring" }));
      s.appendChild(svg("circle", { cx: c[0], cy: c[1], r: 5.5, "class": "map-pin" }));
    }
    var cap = "Service area (outline)" + (havePin ? " and your " + (q.type === "address" ? "address" : "zip code's center") + " (dot)" : "");
    if (sys.boundary_quality === "modeled") cap += ". EPA estimated this boundary.";
    return el("figure", { class: "map-box", style: "margin:0" }, [s, el("figcaption", { class: "map-cap", text: cap })]);
  }

  // ---------- detail cards ----------

  function card(title, kicker, extraClass) {
    var c = el("section", { class: "card reveal " + (extraClass || "") });
    c.appendChild(el("h3", { text: title }));
    if (kicker) c.appendChild(el("p", { class: "kicker", text: kicker }));
    var body = el("div", { class: "card-body" });
    c.appendChild(body);
    c.body = body;
    return c;
  }

  function skeletonCard(title) {
    var c = card(title);
    c.classList.add("is-loading");
    c.body.appendChild(el("div", { class: "skel skel-line", style: "width:85%" }));
    c.body.appendChild(el("div", { class: "skel skel-line", style: "width:70%" }));
    c.body.appendChild(el("div", { class: "skel skel-block" }));
    return c;
  }

  function detailGrid(sys) {
    var grid = el("div", { class: "grid" });
    var track = trackRecordCard(sys);
    var compare = skeletonCard("How it compares");
    var lead = skeletonCard("Lead pipes");
    var state = el("div", { hidden: "" }); // only becomes a card if the state has a report
    var guide = guidanceCard(sys);
    var contact = contactCard(sys);
    track.classList.add("span-2");
    [track, compare, lead, state, guide, contact].forEach(function (c) { if (c) grid.appendChild(c); });
    // Cards that depend on /api/details are swapped in (or removed) when it answers.
    return {
      grid: grid, compare: compare, lead: lead, state: state,
      removeAll: function () { [compare, lead, state].forEach(function (c) { if (c.parentNode) c.remove(); }); }
    };
  }

  function loadDetails(sys, q, myRun, onData, onFail) {
    var p = new URLSearchParams();
    p.set("pwsid", sys.pwsid);
    p.set("state", sys.state || "");
    if (typeof q.lat === "number") p.set("lat", q.lat);
    if (typeof q.lon === "number") p.set("lon", q.lon);
    if (q.zip) p.set("zip", q.zip);
    if (q.tract) p.set("tract", q.tract);
    p.set("name", sys.name || "");
    // Facts the server cannot see on its own (see _system_from_args in app/main.py).
    if (sys.signals) {
      p.set("hb", sys.signals.current_health_based ? "1" : "0");
      if (sys.signals.lsl_inventory) p.set("lsl", "1");
    }
    var lead90 = sys.lead_90th && Number(sys.lead_90th.value_mg_l);
    if (isFinite(lead90) && lead90 >= 0 && sys.lead_90th.value_mg_l !== null) p.set("lead", String(lead90));
    fetchWithTimeout("/api/details?" + p.toString())
      .then(function (r) { if (!r.ok) throw new Error("bad"); return r.json(); })
      .then(function (d) { if (myRun === runId) onData(d); })
      .catch(function () { if (myRun === runId) onFail(); });
  }

  function replaceCard(oldCard, newCard) {
    if (!oldCard.parentNode) return;
    if (newCard) oldCard.replaceWith(newCard);
    else oldCard.remove(); // data missing: hide the card gracefully
  }

  function fillDetailCards(cards, sys, d) {
    replaceCard(cards.compare, compareCard(sys, d));
    replaceCard(cards.lead, leadCard(d));
    replaceCard(cards.state, stateCard(d));
  }

  // ----- 10-year track record -----

  function niceStep(max, ticks) { return Math.max(1, Math.ceil(max / ticks)); }

  function trackRecordCard(sys) {
    var c = card("10-year track record", "Violations by year the problem began");
    var cols = el("div", { class: "two-col" });
    var years = (sys.history && sys.history.by_year) || [];
    if (years.length) {
      var colA = el("div", { class: "chart-col" });
      colA.appendChild(yearChart(years));
      colA.appendChild(el("ul", { class: "legend" }, [
        el("li", {}, [el("span", { class: "swatch health" }), document.createTextNode("Health-based (a contaminant was above its limit)")]),
        el("li", {}, [el("span", { class: "swatch paper" }), document.createTextNode("Paperwork (testing or reporting problems)")])
      ]));
      colA.appendChild(yearTable(years));
      cols.appendChild(colA);
    }
    var lead = ((sys.history && sys.history.lead_90th) || []).filter(function (p) {
      return !isNaN(Date.parse(p.date)) && isFinite(p.value_mg_l * 1000);   // drop unusable points first
    });
    if (lead.length >= 2) {
      var colB = el("div", { class: "chart-col" });
      colB.appendChild(el("h4", { class: "sub", text: "Lead test results over time" }));
      colB.appendChild(leadChart(lead));
      colB.appendChild(el("ul", { class: "legend" }, [
        el("li", {}, [el("span", { class: "swatch dash" }), document.createTextNode("EPA action level: 15 parts per billion")])
      ]));
      colB.appendChild(leadTable(lead));
      cols.appendChild(colB);
    }
    if (cols.childNodes.length) c.body.appendChild(cols);
    if (sys.trend && sys.trend.sentence) c.body.appendChild(el("p", { class: "trend-line", text: sys.trend.sentence }));
    return cols.childNodes.length || (sys.trend && sys.trend.sentence) ? c : null;
  }

  function yearChart(years) {
    var W = 360, H = 200, L = 28, R = 6, T = 10, B = 24;
    var iw = W - L - R, ih = H - T - B;
    var maxTotal = Math.max.apply(null, years.map(function (y) { return (y.health_based || 0) + (y.other || 0); }).concat([0]));
    var step = niceStep(Math.max(maxTotal, 4), 4);
    var top = Math.ceil(Math.max(maxTotal, 4) / step) * step;
    function y(v) { return T + ih - (v / top) * ih; }

    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": "Violations per year for the last 10 years. A table follows." });
    for (var t = 0; t <= top; t += step) {
      s.appendChild(svg("line", { x1: L, x2: W - R, y1: y(t), y2: y(t), "class": t === 0 ? "axis-line" : "grid-line" }));
      s.appendChild(svg("text", { x: L - 6, y: y(t) + 3.5, "text-anchor": "end", text: String(t) }));
    }
    var slot = iw / years.length, bw = slot * 0.62;
    years.forEach(function (yr, i) {
      var x = L + i * slot + (slot - bw) / 2;
      var hb = yr.health_based || 0, ot = yr.other || 0;
      var g = svg("g", { role: "img", "aria-label": yr.year + ": " + hb + " health-based, " + ot + " paperwork" });
      g.appendChild(svg("title", { text: yr.year + ": " + hb + " health-based, " + ot + " paperwork" }));
      if (hb) g.appendChild(svg("rect", { x: x, y: y(hb), width: bw, height: y(0) - y(hb), rx: 2, "class": "seg bar-health" }));
      if (ot) g.appendChild(svg("rect", { x: x, y: y(hb + ot), width: bw, height: y(hb) - y(hb + ot), rx: 2, "class": "seg bar-paper" }));
      // Invisible full-height target so hovering the empty space above a bar still shows the tooltip.
      g.appendChild(svg("rect", { x: L + i * slot, y: T, width: slot, height: ih, fill: "transparent" }));
      s.appendChild(g);
      s.appendChild(svg("text", { x: L + i * slot + slot / 2, y: H - 7, "text-anchor": "middle", text: String(yr.year) }));
    });
    return s;
  }

  function yearTable(years) {
    var table = el("table", { class: "data-table" }, [
      el("thead", {}, [el("tr", {}, [el("th", { text: "Year" }), el("th", { text: "Health-based" }), el("th", { text: "Paperwork" })])]),
      el("tbody", {}, years.map(function (y) {
        return el("tr", {}, [el("td", { text: String(y.year) }), el("td", { text: String(y.health_based || 0) }), el("td", { text: String(y.other || 0) })]);
      }))
    ]);
    return el("details", { class: "table-view" }, [el("summary", { text: "View as a table" }), table]);
  }

  // Lead results come in mg/L; people know parts per billion (1 mg/L = 1,000 ppb).
  function leadChart(points) {
    var pts = points.map(function (p) { return { t: Date.parse(p.date), v: p.value_mg_l * 1000 }; })
      .filter(function (p) { return !isNaN(p.t) && isFinite(p.v); })
      .sort(function (a, b) { return a.t - b.t; });
    var W = 360, H = 170, L = 30, R = 14, T = 12, B = 24;
    var iw = W - L - R, ih = H - T - B;
    var maxV = Math.max.apply(null, pts.map(function (p) { return p.v; }).concat([0]));
    var step = Math.max(5, Math.ceil(Math.max(20, maxV * 1.1) / 4 / 5) * 5);
    var top = step * 4;
    var t0 = pts[0].t, t1 = pts[pts.length - 1].t, span = t1 - t0 || 1;
    function x(t) { return L + ((t - t0) / span) * iw; }
    function y(v) { return T + ih - (v / top) * ih; }

    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "group", "aria-label": "Lead test results over time compared with the 15 parts per billion action level. A table follows." });
    for (var v = 0; v <= top; v += step) {
      s.appendChild(svg("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), "class": v === 0 ? "axis-line" : "grid-line" }));
      s.appendChild(svg("text", { x: L - 6, y: y(v) + 3.5, "text-anchor": "end", text: String(v) }));
    }
    s.appendChild(svg("line", { x1: L, x2: W - R, y1: y(15), y2: y(15), "class": "action-line" }));
    s.appendChild(svg("text", { x: W - R, y: y(15) - 5, "text-anchor": "end", "class": "action-text", text: "Action level 15" }));
    s.appendChild(svg("path", { "class": "line-series", d: pts.map(function (p, i) { return (i ? "L" : "M") + x(p.t).toFixed(1) + " " + y(p.v).toFixed(1); }).join("") }));
    pts.forEach(function (p) {
      var label = new Date(p.t).toISOString().slice(0, 10) + ": " + fmtNum(p.v, 1) + " ppb";
      s.appendChild(svg("circle", { cx: x(p.t), cy: y(p.v), r: 4, "class": "dot" }, [svg("title", { text: label })]));
    });
    var last = pts[pts.length - 1];
    s.appendChild(svg("text", { x: x(last.t), y: y(last.v) - 9, "text-anchor": "end", "class": "val", text: fmtNum(last.v, 1) + " ppb" }));
    s.appendChild(svg("text", { x: L, y: H - 7, "text-anchor": "start", text: String(new Date(t0).getUTCFullYear()) }));
    s.appendChild(svg("text", { x: W - R, y: H - 7, "text-anchor": "end", text: String(new Date(t1).getUTCFullYear()) }));
    return s;
  }

  function leadTable(points) {
    var rows = points.map(function (p) {
      return el("tr", {}, [el("td", { text: p.date }), el("td", { text: fmtNum(p.value_mg_l * 1000, 1) + " ppb" })]);
    });
    var table = el("table", { class: "data-table" }, [
      el("thead", {}, [el("tr", {}, [el("th", { text: "Test date" }), el("th", { text: "Lead (90th percentile)" })])]),
      el("tbody", {}, rows)
    ]);
    return el("details", { class: "table-view" }, [el("summary", { text: "View as a table" }), table]);
  }

  // ----- comparison -----

  function rateRows(c, key) {
    var rows = [];
    if (c.state && typeof c.state[key] === "number") rows.push({ label: c.state.name || c.state.code || "State", v: c.state[key], cls: "a" });
    if (c.national && typeof c.national[key] === "number") rows.push({ label: "U.S.", v: c.national[key], cls: "b" });
    return rows;
  }

  function rateGroup(title, rows) {
    var wrap = el("div");
    wrap.appendChild(el("h4", { class: "sub", text: title }));
    var max = Math.max.apply(null, rows.map(function (r) { return r.v; }).concat([1])) * 1.15;
    rows.forEach(function (r) {
      var fill = el("div", { class: "cmp-fill " + r.cls, style: "width:" + Math.max(1, (r.v / max) * 100).toFixed(1) + "%" });
      wrap.appendChild(el("div", { class: "cmp-row" }, [
        el("span", { text: r.label }),
        el("div", { class: "cmp-bar-wrap" }, [el("div", { class: "cmp-track" }, [fill]), el("span", { class: "cmp-val", text: fmtNum(r.v, 1) + "%" })])
      ]));
    });
    return wrap;
  }

  function neighborStatus(n) {
    if (n.has_current_health_based) return { cls: "red", text: "Current health-based issue" };
    if (n.has_current_any) return { cls: "yellow", text: "Current paperwork issue" };
    if (n.has_current_health_based === false || n.has_current_any === false) return { cls: "green", text: "No current issues" };
    return { cls: "neutral", text: "Status unavailable" };   // the lookup for this one failed; do not guess
  }

  function compareCard(sys, d) {
    var c = d && d.comparison;
    if (!c) return null;
    var out = card("How it compares", "Share of water systems with a health-based violation");
    var any = false;
    var now = rateRows(c, "pct_current_health_based"), five = rateRows(c, "pct_health_based_5yr");
    if (now.length) { out.body.appendChild(rateGroup("Right now", now)); any = true; }
    if (five.length) { out.body.appendChild(rateGroup("At any time in the last 5 years", five)); any = true; }
    (d.comparison_sentences || []).forEach(function (s) { out.body.appendChild(el("p", { class: "trend-line", text: s })); any = true; });
    if (c.neighbors && c.neighbors.length) {
      any = true;
      out.body.appendChild(el("h4", { class: "sub", text: "Nearby water systems" }));
      out.body.appendChild(el("ul", { class: "neighbors" }, c.neighbors.map(function (n) {
        var st = neighborStatus(n);
        return el("li", {}, [
          el("span", { class: "n-name", text: n.name ? tidyClient(n.name) : n.pwsid }),
          n.population_served ? el("span", { class: "n-pop", text: fmtInt(n.population_served) + " people" }) : null,
          el("span", { class: "dotstat " + st.cls }, [icon(st.cls), document.createTextNode(st.text)])
        ]);
      })));
    }
    if (c.as_of) out.body.appendChild(el("p", { class: "kicker", style: "margin-top:12px", text: "State and national figures as of " + c.as_of + "." }));
    return any ? out : null;
  }

  // Neighbor names come straight from EPA in ALL CAPS; a light title-case keeps them readable.
  function tidyClient(name) {
    if (name !== name.toUpperCase()) return name;
    var small = { of: 1, the: 1, and: 1, for: 1 };
    return name.toLowerCase().split(" ").map(function (w, i) {
      return i && small[w] ? w : w.replace(/(^|[\-\/(])([a-z])/g, function (m, a, b) { return a + b.toUpperCase(); });
    }).join(" ");
  }

  // ----- lead pipes -----

  function leadCard(d) {
    var lp = d && d.lead_pipe;
    var housing = d && d.lead_housing;
    var est = d && d.lead_state_estimate;
    if (!lp && !housing && !est) return null;
    var out = card("Lead pipes", "Older homes are more likely to have lead service lines");
    if (lp) {
      var level = lp.level;
      var pill = level === "elevated"
        ? el("span", { class: "pill yellow" }, [icon("yellow"), document.createTextNode("Higher lead-pipe risk signals")])
        : level === "typical"
          ? el("span", { class: "pill neutral" }, [icon("green"), document.createTextNode("Typical lead-pipe risk signals")])
          : el("span", { class: "pill neutral" }, [icon("neutral"), document.createTextNode("Not enough information")]);
      out.body.appendChild(pill);
      (lp.sentences || []).forEach(function (s) { out.body.appendChild(el("p", { text: s, style: "margin-top:12px" })); });
    }
    if (housing && housing.median_year_built) {
      var where = housing.geo === "tract" ? "in your census tract" : "in your zip code area";
      out.body.appendChild(el("p", { class: "trend-line", text: "Median home " + where + " was built in " + housing.median_year_built + ". Lead pipes were banned in 1986." }));
    }
    if (est && est.count) {
      out.body.appendChild(el("p", { class: "kicker", style: "margin-top:12px", text: "About " + fmtInt(est.count) + " lead service lines are estimated statewide (" + (est.source || "EPA estimate") + ")." }));
    }
    out.body.appendChild(el("p", { class: "kicker", style: "margin-top:12px", text: "Not sure what your pipes are made of? Ask your utility, or check where the pipe enters your home: lead is dull gray and soft enough to scratch with a key." }));
    return out;
  }

  // ----- state report -----

  var STATE_STATUS = {
    "Failing": "red", "At-Risk": "yellow", "Potentially At-Risk": "yellow", "Not At-Risk": "green"
  };

  function stateCard(d) {
    var sr = d && d.state_report;
    if (!sr) return null;
    var out = card("State report", (sr.agency || "State agency") + (sr.program ? ": " + sr.program : ""));
    var cls = STATE_STATUS[sr.status];
    out.body.appendChild(el("span", { class: "pill " + (cls || "neutral") }, [icon(cls || "neutral"), document.createTextNode("State rating: " + (sr.status || "Not assessed"))]));
    (d.state_sentences || []).forEach(function (s) { out.body.appendChild(el("p", { text: s, style: "margin-top:12px" })); });
    if (sr.failing_since) out.body.appendChild(el("p", { class: "kicker", style: "margin-top:10px", text: "Rated failing since " + sr.failing_since + "." }));
    if (sr.state_violations && sr.state_violations.length) {
      out.body.appendChild(el("h4", { class: "sub", text: "Issues the state lists" }));
      out.body.appendChild(el("ul", {}, sr.state_violations.map(function (v) {
        return el("li", { text: v.kind + (v.analytes ? ": " + v.analytes : ""), style: "padding:3px 0" });
      })));
    }
    var url = safeUrl(sr.source_url);
    if (url) out.body.appendChild(el("p", { style: "margin-top:12px" }, [el("a", { href: url, rel: "noopener", text: "See the state's data" })]));
    return out;
  }

  // ----- guidance + contact -----

  function guidanceCard(sys) {
    var items = sys.guidance || [];
    if (!items.length) return null;
    var out = card("What you can do", "Practical steps for your household");
    out.body.appendChild(el("ol", { class: "guide" }, items.map(function (g) {
      var url = safeUrl(g.link);
      return el("li", {}, [
        el("div", { class: "g-title", text: g.title }),
        el("div", { class: "g-text", text: g.text }),
        url ? el("a", { href: url, rel: "noopener", text: "Learn more" }) : null
      ]);
    })));
    return out;
  }

  function contactCard(sys) {
    var ct = sys.contact || {};
    var out = card("Contact your utility", ct.org_name || sys.name);
    var list = el("div", { class: "contact-list" });
    var phone = fmtPhone(ct.phone);
    if (phone) list.appendChild(el("a", { class: "btn", href: "tel:" + String(ct.phone).replace(/\D/g, "") }, [icon("phone"), document.createTextNode(phone)]));
    if (ct.email && /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/.test(ct.email)) {
      list.appendChild(el("a", { class: "btn", href: "mailto:" + ct.email }, [icon("mail"), document.createTextNode(ct.email)]));
    }
    if (ct.address) list.appendChild(el("div", { class: "addr" }, [icon("place"), el("span", { text: ct.address })]));
    if (!list.childNodes.length) {
      out.body.appendChild(el("p", { text: "We don't have contact details for this utility. Look on your water bill for its phone number." }));
    } else {
      out.body.appendChild(list);
      out.body.appendChild(el("p", { class: "kicker", style: "margin-top:14px", text: "Ask for the latest Consumer Confidence Report, and whether your service line has been inspected for lead." }));
    }
    return out;
  }

  // ---------- other matched systems ----------

  function othersList(systems, q, myRun) {
    var wrap = el("section", { class: "others" });
    wrap.appendChild(el("h2", { text: "Other water systems that may serve you" }));
    systems.forEach(function (sys) {
      var loaded = false;
      var body = el("div", { class: "o-body" });
      var det = el("details", {}, [
        el("summary", {}, [
          el("span", { class: "dotstat " + sys.status }, [icon(statusIconName(sys.status)), document.createTextNode(sys.status_label)]),
          el("span", { class: "o-name" }, [
            document.createTextNode(sys.name),
            el("small", { text: sys.match_label + (sys.population_served ? " · about " + fmtInt(sys.population_served) + " people" : "") })
          ]),
          icon("chevron", "chev")
        ]),
        body
      ]);
      det.addEventListener("toggle", function () {
        if (!det.open || loaded) return;
        loaded = true;
        (sys.sentences || []).forEach(function (s) { body.appendChild(el("p", { text: s })); });
        var more = el("div");
        more.appendChild(el("div", { class: "skel skel-line", style: "width:70%;margin-top:14px" }));
        body.appendChild(more);
        loadDetails(sys, q, myRun, function (d) {
          more.replaceChildren();
          var sentences = [].concat(d.comparison_sentences || [], d.lead_pipe ? d.lead_pipe.sentences || [] : [], d.state_sentences || []);
          if (sentences.length) more.appendChild(el("h4", { class: "sub", text: "More detail" }));
          sentences.forEach(function (s) { more.appendChild(el("p", { text: s })); });
          if (sys.pwsid) more.appendChild(el("p", { class: "kicker", style: "margin-top:12px", text: "Water system ID: " + sys.pwsid }));
        }, function () { more.replaceChildren(); });
      });
      wrap.appendChild(det);
    });
    return wrap;
  }

  // ---------- start up ----------

  var initial = readUrl();
  if (initial) {
    setMode(initial.mode);
    input.value = initial.value;
    search(initial.mode, initial.value, false);
  }
})();
