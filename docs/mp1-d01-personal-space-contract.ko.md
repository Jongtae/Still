# MP1 D-01 — Personal Space 경계 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d01-personal-space-contract.en.md)을 따른다.

## 상태와 사용자 결과

**상태: 설계 완료.** 이 문서는 MP1 I-01 구현의 결정 기록이다. 새 사용자는 외부 2nd brain을 고르지 않고 Still 개인 공간과 하나의 비서로 시작하며, 무엇이 기억·원문·작업 증거인지 이해하고 제어할 수 있어야 한다.

## 현재 상태 인벤토리

| 현재 저장소 | Personal Space 분류 | 원문과 외부 공유 |
| --- | --- | --- |
| `notes` | 명시 저장 기억 | 사용자가 `/note` 또는 “메모/기록”으로 요청한 내용만 저장; 모델에는 Still 도구를 통해서만 제공 |
| `workspace_results` | 사용자가 저장한 안전한 작업 결과 | 결과 사본과 redacted evidence만 보관; 원 작업은 `jobs`에 남음 |
| `context_events` | 임시 owner-submitted 맥락 | 만료 전 로컬에만 보관; assistant policy와 요청별 승인 없이는 공유하지 않음 |
| 연결 폴더의 문서 | source of truth 원문 | Still DB에 복제하지 않음; 기존 문서 공유 경계를 통과한 발췌만 외부 모델에 전달 가능 |
| `messages`, `jobs`, `tool_events` | 대화·작업·실행 증거 | 장기 기억이 아니며, 사용자-facing 화면에는 redacted summary만 표시 |

## 데이터 흐름

1. 소유자가 명시적으로 메모를 저장하면 Still이 owner runtime의 `notes`에 기록한다. 선택된 실행 엔진은 요청에 필요한 경우에만 `list_notes` 도구 결과를 받으며, DB에 직접 접근하지 않는다.
2. 소유자가 작업 결과를 저장하면 `workspace_results`에 안전한 사본과 redacted evidence가 기록된다. 원 작업과 실행 이력은 `jobs`·`tool_events`에 분리되어 남는다.
3. 연결 폴더 원문과 임시 context는 기존 source policy 및 요청별 승인 경계를 통과한 최소 발췌만 실행 엔진에 전달한다. Personal Space 집계는 원문을 읽거나 복제하지 않는다.
4. I-01 read model은 인증된 소유자에게만 각 저장소의 허용된 메타데이터와 redacted count를 반환한다. 삭제 요청은 지정된 note 또는 result에만 적용하고 evidence와 원문에는 전파되지 않는다.

## 확정된 경계

1. **명시 저장 기억**은 현재 `notes`가 유일한 source of truth다. 전체 대화, 모델 추론, 검색 결과, 파일 내용은 자동 기억으로 승격하지 않는다.
2. **안전한 작업 요약**은 사용자가 작업공간에 저장한 `workspace_results`만 의미한다. I-01은 모델이 자동으로 작업 요약을 만들거나 저장하지 않는다.
3. **원문**은 연결 폴더와 사용자가 제출한 임시 context에 남는다. Personal Space는 원문을 재인덱싱하거나 별도 사본을 만들지 않는다.
4. **실행 증거**는 `jobs`와 `tool_events`에 유지한다. 개인 공간의 증거 view는 source category와 count만 보여 주며 request text, search query, 파일 경로, tool payload, model reasoning을 표시하지 않는다.
5. **외부 공유**는 기존 정책을 보존한다. 연결 문서와 context inbox는 source-specific approval이 필요하고, 명시 저장 기억은 Still이 제공한 `list_notes`/`save_note` 도구를 통해서만 선택된 엔진에 노출된다. 이 설계는 새로운 자동 공유를 추가하지 않는다.

## 위협 모델과 완화

| 위협 | D-01 완화와 잔여 경계 |
| --- | --- |
| 실행 엔진 또는 외부 모델이 개인 DB를 과도하게 읽음 | 엔진에는 선언된 Still 도구와 승인된 최소 결과만 제공하고 owner DB·원문 경로를 직접 mount하거나 노출하지 않는다. |
| 대화·추론·검색·원문이 의도치 않게 장기 기억으로 승격됨 | `notes`와 owner-saved `workspace_results`만 Personal Space 항목이며 자동 저장 경로를 만들지 않는다. |
| Personal Space 화면이나 API가 민감한 context·payload를 재노출함 | 인증 owner만 접근하고 context는 metadata, evidence는 category/count로 redaction한다. |
| 결과 삭제가 증거·원문까지 손상시키거나 복구를 막음 | 삭제 대상은 note/result 사본 하나로 제한하고 jobs, tool events, source originals의 retention은 별도 정책으로 남긴다. |
| export가 credential·연결 grant를 함께 유출함 | portable export의 secret, session, pairing, engine/model choice, folder grant 제외 규칙을 보존한다. |

