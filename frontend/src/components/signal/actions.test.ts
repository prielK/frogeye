import { unquote } from "./actions";

// the reading surfaces' own copy of the quote rule follows the backend's
// (newsroom/fill.py `unquote`, `_encloses`; tests/agents/test_newsroom.py), case for case, so the
// analysis page, the drawer, the product page and the briefing cards agree on every line.
describe("unquote", () => {
  it("keeps quotes that close inside the line, as written", () => {
    for (const line of ['"A" and "B"', "“A” and “B”", "‘A’ and ‘B’", "'Tier' or 'plan'"]) expect(unquote(line)).toBe(line);
  });

  it("drops one level of quotes around the whole line", () => {
    expect(unquote('  "We gate models too."  ')).toBe("We gate models too.");
    expect(unquote("“Too dear”")).toBe("Too dear");
  });

  it("reads a closing mark between two letters as an apostrophe, not a quote", () => {
    expect(unquote("‘We don’t ship that’")).toBe("We don’t ship that");
  });

  it("keeps other or nested quotes inside", () => {
    expect(unquote('"He said “yes” today"')).toBe("He said “yes” today");
    expect(unquote("“He said “yes” today”")).toBe("He said “yes” today");
  });

  it("leaves a lone quote and an empty line alone", () => {
    expect(unquote('"')).toBe('"');
    expect(unquote("")).toBe("");
  });
});
