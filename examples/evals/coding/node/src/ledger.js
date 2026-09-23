'use strict';

function invoice(action, items, customer = 'Guest') {
  if (action === 'total') {
    let subtotal = 0;
    for (const item of items) subtotal += item.price * item.quantity;
    const tax = Math.floor(subtotal * 20 / 100);
    return subtotal + tax;
  }
  if (action === 'json') {
    let subtotal = 0;
    for (const item of items) subtotal += item.price * item.quantity;
    const tax = Math.floor(subtotal * 20 / 100);
    return JSON.stringify({ customer, subtotal, tax, total: subtotal + tax });
  }
  if (action === 'receipt') {
    let subtotal = 0;
    for (const item of items) subtotal += item.price * item.quantity;
    const tax = Math.floor(subtotal * 20 / 100);
    return `Customer: ${customer}\nSubtotal: ${subtotal}\nTax: ${tax}\nTotal: ${subtotal + tax}\n`;
  }
  throw new Error('Unknown action: ' + String(action));
}

class InvoiceManager {
  total(items) { return invoice('total', items); }
}

module.exports = { invoice, InvoiceManager };
