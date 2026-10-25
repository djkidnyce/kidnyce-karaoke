// Kid Nyce: the host's address in small print on the screens.
import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { hostAddressText } = require("../../pikaraoke/static/js/host-address.js");

describe("host address", () => {
  it("shows the address and the venue name", () => {
    expect(hostAddressText({ show: true, ip: "192.168.6.186", url: "https://karaoke.kidnyce.com" }))
      .toBe("192.168.6.186 · karaoke.kidnyce.com");
  });
  it("shows the address alone when there is no name", () => {
    expect(hostAddressText({ show: true, ip: "192.168.6.186", url: "http://192.168.6.186:5555" }))
      .toBe("192.168.6.186 · 192.168.6.186:5555");
    expect(hostAddressText({ show: true, ip: "10.0.0.5", url: "" })).toBe("10.0.0.5");
  });
  it("says so when there is no network", () => {
    expect(hostAddressText({ show: true, ip: null, url: "https://karaoke.kidnyce.com" })).toBe("No network");
  });
  it("shows nothing when the host turned it off", () => {
    expect(hostAddressText({ show: false, ip: "10.0.0.5" })).toBe("");
    expect(hostAddressText(undefined)).toBe("");
  });
});
