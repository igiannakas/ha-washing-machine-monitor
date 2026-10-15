/* The washing-machine-card in jsdom, with a fake hass and a fake washing_machine_monitor/washes. */
const { JSDOM, ResourceLoader } = require("jsdom");
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const SRC = fs.readFileSync(path.join(__dirname, "..", "custom_components", "washing_machine_monitor", "www", "washing-machine-card.js"), "utf8");
const TZ = "Europe/London";
const MIN = 60000;
const DAY = 86400000;
const eq = (a, b, m) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), m);
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));

const ENTITIES = {
  running: "binary_sensor.washing_machine_running",
  status: "sensor.washing_machine_status",
  washes_today: "sensor.washing_machine_washes_today",
  washes_last_7_days: "sensor.washing_machine_washes_last_7_days",
  last_wash: "sensor.washing_machine_last_wash",
  washes: "sensor.washing_machine_washes",
  wash_time: "sensor.washing_machine_wash_time",
  wash: "event.washing_machine_wash",
};
const dayKey = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
// A moment `days` local days before today, at `hourUtc`:00 UTC. 9–15 UTC is the same day in London
// in summer and winter, so washes put there never straddle midnight, whenever the tests run.
const dayBefore = (days, hourUtc = 12) => {
  const [y, m, d] = dayKey(new Date()).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - days, hourUtc));
};

/* `canvas`: a stand-in 2D context for measuring letters. Without one the card sees jsdom (no
   canvas) and skips ink alignment. */
async function setup({ canvas = null } = {}) {
  const resources = canvas ? new ResourceLoader({ userAgent: "Mozilla/5.0 (card test)" }) : undefined;
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", pretendToBeVisual: true, url: "https://ha.local/lovelace/0", resources });
  const { window } = dom;
  if (canvas) window.HTMLCanvasElement.prototype.getContext = () => canvas;
  window.eval(SRC);
  assert(!window.customElements.get("washing-machine-card"), "waits for the app before registering");
  window.customElements.define("home-assistant", class extends window.HTMLElement {});
  await tick();
  assert(window.customElements.get("washing-machine-card"), "registers");
  return window;
}

function scenario(window, { status = "idle", watts = "1.2", startedMinAgo = 48, soFar = 0.62, washes = [], todayWashes = 0, firstWash = null, wsError = null, energy = null } = {}) {
  const now = Date.now();
  const states = {
    [ENTITIES.status]: {
      state: status,
      last_changed: new Date(now - startedMinAgo * MIN).toISOString(),
      attributes: status === "washing" ? { started: new Date(now - startedMinAgo * MIN).toISOString(), kwh: soFar, options: ["idle", "washing", "finished"] }
        : status === "finished" ? { finished: new Date(now - 30 * MIN).toISOString() } : {},
    },
    [ENTITIES.washes]: { state: String(washes.length), attributes: {} },
    [ENTITIES.washes_today]: { state: String(todayWashes), attributes: todayWashes ? { kwh: 1.48, cost: 0.3511, minutes: 220 } : { kwh: null, cost: null, minutes: 0 } },
    [ENTITIES.last_wash]: washes.length
      ? { state: "0.84", attributes: { started: washes[washes.length - 1].start, finished: washes[washes.length - 1].end, minutes: 112, cost: 0.1992 } }
      : { state: "unknown", attributes: {} },
    "sensor.washing_machine_power": { state: watts, attributes: { unit_of_measurement: "W" } },
  };
  const calls = [];
  const hass = {
    states,
    config: { time_zone: TZ, currency: "GBP" },
    callWS: async (msg) => {
      calls.push(msg);
      if (wsError) throw wsError;
      const s = Date.parse(msg.start);
      const e = Date.parse(msg.end);
      return {
        title: "Washing machine",
        entities: ENTITIES,
        power_entity_id: "sensor.washing_machine_power",
        price: 0.2372,
        currency: "GBP",
        status,
        current: status === "washing" ? { started: states[ENTITIES.status].attributes.started, kwh: soFar } : null,
        first_wash: firstWash ?? (washes[0] ? washes[0].end : null),
        energy,
        washes: washes.filter((w) => Date.parse(w.end) >= s && Date.parse(w.end) < e),
      };
    },
  };
  return { hass, calls, states };
}

