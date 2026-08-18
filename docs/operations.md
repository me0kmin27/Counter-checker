# 초기 설정 및 운영 스크립트 안내

이 문서는 명령줄에 익숙하지 않은 사용자도 Counter Checker를 설치하고 운영할 수 있도록 두 가지
실행 경로를 설명합니다. **로컬 개발**은 `bootstrap.py`, **상시 운영**은 Docker Compose용
`manage.py`를 사용합니다. 모든 명령은 저장소 최상위 디렉터리에서 실행합니다.

## 준비 사항

- 로컬 개발: Python 3.12
- Docker 운영: Docker Engine과 `docker compose` 명령
- 처음 내려받은 뒤 `.env`는 아직 없어도 됩니다. 설정 스크립트가 `.env.example`을 복사하고
  POP 비밀번호 암호화에 필요한 `APP_SECRET_KEY`를 자동 생성합니다.

## 로컬 개발 환경 초기 설정

```bash
make bootstrap
make dev
```

`make bootstrap`은 `.venv` 가상 환경을 만들고 개발/테스트 의존성을 설치합니다. 이미 `.env`가
있으면 기존 비밀 키를 유지하고 유효성만 검사하므로 다시 실행해도 키가 바뀌지 않습니다.
`make dev`는 SQLite를 사용하는 개발 서버를 실행합니다. 브라우저에서
`http://127.0.0.1:8000`에 접속하고, 서버는 `Ctrl+C`로 종료합니다.

## Docker 운영 환경 초기 설정

```bash
make setup
```

생성된 `.env`의 `DB_PASSWORD`, `DB_ROOT_PASSWORD`, `WEB_PORT`를 실제 환경에 맞게 변경합니다.
특히 외부에 공개하는 운영 환경에서는 예제 DB 비밀번호를 그대로 사용하지 마십시오.
`APP_SECRET_KEY`는 등록된 POP 비밀번호 복호화에 필요하므로 변경하지 말고 `.env`를 안전하게
백업하십시오.

설정 후 서비스를 시작합니다.

```bash
make start
make status
```

`start`는 이미지를 빌드하고 컨테이너를 백그라운드에서 실행합니다. `status`에서 `web`과
`mariadb`가 실행 중인지 확인한 뒤 `.env`의 `WEB_PORT`(기본 8000)로 접속합니다.

## 일상 운영 명령

| 작업 | 간단 명령 | 직접 실행 및 설명 |
| --- | --- | --- |
| 시작/업데이트 | `make start` | `python3 scripts/manage.py start`; 이미지를 빌드한 뒤 시작 |
| 빠른 시작 | - | `python3 scripts/manage.py start --no-build`; 기존 이미지 사용 |
| 상태 확인 | `make status` | `python3 scripts/manage.py status`; 컨테이너 상태 표시 |
| 로그 보기 | `make logs` | 전체 로그를 계속 표시하며 `Ctrl+C`는 컨테이너를 중지하지 않음 |
| 최근 로그 | - | `python3 scripts/manage.py logs --tail 50 web`; web 최근 50줄 |
| 재시작 | `make restart` | 환경 설정 변경 후 실행 중인 컨테이너 재시작 |
| 종료 | `make stop` | 컨테이너/네트워크 종료, MariaDB 볼륨은 보존 |

명령과 옵션을 잊었을 때는 다음 도움말을 확인합니다.

```bash
python3 scripts/manage.py --help
python3 scripts/manage.py logs --help
```

## 문제 해결

POP 계정 설정의 `SMTP 아이디`와 `SMTP 패스워드`는 별도 SMTP 서버에 접속하기 위한 설정이
아닙니다. 메일 제공업체가 SMTP 계정 정보라고 안내하는 동일한 아이디와 패스워드를 POP3
`USER`/`PASS` 인증에 사용합니다. 아이디는 제공업체 정책에 따라 전체 이메일 주소를 입력해야 할
수 있습니다.

SMTP 암호화 방법은 다음 중 하나를 선택합니다.

