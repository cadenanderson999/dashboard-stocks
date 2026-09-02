"use strict";

// State
let CANDS = [];
let filterRating = "all";
let filterSector = "all";
let searchTerm = "";
let sortKey = "leap_score";
let sortDir = "desc";

const SORT_OPTIONS = [
  ["leap_score", "LEAP score"],
  ["score", "Signal score"],
  ["rs_rank", "RS rank"],
  ["hv60", "Volatility"],
  ["iv_atm", "Implied vol"],
  ["analyst_upside_pct", "Analyst upside"],
  ["revenue_growth_yoy", "Revenue growth"],
  ["market_cap", "Market cap"],
  ["change_pct", "Day %"],
  ["symbol", "Ticker"],
];
const TEXT_KEYS = ["symbol"];

const RATING_CLASS = {
  "Strong Buy": "pill-strong-buy", Buy: "pill-buy", Hold: "pill-hold",
  Sell: "pill-sell", "Strong Sell": "pill-strong-sell", "No Data": "pill-no-data",
};

// --- helpers ---------------------------------------------------------------- //
function fmt(n, digits = 2) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}
function pct(n, digits = 1) {
  return n === null || n === undefined ? "—" : `${n > 0 ? "+" : ""}${fmt(n, digits)}%`;
}
function fmtMarketCap(n) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
  return "$" + Number(n).toFixed(0);
}
function fmtDate(str) {
  if (!str) return "—";
  const d = new Date(str + "T00:00:00");
  return Number.isNaN(d.getTime())
    ? str : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function titleCase(s) {
  return s ? s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—";
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// --- load ------------------------------------------------------------------- //
async function load() {
  const list = document.getElementById("leap-list");
  try {
    const res = await fetch("data/leaps.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    CANDS = data.candidates || [];
    if (data.is_sample) document.getElementById("sample-banner").classList.remove("hidden");
    if (data.chains_available === false && CANDS.length) {
      document.getElementById("chain-banner").classList.remove("hidden");
    }
    if (data.generated_at) {
      document.getElementById("updated-at").textContent =
        new Date(data.generated_at).toLocaleString();
    }
    populateSectorFilter([...new Set(CANDS.map((c) => c.sector).filter(Boolean))].sort());
    render();
    // Deep link: leaps.html#AAPL opens + scrolls to that card.
    const target = decodeURIComponent(location.hash.slice(1)).toUpperCase();
    if (target) {
      const card = document.getElementById(`leap-${target}`);
      if (card) {
        card.querySelector("details")?.setAttribute("open", "");
        card.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  } catch (err) {
    list.innerHTML = `<p class="empty">Could not load LEAP data: ${esc(err.message)}. ` +
      `Run <code>scripts/generate_leaps.py</code> after <code>generate_data.py</code>.</p>`;
  }
}

// --- render ----------------------------------------------------------------- //
function compare(a, b) {
  let av = a[sortKey];
  let bv = b[sortKey];
  if (av === null || av === undefined) return 1;
  if (bv === null || bv === undefined) return -1;
  if (typeof av === "string") { av = av.toLowerCase(); bv = bv.toLowerCase(); }
  if (av < bv) return sortDir === "asc" ? -1 : 1;
  if (av > bv) return sortDir === "asc" ? 1 : -1;
  return 0;
}

function checkRow(name, c) {
  const w = c.max ? Math.round(100 * c.points / c.max) : 0;
  const cls = w >= 70 ? "pos" : w >= 40 ? "" : "neg";
  return `
    <div class="check">
      <div class="check-l">${titleCase(name)}<span class="muted"> · ${esc(c.detail)}</span></div>
      <div class="check-track"><div class="check-fill ${cls}" style="width:${w}%"></div></div>
      <div class="check-v">${c.points}/${c.max}</div>
    </div>`;
}

function contractsTable(c) {
  const rows = c.contracts || [];
  if (!rows.length) {
    const msg = c.chain_status === "no_leaps"
      ? "No expiries ≥ 1 year out are listed for this ticker."
      : c.chain_status === "not_fetched"
        ? "Option chain not fetched this run (only the top candidates are enriched)."
        : "Option chain unavailable on the last run.";
    return `<p class="muted small">${msg}</p>`;
  }
  let lastExp = null;
  const body = rows.map((k) => {
    const sep = k.expiry !== lastExp
      ? `<tr class="exp-row"><td colspan="10">Expiry ${fmtDate(k.expiry)} · ${k.dte} days</td></tr>` : "";
    lastExp = k.expiry;
    return sep + `
      <tr class="${k.liquid ? "" : "illiquid"}" title="${esc(k.why || "")}">
        <td><strong>${esc(k.role)}</strong><span class="muted small"> Δ≈${k.target_delta}</span></td>
        <td class="num">$${fmt(k.strike, k.strike % 1 ? 2 : 0)}</td>
        <td class="num">$${fmt(k.mid)}<span class="muted small"> ${fmt(k.bid)}–${fmt(k.ask)}</span></td>
        <td class="num">$${fmt(k.cost, 0)}</td>
        <td class="num">${fmt(k.delta)}</td>
        <td class="num">${fmt(k.iv, 0)}%</td>
        <td class="num">${k.oi ?? "—"}${k.liquid ? "" : ' <span class="neg" title="Low open interest or wide spread">!</span>'}</td>
        <td class="num">$${fmt(k.breakeven)}<span class="muted small"> ${pct(k.breakeven_pct)}</span></td>
        <td class="num">${fmt(k.extrinsic_pct, 1)}%</td>
        <td class="num">${fmt(k.leverage, 1)}×</td>
      </tr>`;
  }).join("");
  return `
    <div class="table-scroll mini-scroll">
      <table class="mini-table contracts">
        <thead><tr>
          <th>Style</th><th class="num">Strike</th><th class="num">Mid (bid–ask)</th>
          <th class="num" title="Premium per contract (×100)">Cost</th>
          <th class="num" title="Black-Scholes delta">Δ</th><th class="num" title="Implied volatility">IV</th>
          <th class="num" title="Open interest">OI</th><th class="num">Breakeven</th>
          <th class="num" title="Time value as % of the share price — what you pay for the option's duration">Time value</th>
          <th class="num" title="Share price ÷ premium">Lev.</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

function card(c) {
  const isBuy = c.leap_rating === "LEAP Buy";
  const chg = c.change_pct;
  const chgCls = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
  const checks = Object.entries(c.checks || {}).map(([k, v]) => checkRow(k, v)).join("");
  const stats = [
    ["Signal", `<span class="pill ${RATING_CLASS[c.rating] || "pill-no-data"}">${esc(c.rating)}</span> ${c.score > 0 ? "+" : ""}${c.score ?? "—"}`],
    ["RS rank", c.rs_rank ?? "—"],
    ["Trend Template", c.tt_pass != null ? `${c.tt_pass}/8` : "—"],
    ["Timing", esc(c.timing || "—")],
    ["Realised vol (60d)", c.hv60 != null ? `${fmt(c.hv60, 0)}%` : "—"],
    ["Implied vol (LEAP ATM)", c.iv_atm != null
      ? `${fmt(c.iv_atm, 0)}%${c.iv_hv_ratio != null ? ` <span class="muted">(${fmt(c.iv_hv_ratio, 2)}× realised${c.iv_hv_ratio > 1.4 ? " · pricey" : c.iv_hv_ratio < 0.9 ? " · cheap" : ""})</span>` : ""}` : "—"],
    ["Analyst upside", pct(c.analyst_upside_pct)],
    ["Revenue growth (YoY)", pct(c.revenue_growth_yoy)],
    ["Market cap", fmtMarketCap(c.market_cap)],
    ["Next earnings", fmtDate(c.next_earnings)],
    ["6-month return", pct(c.ret_6m)],
    ["Off 52-wk high", pct(c.pct_off_high)],
  ].map(([l, v]) => `<div class="stat"><span class="stat-l">${l}</span><span class="stat-v">${v}</span></div>`).join("");

  return `
    <article class="leap-card-full" id="leap-${esc(c.symbol)}">
      <div class="leap-head">
        <div>
          <div class="stock-symbol"><a href="stock.html?symbol=${encodeURIComponent(c.symbol)}">${esc(c.symbol)}</a></div>
          <div class="stock-name">${esc(c.name || "")}${c.sector ? ` · <span class="muted">${esc(c.sector)}</span>` : ""}</div>
          <div class="setups">${(c.setups || []).map((x) => `<span class="setup-chip">${esc(x)}</span>`).join("")}</div>
        </div>
        <div class="stock-price-box">
          <div class="stock-price">$${fmt(c.price)} <span class="stock-change ${chgCls}">${pct(chg, 2)}</span></div>
          <div><span class="pill ${isBuy ? "pill-strong-buy" : "pill-hold"}">${esc(c.leap_rating)}</span>
            <span class="score-num">${c.leap_score}/100</span></div>
        </div>
      </div>
      <div class="leap-body">
        <div class="checks">${checks}</div>
        <div class="leap-stats">${stats}</div>
      </div>
      <details ${isBuy ? "open" : ""}>
        <summary>Suggested contracts${(c.contracts || []).length ? ` (${c.contracts.length})` : ""}</summary>
        ${contractsTable(c)}
      </details>
    </article>`;
}

function render() {
  const term = searchTerm.trim().toLowerCase();
  const rows = CANDS.filter((c) =>
    (filterRating === "all" || c.leap_rating === filterRating) &&
    (filterSector === "all" || c.sector === filterSector) &&
    (!term || c.symbol.toLowerCase().includes(term) || (c.name || "").toLowerCase().includes(term)));
  rows.sort(compare);

  const buy = CANDS.filter((c) => c.leap_rating === "LEAP Buy").length;
  const withContracts = CANDS.filter((c) => (c.contracts || []).length).length;
  document.getElementById("summary").innerHTML = [
    ["LEAP Buy", buy, "pos"], ["Watch", CANDS.length - buy, ""],
    ["With contracts", withContracts, ""],
  ].map(([l, n, cls]) =>
    `<div class="card"><div class="n ${cls}">${n}</div><div class="l">${l}</div></div>`).join("");
  document.getElementById("result-count").textContent =
    `Showing ${rows.length} of ${CANDS.length} candidates`;
  document.getElementById("empty-state").classList.toggle("hidden", rows.length > 0);
  document.getElementById("leap-list").innerHTML = rows.map(card).join("");
  syncSortControls();
}

// --- controls ----------------------------------------------------------------- //
function populateSectorFilter(sectors) {
  document.getElementById("sector-filter").innerHTML =
    `<option value="all">All sectors</option>` +
    sectors.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
}
function syncSortControls() {
  const sel = document.getElementById("sort-key");
  if (sel && sel.value !== sortKey) sel.value = sortKey;
  document.getElementById("sort-dir").textContent = sortDir === "asc" ? "↑ Asc" : "↓ Desc";
}
document.getElementById("sort-key").innerHTML =
  SORT_OPTIONS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("");
document.getElementById("search").addEventListener("input", (e) => { searchTerm = e.target.value; render(); });
document.getElementById("sector-filter").addEventListener("change", (e) => { filterSector = e.target.value; render(); });
document.getElementById("rating-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  filterRating = btn.dataset.filter;
  document.querySelectorAll("#rating-filters .chip").forEach((c) => c.classList.remove("active"));
  btn.classList.add("active");
  render();
});
document.getElementById("sort-key").addEventListener("change", (e) => {
  sortKey = e.target.value;
  sortDir = TEXT_KEYS.includes(sortKey) ? "asc" : "desc";
  render();
});
document.getElementById("sort-dir").addEventListener("click", () => {
  sortDir = sortDir === "asc" ? "desc" : "asc";
  render();
});

load();
