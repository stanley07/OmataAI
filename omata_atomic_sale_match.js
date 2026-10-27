
'use strict';

// OMATA_STEP_180K_EXACT_SALE_MATCH
// Pure validation. No API requests, writes or approval actions.

function omataMoneyToKobo(value) {
  if (typeof value !== 'string' && typeof value !== 'number') {
    throw Error('Invalid money type');
  }

  const text = String(value).trim();

  // Use decimal-string arithmetic, not floating-point multiplication.
  const match = /^(0|[1-9][0-9]*)(?:\.([0-9]{1,2}))?$/.exec(text);

  if (!match) throw Error('Invalid naira amount');

  const whole = BigInt(match[1]);
  const fractional = BigInt((match[2] || '').padEnd(2, '0'));

  const amount = whole * 100n + fractional;

  if (amount > BigInt(Number.MAX_SAFE_INTEGER)) {
    throw Error('Money amount exceeds safe integer range');
  }

  return Number(amount);
}

function omataPositiveQuantity(value) {
  const text = String(value).trim();

  if (!/^[1-9][0-9]*$/.test(text)) {
    throw Error('Invalid positive quantity');
  }

  const quantity = Number(text);

  if (!Number.isSafeInteger(quantity) ||
      quantity > 1000000000) {
    throw Error('Quantity out of range');
  }

  return quantity;
}

function omataExactSaleMatch(expected, reviewed) {
  if (!expected || !reviewed ||
      typeof expected !== 'object' ||
      typeof reviewed !== 'object') {
    throw Error('Expected and reviewed sale required');
  }

  const textFields = [
    ['entry_id', 'id'],
    ['merchant', 'merchant'],
    ['source', 'source'],
    ['customer', 'customer'],
    ['description', 'description'],
    ['product', 'stock_product'],
    ['location_id', 'location_id'],
    ['location_id', 'stock_location_id']
  ];

  for (const [requested, authoritative] of textFields) {
    const a = expected[requested];
    const b = reviewed[authoritative];

    if (typeof a !== 'string' || !a.trim() ||
        typeof b !== 'string' || !b.trim() ||
        a !== b) {
      throw Error('Sale detail mismatch: ' + authoritative);
    }
  }

  if (reviewed.kind !== 'sale' ||
      reviewed.status !== 'draft' ||
      reviewed.stock_kind !== 'sale' ||
      reviewed.stock_status !== 'draft') {
    throw Error('Sale is not an authoritative linked draft');
  }

  const quantity = omataPositiveQuantity(expected.quantity);

  if (!Number.isSafeInteger(reviewed.stock_quantity) ||
      reviewed.stock_quantity !== -quantity) {
    throw Error('Sale stock quantity mismatch');
  }

  const total = omataMoneyToKobo(expected.total);
  const paid = omataMoneyToKobo(expected.paid);

  if (total <= 0 || paid > total) {
    throw Error('Invalid sale payment');
  }

  if (!Number.isSafeInteger(reviewed.total) ||
      !Number.isSafeInteger(reviewed.paid) ||
      reviewed.total !== total ||
      reviewed.paid !== paid ||
      reviewed.customer_credit !== 0) {
    throw Error('Sale financial details mismatch');
  }

  if (typeof reviewed.stock_move_id !== 'string' ||
      !reviewed.stock_move_id.trim() ||
      typeof reviewed.stock_source !== 'string' ||
      !reviewed.stock_source.trim()) {
    throw Error('Linked stock movement is missing');
  }

  // OMATA_STEP_180S_STOCK_SOURCE_MATCH
  if (reviewed.stock_source !== expected.source ||
      reviewed.stock_source !== reviewed.source) {
    throw Error('Financial and inventory source mismatch');
  }

  return true;
}

if (typeof window !== 'undefined') {
  window.omataExactSaleMatch = omataExactSaleMatch;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    omataMoneyToKobo,
    omataPositiveQuantity,
    omataExactSaleMatch
  };
}
