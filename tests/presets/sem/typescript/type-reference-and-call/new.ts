/** A receipt value carries a numeric total and no behavior. */
type Receipt = { total: number };

/** Construct a receipt from a supplied total. */
function receipt(total: number): Receipt {
  return { total };
}

/** Return a receipt for the supplied checkout total. */
function checkout(total: number): Receipt {
  return receipt(total);
}
