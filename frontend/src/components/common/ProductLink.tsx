import type { ReactNode } from "react";
import { Link } from "react-router";
import { useJfrogProducts } from "@/lib/intelReference";
import { productHref } from "@/lib/products";
import { cn } from "@/lib/utils";

type ProductLinkProps = {
  /** The JFrog product's name, as an analysis or a briefing writes it ("Curation"). */
  name: string;
  /** What the link shows (a tag with its effect); defaults to the name. */
  children?: ReactNode;
  id?: string;
  className?: string;
  /** Added only when the name is a link (a tag's hover and focus look). */
  linkClassName?: string;
};

/**
 * A JFrog product named on a reading surface (an analysis's impact ledger and side column, a
 * briefing card's product tags): a link to its deep dive, `/products/:slug`. A name that
 * is not one of JFrog's products, as the API lists them (GET /intel-reference), stays text, so a
 * stray name never leads to a 404; so does every name for the moment the list is loading.
 */
export function ProductLink({ name, children, id, className, linkClassName }: ProductLinkProps) {
  const href = productHref(name, useJfrogProducts());
  if (!href) {
    return (
      <span id={id} className={className}>
        {children ?? name}
      </span>
    );
  }
  return (
    <Link to={href} id={id} data-product-link className={cn(className, linkClassName)}>
      {children ?? name}
    </Link>
  );
}
