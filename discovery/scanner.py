"""Scanner read-only pour Discovery."""

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path


LOG_PATH_PATTERNS = [
    "var/log",
    "storage/logs",
    "logs",
    "log",
]

KNOWN_PORTS = {
    80: "http",
    443: "https",
    3306: "mysql",
    5432: "postgresql",
    9000: "php-fpm",
    3000: "node",
}


@dataclass
class LogSource:
    path: str
    glob: str
    framework_hint: str = "unknown"
    sample_lines: list[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    target: str
    log_sources: list[LogSource] = field(default_factory=list)
    open_ports: list[dict] = field(default_factory=list)
    docker_containers: list[str] = field(default_factory=list)
    audit_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _audit(report: DiscoveryReport, action: str) -> None:
    report.audit_actions.append(action)


def _framework_hint(path: Path) -> str:
    parts = [p.lower() for p in path.parts]
    if "symfony" in parts or "var" in parts and "log" in parts:
        return "symfony"
    if "laravel" in parts or "storage" in parts:
        return "laravel"
    if "node_modules" in parts or path.suffix == ".log" and "npm" in str(path):
        return "node"
    return "unknown"


def scan_log_paths(target: Path, report: DiscoveryReport) -> None:
    _audit(report, f"scan_log_paths:{target}")
    if not target.exists():
        return
    if target.is_file() and target.suffix in (".log", ".txt"):
        report.log_sources.append(
            LogSource(path=str(target), glob=str(target), framework_hint=_framework_hint(target))
        )
        return
    for root, dirs, files in os.walk(target):
        root_path = Path(root)
        rel = root_path.relative_to(target) if target != root_path else Path(".")
        rel_str = str(rel).replace("\\", "/")
        for pattern in LOG_PATH_PATTERNS:
            if pattern in rel_str or rel_str.endswith(pattern):
                logs = [f for f in files if f.endswith(".log")]
                if logs:
                    glob = str(root_path / "*.log")
                    report.log_sources.append(
                        LogSource(
                            path=str(root_path),
                            glob=glob,
                            framework_hint=_framework_hint(root_path),
                        )
                    )
                break


def sample_lines(source: LogSource, max_lines: int = 20) -> None:
    path = Path(source.glob.replace("*.log", ""))
    if not path.exists():
        path = Path(source.path)
    if path.is_file():
        files = [path]
    else:
        files = sorted(path.glob("*.log"))[:3]
    lines = []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= max_lines:
                        break
                    lines.append(line.strip()[:500])
        except OSError:
            continue
    source.sample_lines = lines[:max_lines]


def scan_ports(report: DiscoveryReport, host: str = "127.0.0.1") -> None:
    _audit(report, f"scan_ports:{host}")
    for port, name in KNOWN_PORTS.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        try:
            if sock.connect_ex((host, port)) == 0:
                report.open_ports.append({"port": port, "service": name})
        except OSError:
            pass
        finally:
            sock.close()


def scan_docker(report: DiscoveryReport) -> None:
    _audit(report, "scan_docker")
    containers_path = Path("/var/lib/docker/containers")
    try:
        if not containers_path.exists():
            return
        for c in containers_path.iterdir():
            if c.is_dir():
                report.docker_containers.append(c.name[:12])
    except PermissionError:
        _audit(report, "scan_docker:permission_denied")


def discover_target(target: str, sample: bool = True) -> DiscoveryReport:
    path = Path(target).resolve()
    report = DiscoveryReport(target=str(path))
    scan_log_paths(path, report)
    if path == Path("/"):
        scan_log_paths(Path("/var/log"), report)
    scan_ports(report)
    scan_docker(report)
    if sample:
        for src in report.log_sources:
            sample_lines(src)
    return report


def report_to_json(report: DiscoveryReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
