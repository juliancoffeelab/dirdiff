/** A receipt value carries a numeric total and no behavior. */
type Receipt = { total: number };

/** Construct a receipt from a supplied total. */
function receipt(total: number): Receipt {
  return { total };
}

/** Return the supplied checkout total. */
function checkout(total: number): number {
  return total;
}
