"use strict";

let STOCKS = [];
let searchTerm = "";
let filterExchange = "all";
let sortKey = "rvol";
let sortDir = "desc";

const TEXT_KEYS = ["symbol", "exchange"];

async function load() {
  try {
    const res = await fetch("data/rvol_scan.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    STOCKS = data.stocks || [];

    populateExchangeFilter(STOCKS);

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
      `<tr><td colspan="7" class="empty">Could not load data: ${err.message}</td></tr>`;
  }
}

function fmt(n, digits = 2) {
  return n === null || n === undefined ? "—" : Number(n).toFixed(digits);
}

function fmtVolume(n) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
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

function populateExchangeFilter(stocks) {
  const exchanges = [...new Set(stocks.map((s) => s.exchange).filter(Boolean))].sort();
  document.getElementById("exchange-filter").innerHTML =
    `<option value="all">All exchanges</option>` +
    exchanges.map((e) => `<option value="${e}">${e}</option>`).join("");
}

function render() {
  const term = searchTerm.trim().toLowerCase();
  let rows = STOCKS.filter((s) => {
    const matchesSearch =
      !term ||
      s.symbol.toLowerCase().includes(term) ||
      (s.name || "").toLowerCase().includes(term);
    const matchesExchange = filterExchange === "all" || s.exchange === filterExchange;
    return matchesSearch && matchesExchange;
  });

  rows.sort(compare);

  const count = document.getElementById("result-count");
  count.textContent = `Showing ${rows.length} of ${STOCKS.length} stocks above RVOL 2.0`;

  document.getElementById("empty-state").classList.toggle("hidden", rows.length > 0);

  document.getElementById("scan-body").innerHTML = rows
    .map((s) => {
      const chg = s.change_pct;
      const chgCls = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
      const chgStr = chg === null || chg === undefined
        ? "—" : `${chg > 0 ? "+" : ""}${fmt(chg)}%`;
      // Stronger highlight the higher the RVOL.
      const rvolCls = s.rvol >= 4 ? "rvol-high" : s.rvol >= 3 ? "rvol-mid" : "";
      return `
        <tr>
          <td class="ticker">${s.symbol}<span class="name">${s.name || ""}</span></td>
          <td class="sector-cell">${s.exchange || "—"}</td>
          <td class="num">$${fmt(s.price)}</td>
          <td class="num ${chgCls}">${chgStr}</td>
          <td class="num">${fmtVolume(s.volume)}</td>
          <td class="num">${fmtVolume(s.avg_volume)}</td>
          <td class="num ${rvolCls}">${fmt(s.rvol)}×</td>
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
      sortKey !== "rvol" ? "Sort by RVOL"
        : sortDir === "asc" ? "RVOL ↑ Asc" : "RVOL ↓ Desc";
    btn.classList.toggle("active", sortKey === "rvol");
  }
}

document.getElementById("search").addEventListener("input", (e) => {
  searchTerm = e.target.value;
  render();
});

document.getElementById("exchange-filter").addEventListener("change", (e) => {
  filterExchange = e.target.value;
  render();
});

// Dedicated RVOL ascending/descending toggle.
document.getElementById("rvol-sort").addEventListener("click", () => {
  if (sortKey !== "rvol") {
    sortKey = "rvol";
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

load();
