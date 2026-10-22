const counts = new Intl.NumberFormat("en-US");

/** "1,240": grouped integers for counts. */
export function formatCount(n: number): string {
  return counts.format(n);
}

/** "93%", or "<1%" for a non-zero share that would round to nothing ("104%" past a cap). */
export function formatPercent(share: number): string {
  const p = share * 100;
  return `${p > 0 && p < 1 ? "<1" : Math.round(p)}%`;
}

/** "3 sources", "1 source". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** Host of a URL without "www.", or the raw string when it is not a URL. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** True only for absolute http(s) URLs: anything else (javascript:, data:, relative) must not become a link. */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

export const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usdTiny = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumSignificantDigits: 2,
  maximumSignificantDigits: 2,
});

const usdFixed = new Map<number, Intl.NumberFormat>();

/**
 * "$0.31". AI spend runs in fractions of a cent per call, so `precise` keeps two significant
 * digits below a dollar ("$0.012", "$0.000041") where a sum would otherwise read "$0.00", and
 * `decimals` fixes the places below a dollar so a column of costs lines up ("$0.018",
 * "$0.104"); otherwise a non-zero amount under half a cent reads "<$0.01".
 */
export function formatUsd(value: number, { precise = false, decimals }: { precise?: boolean; decimals?: number } = {}): string {
  if (decimals !== undefined && value < 1) {
    let format = usdFixed.get(decimals);
    if (!format) {
      format = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: decimals, maximumFractionDigits: decimals });
      usdFixed.set(decimals, format);
    }
    const text = format.format(Math.max(0, value));
    return value <= 0 || /[1-9]/.test(text) ? text : `<${format.format(10 ** -decimals)}`;
  }
  if (value <= 0) return usd.format(0);
  if (precise && value < 1) return usdTiny.format(value);
  if (value < 0.005) return "<$0.01";
  return usd.format(value);
}

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** "182K", "1.2M", "950": token counts at a glance (the exact count goes in a tooltip). */
export function formatCompact(n: number): string {
  return compact.format(n);
}
