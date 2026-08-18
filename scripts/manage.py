#!/usr/bin/env python3
"""Docker Compose 운영 명령을 한곳에서 실행하는 관리 도구.

Docker 명령을 외우지 않아도 ``python3 scripts/manage.py start``처럼
서비스의 시작, 종료, 재시작, 상태 및 로그 확인을 수행할 수 있다.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from _environment import ensure_env


def docker_compose(*arguments: str) -> int:
    """Run Docker Compose and return its exit code without hiding its output."""
    if shutil.which("docker") is None:
        print("오류: Docker를 찾을 수 없습니다. Docker Engine을 먼저 설치해 주세요.", file=sys.stderr)
        return 127
    try:
        return subprocess.run(["docker", "compose", *arguments], check=False).returncode
    except OSError as exc:
        print(f"오류: Docker Compose를 실행하지 못했습니다: {exc}", file=sys.stderr)
        return 127


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Counter Checker Docker Compose 서비스를 관리합니다.",
        epilog="처음에는 setup, start 순서로 실행하세요. 예: make setup && make start",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("setup", help=".env를 만들고 비밀 키를 생성/검증합니다")
    start = commands.add_parser("start", help="이미지를 빌드하고 서비스를 백그라운드로 시작합니다")
    start.add_argument("--no-build", action="store_true", help="이미지를 다시 빌드하지 않습니다")
    commands.add_parser("stop", help="서비스와 네트워크를 종료합니다 (DB 볼륨은 보존)")
    commands.add_parser("restart", help="실행 중인 서비스를 재시작합니다")
    commands.add_parser("status", help="컨테이너 상태를 표시합니다")
    logs = commands.add_parser("logs", help="서비스 로그를 표시합니다")
    logs.add_argument("--follow", "-f", action="store_true", help="새 로그를 계속 출력합니다")
    logs.add_argument("--tail", type=int, default=100, help="서비스별 마지막 줄 수 (기본: 100)")
    logs.add_argument("service", nargs="?", choices=("web", "mariadb"), help="특정 서비스만 조회")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 어떤 운영 명령을 먼저 실행해도 Compose 변수 해석에 필요한 환경이 준비되도록 한다.
    path = ensure_env()
    if args.command == "setup":
        print(f"초기 설정 완료: {path}")
        print("필요하면 .env의 DB 비밀번호와 WEB_PORT를 수정한 뒤 `make start`를 실행하세요.")
        return 0

    if args.command == "start":
        command = ["up", "-d"]
        if not args.no_build:
            command.append("--build")
        result = docker_compose(*command)
        if result == 0:
            print("서비스를 시작했습니다. 상태 확인: make status")
        return result
    if args.command == "stop":
        return docker_compose("down")
    if args.command == "restart":
        # restart는 기존 컨테이너의 환경 변수를 갱신하지 않으므로 다시 생성한다.
        return docker_compose("up", "-d", "--build", "--force-recreate")
    if args.command == "status":
        return docker_compose("ps")

    command = ["logs", "--tail", str(args.tail)]
    if args.follow:
        command.append("--follow")
    if args.service:
        command.append(args.service)
    return docker_compose(*command)


if __name__ == "__main__":
    sys.exit(main())
