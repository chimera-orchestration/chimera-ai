from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from chimera.services import DockerService, ProcessService, Service, TmuxService

STARTED = datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)

adapter: TypeAdapter[Service] = TypeAdapter(Service)


def test_tmux_service_roundtrip() -> None:
    svc = TmuxService(
        type="tmux",
        name="my-tmux",
        use="agent",
        ports={"http": 8080},
        started_at=STARTED,
        session="my-session",
    )
    data = svc.model_dump()
    restored = adapter.validate_python(data)
    assert isinstance(restored, TmuxService)
    assert restored == svc


def test_docker_service_roundtrip() -> None:
    svc = DockerService(
        type="docker",
        name="dolt",
        use="dolt",
        ports={"mysql": 3306},
        started_at=STARTED,
        container_id="abc123def456",
        container_name="dolt-server",
    )
    data = svc.model_dump()
    restored = adapter.validate_python(data)
    assert isinstance(restored, DockerService)
    assert restored == svc


def test_process_service_roundtrip() -> None:
    svc = ProcessService(
        type="process",
        name="my-proc",
        use="beads",
        ports={},
        started_at=STARTED,
        pid=12345,
        cmd="beads serve",
    )
    data = svc.model_dump()
    restored = adapter.validate_python(data)
    assert isinstance(restored, ProcessService)
    assert restored == svc


def test_discriminator_selects_correct_type() -> None:
    tmux_data = {
        "type": "tmux",
        "name": "x",
        "use": "y",
        "ports": {},
        "started_at": STARTED,
        "session": "s",
    }
    docker_data = {
        "type": "docker",
        "name": "x",
        "use": "y",
        "ports": {},
        "started_at": STARTED,
        "container_id": "abc123def456",
        "container_name": "c",
    }
    process_data = {
        "type": "process",
        "name": "x",
        "use": "y",
        "ports": {},
        "started_at": STARTED,
        "pid": 1,
        "cmd": "ls",
    }

    assert isinstance(adapter.validate_python(tmux_data), TmuxService)
    assert isinstance(adapter.validate_python(docker_data), DockerService)
    assert isinstance(adapter.validate_python(process_data), ProcessService)


def test_unknown_type_raises() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"type": "unknown", "name": "x", "use": "y", "ports": {}, "started_at": STARTED}
        )


def test_missing_type_raises() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python({"name": "x", "use": "y", "ports": {}, "started_at": STARTED})


def test_missing_type_specific_field_raises() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"type": "tmux", "name": "x", "use": "y", "ports": {}, "started_at": STARTED}
        )


def test_json_serialisation_roundtrip() -> None:
    svc = DockerService(
        type="docker",
        name="dolt",
        use="dolt",
        ports={"mysql": 3306},
        started_at=STARTED,
        container_id="abc123def456",
        container_name="dolt-server",
    )
    json_str = svc.model_dump_json()
    restored = adapter.validate_json(json_str)
    assert isinstance(restored, DockerService)
    assert restored == svc
