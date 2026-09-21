"use strict";

// State
let STOCKS = [];
const expandedMetrics = new Set();
let LEAPS = {};       // symbol -> LEAP rating (from data/leaps.json)
let filterRating = "all";
let filterSector = "all";
let filterWatchlist = false;
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
  ["trend", "Trend"],
  ["rs_rank", "RS rank"],
  ["rsi", "RSI"],
  ["timing", "Timing"],
  ["rvol_mean", "RVOL 30d"],
  ["rvol_high_days", "Surge Days"],
  ["sector", "Sector"],
  ["score", "Rating"],
];

// Columns sorted as text default to ascending; everything else descending.
const TEXT_KEYS = ["symbol", "trend", "timing", "sector"];

const RATING_CLASS = {
  "Strong Buy": "pill-strong-buy",
  Buy: "pill-buy",
  Hold: "pill-hold",
  Sell: "pill-sell",
  "Strong Sell": "pill-strong-sell",
  "No Data": "pill-no-data",
};

// --- Load data ------------------------------------------------------------ //
async function loadLeaps() {
  try {
    const res = await fetch("data/leaps.json", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    (data.candidates || []).map(c => DataQuality.option(c, data)).forEach((c) => { LEAPS[c.symbol] = c.leap_rating; });
  } catch { /* LEAP badges are optional */ }
}

async function load() {
  try {
    await loadLeaps();
    const res = await fetch("data/stocks.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STOCKS = (data.stocks || []).filter(s => DataQuality.inUniverse(s)).map(s => DataQuality.stock(s));
    DataQuality.banner(data);

    populateSectorFilter(data.sectors || []);

    if (data.is_sample) {
      document.getElementById("sample-banner").classList.remove("hidden");
    }
    if (data.generated_at) {
      const newestQuote = STOCKS.map(s => s.displayed_quote?.as_of).filter(Boolean).sort().pop();
      document.getElementById("updated-at").textContent = DataQuality.formatDate(data.generated_at);
    }
    renderBriefing();
    render();
  } catch (err) {
    document.getElementById("stock-body").innerHTML =
      `<tr><td colspan="13" class="empty">Could not load data: ${err.message}</td></tr>`;
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

function scoreNum(score) {
  if (score === null || score === undefined) return "";
  const cls = score > 0 ? "pos" : score < 0 ? "neg" : "";
  return `<span class="score-num ${cls}">${score > 0 ? "+" : ""}${score}</span>`;
}

function leapBadge(sym) {
  const r = LEAPS[sym];
  if (!r) return "";
  const cls = r === "LEAP Buy" ? "leap-buy" : "leap-watch";
  return `<a class="leap-badge ${cls}" href="leaps.html#${encodeURIComponent(sym)}" ` +
    `title="${r === "LEAP Buy" ? "LEAP call candidate — see suggested contracts" : "On the LEAP watch list"}">LEAP</a>`;
}

const TIMING_CLASS = {
  Pullback: "timing-pullback", Breakout: "timing-breakout", "At Highs": "timing-highs",
  Extended: "timing-extended", "Bear Bounce": "timing-bear", Oversold: "timing-bear",
};

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
    const matchesSector = filterSector === "all" || s.sector === filterSector;
    const matchesSearch =
      !term ||
      s.symbol.toLowerCase().includes(term) ||
      (s.name || "").toLowerCase().includes(term);
    const matchesRanges = !FILTER || FILTER.passes(s);
    const matchesWatch = !filterWatchlist ||
      (window.Account && Account.isStarred(s.symbol));
    return matchesRating && matchesSector &&
      matchesSearch && matchesRanges && matchesWatch;
  });

  rows.sort(compare);

  renderSummary(rows);
  renderActiveFilters();

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
        : s.trend === "Bearish" ? "trend-bearish" : "trend-mixed";
      const rsCls = s.rs_rank >= 80 ? "rs-strong" : s.rs_rank <= 20 ? "rs-weak" : "";
      const timingCls = TIMING_CLASS[s.timing] || "mom-neutral";
      const setups = (s.setups || []).join(" · ");
      // Highlight elevated relative volume.
      const rvolCls = s.rvol_mean >= 1.5 ? "rvol-high"
        : s.rvol_mean >= 1.15 ? "rvol-mid" : "";
      const surgeCls = s.rvol_high_days > 0 ? "rvol-high" : "";
      const surgeStr = s.rvol_high_days === null || s.rvol_high_days === undefined
        ? "—" : s.rvol_high_days;
      const acc = window.Account && Account.ready;
      const starred = acc && Account.isStarred(s.symbol);
      const star = acc
        ? `<button class="star${starred ? " on" : ""}" data-sym="${s.symbol}" aria-label="Toggle watchlist" title="Watchlist">${starred ? "★" : "☆"}</button>`
        : "";
      return `
        <tr class="${expandedMetrics.has(s.symbol) ? "metrics-expanded" : ""}">
          <td class="ticker">${star}<a href="stock.html?symbol=${encodeURIComponent(s.symbol)}">${s.symbol}<span class="name">${s.name || ""}</span></a></td>
          <td class="num" data-label="Price">$${fmt(s.price)}${DataQuality.price(s)}</td>
          <td class="num ${chgCls}" data-label="Day %">${chgStr}</td>
          <td class="num" data-label="Mkt Cap">${fmtMarketCap(s.market_cap)}</td>
          <td class="num" data-label="P/E">${fmt(s.pe, 1)}</td>
          <td class="${trendCls}" data-label="Trend">${s.trend}</td>
          <td class="num ${rsCls}" data-label="RS">${s.rs_rank ?? "—"}</td>
          <td class="num" data-label="RSI">${fmt(s.rsi, 1)}</td>
          <td class="${timingCls}" data-label="Timing">${s.timing || "—"}</td>
          <td class="num ${rvolCls}" data-label="RVOL 30d">${fmt(s.rvol_mean)}×</td>
          <td class="num ${surgeCls}" data-label="Surge">${surgeStr}</td>
          <td class="sector-cell" data-label="Sector">${s.sector || "—"}</td>
          <td data-label="Rating" title="${setups}">${DataQuality.explanation(s, pill(s.rating) + scoreNum(s.score))}${DataQuality.confidence(s)}${leapBadge(s.symbol)}<button class="metrics-toggle" data-symbol="${DataQuality.esc(s.symbol)}" aria-expanded="${expandedMetrics.has(s.symbol)}" aria-label="${expandedMetrics.has(s.symbol) ? "Fewer" : "More"} metrics for ${DataQuality.esc(s.symbol)}">${expandedMetrics.has(s.symbol) ? "Fewer metrics −" : "More metrics +"}</button></td>
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

document.getElementById("sector-filter").addEventListener("change", (e) => {
  filterSector = e.target.value;
  render();
});

document.getElementById("rating-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  filterRating = btn.dataset.filter;
  document.querySelectorAll("#rating-filters .chip")
    .forEach((c) => c.classList.remove("active"));
  btn.classList.add("active");
  render();
});

// Star toggle (event-delegated on the table body).
document.getElementById("stock-body").addEventListener("click", (e) => {
  const toggle = e.target.closest(".metrics-toggle");
  if (toggle) {
    const symbol = toggle.dataset.symbol;
    const expanded = !expandedMetrics.has(symbol);
    expanded ? expandedMetrics.add(symbol) : expandedMetrics.delete(symbol);
    toggle.closest("tr").classList.toggle("metrics-expanded", expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", `${expanded ? "Fewer" : "More"} metrics for ${symbol}`);
    toggle.textContent = expanded ? "Fewer metrics −" : "More metrics +";
    return;
  }
  const star = e.target.closest(".star");
  if (!star) return;
  e.preventDefault();
  if (window.Account) Account.toggleStar(star.dataset.sym);
});

// "My Watchlist" toggle.
const watchlistToggle = document.getElementById("watchlist-toggle");
watchlistToggle.addEventListener("click", () => {
  if (window.Account && !Account.isLoggedIn()) { Account.requireLogin(); return; }
  filterWatchlist = !filterWatchlist;
  watchlistToggle.classList.toggle("active", filterWatchlist);
  watchlistToggle.setAttribute("aria-pressed", String(filterWatchlist));
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

// Show the watchlist toggle only when accounts are available, and re-render
// (stars + any watchlist filter) whenever auth/watchlist state changes.
if (window.Account && Account.ready) {
  watchlistToggle.classList.remove("hidden");
  Account.onChange(() => {
    // If the user logs out while filtering their watchlist, drop the filter.
    if (filterWatchlist && !Account.isLoggedIn()) {
      filterWatchlist = false;
      watchlistToggle.classList.remove("active");
      watchlistToggle.setAttribute("aria-pressed", "false");
    }
    render();
  });
}

FILTER = RangeFilters.create({
  button: document.getElementById("filter-btn"),
  panel: document.getElementById("filter-panel"),
  keys: ["price", "change_pct", "market_cap", "pe", "score", "rs_rank",
    "rsi", "hv60", "rvol_mean", "rvol_high_days"],
  onChange: render,
});

load();

function renderBriefing() {
  const current = STOCKS.filter(s => Number.isFinite(s.score));
  const changed = current.filter(s => Number.isFinite(s.previous_signal?.score));
  const buys = new Set(["Buy", "Strong Buy"]);
  const link = (s, text) => `<li><a href="stock.html?symbol=${encodeURIComponent(s.symbol)}">${DataQuality.esc(s.symbol)}</a><span>${DataQuality.esc(text)}</span></li>`;
  const sections = [
    ["New Buy signals", changed.filter(s => buys.has(s.rating) && !buys.has(s.previous_signal.rating)).slice(0,5).map(s => link(s, `${s.previous_signal.rating} → ${s.rating}`)), changed.length ? "No new Buy signals." : "Available after the next dated refresh."],
    ["Biggest score changes", changed.filter(s => s.score !== s.previous_signal.score).sort((a,b) => Math.abs(b.score-b.previous_signal.score)-Math.abs(a.score-a.previous_signal.score)).slice(0,5).map(s => link(s, `${s.score-s.previous_signal.score > 0 ? "+" : ""}${s.score-s.previous_signal.score} · since ${s.previous_signal.price_as_of}`)), "No score changes available."],
    ["Elevated volume", current.filter(s => s.rvol_today >= 2).sort((a,b) => b.rvol_today-a.rvol_today).slice(0,5).map(s => link(s, `${fmt(s.rvol_today)}× · latest session`)), "No stocks above 2× relative volume in the latest session."],
  ];
  document.getElementById("daily-briefing").innerHTML = `<details id="briefing-disclosure" open><summary><h2>Your daily briefing</h2><span class="briefing-hint">Expand / collapse</span></summary><p class="muted">Across the full universe · ${current.length} rated stocks.  </p><div class="briefing-grid">${sections.map(([title, rows, empty]) => `<article class="stat-card"><h3>${title}</h3>${rows.length ? `<ul>${rows.join("")}</ul>` : `<p class="muted">${empty}</p>`}</article>`).join("")}</div></details>`;
  const disclosure = document.getElementById("briefing-disclosure");
  try { disclosure.open = localStorage.getItem("briefing-collapsed") !== "true"; } catch {}
  disclosure.addEventListener("toggle", () => {
    try { localStorage.setItem("briefing-collapsed", String(!disclosure.open)); } catch {}
  });
}

function renderActiveFilters() {
  const selections = [];
  if (searchTerm) selections.push(["search", `Search: ${searchTerm}`]);
  if (filterSector !== "all") selections.push(["sector", filterSector]);
  if (filterRating !== "all") selections.push(["rating", filterRating]);
  if (filterWatchlist) selections.push(["watchlist", "Watchlist"]);
  for (const range of FILTER?.activeFilters?.() || []) selections.push([range.key, range.label]);
  document.getElementById("active-filters").innerHTML = selections.map(([key,label]) => `<button class="filter-token" data-remove="${DataQuality.esc(key)}" aria-label="Remove ${DataQuality.esc(label)}">${DataQuality.esc(label)} <span aria-hidden="true">×</span></button>`).join("");
}
document.getElementById("active-filters").addEventListener("click", e => {
  const button = e.target.closest("[data-remove]");
  if (!button) return;
  const key = button.dataset.remove;
  if (key === "search") { searchTerm = ""; document.getElementById("search").value = ""; }
  else if (key === "sector") { filterSector = "all"; document.getElementById("sector-filter").value = "all"; }
  else if (key === "rating") { filterRating = "all"; document.querySelectorAll("#rating-filters .chip").forEach(b => b.classList.toggle("active", b.dataset.filter === "all")); }
  else if (key === "watchlist") { filterWatchlist = false; watchlistToggle.classList.remove("active"); watchlistToggle.setAttribute("aria-pressed", "false"); }
  else { FILTER.remove(key); return; }
  render();
});
