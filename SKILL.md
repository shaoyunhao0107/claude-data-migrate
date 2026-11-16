---
name: claude-data-migrate
description: 把 Claude Code / Claude 桌面版的会话、项目、技能、标题完整迁移到另一台电脑，并在 WorkBuddy 里零失败导入。当用户说"换电脑搬迁 Claude 数据""导出我的 Claude 会话""备份 .claude 目录""迁移后标题变了/项目报 missing-folder/技能导入失败""claude-migrate 包"时使用。脚本会先自动发现数据根目录，不写死任何路径。
---

# Claude 数据迁移

把一台机器上的 Claude 数据搬到另一台，且让 WorkBuddy 导入不失败、标题可搜索。
**脚本只依赖 Python 3.8+ 标准库，Windows / macOS / Linux 通吃。**

## 先判断：需不需要这个 skill

| 场景 | 做法 |
|---|---|
| 数据本来就在这台机器，只是要导入 WorkBuddy | **不用本 skill**。直接 WorkBuddy → 设置 → 数据导入，失败再看 `references/import-diagnose.md` |
| 换电脑 / 换系统 / 换盘符 | 用本 skill 全流程 |
| 只补标题（导入后标题不对） | 只用 `scripts/apply_titles.py` |
| 只想修复某几个项目的 missing-folder | 只用 `restore.py` 的建目录逻辑，或 `references/pitfalls.md` §2 |

## 全流程（源机 → 目标机）

### 第一步：发现（源机执行）

```bash
python scripts/discover.py --json discover.json
```

自动探测：`CLAUDE_CONFIG_DIR` 环境变量 → CLI 安装位置 → 各系统常见目录 →
多用户/多盘（`C:\Users\*\\.claude`、`/home/*/.claude`、`\\wsl$\*`）。
同时解析出**每个项目的真实绝对路径**（优先取 jsonl 里的 `cwd` 字段，其次
`~/.claude.json` 的 projects 键，最后才解码目录名）和**标题三源**。

把打印出来的候选清单给用户确认主根目录。多个都有效时默认选会话数最多的。

### 第二步：打包（源机执行）

```bash
python scripts/pack.py --out C:\cm --selftest
# 可选：--root 指定根目录，--secrets 连凭证一起导出
```

必须用**短输出路径**（`C:\cm` 这种）。理由见铁律 1。

产出 `C:\cm\` 下：`data/`（projects、skills、sessions、file-history…）、`MANIFEST.json`、
`projects-index.json`、`session-titles.json`、`path-map.json`、`restore.py`、`apply_titles.py`、
`README-导入说明.md`、`SHA256SUMS.txt`。带 `--selftest` 会做一次假还原并比对数量。

> `restore.py` 和 `apply_titles.py` 都随包带走，所以**目标机不需要装本 skill**，只要有 Python 3.8+。

### 第三步：传输

整目录压成 zip / 7z（>2GB 分卷 1G）。凭证在 `SECRETS/`，不要跟主包一起传网盘。

### 第四步：还原（目标机执行）

```bash
# 解压到短路径！
cd C:\cm
python restore.py verify       # 必须通过；报缺失就换短路径重解，别继续
python restore.py --dry-run    # 看落点
python restore.py              # 正式还原 + 自动建项目空目录
```

`restore.py` 会：校验 sha256 → 合并复制（不覆盖，冲突存 `.imported`）→
**按 path-map 建每个项目的原绝对路径空目录** → 回写标题文件 → 盘符缺失打印 subst 方案。

### 第五步：导入 WorkBuddy + 回填标题（目标机执行）

先让用户打开 WorkBuddy → 设置 → 数据导入 → Claude Code，勾选全部。
导入完成后核对成功/失败数，失败的按 `references/import-diagnose.md` 处理。

然后回填真标题：

```bash
python scripts/apply_titles.py --titles C:\cm\session-titles.json --dry-run
python scripts/apply_titles.py --titles C:\cm\session-titles.json
```

改的是 `~/.workbuddy/workbuddy.db` 的 `sessions.title / custom_title`，脚本会先备份。
**告知用户重启 WorkBuddy 才会显示新标题。**

## 铁律（每一条都是踩过的坑）

1. **路径超 260 字符会静默丢文件**：解压/还原目录必须在短路径（如 `C:\cm`）。
   症状是 `verify` 报大量"缺失"但压缩包里明明有——换短路径重解即可，别怀疑包坏了。
2. **必须建项目原绝对路径空目录**，否则 WorkBuddy 导入报 `missing-folder`。
   建的是**原路径**（`D:\001wendang\xxx`），不是新位置；盘符不存在时用 subst 或让用户选落点。
3. **技能目录里的大依赖必须剥离**：`.venv`/`node_modules`/`__pycache__`/`*.egg-info`。
   典型事故：一个技能带 372MB `.venv`，导入直接 `write-failed`。保留
   `pyproject.toml`/`uv.lock`/`requirements.txt`/`package.json`/`install.md` 供重建。
4. **标题要三源合并**：`<session>/custom-title.json`（用户手动命名，最准）>
   `ai-title` > `sessions/*.json` 的 `name` > 首条用户消息兜底。
   jsonl 里**没有**标题字段——只翻 jsonl 会得出"标题没保存"的错误结论。
5. **每个 `<sid>.jsonl` 旁的 `<sid>\` 子目录要带上**（`subagents/` 子代理记录），漏了会话内容残缺。
6. **改 WorkBuddy 数据库前先备份**；`cwd LIKE '%claude-code-imported%'` 是识别导入会话的条件。
7. **凭证默认不还原**：`sessions/*.key`、`.claude.json`、桌面版 config 走 `SECRETS/`，
   需要时 `--secrets`，并提醒用户导入后轮换密钥。

## 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `verify` 报大量缺失 | 解压路径过长 | 换 `C:\cm` 重解 |
| 导入 `missing-folder` | 项目原路径没建 | 跑 `restore.py`（含建目录）或 `fix-project-paths` |
| 导入 `write-failed` | 技能体积过大 | 手工复制技能目录，排除 `.venv`/`node_modules`，补 `_user_meta.json` |
| 导入后标题是超长一段话 | WorkBuddy 用首条消息当标题 | 跑 `apply_titles.py` |
| 标题搜不到 | 同上 | 同上；`--only-real` 只应用人工/AI 标题 |
| 单文件哈希不符但大小不对 | 源文件本身是 `%TSD` 等加密容器 | 对比本机副本 hash，一致就不是传输损坏 |
| jsonl 解析报 `UnicodeDecodeError`、头部 `%TSD-Header` | 企业加密客户端包装了文件 | 换目录或用 Python 写入，见 pitfalls §9 |
| 会话数比源机少 | 该项目目录缺失导致会话被跳过 | 建目录后重新导入 |

## 参考

- `references/pitfalls.md` — 完整踩坑记录与根因（含实测数字）
- `references/import-diagnose.md` — WorkBuddy 导入失败如何读日志定位
- `references/bootstrap-prompt.md` — 目标机还没有本 skill 时，拿来装它的引导提示词
- `scripts/restore.py` — 还原引擎，已在真实迁移中验证（含假还原自检）
