#!/usr/bin/env python3

import argparse
from pathlib import Path
import sys

IGNORE_DIRS = {
    "CAs",
    "standarts",
    "__pycache__",
}

STATIC_MODULES = """
      https_v4_siterm_2xx:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip4"
          tls_config:
            insecure_skip_verify: true
            cert_file: /etc/tls/tls.crt
            key_file: /etc/tls/tls.key
      https_v6_siterm_2xx:
        prober: http
        http:
          fail_if_ssl: false
          fail_if_not_ssl: true
          preferred_ip_protocol: "ip6"
          tls_config:
            insecure_skip_verify: true
            cert_file: /etc/tls/tls.crt
            key_file: /etc/tls/tls.key
"""

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


def is_site_dir(path: Path):
    return path.is_dir() and path.name not in IGNORE_DIRS


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

    output.append(STATIC_MODULES.rstrip())

    for m in modules:
        output.append(m.rstrip())

    output.append(STATIC_TAIL.rstrip())

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Generate Prometheus blackbox exporter config"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Path to rm-configs directory (default: current dir)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: stdout)",
    )

    args = parser.parse_args()

    base_dir = Path(args.path).resolve()

    if not base_dir.exists():
        print(f"ERROR: Path does not exist: {base_dir}", file=sys.stderr)
        sys.exit(1)

    yaml_output = build_yaml(base_dir)

    if args.output:
        Path(args.output).write_text(yaml_output + "\n")
    else:
        print(yaml_output)


if __name__ == "__main__":
    main()
