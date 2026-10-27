
'use strict';

/*
 * Omata AI ? atomic-sale approval state.
 *
 * Pure client-side state management.
 * This module does not call APIs or mutate databases.
 * The backend must independently validate signed review tokens,
 * permissions, stock availability and financial details.
 */

function createAtomicApprovalState() {
  let state = {
    draftId: null,
    merchant: null,
    review: null,
    reviewToken: null,
    approved: false,
    busy: false,
    uncertain: false
  };

  const copy = () => ({
    ...state,
    review: state.review ? {...state.review} : null
  });

  const reset = () => {
    state = {
      draftId: null,
      merchant: null,
      review: null,
      reviewToken: null,
      approved: false,
      busy: false,
      uncertain: false
    };
    return copy();
  };

  const beginDraft = () => {
    reset();
    state.busy = true;
    return copy();
  };

  const setDraft = (draft, merchant) => {
    if (!draft || typeof draft.entry_id !== 'string' ||
        draft.status !== 'draft' ||
        typeof merchant !== 'string' || !merchant.trim()) {
      throw Error('Invalid draft');
    }

    reset();
    state.draftId = draft.entry_id;
    state.merchant = merchant;
    return copy();
  };

  const setReview = (reviewResponse) => {
    if (!state.draftId || !state.merchant) {
      throw Error('Draft required before review');
    }

    if (!reviewResponse || !reviewResponse.sale ||
        typeof reviewResponse.review_token !== 'string' ||
        !reviewResponse.review_token.trim()) {
      throw Error('Authoritative review and token required');
    }

    const sale = reviewResponse.sale;

    if (sale.id !== state.draftId ||
        sale.merchant !== state.merchant ||
        sale.status !== 'draft') {
      throw Error('Review does not match the draft');
    }

    state.review = {...sale};
    state.reviewToken = reviewResponse.review_token;
    state.approved = false;
    state.uncertain = false;

    return copy();
  };

  const approve = (checked, currentMerchant) => {
    if (checked !== true) {
      state.approved = false;
      return copy();
    }

    if (!state.review || !state.reviewToken ||
        state.uncertain || state.busy ||
        currentMerchant !== state.merchant) {
      throw Error('Approval prerequisites not satisfied');
    }

    state.approved = true;
    return copy();
  };

  const invalidate = () => reset();

  const markUncertain = () => {
    state.uncertain = true;
    state.approved = false;
    state.reviewToken = null;
    return copy();
  };

  const canConfirm = (currentMerchant) => (
    state.approved === true &&
    state.busy === false &&
    state.uncertain === false &&
    state.reviewToken !== null &&
    state.review !== null &&
    state.merchant === currentMerchant
  );

  const confirmationPayload = (currentMerchant) => {
    if (!canConfirm(currentMerchant)) {
      throw Error('Sale confirmation not authorized');
    }

    return {
      merchant: state.merchant,
      id: state.draftId,
      human_confirmed: true,
      review_token: state.reviewToken
    };
  };

  return {
    snapshot: copy,
    reset,
    beginDraft,
    setDraft,
    setReview,
    approve,
    invalidate,
    markUncertain,
    canConfirm,
    confirmationPayload
  };
}


// OMATA_STEP_179Y_BROWSER_EXPORT
if (typeof window !== 'undefined') {
  window.createAtomicApprovalState = createAtomicApprovalState;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {createAtomicApprovalState};
}
