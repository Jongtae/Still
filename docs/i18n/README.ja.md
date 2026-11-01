<div align="center">

<pre>
   ________________   __ 
  / __/_  __/  _/ /  / / 
 _\ \  / / _/ // /__/ /__
/___/ /_/ /___/____/____/
</pre>

**自分のコンピューターで動く、自分のパーソナルエージェント。<br>下で働く AI を替えても、記憶と進行中の仕事と権限はそのまま残ります。**

[![CI](https://github.com/Jongtae/Still/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/validate.yml) [![Full test suite](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml) [![Latest release](https://img.shields.io/github/v/release/Jongtae/Still?sort=semver&label=release)](https://github.com/Jongtae/Still/releases) [![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](../../pyproject.toml) [![macOS | Linux | WSL2 limited](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20WSL2%20limited-555)](../../docs/QUICKSTART.md) [![License: AGPL 3.0](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](../../LICENSE)

[English](../../README.md) | [한국어](README.ko.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

[いまできること](#いまできること) · [クイックスタート](#クイックスタート) · [自分に残るもの](#自分に残るもの) · [ドキュメント](#ドキュメント) · [Engineering](https://jongtae.github.io/Still/engineering/ja.html)

</div>

<!-- readme-parity:v1 -->

<!-- readme-section:hero -->

## AI が変わっても、あなたのアシスタントは続きます。

私たちの多くは、使う AI モデルを所有することはありません。価格もルールも提供状況も、作り手がいつでも変えられます。Still は、自分のコンピューターにインストールするオープンソースのアシスタントです。あなたについて知っていること、終わっていない仕事、任せた権限はそこに残し、実際に働く AI（すでに契約しているもの）は替えられます。

<p align="center">
  <img src="../../docs/assets/readme/demo/demo-v5.ja.gif" width="800" alt="実際の会話記録：コーヒーの好み、Amazonへ続くF1モデル検索、豆のおすすめとログイン後のカート確認。">
</p>
<p align="center"><sub>実際の録画を編集。</sub></p>

<!-- readme-section:ai-switch -->

## AI を替えても続く会話

<p align="center">
  <img src="../../docs/assets/readme/ai-switch/ai-switch-conversation.png" alt="韓国語の Telegram 画面 3 枚を切り出した画像。1: Claude Code に切り替えたあと、昨日スパムを入れたか抜いたかを尋ねると、PA が前のカートの会話を引き継ぎ、ログインを求めます。2: ログイン後、CJ スパム クラシック 200g が 1 個カートにあると答え、一部の手順は確認できなかったと伝えます。3: Codex に切り替えてスパムを抜くよう頼むと、抜いたこと、残り 22 点はそのままであることを伝えます。">
</p>
<p align="center"><sub>実際の会話から抜き出した画面です（Telegram、韓国語 UI）。ログインリンクは切り取り、名前はぼかしています。 · <a href="../../docs/assets/readme/ai-switch/ai-switch-conversation.png">原寸で見る</a></sub></p>

Claude Code に切り替えたあと、PA は昨日の買い物の会話を引き継いでカートを確認します。Codex に切り替えたあとも、同じ会話の中で「スパムを抜いて」が通じます。

*実際の利用セッションを編集した韓国語画面です。会話の継続を示しますが、カート変更の独立検証やリリース全体の検証ではありません。* [原文](https://github.com/Jongtae/Still/blob/main/docs/assets/readme/ai-switch/manifest.json)

<!-- readme-section:capabilities -->

## いまできること

- **契約中の AI がそのまま働く。** ChatGPT のプランは Codex で、Claude のプランは Claude Code で実際の仕事をします。API キーや [Ollama](https://ollama.com) も使えます。会話の途中で `/ai` で切り替えても、そのまま続きます。
- **いつもの場所で話せる。** ターミナルとブラウザー、Telegram、そして Slack や Mattermost のダイレクトメッセージで。
- **言わなくても覚えている。** 先週話したことが、必要なときに戻ってきます。1 日 1 回、最近の依頼を振り返って記憶を整理し、それぞれの事実があなた自身の言葉にもとづくのか、推測にもとづくのかを示します。保存はすべて取り消せます。
- **自分のものと一緒に働く。** 接続したフォルダー（指定しない限り読み取り専用）、Gmail の検索と、許可すればメールボックスの整理（送信はしません）、Google カレンダーとドライブ、そしてログインした Web サイト。スマートフォンからでも使えます。
- **待たせない。** 長い仕事の前に、計画とどの AI が担当するかを伝えます。実行中に別のことを聞いたり、途中で訂正したりできます。
- **止まるべきところで止まる。** 支払いには毎回あなたの承諾が必要です。[秘密情報](../../.github/SECURITY.md)はプロンプトにもログにも入りません。仕事ごとに、どの情報を使い、どこへ送ったかを示します。

まだ荒削りなところもあります。どれがそうかは[製品の状況](../../docs/product-status.en.md)にあります。

<!-- readme-section:try-today -->

## クイックスタート

Mac、Linux、または [WSL2](../../docs/QUICKSTART.md#windows-wsl2) を入れた Windows（一部制限あり）と、AI を一つ用意してください。ChatGPT のプランと [Codex CLI](https://github.com/openai/codex)、Claude のプランと [Claude Code](https://code.claude.com/docs/en/setup)、API キー、または Ollama です。コマンド一つで Still をインストールして起動します。Homebrew、Python、Docker は不要です。

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/ebf97d4150be3eb867649a31e79287f05db02886/scripts/install.sh | sh
```

実行前に[スクリプトを確認](https://github.com/Jongtae/Still/blob/ebf97d4150be3eb867649a31e79287f05db02886/scripts/install.sh)できます。スクリプトはダウンロードするものをすべて検証します。Windows では PowerShell で `wsl --install` を実行して再起動し、Ubuntu でこのコマンドを実行してください。Apple シリコンの Mac で [Homebrew](https://brew.sh) を使う場合は次のとおりです（Homebrew は Intel Mac 向けのパッケージをビルドしなくなったため、Intel Mac では上のコマンドを使ってください）：

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

1. 設定はターミナルで進みます。アクセスパスワード（任意）、次に AI です（ログイン済みの AI はあらかじめ選ばれています）。そのままターミナルで Still と話すか、[http://127.0.0.1:8787](http://127.0.0.1:8787/) の Still の画面で話せます。会話中は `agentos start` を起動したままにしてください。
2. Telegram を使うには「Telegram をつないで」と言い、@BotFather のトークンを非表示の入力欄に貼り付け、ペアリングリンクを開いて Start を押してください。
3. 必要なことを聞いてみてください。**「最近、猫がごはんをあまり食べない。どうしたらいい？」** 数日後に **「5万円以下のロボット掃除機を選んで。」** 猫のことを覚えています。

すべての設定、Windows の詳細、アンインストール（`agentos uninstall` は、削除を選ばない限りデータを残します）は [QUICKSTART](../../docs/QUICKSTART.md) にあります。

**リリース状況：** どちらのインストール方法でも、タグを付けた main コミットの **v0.3.0**（2026-10-31）が入ります。デモと日常利用の画面は実際の利用セッションにもとづくもので、この版の一連の動作を検証したものではありません。構成図は製品の方向性を示す例です。[release manifest](../../docs/release-manifest.json) は各リリースの範囲を記録し、[製品の状況](../../docs/product-status.en.md) は提供中の機能・テストの根拠・方向性を区別しています。

<!-- readme-section:ownership -->

## 自分に残るもの

| 替えられるもの | 自分に残るもの |
| --- | --- |
| モデルと提供元 | 自分についての記憶と文脈 |
| 仕事をする CLI エージェントや API | 進行中の仕事と次の一歩 |
| ツールとコネクター | 権限と承認 |
| | 実際に実行されたことの記録 |

より良い AI が現れたら、替えるのはエージェントではなく働く AI です。ローカルファースト（local-first）はローカル限定（local-only）ではありません。サブスクリプションの AI は OpenAI や Anthropic 側で動くため、リクエストに使う文脈はその提供元へ送られます。このプロジェクトは五つの原則に導かれています。いつもそばにいること、持ち運べる記憶、モデルからの独立、実行、オーナーによる管理です（[理由](../../docs/VISION.md)）。

<!-- readme-section:presence -->

## 仕組み

<details>
<summary>構成図と補足画面を開く</summary>

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/presence-overview.ja.narrow.svg">
  <img src="../../docs/assets/readme/presence-overview.ja.svg" alt="概念図：あなたとの関係（目的・記憶・好み・約束・権限）が Assistant・Finance・Research の専門的視点につながり、ドメイン・役割・委任範囲・権限が Decision Model に入ります。Unified Presence は一つの関係を保ち、作業中のあなたの介入を判断に戻します。提供済みの機能ではなく、製品の方向性の例です。">
</picture>

</details>

Still はあなたとの一つの関係（目的、記憶、好み、権限）を保ち、依頼ごとに必要な役割の視点で読み解いて、答えるか、実行するか、尋ねるか、あとで知らせるかを判断します。作業中にあなたが言ったことは、進行中の仕事を変えます。図は設計の方向性であり、観測された実行ではありません：[アーキテクチャ](../../docs/personal-agentos-architecture.en.md) · [Presence](../../docs/presence-experience-contract.en.md)。

<!-- readme-section:conversation -->

## 日常での使い方

*実際の Telegram の会話画面（韓国語）を抜き出し、短い場面説明とともに並べました。*

<details>
<summary>構成図と補足画面を開く</summary>

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/readme-examples-v4.ja.png">
  <img src="../../docs/assets/readme/readme-examples-v4.ja.png" alt="実際の画面抜粋：好みの記憶、Amazonへ続くF1検索、カートで確認したJardin Espresso Blend 500g 1点。">
</picture>

</details>

以前話したラテの好みは、豆を選ぶときにまた戻ってきます。F1 モデルの検索は言い直さなくても Amazon へ続き、選んだ豆はログインしたカートに入ります。カートへの追加と支払いは別の行動です。

<!-- readme-section:engineering -->

## Engineering

技術思想、Ontology & Context、Decision Model、Presence、モデル独立性、Owner Authority、Research & Evidence。実装、証拠、残る設計課題を原文につなぎます。

[この設計を選ぶ理由](https://jongtae.github.io/Still/engineering/ja.html) · [Research & Evidence](https://jongtae.github.io/Still/engineering/ja.html#research)

<!-- readme-section:more -->

## ドキュメント

[QUICKSTART](../../docs/QUICKSTART.md)（インストール、接続、Telegram、Slack、ソースからの実行） · [製品の状況](../../docs/product-status.en.md) · [なぜ作るのか](../../docs/VISION.md) · [アーキテクチャ](../../docs/personal-agentos-architecture.en.md) · [ドキュメントマップ](../../docs/README.md) · [謝辞](../../docs/acknowledgements.en.md)

**コントリビュート。** Issue と Pull Request を歓迎します。まず [CONTRIBUTING](../../.github/CONTRIBUTING.md) と[コントリビューター向けオンボーディングガイド](../../docs/contributor-onboarding.en.md)（英語）をご覧ください。試したら、[フィードバックフォーム](https://github.com/Jongtae/Still/issues/new?template=feedback.yml) か [Discussions](https://github.com/Jongtae/Still/discussions) で、よかった点を教えてください。

<!-- readme-section:license -->

## ライセンス

[AGPL-3.0-only](../../LICENSE)。Still の名称とロゴは [商標について](../../docs/TRADEMARKS.md) に従います。
