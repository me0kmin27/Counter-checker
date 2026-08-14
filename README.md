# Counter Checker

복사기의 카운터 통지 메일을 POP3/POP3S로 수신해 보관하고 웹에서 확인하는 1차 MVP입니다.
현재 범위는 **메일 수신·저장·조회까지**이며 OCR과 카운터 값 분석은 후속 단계입니다.

## 구현 환경

Docker 없이 Debian 서버에 직접 설치하는 구성을 기준으로 합니다.

- Debian 12 이상
- Nginx
- PHP-FPM / PHP CLI 8.2 이상
- MariaDB
- PHP 확장: PDO MySQL, mbstring, OpenSSL
- 5분 주기 POP 수신: cron

## 주요 기능

- 웹에서 POP 서버, 포트, TLS, 사용자, 서버 삭제 정책 설정
- 웹에서 즉시 수신하거나 cron으로 활성 계정을 5분마다 자동 수신
- MIME 메일의 원문과 첨부는 비공개 파일 경로에, 검색용 메타데이터와 본문은 MariaDB에 저장
- 계정별 원문 SHA-256 unique 제약으로 중복 저장 방지
- 받은 메일 목록, 본문, 첨부 파일 다운로드 및 계정 연결 오류 확인
- POP 비밀번호는 OpenSSL AES-256-GCM로 인증 암호화
- 관리자 로그인, CSRF 보호, HttpOnly/SameSite 세션 쿠키

## Debian 설치

저장소를 배포할 위치에 내려받은 뒤 설치 스크립트를 root로 실행합니다.

```bash
sudo ./scripts/install-debian.sh
```

스크립트가 패키지를 설치하고 다음 값을 대화식으로 받습니다.

- 서비스 표시 이름과 웹 도메인/IP
- MariaDB 호스트, 포트, DB 이름, 애플리케이션 DB 사용자/비밀번호
- 스키마 생성을 위한 DB 관리자 계정/비밀번호
- 웹 관리자 아이디/비밀번호
- HTTPS 세션 쿠키 사용 여부

입력한 비밀값과 자동 생성된 암호화 키는 Git 작업 폴더가 아닌
`/etc/counter-checker/config.php`에 저장됩니다. 공개 저장소에는 운영 도메인, 이메일 주소,
계정, 비밀번호, 암호화 키와 같은 식별 가능 정보나 고정 secret을 포함하지 않습니다.
설치 후 `/etc/nginx/sites-available/counter-checker`에 TLS 인증서를 연결하고
`session_secure`를 `true`로 바꾸는 것을 권장합니다.

### 기존 MariaDB를 사용할 때

설치 스크립트에서 원격 DB 호스트를 입력할 수 있습니다. 관리자 계정은 설치 시 스키마와
최소 권한 애플리케이션 계정을 만드는 데만 사용되며 설정 파일에 저장되지 않습니다.
애플리케이션 DB 계정에는 대상 DB의 `SELECT`, `INSERT`, `UPDATE`, `DELETE` 권한만 부여합니다.

## 설정 방식

런타임 설정 파일 경로는 기본적으로 `/etc/counter-checker/config.php`입니다. 다른 위치를
사용하려면 Nginx/PHP-FPM 및 cron에 `COUNTER_CHECKER_CONFIG` 환경 변수를 지정합니다.
로컬 개발에 한해 `config/config.example.php`를 `config/config.php`로 복사한 후 환경 변수를
입력할 수 있으며, 실제 설정 파일은 `.gitignore`에서 제외됩니다.

## 수동 메일 수신 및 점검

```bash
sudo -u www-data COUNTER_CHECKER_CONFIG=/etc/counter-checker/config.php php scripts/fetch-mail.php
php tests/run.php
find public src scripts tests -name '*.php' -print0 | xargs -0 -n1 php -l
```

수신 원문과 첨부는 기본적으로 `var/mail/YYYY/MM`에 저장되며 Nginx의 document root인
`public/` 밖에 위치합니다. 운영 전 저장 공간, 백업, 보존 기간을 환경에 맞게 결정하십시오.

## 다음 구현 범위

저장된 원문/첨부를 입력으로 하는 OCR Adapter, 브랜드별 본문 Parser, 카운터 검증 및
수동 검수 화면은 다음 단계에서 구현합니다. OCR 결과는 원본 메일을 덮어쓰지 않고 별도의
처리 실행 및 결과 테이블에 기록할 예정입니다.
