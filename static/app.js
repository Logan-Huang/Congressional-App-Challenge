// Frontend logic. All API text goes in with textContent (never innerHTML) so
// nothing from the server can inject markup.
(function () {
  "use strict";

  var MAX_VISIBLE = 3; // show this many systems, hide the rest behind a toggle
  var form = document.getElementById("lookup-form");
  var input = document.getElementById("zip");
  var errorEl = document.getElementById("zip-error");
  var results = document.getElementById("results");
  var submitBtn = document.getElementById("submit");
  var requestId = 0; // so a slow old response can't overwrite a newer search

  // Icons next to the label so status is never conveyed by color alone.
  var STATUS_ICON = { green: "✓ ", yellow: "⚠ ", red: "✖ " };

  // Small helper to build elements safely (textContent only).
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function showFieldError(message) {
    errorEl.textContent = message || "";
    errorEl.hidden = !message;
    input.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function messageCard(title, message) {
    var card = el("div", "card plain");
    card.appendChild(el("h2", null, title));
    card.appendChild(el("p", null, message));
    return card;
  }

  function systemCard(system) {
    // Unknown status -> treat as yellow rather than implying "all clear".
    var status = ["green", "yellow", "red"].indexOf(system.status) >= 0 ? system.status : "yellow";
    var card = el("article", "card " + status);
    card.appendChild(el("h2", null, system.name));
    card.appendChild(el("span", "badge " + status, STATUS_ICON[status] + system.status_label));

    var meta = [];
    if (typeof system.population_served === "number") {
      meta.push("Serves about " + system.population_served.toLocaleString("en-US") + " people");
    }
    if (system.match_label) meta.push(system.match_label);
    if (meta.length) card.appendChild(el("p", "meta", meta.join(" · ")));

    (system.sentences || []).forEach(function (s) { card.appendChild(el("p", null, s)); });
    return card;
  }

  function render(data) {
    results.textContent = "";
    if (!data.found) {
      results.appendChild(messageCard("No results", data.message || "We don't have data for this zip code yet."));
      return;
    }
    var note = data.source === "live" ? "Live EPA data" :
      data.source === "cache" ? "Saved EPA data" : "Saved EPA snapshot (live data unavailable)";
    results.appendChild(el("p", "source", "Results for " + data.zip + " · " + note));

    var extra = [];
    data.systems.forEach(function (system, i) {
      var card = systemCard(system);
      if (i >= MAX_VISIBLE) { card.hidden = true; extra.push(card); }
      results.appendChild(card);
    });

    if (extra.length) {
      var toggle = el("button", "toggle", "Show " + extra.length + " more water system" + (extra.length > 1 ? "s" : ""));
      toggle.type = "button";
      toggle.addEventListener("click", function () {
        extra.forEach(function (c) { c.hidden = false; });
        toggle.remove();
      });
      results.appendChild(toggle);
    }
  }

  function showLoading() {
    results.textContent = "";
    var box = el("div", "loading");
    box.appendChild(el("span", "spinner"));
    box.appendChild(el("span", null, "Checking EPA records... this can take a few seconds."));
    results.appendChild(box);
  }

  function search(zip) {
    var mine = ++requestId;
    submitBtn.disabled = true;
    results.setAttribute("aria-busy", "true");
    showLoading();

    // WHY: the server gives up on EPA after ~20 s; if it is still silent at 30 s, stop waiting.
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 30000) : null;

    fetch("/api/lookup?zip=" + encodeURIComponent(zip), controller ? { signal: controller.signal } : undefined)
      .then(function (resp) {
        return resp.json().then(function (body) { return { ok: resp.ok, body: body }; });
      })
      .then(function (r) {
        if (mine !== requestId) return;
        if (!r.ok) {
          results.textContent = "";
          showFieldError(r.body.error || "Please enter a 5-digit zip code.");
          return;
        }
        render(r.body);
      })
      .catch(function () {
        if (mine !== requestId) return;
        results.textContent = "";
        results.appendChild(messageCard("Couldn't reach the server",
          "We couldn't complete that lookup. Check your connection and try again."));
      })
      .then(function () {
        if (timer) clearTimeout(timer);
        if (mine !== requestId) return;
        submitBtn.disabled = false;
        results.setAttribute("aria-busy", "false");
      });
  }

  // Keep only digits as the user types/pastes.
  input.addEventListener("input", function () {
    input.value = input.value.replace(/\D/g, "").slice(0, 5);
    if (!errorEl.hidden) showFieldError("");
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var zip = input.value.trim();
    if (!/^\d{5}$/.test(zip)) {
      showFieldError("Please enter a 5-digit zip code, like 48502.");
      input.focus();
      return;
    }
    showFieldError("");
    search(zip);
  });
})();
