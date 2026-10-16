/*
 * Washing machine card – part of Washing Machine Monitor
 * https://github.com/igiannakas/ha-washing-machine-monitor
 *
 * A sibling of the appliance card (homeassistant-custom-cards), built on the same rules:
 *
 * - Head row: the icon circle (orange while washing – an appliance that is on, green when a wash
 *   has finished, grey otherwise), "Name · Washing" and, under it, what the wash is doing
 *   ("Started 17:31", or the finished wash's length, energy and cost in pence). At the end, while a wash
 *   runs, the power pill with the plug's watts (orange), which opens the power sensor. The card
 *   doesn't switch the plug.
 * - Today's numbers, split by straight dividers: washes, the running wash's time (or when the last
 *   one ended), and the plug's energy today and its cost, as the Energy dashboard counts them
 *   (the integration reads the plug's entry there). A plug that isn't on the Energy dashboard:
 *   today's washes' energy, the running one counted in.
 * - One tile per day for the last 7 days (washes that finished that day, today on the right) and
 *   the 7 days' totals, with ‹ › to page back a week at a time. Tapping a day opens its sheet: the
 *   date and count under the day, six tiles (washes, time washing, average; energy and cost from
 *   the Energy dashboard; cost per wash in pence), then each wash.
 * - Anywhere else opens `navigation_path` (or the status's details without one).
 *
 * Everything comes from the integration: its entities for the live state, its websocket command
 * for the wash log.
 *
 *   type: custom:washing-machine-card
 *   entity: sensor.washing_machine_status        # any Washing Machine Monitor entity
 *   name: Washing Machine                        # optional
 *   navigation_path: /lovelace/washing-machine   # optional
 */

const WMC_VERSION = "1.4.3";
const WMC_TAG = "washing-machine-card";
const WMC_DOMAIN = "washing_machine_monitor";
/* The "washing done" push opens the dashboard with ?wash=<when the wash finished, in Unix seconds>. */
const LINK_KEY = "wash";

const C = {
  grey: "var(--grey-color, #9e9e9e)",
  green: "var(--green-color, #4caf50)",
  orange: "var(--orange-color, #ff9800)",
};
const tint = (c, p) => `color-mix(in srgb, ${c} ${p}%, transparent)`;
// Readable coloured text – mixed toward the text colour, never the raw colour on white.
const text = (c, p = 75) => `color-mix(in srgb, ${c} ${p}%, var(--primary-text-color))`;
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (v) => (v === null || v === undefined || v === "" || !isFinite(Number(v)) ? null : Number(v));
const fmt = (n, d = 0) => (n === null ? "–" : n.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }));
const LIVE = (s) => s !== undefined && s !== null && s !== "unavailable" && s !== "unknown";
const DAY_MS = 86400000;

/* Status lines wrap only between their parts (each a nowrap span); the dot stays with the part
   before it. A part given as a list (energy and its cost) is kept together on one line. */
const SEP = " · ";
const parts = (list) => list.map((p) => `<span>${Array.isArray(p) ? p.map(esc).join(SEP) : esc(p)}</span>`).join(SEP);

/* ---------- Words and numbers ---------- */

/* 45 min, 1 h 23 min, 3 h, 2 d (design rule: a space, short units); under a minute "<1 min". */
function dur(minutes) {
  const m = Math.max(0, Math.floor(minutes));
  if (m < 1) return "<1 min";
  if (m < 60) return `${m} min`;
  if (m < 1440) return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
  return `${Math.floor(m / 1440)} d`;
}

const kwhText = (v) => `${fmt(v, v < 10 ? 2 : v < 100 ? 1 : 0)} kWh`;
const washesText = (n) => (n === 1 ? "1 wash" : `${n} washes`);


/* Every cost on the card: pence under a pound ("7p", under a penny "0.5p"), from a pound up in
   pounds ("£1.20") – the rule of the dashboard's cost per hour and the integration's push. */
const MINOR = { GBP: "p", EUR: "c", USD: "¢" };
function costText(v, currency) {
  if (v === null || v === undefined || !currency) return null;
  const unit = MINOR[currency];
  if (!unit || v >= 0.995) return money(v, currency);
  const p = Math.max(0, v) * 100;
  return `${p > 0 && p < 0.95 ? fmt(p, 1) : fmt(Math.floor(p + 0.5))}${unit}`;
}

function money(v, currency) {
  if (v === null || v === undefined || !currency) return null;
  try {
    return new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(v);
  } catch (e) {
    return `${v.toFixed(2)} ${currency}`;
  }
}

/* Formatters are costly to build: one per time zone and kind, reused on every update. */
const FMT = new Map();
function formatter(kind, tz, opts) {
  const key = `${kind}|${tz || ""}`;
  if (!FMT.has(key)) {
    let f;
    try {
      f = new Intl.DateTimeFormat(kind === "day" ? "en-CA" : "en-GB", { ...opts, timeZone: tz || undefined });
    } catch (e) {
      f = new Intl.DateTimeFormat(kind === "day" ? "en-CA" : "en-GB", opts); // unknown zone: the browser's
    }
    FMT.set(key, f);
  }
  return FMT.get(key);
}

/* Calendar days in Home Assistant's time zone, as "YYYY-MM-DD" keys. */
function dayKeyFn(tz) {
  const f = formatter("day", tz, { year: "numeric", month: "2-digit", day: "2-digit" });
  return (d) => f.format(d);
}
const keyToUTC = (k) => Date.UTC(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, Number(k.slice(8, 10)));
const addDays = (k, n) => new Date(keyToUTC(k) + n * DAY_MS).toISOString().slice(0, 10);
const fmtKey = (k, opts) => new Date(keyToUTC(k)).toLocaleDateString("en-GB", { timeZone: "UTC", ...opts });

