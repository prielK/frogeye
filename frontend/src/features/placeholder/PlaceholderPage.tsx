import { Link } from "react-router";
import { PageHeader } from "@/components/layout/PageHeader";

/*
 * The app's not-found page. The placeholders that held the competitor and product deep-dive
 * routes until their pages shipped are retired: the product pages have shipped,
 * so every route renders its own page.
 */

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="This address doesn't match any page in FrogEye." />
      <Link
        to="/feed"
        className="text-body font-medium text-primary-ink underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        Go to the Feed
      </Link>
    </>
  );
}
