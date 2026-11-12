"""Constants for the Incus integration."""
DOMAIN = "incus"

CONF_CONNECTION = "connection"
CONN_HTTPS = "https"
CONN_UNIX = "unix"
CONF_URL = "url"
CONF_SOCKET = "socket_path"
CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"
CONF_SERVER_CERT = "server_cert"
CONF_VERIFY = "verify"          # pin | system | skip
VERIFY_PIN, VERIFY_SYSTEM, VERIFY_SKIP = "pin", "system", "skip"
CONF_TRUST_TOKEN = "trust_token"
CONF_CLIENT_NAME = "client_name"

CONF_PROJECTS = "projects"                      # comma separated project names, or "*" for all
CONF_SCAN_INTERVAL = "scan_interval"            # seconds, instances
CONF_VOLUME_SCAN_INTERVAL = "volume_scan_interval"  # seconds, storage

DEFAULT_PROJECTS = "default"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
DEFAULT_VOLUME_SCAN_INTERVAL = 300
MIN_VOLUME_SCAN_INTERVAL = 60
DEFAULT_SOCKET = "/var/lib/incus/unix.socket"
DEFAULT_CLIENT_NAME = "home-assistant"
REQUEST_TIMEOUT = 30

ATTR_PROJECT = "project"
ATTR_INSTANCE = "instance"
ATTR_FORCE = "force"
ATTR_TIMEOUT = "timeout"
ATTR_CONFIG_ENTRY = "config_entry"

SERVICE_START = "start_instance"
SERVICE_STOP = "stop_instance"
SERVICE_RESTART = "restart_instance"
SERVICE_FREEZE = "freeze_instance"
SERVICE_UNFREEZE = "unfreeze_instance"
SERVICE_ACTIONS = {SERVICE_START: "start", SERVICE_STOP: "stop", SERVICE_RESTART: "restart",
                   SERVICE_FREEZE: "freeze", SERVICE_UNFREEZE: "unfreeze"}
DEFAULT_ACTION_TIMEOUT = 30

# ---- inventory (change detection + choosing what to monitor) ---------------------------------
CONF_NOTIFY_CHANGES = "notify_changes"
DEFAULT_NOTIFY_CHANGES = True
CONF_EXCLUDED_INSTANCES = "excluded_instances"   # option: instance keys "project/name" the user switched off
CONF_EXCLUDED_POOLS = "excluded_pools"
CONF_EXCLUDED_VOLUMES = "excluded_volumes"       # volume keys "pool/project/name"
CONF_REVIEWED = "reviewed"                       # inventory the user last saw in the options flow (to flag "(new)")
CONF_MONITOR_INSTANCES = "monitored_instances"   # options-flow form fields (checked = monitored)
CONF_MONITOR_POOLS = "monitored_pools"
CONF_MONITOR_VOLUMES = "monitored_volumes"
EVENT_INVENTORY_CHANGED = "incus_inventory_changed"
SERVICE_RESCAN = "rescan_inventory"
