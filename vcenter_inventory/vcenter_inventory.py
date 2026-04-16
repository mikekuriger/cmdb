#!/usr/bin/env python3
"""
vcenter_inventory.py  –  Generate a CSV inventory of all VMs from one or more
vCenter instances via govc.

Each vCenter is defined by a shell environment file (e.g. ~/vcenter/ev3) that
exports GOVC_URL, GOVC_USERNAME, GOVC_PASSWORD, and optionally GOVC_INSECURE.
The script sources each file, discovers the datacenter, and collects all VMs.

Columns:
  name, power_state, guest_os, os_category, ip_address, hostname,
  vcenter_path, vcenter_url, datacenter, cpus, memory_gb,
  VI.ENV, VI.LANDSCAPE, VI.OWNER, VI.PURPOSE, VI.TIER,
  App_Name, Description, Owner, deployment, cmdb_uuid,
  tags

Usage:
  python3 vcenter_inventory.py \\
      --env ~/vcenter/na1 ~/vcenter/ev3 \\
      --output inventory.csv \\
      --workers 30
"""

import subprocess, json, csv, sys, argparse, os, re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

# Friendly names we care about — matched by name, not key ID, so IDs can vary per vCenter
TARGET_FIELDS = {
    "VI.ENV", "VI.LANDSCAPE", "VI.OWNER", "VI.PURPOSE", "VI.TIER",
    "App_Name", "Description", "Owner", "deployment", "cmdb_uuid",
}

CSV_FIELDS = [
    "name", "moref", "is_template", "power_state", "guest_os", "os_category",
    "ip_address", "hostname", "vcenter_path", "vcenter_url", "datacenter",
    "cpus", "memory_gb",
    "VI.ENV", "VI.LANDSCAPE", "VI.OWNER", "VI.PURPOSE", "VI.TIER",
    "App_Name", "Description", "Owner", "deployment", "cmdb_uuid",
    "tags",
]


