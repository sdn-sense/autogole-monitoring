#!/usr/bin/env python3

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from git import Repo

GIT_URL = "https://github.com/sdn-sense/rm-configs"

IGNORE_DIRS = {
    "CAs",
    "standarts",
    "__pycache__",
}

STATIC_TAIL = """
      https_v4_network_2xx:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip4"
          tls_config:
            insecure_skip_verify: true
            cert_file: /etc/tls/tls.crt
            key_file: /etc/tls/tls.key
      https_v6_network_2xx:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip6"
          tls_config:
            insecure_skip_verify: true
            cert_file: /etc/tls/tls.crt
            key_file: /etc/tls/tls.key
      http_v4_network_2xx:
        prober: http
        http:
          fail_if_ssl: true
          fail_if_not_ssl: false
          preferred_ip_protocol: "ip4"
      http_v6_network_2xx:
        prober: http
        http:
          fail_if_ssl: true
          fail_if_not_ssl: false
          preferred_ip_protocol: "ip6"
      icmp_v4:
        prober: icmp
        icmp:
          preferred_ip_protocol: "ip4"
      icmp_v6:
        prober: icmp
        icmp:
          preferred_ip_protocol: "ip6"
"""


def get_siterm_repo():
    """Clone the rm-configs repo into a fresh temp dir and return its path."""
    dir_path = tempfile.mkdtemp()
    Repo.clone_from(GIT_URL, dir_path)
    return dir_path


def is_site_dir(path: Path):
    return path.is_dir() and not path.name.startswith(".") and path.name not in IGNORE_DIRS


def is_disabled(path: Path):
    return (path / "disabled").exists()


def normalize_name(name: str):
    return name.lower()


def generate_module(site_name: str):
    site = normalize_name(site_name)
    token_file = f"/etc/oidc/oidc-{site}.token"

    return f"""
      v4_{site}:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip4"
          authorization:
            credentials_file: {token_file}
      v6_{site}:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip6"
          authorization:
            credentials_file: {token_file}
"""


def build_yaml(base_dir: Path):
    modules = []

    for entry in sorted(base_dir.iterdir()):
        if not is_site_dir(entry):
            continue

        if is_disabled(entry):
            print(f"Skipping disabled: {entry.name}", file=sys.stderr)
            continue

        modules.append(generate_module(entry.name))

    output = []

    output.append("""apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-blackbox-exporter-config-map
  labels:
    app: prometheus-blackbox-exporter
data:
  blackbox.yaml: |
    modules:""")

    for m in modules:
        output.append(m.rstrip())

    output.append(STATIC_TAIL.rstrip())

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Generate Prometheus blackbox exporter config from the rm-configs repo"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"Path to an existing rm-configs checkout (default: clone {GIT_URL} from GitHub)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: stdout)",
    )

    args = parser.parse_args()

    cloned_dir = None
    if args.path:
        base_dir = Path(args.path).resolve()
        if not base_dir.exists():
            print(f"ERROR: Path does not exist: {base_dir}", file=sys.stderr)
            sys.exit(1)
    else:
        cloned_dir = get_siterm_repo()
        base_dir = Path(cloned_dir)

    try:
        yaml_output = build_yaml(base_dir)
    finally:
        if cloned_dir:
            shutil.rmtree(cloned_dir, ignore_errors=True)

    if args.output:
        Path(args.output).write_text(yaml_output + "\n")
    else:
        print(yaml_output)


if __name__ == "__main__":
    main()
