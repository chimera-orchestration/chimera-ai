import pytest
from pydantic import ValidationError
from testfixtures import TempDir, compare

from chimera.service_config import (
    DockerServiceConfig,
    ProcessServiceConfig,
    ServicesConfig,
    TmuxServiceConfig,
    load_services_config,
)


FULL_YAML = """\
services:
  - type: docker
    name: cache
    use: cache
    ports:
      redis: 6379
    image: redis:7
  - type: tmux
    name: my-agent
    use: agent
    ports: {}
    session: agent-session
    command: bash
  - type: process
    name: my-proc
    use: worker
    ports:
      http: 8080
    command: python worker.py
"""


def test_load_docker_service(tmpdir: TempDir) -> None:
    path = tmpdir.write("services-config.yaml", FULL_YAML.encode())
    config = load_services_config(path)
    compare(len(config.services), expected=3)
    compare(
        config.services[0],
        expected=DockerServiceConfig(
            type="docker", name="cache", use="cache", ports={"redis": 6379}, image="redis:7"
        ),
    )


def test_load_tmux_service(tmpdir: TempDir) -> None:
    path = tmpdir.write("services-config.yaml", FULL_YAML.encode())
    config = load_services_config(path)
    compare(
        config.services[1],
        expected=TmuxServiceConfig(
            type="tmux",
            name="my-agent",
            use="agent",
            ports={},
            session="agent-session",
            command="bash",
        ),
    )


def test_load_process_service(tmpdir: TempDir) -> None:
    path = tmpdir.write("services-config.yaml", FULL_YAML.encode())
    config = load_services_config(path)
    compare(
        config.services[2],
        expected=ProcessServiceConfig(
            type="process",
            name="my-proc",
            use="worker",
            ports={"http": 8080},
            command="python worker.py",
        ),
    )


def test_docker_command_optional(tmpdir: TempDir) -> None:
    yaml_text = """\
services:
  - type: docker
    name: db
    use: cache
    image: some-image:latest
"""
    path = tmpdir.write("services-config.yaml", yaml_text.encode())
    config = load_services_config(path)
    compare(
        config.services[0],
        expected=DockerServiceConfig(
            type="docker", name="db", use="cache", image="some-image:latest"
        ),
    )


def test_docker_command_set(tmpdir: TempDir) -> None:
    yaml_text = """\
services:
  - type: docker
    name: db
    use: cache
    image: some-image:latest
    command: redis-server --appendonly yes
"""
    path = tmpdir.write("services-config.yaml", yaml_text.encode())
    config = load_services_config(path)
    compare(
        config.services[0],
        expected=DockerServiceConfig(
            type="docker",
            name="db",
            use="cache",
            image="some-image:latest",
            command="redis-server --appendonly yes",
        ),
    )


def test_unknown_type_raises(tmpdir: TempDir) -> None:
    yaml_text = """\
services:
  - type: kubernetes
    name: bad
    use: bad
"""
    path = tmpdir.write("services-config.yaml", yaml_text.encode())
    with pytest.raises(ValidationError):
        load_services_config(path)


def test_missing_required_field_raises(tmpdir: TempDir) -> None:
    yaml_text = """\
services:
  - type: docker
    name: no-image
    use: cache
"""
    path = tmpdir.write("services-config.yaml", yaml_text.encode())
    with pytest.raises(ValidationError):
        load_services_config(path)


def test_services_config_model_validate() -> None:
    data = {
        "services": [
            {"type": "process", "name": "x", "use": "y", "command": "run.sh"},
        ]
    }
    config = ServicesConfig.model_validate(data)
    compare(
        config.services,
        expected=[ProcessServiceConfig(type="process", name="x", use="y", command="run.sh")],
    )
