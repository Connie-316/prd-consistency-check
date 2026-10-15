# prd-consistency-check

把「PRD 和原型对不对得上」从**靠人/AI 通读的概率性检查**，变成**可复现的确定性断言 + CI 强制门禁**。

> Deterministic consistency checks for PRD / prototype / portal docs. Turn "I think they match" into `exit 0`.

---

## 它解决什么问题

一份 PRD 往往同时存在四个副本：`.md` 正文、给研发看的 `h5` 阅读版、交互原型、产品门户。
改的时候只改了一处，另外三处就成了"静默漂移"。

这类错误最阴的地方在于：**局部看每一处都通顺自洽**。
比如 PRD §3.1 写"名称自动 +1"，§13.7 写"加 - 副本"——两段单独读都合理，只有跨段对齐才暴露矛盾。
让 AI 通读一遍也一样抓不住，因为它依赖"注意力恰好同时落在两段相距几百行的文字上"——这次抓到，下次漏掉。

这个仓库的做法不同：**把可机械判定的规则写成 Python 断言，违反就必然报错，退出码 1**。
不靠注意力，可复现，能接 git pre-commit 和 CI。

真实捕获过的错误类型（来自一个 B2B 电商订单履约预警模块）：

| 事故 | 后果 |
|---|---|
| h5 阅读版比 .md 落后 13 天 | 研发打开 h5 拿到旧内容，按错版本开发 |
| 原型"剩余 3 小时 12 分"却标"中风险" | 违反自己写的阈值表，示例数据自相矛盾 |
| changelog 日期倒序 | 变更记录失去时间线意义 |
| PRD 写"6 种按钮"，表格只有 5 行 | 研发按文字实现，对不上表 |
| 全文 4 处"4 个维度"，实际列了 5 个 | 一处改了另一处没同步 |
| 商品类型标签复用了风险色系 | 界面撞色，语义混乱 |

---

## 和现有方案的区别

市面上做「文档漂移检测」的工具不少，但方向完全不同：

| 方案 | 检查什么 | 怎么判 |
|---|---|---|
| `prd-drift-detector` | PRD ↔ GitHub Issues（需求到交付） | LLM 语义匹配 |
| `drift-detector`（Go） | 功能需求 ↔ 代码 ↔ 测试，五层链路 | 在 md 里嵌 YAML contract block，解析 AST/DB |
| Prelint（商业 SaaS） | PR ↔ product spec | LLM 审查，需接入其平台 |
| `audit-docs`（skill） | 仓库文档 ↔ PRD/PLAN/task-DAG 是否过期 | LLM 通读比对 |
| **本仓库** | **PRD .md ↔ h5 阅读版 ↔ 交互原型 HTML ↔ 门户 HTML** | **确定性断言，不用 LLM** |

三点本质差异：

1. **管的是产品侧交付物，不是代码侧。**
   上面那些方案的终点都是"代码有没有按需求实现"。这个仓库管的是"PM 自己产出的四份文档彼此对不对得上"——h5 是给研发看的阅读版，原型和门户是 HTML，它们和 .md 之间的漂移，没有工具在管。

2. **确定性断言，不是语义匹配。**
   别人用 LLM 判断"这两段意思是否一致"，这仍然是概率性的。这里是 `if` 语句 + 退出码：指纹不匹配就报错，changelog 日期倒序就报错，声明 4 个维度实际 5 个就报错。同一份输入跑一百次，结果一模一样。

3. **零依赖、离线、能接门禁。**
   纯 Python + `markdown`，不需要 API Key、不联网、不装 Node/Go 环境。所以它能塞进 `pre-commit`，在 commit 那一瞬间拦住你。

**它做不到什么**（说清楚边界）：不理解语义，不会判断"这个需求写得合不合理"；只管改完之后的机械校验，不管创作期写没写对。这两件事是上下游，不是替代关系。

---

## 30 秒上手

```bash
# 1. 把 scripts/ 三个文件复制到你的 PRD 模块
mkdir -p 你的模块/04_工具
cp scripts/check_consistency.py scripts/build_h5.py 你的模块/04_工具/

# 2. 基于模板生成本模块配置
cp scripts/consistency_config.example.json 你的模块/04_工具/consistency_config.json
#    然后按你的目录结构改 files.* 路径

# 3. 跑校验（建议装 markdown 依赖：pip install markdown）
python3 你的模块/04_工具/check_consistency.py
#    exit 0 = 通过，可交付；exit 1 = 有不一致，按报错逐条修
```

推荐目录结构：

```
你的模块/
├── 02_需求文档/PRD_xxx.md          ← 唯一可信源
├── 02_需求文档/PRD_xxx_h5.html     ← 由 .md 生成
├── 03_原型设计/...
└── 04_工具/
    ├── check_consistency.py
    ├── build_h5.py
    └── consistency_config.json
```

---

## 核心心法：单一可信源 + 五动作铁律

**`.md` 是唯一可信源**，h5 / 原型 / 门户都是派生或必须与之对齐。

