"use strict";
(function () {
  var NS = "http://www.w3.org/2000/svg";
  var data = null;
  var busy = false;

  function h(tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") el.className = attrs[k];
      else if (k === "text") el.textContent = attrs[k];
      else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined) continue;
      el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return el;
  }

  function s(tag, attrs, text) {
    var el = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function toast(msg, err) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.className = "toast" + (err ? " err" : "");
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { t.hidden = true; }, 4500);
  }

  var SYMBOLS = { EUR: "€", USD: "$", GBP: "£" };
  function money(v) {
    var cur = (data && data.currency) || "EUR";
    var a = Math.abs(v);
    var t = a >= 1e6 ? (a / 1e6).toFixed(2) + "M" : a >= 1e3 ? Math.round(a / 1e3) + "k" : String(Math.round(a));
    return (v < 0 ? "-" : "") + (SYMBOLS[cur] || cur + " ") + t;
  }
  function pct(p) { return (p * 100).toFixed(1) + "%"; }

  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-FairSim-Request"] = "1";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || "request failed");
        return j;
      });
    });
  }

  // ---- input widgets bound to the data object ----

  function numInput(obj, key, opts) {
    opts = opts || {};
    var scale = opts.percent ? 100 : 1;
    var inp = h("input", { type: "number", step: opts.step || "any", min: opts.min !== undefined ? String(opts.min) : null, max: opts.max !== undefined ? String(opts.max) : null });
    inp.value = obj[key] === undefined || obj[key] === null ? "" : String(Math.round(obj[key] * scale * 1e6) / 1e6);
    inp.addEventListener("input", function () {
      var v = parseFloat(inp.value);
      if (inp.value === "" && opts.optional) delete obj[key];
      else if (isFinite(v)) obj[key] = v / scale;
    });
    return inp;
  }

  function field(label, input) { return h("label", { class: "f" }, h("span", { text: label }), input); }

  function triple(label, obj, key, opts) {
    if (typeof obj[key] !== "object" || obj[key] === null) { var v = obj[key] || 0; obj[key] = { min: v, mode: v, max: v }; }
    var t = obj[key];
    return h("div", null, h("div", { class: "sub", text: label }),
      h("div", { class: "grid3" }, field("min", numInput(t, "min", opts)), field("likely", numInput(t, "mode", opts)), field("max", numInput(t, "max", opts))));
  }

  function renderInputs() {
    var box = document.getElementById("inputs");
    box.textContent = "";
    if (!data) return;
    box.appendChild(h("h2", { text: "Setup" }));
    var org = h("input", { maxlength: "100", value: data.organisation || "" });
    org.addEventListener("input", function () { data.organisation = org.value; });
    var cur = h("input", { maxlength: "5", value: data.currency || "EUR" });
    cur.addEventListener("input", function () { data.currency = cur.value || "EUR"; });
    var iter = h("select");
    [5000, 10000, 25000, 50000].forEach(function (n) {
      var o = h("option", { value: String(n), text: n.toLocaleString("en-US") + " iterations" });
      if (n === data.iterations) o.selected = true;
      iter.appendChild(o);
    });
    iter.addEventListener("change", function () { data.iterations = parseInt(iter.value, 10); });
    box.appendChild(h("div", { class: "grid2" }, field("Organisation", org), field("Currency", cur), field("Simulation size", iter), field("Seed", numInput(data, "seed", { step: "1", min: 0 })),
      field("Risk appetite (annual loss)", numInput(data, "appetite", { min: 0, optional: true })), field("Annual control budget", numInput(data, "budget", { min: 0, optional: true }))));

    box.appendChild(h("h2", { text: "Scenarios" }));
    data.scenarios.forEach(function (sc, idx) { box.appendChild(scenarioCard(sc, idx)); });
    box.appendChild(h("button", { class: "btn sec small", type: "button", onclick: function () {
      var n = data.scenarios.length + 1;
      data.scenarios.push({ id: "scenario_" + n, name: "New scenario " + n, tef: { min: 0.5, mode: 1, max: 2 }, vulnerability: { min: 0.1, mode: 0.2, max: 0.4 },
        primary_loss: { min: 10000, mode: 50000, max: 200000 }, secondary: { probability: 0.2, loss: { min: 10000, mode: 40000, max: 150000 } } });
      renderInputs();
    } }, "Add scenario"));

    box.appendChild(h("h2", { text: "Controls" }));
    (data.controls || []).forEach(function (c, idx) { box.appendChild(controlCard(c, idx)); });
    box.appendChild(h("button", { class: "btn sec small", type: "button", onclick: function () {
      data.controls = data.controls || [];
      var n = data.controls.length + 1;
      data.controls.push({ id: "control_" + n, name: "New control " + n, annual_cost: 20000, applies_to: [], vulnerability_reduction: 0.2 });
      renderInputs();
    } }, "Add control"));
  }

  function scenarioCard(sc, idx) {
    sc.secondary = sc.secondary || { probability: 0, loss: { min: 0, mode: 0, max: 0 } };
    var name = h("input", { maxlength: "100", value: sc.name || "" });
    name.addEventListener("input", function () { sc.name = name.value; });
    var body = h("div", { class: "body" },
      field("Name", name),
      triple("Threat events per year", sc, "tef", { min: 0 }),
      triple("Probability an event becomes a loss (0 to 1)", sc, "vulnerability", { min: 0, max: 1 }),
      triple("Primary loss per event", sc, "primary_loss", { min: 0 }),
      h("div", { class: "grid2" }, field("Chance of secondary loss (0 to 1)", numInput(sc.secondary, "probability", { min: 0, max: 1 }))),
      triple("Secondary loss (fines, litigation, churn)", sc.secondary, "loss", { min: 0 }),
      h("button", { class: "btn sec small", type: "button", onclick: function () {
        data.scenarios.splice(idx, 1);
        (data.controls || []).forEach(function (c) { if (c.applies_to) c.applies_to = c.applies_to.filter(function (x) { return x !== sc.id; }); });
        renderInputs();
      } }, "Remove scenario"));
    var d = h("details", { class: "card" }, h("summary", null, h("span", { text: sc.name }), h("span", { class: "note", text: sc.id })), body);
    if (idx === 0) d.open = true;
    return d;
  }

  function controlCard(c, idx) {
    var name = h("input", { maxlength: "100", value: c.name || "" });
    name.addEventListener("input", function () { c.name = name.value; });
    var checks = h("div", { class: "checks" });
    var all = c.applies_to === undefined || c.applies_to === null;
    data.scenarios.forEach(function (sc) {
      var cb = h("input", { type: "checkbox" });
      cb.checked = all || c.applies_to.indexOf(sc.id) >= 0;
      cb.addEventListener("change", function () {
        var cur = (c.applies_to || data.scenarios.map(function (x) { return x.id; })).filter(function (x) { return x !== sc.id; });
        if (cb.checked) cur.push(sc.id);
        c.applies_to = cur;
      });
      checks.appendChild(h("label", null, cb, sc.name));
    });
    var body = h("div", { class: "body" },
      field("Name", name),
      h("div", { class: "grid2" }, field("Annual cost", numInput(c, "annual_cost", { min: 0 }))),
      h("div", { class: "grid3" },
        field("Fewer events (%)", numInput(c, "frequency_reduction", { percent: true, min: 0, max: 100 })),
        field("Less likely to succeed (%)", numInput(c, "vulnerability_reduction", { percent: true, min: 0, max: 100 })),
        field("Smaller loss (%)", numInput(c, "magnitude_reduction", { percent: true, min: 0, max: 100 }))),
      h("div", null, h("div", { class: "sub", text: "Applies to" }), checks),
      h("button", { class: "btn sec small", type: "button", onclick: function () { data.controls.splice(idx, 1); renderInputs(); } }, "Remove control"));
    return h("details", { class: "card" }, h("summary", null, h("span", { text: c.name }), h("span", { class: "note", text: money(c.annual_cost || 0) + "/yr" })), body);
  }

  // ---- results ----

  function lecChart(r) {
    var W = 720, H = 320, pl = 56, pr = 16, pt = 14, pb = 36;
    var curves = [["Today", r.lec, "#e5604f"]];
    if (r.bundle && r.bundle.controls.length) curves.push(["With recommended controls", r.bundle.lec_after, "#35c27a"]);
    var xmax = 1, pmax = 0;
    curves.forEach(function (c) { xmax = Math.max(xmax, c[1][c[1].length - 1][0]); c[1].forEach(function (p) { pmax = Math.max(pmax, p[1]); }); });
    if (r.appetite && r.appetite < xmax * 1.5) xmax = Math.max(xmax, r.appetite * 1.1);
    pmax = Math.min(1, (pmax || 1) * 1.1);
    function X(x) { return pl + (W - pl - pr) * x / xmax; }
    function Y(p) { return pt + (H - pt - pb) * (1 - p / pmax); }
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Loss exceedance curve", width: "100%" });
    for (var i = 0; i <= 4; i++) {
      var p = pmax * i / 4, x = xmax * i / 4;
      svg.appendChild(s("line", { x1: pl, x2: W - pr, y1: Y(p), y2: Y(p), class: "grid" }));
      svg.appendChild(s("text", { x: pl - 6, y: Y(p) + 4, "text-anchor": "end" }, pct(p)));
      svg.appendChild(s("text", { x: X(x), y: H - 14, "text-anchor": i === 4 ? "end" : "middle" }, money(x)));
    }
    curves.forEach(function (c, k) {
      svg.appendChild(s("polyline", { fill: "none", stroke: c[2], "stroke-width": "2.5", points: c[1].map(function (q) { return X(q[0]).toFixed(1) + "," + Y(q[1]).toFixed(1); }).join(" ") }));
      svg.appendChild(s("rect", { x: W - pr - 210, y: pt + 14 + k * 16, width: 12, height: 4, fill: c[2] }));
      svg.appendChild(s("text", { x: W - pr - 192, y: pt + 20 + k * 16 }, c[0]));
    });
    if (r.appetite !== null && r.appetite <= xmax) {
      svg.appendChild(s("line", { x1: X(r.appetite), x2: X(r.appetite), y1: pt, y2: H - pb, stroke: "#e6ebf2", "stroke-dasharray": "5 4" }));
      svg.appendChild(s("text", { x: X(r.appetite) + 4, y: pt + 12 }, "appetite"));
    }
    return svg;
  }

  function renderResults(r) {
    var box = document.getElementById("results");
    box.textContent = "";
    var p = r.portfolio;
    var k = h("div", { class: "kpis" },
      h("div", { class: "k" }, h("span", { text: "Expected annual loss" }), h("b", { text: money(p.mean) })),
      h("div", { class: "k" }, h("span", { text: "1-in-20 year (P95)" }), h("b", { text: money(p.p95) })),
      h("div", { class: "k" }, h("span", { text: "1-in-100 year (P99)" }), h("b", { text: money(p.p99) })));
    if (r.appetite !== null) k.appendChild(h("div", { class: "k" + (p.prob_over_appetite > 0.1 ? " bad" : "") }, h("span", { text: "Chance above appetite" }), h("b", { text: pct(p.prob_over_appetite) })));
    box.appendChild(k);
    box.appendChild(h("div", { class: "panel" }, h("h2", { text: "Loss exceedance curve" }), lecChart(r),
      h("p", { class: "note", text: "Each point is the chance that one year's total loss exceeds that amount." })));

    var maxMean = Math.max.apply(null, r.scenarios.map(function (x) { return x.mean; })) || 1;
    var tb = h("tbody");
    r.scenarios.slice().sort(function (a, b) { return b.mean - a.mean; }).forEach(function (x) {
      var bar = h("div", { class: "bar" }, h("i"));
      bar.firstChild.style.width = (100 * x.mean / maxMean).toFixed(1) + "%";
      tb.appendChild(h("tr", null, h("td", { text: x.name }), h("td", { class: "barcell" }, bar), h("td", { class: "n", text: money(x.mean) }), h("td", { class: "n", text: money(x.p95) }), h("td", { class: "n", text: pct(x.prob_any_loss) })));
    });
    box.appendChild(h("div", { class: "panel" }, h("h2", { text: "Where the loss comes from" }),
      h("table", null, h("thead", null, h("tr", null, h("th", { text: "Scenario" }), h("th"), h("th", { class: "n", text: "Expected" }), h("th", { class: "n", text: "P95" }), h("th", { class: "n", text: "Chance of loss" }))), tb)));

    if (r.controls.length) {
      var cb = h("tbody");
      r.controls.forEach(function (c) {
        cb.appendChild(h("tr", null, h("td", { text: c.name }), h("td", { class: "n", text: money(c.annual_cost) }), h("td", { class: "n", text: money(c.ale_reduction) }),
          h("td", { class: "n " + (c.net_benefit >= 0 ? "pos" : "neg"), text: money(c.net_benefit) }), h("td", { class: "n", text: c.rosi === null ? "n/a" : Math.round(c.rosi * 100) + "%" })));
      });
      box.appendChild(h("div", { class: "panel" }, h("h2", { text: "Controls ranked by net benefit" }),
        h("table", null, h("thead", null, h("tr", null, h("th", { text: "Control" }), h("th", { class: "n", text: "Cost/yr" }), h("th", { class: "n", text: "Loss avoided" }), h("th", { class: "n", text: "Net benefit" }), h("th", { class: "n", text: "ROSI" }))), cb),
        h("p", { class: "note", text: "Every control is tested against the same random draws as the baseline, so the difference is the control's effect, not simulation noise." })));
      var b = r.bundle;
      var inner = b.controls.length
        ? [h("p", null, "Spend ", h("b", { text: money(b.spent) }), " per year to move expected annual loss from ", h("b", { text: money(b.ale_before) }), " to ", h("b", { text: money(b.ale_after) }), "."),
           (function () {
             var ol = h("ol");
             b.controls.forEach(function (x) { ol.appendChild(h("li", { text: x.name + " (adds " + money(x.marginal_net_benefit) + " net benefit)" })); });
             return ol;
           })()]
        : [h("p", { class: "empty", text: "No control pays for itself inside the budget with these estimates." })];
      var bundle = h("div", { class: "panel" }, h("h2", { text: "Recommended bundle" }));
      inner.forEach(function (n) { bundle.appendChild(n); });
      box.appendChild(bundle);
    }
  }

  function run() {
    if (busy || !data) return;
    busy = true;
    var btn = document.getElementById("run");
    btn.disabled = true;
    btn.textContent = "Simulating...";
    api("POST", "/api/simulate", data).then(renderResults).catch(function (e) { toast(e.message, true); })
      .then(function () { busy = false; btn.disabled = false; btn.textContent = "Run simulation"; });
  }

  document.getElementById("run").addEventListener("click", run);
  document.getElementById("load").addEventListener("click", function () {
    api("GET", "/api/sample").then(function (d) { data = d; renderInputs(); run(); }).catch(function (e) { toast(e.message, true); });
  });
  api("GET", "/api/sample").then(function (d) { data = d; renderInputs(); run(); }).catch(function (e) { toast(e.message, true); });
})();
