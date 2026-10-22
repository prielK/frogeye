import { firstSentence } from "./story";

// the same cases as the backend's publish.first_sentence (tests/agents/test_publish.py),
// so a briefing row, an email row and a Slack line end their first sentence in the same place.
it.each([
  ["It reaches GA on Oct. 15 for Premium tiers. Pricing follows.", "It reaches GA on Oct. 15 for Premium tiers."],
  ["It runs in the U.S. East region. More regions later.", "It runs in the U.S. East region."],
  ["The webinar starts at 9 a.m. ET on Tuesday. Register now.", "The webinar starts at 9 a.m. ET on Tuesday."],
  ["Ranked No. 1 by analysts. The rest trail.", "Ranked No. 1 by analysts."],
  ["Acme Inc. Ships X today. Then more.", "Acme Inc. Ships X today."],
  ["Tools, e.g. Nexus, gain it. Others wait.", "Tools, e.g. Nexus, gain it."],
  ["Version 2.0 ships. Next.", "Version 2.0 ships."],
  ['He said "go." Then left.', 'He said "go."'],
  ["Why now? Because.", "Why now?"],
  ["No full stop here", "No full stop here"],
  ["  Spread\nover lines. Second.", "Spread over lines."],
  ["", ""],
])("firstSentence(%j) keeps abbreviations whole", (text, first) => {
  expect(firstSentence(text)).toBe(first);
});