每次改动必须做完五步，缺一不可：

1. 改内容 —— 只改 `.md`
2. 升版本 —— 文档信息表版本 +1
3. 改最后更新日期
4. 记 changelog —— 日期升序追加
5. 重新生成 h5 —— `python3 04_工具/build_h5.py`

漏掉第 5 步就是那次"h5 落后 13 天"事故的直接原因。

`build_h5.py` 会把源 `.md` 的 sha256 写进 h5。校验时比对指纹，.md 改了而 h5 没重新生成 → 立刻报错。

---

## 14 条规则

R0–R2、R8–R14 是通用规则，任何 PRD 项目开箱即用。
R3–R7 是业务规则示例（阈值、订单语义、配色、精度），按你自己的口径在 config 里改，用不上就关掉。

| 规则 | 检查什么 |
|---|---|
| R0 单一可信源 | 必须有 PRD .md，否则报结构性缺口 |
| R1 版本同步 | .md 与 h5 同源（指纹 + 日期不早于 changelog） |
| R2 变更记录 | changelog 日期必须升序 |
| R3 示例数据 | 原型数据行的数值 ↔ 等级标签/行底色 符合阈值 |
| R4 订单语义 | 特定类型订单不进预警池 |
| R5 配色 | 类型标签不得复用风险色系 |
| R6 阈值 | 各级阈值全文档一致；已废弃机制仅否定语境 |
| R7 精度表述 | 精度表述（精确到秒/分）在 PRD 与原型一致 |
| R8 表格结构 | 原型表格每行单元格数与表头一致 |
| R9 章节引用 | §x.y 交叉引用指向真实标题 |
| R10 字段定义 | 原型出现的字段，PRD 必须定义 |
| R11 门户版本 | 门户入口版本号/日期与 .md 一致 |
| R12 数值声明 | PRD 声明的数量（"N 种"/"N 个维度"）与实际一致 |
| R13 术语统一 | 禁用同义表述（历史纠错引用自动跳过，不误报） |
| R14 待定项追踪 | 统计当前未关闭的待定项（仅警告，不阻断） |

全部规则可开关：`consistency_config.json` 里的 `enabled_rules`。

R12 示例——这条最能体现"确定性"的价值：
PRD 写"统计维度（4 个）"，实际列了 5 个。LLM 通读会顺读过去，正则抓字面数字再数实际项数，不等就报错。

---

## 接强制门禁

### git pre-commit（推荐）

```bash
cp scripts/pre-commit 你的仓库/.git/hooks/pre-commit
chmod +x 你的仓库/.git/hooks/pre-commit
```

每次 commit 前自动扫描仓库内所有 `04_工具/check_consistency.py`，任一模块失败就阻断提交。

> 实现细节：脚本用 `find` 扫工作树而非 `git diff`，因为未跟踪目录会被 git 折叠成单条记录导致漏扫。

### GitHub Actions

把 `scripts/ci.yml` 放到 `.github/workflows/consistency-check.yml`，push/PR 时自动跑全量校验。
CI 环境需要 `pip install markdown`。

也可以手动跑全量编排：

```bash
python3 scripts/check_all.py --base <仓库根目录>
```

---

## 配置说明

| 字段 | 作用 |
|---|---|
| `files.md/h5/prototype/portal` | 各文档相对模块根的路径，**留空则跳过该规则** |
| `files.prototype_dir` | 原型目录，配了就全量扫描目录下所有 html |
| `risk_rules` | 等级阈值，驱动 R3/R6 |
| `prototype.risk_table_anchor` | 原型目标表 tbody 锚点 |
| `orthogonal_fields` | 原型有、PRD 必须定义的字段（R10） |
| `numeric_claims` | R12 数值声明，正则 + 期望值 |
| `terms` | R13 术语统一，禁用词 → 正典词 |
| `open_item_marker` | R14 待定项标记（默认 🔵） |
| `enabled_rules` | 开关 14 条规则 |

完整字段见 `scripts/consistency_config.example.json`。

---

## 谁适合用

- 一个人维护多份 PRD / 原型，改完心里没底的 PM
- 需要给团队 PRD 流程加一道不依赖人肉 review 的自动化门禁
- 已经用 AI 写 PRD，但发现 AI 通读查不出跨文档数值矛盾

## 谁不适合

- 只有一份 `.md`、没有原型和 h5 派生副本 —— 那直接用 R0/R2/R9/R12/R14 几条通用规则就够
- 期望它替代 PRD 写作能力 —— 它只管"改完机械校验"，不管"写对"。这是两个环节

---

## 作为 Agent Skill 使用

本仓库同时是一个标准 [Agent Skill](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills)（`SKILL.md` + `scripts/`）。
放到你的 skills 目录即可被 Claude Code / WorkBuddy / Codex / Cursor 等工具识别调用。

```bash
npx skills add Connie-316/prd-consistency-check
```

触发场景：写完/改完 PRD 准备交付、发现文档和原型对不上、想给 PRD 流程加自动化门禁。

---

MIT License
