# Incus integration for Home Assistant (HACS custom integration)

`custom_components/incus` — polls an Incus server with the vendored Python client (`incus_client/`, from the sibling `incus-client-py` repo),
exposes sensors/binary sensors/buttons/switch per instance, storage pool + custom volume sensors, and start/stop/restart/freeze services.
Min HA version 2025.1 (uses `entry.runtime_data`, `DataUpdateCoordinator(config_entry=...)`, options flow with auto `config_entry`).

## Diagnostics
`tools/diagnose.py` runs the integration's connection steps outside HA (also usable on the HA host when copied next to `custom_components/`). Config-flow errors are logged by
`client_factory` (logger `custom_components.incus.client_factory`) and the reason is shown in the form via the `{error}` placeholder in `strings.json`.

## Commands
- Tests: `python3 -W error::ResourceWarning -m unittest discover -s tests -t .` (no HA, no Incus needed).
- Refresh vendored client: `tools/vendor.sh ../incus-client-py` (never edit `custom_components/incus/incus_client/` by hand; fix it in the client repo, run its tests, re-vendor).

## Layout (custom_components/incus)
- HA-INDEPENDENT (importable and tested without Home Assistant): `inventory.py` (Inventory, InventoryTracker, change descriptions, labels), `data.py` (snapshots, CPU % tracker, volume/pool builders), `fetch.py` (blocking
  Incus calls), `commands.py` (power actions), `client_factory.py` (build client from entry data, trust-token registration, validation), `const.py`.
- HA glue: `__init__.py` (setup, services incl. rescan, device pruning), `inventory_monitor.py` (diff each poll vs persisted baseline in a `Store`, fire `incus_inventory_changed`, persistent notification), `coordinator.py`, `entity.py` (base entities + `async_add_dynamic`), `sensor.py`,
  `binary_sensor.py`, `button.py`, `switch.py`, `config_flow.py`, `services.yaml`, `strings.json` (= `translations/en.json`, keep identical).
- All blocking I/O runs in the executor (`hass.async_add_executor_job`); the client opens a fresh connection per request, so it is thread-safe.

## Inventory / exclusions
- Options hold `excluded_instances|pools|volumes` (keys: `project/name`, `pool`, `pool/project/name`), `reviewed` (inventory last seen in the options flow, used for the "(new)" flag) and `notify_changes`.
- Coordinators fetch everything but only expose non-excluded items; `discovered*` fields carry the full inventory so changes to excluded items are still noticed. Excluded volumes/pools cost no usage requests.
- Server-level counters count all instances (excluded ones too). Saving the options reloads the entry (update listener), which rebuilds entities; `_prune` removes stale devices.
- The options flow is a menu: `settings` (merges into existing options - never replace them, or exclusions are lost) and `inventory` (scan via `fetch.scan_inventory`, checklist of SelectSelectors).

## Rules
- Put logic in the HA-independent modules and keep entities thin (`value_fn(snapshot)` descriptions).
- New entities: add a description + `value_fn`; dynamic ones (per disk/NIC/volume) are discovered through `async_add_dynamic` factories that yield `(unique_id, builder)`.
  Unique IDs must match between the factory and the entity base class (`<entry>_<key>_<entity_key>`); `test_unique_ids_are_unique` guards this.
- `DescribedMixin` must NOT define `_snapshot` (it sits first in the MRO and would shadow the entity base's) — a bug caught by the tests once already.
- Keep `strings.json` and `translations/en.json` identical; every config-flow step/error/selector option needs a string.

## What is NOT verified
- **Never run inside a real Home Assistant.** `tests/ha_stubs.py` is a minimal imitation of the HA APIs used; passing tests prove the logic, not that real
  HA signatures match (DataUpdateCoordinator args, OptionsFlow.config_entry, menu `description_placeholders`, selector classes, DeviceInfo/via_device ordering,
  `async_forward_entry_setups`, service `target` resolution). First task: install in a dev HA and fix whatever differs. Run hassfest + HACS validation (CI workflow included).
- Not run against a real Incus server (mock only). Field names come from my memory of the Go API: `StorageVolumeState.usage.{used,total}`,
  `ResourcesStoragePool.space.{used,total}`, `CertificatesPost.trust_token` (trust-token registration), instance `state.*`. Verify against a live server.
- `cpu.allocated_time` meaning is unclear (exposed only as an attribute). CPU % = delta(cpu usage ns) / delta(wall ns) of the poll interval.
- The `OWNER` placeholders in `manifest.json` / README must be replaced with the real GitHub repository.

## TODO
1. Smoke test in real HA with a real Incus (HTTPS token flow, unix socket, start/stop buttons, volumes).
2. Re-auth flow, diagnostics, entity translations (`translation_key`) and icons/brand assets, `pytest-homeassistant-custom-component` tests.
3. Optional: use the events websocket for instant state changes; per-instance snapshot/backup info; VM agent `os_info` sensors.
