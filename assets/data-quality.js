"use strict";
window.DataQuality = {
  esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  formatDate(value) {
    if (!value) return "Unavailable";
    const text = String(value);
    const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    // A market-session date is a calendar label, not midnight UTC.
    if (day) return `${Number(day[2])}/${Number(day[3])}/${day[1]}`;
    const date = new Date(text);
    if (!Number.isFinite(date.getTime())) return "Unavailable";
    const zone = { timeZone: "America/New_York" };
    const dateText = date.toLocaleDateString("en-US", { ...zone, month:"numeric", day:"numeric", year:"numeric" });
    const timeText = date.toLocaleTimeString("en-US", { ...zone, hour:"numeric", minute:"2-digit", hour12:true }).replace(/\s/g, "");
    return `${dateText} · ${timeText} ET`;
  },
  expired(value) { return !!value && Date.now() > Date.parse(value); },
  inUniverse(row) {
    return !row.lists?.includes("Earnings watch") || row.lists.some(x => x !== "Earnings watch");
  },
  stock(row) {
    const rated = row;
    const quote = row.quote;
    if (quote && Number.isFinite(quote.price) && quote.as_of &&
        quote.as_of.slice(0,10) > (row.price_as_of || "")) {
      return { ...rated, signal_price: row.price, price: quote.price,
        change_pct: quote.change_pct, displayed_quote: quote };
    }
    return rated;
  },
  option(row, doc) { return row; },
  price(row) { return ""; },
  details(row) { return ""; },
  confidence(row) { return ""; },
  explanation(row, label) {
    if (!Number.isFinite(row.score)) return `<span>${this.esc(row.rating || "No Data")}</span>`;
    const parts = [["Trend", "trend_score"], ["Momentum", "momentum_score"], ["Timing", "timing_score"], ["Volume", "volume_score"]];
    parts.sort((a,b) => Math.abs(row[b[1]] || 0) - Math.abs(row[a[1]] || 0));
    return `<details class="rating-explanation"><summary>${label}</summary><div class="rating-reasons"><p>${this.esc(row.reason || "Technical composite signal")}</p>${parts.map(([name,key]) => `<div>${name}<strong class="${row[key] > 0 ? "pos" : row[key] < 0 ? "neg" : ""}">${Number.isFinite(row[key]) ? (row[key] > 0 ? "+" : "") + row[key] : "Unavailable"}</strong></div>`).join("")}<p>Score uses the unrounded contributions, then rounds and limits the total to −100…100. Displayed components are rounded separately, so their sum can differ slightly. Missing inputs can leave a component neutral.</p><a href="stock.html?symbol=${encodeURIComponent(row.symbol)}">Full stock details →</a></div></details>`;
  },
  banner(doc) {}
};