- `자동`: SMTP 포트 465에서는 연결 시작부터 SSL/TLS를 사용하고, 다른 포트에서는 STARTTLS를 사용합니다.
- `SSL/TLS`: 연결 시작부터 TLS로 암호화합니다. 일반적으로 SMTP 포트 465와 함께 사용합니다.
- `STARTTLS`: 일반 SMTP 연결을 연 뒤 인증 정보를 보내기 전에 TLS를 시작합니다.
  일반적으로 SMTP 포트 587과 함께 사용합니다.

Outlook 고급 설정과 같은 형태로 POP의 SSL/TLS 및 SPA 선택값과 SMTP 서버, 포트, 암호화 방법,
타임아웃, SPA, 인증 필요 여부와 인증 방법을 계정별로 저장합니다. SMTP 전용 자격 증명을 선택하면
별도 사용자 이름과 암호를 입력하고, 받는 서버와 동일을 선택하면 POP 자격 증명을 사용합니다.

- **Docker를 찾을 수 없음**: Docker Engine을 설치하고 현재 사용자가 Docker를 실행할 수 있는지
  확인합니다.
- **웹에 접속할 수 없음**: `make status`와 `python3 scripts/manage.py logs --tail 100 web`을
  차례로 실행합니다. 포트 충돌이 있다면 `.env`의 `WEB_PORT`를 변경하고 `make start`를 다시
  실행합니다.
- **DB가 준비되지 않음**: `python3 scripts/manage.py logs --tail 100 mariadb`에서 초기화 및 인증
  오류를 확인합니다.
- **POP 확인 시 `Network is unreachable`**: 계정이나 비밀번호를 검사하기 전에 운영 서버에서
  POP 서버로 가는 네트워크 경로를 찾지 못한 상태입니다. 애플리케이션은 IPv4 주소를 먼저
  시도한 뒤 연결되지 않으면 IPv6 주소도 시도합니다. `docker compose exec web python -c
  "import socket; print(socket.getaddrinfo('POP호스트', 995))"`로 컨테이너의 DNS 확인 결과를
  점검하고, 호스트 및 클라우드 방화벽에서 해당 POP 포트(일반적으로 SSL은 995, 비SSL은 110)의
  외부 연결을 허용하십시오. DNS 결과가 IPv6 주소뿐인데 운영 환경이 IPv6를 지원하지 않는다면
  메일 제공업체의 IPv4 지원 POP 호스트를 사용하거나 운영 환경에 IPv6 경로를 구성해야 합니다.
- **POP 확인 시 연결 시간 초과**: 한 IP 주소의 연결이 응답 없이 멈춰 다른 주소를 시도하지 못하는
  일을 방지하기 위해 각 IP 연결은 5초까지만 기다리고, 연결된 뒤의 POP 통신에는 전체 30초 제한을
  적용합니다. 계속 시간 초과가 발생하면 메일 제공업체에서 POP 사용을 별도로 활성화해야 하는지와
  SSL 사용 시 포트가 995인지 확인하십시오.
- **설정 변경이 반영되지 않음**: `make stop`, `make start` 순서로 컨테이너를 다시 만듭니다.
- **`Data too long for column 'raw_message'`**: 이전 버전에서 생성한 MariaDB의 `BLOB` 컬럼은
  약 64KiB까지만 저장할 수 있습니다. 최신 버전은 원문 메일과 첨부파일을 `LONGBLOB`, 본문을
  `LONGTEXT`로 자동 확장하므로 `make start`를 실행해 이미지를 다시 빌드하고 업데이트를 적용하십시오.
  적용 여부는 `docker compose exec mariadb mariadb -u root -p -e "SHOW COLUMNS FROM
  counter_checker.email_messages LIKE 'raw_message';"`에서 형식이 `longblob`인지 확인할 수 있습니다.
- **데이터까지 완전히 삭제해야 함**: `make stop`은 데이터를 보존합니다. 데이터 삭제는 복구할 수
  없으므로 백업 후에만 직접 `docker compose down --volumes`를 실행합니다.
