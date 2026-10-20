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

[クイックスタート](#クイックスタート) · [自分に残るもの](#自分に残るもの) · [仕組み](#仕組み) · [ドキュメント](#ドキュメント)

</div>

<!-- readme-parity:v1 -->
<!-- readme-section:hero -->

## 頭脳を所有していなくても、自分の J.A.R.V.I.S. を持てる？

**頭脳は借りられる。アシスタントは自分のものであるべきだ。**

トニー・スタークには J.A.R.V.I.S. がいました。自分の文脈を理解し、いつでも話しかけられ、コンピューターやシステムを通じて実際に行動する個人アシスタントです。しかもトニーは、その背後にある技術まで所有していました。しかし私たちの多くは、使う最も高性能な AI の頭脳を所有していません。モデルの性能や価格、ポリシー、利用条件は提供元によって変わる可能性があります。

だから問いは、*自分の Jarvis を作れるか* だけではありません。**その知性を借りながらも、本当に自分のものと言える個人アシスタントを持ち、記憶や文脈、終わっていない仕事、任せた権限を保てるでしょうか。** より良いモデルが現れるたびに、アシスタントとの積み重ねまで手放さなければならないのでしょうか。

Still は、その必要はないという可能性を探ります。自分でインストールし管理するオープンソース環境で、一つのパーソナルエージェント（PA）が文脈・進行中の仕事・権限を保ち、実際に働く AI は替えられるようにします。

<p align="center">
  <img src="../../docs/assets/readme/demo/demo-v5.ja.gif" width="800" alt="実際の会話記録：コーヒーの好み、Amazonへ続くF1モデル検索、豆のおすすめとログイン後のカート確認。">
</p>
<p align="center"><sub>実際の録画を編集。</sub></p>

**主な特長**

- **契約中の AI をそのまま。** ChatGPT のプランは Codex で、Claude のプランは Claude Code で接続し、実際の仕事を任せます。そのほかの接続方法は [QUICKSTART](../../docs/QUICKSTART.md#other-model-connections) にあります。
- **会話が変わっても同じ PA。** 記憶、保存した結果、未完の仕事の状態は次の会話や再起動後も残ります。中断された実行は黙って再実行せず、中断として報告します。
- **境界は自分で決める。** アクセスは自分が接続したフォルダー・アカウント・ツールから始まります。対応している支払いフローは行動ごとの承認を求め、[現在のブラウザー上の制限](https://github.com/Jongtae/Still/issues/26)も公開しています。[秘密情報](../../.github/SECURITY.md)はモデルのプロンプト・ログ・Evidenceに入りません。
- **隠さずに知らせる。** AI が何かを記憶するとそれを知らせ、正確に取り消せるようにします。任せた仕事ごとに、どの情報を使いどこへ送ったかを記録します。
- **いつもの場所で会話。** スマートフォンの Telegram、または macOS・Linux のブラウザーで話せます。Windows は[現在制限のある WSL2](../../docs/QUICKSTART.md#windows-wsl2)で動作します。

<!-- readme-section:principles -->

## Still の五つの柱

これは特定の AI モデルの機能ではありません。私たちが作るアシスタントを評価するための基準です。

- **Presence:** 毎回別のアプリを開いて依頼するのではなく、普段コミュニケーションする場所で寄り添い続けるアシスタント。
- **Portable Memory:** 記憶と文脈が一つの AI サービスに閉じ込められないこと。
- **Model Independence:** AI モデルを替えても、アシスタントとの継続性や関係を失わないこと。
- **Execution:** 質問に答えるだけでなく、実際のサービスやコンピューター上で行動できること。
- **Owner Control:** データ、権限、実行結果を自分で管理できること。

これらは指針であり、すべての場面がすでに動くという主張ではありません。[製品の状況](../../docs/product-status.en.md)、上の実際のデモ、[現在のブラウザー上の制限](https://github.com/Jongtae/Still/issues/26)で、観測済みのことと今後の課題を区別しています。

<!-- readme-section:try-today -->

## クイックスタート

**始める前に用意するもの：**

- Mac、Linux のコンピューター、または WSL2 を入れた Windows と、インターネット接続。
- 仕事を任せる AI を一つ：ChatGPT のプランと [Codex CLI](https://github.com/openai/codex)、Claude のプランと [Claude Code](https://code.claude.com/docs/en/setup)、または API キー（OpenAI、Anthropic、OpenAI 互換サーバー）か、このコンピューターの [Ollama](https://ollama.com)。CLI がまだなければ、初回設定でインストールページを開きます。
- 任意：スマートフォンかコンピューターの [Telegram](https://telegram.org)。@BotFather で作ったボットを通じて、スマートフォンから Still と話せます。

macOS、Linux、Windows（WSL2）で、コマンド一つでインストールしてそのまま起動します。Homebrew、Python、Docker は不要です。

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh | sh
```

実行前に[スクリプトを確認](https://github.com/Jongtae/Still/blob/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh)でき、スクリプトは公開済み Still アーカイブと、ダウンロードする uv インストーラーのチェックサムを検証します。リリースのたびに、この URL はそのリリースを入れるコミットに固定されます。

Windows では PowerShell で `wsl --install` を実行して再起動し、Ubuntu を開いて最初のコマンドを実行してください。Windows の手順、インストーラーの動作、ソースからの実行、すべての設定は [QUICKSTART](../../docs/QUICKSTART.md) にあります。会話中は `agentos start` を起動したままにしてください。

macOS で [Homebrew](https://brew.sh) を使っている場合：

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

1. 初回起動は、このターミナルで設定します。Claude Code を初めて起動したときと同じ流れです。アクセスパスワード（任意）、AI の接続（Codex で ChatGPT、Claude Code で Claude、または API キー）、Telegram のボットトークン（任意）を順に尋ねます。各ステップは Enter で飛ばせます。
2. その後、ブラウザで [http://127.0.0.1:8787](http://127.0.0.1:8787/) が開き、最終確認を行います。画面は英語が既定で、左下で日本語、韓国語、簡体字中国語に切り替えられます。接続エラーと Still のカタログにあるシステム文言は、Telegram・ターミナル・ブラウザで設定した言語で表示されます。使用頻度の低い一部の定型文（会話での設定の一部、家族共有、スマートフォンのログイン画面、カレンダー下書きの項目名）はまだ韓国語です。[そのほかの接続](../../docs/QUICKSTART.md#other-model-connections)
3. Telegram のペアリング: ターミナルで @BotFather のトークンを貼り付けると、ペアリングリンクが表示されます。リンクを開いて Start を押してください。
4. 必要なことを聞いてみてください。**「最近、猫がごはんをあまり食べない。どうしたらいい？」** 数日後に **「5万円以下のロボット掃除機を選んで。」** と言うだけで、PA が猫のことを自分から思い出して反映します。

**リリース状況：** どちらのインストール方法でも、タグを付けた main コミットの **v0.1.0**（2026-10-18）が入ります。デモと日常利用の画面は実際の利用セッションにもとづくもので、この版の一連の動作を検証したものではありません。構成図は製品の方向性を示す例です。[release manifest](../../docs/release-manifest.json) は各リリースの範囲を記録し、[製品の状況](../../docs/product-status.en.md) は提供中の機能・テストの根拠・方向性を区別しています。

**アンインストール：** `agentos uninstall` はバックグラウンドサービスとプログラムを削除し、データは削除を選ばない限り残します（[詳しく](../../docs/QUICKSTART.md#uninstall)）。

<!-- readme-section:ownership -->

## 自分に残るもの

| 替えられるもの | 自分に残るもの |
| --- | --- |
| モデルと提供元 | 自分についての記憶と文脈 |
| 仕事をする CLI エージェントや API | 進行中の仕事と次の一歩 |
| ツールとコネクター | 権限と承認 |
| | 実際に実行されたことの記録 |

より良い AI が現れたら、替えるのはエージェントではなく働く AI です。[誰のエージェントか](../../docs/whitepapers/whose-agent.ko.md)（韓国語の白書）がこの問いをさらに掘り下げます。

ローカルファースト（local-first）はローカル限定（local-only）ではありません。サブスクリプションの AI は OpenAI や Anthropic 側で動くため、リクエストに使う文脈はその提供元へ送られます。

<!-- readme-section:presence -->

## 仕組み

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/presence-overview.ja.narrow.svg">
  <img src="../../docs/assets/readme/presence-overview.ja.svg" alt="概念図：あなたとの関係（目的・記憶・好み・約束・権限）が Assistant・Finance・Research の専門的視点につながり、ドメイン・役割・委任範囲・権限が Decision Model に入ります。Unified Presence は一つの関係を保ち、作業中のあなたの介入を判断に戻します。提供済みの機能ではなく、製品の方向性の例です。">
</picture>

- **あなたとの関係** は、目的・記憶・好み・約束・権限をあなたの管理下に置きます。
- **専門的視点** は、同じ状況を関連するドメインと役割で読み解きます。委任範囲は目標と制約を定め、実際の権限は別に守られます。
- **Decision Model** は、考えるか、実行するか、任せるか、尋ねるか、知らせるか、引き続き見守るかを判断します。ツールを選ぶだけではありません。
- **Unified Presence** は役割ではありません。すべての役割をまたいで一つの関係を保ち、意味のある知らせはあなたに届き、あなたの訂正は進行中の仕事に反映されます（実行環境が対応する範囲で）。

役割は資格や実行権限ではなく、複数の役割が協働する姿は提供済みの機能ではなく製品の方向性です（[契約](../../docs/professional-roles-and-communication-contract.en.md)）。図は設計の方向性であり、観測された実行ではありません。詳しくは [アーキテクチャとオントロジー](../../docs/personal-agentos-architecture.en.md) · [Presence](../../docs/presence-experience-contract.en.md)。

<!-- readme-section:ai-switch -->

## AI を替えても続く会話

<p align="center">
  <img src="../../docs/assets/readme/ai-switch/ai-switch-conversation.png" alt="韓国語の Telegram 画面 3 枚を切り出した画像。1: Claude Code に切り替えたあと、昨日スパムを入れたか抜いたかを尋ねると、PA が前のカートの会話を引き継ぎ、ログインを求めます。2: ログイン後、CJ スパム クラシック 200g が 1 個カートにあると答え、一部の手順は確認できなかったと伝えます。3: Codex に切り替えてスパムを抜くよう頼むと、抜いたこと、残り 22 点はそのままであることを伝えます。">
</p>
<p align="center"><sub>実際の会話から抜き出した画面です（Telegram、韓国語 UI）。ログインリンクは切り取り、名前はぼかしています。 · <a href="../../docs/assets/readme/ai-switch/ai-switch-conversation.png">原寸で見る</a></sub></p>

① **Claude Code に切り替え。** 「昨日スパムは結局入れた？抜いた？」という質問に、前の買い物の会話を引き継ぎます。

② **今のカートを確認。** もう一度ログインしたあと、スパム クラシック 200g が 1 個カートにあると答えます。

③ **Codex に切り替えて次の行動を依頼。** 「スパムを抜いて」。同じ会話の中で、削除の結果と残り 22 点の商品を伝えます。

<!-- readme-section:conversation -->

## 日常での使い方

*実際の Telegram の会話画面（韓国語）を抜き出し、短い場面説明とともに並べました。*

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/readme-examples-v4.ja.png">
  <img src="../../docs/assets/readme/readme-examples-v4.ja.png" alt="実際の画面抜粋：好みの記憶、Amazonへ続くF1検索、カートで確認したJardin Espresso Blend 500g 1点。">
</picture>

以前話したラテの好みは、コーヒーマシン用の豆を選ぶときにまた基準になります。F1 モデルの検索は言い直さなくても Amazon へ続き、おすすめと一緒に確認できなかった点も伝えます。豆を決めると、ログインしたカートに 1 点入ったことまで確かめます。**話したことや見せたもの → 出典と時刻のある許可された文脈 → 必要な質問やツール → 行動の境界 → 確認された結果。** カートへの追加と支払いは別の行動です。

**次の目標：** 数時間たっても「それ」や「同じの」が何を指すかを追えること。ワークフローを組み立てず、会話を続けるだけで済むようにします。

> 「コーヒーがほとんどない。」 · *数時間後* · 「出かけるついでに買える所ある？」 · *その後* · 「時間ない。前と同じので。」

<!-- readme-section:more -->

## ドキュメント

| ドキュメント | 内容 |
| --- | --- |
| [QUICKSTART](../../docs/QUICKSTART.md) | インストール、モデル接続、ファイル、Telegram、ソースからの実行 |
| [製品の状況](../../docs/product-status.en.md) | できること、まだ不便なこと、領域ごとの根拠 |
| [なぜ作るのか](../../docs/VISION.md) | プロジェクトの動機 |
| [アーキテクチャとオントロジー](../../docs/personal-agentos-architecture.en.md) | カーネルの基本概念、パッケージ、ランタイム、オーナーによる管理 |
| [ドキュメントマップ](../../docs/README.md) | 現在の契約とガイドの一覧 |
| [謝辞と参考文献](../../docs/acknowledgements.en.md) | 設計に影響を与えた研究とプロジェクト |

**コントリビュート。** Issue と Pull Request を歓迎します。まず [CONTRIBUTING](../../.github/CONTRIBUTING.md) を、開発の流れは [AGENTS.md](../../AGENTS.md) をご覧ください。コードが初めての方は、[コントリビューター向けオンボーディングガイド](../contributor-onboarding.en.md)（英語）でコードの構成と最初の作業の進め方をご確認ください。試したら、[フィードバックフォーム](https://github.com/Jongtae/Still/issues/new?template=feedback.yml) か [Discussions](https://github.com/Jongtae/Still/discussions) で、よかった点と困った点を教えてください。

<!-- readme-section:license -->

## ライセンス

[AGPL-3.0-only](../../LICENSE)。Still の名称とロゴは [商標について](../../docs/TRADEMARKS.md) に従います。