## I-01 저장·API 계약

I-01은 새 personal-memory 테이블을 만들지 않는다. 기존 `notes`, `workspace_results`, `context_events`, `jobs`, `tool_events`를 집계하는 read model을 사용해 중복과 migration 위험을 피한다.

| Interface | 동작 |
| --- | --- |
| `GET /api/personal-space` | 명시 기억 목록/개수, 저장한 결과 목록/개수, 임시 context 메타데이터, redacted evidence category/count를 반환 |
| `DELETE /api/personal-space/memories/:id` | 해당 명시 기억 하나만 삭제; job, message, tool event, source document는 삭제하지 않음 |
| `DELETE /api/personal-space/results/:id` | 해당 저장 결과 사본만 삭제; 원 job과 증거는 삭제하지 않음 |

응답은 인증된 소유자에게만 제공하며, context item은 content가 아니라 현재 status가 제공하는 metadata만 반환한다. 모든 삭제는 owner action이고 재시도해도 같은 상태가 되는 멱등 동작이어야 한다. 작업 증거나 대화 원문을 지우는 별도 retention/purge 정책은 I-01 범위 밖이다.

## UI와 채널 계약

- 웹의 기본 화면은 “내 비서”이며, Personal Space는 고급 관리에서 기억·저장 결과·맥락·사용한 근거를 설명하는 단일 화면으로 제공한다.
- 신규 사용자가 연결 목록, MCP, A2A, 2nd brain 선택을 강제받지 않는다.
- Telegram은 기존 `/note`, `/notes`, 작업 결과, 근거 표기를 유지한다. I-01은 Telegram에 새로운 자동 기억 또는 데이터 공유를 추가하지 않는다.
- 삭제와 내보내기는 “무엇이 삭제/이동되고 무엇이 남는지”를 사람 언어로 표시한다.

## 보존·내보내기·복구

현재 portable export는 auth, session, connection secret, Telegram pairing, 엔진/모델 선택, 로컬 폴더 grant를 제외하고 owner DB를 옮긴다. I-01은 이 계약을 유지한다. 따라서 명시 기억, 저장 결과, 작업 증거는 export/restore 대상이고, context inbox는 현재 DB에 남아 있는 만료 전 항목만 함께 이동한다. 연결된 원문과 비밀값은 다시 연결해야 한다.

## I-01 검증 계획

| 검증 | 증명할 내용 |
| --- | --- |
| unit/store | personal-space aggregate가 각 source를 중복 없이 분류하고 redacted evidence만 반환 |
| API/auth | 미인증 요청 거절, owner가 note/result만 멱등 삭제, context content 미노출 |
| migration | 기존 QuickStore 데이터에서 schema migration 없이 personal-space read model 생성 |
| export/restore | note, 저장 결과, evidence는 이동하고 secret·연결·folder grant는 이동하지 않음 |
| browser | 새 owner가 연결 선택 없이 메모 저장, 저장 결과 확인, 개별 삭제, export 안내를 수행 |
| live acceptance | 실제 owner가 웹과 paired Telegram에서 명시 저장 메모와 저장 결과를 확인하고, 외부 연결 없이 새 설치 흐름을 완료 |

## Fixture와 acceptance 기록

구현 이슈는 `QuickStore` fixture로 명시 메모 2개, 저장 결과 1개, 만료 전 context 1개, job/tool evidence 2개를 owner 하나에 만들고 두 번째 owner의 항목도 추가한다. fixture는 owner 격리, 중복 없는 count, context 내용의 부재, note/result 삭제가 evidence에 영향을 주지 않음을 검증한다. live acceptance는 실제 소유자, 사용한 채널, 날짜, redacted observation, 실패·복구 결과를 named evidence로 남긴다. automated 결과와 live 결과는 서로 대체하지 않는다.

I-01은 이 모든 automated contract와 named live acceptance 절차가 구현 이슈에 기록된 뒤에만 시작한다.
