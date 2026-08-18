# Counter Checker

복사기 카운터 통지 메일을 POP3/POP3S로 수신하고 조회하는 FastAPI 애플리케이션입니다.
현재 범위는 메일 수신·저장·조회이며 OCR과 카운터 자동 분석은 후속 단계입니다.

## 기술 방향

이 저장소의 실행 경로는 **Python 하나로 통일**합니다.

- Python 3.12, FastAPI, Uvicorn
- SQLAlchemy와 MariaDB (로컬 개발은 SQLite)
- Jinja2 서버 렌더링
- Python 표준 라이브러리 `poplib` 기반 POP 수신
- Fernet 방식 POP 비밀번호 암호화
- 운영 보조 명령도 모두 `scripts/*.py`

PHP 런타임, PHP-FPM, PHP 설정 파일 및 PHP 수신기는 사용하지 않습니다. 데이터베이스 테이블은
Python SQLAlchemy 모델에서 생성합니다.

## 로컬 실행

Python 3.12가 설치된 환경에서 다음 명령을 실행합니다.

```bash
make bootstrap
make dev
```

`make bootstrap`은 `.venv`를 만들고 의존성을 설치하며, `.env.example`을 바탕으로 `.env`를
생성합니다. 비어 있는 `APP_SECRET_KEY`에는 Fernet 호환 키를 최초 한 번만 생성합니다. 개발
서버는 기본적으로 `http://127.0.0.1:8000`에서 실행되고 SQLite 파일을 사용합니다.

## Docker Compose 실행

처음 설치하는 사용자는 아래 두 명령만 실행하면 됩니다.

```bash
make setup
make start
```

`make setup`은 `.env`를 만들고 암호화 키를 생성하며, `make start`는 FastAPI와 MariaDB를
백그라운드로 시작합니다. 운영 중 `APP_SECRET_KEY`를 변경하면 기존 POP 비밀번호를 복호화할 수
없으므로 `.env`를 안전하게 백업해야 합니다.

자주 사용하는 운영 명령은 다음과 같습니다.

```bash
make status   # 컨테이너 실행 상태
make logs     # 실시간 로그 (Ctrl+C로 보기만 종료)
make restart  # 설정 변경 후 재시작
make stop     # 서비스 종료; DB 데이터는 보존
```

세부 옵션(특정 서비스 로그, 로그 줄 수, 이미지 빌드 생략)은
`python3 scripts/manage.py --help`와 [운영 스크립트 안내](docs/operations.md)에서 확인할 수 있습니다.

## 명령 체계

| 목적 | 명령 |
| --- | --- |
| 개발 환경 구성 | `python3 scripts/bootstrap.py` 또는 `make bootstrap` |
| 개발 서버 실행 | `.venv/bin/python scripts/dev.py` 또는 `make dev` |
| 전체 테스트 | `.venv/bin/pytest` 또는 `make test` |
| 활성 POP 계정 즉시 수신 | `.venv/bin/python scripts/fetch_mail.py` |
| Compose 최초 설정 | `python3 scripts/manage.py setup` 또는 `make setup` |
| Compose 시작/종료 | `make start` / `make stop` |
| Compose 상태/로그 | `make status` / `make logs` |

배포 환경에서는 `DATABASE_URL`, `APP_SECRET_KEY`, `POLL_INTERVAL_SECONDS`를 환경 변수로
전달합니다. 활성 POP 계정은 FastAPI 프로세스 내부 polling task가 기본 5분 간격으로 확인하므로
별도의 PHP cron 작업은 필요하지 않습니다.

## 설정

`.env.example`의 항목은 다음과 같습니다.

- `APP_SECRET_KEY`: 32바이트 URL-safe base64 Fernet 키
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`: Compose MariaDB 설정
- `DB_MAX_ALLOWED_PACKET`: 큰 메일 저장을 위한 MariaDB 패킷 한도(기본 `64M`)
- `WEB_PORT`: 호스트에 공개할 웹 포트
- `POLL_INTERVAL_SECONDS`: POP 자동 수신 간격(최소 60초)
- `DATABASE_URL`: 로컬에서 기본 SQLite 대신 다른 DB를 쓸 때 선택적으로 지정

화면별 사용 방법은 [사용 방법](docs/usage.md), 구조와 후속 계획은
[아키텍처](docs/architecture.md)를 참고하십시오.
