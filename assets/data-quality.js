"use strict";
window.DataQuality = {
  esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  expired(value) { return !!value && Date.now() > Date.parse(value); },
  stock(row) {
    const stale = row.data_quality?.prices?.stale || this.expired(row.price_valid_until);
    return stale ? { ...row, rating: row.price == null ? "No Data" : "Stale", score: null, rs_rank: null, setups: [],
      data_quality: { ...row.data_quality, prices: { ...row.data_quality?.prices, stale: true } } } : row;
  },
  option(row, doc) {
    if (doc.refresh_status === "failed" || this.expired(row.valid_until)) {
      return { ...row, leap_rating: "Stale", leap_score: null, chain_status: "stale",
        historical_contracts: row.contracts?.length ? row.contracts : row.historical_contracts,
        historical_as_of: row.chain_as_of || row.historical_as_of, contracts: [] };
    }
    return row;
  },
  price(row) {
    const quality = row.data_quality?.prices;
    if (!quality) return "";
    return `<span class="data-freshness">${quality.stale ? "Stale · " : ""}As of ${this.esc(row.price_as_of || quality.as_of || "unavailable")}</span>`;
  },
  details(row) {
    const quality = row.data_quality;
    if (!quality) return "";
    const groups = Object.entries(quality.fundamentals || {}).map(([name, q]) => {
      const retained = q.retained_fields?.length ? `; older fields retained: ${q.retained_fields.map(k => `${k} (${q.field_updated_at?.[k] || "date unknown"})`).join(", ")}` : "";
      return `${name}: ${q.status}${q.reason ? ` (${q.reason})` : ""}; updated ${q.updated_at || "unavailable"}${retained}`;
    });
    const missing = Object.entries(quality.missing_fields || {}).map(([k, v]) => `${k}: ${v}`);
    return `<details class="stat-card"><summary>Data freshness and availability</summary>
      <p>${groups.map(x => this.esc(x)).join("<br>")}</p>
      ${missing.length ? `<p>${missing.map(x => this.esc(x)).join("<br>")}</p>` : ""}</details>`;
  },
  confidence(row) {
    const fields = ["trend_score", "momentum_score", "timing_score", "volume_score"];
    const available = fields.filter(k => Number.isFinite(row[k])).length;
    const q = row.data_quality?.prices;
    const stale = q?.stale || this.expired(row.price_valid_until);
    const label = stale ? "Stale prices" : !q ? "Freshness unverified" : available === 4 ? "4/4 score components available" : `${available}/4 score components available`;
    const missing = Object.keys(row.data_quality?.missing_fields || {});
    const inputs = ["sma50", "sma200", "rsi", "rs_rank", "mom_12_1", "udv_ratio"];
    const missingInputs = inputs.filter(k => !Number.isFinite(row[k]));
    return `<details class="confidence"><summary>${this.esc(label)}</summary><p>Data coverage is separate from signal strength; it is not a probability of success. ${missingInputs.length ? `Unavailable technical inputs: ${missingInputs.map(x => this.esc(x)).join(", ")}.` : "Core trend, momentum and volume inputs are available."} ${missing.length ? `Missing supporting fields: ${missing.map(x => this.esc(x)).join(", ")}.` : ""} Fundamentals do not contribute to the technical score.</p>${this.price(row)}</details>`;
  },
  explanation(row, label) {
    if (!Number.isFinite(row.score)) return `<span>${this.esc(row.rating || "No Data")}</span>`;
    const parts = [["Trend", "trend_score"], ["Momentum", "momentum_score"], ["Timing", "timing_score"], ["Volume", "volume_score"]];
    parts.sort((a,b) => Math.abs(row[b[1]] || 0) - Math.abs(row[a[1]] || 0));
    return `<details class="rating-explanation"><summary>${label}</summary><div class="rating-reasons"><p>${this.esc(row.reason || "Technical composite signal")}</p>${parts.map(([name,key]) => `<div>${name}<strong class="${row[key] > 0 ? "pos" : row[key] < 0 ? "neg" : ""}">${Number.isFinite(row[key]) ? (row[key] > 0 ? "+" : "") + row[key] : "Unavailable"}</strong></div>`).join("")}<p>Score uses the unrounded contributions, then rounds and limits the total to −100…100. Displayed components are rounded separately, so their sum can differ slightly. Missing inputs can leave a component neutral.</p><a href="stock.html?symbol=${encodeURIComponent(row.symbol)}">Full stock details →</a></div></details>`;
  },
  banner(doc) {
    if (!["failed", "unavailable"].includes(doc.refresh_status)) return;
    const banner = document.createElement("div");
    banner.className = "banner";
    banner.textContent = doc.refresh_status === "failed"
      ? "The latest refresh was incomplete. Available historical data is dated below."
      : "Live data is not available yet.";
    document.querySelector("main")?.prepend(banner);
  }
};
