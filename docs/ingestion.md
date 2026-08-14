# 이메일 및 OCR 처리 설계

## 수신 주소 전략

장비 또는 고객사별 식별 가능한 주소(예: `counter+<opaque-token>@inbound.example.com`)를
발급합니다. token에는 고객사 ID나 시리얼을 평문으로 넣지 않고 무작위 값을 사용합니다.
주소와 등록 장비를 연결하되 본문에서 읽은 시리얼과 반드시 교차 검증합니다.

현재 MVP는 POP3/POP3S polling을 사용합니다. 계정과 원문 SHA-256 조합으로 동일한 원본의
중복 저장을 방지하며, 향후 수신 방식이 바뀌어도 같은 중복 방지 계층을 사용합니다.

## Adapter 계약

브랜드별 Adapter는 다음과 같은 정규화 전 결과를 반환합니다.

```json
{
  "brand": "example",
  "serial_number": {"raw": "ABC-123", "confidence": 0.99},
  "captured_at": {"raw": "2026-08-14 09:00", "confidence": 0.95},
  "counters": [
    {"source_label": "Total B/W", "raw_value": "12,345", "value": 12345, "confidence": 0.98}
  ],
  "evidence": [{"attachment_id": "...", "page": 1, "bounding_box": [10, 20, 30, 40]}]
}
```

Adapter는 DB에 직접 쓰지 않습니다. 공통 Validation 계층이 장비 연결, 단위 변환, 상태 결정과
저장을 맡습니다. Parser/OCR 버전을 고정해 같은 입력의 결과를 재현할 수 있어야 합니다.

## 처리 순서

1. Webhook 인증, replay 방지, 용량 제한을 적용합니다.
2. 원본 MIME을 암호화 저장하고 checksum과 최소 헤더를 기록합니다.
3. sandbox된 Parser로 MIME 구조를 해석하고 허용된 본문/첨부만 추출합니다.
4. 알려진 템플릿을 fingerprint한 뒤 브랜드 Adapter 후보를 점수화합니다.
5. 평문/HTML에서 값을 우선 추출하고, 이미지 또는 PDF만 OCR로 보냅니다.
6. OCR 전 EXIF 방향, 해상도, 대비, 관심 영역을 보정하되 원본은 수정하지 않습니다.
7. 형식 규칙과 과거 값으로 검증하고 확정 또는 검수 대기로 분류합니다.
8. 처리 지표를 기록하고 사용자 화면에서 원본 근거와 함께 노출합니다.

## 검수 기준 기본값

- 장비 또는 시리얼을 식별할 수 없음
- 필수 카운터 누락
- 필드 신뢰도가 설정 임계값 미만
- 최근 confirmed 값보다 감소
- 장비별 일평균 대비 설정 배수 이상 증가
- 수신 주소에 연결된 장비와 본문 시리얼 불일치
- 동일 시각의 기존 confirmed 값과 충돌

임계값은 브랜드/모델별로 구성 가능하게 하며, 변경 자체도 감사 대상입니다. 과거 값은 OCR
결과를 몰래 교정하는 용도가 아니라 anomaly 판단에만 사용합니다.

## 실패와 재처리

오류는 `temporary`, `unsupported_format`, `invalid_message`, `security_rejected`,
`manual_action_required`로 분류합니다. temporary만 자동 재시도하며 최대 횟수 초과 후 DLQ로
이동합니다. Adapter 배포 후 원본으로 재처리할 수 있지만 기존 실행과 결과를 덮어쓰지 않고
새 `extraction_run`을 만듭니다.

## 품질 측정

- 브랜드/모델별 자동 처리율과 필드별 정확도
- OCR 신뢰도 구간별 실제 수정률
- 수신부터 확정까지의 시간
- 실패/재시도/DLQ 비율
- 운영자 수정이 많은 템플릿과 필드

익명화된 golden fixture에 대해 시리얼과 카운터의 exact match 회귀 테스트를 수행합니다.
