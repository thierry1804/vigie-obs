"""Service Discovery — inférence LLM + génération vector.toml."""

import difflib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agent.harness.runner import run_agent
from discovery.scanner import DiscoveryReport, discover_target, report_to_json

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"


async def infer_formats(report: DiscoveryReport, tenant_id: str = "default") -> DiscoveryReport:
    last_index = len(report.log_sources) - 1
    prompt = (
        "Voici les sources de logs déjà découvertes, avec un premier échantillon de lignes "
        "pour chacune :\n"
        f"{report_to_json(report)}\n\n"
        f"Pour chaque source (index 0 à {last_index}), détermine son format/framework et "
        "enregistre ta conclusion avec l'outil set_framework_hint. Si les échantillons sont "
        "insuffisants pour conclure, utilise sample_lines pour en obtenir plus avant de conclure."
    )
    await run_agent("discovery", prompt, tenant_id=tenant_id, endpoint="discover", report=report)
    return report


def generate_vector_config(report: DiscoveryReport, tenant_id: str = "default") -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    template = env.get_template("vector.toml.j2")
    sources = []
    for i, src in enumerate(report.log_sources):
        sources.append(
            {
                "name": f"source_{i}",
                "include": [src.glob.replace(str(Path(report.target)), f"/host/{Path(report.target).name}") if report.target in src.glob else src.glob],
                "framework": src.framework_hint,
            }
        )
    if not sources:
        sources = [
            {
                "name": "app_logs",
                "include": [f"/host/{Path(report.target).name}/var/log/*.log"],
                "framework": "symfony",
            }
        ]
    return template.render(sources=sources, tenant_id=tenant_id)


def diff_config(proposed: str, existing_path: Path | None) -> str:
    if not existing_path or not existing_path.exists():
        return proposed
    existing = existing_path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        existing.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile=str(existing_path),
        tofile="proposed_vector.toml",
    )
    return "".join(diff) or "Aucune différence."


async def run_discovery(
    target: str, tenant_id: str = "default", existing_config: Path | None = None
) -> dict:
    report = discover_target(target)
    if report.log_sources:
        report = await infer_formats(report, tenant_id=tenant_id)
    proposed = generate_vector_config(report, tenant_id=tenant_id)
    return {
        "report": report.to_dict(),
        "proposed_config": proposed,
        "diff": diff_config(proposed, existing_config),
    }
