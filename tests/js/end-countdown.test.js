// Kid Nyce: the singer's countdown for a song's last seconds.
import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { countdownText } = require("../../pikaraoke/static/js/end-countdown.js");

describe("end countdown", () => {
  it("stays hidden until the last seconds", () => {
    expect(countdownText(240, 100, 20)).toBe("");
    expect(countdownText(240, 219.9, 20)).toBe("");
  });
  it("counts whole seconds down to 1", () => {
    expect(countdownText(240, 220, 20)).toBe("20");
    expect(countdownText(240, 220.5, 20)).toBe("20");
    expect(countdownText(240, 235.2, 20)).toBe("5");
    expect(countdownText(240, 239.99, 20)).toBe("1");
  });
  it("hides at the end, without a length, or when turned off", () => {
    expect(countdownText(240, 240, 20)).toBe("");
    expect(countdownText(null, 230, 20)).toBe("");
    expect(countdownText(240, 230, 0)).toBe("");
  });
});

const { position, POSITIONS } = require("../../pikaraoke/static/js/end-countdown.js");

describe("countdown position", () => {
  it("offers the seven places", () => {
    expect(POSITIONS).toEqual([
      "top-left", "top-center", "top-right", "center",
      "bottom-left", "bottom-center", "bottom-right",
    ]);
  });
  it("keeps a known place and falls back to top center", () => {
    expect(position("bottom-right")).toBe("bottom-right");
    expect(position("center")).toBe("center");
    expect(position("sideways")).toBe("top-center");
    expect(position(undefined)).toBe("top-center");
  });
});
