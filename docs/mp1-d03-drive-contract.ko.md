# MP1 D-03 — Google Drive 읽기 전용 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d03-drive-contract.en.md)을 따른다.

## 범위

I-03은 표준 설치형 앱 OAuth Authorization Code 흐름과 PKCE, 최소 읽기 전용 scope로 한 소유자의 Google Drive를 연결한다. 소비자는 Still에서 Google Drive 연결을 누르고 Google 접근만 승인한다. Google Cloud project, client ID, token을 직접 만들지 않는다. Still은 공개 OAuth client ID를 포함하고 시스템 브라우저 및 로컬 redirect를 사용한다. 제품 배포자가 이 배포용 client에 대해 Google API 활성화, OAuth client 등록, consent screen 설정, privacy policy 준비를 한 번 수행한다. 검색 후 선택 파일 읽기만 지원하며 전체 동기화, 쓰기, 삭제, 공유, 권한 변경은 하지 않는다.

## 데이터와 보안

Token은 private connection store에만 남고 export, event, log, engine, browser 응답에서 제외된다. 검색 metadata와 선택 발췌문은 소유자 로컬에 남는다. 선택 발췌문은 소유자에 묶이고 최대 4,000자로 제한되며, 명시 A2A 위임에 정확히 그 context를 전달하려면 단발 승인이 필요하다. Evidence는 lifecycle metadata만 기록하고, portable export는 발췌문·승인·소유자 식별·파일 내용을 제거한다.

## API와 acceptance

Connector는 connect, health, search, 선택 읽기, disconnect를 제공한다. Disconnect는 로컬 token 저장을 폐기하고 redacted audit metadata를 남긴다. 만료 token은 새 인증이 완료될 때까지 `reauth-required`가 된다. Fixture는 PKCE state 불일치, 만료 token/re-auth, scope 거절, 결과 redaction, 선택 발췌 승인 거절, disconnect/reconnect를 다룬다. 자동 테스트는 모든 경로를 다루며 I-03은 Drive 쓰기 capability를 추가하지 않는다.
