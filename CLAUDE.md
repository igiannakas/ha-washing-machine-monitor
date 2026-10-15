# CLAUDE.md – project instructions

## Project

**Washing Machine Monitor** (domain `washing_machine_monitor`, repo
`igiannakas/ha-washing-machine-monitor`): a Home Assistant custom integration (HACS) that turns a
smart plug's power readings into washes (and each wash's energy): running / status, wash counts, the last wash,
running totals for long-term statistics, a `finished` event, a wash log kept in storage, a
"washing done" push, and a bundled dashboard card (`custom:washing-machine-card`).

Washing-machine wording only (washes, washing). Do not generalise it to other appliances.

## Working rules

- Make changes on `main`, in the working tree. **Do not commit or push** unless asked. The owner
  reviews the changes and commits them with GitHub Desktop.
- Before handing over, all tests and the linter must pass:
  `python -m pytest tests/ -q`, `ruff check .` and `npm test`.
- Bump `manifest.json` `version` and the card's `WMC_VERSION` on every change.
- End with a ready-to-use commit title: `feat: …`, `fix: …`, `docs: …` or `refactor: …`.

## Architecture

```
HA-free (tested directly):   HA bridge:
tracker.py, phones.py        monitor.py (store, timers, push, Live Activity) → entity.py → sensor / binary_sensor / event
                             config_flow.py, websocket.py (card data), daily_energy.py,
                             card.py + www/ (the card)
```

- `tracker.py`: detection (threshold, finish delay, shortest wash), the Wash record, log helpers,
  push wording. Build new logic here first.
- `phones.py`: the payloads the phones get (companion app keys): the "washing done" push (icon,
  subtitle, `?wash=<end, Unix s>` link the card opens) and the Live Activity (washing with a
  count-up timer, done, end).
- `monitor.py`: one per entry. Feeds power readings to the tracker, keeps the log and the
  in-progress wash in `.storage/washing_machine_monitor.<entry_id>`, arms the finish,
  "finished" and Live Activity timers, sends the push and the Live Activity (what it shows is
  stored as `live`), notifies entities.
- Entities join the power sensor's device (`entity.device_entry`), so ids read
  `sensor.washing_machine_status` etc. for a device called "Washing machine".
- `websocket.py`: `washing_machine_monitor/washes` {entity_id, start?, end?} → the log for the
  period, the plug's kWh per day, entity ids, price, current wash. The card's only data source
  besides states.
- `daily_energy.py`: the plug's energy per day as the Energy dashboard counts it (its entry under
  Individual devices; daily statistics, today topped up with the meter's latest reading).
- Card rules follow the owner's `homeassistant-custom-cards` house style (Mushroom-matched sizes,
  build DOM once and update in place, no transform on :active, Bubble-style sheets).
