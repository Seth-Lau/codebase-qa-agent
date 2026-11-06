const gbp = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });

/** 1234 -> "£12.34" */
export function formatPrice(pence: number): string {
  return gbp.format(pence / 100);
}
