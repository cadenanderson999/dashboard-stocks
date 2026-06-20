"use strict";

// State
let STOCKS = [];
let filterRating = "all";
let filterUniverse = "all";
let filterSector = "all";
let searchTerm = "";
let sortKey = "score";
let sortDir = "desc"; // 'asc' | 'desc'
let FILTER = null;    // numeric range-filter panel (assigned at the bottom)

// Every numeric/string column the user can sort by (key -> label).
// Order mirrors the table; used to build the "Sort by" dropdown.
const SORT_OPTIONS = [
  ["symbol", "Ticker"],
  ["price", "Price"],
  ["change_pct", "Day %"],
  ["market_cap", "Market Cap"],
  ["pe", "P/E"],
  ["ema50", "EMA 50"],
  ["ema200", "EMA 200"],
  ["trend", "Trend"],
  ["rsi", "RSI"],
  ["momentum", "Momentum"],
  ["rvol_mean", "RVOL 30d"],
  ["rvol_high_days", "Surge Days"],
  ["sector", "Sector"],
  ["score", "Rating"],
];

// Columns sorted as text default to ascending; everything else descending.
const TEXT_KEYS = ["symbol", "trend", "momentum", "sector"];

const RATING_CLASS = {
  "Strong Buy": "pill-strong-buy",
  Buy: "pill-buy",
  Hold: "pill-hold",
  Sell: "pill-sell",
  "Strong Sell": "pill-strong-sell",
  "No Data": "pill-no-data",
};

// --- Load data ------------------------------------------------------------ //
async function load() {
  try {
    const res = await fetch("data/stocks.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STOCKS = data.stocks || [];

    populateUniverseFilter(data.lists || []);
    populateSectorFilter(data.sectors || []);

    if (data.is_sample) {
      document.getElementById("sample-banner").classList.remove("hidden");
    }
    if (data.generated_at) {
      const d = new Date(data.generated_at);
      document.getElementById("updated-at").textContent = d.toLocaleString();
    }
    render();
  } catch (err) {
    document.getElementById("stock-body").innerHTML =
      `<tr><td colspan="14" class="empty">Could not load data: ${err.message}</td></tr>`;
  }
}

// --- Helpers -------------------------------------------------------------- //
function fmt(n, digits = 2) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}

function fmtMarketCap(n) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + Number(n).toFixed(0);
}

function pill(rating) {
  const cls = RATING_CLASS[rating] || "pill-no-data";
  return `<span class="pill ${cls}">${rating}</span>`;
}

function compare(a, b) {
  let av = a[sortKey];
  let bv = b[sortKey];
  // Nulls always sort last.
  if (av === null || av === undefined) return 1;
  if (bv === null || bv === undefined) return -1;
  if (typeof av === "string") {
    av = av.toLowerCase();
    bv = bv.toLowerCase();
  }
  if (av < bv) return sortDir === "asc" ? -1 : 1;
  if (av > bv) return sortDir === "asc" ? 1 : -1;
  return 0;
}

// --- Render --------------------------------------------------------------- //
function render() {
  const term = searchTerm.trim().toLowerCase();
  let rows = STOCKS.filter((s) => {
    const matchesRating = filterRating === "all" || s.rating === filterRating;
    const matchesUniverse =
      filterUniverse === "all" || (s.lists || []).includes(filterUniverse);
    const matchesSector = filterSector === "all" || s.sector === filterSector;
    const matchesSearch =
      !term ||
      s.symbol.toLowerCase().includes(term) ||
      (s.name || "").toLowerCase().includes(term);
    const matchesRanges = !FILTER || FILTER.passes(s);
    return matchesRating && matchesUniverse && matchesSector &&
      matchesSearch && matchesRanges;
  });

  rows.sort(compare);

  renderSummary(rows);

  const count = document.getElementById("result-count");
  if (count) {
    count.textContent = `Showing ${rows.length} of ${STOCKS.length} stocks`;
  }

  const body = document.getElementById("stock-body");
  document.getElementById("empty-state").classList.toggle("hidden", rows.length > 0);

  body.innerHTML = rows
    .map((s) => {
      const chg = s.change_pct;
      const chgCls = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
      const chgStr = chg === null || chg === undefined
        ? "—"
        : `${chg > 0 ? "+" : ""}${fmt(chg)}%`;
      const trendCls = s.trend === "Bullish" ? "trend-bullish"
        : s.trend === "Bearish" ? "trend-bearish" : "";
      const momCls = s.momentum === "Oversold" ? "mom-oversold"
        : s.momentum === "Overbought" ? "mom-overbought" : "mom-neutral";
      // Highlight elevated relative volume.
      const rvolCls = s.rvol_mean >= 1.5 ? "rvol-high"
        : s.rvol_mean >= 1.15 ? "rvol-mid" : "";
      const surgeCls = s.rvol_high_days > 0 ? "rvol-high" : "";
      const surgeStr = s.rvol_high_days === null || s.rvol_high_days === undefined
        ? "—" : s.rvol_high_days;
      const badges = (s.lists || [])
        .map((l) => `<span class="badge badge-${l === "S&P 500" ? "sp" : "rh"}">` +
          `${l === "S&P 500" ? "S&P" : "RH"}</span>`)
        .join("");
      return `
        <tr>
          <td class="ticker">${s.symbol}${badges}<span class="name">${s.name || ""}</span></td>
          <td class="num">$${fmt(s.price)}</td>
          <td class="num ${chgCls}">${chgStr}</td>
          <td class="num">${fmtMarketCap(s.market_cap)}</td>
          <td class="num">${fmt(s.pe, 1)}</td>
          <td class="num">${fmt(s.ema50)}</td>
          <td class="num">${fmt(s.ema200)}</td>
          <td class="${trendCls}">${s.trend}</td>
          <td class="num">${fmt(s.rsi, 1)}</td>
          <td class="${momCls}">${s.momentum}</td>
          <td class="num ${rvolCls}">${fmt(s.rvol_mean)}×</td>
          <td class="num ${surgeCls}">${surgeStr}</td>
          <td class="sector-cell">${s.sector || "—"}</td>
          <td>${pill(s.rating)}<span class="reason">${s.reason || ""}</span></td>
        </tr>`;
    })
    .join("");

  updateSortHeaders();
}

