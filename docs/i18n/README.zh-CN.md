<div align="center">

<pre>
   ________________   __ 
  / __/_  __/  _/ /  / / 
 _\ \  / / _/ // /__/ /__
/___/ /_/ /___/____/____/
</pre>

**在你自己电脑上运行的个人智能体。<br>底层 AI 可以更换，记忆、进行中的工作和权限都会保留。**

[![CI](https://github.com/Jongtae/Still/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/validate.yml) [![Full test suite](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml/badge.svg?branch=main)](https://github.com/Jongtae/Still/actions/workflows/full-validate.yml) [![Latest release](https://img.shields.io/github/v/release/Jongtae/Still?sort=semver&label=release)](https://github.com/Jongtae/Still/releases) [![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](../../pyproject.toml) [![macOS | Linux | WSL2 limited](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20WSL2%20limited-555)](../../docs/QUICKSTART.md) [![License: AGPL 3.0](https://img.shields.io/badge/license-AGPL--3.0--only-blue)](../../LICENSE)

[English](../../README.md) | [한국어](README.ko.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

[快速开始](#快速开始) · [留给你的东西](#留给你的东西) · [工作原理](#工作原理) · [文档](#文档)

</div>

<!-- readme-parity:v1 -->
<!-- readme-section:hero -->

## 不拥有它的“大脑”，也能拥有自己的 J.A.R.V.I.S. 吗？

**大脑可以借用。助手应该仍然属于你。**

托尼·斯塔克拥有 J.A.R.V.I.S.：它理解他的上下文，随时可以对话，还能通过他的电脑和系统实际行动。而且托尼连背后的技术也拥有。但我们大多数人并不拥有自己使用的最强 AI 大脑。模型的能力、价格、政策和可用性，都可能随着提供方而变化。

所以问题不只是*我们能不能造出自己的贾维斯*。**借用 AI 的智能时，我们能不能仍然拥有真正属于自己的个人助手，并保留它的记忆、上下文、尚未完成的工作和我们交付的权限？** 每当出现更好的模型，我们是否都必须放弃与助手一起积累的一切？

Still 探索的答案是：*不必*。在一个由你安装和掌控的开源环境里，一个个人智能体（PA）保管你的上下文、进行中的工作和权限，而真正干活的 AI 可以更换。

<p align="center">
  <img src="../../docs/assets/readme/demo/demo-v5.zh-CN.gif" width="800" alt="真实对话记录：记住咖啡喜好、在亚马逊继续搜索F1模型、推荐咖啡豆及登录后确认购物车。">
</p>
<p align="center"><sub>由真实录屏剪辑而成。</sub></p>

**主要特点**

- **用你已订阅的 AI。** ChatGPT 订阅通过 Codex、Claude 订阅通过 Claude Code 接入，完成实际工作。其他接入方式见 [QUICKSTART](../../docs/QUICKSTART.md#other-model-connections)。
- **换了对话，还是同一个 PA。** 记忆、已保存的结果和未完工作的状态会跨对话、跨重启保留。中断的执行会如实报告，不会悄悄重放。
- **边界由你决定。** 访问从你连接的文件夹、账户和工具开始。支持的付款流程会要求逐次批准；[当前浏览器限制](https://github.com/Jongtae/Still/issues/26)已公开说明。[密钥](../../.github/SECURITY.md)不会进入模型提示词、日志或 Evidence。
- **告诉你，而不是瞒着你。** AI 记住某件事时会告诉你，并提供精确的撤销。每项委托工作都会记录用了你的哪些信息、发送到了哪里。
- **在你常用的地方对话。** 用手机上的 Telegram，或在 macOS、Linux 的浏览器中对话。Windows 可通过[目前仍有限制的 WSL2](../../docs/QUICKSTART.md#windows-wsl2)运行。

<!-- readme-section:principles -->

## Still 的五大支柱

这不是某个特定 AI 模型的功能清单，而是我们评价正在构建的助手的标准。

- **Presence（陪伴）：** 助手持续存在于你平时沟通的地方，而不是每次要办事都让你打开一个单独的应用。
- **Portable Memory（可迁移记忆）：** 你的记忆和上下文不会被锁定在某一个 AI 服务里。
- **Model Independence（模型独立）：** 更换 AI 模型时，不会失去助手的连续性以及你们之间的关系。
- **Execution（执行）：** 不只是回答问题，还能在真实服务和计算机上采取行动。
- **Owner Control（所有者控制）：** 数据、权限和执行结果始终由你掌控。

这些是指导原则，并不表示所有场景今天都已经可用。[产品状态](../../docs/product-status.en.md)、上面的真实演示和[当前浏览器限制](https://github.com/Jongtae/Still/issues/26)会区分已经观察到的行为与仍在完善的部分。

<!-- readme-section:try-today -->

## 快速开始

**开始前请准备：**

- 一台 Mac、Linux 电脑或装有 WSL2 的 Windows，以及网络连接。
- 一个负责干活的 AI：ChatGPT 订阅加 [Codex CLI](https://github.com/openai/codex)、Claude 订阅加 [Claude Code](https://code.claude.com/docs/en/setup)，或者 API 密钥（OpenAI、Anthropic、OpenAI 兼容服务器）或这台电脑上的 [Ollama](https://ollama.com)。如果还没有安装 CLI，首次设置会打开安装页面。
- 可选：手机或电脑上的 [Telegram](https://telegram.org)，通过用 @BotFather 创建的机器人在手机上与 Still 对话。

在 macOS、Linux 或 Windows（WSL2）上，一条命令即可安装并启动，无需 Homebrew、Python 或 Docker：

```sh
curl -LsSf https://raw.githubusercontent.com/Jongtae/Still/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh | sh
```

运行前可以[检查脚本](https://github.com/Jongtae/Still/blob/8c88eb1d96533ffd25351b6d54b4eac0f78f66cd/scripts/install.sh)；脚本会校验公开的 Still 归档和它下载的 uv 安装程序。每次发布时，此 URL 都会固定到安装该版本的提交。

在 Windows 上，请在 PowerShell 中运行 `wsl --install` 并重启，然后打开 Ubuntu 运行第一条命令。Windows 说明、安装脚本做了什么、从源码运行以及所有设置，请见 [QUICKSTART](../../docs/QUICKSTART.md)。对话期间请保持 `agentos start` 运行。

如果你在 macOS 上使用 [Homebrew](https://brew.sh)：

```sh
brew tap jongtae/still https://github.com/Jongtae/Still
brew install jongtae/still/agentos
agentos start
```

1. 首次启动时在此终端中完成设置，方式与第一次启动 Claude Code 相同：依次询问访问密码（可选）、AI 连接（通过 Codex 连接 ChatGPT、通过 Claude Code 连接 Claude，或使用 API 密钥）以及 Telegram 机器人令牌（可选）。每一步都可以按 Enter 跳过。
2. 随后浏览器会打开 [http://127.0.0.1:8787](http://127.0.0.1:8787/) 进行最终确认。界面默认为英语，可在左下角切换为简体中文、韩语或日语。连接错误和固定系统提示会以你选择的语言显示；部分界面和回复目前仍为韩语。[其他接入方式](../../docs/QUICKSTART.md#other-model-connections)
3. Telegram 配对：在终端粘贴 @BotFather 的令牌后会输出配对链接。打开链接并点击 Start 即可。
4. 问你真正需要的事：**“我家猫最近不怎么吃饭，该怎么办？”** 几天后只要说 **“帮我挑一台 3000 元以内的扫地机器人。”** PA 会自己记得你养猫，并把这一点考虑进去。

**发布状态：** 两种安装方式安装的都是从已打标签的 main 提交构建的 **v0.1.0**（2026-10-18）。演示和日常使用画面来自真实使用会话，并非该发行版本的端到端验证；架构图属于产品方向。[release manifest](../../docs/release-manifest.json) 记录每个版本的覆盖范围，[产品状态](../../docs/product-status.en.md) 区分已提供的功能、测试依据和发展方向。

**卸载：** `agentos uninstall` 会移除后台服务和程序，除非你选择删除，否则数据会保留（[详情](../../docs/QUICKSTART.md#uninstall)）。

<!-- readme-section:ownership -->

## 留给你的东西

| 可以更换的 | 始终属于你的 |
| --- | --- |
| 模型及其提供方 | 关于你的记忆和上下文 |
| 干活的 CLI 智能体或 API | 进行中的工作及下一步 |
| 工具和连接器 | 权限与批准 |
| | 实际执行过的记录 |

出现更好的 AI 时，你更换的是干活的 AI，而不是智能体本身。[《谁的智能体？》](../../docs/whitepapers/whose-agent.ko.md)（韩语白皮书）对这个问题有更深入的探讨。

本地优先（local-first）不等于只在本地（local-only）：订阅的 AI 运行在 OpenAI 或 Anthropic，因此请求所用的上下文会发送给该提供方。

<!-- readme-section:presence -->

## 工作原理

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/presence-overview.zh-CN.narrow.svg">
  <img src="../../docs/assets/readme/presence-overview.zh-CN.svg" alt="概念图：与你的关系（目标、记忆、偏好、承诺、权限）连接 Assistant、Finance、Research 三种专业视角；领域、角色、委托范围与权限进入 Decision Model。Unified Presence 维持同一段关系，并把你在工作进行中的介入反馈给判断。这是产品方向示例，并非已发布的功能。">
</picture>

- **与你的关系** 把目标、记忆、偏好、承诺和权限保留在你的掌控之下。
- **专业视角** 从相关领域和角色解读同一情境。委托范围确定目标与限制，实际权限另行把守。
- **Decision Model** 判断是思考、执行、委派、询问、汇报还是持续跟进，而不只是挑选工具。
- **Unified Presence** 不是一种角色。它跨越所有角色维持同一段关系：有意义的进展会告诉你，你的纠正会作用于进行中的工作（在运行环境支持的范围内）。

角色不等于资质或执行权限；多种角色协同工作是产品方向，并非已发布的功能（[契约](../../docs/professional-roles-and-communication-contract.en.md)）。此图是设计方向，并非已观测的运行。详情：[架构与本体](../../docs/personal-agentos-architecture.en.md) · [Presence](../../docs/presence-experience-contract.en.md)

<!-- readme-section:ai-switch -->

## 换了 AI，对话照样继续

<p align="center">
  <img src="../../docs/assets/readme/ai-switch/ai-switch-conversation.png" alt="三张裁剪后的韩语 Telegram 截图。1：切换到 Claude Code 后，主人问昨天的午餐肉最后放进去了还是拿出来了，PA 接上之前的购物车对话并请求登录。2：登录后，它回答购物车里有 1 罐 CJ 经典午餐肉 200g，并说明有些步骤未能确认。3：切换到 Codex 后，主人让它把午餐肉拿掉，PA 回复已删除，其余 22 件商品仍在。">
</p>
<p align="center"><sub>截取自真实对话（Telegram，韩语界面）。登录链接已裁掉，姓名已模糊处理。 · <a href="../../docs/assets/readme/ai-switch/ai-switch-conversation.png">查看大图</a></sub></p>

① **切换到 Claude Code。** 问“昨天的午餐肉最后放进去了还是拿出来了？”，PA 接上之前的购物对话。

② **查看当前购物车。** 重新登录后，回答购物车里有 1 罐 200g 经典午餐肉（SPAM）。

③ **切换到 Codex，请它做下一步。** “把午餐肉拿掉。”在同一段对话里，它告知已删除，其余 22 件商品仍在。

<!-- readme-section:conversation -->

## 日常使用

*节选自真实的 Telegram 对话画面（韩语界面），并配有简短的场景说明。*

<picture>
  <source media="(max-width: 600px)" srcset="../../docs/assets/readme/readme-examples-v4.zh-CN.png">
  <img src="../../docs/assets/readme/readme-examples-v4.zh-CN.png" alt="真实画面节选：记住喜好、在亚马逊继续搜索F1模型、购物车中确认有一件Jardin Espresso Blend 500克咖啡豆。">
</picture>

之前提到的拿铁口味，会在为咖啡机挑选咖啡豆时再次成为依据。F1 模型的搜索无需重新说明就延续到亚马逊，并在推荐的同时说明未能确认的部分。选定咖啡豆后，会确认已登录的购物车里有 1 件。**你说的或展示的 → 带来源和时间的已授权上下文 → 有用的提问或工具 → 行动边界 → 确认的结果。** 加入购物车和付款是两种不同的行动。

**下一步目标：** 即使隔了几个小时，也能理解“那个”和“上次那个”指什么，让你只需继续对话，而不必拼装工作流。

> “咖啡快没了。” · *几小时后* · “出门路上有地方买吗？” · *之后* · “没时间了，买上次那个。”

<!-- readme-section:more -->

## 文档

| 文档 | 内容 |
| --- | --- |
| [QUICKSTART](../../docs/QUICKSTART.md) | 安装、连接模型、文件、Telegram、从源码运行 |
| [产品状态](../../docs/product-status.en.md) | 能做什么、还有哪些不便，以及各部分的依据 |
| [为什么做这个项目](../../docs/VISION.md) | 项目的动机 |
| [架构与本体](../../docs/personal-agentos-architecture.en.md) | 内核基本概念、软件包、运行时和所有者控制 |
| [文档地图](../../docs/README.md) | 当前全部契约和指南 |
| [致谢与参考](../../docs/acknowledgements.en.md) | 影响设计的研究与项目 |

**参与贡献。** 欢迎提交 Issue 和 Pull Request。请先阅读 [CONTRIBUTING](../../.github/CONTRIBUTING.md)；开发流程见 [AGENTS.md](../../AGENTS.md)。初次接触代码的话，可先阅读[贡献者入门指南](../contributor-onboarding.en.md)（英文），了解代码结构和第一次修改的方法。用过之后，欢迎通过[反馈表单](https://github.com/Jongtae/Still/issues/new?template=feedback.yml)或 [Discussions](https://github.com/Jongtae/Still/discussions) 告诉我们哪些好用、哪些不好用。

<!-- readme-section:license -->

## 许可证

[AGPL-3.0-only](../../LICENSE)。Still 的名称和标志遵循[商标说明](../../docs/TRADEMARKS.md)。
