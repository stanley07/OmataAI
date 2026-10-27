
'use strict';

// OMATA_STEP_180M_SALE_SNAPSHOT
// Pure local state. Does not call APIs or databases.

function createOmataSaleSnapshot() {
  let original = null;

  const required = [
    'merchant',
    'source',
    'customer',
    'product',
    'location_id',
    'description',
    'quantity',
    'total',
    'paid'
  ];

  const capture = input => {
    if (original !== null) {
      throw Error('Sale snapshot already captured');
    }

    if (!input || typeof input !== 'object' ||
        Array.isArray(input)) {
      throw Error('Invalid sale snapshot input');
    }

    const data = Object.create(null);

    for (const field of required) {
      if (!Object.prototype.hasOwnProperty.call(input, field) ||
          typeof input[field] !== 'string' ||
          !input[field].trim()) {
        throw Error('Invalid snapshot field: ' + field);
      }

      data[field] = input[field];
    }

    original = Object.freeze(data);
    return Object.freeze({...original});
  };

  const bindDraft = entryId => {
    if (!original) throw Error('Capture sale before draft ID');

    if (typeof entryId !== 'string' || !entryId.trim()) {
      throw Error('Invalid draft ID');
    }

    if (original.entry_id) {
      throw Error('Draft ID already bound');
    }

    original = Object.freeze({
      ...original,
      entry_id: entryId
    });

    return Object.freeze({...original});
  };

  const read = () =>
    original ? Object.freeze({...original}) : null;

  const clear = () => {
    original = null;
  };

  return Object.freeze({
    capture,
    bindDraft,
    read,
    clear
  });
}

if (typeof window !== 'undefined') {
  window.createOmataSaleSnapshot = createOmataSaleSnapshot;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {createOmataSaleSnapshot};
}