function mount(window, hass, config = { entity: "sensor.washing_machine_status" }) {
  const card = window.document.createElement("washing-machine-card");
  const events = [];
  card.addEventListener("hass-more-info", (e) => events.push(["more-info", e.detail.entityId]));
  card.addEventListener("location-changed", () => events.push(["navigate", window.location.pathname]));
  window.document.body.appendChild(card);
  card.setConfig(config);
  card.hass = hass;
  return { card, events, root: () => card.shadowRoot };
}

const wash = (endMsAgo, minutes = 112, kwh = 0.84, cost = 0.1992) => ({
  start: new Date(Date.now() - endMsAgo - minutes * MIN).toISOString(),
  end: new Date(Date.now() - endMsAgo).toISOString(),
  minutes, kwh, cost,
});

const text = (el) => el.textContent.replace(/\s+/g, " ").trim();
const stats = (r) => [...r.querySelectorAll(".stats > *")].map((c) => [text(c.querySelector("b")), text(c.querySelector("span"))]);
// The day sheet's tiles as [label, value].
const sheetTiles = (sheet) => [...sheet.shadowRoot.querySelectorAll(".tile")].map((t) => [text(t.querySelector("span")), text(t.querySelector("b"))]);
// The power pill's text, or null while it is hidden.
const pill = (r) => (r.querySelector(".pill").hidden ? null : text(r.querySelector(".pill")));

