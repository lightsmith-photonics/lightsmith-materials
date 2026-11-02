/* Dataset page: n and k evaluated exactly as the API does (materials_db.optics / rii.formula_n),
   the interactive plot, the single-wavelength calculator, and the transmission and Fresnel calculators.
   Wavelengths are in µm unless a name says otherwise; time dependence exp(-iωt), k > 0 for loss. */
"use strict";
(function () {
  var LS = window.LS, $ = LS.$, $$ = LS.$$;
  var node = document.getElementById("plot-data");
  if (!node) return;
  var D = JSON.parse(node.textContent);
  var HC = 1239.841984; // eV nm
  var C_UM_FS = 0.299792458;

  /* --- n, k (keep in step with materials_db/optics.py and rii.py) ---------------------------- */
  function formulaN(num, c0, lam) {
    var c = c0.slice();
    while (c.length < 17) c.push(0);
    var l2 = lam * lam, n2, i;
    var pairsEnd = c.length - 1;
    switch (num) {
      case 1: n2 = 1 + c[0]; for (i = 1; i < pairsEnd; i += 2) if (c[i] !== 0) n2 += c[i] * l2 / (l2 - c[i + 1] * c[i + 1]); break;
      case 2: n2 = 1 + c[0]; for (i = 1; i < pairsEnd; i += 2) if (c[i] !== 0) n2 += c[i] * l2 / (l2 - c[i + 1]); break;
      case 3: n2 = c[0]; for (i = 1; i < pairsEnd; i += 2) if (c[i] !== 0) n2 += c[i] * Math.pow(lam, c[i + 1]); break;
      case 4:
        n2 = c[0];
        if (c[1] !== 0) n2 += c[1] * Math.pow(lam, c[2]) / (l2 - Math.pow(c[3], c[4]));
        if (c[5] !== 0) n2 += c[5] * Math.pow(lam, c[6]) / (l2 - Math.pow(c[7], c[8]));
        for (i = 9; i < pairsEnd; i += 2) if (c[i] !== 0) n2 += c[i] * Math.pow(lam, c[i + 1]);
        break;
      case 5: { var n = c[0]; for (i = 1; i < pairsEnd; i += 2) if (c[i] !== 0) n += c[i] * Math.pow(lam, c[i + 1]); return n; }
      case 6: { var g = 1 + c[0]; for (i = 1; i < pairsEnd; i += 2) if (c[i] !== 0) g += c[i] / (c[i + 1] - 1 / l2); return g; }
      case 7: { var d = 1 / (l2 - 0.028); return c[0] + c[1] * d + c[2] * d * d + c[3] * l2 + c[4] * l2 * l2 + c[5] * l2 * l2 * l2; }
      case 8: { var x = c[0] + c[1] * l2 / (l2 - c[2]) + c[3] * l2; n2 = (1 + 2 * x) / (1 - x); break; }
      case 9: n2 = c[0] + c[1] / (l2 - c[2]) + c[3] * (lam - c[4]) / ((lam - c[4]) * (lam - c[4]) + c[5]); break;
      default: return NaN;
    }
    return Math.sqrt(n2);
  }
  LS.formulaN = formulaN;
  function interp(xs, ys, x, outside) {
    var n = xs.length;
    if (x < xs[0]) return outside === undefined ? ys[0] : outside;
    if (x > xs[n - 1]) return outside === undefined ? ys[n - 1] : outside;
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (xs[mid] <= x) lo = mid; else hi = mid; }
    if (xs[hi] === xs[lo]) return ys[lo];
    return ys[lo] + (ys[hi] - ys[lo]) * (x - xs[lo]) / (xs[hi] - xs[lo]);
  }
  var LO = D.range_nm[0], HI = D.range_nm[1];
  function nk(wlNm) {
    var tol = 1e-9 * Math.max(Math.abs(HI), 1);
    if (!(wlNm >= LO - tol && wlNm <= HI + tol)) return [NaN, NaN];
    var w = Math.min(Math.max(wlNm, LO), HI), n, k;
    if (D.formula) n = formulaN(D.formula.type, D.formula.c, w / 1000);
    else if (D.n) n = interp(D.n[0], D.n[1], w);
    else n = NaN;
    k = D.k ? interp(D.k[0], D.k[1], w, 0) : 0;
    return [n, k];
  }
  LS.nk = nk;

  /* --- derived quantities (keep in step with site/content.py: derived_at) ----------------------- */
  function derivedAt(wl) {
    var v = nk(wl * 1000), n = v[0], k = v[1], out = { n: n, k: k };
    if (!isFinite(k)) return out;
    out.eps1 = isFinite(n) ? n * n - k * k : NaN;
    out.eps2 = isFinite(n) ? 2 * n * k : NaN;
    out.alpha = 4 * Math.PI * k / (wl * 1e-4);
    out.skin = k > 0 ? wl * 1000 / (2 * Math.PI * k) : NaN;
    var inside = function (h, m) { return LO <= (wl - m * h) * 1000 && (wl + m * h) * 1000 <= HI; };
    var f = function (x) { return nk(x * 1000)[0]; };
    if (isFinite(n)) {
      var d1 = null, h;
      if (D.formula && inside(wl * 1e-3, 2)) { // five-point stencils for formulas
        h = wl * 1e-3;
        d1 = (-f(wl + 2 * h) + 8 * f(wl + h) - 8 * f(wl - h) + f(wl - 2 * h)) / (12 * h);
      } else if (inside(wl * 1e-4, 1)) { // tables: the slope of the interpolating segment
        h = wl * 1e-4;
        d1 = (f(wl + h) - f(wl - h)) / (2 * h);
      }
      if (d1 !== null) { out.dndl = d1; out.ng = n - wl * d1; }
      if (D.formula) { // group-velocity dispersion needs a smooth n(λ)
        var d2 = null;
        if (inside(wl * 5e-3, 2)) {
          h = wl * 5e-3;
          d2 = (-f(wl + 2 * h) + 16 * f(wl + h) - 30 * n + 16 * f(wl - h) - f(wl - 2 * h)) / (12 * h * h);
        } else if (inside(wl * 1e-3, 1)) {
          h = wl * 1e-3;
          d2 = (f(wl + h) - 2 * n + f(wl - h)) / (h * h);
        }
        if (d2 !== null) {
          out.gvd = 1e3 * Math.pow(wl, 3) / (2 * Math.PI * C_UM_FS * C_UM_FS) * d2;
          out.D = -wl / C_UM_FS * d2 * 1e3;
        }
      }
    }
    return out;
  }
  LS.derivedAt = derivedAt;

  /* --- units -------------------------------------------------------------------------------- */
  var AXES = {
    um: { from: function (nm) { return nm / 1000; }, to: function (v) { return v * 1000; }, label: "Wavelength λ (µm)", name: "λ", unit: "µm", log: false },
    nm: { from: function (nm) { return nm; }, to: function (v) { return v; }, label: "Wavelength λ (nm)", name: "λ", unit: "nm", log: false },
    ev: { from: function (nm) { return HC / nm; }, to: function (v) { return HC / v; }, label: "Photon energy (eV)", name: "E", unit: "eV", log: false },
    wn: { from: function (nm) { return 1e7 / nm; }, to: function (v) { return 1e7 / v; }, label: "Wavenumber (cm⁻¹)", name: "ν̃", unit: "cm⁻¹", log: false }
  };

  /* --- plot grid: native points (tables) or a dense log grid (formulas) ----------------------- */
  function grid() {
    var pts = [];
    if (D.formula) {
      var N = 600, a = Math.log(LO), b = Math.log(HI);
      for (var i = 0; i < N; i++) pts.push(Math.exp(a + (b - a) * i / (N - 1)));
      if (D.k) D.k[0].forEach(function (x) { if (x > LO && x < HI) pts.push(x); });
    } else {
      if (D.n) pts = pts.concat(D.n[0]);
      if (D.k) pts = pts.concat(D.k[0]);
    }
    pts.sort(function (x, y) { return x - y; });
    return pts.filter(function (x, i) { return i === 0 || x !== pts[i - 1]; });
  }
  var WL = grid();
  var NK = WL.map(nk);

  var state = { q: "nk", axis: "um", logX: false, logY: false, wl: parseFloat(($("[data-wl]") || {}).value) || null };
  var chartBox = document.getElementById("nk-chart");
  var chart = null;
  function seriesFor() {
    var pal = LS.palette(), ax = AXES[state.axis], xs = WL.map(ax.from), s = [];
    var n = NK.map(function (v) { return v[0]; }), k = NK.map(function (v) { return v[1]; });
    var both = D.has_n && D.k;
    if (state.q === "nk") {
      if (D.has_n) s.push({ x: xs, y: n, name: "n", color: pal[0] });
      if (D.k) s.push({ x: xs, y: k, name: "k", color: pal[1], axis: D.has_n ? "y2" : "y" });
      return { series: s, yLabel: D.has_n ? "n" : "k", y2Label: both ? "k" : null };
    }
    if (state.q === "eps") {
      s.push({ x: xs, y: NK.map(function (v) { return v[0] * v[0] - v[1] * v[1]; }), name: "ε₁", color: pal[0] });
      if (D.k) s.push({ x: xs, y: NK.map(function (v) { return 2 * v[0] * v[1]; }), name: "ε₂", color: pal[1], axis: "y2" });
      return { series: s, yLabel: "ε₁", y2Label: D.k ? "ε₂" : null };
    }
    s.push({ x: xs, y: NK.map(function (v, i) { return 4 * Math.PI * v[1] / (WL[i] * 1e-7); }), name: "α", color: pal[2] });
    return { series: s, yLabel: "Absorption coefficient α (cm⁻¹)", y2Label: null };
  }
  function drawPlot() {
    var ax = AXES[state.axis], sf = seriesFor();
    var opts = { series: sf.series, yLabel: sf.yLabel, y2Label: sf.y2Label, xLabel: ax.label, xName: ax.name, xUnit: ax.unit,
                 logX: state.logX, logY: state.logY, marker: state.wl ? ax.from(state.wl * 1000) : null,
                 empty: state.q === "alpha" && !D.k ? "No absorption data (k = 0)" : "No data",
                 onPick: function (x) { setWavelength(ax.to(x) / 1000, true); },
                 recolor: function () { chart.opts.series = seriesFor().series; } };
    if (!chart) chart = LS.chart(chartBox, opts); else chart.update(opts);
  }
  $$("[data-q]").forEach(function (b) {
    b.addEventListener("click", function () {
      state.q = b.getAttribute("data-q");
      $$("[data-q]").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
      if (state.q === "alpha" && !state.logYTouched) { state.logY = true; $("[data-logy]").checked = true; }
      drawPlot();
    });
  });
  var axisSel = $("[data-axis]");
  if (axisSel) axisSel.addEventListener("change", function () { state.axis = axisSel.value; drawPlot(); });
  var lx = $("[data-logx]"), ly = $("[data-logy]");
  if (lx) lx.addEventListener("change", function () { state.logX = lx.checked; drawPlot(); });
  if (ly) ly.addEventListener("change", function () { state.logY = ly.checked; state.logYTouched = true; drawPlot(); });
  // wide formula ranges read best on a log axis
  if (HI / LO > 20) { state.logX = true; if (lx) lx.checked = true; }
  drawPlot();

  /* --- calculator ------------------------------------------------------------------------------- */
  var wlIn = $("[data-wl]"), wlUnit = $("[data-wl-unit]");
  var UNIT = { um: AXES.um, nm: AXES.nm, ev: AXES.ev, wn: AXES.wn };
  function out(name, v, digits) {
    var el = document.querySelector('[data-out="' + name + '"]');
    if (el) el.textContent = isFinite(v) && v !== null && v !== undefined ? LS.fmt(+(+v).toPrecision(digits || 6)) : "—";
  }
  function setWavelength(wlUm, fromPlot) {
    state.wl = wlUm;
    if (fromPlot && wlIn) { wlIn.value = +UNIT[wlUnit.value].from(wlUm * 1000).toPrecision(5); }
    var r = derivedAt(wlUm);
    ["n", "k", "eps1", "eps2", "alpha", "skin", "dndl", "ng", "gvd", "D"].forEach(function (q) { out(q, r[q]); });
    out("ev", HC / (wlUm * 1000));
    var outside = !isFinite(r.k);
    if (wlIn) { wlIn.setAttribute("aria-invalid", outside ? "true" : "false"); }
    if (chart) chart.update({ marker: AXES[state.axis].from(wlUm * 1000) });
    syncTools(wlUm);
  }
  function readWavelength() {
    var v = parseFloat(wlIn.value);
    if (!(v > 0)) return;
    setWavelength(UNIT[wlUnit.value].to(v) / 1000, false);
  }
  if (wlIn) {
    wlIn.addEventListener("input", readWavelength);
    wlUnit.addEventListener("change", function () {
      if (state.wl) wlIn.value = +UNIT[wlUnit.value].from(state.wl * 1000).toPrecision(5);
    });
  }
  // Abbe number from the d, F and C lines
  (function () {
    var d = nk(587.5618), F = nk(486.1327)[0], C = nk(656.2725)[0];
    var ok = D.formula && isFinite(d[0]) && isFinite(F) && isFinite(C) && F !== C && d[1] <= 0.01; // formula data, transparent media
    out("abbe", ok ? (d[0] - 1) / (F - C) : NaN, 5);
  })();

  /* --- complex numbers ----------------------------------------------------------------------- */
  function cx(re, im) { return { re: re, im: im || 0 }; }
  function add(a, b) { return cx(a.re + b.re, a.im + b.im); }
  function sub(a, b) { return cx(a.re - b.re, a.im - b.im); }
  function mul(a, b) { return cx(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re); }
  function div(a, b) { var d = b.re * b.re + b.im * b.im; return cx((a.re * b.re + a.im * b.im) / d, (a.im * b.re - a.re * b.im) / d); }
  function abs2(a) { return a.re * a.re + a.im * a.im; }
  function csqrt(a) { var r = Math.hypot(a.re, a.im), re = Math.sqrt((r + a.re) / 2), im = Math.sqrt(Math.max(0, (r - a.re) / 2)); return cx(re, a.im < 0 ? -im : im); }
  function cexp(a) { var e = Math.exp(a.re); return cx(e * Math.cos(a.im), e * Math.sin(a.im)); }
  var ONE = cx(1, 0);

  /* --- transmission of a free-standing slab in vacuum, normal incidence --------------------- */
  function slab(wl, dUm, method) {
    var v = nk(wl * 1000);
    if (!isFinite(v[0])) return null;
    var N = cx(v[0], v[1]);
    var Ti = Math.exp(-4 * Math.PI * v[1] / wl * dUm);
    var r1 = div(sub(N, ONE), add(N, ONE)), R1 = abs2(r1);
    if (method === "internal") return { T: Ti, R: null };
    if (method === "single") return { T: (1 - R1) * (1 - R1) * Ti, R: R1 };
    if (method === "incoherent") {
      var den = 1 - R1 * R1 * Ti * Ti;
      return { T: (1 - R1) * (1 - R1) * Ti / den, R: R1 + (1 - R1) * (1 - R1) * R1 * Ti * Ti / den };
    }
    // coherent: Airy sum of fields; exp(i beta) decays for k > 0
    var r12 = div(sub(ONE, N), add(ONE, N)), r23 = div(sub(N, ONE), add(N, ONE));
    var t12 = div(cx(2, 0), add(ONE, N)), t23 = div(mul(cx(2, 0), N), add(N, ONE));
    var beta = mul(cx(2 * Math.PI * dUm / wl, 0), N);
    var e1 = cexp(mul(cx(0, 1), beta)), e2 = mul(e1, e1);
    var dd = add(ONE, mul(mul(r12, r23), e2));
    return { T: abs2(div(mul(mul(t12, t23), e1), dd)), R: abs2(div(add(r12, mul(r23, e2)), dd)) };
  }
  LS.slab = slab;
  var tBox = $("[data-transmission]"), tChart = null;
  function tUpdate() {
    if (!tBox) return;
    var wl = parseFloat($("[data-t-wl]").value), d = parseFloat($("[data-t-d]").value) * parseFloat($("[data-t-unit]").value);
    var method = ($('input[name="t-method"]:checked') || {}).value || "incoherent";
    var r = wl > 0 && d >= 0 ? slab(wl, d, method) : null;
    $('[data-t-out="T"]').textContent = r ? LS.fmt(r.T) : "—";
    $('[data-t-out="R"]').textContent = r && r.R !== null ? LS.fmt(r.R) : "—";
    if (!(d >= 0)) return;
    var pal = LS.palette(), xs = [], T = [], R = [], N = method === "coherent" ? 1500 : 400;
    for (var i = 0; i < N; i++) {
      var w = LO * Math.pow(HI / LO, i / (N - 1));
      var s = slab(w / 1000, d, method);
      xs.push(w / 1000); T.push(s ? s.T : null); R.push(s ? s.R : null);
    }
    var series = [{ x: xs, y: T, name: "T", color: pal[0] }];
    if (method !== "internal") series.push({ x: xs, y: R, name: "R", color: pal[1] });
    var opts = { series: series, xLabel: "Wavelength λ (µm)", xName: "λ", xUnit: "µm", yLabel: "T, R", logX: HI / LO > 20,
                 marker: wl > 0 ? wl : null, onPick: function (x) { $("[data-t-wl]").value = +x.toPrecision(5); tUpdate(); } };
    if (!tChart) tChart = LS.chart($("[data-t-chart]"), opts); else tChart.update(opts);
  }
  if (tBox) { $$("input, select", tBox).forEach(function (e) { e.addEventListener("input", tUpdate); e.addEventListener("change", tUpdate); }); tUpdate(); }

  /* --- Fresnel reflection ------------------------------------------------------------------- */
  function fresnel(wl, thetaDeg, dir) {
    var v = nk(wl * 1000);
    if (!isFinite(v[0])) return null;
    var N = cx(v[0], v[1]);
    var n1 = dir === "out" ? N : ONE, n2 = dir === "out" ? ONE : N;
    var th = thetaDeg * Math.PI / 180, ci = cx(Math.cos(th), 0), si = cx(Math.sin(th), 0);
    var st = div(mul(n1, si), n2);
    var ct = csqrt(sub(ONE, mul(st, st)));
    if (mul(n2, ct).im < 0) ct = cx(-ct.re, -ct.im); // the transmitted wave decays (exp(-iωt))
    var rs = div(sub(mul(n1, ci), mul(n2, ct)), add(mul(n1, ci), mul(n2, ct)));
    var rp = div(sub(mul(n2, ci), mul(n1, ct)), add(mul(n2, ci), mul(n1, ct)));
    var Rs = abs2(rs), Rp = abs2(rp);
    return { Rs: Rs, Rp: Rp, R: (Rs + Rp) / 2, phis: Math.atan2(rs.im, rs.re) * 180 / Math.PI, phip: Math.atan2(rp.im, rp.re) * 180 / Math.PI,
             brewster: Math.atan(n2.re / n1.re) * 180 / Math.PI, critical: n2.re < n1.re ? Math.asin(n2.re / n1.re) * 180 / Math.PI : NaN };
  }
  LS.fresnel = fresnel;
  var fBox = $("[data-fresnel]"), fChart = null;
  function fUpdate() {
    if (!fBox) return;
    var wl = parseFloat($("[data-f-wl]").value), th = parseFloat($("[data-f-angle]").value);
    var dir = ($('input[name="f-dir"]:checked') || {}).value || "in";
    var r = wl > 0 && th >= 0 && th <= 90 ? fresnel(wl, th, dir) : null;
    ["Rp", "Rs", "R", "phip", "phis", "brewster", "critical"].forEach(function (q) {
      var e = document.querySelector('[data-f-out="' + q + '"]');
      var v = r ? r[q] : NaN;
      e.textContent = isFinite(v) ? LS.fmt(v) + (/phi|brewster|critical/.test(q) ? "°" : "") : "—";
    });
    if (!(wl > 0) || !fresnel(wl, 0, dir)) { if (fChart) fChart.update({ series: [] }); return; }
    var pal = LS.palette(), xs = [], P = [], S = [], A = [];
    for (var a = 0; a <= 90.0001; a += 0.25) { var f = fresnel(wl, Math.min(a, 89.999), dir); xs.push(a); P.push(f.Rp); S.push(f.Rs); A.push(f.R); }
    var opts = { series: [{ x: xs, y: P, name: "Rp", color: pal[0] }, { x: xs, y: S, name: "Rs", color: pal[1] }, { x: xs, y: A, name: "R", color: pal[2], dash: true }],
                 xLabel: "Angle of incidence (°)", xName: "θ", xUnit: "°", yLabel: "Reflectance", marker: th,
                 onPick: function (x) { $("[data-f-angle]").value = +x.toFixed(2); fUpdate(); } };
    if (!fChart) fChart = LS.chart($("[data-f-chart]"), opts); else fChart.update(opts);
  }
  if (fBox) { $$("input, select", fBox).forEach(function (e) { e.addEventListener("input", fUpdate); e.addEventListener("change", fUpdate); }); fUpdate(); }

  // the calculator wavelength drives the tool wavelengths until they are edited by hand
  function syncTools(wl) {
    [["[data-t-wl]", tUpdate], ["[data-f-wl]", fUpdate]].forEach(function (p) {
      var e = $(p[0]);
      if (e && !e.dataset.touched) { e.value = +wl.toPrecision(5); p[1](); }
    });
  }
  $$("[data-t-wl], [data-f-wl]").forEach(function (e) { e.addEventListener("input", function () { e.dataset.touched = "1"; }); });
  if (wlIn) readWavelength();
})();
