"use strict";

// State
let STOCKS = [];
let filterRating = "all";
let searchTerm = "";
let sortKey = "score";
let sortDir = "desc"; // 'asc' | 'desc'

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
      `<tr><td colspan="9" class="empty">Could not load data: ${err.message}</td></tr>`;
  }
}

// --- Helpers -------------------------------------------------------------- //
function fmt(n, digits = 2) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
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
    const matchesSearch =
      !term ||
      s.symbol.toLowerCase().includes(term) ||
      (s.name || "").toLowerCase().includes(term);
    return matchesRating && matchesSearch;
  });

  rows.sort(compare);

  renderSummary();

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
      return `
        <tr>
          <td class="ticker">${s.symbol}<span class="name">${s.name || ""}</span></td>
          <td class="num">$${fmt(s.price)}</td>
          <td class="num ${chgCls}">${chgStr}</td>
          <td class="num">${fmt(s.ema50)}</td>
          <td class="num">${fmt(s.ema200)}</td>
          <td class="${trendCls}">${s.trend}</td>
          <td class="num">${fmt(s.rsi, 1)}</td>
          <td class="${momCls}">${s.momentum}</td>
          <td>${pill(s.rating)}<span class="reason">${s.reason || ""}</span></td>
        </tr>`;
    })
    .join("");

  updateSortHeaders();
}

function renderSummary() {
  const counts = { "Strong Buy": 0, Buy: 0, Hold: 0, Sell: 0, "Strong Sell": 0 };
  STOCKS.forEach((s) => {
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
}

// --- Events --------------------------------------------------------------- //
document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
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
      // Numeric-ish columns default to descending; text to ascending.
      sortDir = ["symbol", "trend", "momentum"].includes(key) ? "asc" : "desc";
    }
    render();
  });
});

load();
