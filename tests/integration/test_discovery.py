from discovery.scanner import discover_target


def test_discover_symfony_lab(tmp_path):
    target = tmp_path / "symfony"
    (target / "var" / "log").mkdir(parents=True)
    (target / "var" / "log" / "app.log").write_text(
        '{"level":"info","message":"commande créée"}\n', encoding="utf-8"
    )
    report = discover_target(str(target), sample=True)
    assert len(report.log_sources) >= 1


def test_discover_laravel_lab(tmp_path):
    target = tmp_path / "laravel"
    (target / "storage" / "logs").mkdir(parents=True)
    (target / "storage" / "logs" / "laravel.log").write_text("Order created\n", encoding="utf-8")
    report = discover_target(str(target))
    assert report.log_sources


def test_discover_node_lab(tmp_path):
    target = tmp_path / "node"
    (target / "logs").mkdir(parents=True)
    (target / "logs" / "app.log").write_text('{"msg":"payment"}\n', encoding="utf-8")
    report = discover_target(str(target))
    assert report.log_sources
