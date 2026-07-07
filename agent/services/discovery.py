"""Service Discovery — inférence LLM + génération vector.toml."""

import difflib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agent.services.llm_client import _mock_enabled, create_message
from agent.services.tokens import record_usage
from discovery.scanner import DiscoveryReport, discover_target, report_to_json

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "config" / "templates"


def infer_formats(report: DiscoveryReport) -> DiscoveryReport:
    prompt = (
        "Analyse ces échantillons de logs et infère pour chaque source : "
        "format (json/texte), champs disponibles, niveau de verbosité.\n"
        f"{report_to_json(report)}"
    )
    response = create_message(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        system="Tu es un expert observabilité. Réponds en JSON structuré.",
        messages=[{"role": "user", "content": prompt}],
    )
    record_usage("default", "discover", "claude-haiku-4-5-20251001", response.usage.input_tokens, response.usage.output_tokens)
    if not _mock_enabled():
        for src in report.log_sources:
            if src.sample_lines and src.sample_lines[0].startswith("{"):
                src.framework_hint = src.framework_hint or "json"
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


def run_discovery(target: str, tenant_id: str = "default", existing_config: Path | None = None) -> dict:
    report = discover_target(target)
    report = infer_formats(report)
    proposed = generate_vector_config(report, tenant_id=tenant_id)
    return {
        "report": report.to_dict(),
        "proposed_config": proposed,
        "diff": diff_config(proposed, existing_config),
    }
