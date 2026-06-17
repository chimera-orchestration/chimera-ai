from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError
from testfixtures import ShouldRaise, compare

from chimera.services import AnyService, DockerService, ProcessService, TmuxService

STARTED = datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)

adapter: TypeAdapter[AnyService] = TypeAdapter(AnyService)


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
    compare(adapter.validate_python(data), expected=svc)


def test_docker_service_roundtrip() -> None:
    svc = DockerService(
        type="docker",
        name="cache",
        use="cache",
        ports={"redis": 6379},
        started_at=STARTED,
        container_id="abc123def456",
        container_name="cache-server",
    )
    data = svc.model_dump()
    compare(adapter.validate_python(data), expected=svc)


def test_process_service_roundtrip() -> None:
    svc = ProcessService(
        type="process",
        name="my-proc",
        use="worker",
        ports={},
        started_at=STARTED,
        pid=12345,
        cmd="python worker.py",
    )
    data = svc.model_dump()
    compare(adapter.validate_python(data), expected=svc)


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

    compare(
        adapter.validate_python(tmux_data),
        expected=TmuxService(
            type="tmux", name="x", use="y", ports={}, started_at=STARTED, session="s"
        ),
    )
    compare(
        adapter.validate_python(docker_data),
        expected=DockerService(
            type="docker",
            name="x",
            use="y",
            ports={},
            started_at=STARTED,
            container_id="abc123def456",
            container_name="c",
        ),
    )
    compare(
        adapter.validate_python(process_data),
        expected=ProcessService(
            type="process", name="x", use="y", ports={}, started_at=STARTED, pid=1, cmd="ls"
        ),
    )


def test_unknown_type_raises() -> None:
    with ShouldRaise(ValidationError):
        adapter.validate_python(
            {"type": "unknown", "name": "x", "use": "y", "ports": {}, "started_at": STARTED}
        )


def test_missing_type_raises() -> None:
    with ShouldRaise(ValidationError):
        adapter.validate_python({"name": "x", "use": "y", "ports": {}, "started_at": STARTED})


def test_missing_type_specific_field_raises() -> None:
    with ShouldRaise(ValidationError):
        adapter.validate_python(
            {"type": "tmux", "name": "x", "use": "y", "ports": {}, "started_at": STARTED}
        )


def test_json_serialisation_roundtrip() -> None:
    svc = DockerService(
        type="docker",
        name="cache",
        use="cache",
        ports={"redis": 6379},
        started_at=STARTED,
        container_id="abc123def456",
        container_name="cache-server",
    )
    json_str = svc.model_dump_json()
    compare(adapter.validate_json(json_str), expected=svc)
