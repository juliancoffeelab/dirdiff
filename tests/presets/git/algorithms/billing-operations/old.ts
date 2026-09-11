/** HTTP operations for the billing screen. Responses stay with the caller. */

/** Load an invoice, rejecting unsuccessful HTTP responses. */
export async function loadInvoice(id: string): Promise<Response> {
  const response = await fetch(`/api/invoices/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
  return response;
}

/** Load a customer, rejecting unsuccessful HTTP responses. */
export async function loadCustomer(id: string): Promise<Response> {
  const response = await fetch(`/api/customers/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
  return response;
}

/** Delete an unpaid invoice; a failed deletion rejects the operation. */
export async function deleteInvoice(id: string): Promise<void> {
  const response = await fetch(`/api/invoices/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Billing API returned ${response.status}`);
  }
}
