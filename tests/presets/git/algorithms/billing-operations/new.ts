/** HTTP operations for the billing screen. Responses stay with the caller. */

/** Load a customer, rejecting unsuccessful HTTP responses. */
export async function loadCustomer(id: string): Promise<Response> {
  const response = await fetch(`/api/customers/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
  return response;
}

/** Load an invoice, rejecting unsuccessful HTTP responses. */
export async function loadInvoice(id: string): Promise<Response> {
  const response = await fetch(`/api/invoices/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
  return response;
}

/** Archive an invoice without deleting its billing history. */
export async function archiveInvoice(id: string): Promise<void> {
  const response = await fetch(`/api/invoices/${encodeURIComponent(id)}/archive`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
}
