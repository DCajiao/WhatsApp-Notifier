from pathlib import Path


def test_dockerfile_gunicorn_timeout_exceeds_zernio_timeout():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "--timeout" in dockerfile
    assert "90" in dockerfile
    assert "--workers" in dockerfile
    assert "--threads" in dockerfile
    assert "--access-logfile" in dockerfile
