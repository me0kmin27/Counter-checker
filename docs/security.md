# 보안 및 개인정보 보호

이메일과 첨부는 신뢰할 수 없는 입력입니다. 발신자 주소나 첨부 확장자만으로 브랜드와 파일
유형을 신뢰하지 않습니다.

## 필수 통제

- 수신 Webhook 서명과 timestamp를 검증하고 replay window를 제한합니다.
- MIME 실제 형식 탐지, 허용 목록, 압축 해제 크기/파일 수/페이지 수 제한을 적용합니다.
- 첨부 악성코드 검사와 이미지/PDF decoder 격리, CPU/메모리/시간 제한을 적용합니다.
- HTML은 script 및 외부 resource를 실행하거나 자동 요청하지 않습니다.
- DB, Object Storage, backup을 암호화하고 TLS로 전송합니다.
- 원본 다운로드 URL은 짧은 수명의 서명 URL로 만들고 접근을 감사 기록합니다.
- Webhook secret, DB password, OCR key는 저장소가 아닌 secret manager에 보관합니다.
- 운영자 SSO, MFA, 최소 권한 RBAC를 사용하고 운영/개발 데이터를 분리합니다.
- 로그에는 본문, 주소 전체, token, 인증정보를 기록하지 않고 구조화된 오류 코드만 남깁니다.

## 위협별 대응

| 위협 | 대응 |
| --- | --- |
| 위조 메일/스푸핑 | 전용 opaque 수신 주소, SPF/DKIM/DMARC 결과 기록, 본문 시리얼 교차 검증 |
| 중복/replay | Message-ID + 원본 checksum unique, Webhook timestamp 검증 |
| Parser 취약점 | 최신 decoder, 격리 worker, resource quota, 악성코드 검사 |
| OCR 데이터 유출 | 지역/보존 정책에 맞는 Provider, 학습 사용 금지 계약, 필요 시 자체 OCR |
| 권한 오용 | tenant 범위 강제, RBAC, append-only 감사 로그, 정기 권한 검토 |
| 공급망 공격 | lockfile, dependency/SAST/secret scan, 서명된 immutable image |

## 개인정보와 데이터 수명주기

이메일 주소와 설치 장소는 개인정보 또는 고객 기밀이 될 수 있습니다. 수집 목적, 보존 기간,
처리 위탁자, 저장 지역, 삭제 및 열람 절차를 서비스 시작 전에 문서화합니다. 샘플 이메일은
익명화 후 테스트에 포함하고 실제 고객 원본을 Git 저장소나 issue에 첨부하지 않습니다.

분기마다 복원 시험과 권한 검토를 하고, 사고 대응 runbook에는 credential 회전, 수신 중단,
영향 범위 산정, 통지와 증거 보존 절차를 포함합니다.
