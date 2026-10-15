# Washing Machine Monitor

A Home Assistant integration for a washing machine on a smart plug that reports its power. It works out when a wash starts and finishes, keeps every wash, sends a
"washing done" push to the phones you pick, and comes with its own dashboard card.

## What you get

On the plug's own device page:

| Entity | What it is |
|---|---|
| Running | On from the first watt over the threshold until the wash has finished. |
| Status | Idle, Washing or Finished (Finished for an hour after a wash, then Idle). While washing it carries `started` and `kwh`, the energy so far (live, in 0.01 kWh steps; not kept in the history). |
| Washes today, Washes last 7 days | Finished washes, with their `kwh`, `cost` and `minutes` as attributes. The 7 days are today and the 6 days before it. |
| Last wash | The last wash's energy in kWh, with `started`, `finished`, `minutes` and `cost`. |
| Washes, Wash time | Running totals. Home Assistant keeps their statistics for good, so washes and wash time per day, week or month can be charted for any period (the recorder itself only keeps a few days). |
| Wash | An event entity that fires `finished` with the wash's times, energy and cost, for automations. |

Every wash (start, end, minutes, kWh, cost) is also kept in the integration's own storage, for
good. The card reads it to page back through past weeks.

## How a wash is detected

- A wash **starts** when the plug draws more than the threshold (8 W).
- It **finishes** once the draw has stayed at or under the threshold for the finish delay
  (3 minutes). The drum rests at a few watts during a programme, so a single dip must not end
  the wash. The finish time is when the draw last fell under the threshold, so the push arrives
  one finish delay after the last spin.
- Runs shorter than the shortest wash (10 minutes), such as a cancelled programme, are ignored.
- The defaults come from a real machine's readings over two programmes: off 0 W; on but idle
  2.5–5.5 W; filling with water 7.7–12 W; the drum 12–53 W; spin 60–260 W; heating about 2.1 kW.
  8 W sits above idle (a machine left on, or counting down a delayed start, never starts a wash)
  and under filling (a fill doesn't look like a pause); the longest pause under it within a
  programme was 20 seconds. If your machine idles higher or fills lower, set the threshold
  between the two: the plug's power history while the machine is on but idle, and in the first
  minutes of a programme, shows both.
- **An unavailable plug never ends or splits a wash.** A finish waits for the next real reading:
  still low, and the wash ends when the draw first fell; high again, and it carries on. A plug
  that stays unavailable for 6 hours ends the wash at the last moment it was known to run, without
  a push (when it really finished is unknown).
- A restart or reload mid-wash resumes the same wash. A finish that fell due while Home Assistant
  was down is handled once it has started again, push included. If the machine stopped while
  Home Assistant was down, the end time is the first low reading after it is back (nothing
  records the plug while Home Assistant is down).
- **Energy** is the area under the plug's power readings from the start of the wash to its end,
  added up the way Home Assistant's Integral helper does it: each reading holds until the next.
  Plugs report their power every few seconds, so this places the energy exactly inside the wash
  and it is there the moment the wash ends. An energy meter can't: many plugs report it only
  now and then, so a short wash can end before the meter has counted any of it. (Up to 1.1 the
  energy came from the meter; updating removes that setting.) A reading counts for no more than
  a 16 A socket delivers (3.7 kW), so a glitched one can't inflate a wash. What the machine draws during the finish delay isn't part
  of the wash. While the plug is unavailable, or Home Assistant isn't running, the draw is
  unknown and nothing is counted (a restart costs the minute or so it takes).
- **Cost** is the energy times the price per kWh: your own, or the grid price from
  **Settings → Energy** (a fixed price, or a price entity in any energy unit, e.g. EUR/MWh). Each
  wash keeps the price it finished with. A wash's cost is shown in pence ("6p", under a penny
  "0.5p"), from £1 up in pounds.