def load_env_file(path):
    """Parse a shell env file that uses 'export KEY=VALUE' and return a dict."""
    env = os.environ.copy()
    path = os.path.expanduser(path)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                m = re.match(r"^export\s+(\w+)=['\"]?([^'\"#\n]*)['\"]?", line)
                if m:
                    env[m.group(1)] = m.group(2)
    except FileNotFoundError:
        print(f"[!] Env file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return env


def govc_run(env, *args):
    result = subprocess.run(
        ["govc"] + list(args),
        capture_output=True, text=True, env=env
    )
    return result.stdout.strip(), result.stderr.strip()


def govc_json(env, *args):
    stdout, _ = govc_run(env, *args)
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def get_datacenter(env):
    """Return the first datacenter path found in this vCenter."""
    stdout, _ = govc_run(env, "ls", "/")
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    return lines[0] if lines else None


def build_field_map(env):
    """
    Build {key_id (int): friendly_name} for fields we care about by running
    govc fields.ls and matching names to TARGET_FIELDS.
    """
    stdout, _ = govc_run(env, "fields.ls")
    field_map = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                key_id = int(parts[0])
            except ValueError:
                continue
            name = parts[1]
            if name in TARGET_FIELDS:
                field_map[key_id] = name
    return field_map


def categorize_os(guest_full_name):
    if not guest_full_name:
        return "other"
    n = guest_full_name.lower()
    if "windows" in n:
        return "windows"
    if any(x in n for x in ["linux", "centos", "redhat", "oracle", "ubuntu",
                              "debian", "suse", "amazon", "fedora", "rocky",
                              "alma", "photon"]):
        return "linux"
    return "other"


def get_vm_tags(env, vm_path):
    stdout, _ = govc_run(env, "tags.attached.ls", vm_path)
    tags = [t.strip() for t in stdout.splitlines() if t.strip()]
    return "|".join(tags)


def process_vm(env, field_map, vcenter_url, datacenter, vm_path):
    data = govc_json(env, "vm.info", "-json", vm_path)
    if not data or not data.get("virtualMachines"):
        return None

    v     = data["virtualMachines"][0]
    cfg   = v.get("config") or {}
    rt    = v.get("runtime") or {}
    guest = v.get("guest") or {}
    hw    = cfg.get("hardware") or {}

    row = {f: "" for f in CSV_FIELDS}
    row["name"]         = cfg.get("name", "")
    row["moref"]        = (v.get("self") or {}).get("value", "")
    row["is_template"]  = "1" if cfg.get("template") else "0"
    row["power_state"]  = rt.get("powerState", "")
    row["guest_os"]     = guest.get("guestFullName") or cfg.get("guestFullName", "")
    row["os_category"]  = categorize_os(row["guest_os"])
    row["ip_address"]   = guest.get("ipAddress", "")
    row["hostname"]     = guest.get("hostName", "")
    row["vcenter_path"] = vm_path
    row["vcenter_url"]  = vcenter_url
    row["datacenter"]   = datacenter
    row["cpus"]         = hw.get("numCPU", "")
    row["memory_gb"]    = round(hw.get("memoryMB", 0) / 1024, 1) if hw.get("memoryMB") else ""

    for cv in v.get("customValue") or []:
        key_id = cv.get("key")
        value  = cv.get("value", "")
        fname  = field_map.get(key_id)
        if fname:
            row[fname] = value

    row["tags"] = get_vm_tags(env, vm_path)
    return row


def collect_vcenter(env_file, workers):
    print(f"\n[*] Loading env: {env_file}", flush=True)
    env = load_env_file(env_file)
    vcenter_url = env.get("GOVC_URL", env_file)

    datacenter = get_datacenter(env)
    if not datacenter:
        print(f"[!] Could not find datacenter for {vcenter_url}", file=sys.stderr)
        return []
    print(f"    Datacenter : {datacenter}", flush=True)

    field_map = build_field_map(env)
    print(f"    Field map  : { {v: k for k, v in field_map.items()} }", flush=True)

    print(f"[*] Discovering VMs...", flush=True)
    stdout, err = govc_run(env, "find", datacenter + "/vm", "-type", "m")
    if not stdout:
        print(f"[!] No VMs found. Error: {err}", file=sys.stderr)
        return []

    vm_paths = [p for p in stdout.splitlines() if p.strip()]
    print(f"[*] Found {len(vm_paths)} VMs. Collecting with {workers} workers...", flush=True)

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(process_vm, env, field_map, vcenter_url, datacenter, p): p
            for p in vm_paths
        }
        for f in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"    {done}/{len(vm_paths)} processed...", flush=True)
            row = f.result()
            if row:
                rows.append(row)

    return rows


def main():
    parser = argparse.ArgumentParser(description="Generate multi-vCenter VM inventory CSV")
    parser.add_argument("--env", nargs="+",
                        default=["~/vcenter/na1", "~/vcenter/ev3"],
                        help="One or more vCenter env files (e.g. ~/vcenter/na1 ~/vcenter/ev3)")
    parser.add_argument("--output",  default="vcenter_inventory.csv", help="Output CSV file")
    parser.add_argument("--workers", type=int, default=30,            help="Parallel workers per vCenter")
    args = parser.parse_args()

    all_rows = []
    for env_file in args.env:
        rows = collect_vcenter(env_file, args.workers)
        all_rows.extend(rows)

    # Sort: datacenter, os_category, name
    all_rows.sort(key=lambda r: (r["datacenter"], r["os_category"], r["name"]))

    with open(args.output, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    # Summary
    print(f"\n[+] Done. {len(all_rows)} total VMs written to {args.output}")
    by_dc = Counter((r["datacenter"], r["os_category"]) for r in all_rows)
    for (dc, cat), count in sorted(by_dc.items()):
        print(f"    {dc:35s}  {cat:10s}: {count}")


if __name__ == "__main__":
    main()
