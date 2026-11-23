// format.js
// Number formatting in the South-Asian grouping used in Nepal (15,00,000).

const money = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

export const formatRs = (value) => (value === null || value === undefined ? '—' : `Rs. ${money.format(value)}`);

export const formatNum = (value) => (value === null || value === undefined ? '—' : money.format(value));

export const formatPct = (value) => (value === null || value === undefined ? '—' : `${value}%`);

export const newSessionId = () =>
  (globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`).slice(0, 64);