- **Energy per day** is what the Energy dashboard counts for the plug: add it under **Settings →
  Energy → Individual devices** (its power sensor as the entry's power, or any energy sensor on
  the plug's device). That is the plug's whole use that day, standby included. Past days are the
  dashboard's own daily figures; today also counts the meter's latest reading straight away.

The wash log is written atomically and never overwritten by a version that can't read it: a log
it can't read fails the setup and is left as it is.

If the plug's power sensor is deleted, renamed outside Home Assistant's registry, or disabled, a
repair says so. A rename through the registry is followed automatically.

## The push

**Washing done** · *Unload the clothes* · *Finished at 14:20 after 1 h 52 min · 0.84 kWh · 20p*
(a wash's cost in pence; from £1 up in pounds), with the washing machine's icon on green in
place of the Home Assistant logo (the companion app's `notification_icon`). Numbers keep their
units on the same line.

Tapping it opens the dashboard path you set (`/lovelace/washing-machine` is suggested; leave it
empty for no link) with the wash in the address (`?wash=<when it finished, in Unix seconds>`).
The card there opens that wash: *Washing done*, *Unload the clothes* while it's still the wash
waiting in the machine, the wash in numbers (took, energy, cost) and the last 7 days' (washes,
energy, cost, as the card's footer counts them). The link then comes off the address, so a
reload doesn't open it again.

Pick the phones in the options. A phone whose notify action no longer exists is skipped with a
warning in the log, and one failing never stops the others.

### Live Activity while washing (optional)

Turn on **Live Activity while washing** for a lock-screen timer on the phones that use the Home
Assistant app (iPhone with iOS 17.2 or later, also in the Dynamic Island; a Live Update on
Android 16). It is a Labs feature of the app and needs Home Assistant 2026.7 or later.

- It appears two minutes into a wash, with the machine drawing power, counting up from the
  start. A door lock or a switch-on surge is over long before, so it never comes and goes for
  nothing; a programme cancelled after that ends it again.
- At the end it turns green (*Washing done · Unload the clothes · 1 h 52 min*, the finish time
  on the right) without an alert of its own: the push is the alert. Tapping it opens the wash.
- It stays until someone opens the wash by tapping the push or the Live Activity (the card
  tells the integration), then goes from every phone. The next wash starting replaces it; turning
  it off or deleting the washing machine ends it too. A restart neither starts it again nor
  leaves it behind. Apple itself ends any Live Activity after 8 hours.
- Only the companion app's notify actions (`mobile_app_…`) get it; any other notify action
  would show every update as a message, so it gets the push only.

## The card

```yaml
type: custom:washing-machine-card
entity: sensor.washing_machine_status
name: Washing Machine
navigation_path: /lovelace/washing-machine
```

A sibling of the appliance card in
[homeassistant-custom-cards](https://github.com/igiannakas/homeassistant-custom-cards), built on
the same design rules:

- **Head row:** the icon circle – orange while washing, green when a wash has finished, grey
  otherwise – then "Washing Machine · Washing" ("Finished", "Idle", "Unavailable"), and
  under it what the wash is doing: "Started 17:31", or the finished wash's length, energy and cost.
- **Power pill** while a wash runs: the plug's watts, "⚡ 196 W" in orange (the watts half of the
  printer and appliance cards' pill). It opens the power sensor. The card doesn't switch the plug.
- **Today's numbers**, split by straight dividers: washes today, how long the wash has been
  running (or when the last one ended), and the plug's energy today and its cost, as the Energy
  dashboard counts them. A plug that isn't on the Energy dashboard: today's washes' energy, a
  running one counted in.
- **One tile per day** for the last 7 days (washes that finished that day, today on the right),
  a heat map in green: stronger with each wash (1, 2, 3 or more), a day without washes neutral.
  Under them the 7 days' totals, starting on the device icon's edge (set a touch inside it, so
  the straight first letter looks level with the round edge); ‹ › page back a week at a
  time. Tap a day for its sheet: the date and the count, six tiles – Washes, Time washing,
  Average; Energy and Cost (from the Energy dashboard) and Per wash – then each wash with its
  times, energy and cost.
- **Every cost** is in pence under a pound ("7p", under a penny "0.5p") and in pounds from £1
  up, as in the push.
- **Tap anywhere else** to open `navigation_path`. Without one, the status opens, and each of
  today's numbers opens its own sensor.
- Only the icon circle is coloured, nothing is cut off, and lines wrap only between their parts.

| Option | What it is |
|---|---|
| `entity` | Any Washing Machine Monitor entity, e.g. its Status sensor. Required. |
| `name`, `icon` | Default: the integration entry's name and `mdi:washing-machine`. |
| `navigation_path` | Where a tap goes. Default: the status's details. |

The card is served by the integration and added to your dashboard resources automatically.

## Install

HACS → Integrations → ⋮ → Custom repositories → `igiannakas/ha-washing-machine-monitor`
(Integration). Install, restart Home Assistant, then **Settings → Devices & services → Add
integration → Washing Machine Monitor**.

## Diagnostics

**Settings → Devices & services → Washing Machine Monitor → ⋮ → Download diagnostics** gives the
settings, the wash in progress, the plug's readings and the latest washes.

## Development

```
pip install pytest-homeassistant-custom-component==0.13.372   # Home Assistant 2026.10.1, Python 3.14
python -m pytest tests/ -q
ruff check .
npm install && npm test      # the card, in jsdom
```

`tracker.py` holds the detection and has no Home Assistant imports; `monitor.py` connects it
to Home Assistant.
