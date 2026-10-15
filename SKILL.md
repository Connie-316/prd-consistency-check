---
name: prd-consistency-check
description: PRD/原型/门户 多文档自动一致性校验工作流。把"版本同步、changelog 顺序、示例数据等级、订单语义、配色、阈值、精度、表格结构、章节引用、字段定义、门户版本"等可机械判定的规则固化成确定性断言，替代靠 AI/人通读的概率性检查；并接 git pre-commit / CI 强制门禁，让"改文档→跑校验→零错误才可交付"成为铁律。触发词：PRD一致性、文档漂移、原型校验、PRD检查、一致性门禁、文档版本同步、改完PRD帮我验、接pre-commit、文档CI。
---

# PRD / 原型 一致性校验（确定性断言 + 强制门禁）

## 为什么需要它（先讲清楚痛点）

纯靠 AI 或人"通读一遍"来发现文档矛盾，本质是**概率性检查**：同一个错误
（例如原型里"剩余 3 小时 12 分"却标"⏰ 中风险"）可能这次抓到、下次漏掉——
因为它依赖注意力恰好同时落在两个相距 200 行的数字上，且跨 .md / h5 / 原型 / 门户
多份异构文档。

本 skill 把可机械判定的规则固化成**确定性断言**：违反就必然报错，与注意力无关、
可复现、可接入 CI。在一个 B2B 电商的订单履约预警模块上线后，真实捕获过：h5 脱节 13 天、
示例等级错配、changelog 日期倒序、门户版本漏更新、商品类型与风险预警撞色等。

## 核心心法：单一可信源 + 五动作铁律

**唯一可信源 = PRD 的 `.md`**。h5 阅读版、原型、门户都是从它派生或必须与之对齐。

任何一次修改，必须做完这五个动作（缺一不可）：

1. **改内容** —— 只改 `.md`
2. **升版本** —— 文档信息表「版本」+1（如 v2.3→v2.4）
3. **改最后更新日期** —— 改为当天
4. **记 changelog** —— 在「变更记录」表追加一行（日期升序）
5. **重新生成 h5** —— 跑 `build_h5.py`，刷新源指纹

> 漏掉第 5 步 = 研发打开 h5 拿到旧内容（本项目真实事故：脱节 13 天）。

## 交付物（scripts/ 目录）

| 文件 | 作用 |
|------|------|
| `check_consistency.py` | 14 类确定性规则，退出码 0=通过 / 1=错误 |
| `build_h5.py` | 从 `.md` 生成 h5（写入 sha256 源指纹；兼容「更新日期」表头） |
| `check_all.py` | 仓库级编排：扫描所有 `04_工具/consistency_config.json` 逐个校验 |
| `consistency_config.example.json` | 配置模板（路径/阈值/原型锚点/门户选择器/数值声明/术语） |

## 使用流程

### 1. 在新 PRD 模块里初始化

把 `scripts/` 下三个文件复制到模块的 `04_工具/` 目录，并基于
`consistency_config.example.json` 创建本模块的 `consistency_config.json`：

```
<你的模块>/
├── 02_需求文档/PRD_xxx.md
├── 02_需求文档/PRD_xxx_h5.html
├── 03_原型设计/...
└── 04_工具/
    ├── check_consistency.py
    ├── build_h5.py
    └── consistency_config.json   ← 本模块配置
```

配置字段说明：
- `files.md/h5/prototype/portal`：相对模块根的路径（留空字符串则该规则跳过）
- `files.prototype_dir`：原型目录（可选），若配置则**全量扫描**该目录下所有 html（R8 表格结构、R13 术语覆盖范围更广）
- `risk_rules`：风险等级阈值（小时），驱动 R3 等级判定与 R6 阈值存在性检查
- `prototype.risk_table_anchor`：原型目标表 tbody 锚点（R3 定位用）
- `prototype.software_class` / `goods_type_selector` / `risk_colors`：R4/R5 用
- `prototype.portal_version_selectors`：R11 门户版本正则（按你门户 HTML 结构调整）
- `orthogonal_fields`：原型有、PRD 必须定义的字段（R10）
- `numeric_claims`：R12 数值声明校验，如 `[{"claim_regex":"统计维度（(\\d+) 个）","expected":5,"note":"实际维度数"}]`
- `terms`：R13 术语统一，如 `[{"forbidden":"已失效","canonical":"已过期","scope":["prototype","portal"]}]`（历史纠错引用"此前误写X"会自动跳过，不误报）
- `open_item_marker`：R14 待定项标记（默认 🔵）
- `enabled_rules`：按需开关 14 条规则

