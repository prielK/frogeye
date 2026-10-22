import { render } from "@testing-library/react";
import { FunnelBar } from "./FunnelBar";

const segment = (container: HTMLElement, stage: number) =>
  container.querySelector<HTMLElement>(`[data-stage="${stage}"]`);

it("runs light to dark left to right, with the last stage anchored under its label on the right", () => {
  const { container } = render(
    <FunnelBar stats={{ fetched: 1240, new_raw: 312, documents_created: 298, prefilter_passed: 41 }} />,
  );
  // Passed (stage 4, darkest) sits at the right edge, Fetched (stage 1, lightest) reaches the left edge.
  expect(segment(container, 4)?.style.right).toBe("0%");
  expect(segment(container, 1)?.style.right).toBe(`${(312 / 1240) * 100}%`);
  expect(segment(container, 1)?.className).toContain("rounded-l");
});

it("gives a tied extent to the later stage", () => {
  const { container } = render(
    <FunnelBar stats={{ fetched: 5219, new_raw: 5219, documents_created: 5152, prefilter_passed: 3917 }} />,
  );
  // Fetched and New are equal: the shared extent wears New's colour, Fetched draws nothing.
  expect(segment(container, 2)).not.toBeNull();
  expect(segment(container, 1)).toBeNull();
});
