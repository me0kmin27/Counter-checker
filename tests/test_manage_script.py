import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import manage  # noqa: E402


def test_compose_allows_database_packets_larger_than_pop_message_limit():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "compose.yaml").read_text()

    assert '--max-allowed-packet=${DB_MAX_ALLOWED_PACKET:-64M}' in compose


def test_setup_prepares_environment_without_docker(tmp_path):
    env_file = tmp_path / ".env"
    with patch.object(manage, "ensure_env", return_value=env_file) as ensure:
        assert manage.main(["setup"]) == 0
    ensure.assert_called_once_with()


def test_start_builds_and_runs_in_background():
    with patch.object(manage, "ensure_env"), patch.object(
        manage, "docker_compose", return_value=0
    ) as compose:
        assert manage.main(["start"]) == 0
    compose.assert_called_once_with("up", "-d", "--build")


def test_logs_for_one_service_passes_options():
    with patch.object(manage, "ensure_env"), patch.object(
        manage, "docker_compose", return_value=0
    ) as compose:
        assert manage.main(["logs", "--follow", "--tail", "25", "web"]) == 0
    compose.assert_called_once_with("logs", "--tail", "25", "--follow", "web")


def test_restart_recreates_containers_to_apply_environment():
    with patch.object(manage, "ensure_env"), patch.object(
        manage, "docker_compose", return_value=0
    ) as compose:
        assert manage.main(["restart"]) == 0
    compose.assert_called_once_with("up", "-d", "--build", "--force-recreate")
