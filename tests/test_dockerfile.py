from pathlib import Path


def test_dockerfile_allows_indefinite_zernio_database_retries():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "--timeout" in dockerfile
    assert '"0"' in dockerfile
    assert "--workers" in dockerfile
    assert "--threads" in dockerfile
    assert "--access-logfile" in dockerfile
