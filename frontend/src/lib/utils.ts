import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * FrogEye's own theme scales (the `@theme` block in styles/globals.css; utils.test.ts checks
 * the lists against it). tailwind-merge only knows Tailwind's defaults, so without these it
 * reads `text-meta` as a text colour (and drops it beside `text-threat`), `shadow-floating` as
 * a shadow colour, and never dedupes the custom easings.
 */
export const THEME_SCALES = {
  text: ["meta", "body", "title", "display"],
  shadow: ["floating"],
  ease: ["out-expo", "out-quart"],
} as const;

const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: [...THEME_SCALES.text],
      shadow: [...THEME_SCALES.shadow],
      ease: [...THEME_SCALES.ease],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
