"""CLI VIGIE — discover, taxonomy."""

import asyncio
import json
from pathlib import Path

import typer

app = typer.Typer(help="VIGIE — Agent d'observabilité branchable")


@app.command()
def discover(
    target: str = typer.Argument(..., help="Chemin hôte ou conteneur à scanner"),
    tenant: str = typer.Option("default", "--tenant", "-t"),
    output: Path | None = typer.Option(None, "--output", "-o"),
    existing: Path | None = typer.Option(None, "--existing"),
):
    """Scan read-only + génération vector.toml proposé."""
    from agent.services.discovery import run_discovery

    result = asyncio.run(run_discovery(target, tenant_id=tenant, existing_config=existing))
    typer.echo(json.dumps(result["report"], ensure_ascii=False, indent=2))
    typer.echo("\n--- Config proposée ---\n")
    typer.echo(result["proposed_config"])
    if output:
        output.write_text(result["proposed_config"], encoding="utf-8")
        typer.echo(f"\nÉcrit: {output}")


@app.command("taxonomy")
def taxonomy_cmd(
    action: str = typer.Argument(..., help="propose|validate|apply|diff"),
    tenant: str = typer.Option("default", "--tenant", "-t"),
):
    """Gestion taxonomie métier."""
    from agent.services import taxonomy as tax

    if action == "propose":
        result = asyncio.run(tax.propose_taxonomy(tenant))
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    elif action == "validate":
        typer.echo(json.dumps(tax.validate_taxonomy(tenant), indent=2))
    elif action == "apply":
        vrl = tax.apply_taxonomy(tenant)
        typer.echo("Taxonomie appliquée. VRL généré:\n")
        typer.echo(vrl)
    elif action == "diff":
        typer.echo(tax.diff_taxonomy(tenant))
    else:
        typer.echo("Actions: propose, validate, apply, diff")
        raise typer.Exit(1)


def main():
    app()


if __name__ == "__main__":
    main()