(async () => {
  const window = await setup();

  // 1. A helpful error without an entity, and a config form listing every option.
  {
    const Card = window.customElements.get("washing-machine-card");
    assert.throws(() => new Card().setConfig({}), /Washing Machine Monitor entity/);
    const names = Card.getConfigForm().schema.flatMap((s) => (s.schema ? s.schema.map((x) => x.name) : [s.name]));
    eq(names, ["entity", "name", "icon", "navigation_path"]);
  }

  // 2. Idle, with washes earlier this week. Head as the appliance card: "Name · Idle" and no pill
  //    (no wash running), today's numbers in a strip; tiles per day, the footer totals them.
  {
    const washes = [wash(Date.now() - dayBefore(3, 9)), wash(Date.now() - dayBefore(3, 12)), wash(1 * DAY)];
    const { hass, calls } = scenario(window, { washes, todayWashes: 0 });
    const { root, events } = mount(window, hass, { entity: "sensor.washing_machine_status", navigation_path: "/lovelace/washing-machine" });
    await tick(10);
    const r = root();
    eq(calls.length, 1, "one fetch");
    eq(calls[0].type, "washing_machine_monitor/washes");
    eq(text(r.querySelector(".nm")), "Washing machine · Idle");
    eq(text(r.querySelector(".sc")), "");
    assert(r.querySelector(".txt").classList.contains("one"), "a name alone sits 1px low on the circle");
    assert.match(r.querySelector(".shape ha-icon").style.color, /grey/);
    eq(pill(r), null);
    assert(!r.querySelector(".tog"), "the card doesn't switch the plug");
    const yesterday = new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: TZ }).format(new Date(Date.now() - DAY));
    eq(stats(r), [["0", "Washes today"], [`${yesterday} ${text({ textContent: new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TZ }).format(new Date(Date.now() - DAY)) })}`, "Last wash"], ["0.00 kWh", "Today"], ["0p", "Today's cost"]]);
    const tiles = [...r.querySelectorAll(".day")];
    eq(tiles.length, 7);
    eq(text(tiles[6].querySelector(".dk")), "Today");
    assert(tiles[6].classList.contains("today"));
    const counts = Object.fromEntries(tiles.map((t) => [t.dataset.d, text(t.querySelector(".dv"))]));
    eq(counts[dayKey(new Date(Date.now() - DAY))], "1");
    eq(counts[dayKey(dayBefore(3))], "2");
    // Zero is a number (0, quiet), not "–", which means unknown.
    eq(text(tiles[6].querySelector(".dv")), "0");
    assert(tiles[6].querySelector(".dv").classList.contains("zero"));
    // A heat map of washes: green, one step per wash up to 3+; a day without washes stays neutral.
    const heat = (t) => ["h1", "h2", "h3"].filter((c) => t.classList.contains(c));
    const byKey = Object.fromEntries(tiles.map((t) => [t.dataset.d, t]));
    eq(heat(byKey[dayKey(new Date(Date.now() - DAY))]), ["h1"]);
    eq(heat(byKey[dayKey(dayBefore(3))]), ["h2"]);
    eq(heat(tiles[6]), []);
    eq(text(r.querySelector(".ft")), "Last 7 days · 3 washes · 2.52 kWh · 60p");
    assert(r.querySelector(".next").disabled, "can't page into the future");
    assert(r.querySelector(".prev").disabled, "nothing older than the first wash");
    // Parts of a line are joined with a no-break space before the dot, so a line only wraps after "·".
    assert(r.querySelector(".ft").innerHTML.includes("</span>&nbsp;· <span>"), "the dot stays with the part before it");

    // Stability: a hass update with the same states keeps the same nodes (taps are never lost).
    const before = r.querySelector(".day");
    const ft = r.querySelector(".ft").innerHTML;
    root().host.hass = { ...hass };
    await tick(5);
    assert.strictEqual(r.querySelector(".day"), before);
    eq(r.querySelector(".ft").innerHTML, ft);
    eq(calls.length, 1, "no refetch when nothing changed");

    // With a dashboard to open, a tap anywhere but the controls goes there; the watts open the sensor.
    r.querySelector(".stats").click();
    r.querySelector(".nm").click();
    r.querySelector(".w").click();
    eq(events, [["navigate", "/lovelace/washing-machine"], ["navigate", "/lovelace/washing-machine"], ["more-info", "sensor.washing_machine_power"]]);
    assert(!r.querySelector(".open").contains(r.querySelector(".pill")), "no control nested in another");

    // A day with two washes: the date and count under the title, then the day in six tiles (the
    // chosen design), then each wash in its glyph's circle.
    two: {
      tiles.find((t) => text(t.querySelector(".dv")) === "2").click();
      const sheet = window.document.querySelector("wmc-day-sheet");
      assert.match(text(sheet.shadowRoot.querySelector(".sub")), / · 2 washes$/);
      eq(sheetTiles(sheet), [["Washes", "2"], ["Time washing", "3 h 44 min"], ["Average", "1 h 52 min"], ["Energy", "1.68 kWh"], ["Cost", "40p"], ["Per wash", "20p"]]);
      eq(sheet.shadowRoot.querySelectorAll(".wash").length, 2);
      // The icons carry colour, the card idle or not: green for a day with finished washes and
      // for each of them.
      assert.match(sheet.shadowRoot.querySelector(".row .glyph ha-icon").style.color, /green/);
      for (const g of sheet.shadowRoot.querySelectorAll(".wash .glyph")) {
        assert.match(g.style.background, /green/);
        assert.match(g.querySelector("ha-icon").style.color, /green/);
      }
      sheet.shadowRoot.querySelector(".close").click();
      assert(!window.document.querySelector("wmc-day-sheet"));
    }
    // One wash: the same six tiles.
    tiles.find((t) => text(t.querySelector(".dv")) === "1").click();
    const sheet = window.document.querySelector("wmc-day-sheet");
    eq(sheetTiles(sheet).map((t) => t[1]), ["1", "1 h 52 min", "1 h 52 min", "0.84 kWh", "20p", "20p"]);
    sheet.shadowRoot.querySelector(".close").click();
    // A day without washes: nothing happened, the icon stays grey.
    tiles.find((t) => text(t.querySelector(".dv")) === "0").click();
    const none = window.document.querySelector("wmc-day-sheet");
    assert.match(none.shadowRoot.querySelector(".row .glyph ha-icon").style.color, /grey/);
    none.shadowRoot.querySelector(".close").click();
  }

  // 3. Washing: an orange circle (and nothing else coloured), "Started", the running wash in today's
  //    numbers, the watts in the power pill, which opens the power sensor.
  {
    const { hass } = scenario(window, { status: "washing", watts: "1850.4", startedMinAgo: 48, soFar: 0.62 });
    const { root, events } = mount(window, hass, { entity: "sensor.washing_machine_status", navigation_path: "/lovelace/washing-machine" });
    await tick(10);
    const r = root();
    eq(text(r.querySelector(".nm")), "Washing machine · Washing");
    assert.match(text(r.querySelector(".sc")), /^Started \d\d:\d\d$/);
    assert.match(r.querySelector(".shape ha-icon").style.color, /orange/);
    assert.match(r.querySelector(".shape").style.background, /orange/);
    assert(!r.querySelector("ha-card").getAttribute("style"), "the card itself is never tinted");
    eq(pill(r), "1,850 W");
    eq(r.querySelector(".pill ha-icon").getAttribute("icon"), "mdi:flash");
    r.querySelector(".pill button").click();
    eq(events, [["more-info", "sensor.washing_machine_power"]], "the watts open the power sensor, not the dashboard");
    eq(stats(r), [["0", "Washes today"], ["48 min", "Washing for"], ["0.62 kWh", "Today"], ["15p", "Today's cost"]]);
    // Tiles count finished washes, like the integration.
    eq(text(r.querySelectorAll(".day")[6].querySelector(".dv")), "0");
    assert.match(r.querySelectorAll(".day")[6].getAttribute("aria-label"), /washing now$/);

    // Today's sheet lists the wash in progress, its icon orange like the card's circle.
    const todayTile = r.querySelectorAll(".day")[6];
    todayTile.click();
    const sheet = window.document.querySelector("wmc-day-sheet");
    eq(text(sheet.shadowRoot.querySelector(".title")), "Today");
    assert.match(text(sheet.shadowRoot.querySelector(".sub")), / · Washing now$/);
    eq(sheet.shadowRoot.querySelector(".dialog").getAttribute("aria-labelledby"), "t");
    assert.match(sheet.shadowRoot.querySelector(".row .glyph ha-icon").style.color, /orange/);
    assert.match(text(sheet.shadowRoot.querySelector(".list")), /^\d\d:\d\d – now Washing · 48 min so far$/);
    window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert(!window.document.querySelector("wmc-day-sheet"), "Escape closes it");
    assert.strictEqual(r.activeElement, todayTile, "focus returns to the day");
  }

  // 4. Finished: a green circle; under the name the wash's length, energy and cost.
  {
    const washes = [wash(30 * MIN)];
    const { hass } = scenario(window, { status: "finished", washes, todayWashes: 1 });
    const { root, events } = mount(window, hass);
    await tick(10);
    const r = root();
    eq(text(r.querySelector(".nm")), "Washing machine · Finished");
    eq(text(r.querySelector(".sc")), "1 h 52 min · 0.84 kWh · 20p");
    assert.match(r.querySelector(".shape ha-icon").style.color, /green/);
    eq(stats(r)[0], ["1", "Wash today"]);
    eq(text(r.querySelectorAll(".day")[6].querySelector(".dv")), "1");
    // Without a dashboard to open, each number opens its own sensor.
    r.querySelectorAll(".stats > button")[1].click();
    r.querySelector(".nm").click();
    eq(events, [["more-info", "sensor.washing_machine_last_wash"], ["more-info", "sensor.washing_machine_status"]]);

    r.querySelectorAll(".day")[6].click();
    const sheet = window.document.querySelector("wmc-day-sheet");
    assert.match(text(sheet.shadowRoot.querySelector(".list")), /^\d\d:\d\d – \d\d:\d\d 1 h 52 min · 0\.84 kWh · 20p$/);
    assert.match(text(sheet.shadowRoot.querySelector(".sub")), / · 1 wash$/);
    assert.match(sheet.shadowRoot.querySelector(".row .glyph ha-icon").style.color, /green/);
    sheet.shadowRoot.querySelector(".close").click();
  }

  // 5. Idle after washes today: today's energy and cost from the washes-today sensor.
  {
    const { hass } = scenario(window, { washes: [wash(3 * 3600000)], todayWashes: 2 });
    const { root } = mount(window, hass);
    await tick(10);
    const r = root();
    eq(stats(r).slice(2), [["1.48 kWh", "Today"], ["35p", "Today's cost"]]);
  }
  // 6a. The plug on the Energy dashboard: Today and its cost, the 7 days' total and each day's
  //     energy are what the dashboard counts (standby included), not the washes' own; tapping
  //     Today opens the meter, and a new meter reading fetches again. Each wash's own cost, and
  //     the average per wash, are in pence.
  {
    const days = { [dayKey(new Date())]: 0.28, [dayKey(new Date(Date.now() - DAY))]: 0.4 };
    const washes = [wash(2 * 3600000, 23, 0, 0), wash(3600000, 45, 0.27, 0.0641)];
    const { hass, calls } = scenario(window, { washes, todayWashes: 2, energy: { statistic: "sensor.washing_machine_energy_today", days } });
    hass.states["sensor.washing_machine_energy_today"] = { state: "0.28", attributes: { unit_of_measurement: "kWh" } };
    const { card, root, events } = mount(window, hass);
    await tick(10);
    const r = root();
    eq(stats(r).slice(2), [["0.28 kWh", "Today"], ["7p", "Today's cost"]]);
    eq(text(r.querySelector(".ft")), "Last 7 days · 2 washes · 0.68 kWh · 16p");
    r.querySelectorAll(".stats > button")[2].click();
    eq(events, [["more-info", "sensor.washing_machine_energy_today"]]);

    r.querySelectorAll(".day")[6].click();
    let sheet = window.document.querySelector("wmc-day-sheet");
    eq(sheetTiles(sheet), [["Washes", "2"], ["Time washing", "1 h 8 min"], ["Average", "34 min"], ["Energy", "0.28 kWh"], ["Cost", "7p"], ["Per wash", "3p"]]);
    eq([...sheet.shadowRoot.querySelectorAll(".wash .ws")].map(text), ["23 min · 0.00 kWh · 0p", "45 min · 0.27 kWh · 6p"]);
    sheet.shadowRoot.querySelector(".close").click();
    // A day without washes still shows what the plug used.
    r.querySelectorAll(".day")[5].click();
    sheet = window.document.querySelector("wmc-day-sheet");
    eq(sheetTiles(sheet), [["Washes", "0"], ["Time washing", "–"], ["Average", "–"], ["Energy", "0.40 kWh"], ["Cost", "9p"], ["Per wash", "–"]]);
    sheet.shadowRoot.querySelector(".close").click();

    const before = calls.length;
    hass.states["sensor.washing_machine_energy_today"] = { state: "0.31", attributes: { unit_of_measurement: "kWh" } };
    card.hass = { ...hass, states: { ...hass.states } };
    await tick(10);
    eq(calls.length, before + 1, "a new meter reading fetches today's energy again");
  }

  // 6b. The push's link: the dashboard opens with ?wash=<when it finished, in Unix seconds>. The
  //     card opens that wash in a sheet once this week's washes are in: Washing done, what to do
  //     while it still waits in the machine, the wash in numbers and the last 7 days as the footer
  //     counts them. The link comes off the address (a reload doesn't open it again); other
  //     parameters stay. A link to a wash the card doesn't have is left for the card that has it.
  {
    const hhmm = (ms) => new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: TZ }).format(new Date(ms));
    const days = { [dayKey(new Date())]: 0.32, [dayKey(new Date(Date.now() - DAY))]: 0.29 };
    const washes = [wash(DAY, 45, 0.27, 0.0636), wash(30 * MIN, 127, 0.327, 0.0776)];
    const id = Math.floor(Date.parse(washes[1].end) / 1000);
    window.history.replaceState(null, "", `/lovelace/washing-machine?edit=1&wash=${id}#laundry`);
    const { hass, calls } = scenario(window, { status: "finished", washes, todayWashes: 1, energy: { statistic: "sensor.washing_machine_energy_today", days } });
    const { card, root } = mount(window, hass);
    await tick(10);
    const sheet = window.document.querySelector("wmc-wash-sheet");
    assert(sheet, "the linked wash opens");
    // Opening it from the push is seeing it: the integration ends the Live Activity on the phones.
    eq(calls.filter((c) => c.type === "washing_machine_monitor/wash_seen"), [{ type: "washing_machine_monitor/wash_seen", entity_id: "sensor.washing_machine_status", wash: id }]);
    const sr = sheet.shadowRoot;
    const tilesOf = (sel) => [...sr.querySelectorAll(`${sel} .tile`)].map((t) => [text(t.querySelector("span")), text(t.querySelector("b"))]);
    eq(text(sr.querySelector(".title")), "Washing done");
    eq(text(sr.querySelector(".sub")), `Unload the clothes · finished ${hhmm(Date.parse(washes[1].end))}`);
    eq([...sr.querySelectorAll(".lab")].map(text), ["This wash", "Last 7 days"]);
    eq(tilesOf(".wash-tiles"), [["Took", "2 h 7 min"], ["Energy", "0.33 kWh"], ["Cost", "8p"]]);
    eq(tilesOf(".week-tiles"), [["Washes", "2"], ["Energy", "0.61 kWh"], ["Cost", "14p"]]);
    eq(text(root().querySelector(".ft")), "Last 7 days · 2 washes · 0.61 kWh · 14p", "the same numbers as the footer");
    assert.match(sr.querySelector(".row .glyph").style.background, /green/);
    eq(window.location.search, "?edit=1", "the link comes off the address; the rest stays");
    eq(window.location.hash, "#laundry");
    card.hass = { ...hass };
    await tick(10);
    eq(window.document.querySelectorAll("wmc-wash-sheet").length, 1, "opened once");
    sr.querySelector(".close").click();
    assert(!window.document.querySelector("wmc-wash-sheet"), "Close closes it");

    // An older wash, linked from an old push: when it finished, without "Unload the clothes".
    const older = Math.floor(Date.parse(washes[0].end) / 1000);
    window.history.replaceState(null, "", `/lovelace/washing-machine?wash=${older}`);
    window.dispatchEvent(new window.CustomEvent("location-changed"));
    await tick(10);
    const old = window.document.querySelector("wmc-wash-sheet");
    assert(old, "a link that arrives by navigating opens too");
    eq(text(old.shadowRoot.querySelector(".sub")), `Finished ${new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: TZ }).format(new Date(Date.parse(washes[0].end)))} ${hhmm(Date.parse(washes[0].end))}`);
    eq(old.shadowRoot.querySelectorAll(".wash-tiles .tile").length, 3);
    old.shadowRoot.querySelector(".close").click();

    // Another washing machine's wash: left alone, the link stays for its own card.
    window.history.replaceState(null, "", `/lovelace/washing-machine?wash=${id + 1}`);
    window.dispatchEvent(new window.CustomEvent("location-changed"));
    await tick(10);
    assert(!window.document.querySelector("wmc-wash-sheet"));
    eq(window.location.search, `?wash=${id + 1}`);
    card.remove();
    window.history.replaceState(null, "", "/lovelace/0");
  }

  // 7. Paging back a week, and forward again.
  {
    const washes = [wash(20 * DAY), wash(9 * DAY), wash(8 * DAY), wash(2 * DAY)];
    const { hass, calls } = scenario(window, { washes });
    const { root } = mount(window, hass);
    await tick(10);
    const r = root();
    eq(text(r.querySelector(".ft")), "Last 7 days · 1 wash · 0.84 kWh · 20p");
    assert(!r.querySelector(".prev").disabled, "older washes exist");
    r.querySelector(".prev").click();
    await tick(10);
    eq(calls.length, 2, "fetched the previous week");
    assert(Date.parse(calls[1].start) < Date.parse(calls[0].start) - 6 * DAY);
    const tiles = [...r.querySelectorAll(".day")];
    assert(!tiles.some((t) => t.classList.contains("today")), "no today tile on an older week");
    eq(tiles[6].dataset.d, dayKey(new Date(Date.now() - 7 * DAY)));
    assert.match(text(r.querySelector(".ft")), /^\d{1,2}(–| \w{3} – )\d{1,2} \w{3} · 2 washes · 1\.68 kWh · 40p$/);
    assert(!r.querySelector(".next").disabled);
    r.querySelector(".next").click();
    await tick(10);
    eq(text(r.querySelector(".ft")), "Last 7 days · 1 wash · 0.84 kWh · 20p");
    assert(r.querySelector(".next").disabled);
  }

  // 8. A finished wash refetches (the total changed); an unavailable power sensor says so.
  {
    const s = scenario(window, { status: "washing", watts: "300" });
    const { card, root } = mount(window, s.hass);
    await tick(10);
    eq(s.calls.length, 1);
    s.states["sensor.washing_machine_washes"] = { state: "1", attributes: {} };
    card.hass = { ...s.hass, states: { ...s.states } };
    await tick(10);
    eq(s.calls.length, 2, "refetched after the wash count changed");
    s.states["sensor.washing_machine_power"] = { state: "unavailable", attributes: {} };
    card.hass = { ...s.hass, states: { ...s.states } };
    await tick(10);
    eq(text(root().querySelector(".nm")), "Washing machine · Unavailable");
    eq(pill(root()), null, "no watts while the sensor is unavailable");
  }

  // 9. The wrong entity: Home Assistant's own message on the line under the name, tiles unknown.
  {
    const { hass } = scenario(window, { wsError: { code: "not_found", message: "Not a Washing Machine Monitor entity" } });
    const { root } = mount(window, hass, { entity: "sensor.something_else" });
    await tick(10);
    assert.match(text(root().querySelector(".sc")), /Not a Washing Machine Monitor entity/);
    assert([...root().querySelectorAll(".dv")].every((d) => text(d) === "–"));
  }

  // 10. The fetch window is padded by a day each side: a wash just outside the 7 days is not counted.
  {
    const justOutside = wash(7 * DAY + 2 * 3600000, 60, 0.5, 0.12);
    const { hass } = scenario(window, { washes: [justOutside], firstWash: justOutside.end });
    const { root } = mount(window, hass);
    await tick(10);
    eq(text(root().querySelector(".ft")), "Last 7 days · No washes");
    assert([...root().querySelectorAll(".dv")].every((d) => text(d) === "0"));
  }

  // 11. A name with an apostrophe is written once, not on every update.
  {
    const { hass } = scenario(window, {});
    const { card, root } = mount(window, hass, { entity: "sensor.washing_machine_status", name: "Nancy's washer" });
    await tick(10);
    const t = root().querySelector(".nm");
    eq(text(t), "Nancy's washer · Idle");
    const node = t.firstChild;
    card.hass = { ...hass };
    await tick(5);
    assert.strictEqual(t.firstChild, node, "not rewritten");
  }

  // 12. House style (design tokens), as the appliance card: content on Mushroom's 10px edge, text
  //     never cut off, the power pill (orange, primary tier), the today strip with dividers, tiles 8px
  //     apart, only the icon circle coloured, secondary-tier icons 16px, the house pop-up.
  {
    const { hass } = scenario(window, {});
    const { root } = mount(window, hass);
    await tick(5);
    const css = root().querySelector("style").textContent.replace(/\s+/g, " ");
    assert.doesNotMatch(css, /ellipsis|overflow: hidden/, "no truncated text");
    assert.match(css, /\.in \{ margin: calc\(-1 \* var\(--ha-card-border-width, 1px\)\); \}/);
    assert.match(css, /\.pill \{[^}]*height: 36px[^}]*\}/, "primary pill tier");
    assert.match(css, /\.pill \{[^}]*background: color-mix\(in srgb, var\(--orange-color, #ff9800\) 18%, transparent\); color: color-mix\(in srgb, var\(--orange-color, #ff9800\) 70%, var\(--primary-text-color\)\); \}/);
    assert.match(css, /\.stats > \* \{[^}]*border-left: 1px solid var\(--divider-color/);
    assert.match(css, /\.stats b \{ font-size: 14px; line-height: 20px; font-weight: 500;/);
    assert.match(css, /\.stats span \{ font-size: 11px; line-height: 14px; letter-spacing: \.4px;/);
    assert.match(css, /\.nm \{ font-size: 14px; line-height: 20px; font-weight: 500; letter-spacing: \.1px;/);
    assert.match(css, /\.nm \.st \{ font-weight: 400; color: var\(--secondary-text-color\); white-space: nowrap; \}/);
    assert.match(css, /\.sc \{ font-size: 12px; line-height: 16px; letter-spacing: \.4px;/);
    assert.match(css, /\.week \{[^}]*gap: 8px/);
    assert.match(css, /\.day \{[^}]*padding: 11px 0/);
    assert.match(css, /\.dk \{[^}]*font-size: 11px; line-height: 14px; letter-spacing: \.4px;/);
    assert.match(css, /\.nav ha-icon \{ --mdc-icon-size: 16px; \}/);
    assert.doesNotMatch(css, /\.foot \{[^}]*padding-left/, "the footer starts on the device icon's edge, not the name's column");
    assert.match(css, /\.day\.h1 \{ background: color-mix\(in srgb, var\(--green-color, #4caf50\) 20%, transparent\); \}/);
    assert.match(css, /\.day\.h2 \{ background: color-mix\(in srgb, var\(--green-color, #4caf50\) 40%, transparent\); \}/);
    assert.match(css, /\.day\.h3 \{ background: color-mix\(in srgb, var\(--green-color, #4caf50\) 60%, transparent\); \}/);
    assert.doesNotMatch(css, /glow|\.day\.today/, "no tinted card or tile");
    assert.doesNotMatch(css.replace(/\/\*.*?\*\//g, ""), /transform/, "no transform on press");
  }

  // 12b. The footer's first letter's ink sits 1–2px inside the device circle's left edge: a
  //      straight letter exactly on a round edge looks outside it. jsdom has no canvas or layout,
  //      so a window of its own measures every first letter's ink 0.8px in from its box, and boxes
  //      start at 10px (the circle's left edge) plus their margin.
  {
    const w = await setup({ canvas: { font: "", measureText: () => ({ actualBoundingBoxLeft: -0.8 }) } });
    w.HTMLElement.prototype.getBoundingClientRect = function () {
      const left = 10 + (this.classList.contains("shape") ? 0 : parseFloat(this.style.marginLeft) || 0);
      return { left, right: left, top: 0, bottom: 0, width: 0, height: 0, x: left, y: 0 };
    };
    const { hass } = scenario(w, { washes: [wash(DAY)] });
    const { card, root } = mount(w, hass);
    await tick(60);
    const ft = root().querySelector(".ft");
    const shape = root().querySelector(".shape");
    const inset = () => ft.getBoundingClientRect().left + 0.8 - 10; // the ink's distance inside the edge
    assert(inset() >= 1 && inset() <= 2, `the ink sits 1–2px inside the circle's edge (${inset()})`);
    const set = ft.style.marginLeft;
    w.inkToEdge(ft, shape, inset());
    eq(ft.style.marginLeft, set, "measuring again leaves it where it is");
    w.inkToEdge(ft, shape);
    eq(inset().toFixed(2), "0.00", "without an inset the ink is exactly on the edge");
    card.remove();
  }

  // 13. Loaded twice (resource added again) is harmless.
  window.eval(SRC);
  await tick();

  console.log("washing-machine-card: all tests passed");
  process.exit(0); // the cards' 30 s timers would keep node running
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