### 2. 修改 PRD 后跑校验

```bash
python3 04_工具/check_consistency.py
```

（推荐用带 markdown 依赖的 Python 环境运行，例如自建 venv 激活后：
`python3 04_工具/check_consistency.py`）

- 退出码 `0` = 全部规则通过，可交付
- 退出码 `1` = 存在不一致，按报错逐条修复后重跑

改完 `.md` 后记得重生成 h5：
```bash
python3 04_工具/build_h5.py
```

### 3. 接 git pre-commit 强制门禁（推荐）

把 `scripts/pre-commit` 复制到仓库 `.git/hooks/pre-commit` 并加可执行权限。
它会在每次 `git commit` 前扫描仓库内所有 `04_工具/check_consistency.py`，
任一模块校验失败就**阻断提交**。

```bash
cp scripts/pre-commit <你的仓库>/.git/hooks/pre-commit
chmod +x <你的仓库>/.git/hooks/pre-commit
```

> 门禁脚本用 `find` 扫工作树（含未跟踪目录），避免未跟踪目录被 git 折叠成单条记录而漏扫；
> 只要校验脚本存在于任一模块的 `04_工具/`，门禁即时生效。
> 也可手动跑全量编排：`python3 check_all.py --base <仓库根目录>`。

### 4. 接 CI（GitHub Actions 模板）

`scripts/ci.yml` 是 GitHub Actions 示例：push/PR 时自动跑所有模块的校验。
放到 `.github/workflows/consistency-check.yml` 即可。注意 CI 环境需
`pip install markdown`。

## 14 类规则速查

> R0–R2、R8–R14 是**通用规则**，任何 PRD 项目开箱即用。
> R3–R7 是**业务规则示例**（等级阈值、订单语义、配色、精度），按你自己模块的口径在 config 里改，用不上就关掉。

| 规则 | 检查什么 |
|------|---------|
| R0 单一可信源 | 模块必须存在 PRD .md，否则报告结构性缺口并跳过依赖 PRD 的规则 |
| R1 版本同步 | .md 版本/日期 与 h5 同源（含 SOURCE_DIGEST + 日期不早于 changelog；兼容「更新日期」/「最后更新」） |
| R2 变更记录 | changelog 日期必须升序 |
| R3 示例数据 | 原型目标行剩余时间 ↔ 风险等级标签/行底色 符合阈值 |
| R4 订单语义 | 特定类型订单不进预警池（不得待处理/可操作/有风险标签） |
| R5 配色 | 商品类型标签不得复用风险色系（防撞色） |
| R6 阈值 | 4 级阈值全文档一致；已废弃机制仅否定语境 |
| R7 精度表述 | 倒计时精度（如精确到秒）在 PRD/原型必须一致 |
| R8 表格结构 | 原型表格每行单元格数须与表头一致 |
| R9 章节引用 | §x.y 交叉引用须指向真实标题 |
| R10 字段定义 | 原型出现的字段 PRD 须定义并说明关系 |
| R11 门户版本 | 门户入口版本号/日期须与 .md 一致 |
| R12 数值声明 | PRD 声明的数量（"N 种"/"N 个维度"）须与实际一致（配置驱动） |
| R13 术语统一 | PRD 定义的正典术语，原型/门户/h5 不得出现禁用同义表述（配置驱动；历史纠错引用自动跳过） |
| R14 待定项追踪 | 统计「当前未关闭」的 🔵 待定项（排除历史引用、交叉引用、变更记录行、配色图例），提示是否缺集中追踪表（仅警告） |

## 何时用这个 skill

- 写完/改完一份 PRD 或原型，准备交付/提交前
- 发现"文档好像和原型对不上"想系统排查
- 想给团队 PRD 流程加一道不依赖人肉 review 的自动化门禁
- 任何"AI 通读了一遍但心里没底"的场景
