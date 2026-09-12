const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {}, Date };
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/data-quality.js', 'utf8'), context);
const q = context.window.DataQuality;
const stale = q.stock({ price: 100, score: 50, rs_rank: 90,
  price_valid_until: '2000-01-01T00:00:00Z' });
assert.equal(stale.price, 100);
assert.equal(stale.score, null);
assert.equal(stale.rs_rank, null);
assert.equal(stale.rating, 'Stale');
const expired = q.option({ contracts: [{ mid: 5 }], leap_rating: 'LEAP Buy',
  valid_until: '2000-01-01T00:00:00Z', chain_as_of: '1999-12-31' }, {});
assert.equal(expired.contracts.length, 0);
assert.equal(expired.historical_contracts[0].mid, 5);
assert.equal(expired.leap_rating, 'Stale');
assert.equal(q.esc('<script>'), '&lt;script&gt;');
assert.match(q.price({ price_as_of: '2026-09-09', data_quality: { prices: { stale: true } } }), /Stale/);
console.log('Data freshness UI tests passed.');

assert.match(q.confidence({trend_score: 0, momentum_score: 0, timing_score: 0, volume_score: 0, data_quality: {prices: {stale: false}}}), /4\/4/);
assert.match(q.confidence({data_quality: {prices: {stale: true}}}), /Stale prices/);
assert.match(q.confidence({score: 50}), /Freshness unverified/);
assert.doesNotMatch(q.explanation({score: null, rating: 'Stale'}, 'Why?'), /rating-reasons/);
assert.match(q.explanation({symbol:'TEST', score:1, reason:'<script>',trend_score:2}, 'Why?'), /&lt;script&gt;/);
assert.match(q.explanation({symbol:'TEST', score:1,trend_score:2}, 'Why?'), /Unavailable/);

assert.equal(q.formatDate('2026-09-11T11:34:03.241300+00:00'), '9/11/2026 · 7:34AM ET');
assert.equal(q.formatDate('2026-01-11T14:45:00Z'), '1/11/2026 · 9:45AM ET');
assert.equal(q.formatDate('2026-09-11T02:00:00Z'), '9/10/2026 · 10:00PM ET');
assert.equal(q.formatDate('2026-09-11'), '9/11/2026');
assert.equal(q.formatDate(null), 'Unavailable');
assert.equal(q.formatDate('invalid'), 'Unavailable');