function timeFn(tz) {
  const f = formatter("time", tz, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return (d) => f.format(d);
}

/* "14:20" today, "Tue 18:10" within the week, "4 Oct" before that (as the appliance card). */
function since(ms, tz) {
  const dk = dayKeyFn(tz);
  const k = dk(new Date(ms));
  const today = dk(new Date());
  if (k === today) return timeFn(tz)(new Date(ms));
  if (Date.now() - ms < 6.5 * DAY_MS) return `${fmtKey(k, { weekday: "short" })} ${timeFn(tz)(new Date(ms))}`;
  return fmtKey(k, { day: "numeric", month: "short" });
}

/* "8–14 Oct", "29 Sep – 5 Oct", "29 Dec 2025 – 4 Jan" */
function rangeText(first, last) {
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  if (first.slice(0, 7) === last.slice(0, 7)) return `${Number(first.slice(8))}–${fmtKey(last, { day: "numeric", month: "short" })}`;
  return `${fmtKey(first, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) })} – ${fmtKey(last, { day: "numeric", month: "short" })}`;
}

/* ---------- Ink alignment (design rule: lines under a title start at its first letter, by ink) ---------- */

const INK = { ctx: undefined };
const inkCtx = () => {
  if (INK.ctx === undefined)
    INK.ctx = /jsdom/i.test(navigator.userAgent) ? null : document.createElement("canvas").getContext("2d");
  return INK.ctx;
};
const inkVisible = (el) => !el.hidden && getComputedStyle(el).display !== "none";
function inkLead(el) {
  for (const n of el?.childNodes || []) {
    if (n.nodeType === 3) {
      if (n.textContent.trim()) return n;
    } else if (n.nodeType === 1 && inkVisible(n)) {
      const r = inkLead(n);
      if (r) return r;
    }
  }
  return null;
}
function inkText(node) {
  const ctx = inkCtx();
  if (!ctx || !node) return null;
  const cs = getComputedStyle(node.parentElement);
  ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  return -ctx.measureText(node.textContent.trim()[0]).actualBoundingBoxLeft;
}
/* Give each line the margin that puts its first letter's ink on the reference line's. */
function inkAlign(ref, lines) {
  const a = inkText(inkLead(ref));
  if (a === null) return;
  for (const el of lines) {
    if (!el || !inkVisible(el)) continue;
    const b = inkText(inkLead(el));
    if (b === null) continue;
    const v = Math.abs(a - b) > 0.2 ? `${(a - b).toFixed(2)}px` : "";
    if (el.style.marginLeft !== v) el.style.marginLeft = v;
  }
}

/* A round edge reaches its extreme at a single point, so the eye places it further in than it is:
   a straight letter put exactly on it looks outside it. This much inside lines them up to the eye. */
const ROUND_EDGE_INSET = 1.5;

/* Put a line's first letter's ink `inset` px inside another element's left edge (the footer under
   the device icon). */
function inkToEdge(el, ref, inset = 0) {
  if (!el || !ref || !inkVisible(el)) return;
  const b = inkText(inkLead(el));
  if (b === null) return;
  const now = parseFloat(el.style.marginLeft) || 0;
  const dx = ref.getBoundingClientRect().left + inset - (el.getBoundingClientRect().left - now) - b;
  const v = Math.abs(dx) > 0.2 ? `${dx.toFixed(2)}px` : "";
  if (el.style.marginLeft !== v) el.style.marginLeft = v;
}

/* ---------- Pop-ups (the house Bubble look, as the printer card's) ---------- */

const DIALOG_CSS = `
  /* Icons centre their glyph with flex. As a plain block, ha-icon lays its glyph out on a text line
     and it sits 0.4–3px low (design rule: glyphs are centred on their row, measured). */
  ha-icon { display: inline-flex; align-items: center; justify-content: center; }
  ha-icon[hidden] { display: none; }
  :host { position: fixed; inset: 0; z-index: 9999; display: flex; align-items: center; justify-content: center;
    font-family: var(--ha-font-family-body, Roboto, sans-serif); }
  .backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.32); backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px); animation: fade 160ms ease-out; }
  .dialog { position: relative; box-sizing: border-box; width: min(400px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto;
    padding: 25px 18px 18px; border-radius: 32px; color: var(--primary-text-color);
    background: color-mix(in srgb, var(--card-background-color, #fff) 94%, transparent);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); box-shadow: 0 12px 40px rgba(0,0,0,.25);
    animation: pop 180ms cubic-bezier(.2,.9,.3,1.2); }
  .row { display: flex; align-items: center; gap: 6px; padding-left: 8px; }
  .glyph { flex: 0 0 42px; height: 42px; display: flex; align-items: center; justify-content: center; border-radius: 50%; }
  .glyph ha-icon { --mdc-icon-size: 24px; }
  .title { min-width: 0; font-size: 20px; line-height: 26px; font-weight: 600; overflow-wrap: break-word; }
  /* The date and the count sit under the title, beside the glyph. The device isn't named: the
     pop-up opens from its own card. */
  .hd { min-width: 0; }
  .sub { font-size: 15px; line-height: 21px; color: var(--secondary-text-color); }
  .sub span { white-space: nowrap; }
  /* The day in numbers: three tiles a row, as the card's day tiles (radius 10, 8px apart), the label
     over its value. Values wrap rather than being cut off. */
  .tiles { margin-top: 20px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .tile { display: grid; align-content: center; gap: 2px; min-height: 64px; padding: 10px 6px; box-sizing: border-box;
    border-radius: 10px; text-align: center; background: rgba(var(--rgb-primary-text-color, 33,33,33), .05); }
  .tile span { font-size: 12px; line-height: 16px; letter-spacing: .4px; color: var(--secondary-text-color); }
  .tile b { font-size: 16px; line-height: 24px; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: break-word; }
  /* A label over a row of tiles ("This wash", "Last 7 days") starts on the glyph's left edge, its
     first letter's ink measured onto it (set optically inside the round edge), as the card's footer
     does on the device icon. */
  .lab { margin: 20px 0 8px; font-size: 12px; line-height: 16px; font-weight: 500; letter-spacing: .4px; color: var(--secondary-text-color); }
  .lab + .tiles { margin-top: 0; }
  .buttons { margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--divider-color, rgba(0,0,0,.12)); display: grid; gap: 8px; }
  button { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 10px; height: 56px; padding-left: 10px;
    border-radius: 28px; cursor: pointer; font-size: 16px; font-weight: 600;
    background: rgba(var(--rgb-primary-text-color, 33,33,33), .07); color: var(--primary-text-color); }
  button .glyph { flex-basis: 36px; height: 36px; background: ${tint(C.grey, 20)}; }
  button .glyph ha-icon { --mdc-icon-size: 22px; color: ${C.grey}; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  button:active { filter: brightness(.92); }
  /* The day's washes, each in its glyph's circle as in the chosen design: green when finished,
     orange while running. */
  .list { margin-top: 20px; display: grid; gap: 8px; }
  .list:empty { display: none; }
  .wash { display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 6px 12px 6px 10px; box-sizing: border-box;
    border-radius: 28px; background: rgba(var(--rgb-primary-text-color, 33,33,33), .05); }
  .wash .glyph { flex-basis: 36px; height: 36px; }
  .wash .glyph ha-icon { --mdc-icon-size: 22px; }
  .wp { font-size: 16px; line-height: 24px; font-weight: 600; letter-spacing: .1px; font-variant-numeric: tabular-nums; }
  .ws { font-size: 14px; line-height: 20px; color: var(--secondary-text-color); }
  .ws span { white-space: nowrap; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: scale(.94); } }
`;

/* A pop-up shell: an undefined tag with a shadow root on document.body (HA uses a scoped
   registry). Escape and the backdrop close it; the focus stays inside and goes back on close. */
function openPopup(tag, html, { onClose, focus, opener }) {
  const host = document.createElement(tag);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${DIALOG_CSS}</style><div class="backdrop"></div>${html}`;
  const close = (result) => {
    document.removeEventListener("keydown", onKey, true);
    host.remove();
    if (opener?.isConnected) opener.focus();
    onClose?.(result);
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close(false);
    } else if (e.key === "Tab") {
      const buttons = [...root.querySelectorAll("button")];
      if (!buttons.length) return;
      e.preventDefault();
      const i = buttons.indexOf(root.activeElement);
      buttons[(i + (e.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
    }
  };
  root.querySelector(".backdrop").addEventListener("click", () => close(false));
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(host);
  root.querySelector(focus)?.focus();
  return { host, root, close };
}

/* ---------- Card ---------- */

const CSS = `
  /* Icons centre their glyph with flex. As a plain block, ha-icon lays its glyph out on a text line
     and it sits 0.4–3px low (design rule: glyphs are centred on their row, measured). */
  ha-icon { display: inline-flex; align-items: center; justify-content: center; }
  ha-icon[hidden] { display: none; }
  :host { display: block; }
  /* Only the icon circle carries the state's colour, as on the appliance cards; the card stays
     neutral. A tap anywhere but the controls opens the washing machine's dashboard. */
  ha-card { padding: 10px; cursor: pointer; -webkit-tap-highlight-color: transparent; }
  /* Content starts 10px from the card's outer edge, as in Mushroom: .in is pulled over the theme's
     card border the way Mushroom does it, so the icon and name line up with a Mushroom card above
     or below. A theme that sets a unitless 0 makes the calc invalid, the margin falls back to 0,
     and that is right, because there is no border then. */
  .in { margin: calc(-1 * var(--ha-card-border-width, 1px)); }
  /* One centre line: circle, text block and pill. */
  .head { display: flex; align-items: center; gap: 10px; }
  /* The circle and the text are one button (the keyboard's way to the dashboard). */
  .open { all: unset; box-sizing: border-box; flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px;
    cursor: pointer; border-radius: 18px; -webkit-tap-highlight-color: transparent; }
  .shape { flex: 0 0 36px; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    transition: background-color 180ms; }
  .shape ha-icon { --mdc-icon-size: 24px; transition: color 180ms; }
  .txt { flex: 1; min-width: 0; }
  .txt > span { display: block; }
  /* Never cut off: the name wraps, and the state word moves to the next line whole, the dot
     staying with the name. Alone, the name sits 1px low to centre on the circle. */
  .nm { font-size: 14px; line-height: 20px; font-weight: 500; letter-spacing: .1px; color: var(--primary-text-color); overflow-wrap: break-word; }
  .txt.one .nm { position: relative; top: 1px; }
  .nm .st { font-weight: 400; color: var(--secondary-text-color); white-space: nowrap; }
  .sc { font-size: 12px; line-height: 16px; letter-spacing: .4px; color: var(--secondary-text-color); }
  .sc:empty { display: none; }
  .sc span { white-space: nowrap; }
  /* The power pill, as the watts half of the printer and appliance cards' pill: orange, shown
     while a wash runs, and it opens the power sensor. */
  .pill { flex: none; display: inline-flex; align-items: stretch; height: 36px; border-radius: 18px;
    font-size: 13px; font-weight: 600; letter-spacing: .2px; white-space: nowrap;
    background: ${tint(C.orange, 18)}; color: ${text(C.orange, 70)}; }
  .pill[hidden] { display: none; }
  .pill button { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 6px; padding: 0 14px 0 10px;
    cursor: pointer; -webkit-tap-highlight-color: transparent; }
  .pill ha-icon { --mdc-icon-size: 18px; color: ${C.orange}; }
  /* Today's numbers, split by straight 1px dividers (as on the appliance and printer cards). */
  .stats { display: grid; grid-template-columns: repeat(var(--n, 4), minmax(0, 1fr)); margin: 10px 0 0; text-align: center; }
  .stats:empty { display: none; }
  .stats > * { display: grid; align-content: start; min-width: 0; border-left: 1px solid var(--divider-color, rgba(0,0,0,.12)); }
  .stats > :first-child { border-left: none; }
  /* Without a dashboard to open, every number opens its own details. */
  .stats > button { all: unset; box-sizing: border-box; display: grid; align-content: start; min-width: 0; cursor: pointer;
    border-left: 1px solid var(--divider-color, rgba(0,0,0,.12)); -webkit-tap-highlight-color: transparent; }
  .stats b { font-size: 14px; line-height: 20px; font-weight: 500; color: var(--primary-text-color); white-space: nowrap; }
  .stats span { font-size: 11px; line-height: 14px; letter-spacing: .4px; color: var(--secondary-text-color); overflow-wrap: break-word; padding: 0 4px; }
  /* The days: tiles 8px apart, every one the same height; the weekday caption over the count. */
  .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); grid-auto-rows: 1fr; gap: 8px; margin-top: 10px; }
  .day { all: unset; box-sizing: border-box; min-width: 0; border-radius: 10px; padding: 11px 0; text-align: center; cursor: pointer;
    background: rgba(var(--rgb-primary-text-color, 33,33,33), .04); -webkit-tap-highlight-color: transparent; }
  .dk { display: block; font-size: 11px; line-height: 14px; letter-spacing: .4px; color: var(--secondary-text-color); white-space: nowrap; }
  .dv { display: block; font-size: 14px; line-height: 20px; font-weight: 500; color: var(--primary-text-color); }
  .dv.zero { color: var(--secondary-text-color); }
  /* A heat map of washes: one hue (green, as finished washes everywhere on the card), stronger
     with each wash – 1, 2, 3 or more – the same steps every week. The count is printed on every
     tile, so the colour is never the only cue; on a coloured tile both lines use the primary text
     colour, which reads on every step in light and dark themes. */
  .day.h1 { background: ${tint(C.green, 20)}; }
  .day.h2 { background: ${tint(C.green, 40)}; }
  .day.h3 { background: ${tint(C.green, 60)}; }
  .day.h1 .dk, .day.h2 .dk, .day.h3 .dk { color: var(--primary-text-color); }
  /* The footer line starts on the device icon's left edge, its first letter's ink measured onto it
     and set optically inside the round edge (ROUND_EDGE_INSET); the arrows end on the right edge,
     under the last tile. */
  .foot { display: flex; align-items: center; gap: 8px; margin-top: 8px; min-height: 30px; }
  .ft { flex: 1; min-width: 0; font-size: 12px; line-height: 16px; letter-spacing: .4px; color: var(--secondary-text-color); }
  .ft span { white-space: nowrap; }
  .nav { all: unset; box-sizing: border-box; flex: none; width: 30px; height: 30px; border-radius: 15px; cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center; color: var(--secondary-text-color);
    background: rgba(var(--rgb-primary-text-color, 33,33,33), .05); -webkit-tap-highlight-color: transparent; }
  .nav ha-icon { --mdc-icon-size: 16px; }
  .nav[disabled] { opacity: .35; cursor: default; }
  /* filter, never transform: scaling moves the hit area mid-tap. */
  .open:active, .pill button:active, .stats > button:active, .day:active, .nav:not([disabled]):active { filter: brightness(.92); }
  .open:focus-visible, .pill button:focus-visible, .stats > button:focus-visible, .day:focus-visible, .nav:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
