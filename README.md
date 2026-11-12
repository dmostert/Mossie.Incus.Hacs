# Incus for Home Assistant

A HACS **custom integration** (`custom_components/incus`) that monitors an [Incus](https://linuxcontainers.org/incus/) server and
controls its instances. It uses the Python Incus client from this repo (vendored in `custom_components/incus/incus_client`,
standard library only), polls the REST API, and needs no extra Python packages.

> HACS distributes *integrations*, not Supervisor add-ons, so this is an integration: it runs inside Home Assistant and talks to your Incus server over the network (or a Unix socket).

## What you get

**Per instance** (container or VM; one device each, linked to the server device)
| Entity | Notes |
|---|---|
| Status, Running (binary) | status text with project/type/arch/image/profiles/limits as attributes |
| CPU time (s), **CPU usage (%)** | usage is percent of *one* core since the previous poll (can exceed 100) |
| Memory usage, Memory usage percent, Memory peak, Swap usage | percent is relative to the memory limit (or host memory if unlimited) |
| Disk `<device>` usage | one per disk device (e.g. `root`); unknown when the storage driver can't report it |
| `<nic>` received / sent | one pair per network interface (cumulative bytes, loopback excluded), MAC/MTU/addresses as attributes |
| IP address (+ IPv6, disabled by default), Processes, Started (timestamp) | |
| Start / Stop / Restart buttons (+ Force stop, disabled by default), **Power switch** | |

**Storage** (slower poll; only volumes you created - *custom* volumes, not instance or image volumes)
- One device per **storage pool**: used / total / free / used % / number of custom volumes (driver and status as attributes).
- One device per **custom volume**: used, size limit, free, used %, and an *In use* binary sensor. Attributes: pool, project, content type, description, created at, config, and `used_by` (the instances using it).

**Server device**: instance count, running count, version, and a **Rescan inventory** button.

**Inventory changes** (instances, storage pools and custom volumes appearing or disappearing)
- Every poll is compared with the previous inventory, which is saved so changes made while Home Assistant was down are noticed after a restart (the very first run only records a baseline, silently).
- On a change Home Assistant fires the event `incus_inventory_changed` and, unless switched off, creates a persistent notification ("New instances: web, ...").
- New instances/volumes get their entities automatically; deleted ones are removed together with their devices.
- **Configure → Rescan the server and choose what to monitor** reads the server now and shows a checklist of all instances, pools and custom volumes. Anything marked *(new)* appeared since you last reviewed; untick what you don't want to monitor (those entities and devices are removed; the server counters still count everything).

```yaml
# example automation: tell me when a container appears or goes away
triggers:
  - trigger: event
    event_type: incus_inventory_changed
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ trigger.event.data.server }}: +{{ trigger.event.data.added_instances | default([]) | join(', ') }}
        -{{ trigger.event.data.removed_instances | default([]) | join(', ') }}
# keys present only when non-empty: added_/removed_ + instances | pools | volumes ("project/name", "pool/project/name"),
# plus server and entry_id
```
Force a check any time with the *Rescan inventory* button or `incus.rescan_inventory`.

**Services** (`incus.start_instance`, `stop_instance`, `restart_instance`, `freeze_instance`, `unfreeze_instance`)
```yaml
action: incus.stop_instance
target:
  device_id: <device of the instance>     # or entity_id: button.web_stop ...
data:
  force: false
  timeout: 30            # graceful shutdown seconds (stop/restart)

action: incus.restart_instance       # without a target:
data:
  instance: web
  project: default
  # config_entry: <only if you configured several servers>
```
Errors from Incus (for example "The instance is already running") are shown in Home Assistant as the action error.

## Install
1. HACS → ⋮ → *Custom repositories* → add this repository as **Integration** → install **Incus** → restart Home Assistant.
2. Prepare the server (remote access):
   ```sh
   incus config set core.https_address :8443
   incus config trust add home-assistant        # prints a one-time trust token
   ```
3. Settings → Devices & services → *Add integration* → **Incus** → *Remote server* → enter the URL, keep *pin this server's certificate*
   (compare the fingerprint shown with `incus info`), choose *trust token* and paste the token. Home Assistant generates its own client
   certificate and registers it, so no certificate files are needed.
   - Already have a trusted client certificate? Choose *existing client certificate* and paste the PEM cert and key.
   - Home Assistant on the same host as Incus? Choose *Unix socket* (default `/var/lib/incus/unix.socket`); in a container the socket must be mounted into it, and access via the socket is root-equivalent.

**Options** (Configure on the integration): *Projects, update intervals and change notifications* (projects: `default`, a comma-separated list, or `*` for all; instance interval default 30 s, min 10 s; storage interval default 300 s, min 60 s; notifications on/off) and *Rescan the server and choose what to monitor*.

## Security
The client certificate can do whatever you allow it to. Prefer a restricted certificate limited to the projects you monitor
(`incus config trust add home-assistant --restricted --projects default,web`); it must still be allowed to start/stop instances there.
Never use *Do not verify* on untrusted networks.

## Troubleshooting: "Cannot connect to the Incus server"
The form now shows the real reason after the colon, and the same text is logged as a warning. Where to look:
- **UI:** Settings → System → Logs → pick *Home Assistant Core* (or *Show raw logs*); search for `incus`.
- **Files/CLI:** `/config/home-assistant.log`, `ha core logs` (HA OS, over SSH) or `docker logs homeassistant`.
- **Debug logging** (add to `configuration.yaml`, then restart; shows every request and error the client makes):
  ```yaml
  logger:
    default: warning
    logs:
      custom_components.incus: debug
  ```
  Once the integration exists you can also use its page → ⋮ → *Enable debug logging*.

Typical reasons:
| Message | Meaning / fix |
|---|---|
| `ConnectionRefusedError` | Incus isn't listening on the network: `incus config set core.https_address :8443` |
| `TimeoutError` / `timed out` | Firewall or wrong address; HA must be able to reach `host:8443` (a container/VM may use a different network) |
| `gaierror` / `Name or service not known` | Host name doesn't resolve from Home Assistant: use the IP address |
| `Only https URLs are supported` | Use `https://host:8443` |
| `No such file` / `FileNotFoundError` (socket) | The socket path doesn't exist **inside** Home Assistant's container; mount it or use HTTPS |
| `PermissionError` (socket) | The socket belongs to `root:incus-admin` |
| `The server certificate doesn't match the pinned certificate` | The server certificate changed; remove and re-add the integration (or compare with `incus info`) |
| `SSLError ... PEM` | The pasted client certificate/key is malformed |
| *not trusted* (`auth=untrusted`) | The server doesn't trust this client yet: `incus config trust add home-assistant` and use the token |

To test outside Home Assistant, copy `tools/diagnose.py` to your HA config directory (next to `custom_components/`) and run it where HA runs:
```sh
docker exec -it homeassistant python3 /config/diagnose.py https://incus-host:8443
docker exec -it homeassistant python3 /config/diagnose.py https://incus-host:8443 --cert /config/c.crt --key /config/c.key
docker exec -it homeassistant python3 /config/diagnose.py --socket /var/lib/incus/unix.socket      # add -v for the debug log
```
It checks DNS, TCP, the TLS handshake (printing the certificate fingerprint), authentication, then lists instances and storage, with a hint for each failure.

## Notes and limits
- Polling, not events: instance state refreshes every `scan_interval` seconds.
- Volume usage needs one request per volume per storage poll, hence the slower default interval. Some drivers (e.g. `dir`) can't report usage; those sensors stay *unknown*.
- Instance disk usage of `-1` from Incus means "unknown" and is shown as unknown.
- Only custom volumes are listed; set the projects option to `*` to include volumes of every project.
- Changes are detected at poll time (default 30 s for instances, 5 min for storage); press *Rescan inventory* for an immediate check.
- Not implemented: re-authentication flow, diagnostics download, snapshots/backups, exec/console.

## Development
```sh
python3 -m unittest discover -s tests -t .        # logic tests + glue tests against a mock Incus (HA is stubbed)
tools/vendor.sh ../incus-client-py                 # refresh the vendored client
```
