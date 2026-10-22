import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeToggle } from "./ThemeToggle";

it("defaults to light and toggles to dark + persists", async () => {
  render(<ThemeToggle />);
  expect(document.documentElement).not.toHaveClass("dark");
  await userEvent.click(screen.getByRole("button", { name: /toggle theme/i }));
  expect(document.documentElement).toHaveClass("dark");
  expect(localStorage.getItem("frogeye-theme")).toBe("dark");
});
