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
