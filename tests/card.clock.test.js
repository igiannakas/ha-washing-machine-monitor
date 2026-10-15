/* The card on a fake clock: days around the October clock change, and retrying after a failure. */
const { JSDOM } = require("jsdom");
const FakeTimers = require("@sinonjs/fake-timers");
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const SRC = fs.readFileSync(path.join(__dirname, "..", "custom_components", "washing_machine_monitor", "www", "washing-machine-card.js"), "utf8");
const text = (el) => el.textContent.replace(/\s+/g, " ").trim();
const ENTITIES = { status: "sensor.washing_machine_status", washes: "sensor.washing_machine_washes", washes_today: "sensor.washing_machine_washes_today", last_wash: "sensor.washing_machine_last_wash" };

async function boot(nowIso) {
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", pretendToBeVisual: true, url: "https://ha.local/lovelace/0" });
  const { window } = dom;
  const clock = FakeTimers.withGlobal(window).install({ now: Date.parse(nowIso), toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  window.eval(SRC);
  window.customElements.define("home-assistant", class extends window.HTMLElement {});
  await clock.tickAsync(1);
  return { window, clock };
}

function mount(window, callWS) {
  const states = {
    [ENTITIES.status]: { state: "idle", last_changed: "2026-10-26T08:00:00Z", attributes: {} },
    [ENTITIES.washes]: { state: "3", attributes: {} },
    [ENTITIES.washes_today]: { state: "0", attributes: {} },
    [ENTITIES.last_wash]: { state: "0.8", attributes: { finished: "2026-10-25T23:30:00Z" } },
    "sensor.washing_machine_power": { state: "1", attributes: { unit_of_measurement: "W" } },
  };
  const card = window.document.createElement("washing-machine-card");
  window.document.body.appendChild(card);
  card.setConfig({ entity: ENTITIES.status });
  card.hass = { states, config: { time_zone: "Europe/London", currency: "GBP" }, callWS };
  return card;
}

const reply = (washes) => ({
  title: "Washing machine", entities: ENTITIES, power_entity_id: "sensor.washing_machine_power",
  price: 0.25, currency: "GBP", status: "idle", current: null, first_wash: "2026-10-01T10:00:00Z", washes,
});

(async () => {
  // Monday 26 October 2026, the day after the clocks went back (BST -> GMT on Sunday 25th).
  {
    const { window, clock } = await boot("2026-10-26T10:00:00Z");
    const washes = [
      { start: "2026-10-24T22:00:00Z", end: "2026-10-24T23:30:00Z", minutes: 90, kwh: 0.8, cost: 0.2 }, // 00:30 BST on the 25th
      { start: "2026-10-25T22:00:00Z", end: "2026-10-25T23:30:00Z", minutes: 90, kwh: 0.8, cost: 0.2 }, // 23:30 GMT on the 25th
      { start: "2026-10-19T21:00:00Z", end: "2026-10-19T22:30:00Z", minutes: 90, kwh: 0.8, cost: 0.2 }, // 23:30 BST on the 19th: before the 7 days
    ];
    const card = mount(window, async () => reply(washes));
    await clock.tickAsync(10);
    const r = card.shadowRoot;
    const tiles = [...r.querySelectorAll(".day")];
    assert.deepStrictEqual(tiles.map((t) => t.dataset.d), ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"]);
    assert.deepStrictEqual(tiles.map((t) => text(t.querySelector(".dv"))), ["0", "0", "0", "0", "0", "2", "0"]);
    assert.strictEqual(text(r.querySelector(".ft")), "Last 7 days · 2 washes · 1.60 kWh · 40p");
    // The day sheet shows local times on both sides of the change.
    tiles[5].click();
    const sheet = window.document.querySelector("wmc-day-sheet");
    assert.strictEqual(text(sheet.shadowRoot.querySelector(".list")), "23:00 – 00:30 1 h 30 min · 0.80 kWh · 20p 22:00 – 23:30 1 h 30 min · 0.80 kWh · 20p");
    sheet.shadowRoot.querySelector(".close").click();
    clock.uninstall();
  }

  // A failed fetch shows its error, and is retried by the card's own 30 s timer.
  {
    const { window, clock } = await boot("2026-10-14T12:00:00Z");
    let calls = 0;
    let release;
    const card = mount(window, async () => {
      calls += 1;
      if (calls === 1) throw { code: "timeout", message: "Timed out" };
      await new Promise((r) => (release = r)); // the retry is slow to answer
      return reply([]);
    });
    await clock.tickAsync(10);
    // The error goes on the line under the name, the tiles say "–" (unknown).
    assert.match(text(card.shadowRoot.querySelector(".sc")), /Timed out/);
    assert.strictEqual(text(card.shadowRoot.querySelector(".nm")), "Washing machine · Unavailable");
    assert(card.shadowRoot.querySelectorAll(".dv")[0].textContent === "–");
    // Unrelated state updates while it waits don't hammer Home Assistant.
    for (let i = 0; i < 20; i++) card.hass = { ...card._hass, states: { ...card._hass.states } };
    await clock.tickAsync(10);
    assert.strictEqual(calls, 1, "no retry storm");
    await clock.tickAsync(29000);
    assert.strictEqual(calls, 1, "no retry before 30 s");
    await clock.tickAsync(2000);
    assert.strictEqual(calls, 2, "retried");
    for (let i = 0; i < 20; i++) card.hass = { ...card._hass, states: { ...card._hass.states } };
    await clock.tickAsync(10);
    assert.strictEqual(calls, 2, "one retry in flight at a time");
    release();
    await clock.tickAsync(10);
    assert.strictEqual(text(card.shadowRoot.querySelector(".sc")), "");
    assert.strictEqual(text(card.shadowRoot.querySelector(".ft")), "Last 7 days · No washes");
    clock.uninstall();
  }

  console.log("washing-machine-card (clock): all tests passed");
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
