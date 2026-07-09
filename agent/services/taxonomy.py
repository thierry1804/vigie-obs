"""Service taxonomie métier apprise."""

from pathlib import Path

import yaml

from agent.harness.runner import run_agent

TAXONOMY_DIR = Path(__file__).resolve().parents[2] / "config" / "taxonomies"


async def propose_taxonomy(tenant_id: str, days: int = 7) -> dict:
    prompt = (
        f'Explore les logs métier (stream_type="business") des {days} derniers jours '
        f"(hours_back={days * 24}) via l'outil query_loki, puis propose une taxonomie."
    )
    text = await run_agent("taxonomy", prompt, tenant_id=tenant_id, endpoint="taxonomy")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = {"events": [], "raw": text}
    if not isinstance(data, dict) or "events" not in data:
        data = {"events": [], "raw": text}
    path = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    TAXONOMY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return {"tenant_id": tenant_id, "path": str(path), "taxonomy": data}


def validate_taxonomy(tenant_id: str) -> dict:
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    if not proposed.exists():
        return {"valid": False, "error": "Aucune taxonomie proposée."}
    data = yaml.safe_load(proposed.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        return {"valid": False, "error": "Aucun événement défini."}
    for ev in events:
        if not ev.get("name") or not ev.get("patterns"):
            return {"valid": False, "error": f"Événement invalide: {ev}"}
    return {"valid": True, "events_count": len(events)}


def apply_taxonomy(tenant_id: str) -> str:
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    validated = TAXONOMY_DIR / f"{tenant_id}.yaml"
    data = yaml.safe_load(proposed.read_text(encoding="utf-8"))
    validated.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return generate_vrl(data)


def diff_taxonomy(tenant_id: str) -> str:
    import difflib

    current = TAXONOMY_DIR / f"{tenant_id}.yaml"
    proposed = TAXONOMY_DIR / f"{tenant_id}.proposed.yaml"
    if not proposed.exists():
        return "Pas de proposition."
    if not current.exists():
        return proposed.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        current.read_text(encoding="utf-8").splitlines(keepends=True),
        proposed.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile="current",
        tofile="proposed",
    )
    return "".join(diff) or "Identique."


def generate_vrl(taxonomy: dict) -> str:
    lines = [
        "msg = downcase(string!(.message))",
        '.stream_type = "technical"',
        '.business_event_type = "unknown"',
    ]
    for ev in taxonomy.get("events", []):
        name = ev["name"]
        pats = ev.get("patterns", [])
        conds = " || ".join(f'contains(msg, "{p.lower()}")' for p in pats if not p.startswith("("))
        if conds:
            lines.append(f"if {conds} {{")
            lines.append('  .stream_type = "business"')
            lines.append(f'  .business_event_type = "{name}"')
            lines.append("}")
    return "\n".join(lines)


def load_taxonomy(tenant_id: str) -> dict | None:
    path = TAXONOMY_DIR / f"{tenant_id}.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))
