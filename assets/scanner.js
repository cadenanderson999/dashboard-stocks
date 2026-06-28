"use strict";

let STOCKS = [];
let searchTerm = "";
let filterSector = "all";
let sortKey = "rvol_today";
let sortDir = "desc";
let FILTER = null;    // numeric range-filter panel (assigned at the bottom)

const TEXT_KEYS = ["symbol", "trend", "momentum", "sector"];

const RATING_CLASS = {
  "Strong Buy": "pill-strong-buy",
  Buy: "pill-buy",
  Hold: "pill-hold",
  Sell: "pill-sell",
  "Strong Sell": "pill-strong-sell",
  "No Data": "pill-no-data",
};

async function load() {
  try {
    const res = await fetch("data/rvol_scan.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STOCKS = data.stocks || [];

    populateSectorFilter(STOCKS);

    if (data.is_sample) {
      document.getElementById("sample-banner").classList.remove("hidden");
    }
    if (data.generated_at) {
      document.getElementById("updated-at").textContent =
        new Date(data.generated_at).toLocaleString();
    }
    render();
  } catch (err) {
    document.getElementById("scan-body").innerHTML =
      `<tr><td colspan="15" class="empty">Could not load data: ${err.message}</td></tr>`;
  }
}

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
  return `<span class="pill ${RATING_CLASS[rating] || "pill-no-data"}">${rating}</span>`;
}

function compare(a, b) {
  let av = a[sortKey];
  let bv = b[sortKey];
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

function populateSectorFilter(stocks) {
  const sectors = [...new Set(stocks.map((s) => s.sector).filter(Boolean))].sort();
  document.getElementById("sector-filter").innerHTML =
    `<option value="all">All sectors</option>` +
    sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
}

function render() {
  const term = searchTerm.trim().toLowerCase();
  let rows = STOCKS.filter((s) => {
    const matchesSearch =
      !term ||
      s.symbol.toLowerCase().includes(term) ||
      (s.name || "").toLowerCase().includes(term);
    const matchesSector = filterSector === "all" || s.sector === filterSector;
    const matchesRanges = !FILTER || FILTER.passes(s);
    return matchesSearch && matchesSector && matchesRanges;
  });

  rows.sort(compare);

  document.getElementById("result-count").textContent =
    `Showing ${rows.length} of ${STOCKS.length} stocks above RVOL 2.0`;
  document.getElementById("empty-state").classList.toggle("hidden", rows.length > 0);

  document.getElementById("scan-body").innerHTML = rows
    .map((s) => {
      const chg = s.change_pct;
      const chgCls = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
      const chgStr = chg === null || chg === undefined
        ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg)}%`;
      const trendCls = s.trend === "Bullish" ? "trend-bullish"
        : s.trend === "Bearish" ? "trend-bearish" : "";
      const momCls = s.momentum === "Oversold" ? "mom-oversold"
        : s.momentum === "Overbought" ? "mom-overbought" : "mom-neutral";
      const rvolCls = s.rvol_mean >= 1.5 ? "rvol-high"
        : s.rvol_mean >= 1.15 ? "rvol-mid" : "";
      const surgeCls = s.rvol_high_days > 0 ? "rvol-high" : "";
      const surgeStr = s.rvol_high_days === null || s.rvol_high_days === undefined
        ? "—" : s.rvol_high_days;
      const todayCls = s.rvol_today >= 4 ? "rvol-high" : s.rvol_today >= 3 ? "rvol-mid" : "";
      return `
        <tr>
          <td class="ticker">${s.symbol}<span class="name">${s.name || ""}</span></td>
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
          <td class="num ${todayCls}">${fmt(s.rvol_today)}×</td>
          <td class="sector-cell">${s.sector || "—"}</td>
          <td>${pill(s.rating)}<span class="reason">${s.reason || ""}</span></td>
        </tr>`;
    })
    .join("");

  updateSortHeaders();
}

function updateSortHeaders() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.classList.remove("sorted-asc", "sorted-desc");
    if (th.dataset.key === sortKey) {
      th.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
    }
  });
  const btn = document.getElementById("rvol-sort");
  if (btn) {
    btn.textContent =
      sortKey !== "rvol_today" ? "Sort by RVOL"
        : sortDir === "asc" ? "RVOL ↑ Asc" : "RVOL ↓ Desc";
    btn.classList.toggle("active", sortKey === "rvol_today");
  }
}

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

document.getElementById("sector-filter").addEventListener("change", (e) => {
  filterSector = e.target.value;
  render();
});

// Dedicated RVOL (today) ascending/descending toggle.
document.getElementById("rvol-sort").addEventListener("click", () => {
  if (sortKey !== "rvol_today") {
    sortKey = "rvol_today";
    sortDir = "desc";
  } else {
    sortDir = sortDir === "desc" ? "asc" : "desc";
  }
  render();
});

document.querySelectorAll("th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (sortKey === key) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortKey = key;
      sortDir = TEXT_KEYS.includes(key) ? "asc" : "desc";
    }
    render();
  });
});

FILTER = RangeFilters.create({
  button: document.getElementById("filter-btn"),
  panel: document.getElementById("filter-panel"),
  keys: ["price", "change_pct", "market_cap", "pe", "ema50", "ema200",
    "rsi", "rvol_mean", "rvol_high_days", "rvol_today"],
  onChange: render,
});

load();
