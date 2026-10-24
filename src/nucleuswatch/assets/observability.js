// Tonight's observability of each candidate, computed in the browser.
//
// Low-precision Sun and Moon positions (Astronomical Almanac formulae). Checked
// against astropy: target altitudes agree to 0.35 deg, the Moon to 0.5 deg, and
// the Sun to 0.4 deg (mostly precession: these are positions of date, the
// targets are J2000), which moves twilight by about two minutes. Plenty for
// "how high does it get tonight". The night is the first stretch, from now on,
// with the Sun below -12 deg (nautical twilight).
(function () {
  "use strict";
  var D = Math.PI / 180;
  var SITES = [
    ["Palomar (ZTF)", 33.356, -116.863],
    ["Kitt Peak", 31.963, -111.6],
    ["Lick", 37.341, -121.643],
    ["Mauna Kea", 19.826, -155.475],
    ["Roque de los Muchachos (La Palma)", 28.762, -17.89],
    ["Calar Alto", 37.224, -2.546],
    ["Cerro Pachón (Rubin, Gemini-S, SOAR)", -30.241, -70.737],
    ["La Silla", -29.257, -70.738],
    ["Paranal", -24.627, -70.404],
    ["Las Campanas", -29.015, -70.692],
    ["SAAO Sutherland", -32.379, 20.811],
    ["Siding Spring", -31.273, 149.064],
    ["Lulin", 23.469, 120.873],
    ["Xinglong", 40.396, 117.575],
    ["Devasthal (ARIES)", 29.361, 79.685],
    ["Hanle (IAO)", 32.779, 78.964],
  ];
  var SUN_LIMIT = -12, ALT_GOOD = 30, STEP_MIN = 10;

  function jdNow() { return Date.now() / 86400000 + 2440587.5; }

  function eqFromEcliptic(lam, beta, eps) {
    var x = Math.cos(beta) * Math.cos(lam);
    var y = Math.cos(eps) * Math.cos(beta) * Math.sin(lam) - Math.sin(eps) * Math.sin(beta);
    var z = Math.sin(eps) * Math.cos(beta) * Math.sin(lam) + Math.cos(eps) * Math.sin(beta);
    return [Math.atan2(y, x), Math.asin(z)];
  }

  function sun(jd) {  // [ra, dec] in radians
    var n = jd - 2451545.0;
    var L = 280.46 + 0.9856474 * n, g = (357.528 + 0.9856003 * n) * D;
    var lam = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D;
    return eqFromEcliptic(lam, 0, (23.439 - 4e-7 * n) * D);
  }

  function moon(jd) {  // [ra, dec] in radians, geocentric
    var T = (jd - 2451545.0) / 36525, s = function (a, b) { return Math.sin((a + b * T) * D); };
    var lam = 218.32 + 481267.881 * T + 6.29 * s(135.0, 477198.87) - 1.27 * s(259.3, -413335.36)
      + 0.66 * s(235.7, 890534.22) + 0.21 * s(269.9, 954397.74) - 0.19 * s(357.5, 35999.05)
      - 0.11 * s(186.5, 966404.03);
    var beta = 5.13 * s(93.3, 483202.02) + 0.28 * s(228.2, 960400.89) - 0.28 * s(318.3, 6003.15)
      - 0.17 * s(217.6, -407332.21);
    return eqFromEcliptic(lam * D, beta * D, (23.439 - 1.3e-2 * T) * D);
  }

  function lst(jd, lonDeg) {  // local sidereal time, radians
    var g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + lonDeg;
    return (((g % 360) + 360) % 360) * D;
  }

  function altitude(ra, dec, jd, latDeg, lonDeg) {  // radians in, degrees out
    var phi = latDeg * D, H = lst(jd, lonDeg) - ra;
    return Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(H)) / D;
  }

  function separation(a, b) {  // [ra, dec] radians -> degrees
    var c = Math.sin(a[1]) * Math.sin(b[1]) + Math.cos(a[1]) * Math.cos(b[1]) * Math.cos(a[0] - b[0]);
    return Math.acos(Math.max(-1, Math.min(1, c))) / D;
  }

  // The first dark interval within the next 24 h: [startJD, endJD] or null.
  function night(lat, lon, jd0) {
    var step = STEP_MIN / 1440, start = null;
    for (var i = 0; i <= 144; i++) {
      var jd = jd0 + i * step, s = sun(jd);
      var dark = altitude(s[0], s[1], jd, lat, lon) < SUN_LIMIT;
      if (dark && start === null) start = jd;
      if (!dark && start !== null) return [start, jd];
    }
    return start === null ? null : [start, jd0 + 1];
  }

  // Everything the page shows for one target, from one site, tonight.
  function tonight(lat, lon, raDeg, decDeg, jd0) {
    var n = night(lat, lon, jd0 === undefined ? jdNow() : jd0);
    if (!n) return { dark: false };
    var ra = raDeg * D, dec = decDeg * D, step = STEP_MIN / 1440, best = -90, hours = 0;
    for (var jd = n[0]; jd <= n[1]; jd += step) {
      var a = altitude(ra, dec, jd, lat, lon);
      if (a > best) best = a;
      if (a >= ALT_GOOD) hours += STEP_MIN / 60;
    }
    var mid = (n[0] + n[1]) / 2, m = moon(mid), s = sun(mid);
    return {
      dark: true, start: n[0], end: n[1], maxAlt: best, hoursAbove: hours,
      moonSep: separation([ra, dec], m), moonIllum: (1 - Math.cos(separation(m, s) * D)) / 2,
    };
  }

  var api = { SITES: SITES, ALT_GOOD: ALT_GOOD, sun: sun, moon: moon, altitude: altitude, night: night,
              tonight: tonight };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // ---- page wiring: <select id="obs-site"> and cells with data-ra/data-dec ----
  function fill(index) {
    var site = SITES[index], cells = document.querySelectorAll("[data-obs]"), now = jdNow();
    var note = document.getElementById("obs-note");
    var n = night(site[1], site[2], now);
    if (note) {
      if (!n) { note.textContent = "The Sun stays above " + SUN_LIMIT + "° for the next 24 hours at this site."; }
      else {
        var fmt = function (jd) { return new Date((jd - 2440587.5) * 86400000).toUTCString().slice(17, 22); };
        note.textContent = "Dark (Sun below " + SUN_LIMIT + "°) " + fmt(n[0]) + "–" + fmt(n[1]) + " UTC.";
      }
    }
    for (var i = 0; i < cells.length; i++) {
      var c = cells[i], r = tonight(site[1], site[2], +c.dataset.ra, +c.dataset.dec, now);
      if (!r.dark) { c.textContent = "–"; c.title = ""; continue; }
      var up = r.maxAlt >= ALT_GOOD;
      c.textContent = up ? Math.round(r.maxAlt) + "° · " + r.hoursAbove.toFixed(1) + " h" : "low (" + Math.round(r.maxAlt) + "°)";
      c.className = "num obs " + (up ? "obs-up" : "obs-low");
      c.title = "Highest altitude " + r.maxAlt.toFixed(0) + "°, " + r.hoursAbove.toFixed(1) + " h above "
        + ALT_GOOD + "°. Moon " + r.moonSep.toFixed(0) + "° away, " + Math.round(100 * r.moonIllum) + "% lit.";
    }
  }

  function init() {
    var sel = document.getElementById("obs-site");
    if (!sel) return;
    for (var i = 0; i < SITES.length; i++) {
      var o = document.createElement("option"); o.value = i; o.textContent = SITES[i][0]; sel.appendChild(o);
    }
    var saved = 0;
    try { saved = +(localStorage.getItem("nw-obs-site") || 0); } catch (e) { saved = 0; }
    sel.value = saved < SITES.length ? saved : 0;
    sel.addEventListener("change", function () {
      try { localStorage.setItem("nw-obs-site", sel.value); } catch (e) { /* storage blocked: fine */ }
      fill(+sel.value);
    });
    fill(+sel.value);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