function renderSummary(rows) {
  const counts = { "Strong Buy": 0, Buy: 0, Hold: 0, Sell: 0, "Strong Sell": 0 };
  rows.forEach((s) => {
    if (counts[s.rating] !== undefined) counts[s.rating]++;
  });
  const cards = [
    ["Strong Buy", counts["Strong Buy"], "pos"],
    ["Buy", counts["Buy"], "pos"],
    ["Hold", counts["Hold"], ""],
    ["Sell", counts["Sell"], "neg"],
    ["Strong Sell", counts["Strong Sell"], "neg"],
  ];
  document.getElementById("summary").innerHTML = cards
    .map(
      ([label, n, cls]) =>
        `<div class="card"><div class="n ${cls}">${n}</div><div class="l">${label}</div></div>`
    )
    .join("");
}

function updateSortHeaders() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.key === sortKey) {
      th.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
    }
  });
  syncSortControls();
}

// Default direction when a new sort key is picked.
function defaultDirFor(key) {
  return TEXT_KEYS.includes(key) ? "asc" : "desc";
}

function populateSortControls() {
  const sel = document.getElementById("sort-key");
  sel.innerHTML = SORT_OPTIONS.map(
    ([key, label]) => `<option value="${key}">${label}</option>`
  ).join("");
}

function populateUniverseFilter(lists) {
  const sel = document.getElementById("universe-filter");
  sel.innerHTML =
    `<option value="all">All lists</option>` +
    lists.map((l) => `<option value="${l}">${l}</option>`).join("");
}

function populateSectorFilter(sectors) {
  const sel = document.getElementById("sector-filter");
  sel.innerHTML =
    `<option value="all">All sectors</option>` +
    sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
}

// Reflect current sortKey/sortDir in the dropdown + direction button.
function syncSortControls() {
  const sel = document.getElementById("sort-key");
  if (sel && sel.value !== sortKey) sel.value = sortKey;
  const btn = document.getElementById("sort-dir");
  if (btn) btn.textContent = sortDir === "asc" ? "↑ Asc" : "↓ Desc";
}

// --- Events --------------------------------------------------------------- //
document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

document.getElementById("universe-filter").addEventListener("change", (e) => {
  filterUniverse = e.target.value;
  render();
});

document.getElementById("sector-filter").addEventListener("change", (e) => {
  filterSector = e.target.value;
  render();
});

document.getElementById("rating-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  filterRating = btn.dataset.filter;
  document.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
  btn.classList.add("active");
  render();
});

document.querySelectorAll("th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortKey = key;
      sortDir = defaultDirFor(key);
    }
    render();
  });
});

// "Sort by" dropdown: picking a metric applies its sensible default direction.
document.getElementById("sort-key").addEventListener("change", (e) => {
  sortKey = e.target.value;
  sortDir = defaultDirFor(sortKey);
  render();
});

// Direction toggle button.
document.getElementById("sort-dir").addEventListener("click", () => {
  sortDir = sortDir === "asc" ? "desc" : "asc";
  render();
});

populateSortControls();

FILTER = RangeFilters.create({
  button: document.getElementById("filter-btn"),
  panel: document.getElementById("filter-panel"),
  keys: ["price", "change_pct", "market_cap", "pe", "ema50", "ema200",
    "rsi", "rvol_mean", "rvol_high_days"],
  onChange: render,
});

load();
