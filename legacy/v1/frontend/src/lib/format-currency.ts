/**
 * Shared currency formatting driven by account currency.
 *
 * Usage:
 *   formatCurrency(1234.56)           → "£1,234.56"
 *   formatCurrency(-50, "GBP", true)  → "-£50.00"
 *   formatPnl(120.5)                  → "+£120.50"
 *   formatPnl(-30.2)                  → "-£30.20"
 *   currencySymbol("GBP")             → "£"
 *   currencySymbol("USD")             → "$"
 */

const SYMBOLS: Record<string, string> = {
  GBP: "£",
  USD: "$",
  EUR: "€",
  JPY: "¥",
};

/** Get the symbol for a currency code. */
export function currencySymbol(currency = "GBP"): string {
  return SYMBOLS[currency.toUpperCase()] ?? currency;
}

/** Format a monetary value with currency symbol. */
export function formatCurrency(
  value: number,
  currency = "GBP",
  decimals = 2,
): string {
  const sym = currencySymbol(currency);
  const formatted = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return value < 0 ? `-${sym}${formatted}` : `${sym}${formatted}`;
}

/** Format a PnL value with +/- prefix and currency symbol. */
export function formatPnl(value: number, currency = "GBP", decimals = 2): string {
  const sym = currencySymbol(currency);
  const formatted = Math.abs(value).toFixed(decimals);
  if (value >= 0) return `+${sym}${formatted}`;
  return `-${sym}${formatted}`;
}
