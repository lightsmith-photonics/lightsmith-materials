/* Lightsmith Materials: progressive enhancement for every page (no dependencies).
   Theme toggle, search typeahead, copy buttons, tabs, list filters, a small SVG chart (LS.chart)
   and the dataset comparison plot on material pages. */
"use strict";
(function () {
  var LS = (window.LS = window.LS || {});
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  LS.$ = $; LS.$$ = $$;
  var SVGNS = "http://www.w3.org/2000/svg";

  /* --- theme ------------------------------------------------------------------------- */
  function setThemeLabel() {
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    $$("[data-theme-toggle]").forEach(function (b) { b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme"); });
  }
  $$("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", t);
      try { localStorage.setItem("theme", t); } catch (e) { /* private mode */ }
      setThemeLabel();
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  });
  setThemeLabel();

  /* "/" focuses the search box */
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    var input = $("#q") || $("#hs-q");
    if (input) { e.preventDefault(); input.focus(); input.select(); }
  });

  /* --- copy -------------------------------------------------------------------------- */
  function copyText(text, el) {
    var done = function () {
      if (!el) return;
      var old = el.getAttribute("data-label") || el.textContent;
      el.setAttribute("data-label", old);
      if (el.tagName === "BUTTON") { el.textContent = "Copied"; setTimeout(function () { el.textContent = old; }, 1400); }
      else { el.classList.add("copied"); el.setAttribute("title", "Copied"); }
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () {});
    else {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
      document.body.removeChild(ta);
    }
  }
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-copy], [data-copy-target]");
    if (!el) return;
    if (el.hasAttribute("data-copy")) copyText(el.getAttribute("data-copy"), el);
    else {
      var code = $("#" + el.getAttribute("data-copy-target") + " code");
      if (code) copyText(code.textContent, el);
    }
  });
  $$(".install[data-copy]").forEach(function (el) { el.setAttribute("role", "button"); el.setAttribute("tabindex", "0"); });
  document.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches(".install[data-copy]")) {
      e.preventDefault(); copyText(e.target.getAttribute("data-copy"), e.target);
    }
  });

  /* --- python highlighting for the snippets ------------------------------------------- */
  var PY = /(#[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|\b(import|from|as|print|for|in|def|return|None|True|False)\b|\b(\d+(?:\.\d+)?(?:e[-+]?\d+)?)\b/g;
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  $$('pre[data-lang="python"] code').forEach(function (code) {
    var src = code.textContent, out = "", last = 0, m;
    PY.lastIndex = 0;
    while ((m = PY.exec(src))) {
      out += esc(src.slice(last, m.index));
      var cls = m[1] ? "tok-c" : m[2] ? "tok-s" : m[3] ? "tok-k" : "tok-n";
      out += '<span class="' + cls + '">' + esc(m[0]) + "</span>";
      last = PY.lastIndex;
    }
    code.innerHTML = out + esc(src.slice(last));
  });

  /* --- tabs (WAI-ARIA pattern) -------------------------------------------------------- */
  $$("[data-tabs]").forEach(function (box) {
    var tabs = $$('[role="tab"]', box);
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
      try { localStorage.setItem("snippet-tab", tab.id); } catch (e) { /* ignore */ }
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : null;
        if (j === null) return;
        e.preventDefault(); select(tabs[(j + tabs.length) % tabs.length], true);
      });
    });
    var saved = null;
    try { saved = localStorage.getItem("snippet-tab"); } catch (e) { /* ignore */ }
    var pick = saved && tabs.filter(function (t) { return t.id === saved; })[0];
    if (pick) select(pick);
  });

  /* --- list filters -------------------------------------------------------------------- */
  $$("[data-filter]").forEach(function (input) {
    var root = $(input.getAttribute("data-filter"));
    if (!root) return;
    var empty = $("[data-filter-empty]");
    var items = $$("[data-filter-item]", root).map(function (li) { return [li, li.textContent.toLowerCase()]; });
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase(), shown = 0;
      items.forEach(function (it) { var ok = !q || it[1].indexOf(q) >= 0; it[0].hidden = !ok; if (ok) shown++; });
      $$("[data-filter-group]", root).forEach(function (g) { g.hidden = !$$("[data-filter-item]", g).some(function (li) { return !li.hidden; }); });
      if (empty) empty.hidden = shown > 0;
    });
  });

  /* --- "read more" for long descriptions ------------------------------------------------ */
  $$("[data-clamp]").forEach(function (el) {
    if (el.scrollHeight <= el.clientHeight + 4) { el.classList.add("open"); return; }
    var b = document.createElement("button");
    b.type = "button"; b.className = "link-btn small"; b.textContent = "Read more";
    b.setAttribute("aria-expanded", "false");
    b.addEventListener("click", function () {
      var open = el.classList.toggle("open");
      b.textContent = open ? "Show less" : "Read more";
      b.setAttribute("aria-expanded", open ? "true" : "false");
    });
    el.after(b);
  });

  /* --- typeahead over /v1/materials ------------------------------------------------------ */
  $$("[data-typeahead]").forEach(function (input, idx) {
    var form = input.form;
    form.style.position = form.style.position || "relative";
    var list = document.createElement("ul");
    list.className = "ta-list"; list.id = "ta-" + idx; list.setAttribute("role", "listbox"); list.hidden = true;
    form.appendChild(list);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-expanded", "false");
    var items = [], active = -1, timer = null, seq = 0;
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); active = -1; }
    function render() {
      list.innerHTML = "";
      items.forEach(function (m, i) {
        var li = document.createElement("li");
        li.id = list.id + "-" + i; li.setAttribute("role", "option"); li.setAttribute("aria-selected", i === active ? "true" : "false");
        var name = document.createElement("span"); name.textContent = m.name;
        var meta = document.createElement("span"); meta.className = "ta-meta";
        meta.textContent = m.dataset_count + (m.dataset_count === 1 ? " dataset" : " datasets") + (m.manufacturer ? " · " + m.manufacturer : "");
        li.appendChild(name); li.appendChild(meta);
        li.addEventListener("mousedown", function (e) { e.preventDefault(); location.href = "/materials/" + m.slug; });
        list.appendChild(li);
      });
      list.hidden = items.length === 0;
      input.setAttribute("aria-expanded", items.length ? "true" : "false");
      if (active >= 0) input.setAttribute("aria-activedescendant", list.id + "-" + active); else input.removeAttribute("aria-activedescendant");
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 1) { items = []; close(); return; }
      timer = setTimeout(function () {
        var mine = ++seq;
        fetch("/v1/materials?limit=8&q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } })
          .then(function (r) { return r.ok ? r.json() : { items: [] }; })
          .then(function (d) { if (mine !== seq) return; items = d.items || []; active = -1; render(); })
          .catch(function () { /* offline: plain search still works */ });
      }, 120);
    });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        // positions -1 (the input itself) .. items.length - 1, wrapping around
        var n = items.length + 1;
        active = ((active + 1 + (e.key === "ArrowDown" ? 1 : -1)) % n + n) % n - 1;
        render();
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault(); location.href = "/materials/" + items[active].slug;
      } else if (e.key === "Escape") { close(); }
    });
    input.addEventListener("blur", function () { setTimeout(close, 100); });
  });

  /* --- chart ----------------------------------------------------------------------------- */
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  LS.css = css;
  function el(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function niceTicks(lo, hi, n) {
    if (!(hi > lo)) { var d = Math.abs(lo) * 0.1 || 1; lo -= d; hi += d; }
    var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / n))), err = (n * step) / span;
    if (err <= 0.15) step *= 10; else if (err <= 0.35) step *= 5; else if (err <= 0.75) step *= 2;
    var a = Math.ceil(lo / step - 1e-9) * step, out = [];
    for (var v = a; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    return out;
  }
  function logTicks(lo, hi) {
    var a = Math.floor(Math.log10(lo)), b = Math.ceil(Math.log10(hi)), out = [];
    var mults = b - a <= 1 ? [1, 2, 3, 5] : b - a <= 3 ? [1, 2, 5] : [1];
    for (var e = a; e <= b; e++) mults.forEach(function (m) { var v = m * Math.pow(10, e); if (v >= lo * 0.999 && v <= hi * 1.001) out.push(v); });
    if (out.length < 2) out = [lo, hi];
    return out;
  }
  function fmt(v) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2).replace("e+", "e");
    return String(+v.toPrecision(5));
  }
  function fmtTick(v) {
    var a = Math.abs(v);
    if (a !== 0 && (a < 1e-3 || a >= 1e5)) {
      var e = Math.floor(Math.log10(a)), m = v / Math.pow(10, e);
      return (Math.abs(m - 1) < 1e-9 ? "" : +m.toPrecision(3) + "×") + "10" + String(e).replace(/-/g, "⁻").replace(/\d/g, function (d) { return "⁰¹²³⁴⁵⁶⁷⁸⁹"[d]; });
    }
    return String(+v.toPrecision(6));
  }
  LS.fmt = fmt;
  function nearest(xs, x) {
    var lo = 0, hi = xs.length - 1;
    if (hi < 0) return -1;
    var asc = xs[hi] >= xs[0];
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if ((xs[mid] < x) === asc) lo = mid; else hi = mid; }
    return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
  }

  /* LS.chart(container, {series: [{x, y, name, color, dash, axis: "y"|"y2"}], xLabel, yLabel, y2Label,
       logX, logY, marker, onPick(x)}) draws an accessible SVG line chart and redraws on resize/theme change. */
  LS.chart = function (box, opts) {
    var state = { opts: opts };
    var tip = document.createElement("div");
    tip.className = "chart-tip"; tip.hidden = true;
    function draw() {
      var o = state.opts;
      Array.prototype.slice.call(box.children).forEach(function (c) { if (c.tagName.toLowerCase() === "svg" || c.classList.contains("chart-msg")) c.remove(); });
      if (!tip.parentNode) box.appendChild(tip);
      var W = box.clientWidth, H = box.clientHeight;
      if (!W || !H) return;
      var series = (o.series || []).filter(function (s) { return s.x && s.x.length; });
      if (!series.length) { var msg = document.createElement("div"); msg.className = "chart-msg"; msg.textContent = o.empty || "No data to plot"; box.appendChild(msg); return; }
      var hasY2 = series.some(function (s) { return s.axis === "y2"; });
      var narrow = W < 480;
      var m = { l: narrow ? 50 : 62, r: hasY2 ? (narrow ? 50 : 62) : 16, t: series.length > 1 ? 30 : 12, b: 44 };
      var pw = W - m.l - m.r, ph = H - m.t - m.b;
      function ext(axis, isY, log) {
        var lo = Infinity, hi = -Infinity;
        series.forEach(function (s) {
          if (isY && (s.axis || "y") !== axis) return;
          (isY ? s.y : s.x).forEach(function (v) { if (v !== null && isFinite(v) && (!log || v > 0)) { if (v < lo) lo = v; if (v > hi) hi = v; } });
        });
        return [lo, hi];
      }
      function scale(range, a, b, log, pad) {
        var lo = range[0], hi = range[1];
        if (!isFinite(lo)) { lo = log ? 1 : 0; hi = log ? 10 : 1; }
        if (log) { var L = Math.log10(lo), H2 = Math.log10(hi); if (H2 - L < 1e-9) { L -= 0.5; H2 += 0.5; } lo = Math.pow(10, L); hi = Math.pow(10, H2); }
        else {
          if (hi - lo < 1e-12 * Math.max(1, Math.abs(hi))) { var d = Math.abs(hi) * 0.05 || 0.5; lo -= d; hi += d; }
          if (pad) { var p = (hi - lo) * 0.05; lo -= p; hi += p; }
        }
        var f = log ? function (v) { return a + (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) * (b - a); }
                    : function (v) { return a + (v - lo) / (hi - lo) * (b - a); };
        f.inv = log ? function (p) { return Math.pow(10, Math.log10(lo) + (p - a) / (b - a) * (Math.log10(hi) - Math.log10(lo))); }
                    : function (p) { return lo + (p - a) / (b - a) * (hi - lo); };
        f.lo = lo; f.hi = hi; f.log = log;
        f.ticks = log ? logTicks(lo, hi) : niceTicks(lo, hi, narrow ? 4 : 6);
        return f;
      }
      var X = scale(ext("x", false, o.logX), m.l, m.l + pw, o.logX, false);
      var Y = scale(ext("y", true, o.logY), m.t + ph, m.t, o.logY, true);
      var Y2 = hasY2 ? scale(ext("y2", true, o.logY), m.t + ph, m.t, o.logY, true) : null;
      var svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, "aria-hidden": "true", focusable: "false" });
      box.insertBefore(svg, tip);
      var g = el("g", { "class": "axis" }, svg);
      X.ticks.forEach(function (t) {
        var x = X(t); if (x < m.l - 0.5 || x > m.l + pw + 0.5) return;
        el("line", { x1: x, x2: x, y1: m.t, y2: m.t + ph, "class": "gridline" }, g);
        el("text", { x: x, y: m.t + ph + 18, "text-anchor": "middle" }, g).textContent = fmtTick(t);
      });
      Y.ticks.forEach(function (t) {
        var y = Y(t); if (y < m.t - 0.5 || y > m.t + ph + 0.5) return;
        el("line", { x1: m.l, x2: m.l + pw, y1: y, y2: y, "class": "gridline" }, g);
        el("text", { x: m.l - 8, y: y + 4, "text-anchor": "end" }, g).textContent = fmtTick(t);
      });
      if (Y2) Y2.ticks.forEach(function (t) {
        var y = Y2(t); if (y < m.t - 0.5 || y > m.t + ph + 0.5) return;
        el("text", { x: m.l + pw + 8, y: y + 4, "text-anchor": "start" }, g).textContent = fmtTick(t);
      });
      el("rect", { x: m.l, y: m.t, width: pw, height: ph, "class": "frame" }, svg);
      el("text", { x: m.l + pw / 2, y: H - 6, "text-anchor": "middle", "class": "axis-label" }, svg).textContent = o.xLabel || "";
      var yl = el("text", { x: 0, y: 0, "text-anchor": "middle", "class": "axis-label", transform: "translate(13," + (m.t + ph / 2) + ") rotate(-90)" }, svg);
      yl.textContent = o.yLabel || "";
      if (Y2) { var yl2 = el("text", { "text-anchor": "middle", "class": "axis-label", transform: "translate(" + (W - 6) + "," + (m.t + ph / 2) + ") rotate(90)" }, svg); yl2.textContent = o.y2Label || ""; }
      var clipId = "clip" + Math.random().toString(36).slice(2);
      var cp = el("clipPath", { id: clipId }, el("defs", {}, svg));
      el("rect", { x: m.l, y: m.t - 2, width: pw, height: ph + 4 }, cp);
      var plot = el("g", { "clip-path": "url(#" + clipId + ")" }, svg);
      series.forEach(function (s) {
        var S = s.axis === "y2" ? Y2 : Y, d = "", pen = false;
        for (var i = 0; i < s.x.length; i++) {
          var xv = s.x[i], yv = s.y[i];
          if (yv === null || !isFinite(yv) || !isFinite(xv) || (o.logY && yv <= 0) || (o.logX && xv <= 0)) { pen = false; continue; }
          d += (pen ? "L" : "M") + X(xv).toFixed(2) + "," + S(yv).toFixed(2);
          pen = true;
        }
        el("path", { d: d, "class": "series", stroke: s.color, "stroke-dasharray": s.dash ? "6 4" : null }, plot);
        if (s.x.length < 40) s.x.forEach(function (xv, i) {
          var yv = s.y[i]; if (yv === null || !isFinite(yv) || (o.logY && yv <= 0)) return;
          el("circle", { cx: X(xv), cy: S(yv), r: 2.5, fill: s.color }, plot);
        });
      });
      if (series.length > 1) {
        var lg = el("g", { "class": "legend" }, svg), lx = m.l;
        series.forEach(function (s) {
          if (lx > W - 60) return;
          el("line", { x1: lx, x2: lx + 18, y1: 14, y2: 14, stroke: s.color, "stroke-width": 2.5, "stroke-dasharray": s.dash ? "5 3" : null }, lg);
          var t = el("text", { x: lx + 23, y: 18 }, lg); t.textContent = s.name;
          lx += 23 + Math.min(220, t.getComputedTextLength ? t.getComputedTextLength() : s.name.length * 7) + 16;
        });
      }
      if (o.marker !== null && o.marker !== undefined && isFinite(o.marker)) {
        var mx = X(o.marker);
        if (mx >= m.l && mx <= m.l + pw) el("line", { x1: mx, x2: mx, y1: m.t, y2: m.t + ph, "class": "marker" }, svg);
      }
      var hl = el("line", { y1: m.t, y2: m.t + ph, "class": "hover-line", visibility: "hidden" }, svg);
      var hit = el("rect", { x: m.l, y: m.t, width: pw, height: ph, fill: "transparent", style: o.onPick ? "cursor:crosshair" : null }, svg);
      function at(ev) {
        var r = svg.getBoundingClientRect();
        return { px: (ev.clientX - r.left) * (W / r.width), py: (ev.clientY - r.top) * (H / r.height) };
      }
      hit.addEventListener("mousemove", function (ev) {
        var p = at(ev), xv = X.inv(p.px), rows = [];
        series.forEach(function (s) { var i = nearest(s.x, xv); if (i >= 0 && s.y[i] !== null && isFinite(s.y[i])) rows.push([s, s.y[i]]); });
        hl.setAttribute("x1", p.px); hl.setAttribute("x2", p.px); hl.setAttribute("visibility", "visible");
        tip.innerHTML = "";
        var head = document.createElement("div"); head.textContent = (o.xName || "x") + " = " + fmt(xv) + (o.xUnit ? " " + o.xUnit : "");
        tip.appendChild(head);
        rows.forEach(function (r) {
          var line = document.createElement("div"), b = document.createElement("b");
          b.textContent = r[0].name; b.style.color = r[0].color;
          line.appendChild(b); line.appendChild(document.createTextNode(" " + fmt(r[1])));
          tip.appendChild(line);
        });
        tip.hidden = false;
        var bw = box.clientWidth, left = p.px * (box.clientWidth / W) + 12;
        if (left + tip.offsetWidth > bw) left = p.px * (box.clientWidth / W) - tip.offsetWidth - 12;
        tip.style.left = Math.max(0, left) + "px"; tip.style.top = (m.t + 4) + "px";
      });
      hit.addEventListener("mouseleave", function () { tip.hidden = true; hl.setAttribute("visibility", "hidden"); });
      if (o.onPick) hit.addEventListener("click", function (ev) { o.onPick(X.inv(at(ev).px)); });
    }
    state.update = function (o) { state.opts = Object.assign({}, state.opts, o); draw(); };
    if (window.ResizeObserver) { var t; new ResizeObserver(function () { clearTimeout(t); t = setTimeout(draw, 60); }).observe(box); }
    document.addEventListener("themechange", function () { if (state.opts.recolor) state.opts.recolor(); draw(); });
    draw();
    return state;
  };
  LS.palette = function () { return [1, 2, 3, 4, 5, 6].map(function (i) { return css("--plot-" + i); }); };

  /* --- material page: compare datasets with /v1/compare ---------------------------------- */
  var cmpBox = $("[data-compare]");
  if (cmpBox) {
    var checks = $$("input.cmp");
    checks.forEach(function (c) { c.hidden = false; });
    var quantity = "n", chart = null, lastData = null;
    // "Wang et al. 2025: n,k 0.400–1.31 µm; 25 °C" -> "Wang et al. 2025, 25 °C"
    function legendName(name) {
      var parts = name.split(":"), extra = name.indexOf(";") >= 0 ? name.slice(name.indexOf(";") + 1).trim() : "";
      return parts[0] + (extra ? ", " + extra : "");
    }
    function series() {
      if (!lastData) return [];
      var pal = LS.palette();
      return lastData.items.map(function (it, i) {
        return { x: it.data.wavelength_nm.map(function (v) { return v / 1000; }), y: it.data[quantity],
                 name: legendName(it.dataset.name), color: pal[i % pal.length], dash: i >= pal.length };
      }).filter(function (s) { return s.y && s.y.some(function (v) { return v !== null && (quantity !== "k" || v > 0); }); });
    }
    function redraw() {
      var opts = { series: series(), xLabel: "Wavelength λ (µm)", xName: "λ", xUnit: "µm", yLabel: quantity, logX: true,
                   logY: quantity === "k", empty: quantity === "k" ? "No k data in the selected datasets" : "Select datasets to plot",
                   recolor: function () { chart.opts.series = series(); } };
      if (!chart) chart = LS.chart(cmpBox, opts); else chart.update(opts);
    }
    function load() {
      var ids = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; }).slice(0, 12);
      if (!ids.length) { lastData = null; redraw(); return; }
      fetch("/v1/compare?points=400&spacing=log&quantity=nk&datasets=" + ids.map(encodeURIComponent).join(","), { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); }).then(function (d) { lastData = d.items ? d : null; redraw(); })
        .catch(function () { lastData = null; redraw(); });
    }
    checks.forEach(function (c) {
      c.addEventListener("change", function () {
        if (checks.filter(function (x) { return x.checked; }).length > 12) c.checked = false;
        load();
      });
    });
    $$("[data-cmp-q]").forEach(function (b) {
      b.addEventListener("click", function () {
        quantity = b.getAttribute("data-cmp-q");
        $$("[data-cmp-q]").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        redraw();
      });
    });
    load();
  }
})();
