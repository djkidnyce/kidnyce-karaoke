// Kid Nyce: the one screen setup (/splash?all=1) draws the join code and the
// singing order next to the lyrics. Names and titles come from guests' phones.
import { beforeEach, describe, expect, it } from "vitest";
import { splash } from "./splash.setup.js";

const state = (overrides = {}) => ({
  join: { required: true, code: "PFGFQP" },
  queue: [],
  queue_length: 0,
  ...overrides,
});

const song = (user, title = "A Song") => ({ user, title });

beforeEach(() => {
  document.getElementById("all-queue")?.remove();
  document.getElementById("splash-join-code")?.remove();
  document.body.insertAdjacentHTML(
    "beforeend",
    `<div id="splash-join-code" style="display: none"></div>
     <div id="all-queue" style="display: none">
       <ol id="all-queue-list"></ol><div id="all-queue-more"></div>
     </div>`,
  );
  globalThis.PikaraokeConfig.translations.moreInLine = "+{count} more";
});

const visible = (id) => document.getElementById(id).style.display !== "none";

describe("one screen setup", () => {
  it("shows the join code in two groups of three", () => {
    splash.renderAllInOne(state());
    expect(document.getElementById("splash-join-code").textContent).toBe("PFG FQP");
    expect(visible("splash-join-code")).toBe(true);
  });

  it("hides the code when the join gate is off", () => {
    splash.renderAllInOne(state({ join: { required: false, code: "PFGFQP" } }));
    expect(visible("splash-join-code")).toBe(false);
  });

  it("renders guest names and titles as text, never as HTML", () => {
    const name = '<img src=x onerror="alert(1)">';
    splash.renderAllInOne(state({ queue: [song(name, "<b>Hit</b>")], queue_length: 1 }));
    const item = document.querySelector("#all-queue-list li");
    expect(item.querySelector("img")).toBeNull();
    expect(item.querySelector("b").textContent).toBe(name);
    expect(item.textContent).toContain("<b>Hit</b>");
  });

  it("lists the next four and counts the rest", () => {
    const queue = ["Ana", "Bo", "Cy", "Di", "Ed", "Flo"].map((n) => song(n));
    splash.renderAllInOne(state({ queue, queue_length: 6 }));
    expect(document.querySelectorAll("#all-queue-list li")).toHaveLength(4);
    expect(document.getElementById("all-queue-more").textContent).toBe("+2 more");
    expect(visible("all-queue")).toBe(true);
  });

  it("hides the list when nobody is waiting", () => {
    splash.renderAllInOne(state());
    expect(visible("all-queue")).toBe(false);
  });
});
