// Kid Nyce: time left tonight on the queue page.
import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { formatLeft, bannerText } = require("../../pikaraoke/static/js/night.js");

const t = {
  host: "Night ends {end}: {left} left. The queue runs to {queueEnd}.",
  hostNoEnd: "The queue runs about {length}, to {queueEnd}.",
  guest: "{left} left tonight.",
  late: "{count} songs may not fit.",
  allFit: "Every song fits.",
  over: "The night is over.",
  estimated: "({count} lengths estimated)",
};
const plan = (o = {}) => ({
  ends_at: "01:00", time_left: 4320, queue_seconds: 6000, queue_ends_at: "01:35",
  first_late_index: 10, late_count: 6, estimated_songs: 0, show_guests: false,
  items: new Array(16).fill({ fits: true }), ...o,
});

describe("time left", () => {
  it("formats hours and minutes", () => {
    expect(formatLeft(4320)).toBe("1h 12m");
    expect(formatLeft(2700)).toBe("45m");
    expect(formatLeft(-5)).toBe("0m");
  });
  it("tells the host what will not fit", () => {
    expect(bannerText(plan(), true, t)).toBe("Night ends 01:00: 1h 12m left. The queue runs to 01:35. 6 songs may not fit.");
  });
  it("says when everything fits, and when lengths are guesses", () => {
    expect(bannerText(plan({ late_count: 0, first_late_index: null, estimated_songs: 2 }), true, t))
      .toBe("Night ends 01:00: 1h 12m left. The queue runs to 01:35. Every song fits. (2 lengths estimated)");
  });
  it("without an end time the host still sees the queue length", () => {
    expect(bannerText(plan({ ends_at: null, time_left: null, late_count: 0 }), true, t))
      .toBe("The queue runs about 1h 40m, to 01:35.");
  });
  it("guests see nothing unless the host allows it", () => {
    expect(bannerText(plan(), false, t)).toBe("");
    expect(bannerText({ show_guests: false }, false, t)).toBe("");
    expect(bannerText(plan({ show_guests: true }), false, t)).toBe("1h 12m left tonight. 6 songs may not fit.");
  });
  it("says when the night is over", () => {
    expect(bannerText(plan({ time_left: 0 }), true, t)).toBe("The night is over.");
  });
});
