"use strict";

/**
 * Reusable numeric range-filter panel, shared by the Signals and Scanner pages.
 *
 * Usage:
 *   const FILTER = RangeFilters.create({ button, panel, keys, onChange });
 *   ... FILTER.passes(stock) ...   // true if the row clears all active filters
 *
 * Each metric carries a "smart" spec (sensible min/max/step/placeholders) so the
 * inputs make sense for that indicator (RSI 0-100, market cap accepts 1M/2B/3T…).
 */
window.RangeFilters = (function () {
  // money: render two text inputs that accept 1.5M / 2B / 3T shorthand.
  const SPECS = {
    price:          { label: "Price ($)",      min: 0,         step: 0.01, ph: ["0", "1000"] },
    change_pct:     { label: "Day % change",                   step: 0.1,  ph: ["-5", "5"] },
    market_cap:     { label: "Market Cap",      money: true,                ph: ["1M", "3T"] },
    pe:             { label: "P/E ratio",       min: 0,         step: 0.1,  ph: ["0", "50"] },
    ema50:          { label: "EMA 50 ($)",      min: 0,         step: 0.01, ph: ["0", ""] },
    ema200:         { label: "EMA 200 ($)",     min: 0,         step: 0.01, ph: ["0", ""] },
    rsi:            { label: "RSI",             min: 0, max: 100, step: 1,  ph: ["0", "100"] },
    rvol_mean:      { label: "RVOL 30d (×)",    min: 0,         step: 0.1,  ph: ["0", "10"] },
    rvol_high_days: { label: "Surge Days",      min: 0, max: 30, step: 1,   ph: ["0", "30"] },
    rvol_today:     { label: "RVOL Today (×)",  min: 0,         step: 0.1,  ph: ["2", "20"] },
  };

  // Parse 1.5M / 2B / 3T / 900000 -> Number, or null if blank/invalid.
  function parseMoney(raw) {
    if (raw == null) return null;
    const s = String(raw).trim().toUpperCase().replace(/[$,\s]/g, "");
    if (s === "") return null;
    const m = s.match(/^(-?\d*\.?\d+)([KMBT]?)$/);
    if (!m) return null;
    const mult = { "": 1, K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[m[2]];
    return parseFloat(m[1]) * mult;
  }

  function parseNum(raw) {
    if (raw == null || String(raw).trim() === "") return null;
    const n = Number(raw);
    return Number.isNaN(n) ? null : n;
  }

  function create({ button, panel, keys, onChange }) {
    const state = {}; // key -> { min, max }
    keys.forEach((k) => (state[k] = { min: null, max: null }));

    const baseLabel = button.textContent.trim() || "Filters";

    // Build the panel UI.
    const rows = keys
      .map((key) => {
        const spec = SPECS[key];
        if (!spec) return "";
        const type = spec.money ? "text" : "number";
        const attrs = spec.money
          ? ""
          : [
              spec.min != null ? `min="${spec.min}"` : "",
              spec.max != null ? `max="${spec.max}"` : "",
              spec.step != null ? `step="${spec.step}"` : "",
            ].join(" ");
        return `
          <div class="filter-row">
            <label for="f-${key}-min">${spec.label}</label>
            <input id="f-${key}-min" data-key="${key}" data-bound="min"
                   type="${type}" ${attrs} placeholder="${spec.ph[0] || "min"}"
                   inputmode="decimal" aria-label="${spec.label} minimum" />
            <input id="f-${key}-max" data-key="${key}" data-bound="max"
                   type="${type}" ${attrs} placeholder="${spec.ph[1] || "max"}"
                   inputmode="decimal" aria-label="${spec.label} maximum" />
          </div>`;
      })
      .join("");

    panel.innerHTML = `
      <div class="filter-head"><span>Metric</span><span>Min</span><span>Max</span></div>
      ${rows}
      <div class="panel-actions">
        <button type="button" class="chip" data-action="clear">Clear all</button>
        <button type="button" class="chip active" data-action="close">Done</button>
      </div>`;

    function activeCount() {
      return keys.reduce(
        (n, k) => n + (state[k].min != null || state[k].max != null ? 1 : 0),
        0
      );
    }

    function refreshBadge() {
      const n = activeCount();
      button.textContent = n ? `${baseLabel} (${n})` : baseLabel;
      button.classList.toggle("active", n > 0);
    }

    function readInput(input) {
      const spec = SPECS[input.dataset.key];
      const val = spec.money ? parseMoney(input.value) : parseNum(input.value);
      state[input.dataset.key][input.dataset.bound] = val;
    }

    // Live-apply on input.
    panel.querySelectorAll("input").forEach((input) => {
      input.addEventListener("input", () => {
        readInput(input);
        refreshBadge();
        onChange();
      });
    });

    function clearAll() {
      panel.querySelectorAll("input").forEach((i) => (i.value = ""));
      keys.forEach((k) => (state[k] = { min: null, max: null }));
      refreshBadge();
      onChange();
    }

    function open() { panel.classList.remove("hidden"); }
    function close() { panel.classList.add("hidden"); }
    function toggle() { panel.classList.toggle("hidden"); }

    button.addEventListener("click", (e) => {
      e.stopPropagation();
      toggle();
    });

    panel.addEventListener("click", (e) => {
      e.stopPropagation();
      const action = e.target.dataset.action;
      if (action === "clear") clearAll();
      else if (action === "close") close();
    });

    // Close when clicking outside the panel/button.
    document.addEventListener("click", (e) => {
      if (!panel.contains(e.target) && e.target !== button) close();
    });

    refreshBadge();

    return {
      passes(stock) {
        for (const key of keys) {
          const f = state[key];
          if (f.min == null && f.max == null) continue;
          const v = stock[key];
          if (v == null) return false; // no value can't satisfy a numeric range
          if (f.min != null && v < f.min) return false;
          if (f.max != null && v > f.max) return false;
        }
        return true;
      },
      activeCount,
      clear: clearAll,
    };
  }

  return { create, parseMoney };
})();
