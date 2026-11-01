<div align="center">

<pre>
   ________________   __ 
  / __/_  __/  _/ /  / / 
 _\ \  / / _/ // /__/ /__
/___/ /_/ /___/____/____/
</pre>

**내 컴퓨터에서 돌아가는 나만의 개인 에이전트.<br>일하는 AI를 바꿔도 기억과 진행 중인 일, 권한은 그대로 남습니다.**

[![CI](https://github.com/Jongtae/Still/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/validate.yml) [![Full test suite](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml) [![Latest release](https://img.shields.io/github/v/release/Jongtae/Still?sort=semver&label=release)](https://github.com/Jongtae/Still/releases) [![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](../../pyproject.toml) [![macOS | Linux | WSL2 limited](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20WSL2%20limited-555)](../../docs/QUICKSTART.md) [![License: AGPL 3.0](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](../../LICENSE)

[English](../../README.md) | [한국어](README.ko.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

[지금 할 수 있는 것](#지금-할-수-있는-것) · [빠른 시작](#빠른-시작) · [나에게 남는 것](#나에게-남는-것) · [문서](#문서) · [Engineering](https://jongtae.github.io/Still/engineering/ko.html)

</div>

<!-- readme-parity:v1 -->

<!-- readme-section:hero -->

## AI는 바뀌어도, 나의 비서는 이어집니다.

우리 대부분은 직접 쓰는 AI 모델을 소유하지 못합니다. 모델을 만든 기업은 가격과 규칙, 이용 여부를 언제든 바꿀 수 있습니다. Still은 내 컴퓨터에 설치하는 오픈소스 비서입니다. 나에 대해 아는 것과 끝내지 못한 일, 맡겨 둔 권한은 내 컴퓨터에 두고, 실제로 일하는 AI(이미 구독 중인 바로 그 AI)는 얼마든지 바꿀 수 있습니다.

<p align="center">
  <img src="../../docs/assets/readme/demo/demo-v5.ko.gif" width="800" alt="실제 대화 녹화: 커피 취향 기억, 아마존으로 이어지는 F1 모형 검색, 원두 추천과 로그인 후 장바구니 확인.">
</p>
<p align="center"><sub>실제 녹화 화면을 편집했습니다.</sub></p>

<!-- readme-section:ai-switch -->

## AI를 바꿔도 이어지는 대화

<p align="center">
  <img src="../../docs/assets/readme/ai-switch/ai-switch-conversation.png" alt="한국어 Telegram 화면 세 장을 잘라 붙인 이미지. 1: Claude Code로 바꾼 뒤 어제 스팸을 넣었는지 뺐는지 묻자, PA가 이전 장바구니 대화를 이어받고 로그인을 요청합니다. 2: 로그인 후 CJ 스팸 클래식 200g 1개가 장바구니에 있다고 답하고, 일부 단계는 확인하지 못했다고 알립니다. 3: Codex로 바꾼 뒤 스팸을 빼 달라고 하자, 뺐고 나머지 22개 상품은 그대로라고 답합니다.">
</p>
<p align="center"><sub>실제 대화에서 발췌한 화면입니다. 로그인 링크는 잘라내고 이름은 가렸습니다. · <a href="../../docs/assets/readme/ai-switch/ai-switch-conversation.png">크게 보기</a></sub></p>

Claude Code로 바꾼 뒤에는 PA가 어제의 쇼핑 대화를 이어받아 장바구니를 확인합니다. Codex로 바꾼 뒤에도 같은 대화에서 “스팸 빼 줘.”라고 하면 그대로 처리됩니다.

*편집한 실제 사용자 세션이며 화면은 한국어입니다. 대화의 연속성을 보여 주지만, 장바구니 변경의 독립 검증이나 배포본 전체 검증은 아닙니다.* [원문](https://github.com/Jongtae/Still/blob/main/docs/assets/readme/ai-switch/manifest.json)

<!-- readme-section:capabilities -->

## 지금 할 수 있는 것

- **이미 구독 중인 AI로 일합니다.** ChatGPT 구독은 Codex로, Claude 구독은 Claude Code로 실제 일을 맡깁니다. API 키나 [Ollama](https://ollama.com)도 쓸 수 있습니다. 대화 중에 `/ai`로 바꿔도 대화는 그대로 이어집니다.
- **터미널·브라우저·메신저에서 대화합니다.** 터미널과 브라우저, Telegram, Slack이나 Mattermost의 다이렉트 메시지에서 이야기하면 됩니다.
- **이전 대화를 다음 요청에 활용합니다.** 지난주에 한 이야기가 필요할 때 다시 쓰입니다. 하루에 한 번 최근 요청을 돌아보며 기억을 정리하고, 사실마다 내가 직접 한 말에 근거한 것인지 추론한 것인지 표시합니다. 저장한 내용은 모두 되돌릴 수 있습니다.
- **내가 연결한 폴더와 서비스를 사용합니다.** 내가 연결한 폴더(따로 말하지 않으면 읽기 전용), Gmail 검색과 허락한 경우의 메일함 정리(메일을 보내지는 않음), Google Calendar와 Drive, 그리고 내가 로그인한 웹사이트(휴대폰에서 로그인해도 됩니다)에서 일합니다.
- **작업 중에도 질문하거나 요청을 수정할 수 있습니다.** 긴 일을 시작하기 전에 계획과 어떤 AI가 맡는지 알려 줍니다. 진행 중에 다른 것을 묻거나 중간에 바로잡을 수 있습니다.
- **결제 승인을 받고 비밀번호와 토큰을 보호합니다.** 결제는 매번 내 승인이 필요합니다. [비밀값](../../.github/SECURITY.md)은 프롬프트나 로그에 들어가지 않습니다. 맡긴 일마다 내 정보 중 무엇을 썼고 어디로 보냈는지 보여 줍니다.

아직 다듬는 중인 부분도 있으며, 어느 것인지는 [현재 상태](../../docs/product-status.ko.md)에서 확인할 수 있습니다.

<!-- readme-section:try-today -->

## 빠른 시작

Mac, Linux 또는 [WSL2](../../docs/QUICKSTART.md#windows-wsl2)가 설치된 Windows(일부 제약 있음)와 AI 하나가 필요합니다. ChatGPT 구독과 [Codex CLI](https://github.com/openai/codex), Claude 구독과 [Claude Code](https://code.claude.com/docs/en/setup), API 키, 또는 Ollama 중 하나면 됩니다. 명령 하나로 Still을 설치하고 바로 시작하며, Homebrew, Python, Docker는 필요 없습니다.

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/ebf97d4150be3eb867649a31e79287f05db02886/scripts/install.sh | sh
```

실행 전에 [스크립트를 직접 확인](https://github.com/Jongtae/Still/blob/ebf97d4150be3eb867649a31e79287f05db02886/scripts/install.sh)해 보세요. 스크립트는 내려받는 모든 것을 검증합니다. Windows에서는 PowerShell에서 `wsl --install`을 실행하고 재시작한 뒤, Ubuntu에서 이 명령을 실행하세요. Apple Silicon Mac에서 [Homebrew](https://brew.sh)를 쓴다면 아래처럼 설치할 수도 있습니다. Homebrew가 더 이상 Intel Mac용 패키지를 빌드하지 않으므로, Intel Mac에서는 위 명령을 쓰세요.

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

1. 설정은 터미널에서 진행합니다. 접근 비밀번호(선택)를 정한 뒤 AI를 고르며, 이미 로그인된 AI는 미리 골라 둡니다. 그 자리에서 바로 Still과 대화하거나 [http://127.0.0.1:8787](http://127.0.0.1:8787/)의 Still 화면을 쓰면 됩니다. 대화하는 동안에는 `agentos start`를 계속 실행해 두세요. Mac에서는 `agentos service install`을 한 번 실행하면 터미널 없이 백그라운드에서 계속 돌고, 로그인할 때마다 다시 시작됩니다([자세히](../../docs/QUICKSTART.md#background-service-macos)).
2. Telegram을 쓰려면 “텔레그램 연결해줘”라고 말하고, 숨김 입력 칸에 @BotFather 토큰을 붙여 넣은 뒤, 페어링 링크를 열어 Start를 누르세요.
3. 실제로 필요한 것을 말해 보세요. **“고양이가 요즘 사료를 잘 안 먹어. 어떻게 하면 좋을까?”** 며칠 뒤 **“50만 원 이하 로봇청소기 하나 골라 줘.”**라고 하면, 고양이 이야기를 기억하고 있습니다.

모든 설정과 Windows 안내, 제거 방법(`agentos uninstall`은 지우겠다고 고르지 않는 한 데이터를 남깁니다)은 [QUICKSTART](../../docs/QUICKSTART.md)에 있습니다.

**배포 상태:** 두 설치 방법 모두 태그가 붙은 main 커밋으로 빌드한 **v0.3.0**(2026-10-31)을 설치합니다. 데모와 일상 사용 화면은 실제 사용 세션에서 가져온 것으로, 이 배포본을 처음부터 끝까지 검증한 결과는 아닙니다. 구조 그림은 제품 방향을 보여 주는 예시입니다. 배포본별 포함 범위는 [release manifest](../../docs/release-manifest.json)에, 제공 기능·테스트 근거·방향의 구분은 [현재 상태](../../docs/product-status.ko.md)에 정리되어 있습니다.

<!-- readme-section:ownership -->

## 나에게 남는 것

| 바뀔 수 있는 것 | 나에게 남는 것 |
| --- | --- |
| 모델과 제공자 | 나에 대한 기억과 맥락 |
| 일을 맡은 CLI 에이전트나 API | 진행 중인 일과 다음 단계 |
| 도구와 커넥터 | 권한과 승인 |
| | 실제로 실행된 일의 기록 |

더 좋은 AI가 나오면 에이전트는 그대로 두고 일하는 AI만 바꾸면 됩니다. 로컬 우선(local-first)이 로컬 전용(local-only)을 뜻하지는 않습니다. 구독 AI는 OpenAI나 Anthropic의 서버에서 동작하므로, 요청에 쓰이는 맥락은 해당 제공자에게 전송됩니다. 이 프로젝트는 다섯 가지 원칙을 따릅니다. 늘 곁에 있는 비서(Presence), 옮길 수 있는 기억, 모델 독립성, 실제 실행, 소유자 통제입니다([왜 만드는가](../../docs/VISION.md)).

<!-- readme-section:presence -->

## 동작 방식

<details>
<summary>구조와 보조 화면 펼치기</summary>

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/presence-overview.ko.narrow.svg">
  <img src="../../docs/assets/readme/presence-overview.ko.svg" alt="개념 그림: 나와의 관계(목적·기억·선호·약속·권한)가 Assistant·Finance·Research 전문 관점으로 이어지고, 도메인·역할·위임 범위·권한이 Decision Model로 들어갑니다. Unified Presence는 하나의 관계를 지키며, 작업 중 나의 개입을 판단에 되돌립니다. 배포 기능이 아닌 제품 방향 예시입니다.">
</picture>

</details>

Still은 나와의 관계(목적, 기억, 선호, 권한)를 하나로 유지하면서, 요청마다 그에 맞는 역할의 눈으로 읽고 답할지, 실행할지, 물을지, 결과를 알릴지 판단합니다. 일하는 도중에 내가 한 말은 진행 중인 일에 반영됩니다. 그림은 설계 방향을 나타낼 뿐, 실제 실행을 관찰한 것이 아닙니다. 자세히 보기: [아키텍처](../../docs/personal-agentos-architecture.en.md) · [Presence](../../docs/presence-experience-contract.en.md)

<!-- readme-section:conversation -->

## 일상에서 쓰는 모습

*실제 Telegram 대화(한국어 화면)에서 발췌하고 짧은 장면 설명을 붙였습니다.*

<details>
<summary>구조와 보조 화면 펼치기</summary>

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/readme-examples-v4.ko.png">
  <img src="../../docs/assets/readme/readme-examples-v4.ko.png" alt="실제 화면 발췌: 기억한 취향, 아마존으로 이어지는 F1 검색, 장바구니에서 확인한 자뎅 에스프레소 블렌드 500g 1개.">
</picture>

</details>

전에 말한 라테 취향은 원두를 고를 때 다시 기준이 되고, F1 모형 검색은 다시 설명하지 않아도 아마존으로 이어지며, 고른 원두는 로그인된 장바구니에 담긴 것까지 확인합니다. 장바구니에 담는 것과 결제하는 것은 별개의 행동입니다.

<!-- readme-section:engineering -->

## Engineering

Still이 기억과 작업을 유지하는 구조, AI의 판단 방식, 사용자 권한을 다루는 설계를 설명합니다. 구현된 기능과 검증한 범위, 앞으로의 설계 방향을 구분하고 관련 문서와 코드를 연결합니다.

[왜 이렇게 설계했을까요?](https://jongtae.github.io/Still/engineering/ko.html) · [Research & Evidence](https://jongtae.github.io/Still/engineering/ko.html#research)

<!-- readme-section:more -->

## 문서

[QUICKSTART](../../docs/QUICKSTART.md)(설치, 연결, Telegram, Slack, 소스 실행) · [현재 상태](../../docs/product-status.ko.md) · [왜 만드는가](../../docs/VISION.md) · [아키텍처](../../docs/personal-agentos-architecture.en.md) · [문서 지도](../../docs/README.md) · [참고한 연구와 프로젝트](../../docs/acknowledgements.en.md)

**기여하기.** 이슈와 PR을 환영합니다. [CONTRIBUTING](../../.github/CONTRIBUTING.md)과 [기여자 온보딩 안내](../../docs/contributor-onboarding.en.md)(영문)부터 읽어 주세요. 써 보셨다면 [피드백 양식](https://github.com/Jongtae/Still/issues/new?template=feedback.yml)이나 [Discussions](https://github.com/Jongtae/Still/discussions)에 좋았던 점과 아쉬운 점을 남겨 주세요.

<!-- readme-section:license -->

## 라이선스

[AGPL-3.0-only](../../LICENSE). Still 이름과 로고는 [상표 안내](../../docs/TRADEMARKS.md)를 따릅니다.
