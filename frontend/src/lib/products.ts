/*
 * JFrog's products. Their names and their order are the API's (GET /intel-reference serves the
 * backend's JFROG_PRODUCTS, read with `useJfrogProducts` or a page's `useRules`), so the app keeps
 * no copy of the list. The overview's `jfrog_products`, a signal's product
 * impacts and the product deep dives all use these names; the product pages use their kebab-case
 * slugs (the backend's `product_slug`).
 */

/** "JFrog ML" → "jfrog-ml", "AI Catalog" → "ai-catalog": a product page's slug. */
export function productSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/** The product a slug names, or null for a slug that is not one of JFrog's `products`. */
export function productName(slug: string, products: readonly string[]): string | null {
  return products.find((name) => productSlug(name) === slug) ?? null;
}

/**
 * A JFrog product's deep dive (`/products/artifactory`), or null for a name that is not one of
 * JFrog's `products` (and while the list has not loaded: a name stays text until then).
 */
export function productHref(name: string, products: readonly string[] | undefined): string | null {
  if (!products) return null;
  const slug = productSlug(name);
  return productName(slug, products) ? `/products/${slug}` : null;
}

/** Where a product sits in the list, for sorting names the same way everywhere (unknown names last). */
export function productRank(name: string, products: readonly string[]): number {
  const at = products.indexOf(name);
  return at === -1 ? products.length : at;
}
