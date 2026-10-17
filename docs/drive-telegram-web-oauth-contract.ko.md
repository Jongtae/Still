# DRIVE-TG-01 — Telegram Drive 웹 OAuth 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](drive-telegram-web-oauth-contract.en.md)을 따른다.

## 사용자 흐름

연결되지 않은 상태에서 연결된 Telegram 사용자가 Drive 자료를 요청하면 Still은 HTTPS `Google Drive 연결하기` 버튼을 보낸다. 사용자는 일반 브라우저에서 직접 Google 로그인·동의를 수행하고, Telegram에서 민감정보 없는 연결 결과를 확인한다. Telegram 내장 브라우저는 OAuth 신뢰 경계가 아니다.

사용자 로컬 callback 경계는 동일한 연결 Telegram 사용자에게 간단한 연결 완료 또는 복구 결과를 보낸다. 브라우저 callback을 Telegram 명령으로 받지 않으며, Telegram 메시지에 OAuth code·state·token·파일 데이터를 포함하지 않는다.

권한은 `https://www.googleapis.com/auth/drive.file` 하나뿐이다. Still은 전체 Drive를 검색하지 않는다. 사용자는 Google Picker에서 파일을 선택하고, 선택된 파일 ID만 읽어 로컬 컨텍스트로 사용할 수 있다. 쓰기·공유·삭제·Calendar·Gmail·서비스 계정·전체 Drive 권한은 거부한다.

파일 본문은 Picker 선택 검증을 통과한 뒤에만 사용자 로컬 injected transport로 읽는다. 해당 요청에 대한 메모리 내 요약에만 쓰며, Drive OAuth 상태·감사 증거·로컬 설정·relay payload에 저장하지 않는다.

## 로컬·중계 경계

사용자 로컬 런타임이 PKCE verifier/challenge, HMAC로 서명한 무작위 일회성 state, 만료, Telegram 사용자 바인딩을 만든다. 콜백은 state·사용자·만료·일회성 검증을 모두 통과할 때만 처리한다. 코드 교환과 토큰 저장은 로컬 암호화 secret store에서만 한다. Drive handoff는 일반 `QuickStore`를 받으면 실패하며, `EncryptedDriveSecretStore`는 인증된 암호화를 사용하고 키는 데이터 디렉터리가 아닌 사용자 로컬 keychain 또는 런타임 secret 경계에서만 받는다. 상태·감사 기록에는 상태명과 시간만 남기며 코드·verifier·토큰·client secret·Telegram 본문·파일 본문·발췌문을 남기지 않는다.

HTTPS handoff/relay는 브라우저를 라우팅할 수 있지만 위 값을 저장해서는 안 된다. 로그·DB·분석·오류 payload에도 secret 또는 파일 데이터를 담지 않는다.

## 배포 절차 (이번 작업에서 수행하지 않음)

1. 제공자 Google Cloud 프로젝트에 Desktop과 별도의 **Web application** OAuth client를 만든다.
2. 소유한 HTTPS callback URI 하나를 정확히 등록하고 Still 배포 설정과 동일하게 맞춘다.
3. 운영 공개 전 소유 도메인, 홈페이지, 개인정보처리방침을 준비한다. 그 전에는 Testing과 제한된 test user를 유지한다.
4. client secret은 사용자 로컬의 암호화 deployment secret store에만 넣고, 코드·Telegram·저장소에는 넣지 않는다.
5. Google 로그인·동의·토큰 교환·Picker 선택·실제 Drive 관찰은 별도 사용자 운영 승인 뒤에만 수행한다.

## 소유자 승인 로컬 전용 테스트 모드

소유자 Mac에서만 테스트할 때는 별도 Web client에 `http://localhost:<port>/oauth/google/callback`을 등록할 수 있다. Google은 localhost를 HTTPS 예외로 문서화한다. handoff와 callback 모두 같은 loopback runtime으로 지정하고, 명시적 local-only 모드를 켜면 localhost 이외 주소는 거부한다. 이 Mac의 Telegram Desktop에서만 버튼을 열어 일반 브라우저가 localhost의 Still runtime으로 연결되게 한다. 휴대폰의 localhost는 Mac이 아니라 휴대폰이므로 이 흐름을 사용할 수 없다. 공개 tunnel·DNS·별도 호스팅은 사용하지 않는다.

## 복구와 증거

거부·불일치·재사용·만료·code 누락·교환 실패·취소·토큰 만료에는 간단한 Telegram 재연결 안내를 보낸다. 새 연결 요청은 새 state를 만들며 콜백이나 외부 메시지를 재전송하지 않는다. fixture 테스트는 정상·거부·만료·재사용·재인증·토큰 비노출·선택 파일 강제를 증명한다. 이는 mock 검증일 뿐 실제 OAuth·Drive 증거가 아니다.
