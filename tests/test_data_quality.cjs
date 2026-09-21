const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {}, Date };
vm.createContext(context);
vm.runInContext(fs.readFileSync('assets/data-quality.js', 'utf8'), context);
const q = context.window.DataQuality;
const stale = q.stock({ price: 100, score: 50, rs_rank: 90, rating: "Buy",
  price_valid_until: '2000-01-01T00:00:00Z' });
assert.equal(stale.price, 100);
assert.equal(stale.score, 50);
assert.equal(stale.rs_rank, 90);
assert.equal(stale.rating, 'Buy');
const expired = q.option({ contracts: [{ mid: 5 }], leap_rating: 'LEAP Buy',
  valid_until: '2000-01-01T00:00:00Z', chain_as_of: '1999-12-31' }, {});
assert.equal(expired.contracts.length, 1);
assert.equal(expired.contracts[0].mid, 5);
assert.equal(expired.leap_rating, 'LEAP Buy');
assert.equal(q.esc('<script>'), '&lt;script&gt;');
console.log('Data freshness UI tests passed.');

assert.doesNotMatch(q.explanation({score: null, rating: 'Stale'}, 'Why?'), /rating-reasons/);
assert.match(q.explanation({symbol:'TEST', score:1, reason:'<script>',trend_score:2}, 'Why?'), /&lt;script&gt;/);
assert.match(q.explanation({symbol:'TEST', score:1,trend_score:2}, 'Why?'), /Unavailable/);

assert.equal(q.formatDate('2026-09-11T11:34:03.241300+00:00'), '9/11/2026 · 7:34AM ET');
assert.equal(q.formatDate('2026-01-11T14:45:00Z'), '1/11/2026 · 9:45AM ET');
assert.equal(q.formatDate('2026-09-11T02:00:00Z'), '9/10/2026 · 10:00PM ET');
assert.equal(q.formatDate('2026-09-11'), '9/11/2026');
assert.equal(q.formatDate(null), 'Unavailable');
assert.equal(q.formatDate('invalid'), 'Unavailable');
const live = q.stock({price:100, price_as_of:'2026-09-11',score:60,
 price_valid_until:'2000-01-01T00:00:00Z',quote:{price:110,as_of:'2026-09-14T15:00:00-04:00',change_pct:10}});
assert.equal(live.price,110);
assert.equal(live.signal_price,100);
assert.equal(live.score,60); // age must not erase saved ratings

assert.equal(q.inUniverse({lists:['Earnings watch'],earnings_retain_until:'2000-01-01'}),false);
assert.equal(q.inUniverse({lists:['Earnings watch','S&P 500'],earnings_retain_until:'2000-01-01'}),true);

assert.equal(q.inUniverse({lists:["Earnings watch"],earnings_retain_until:"2099-01-01"}),false);

assert.equal(q.price({}), "");
assert.equal(q.confidence({}), "");
assert.equal(q.details({}), "");
