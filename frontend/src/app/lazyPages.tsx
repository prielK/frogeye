import { lazy, Suspense, type ReactNode } from "react";

/*
 * the intelligence pages (the competitor and product lists and deep dives, the comparison) (and the chart kit only they draw with, in its own `d3`
 * chunk) load when one is opened, so the sign-in and the Morning Briefing never carry them.
 */
const Competitors = lazy(() => import("@/features/competitors/CompetitorsPage").then((m) => ({ default: m.CompetitorsPage })));
const Competitor = lazy(() => import("@/features/competitors/CompetitorPage").then((m) => ({ default: m.CompetitorPage })));
const Products = lazy(() => import("@/features/products/ProductsPage").then((m) => ({ default: m.ProductsPage })));
const Product = lazy(() => import("@/features/products/ProductPage").then((m) => ({ default: m.ProductPage })));
const Compare = lazy(() => import("@/features/compare/ComparePage").then((m) => ({ default: m.ComparePage })));

/** While a lazy page's code loads: a quiet line, as the pages' own skeletons follow. */
function Loading({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div role="status" aria-label="Loading the page" className="min-h-40" />}>{children}</Suspense>;
}

export const CompetitorsPage = () => <Loading><Competitors /></Loading>;
export const CompetitorPage = () => <Loading><Competitor /></Loading>;
export const ProductsPage = () => <Loading><Products /></Loading>;
export const ProductPage = () => <Loading><Product /></Loading>;
export const ComparePage = () => <Loading><Compare /></Loading>;