`;

/* The circle shows the washing machine's state: washing is orange (an appliance that is on, like
   the dryer above it), a finished wash green, everything else grey. */
const STATUS_COLOR = { washing: C.orange, finished: C.green };
const WORD = { loading: "Loading", unavailable: "Unavailable", washing: "Washing", finished: "Finished", idle: "Idle" };

class WashingMachineCard extends HTMLElement {
  static getStubConfig(hass) {
    const ids = Object.keys(hass?.states || {});
    const found = ids.find((id) => id.startsWith("sensor.") && JSON.stringify(hass.states[id].attributes?.options) === '["idle","washing","finished"]');
    return { entity: found || "sensor.washing_machine_status" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { integration: WMC_DOMAIN } } },
        { type: "grid", name: "", schema: [
          { name: "name", selector: { text: {} } },
          { name: "icon", selector: { icon: {} } },
        ] },
        { name: "navigation_path", selector: { navigation: {} } },
      ],
      computeLabel: (s) =>
        ({ entity: "Washing machine", name: "Name", icon: "Icon", navigation_path: "Tap opens" })[s.name] ?? s.name,
      computeHelper: (s) =>
        ({
          entity: "Any entity of the Washing Machine Monitor integration, for example its Status sensor.",
          navigation_path: "Leave empty to open the status's details instead.",
        })[s.name],
    };
  }

  setConfig(config) {
    if (!config?.entity) throw new Error("Set entity: a Washing Machine Monitor entity, e.g. sensor.washing_machine_status");
    this._config = { ...config };
    this._built = false;
    this._page = 0;
    this._data = null;
    this._sig = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  connectedCallback() {
    // The running wash's time and "today" move on even when no entity changes.
    clearInterval(this._timer);
    this._timer = setInterval(() => this._hass && this._render(), 30000);
    // Navigating within the app can bring a ?wash= link without a reload.
    this._onLocation ??= () => this._hass && this._render();
    window.addEventListener("location-changed", this._onLocation);
    if (this._hass) this._render();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    window.removeEventListener("location-changed", this._onLocation);
    this._sheet?.close();
  }

  /* ---- data ---- */

  _tz() {
    return this._hass?.config?.time_zone;
  }

  _days() {
    const key = dayKeyFn(this._tz());
    const last = addDays(key(new Date()), -7 * this._page);
    return Array.from({ length: 7 }, (_, i) => addDays(last, i - 6));
  }

  /* What the fetched data depends on: the page, the day, the wash count and status, and the
     plug's energy meter (a new reading changes today's energy; a statistic that isn't an entity
     is fetched again every 5 minutes, as its statistics are compiled). */
  _signature() {
    const S = this._hass.states;
    const ent = this._data?.entities || {};
    const st = S[this._config.entity];
    const stat = this._data?.energy?.statistic;
    const meter = stat ? (S[stat] ? S[stat].state : Math.floor(Date.now() / 300000)) : "";
    return [this._page, dayKeyFn(this._tz())(new Date()), st?.state, st?.last_changed, S[ent.washes]?.state, S[ent.status]?.state, meter].join("|");
  }

  /* Fetch again when a wash starts or ends, the day changes, the page changes or the meter moves. */
  _maybeFetch() {
    const sig = this._signature();
    // After a failure, one retry at a time, at most every 30 s (not one per state update).
    const retry = this._error && !this._inflight && Date.now() >= (this._retryAt || 0);
    if (sig === this._sig && !retry) return;
    this._sig = sig;
    this._fetch();
  }

  async _fetch() {
    const seq = (this._seq = (this._seq || 0) + 1);
    this._inflight = true;
    const days = this._days();
    // A day either side, so every time zone's midnight is covered; the days are bucketed locally.
    const start = new Date(keyToUTC(days[0]) - DAY_MS).toISOString();
    const end = new Date(keyToUTC(days[6]) + 2 * DAY_MS).toISOString();
    try {
      const r = await this._hass.callWS({ type: `${WMC_DOMAIN}/washes`, entity_id: this._config.entity, start, end });
      if (seq !== this._seq) return;
      this._data = { ...r, days };
      this._error = "";
      // The first answer names the wash-count entity; count it in now, so its arrival isn't a change.
      this._sig = this._signature();
    } catch (err) {
      if (seq !== this._seq) return;
      this._retryAt = Date.now() + 30000; // the 30 s timer re-renders, which retries
      // Home Assistant's own message when it has a useful one, else "Couldn't <what you tried>."
      this._error = err?.code === "unknown_command"
        ? "Washing Machine Monitor isn't loaded."
        : err?.message || `Couldn't load the washes of ${this._name()}.`;
    } finally {
      if (seq === this._seq) this._inflight = false;
    }
    this._render();
  }

  _price() {
    return num(this._data?.price);
  }

  _currency() {
    return this._data?.currency || this._hass?.config?.currency;
  }

  _name() {
    return this._config.name || this._data?.title || "Washing machine";
  }

  /* The card's whole view of the washing machine: everything the DOM shows, as plain values. */
  _model() {
    const S = this._hass.states;
    const d = this._data;
    const ent = d?.entities || {};
    const cur = this._currency();
    const price = this._price();
    const tz = this._tz();
    const time = timeFn(tz);
    const st = S[ent.status || this._config.entity];
    const power = d ? S[d.power_entity_id] : null;
    let key;
    if (this._error && !d) key = "unavailable";
    else if (!d) key = "loading";
    else if (!power || !LIVE(power.state)) key = "unavailable";
    else key = st && ["idle", "washing", "finished"].includes(st.state) ? st.state : d.status || "idle";

    // The line under the name: what the wash is doing.
    let line = [];
    let elapsed = null;
    let used = null;
    if (this._error) line = [this._error];
    else if (key === "washing") {
      const started = Date.parse(st?.attributes?.started || d.current?.started || "");
      if (Number.isFinite(started)) {
        elapsed = (Date.now() - started) / 60000;
        line.push(`Started ${time(new Date(started))}`);
      }
      // The integration adds the wash's energy up from the power readings as it runs.
      used = num(st?.attributes?.kwh ?? d.current?.kwh);
    } else if (key === "finished") {
      const lw = S[ent.last_wash];
      const bits = [];
      if (num(lw?.attributes?.minutes) !== null) bits.push(dur(lw.attributes.minutes));
      const kwh = num(lw?.state);
      const cost = num(lw?.attributes?.cost) ?? (kwh !== null && price !== null ? kwh * price : null);
      const pair = [kwh !== null ? kwhText(kwh) : null, costText(cost, cur)].filter(Boolean);
      if (pair.length) bits.push(pair);
      line = bits;
    }

    // Today's energy and cost, only once there is something to tell: a wash finished today, or
    // one is running (and then how long it has been going). The tiles below already show how many
    // washes each day had, today's included, and when the last one was; a day without washes
    // shows none of these.
    const today = S[ent.washes_today];
    const done = num(today?.state);
    const stats = [];
    if (d && (done > 0 || key === "washing")) {
      if (key === "washing") stats.push({ v: elapsed === null ? "–" : dur(elapsed), l: "Washing for", e: ent.status });
      const board = d.energy ? num(d.energy.days?.[dayKeyFn(tz)(new Date())]) : null;
      let kwh;
      let cost;
      if (d.energy) {
        // As the Energy dashboard counts the plug today, standby included.
        kwh = board;
        cost = kwh !== null && price !== null ? kwh * price : null;
      } else {
        const doneKwh = done === 0 ? 0 : num(today?.attributes?.kwh);
        kwh = doneKwh === null && used === null ? null : (doneKwh ?? 0) + (used ?? 0);
        const doneCost = done === 0 ? 0 : num(today?.attributes?.cost);
        const nowCost = used !== null && price !== null ? used * price : null;
        cost = doneCost === null && nowCost === null ? null : (doneCost ?? 0) + (nowCost ?? 0);
      }
      const tap = (d.energy && S[d.energy.statistic] ? d.energy.statistic : null) || ent.washes_today;
      stats.push({ v: kwh === null ? "–" : kwhText(kwh), l: "Today", e: tap });
      if (cur && (cost !== null || price !== null)) stats.push({ v: cost === null ? "–" : costText(cost, cur), l: "Today's cost", e: tap });
    }

    const w = power && LIVE(power.state) ? num(power.state) : null;
    return {
      key,
      word: WORD[key],
      color: STATUS_COLOR[key] || C.grey,
      icon: this._config.icon || "mdi:washing-machine",
      name: this._name(),
      line,
      stats,
      watts: w,
    };
  }

  /* ---- DOM ---- */

  _build() {
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${CSS}</style><ha-card><div class="in">
      <div class="head">
        <button class="open"><span class="shape"><ha-icon></ha-icon></span><span class="txt"><span class="nm"></span><span class="sc"></span></span></button>
        <div class="pill"><button class="w"><ha-icon icon="mdi:flash"></ha-icon><span></span></button></div>
      </div>
      <div class="stats"></div>
      <div class="week">${Array.from({ length: 7 }, () => `<button class="day"><span class="dk"></span><span class="dv"></span></button>`).join("")}</div>
      <div class="foot"><div class="ft"></div>
        <button class="nav prev" aria-label="Previous week"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
        <button class="nav next" aria-label="Next week"><ha-icon icon="mdi:chevron-right"></ha-icon></button></div>
      </div></ha-card>`;
    root.querySelector("ha-card").addEventListener("click", (e) => this._click(e));
    this._built = true;
    // Ink offsets are measured with the real font: measure again once the web fonts are in.
    document.fonts?.ready?.then(() => this._ink());
  }

  _click(e) {
    if (e.target.closest(".pill")) return this._moreInfo(this._data?.power_entity_id);
    const nav = e.target.closest(".nav");
    if (nav) {
      if (!nav.disabled) {
        this._page = Math.max(0, this._page + (nav.classList.contains("prev") ? 1 : -1));
        this._render();
      }
      return;
    }
    const day = e.target.closest(".day");
    if (day) return this._openDay(day.dataset.d, day);
    const value = e.target.closest(".stats > button[data-e]");
    if (value) return this._moreInfo(value.dataset.e);
    this._open();
  }

  /* As the appliance card: the dashboard, or the status's details without one. */
  _open() {
    const path = this._config.navigation_path;
    if (path) {
      history.pushState(null, "", path);
      this.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false }, bubbles: true, composed: true }));
    } else this._moreInfo(this._data?.entities?.status || this._config.entity);
  }

  _moreInfo(id) {
    if (id) this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: id }, bubbles: true, composed: true }));
  }

  /* Only touch the DOM when something changed, so a tap is never lost mid-update. Compared with
     what was last written, not innerHTML, which the browser re-serialises ("&#39;" reads back as "'"). */
  _set(node, html) {
    if (!node || node._wmcHtml === html) return false;
    node._wmcHtml = html;
    node.innerHTML = html;
    return true;
  }

  _render() {
    if (!this._config || !this._hass) return;
    if (!this._built) this._build();
    this._maybeFetch();
    const root = this.shadowRoot;
    const d = this._data;
    const cur = this._currency();
    const m = this._model();
    this._m = m;

    // --- head ---
    const shape = root.querySelector(".shape");
    const bg = tint(m.color, 20);
    if (shape.style.background !== bg) shape.style.background = bg;
    const ic = shape.querySelector("ha-icon");
    if (ic.getAttribute("icon") !== m.icon) ic.setAttribute("icon", m.icon);
    if (ic.style.color !== m.color) ic.style.color = m.color;
    this._set(root.querySelector(".nm"), `${esc(m.name)}<span class="st">${SEP}${esc(m.word)}</span>`);
    this._set(root.querySelector(".sc"), parts(m.line));
    root.querySelector(".txt").classList.toggle("one", !m.line.length);
    root.querySelector(".open").setAttribute("aria-label", `${m.name}, ${m.word}`);

    // The watts, while a wash runs.
    const pill = root.querySelector(".pill");
    const showW = m.watts !== null && m.key === "washing";
    if (pill.hidden !== !showW) pill.hidden = !showW;
    this._set(pill.querySelector("span"), showW ? `${fmt(m.watts)} W` : "");
    pill.querySelector(".w").setAttribute("aria-label", showW ? `${m.name} power, ${fmt(m.watts)} W` : "");

    const stats = root.querySelector(".stats");
    stats.style.setProperty("--n", String(m.stats.length || 1));
    // With a dashboard to open, the whole card is one tap target; without one, each number opens
    // its own sensor (design rule).
    const cell = this._config.navigation_path
      ? (s) => `<div><b>${esc(s.v)}</b><span>${esc(s.l)}</span></div>`
      : (s) => `<button data-e="${esc(s.e)}"><b>${esc(s.v)}</b><span>${esc(s.l)}</span></button>`;
    this._set(stats, m.stats.map(cell).join(""));

    // --- the days ---
    const days = d?.days?.length === 7 && d.days[6] === this._days()[6] ? d.days : this._days();
    const dayKey = dayKeyFn(this._tz());
    const todayKey = dayKey(new Date());
    const own = d && d.days === days;
    const byDay = Object.fromEntries(days.map((k) => [k, []]));
    for (const wsh of own ? d.washes : []) {
      const k = dayKey(new Date(wsh.end));
      if (byDay[k]) byDay[k].push(wsh); // the fetch window is padded; only the 7 tiles count
    }
    const tiles = root.querySelectorAll(".day");
    days.forEach((k, i) => {
      const el = tiles[i];
      const isToday = k === todayKey;
      // Finished washes, like the integration's counts.
      const n = byDay[k].length;
      if (el.dataset.d !== k) el.dataset.d = k;
      el.classList.toggle("today", isToday);
      const label = `${fmtKey(k, { weekday: "long", day: "numeric", month: "long" })}: ${own ? (n ? washesText(n) : "no washes") : "not loaded"}${isToday && m.key === "washing" ? ", washing now" : ""}`;
      if (el.getAttribute("aria-label") !== label) el.setAttribute("aria-label", label);
      this._set(el.querySelector(".dk"), isToday ? "Today" : esc(fmtKey(k, { weekday: "short" })));
      const dv = el.querySelector(".dv");
      // Zero is a number; "–" means unknown (not loaded, or the log couldn't be read).
      this._set(dv, own ? String(n) : this._error ? "–" : "&nbsp;");
      dv.classList.toggle("zero", !own || !n);
      const heat = own ? Math.min(n, 3) : 0;
      for (const h of [1, 2, 3]) el.classList.toggle(`h${h}`, heat === h);
    });

    // --- footer: the 7 days' totals and the arrows ---
    let foot = "";
    if (own) {
      const list = Object.values(byDay).flat();
      const bits = [this._page === 0 ? "Last 7 days" : rangeText(days[0], days[6]), list.length ? washesText(list.length) : "No washes"];
      const pair = [];
      const { kwh, cost } = this._totals(days, list);
      if (kwh !== null) pair.push(kwhText(kwh));
      const total = cost !== null ? costText(cost, cur) : null;
      if (total) pair.push(total);
      if (pair.length) bits.push(pair); // "1.68 kWh · £0.40" wraps as one
      foot = parts(bits);
    }
    this._set(root.querySelector(".ft"), foot);
    const prev = root.querySelector(".prev");
    const next = root.querySelector(".next");
    // Nothing to see before the week of the first wash.
    const firstKey = d?.first_wash ? dayKey(new Date(d.first_wash)) : null;
    const noOlder = !firstKey || firstKey >= days[0];
    if (prev.disabled !== noOlder) prev.disabled = noOlder;
    if (next.disabled !== (this._page === 0)) next.disabled = this._page === 0;

    if (this._sheet) this._paintSheet();
    this._linked();
    this._ink();
  }

  /* The energy and cost of some days' washes: the days as the Energy dashboard counts the plug,
     or, without it, the washes' own added up. */
  _totals(days, list) {
    const d = this._data;
    let kwh = null;
    let cost = null;
    if (d.energy) {
      const used = days.map((k) => num(d.energy.days?.[k])).filter((v) => v !== null);
      if (used.length) kwh = used.reduce((t, v) => t + v, 0);
      const price = this._price();
      if (kwh !== null && price !== null) cost = kwh * price;
    } else {
      const used = list.map((x) => num(x.kwh)).filter((v) => v !== null);
      const costs = list.map((x) => num(x.cost)).filter((v) => v !== null);
      if (used.length) kwh = used.reduce((t, v) => t + v, 0);
      if (costs.length) cost = costs.reduce((t, v) => t + v, 0);
    }
    return { kwh, cost };
  }

  /* The push's link: open that wash once this week's washes are in, and take ?wash= off the
     address, so a reload or Back doesn't open it again. The washes are this washing machine's, so
     another machine's card never finds the wash and leaves the link to its own card. */
  _linked() {
    const want = new URLSearchParams(location.search).get(LINK_KEY);
    if (!want || !/^\d+$/.test(want)) return;
    if (this._page !== 0) {
      this._page = 0; // the link is to a recent wash: back to this week, then look
      this._render();
      return;
    }
    const d = this._data;
    if (!d || d.days?.[6] !== this._days()[6]) return; // this week's washes aren't in yet
    const id = Number(want);
    if (!(d.washes || []).some((w) => Math.floor(Date.parse(w.end) / 1000) === id)) return;
    const url = new URL(location.href);
    url.searchParams.delete(LINK_KEY);
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    this._openWash(id);
    this._seen(id);
  }

  /* Opening the wash from its push or its Live Activity is seeing it: the integration ends the Live
     Activity on the phones. Nothing else depends on the answer; a failure (an older integration
     without the command, a dropped connection) leaves it to end when Finished does. */
  _seen(id) {
    this._hass
      .callWS({ type: `${WMC_DOMAIN}/wash_seen`, entity_id: this._config.entity, wash: id })
      .catch((err) => console.debug("washing-machine-card: the Live Activity stays until Finished ends", err));
  }

  /* Once per frame after a render: the line under the name starts where the name's first letter
     does, and the footer on the device icon's left edge; in the day sheet, the date starts under
     the day, and each wash's numbers under its times. */
  _ink() {
    if (this._inkQueued) return;
    this._inkQueued = true;
    requestAnimationFrame(() => {
      this._inkQueued = false;
      const root = this.shadowRoot;
      if (!root) return;
      const nm = root.querySelector(".nm");
      inkAlign(nm, [root.querySelector(".sc")]);
      inkToEdge(root.querySelector(".ft"), root.querySelector(".shape"), ROUND_EDGE_INSET);
      const sheet = this._sheet?.root;
      if (sheet) {
        inkAlign(sheet.querySelector(".title"), [sheet.querySelector(".sub")]);
        for (const lab of sheet.querySelectorAll(".lab")) inkToEdge(lab, sheet.querySelector(".row .glyph"), ROUND_EDGE_INSET);
        for (const w of sheet.querySelectorAll(".wash")) inkAlign(w.querySelector(".wp"), [w.querySelector(".ws")]);
      }
    });
  }

  /* ---- the day sheet ---- */

  _openDay(k, opener) {
    if (!k || this._sheet) return;
    const pop = openPopup(
      "wmc-day-sheet",
      `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="t" aria-describedby="d">
        <div class="row"><div class="glyph"><ha-icon></ha-icon></div>
          <div class="hd"><div class="title" id="t"></div><div class="sub" id="d"></div></div></div>
        <div class="tiles"></div>
        <div class="list"></div>
        <div class="buttons"><button class="close"><span class="glyph"><ha-icon icon="mdi:close"></ha-icon></span>Close</button></div></div>`,
      { focus: ".close", opener, onClose: () => (this._sheet = null) },
    );
    pop.root.querySelector(".close").addEventListener("click", () => pop.close(false));
    this._sheet = { ...pop, day: k };
    this._paintDay();
    this._ink();
  }

  _paintSheet() {
    if (this._sheet.day) this._paintDay();
    else this._paintWash();
  }

  /* ---- the wash sheet: where the "washing done" push leads ---- */

  _openWash(id) {
    this._sheet?.close();
    const pop = openPopup(
      "wmc-wash-sheet",
      `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="t" aria-describedby="d">
        <div class="row"><div class="glyph"><ha-icon></ha-icon></div>
          <div class="hd"><div class="title" id="t"></div><div class="sub" id="d"></div></div></div>
        <div class="lab">This wash</div><div class="tiles wash-tiles"></div>
        <div class="lab">Last 7 days</div><div class="tiles week-tiles"></div>
        <div class="buttons"><button class="close"><span class="glyph"><ha-icon icon="mdi:close"></ha-icon></span>Close</button></div></div>`,
      { focus: ".close", onClose: () => (this._sheet = null) },
    );
    pop.root.querySelector(".close").addEventListener("click", () => pop.close(false));
    this._sheet = { ...pop, wash: id };
    this._paintWash();
    this._ink();
  }

  /* Washing done: what to do while it's still the wash waiting in the machine (the card shows
     Finished), then this wash in numbers and the last 7 days', as the card's footer counts them. */
  _paintWash() {
    const { root, wash: id } = this._sheet;
    const d = this._data;
    const w = (d?.washes || []).find((x) => Math.floor(Date.parse(x.end) / 1000) === id);
    if (!w) return; // only while this week's washes are loaded; what is shown stays
    const tz = this._tz();
    const time = timeFn(tz);
    const dayKey = dayKeyFn(tz);
    const cur = this._currency();
    const m = this._m;
    const end = Date.parse(w.end);

    const glyph = root.querySelector(".row .glyph");
    const gbg = tint(C.green, 20);
    if (glyph.style.background !== gbg) glyph.style.background = gbg;
    const gi = glyph.querySelector("ha-icon");
    if (gi.getAttribute("icon") !== m.icon) gi.setAttribute("icon", m.icon);
    if (gi.style.color !== C.green) gi.style.color = C.green;

    const latest = Math.max(...d.washes.map((x) => Date.parse(x.end)));
    const waiting = m.key === "finished" && end === latest;
    const sub = waiting ? ["Unload the clothes", `finished ${time(new Date(end))}`] : [`Finished ${since(end, tz)}`];
    const tile = ([l, v]) => `<div class="tile"><span>${esc(l)}</span><b>${esc(v)}</b></div>`;
    const kwh = num(w.kwh);
    const cost = num(w.cost);
    const one = [
      ["Took", num(w.minutes) !== null ? dur(num(w.minutes)) : "–"],
      ["Energy", kwh !== null ? kwhText(kwh) : "–"],
      ["Cost", cost !== null && cur ? costText(cost, cur) : "–"],
    ];
    const inWeek = d.washes.filter((x) => d.days.includes(dayKey(new Date(x.end))));
    const t = this._totals(d.days, inWeek);
    const week = [
      ["Washes", fmt(inWeek.length)],
      ["Energy", t.kwh !== null ? kwhText(t.kwh) : "–"],
      ["Cost", t.cost !== null && cur ? costText(t.cost, cur) : "–"],
    ];
    this._set(root.querySelector(".title"), "Washing done");
    this._set(root.querySelector(".sub"), parts(sub));
    this._set(root.querySelector(".wash-tiles"), one.map(tile).join(""));
    this._set(root.querySelector(".week-tiles"), week.map(tile).join(""));
  }

  _paintDay() {
    const { root, day: k } = this._sheet;
    const d = this._data;
    const tz = this._tz();
    const time = timeFn(tz);
    const dayKey = dayKeyFn(tz);
    const cur = this._currency();
    const m = this._m;
    const washes = (d?.washes || []).filter((w) => dayKey(new Date(w.end)) === k).sort((a, b) => Date.parse(a.end) - Date.parse(b.end));
    const isToday = k === dayKey(new Date());
    const running = isToday && m.key === "washing";
    const st = this._hass.states[d?.entities?.status || this._config.entity];

    // The icons carry the card's colours: orange while a wash runs (power), green for washes that
    // finished; the day's icon green with any, grey for a day without washes.
    const c = running ? C.orange : washes.length ? C.green : C.grey;
    const glyph = root.querySelector(".row .glyph");
    const gbg = tint(c, 20);
    if (glyph.style.background !== gbg) glyph.style.background = gbg;
    const gi = glyph.querySelector("ha-icon");
    if (gi.getAttribute("icon") !== m.icon) gi.setAttribute("icon", m.icon);
    if (gi.style.color !== c) gi.style.color = c;

    const rows = washes.map((w) => {
      const pair = [];
      if (num(w.kwh) !== null) pair.push(kwhText(num(w.kwh)));
      const cost = num(w.cost) !== null ? costText(num(w.cost), cur) : null;
      if (cost) pair.push(cost);
      const bits = [dur(num(w.minutes) ?? 0)];
      if (pair.length) bits.push(pair);
      return `<div class="wash"><span class="glyph" style="background:${tint(C.green, 20)}"><ha-icon icon="${esc(m.icon)}" style="color:${C.green}"></ha-icon></span>
        <div><div class="wp">${esc(time(new Date(w.start)))} – ${esc(time(new Date(w.end)))}</div>
        <div class="ws">${parts(bits)}</div></div></div>`;
    });
    if (running) {
      const started = Date.parse(st?.attributes?.started || d?.current?.started || "");
      if (Number.isFinite(started))
        rows.push(`<div class="wash now"><span class="glyph" style="background:${tint(C.orange, 20)}"><ha-icon icon="${esc(m.icon)}" style="color:${C.orange}"></ha-icon></span>
          <div><div class="wp">${esc(time(new Date(started)))} – now</div>
          <div class="ws">${parts(["Washing", `${dur((Date.now() - started) / 60000)} so far`])}</div></div></div>`);
    }

    // Under the title: the date and the count. Then the day in six tiles: its finished washes, their
    // time and average; the energy the Energy dashboard counted that day and its cost (without the
    // dashboard, the washes' own added up); and the average wash's own cost, in pence.
    const n = washes.length;
    const sub = [fmtKey(k, { day: "numeric", month: "long", year: "numeric" })];
    if (n) sub.push(washesText(n));
    if (running) sub.push(n ? "washing now" : "Washing now");
    else if (!n) sub.push("No washes");
    const price = this._price();
    const timed = washes.filter((w) => num(w.minutes) !== null);
    const minutes = timed.reduce((t, w) => t + num(w.minutes), 0);
    const own = (key) => washes.map((w) => num(w[key])).filter((v) => v !== null);
    const board = d?.energy ? num(d.energy.days?.[k]) : null;
    let kwh = board;
    let cost = board !== null && price !== null ? board * price : null;
    if (!d?.energy) {
      kwh = own("kwh").length ? own("kwh").reduce((t, v) => t + v, 0) : null;
      cost = own("cost").length ? own("cost").reduce((t, v) => t + v, 0) : null;
    }
    const costs = own("cost");
    const tiles = [
      ["Washes", fmt(n)],
      ["Time washing", timed.length ? dur(minutes) : "–"],
      ["Average", timed.length ? dur(minutes / timed.length) : "–"],
      ["Energy", kwh !== null ? kwhText(kwh) : "–"],
      ["Cost", cost !== null && cur ? costText(cost, cur) : "–"],
      ["Per wash", costs.length && cur ? costText(costs.reduce((t, v) => t + v, 0) / costs.length, cur) : "–"],
    ];
    this._set(root.querySelector(".title"), esc(isToday ? "Today" : fmtKey(k, { weekday: "long" })));
    this._set(root.querySelector(".sub"), parts(sub));
    this._set(root.querySelector(".tiles"), tiles.map(([l, v]) => `<div class="tile"><span>${esc(l)}</span><b>${esc(v)}</b></div>`).join(""));
    this._set(root.querySelector(".list"), rows.join(""));
  }
}

async function registerWashingMachineCard() {
  await window.customElements.whenDefined("home-assistant");
  if (window.customElements.get(WMC_TAG)) return;
  window.customElements.define(WMC_TAG, WashingMachineCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: WMC_TAG,
    name: "Washing machine",
    description: "Washing now, today's washes, energy and cost, and washes per day for the last 7 days, from Washing Machine Monitor.",
  });
  console.info(`%c WASHING-MACHINE-CARD %c ${WMC_VERSION} `, "background:#ff9800;color:#fff", "");
}

registerWashingMachineCard();
