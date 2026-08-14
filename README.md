# Counter Checker

복사기의 이메일 카운터 통지를 POP3로 수집해 MariaDB에 보관하고 웹에서 확인하는 MVP입니다.
본문/OCR 카운터 추출과 월간 사용량 정리는 다음 구현 단계로 분리되어 있습니다.

## 현재 구현 범위

- 웹에서 POP3 서버, 포트, SSL, 계정 및 수신 정책 등록
- 활성 계정 5분 주기 자동 확인 및 `지금 받기` 수동 실행
- RFC MIME 원문, 텍스트/HTML 본문, 첨부 파일과 메타데이터를 MariaDB에 저장
- SHA-256을 이용한 계정별 중복 저장 방지
- 받은 메일 목록/상세 본문/첨부 다운로드와 계정별 연결 오류 확인
- POP 비밀번호 Fernet 암호화 저장

## 실행

### 자동 설치 및 로컬 프로토타입

아래 명령은 `.env`와 Fernet 키를 자동 생성하고, 가상환경을 만든 뒤 개발/테스트 패키지를
설치합니다. 로컬 프로토타입은 별도 DB 설치 없이 SQLite를 사용합니다.

```bash
make bootstrap
make dev
```

MariaDB까지 포함한 웹 환경은 같은 `.env`를 이용해 실행할 수 있습니다.

```bash
make compose-up
```

Python 3.12와 Docker Compose가 필요합니다. 먼저 암호화 키를 만들고 서비스를 시작합니다.

```bash
cp .env.example .env
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
# 출력된 키를 .env의 APP_SECRET_KEY에 입력
docker compose up --build
```

브라우저에서 `http://localhost:8000`을 열고 **POP 설정**에서 수신 계정을 등록합니다.
운영 환경에서는 Compose 예제의 DB 비밀번호를 반드시 변경하고 웹 앞단에 인증과 TLS를
구성해야 합니다.

로컬 개발과 테스트는 다음과 같이 실행합니다.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
APP_SECRET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" pytest
```

## 목표

- 브랜드별로 형식이 다른 카운터 이메일을 안전하게 수신한다.
- 이메일 본문과 이미지 첨부를 동일한 처리 파이프라인으로 정규화한다.
- OCR 결과를 무조건 확정하지 않고 신뢰도와 검수 상태를 함께 관리한다.
- 원본, 추출 결과, 수정 이력을 보존해 모든 수치의 출처를 추적할 수 있게 한다.
- 장비 시리얼 번호를 고객사 및 설치 정보와 연결하고 월간 사용량을 조회한다.
- `main` 브랜치 병합 시 테스트를 통과한 동일 산출물을 운영 서버에 자동 배포한다.

## 문서

- [시스템 아키텍처](docs/architecture.md)
- [데이터 모델](docs/data-model.md)
- [이메일/OCR 처리 설계](docs/ingestion.md)
- [보안 및 개인정보 보호](docs/security.md)
- [배포 및 운영](docs/deployment.md)

## 권장 기술 스택

| 영역 | 1차 선택 | 선택 이유 |
| --- | --- | --- |
| API/작업자 | Python 3.12, FastAPI, Celery | 이메일·이미지 생태계와 비동기 작업 지원 |
| Web | TypeScript, Next.js | 서버 렌더링 대시보드와 타입 안정성 |
| DB | MariaDB 11.4 | 웹/메일 데이터의 관계형 저장과 운영 요구사항 반영 |
| 큐/캐시 | Redis | 작업 재시도와 중복 실행 제어 |
| 원본 저장소 | S3 호환 Object Storage | 이메일·이미지의 불변 보관 및 수명주기 관리 |
| OCR | 교체 가능한 Adapter | 클라우드 OCR과 자체 OCR을 정책에 따라 선택 |
| 메일 수신 | POP3/POP3S | 기존 카운터 수신함과 간단히 연동 |

기술 선택은 확정 계약이 아니라 초기 제안입니다. 실제 메일량, 이미지 품질, 데이터 보존
기간, 설치 환경(클라우드/온프레미스)을 측정한 뒤 ADR로 확정합니다.

## 첫 구현 순서

1. 샘플 이메일을 브랜드별로 확보하고 기대 추출값을 익명화된 fixture로 만든다.
2. 장비·고객사 관리 API와 이메일 원본 저장/중복 방지를 구현한다.
3. 가장 많은 한 브랜드의 평문 Parser를 구현한다.
4. OCR Adapter와 수동 검수 화면을 구현한다.
5. 월간 사용량 및 수집 상태 대시보드를 구현한다.
6. 관측성, 백업 복구 훈련, 배포 자동화를 통과한 뒤 브랜드를 순차 확대한다.

## 핵심 원칙

카운터는 일반적으로 누적값이므로 월 사용량은 해당 월의 단순 합계가 아니라, 검증된
스냅샷의 차이로 계산합니다. 카운터 리셋·장비 교체·감소값은 자동 확정하지 않고 예외로
분류합니다. 자세한 규칙은 [데이터 모델](docs/data-model.md#월간-사용량-계산)을 참고하세요.
