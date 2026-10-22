import { render } from "@testing-library/react";
import css from "@/styles/globals.css?raw";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./select";

// DESIGN.md §4: dropdown → sticky → modal-backdrop → modal → popover → toast → tooltip
const ORDER = ["dropdown", "sticky", "modal-backdrop", "modal", "popover", "toast", "tooltip"];

function zScale(): Record<string, number> {
  const scale: Record<string, number> = {};
  for (const [, name, value] of css.matchAll(/--z-([a-z-]+):\s*(\d+);/g)) scale[name] = Number(value);
  return scale;
}

it("defines the semantic z-index tiers in strictly increasing order", () => {
  const scale = zScale();
  const values = ORDER.map((tier) => scale[tier]);
  expect(values.every((v) => Number.isFinite(v))).toBe(true);
  for (let i = 1; i < values.length; i++) expect(values[i]).toBeGreaterThan(values[i - 1]);
});

it("lifts portaled menus to the popover tier", () => {
  render(
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger>Account</DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>,
  );
  const menu = document.querySelector('[data-slot="dropdown-menu-content"]');
  expect(menu?.className).toContain("z-(--z-popover)");
});

it("renders a Select opened inside a Dialog above the dialog layers", () => {
  render(
    <Dialog defaultOpen>
      <DialogContent>
        <DialogTitle>Test fetch</DialogTitle>
        <DialogDescription>Pick a source.</DialogDescription>
        <Select defaultOpen defaultValue="artifactory">
          <SelectTrigger aria-label="Source">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="artifactory">Artifactory</SelectItem>
          </SelectContent>
        </Select>
      </DialogContent>
    </Dialog>,
  );
  const overlay = document.querySelector('[data-slot="dialog-overlay"]');
  const dialog = document.querySelector('[data-slot="dialog-content"]');
  const select = document.querySelector('[data-slot="select-content"]');
  expect(overlay?.className).toContain("z-(--z-modal-backdrop)");
  expect(dialog?.className).toContain("z-(--z-modal)");
  expect(select?.className).toContain("z-(--z-popover)");
});
