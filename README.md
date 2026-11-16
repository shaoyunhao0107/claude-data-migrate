# claude-data-migrate

把 **Claude Code / Claude 桌面版** 的会话、项目、技能、标题完整迁移到另一台电脑，并让 **WorkBuddy 数据导入零失败、标题保持可搜索**。

> 只用 Python 3.8+ 标准库，Windows / macOS / Linux 通吃。不写死任何路径，脚本自己发现数据在哪。

---

## 为什么需要它

直接拷 `.claude` 目录再导入，几乎一定会在下面三处翻车——这三条都是实测踩出来的：

| 现象 | 真实原因 |
|---|---|
| 解压后 `verify` 报一大堆「文件缺失」，压缩包里明明有 | 路径超过 **260 字符**，7z/资源管理器**不报错**但没解出来（实测漏 105 个文件，退出码仍是 0） |
| WorkBuddy 导入报 `write-failed` | 某个技能自带 `.venv`（实测一个技能 372MB），复制失败 |
| 导入报 `missing-folder`，项目全红 | 项目记录了**原机的绝对路径**（如 `D:\aliyun`），新机器没这个目录，整项被拒 |
| 导入后标题变成一大段话，搜不到 | Claude Code 的标题存在 `custom-title.json` / `ai-title` / `sessions/*.json`，**不在会话 jsonl 里**；WorkBuddy 拿首条用户消息当标题 |

这个 skill 就是把这四条坑固化进脚本：短路径校验、依赖剥离、原路径预建、标题三源合并。

---

## 安装

### 方式一：直接 clone 到技能目录

```bash
# Claude Code（两个目录都放一份，WorkBuddy 也认）
git clone https://github.com/shaoyunhao0107/claude-data-migrate.git ~/.claude/skills/claude-data-migrate
cp -r ~/.claude/skills/claude-data-migrate ~/.workbuddy/skills/
```

```powershell
# Windows PowerShell
git clone https://github.com/shaoyunhao0107/claude-data-migrate.git "$env:USERPROFILE\.claude\skills\claude-data-migrate"
Copy-Item "$env:USERPROFILE\.claude\skills\claude-data-migrate" "$env:USERPROFILE\.workbuddy\skills\" -Recurse
```

### 方式二：不想用 git

下载 ZIP → 解压 → 把整个 `claude-data-migrate` 文件夹丢进 `~/.claude/skills/`（Windows：`%USERPROFILE%\.claude\skills\`）。

装好后让 agent 读一遍 `SKILL.md` 即可自动触发。

---

## 快速开始：A 机 → B 机

```
A 机（旧）：discover 确认根目录 → pack --selftest → 得到 C:\cm
                                              ↓ 拷到 B 机，解压到 C:\cm
B 机（新）：verify → dry-run → restore → WorkBuddy 导入 → apply_titles → 重启
```

### A 机（数据源）

```bash
cd ~/.claude/skills/claude-data-migrate/scripts

# 1) 发现：会打印候选根目录（路径|会话数|技能数|体积|判定），多个时选会话最多的
python discover.py --json discover.json

# 2) 打包：输出目录必须短！这一步会做假还原自检
python pack.py --root ~/.claude --out C:\cm --selftest

# 可选
#   --secrets   连凭证一起导出到 SECRETS/（导入后记得立刻轮换密钥）
#   --label     包名标签，默认主机名
```

产出：

```
C:\cm\
├── data\                  projects / skills / sessions / file-history …
├── MANIFEST.json          每个文件的 sha256
├── projects-index.json    项目清单 + 原绝对路径 + 是否存在
├── path-map.json          每个路径在目标机的落地方案
├── session-titles.json    全量真标题（能按原习惯搜索的关键）
├── restore.py             还原引擎（随包带走，目标机不用装 skill）
├── apply_titles.py        标题回填
├── README-导入说明.md
└── SHA256SUMS.txt
```

把 `C:\cm` 压成 zip，拷到 B 机，**解压到 B 机的 `C:\cm`**（短路径，别放进深层目录）。

### B 机（目标机）

```bash
cd C:\cm\claude-migrate-<...>

python restore.py verify       # ① 校验 sha256，缺失 0 才继续
python restore.py --dry-run    # ② 演练，看落点
python restore.py              # ③ 正式还原 + 自动建项目空目录
```

> 包里自带 `restore.py` 和 `apply_titles.py`，**B 机不装 skill 也能跑完全流程**，只要有 Python 3.8+。

然后手动：**WorkBuddy → 设置 → 数据导入 → Claude Code → 勾选全部类别导入**。

导入完成后回填标题：

```bash
python apply_titles.py --titles session-titles.json --dry-run   # 先看要改哪些
python apply_titles.py --titles session-titles.json             # 真改
```

脚本会先备份 `~/.workbuddy/workbuddy.db`，改的是 `sessions.title / custom_title`。
**重启 WorkBuddy 后生效。**

---

## 脚本说明

| 脚本 | 作用 | 常用参数 |
|---|---|---|
| `discover.py` | 自动发现数据根目录、项目真实路径、标题三源 | `--json <out>` |
| `pack.py` | 清洗打包 + MANIFEST + 假还原自检 | `--root` `--out` `--secrets` `--selftest` `--label` |
| `restore.py` | 校验 + 合并还原 + 建项目空目录 | `verify` / `--dry-run` / `--target` / `--secrets` |
| `apply_titles.py` | 把真标题回填到 WorkBuddy 数据库 | `--titles` `--root` `--db` `--dry-run` `--only-real` |

`apply_titles.py` 是幂等的：第二次运行会识别"已经是真名"的会话并跳过，不会二次污染。

### 根目录发现顺序

1. `CLAUDE_CONFIG_DIR` 环境变量
2. `where claude` / `which claude` 反推
3. 各系统常见位置：`~/.claude`、`%APPDATA%\Claude`、`~/Library/Application Support/Claude`、`${XDG_CONFIG_HOME:-~/.config}/claude`
4. 多用户多盘：`C:\Users\*\.claude`、`/home/*/.claude`、`\\wsl$\*\home\*`
5. 用户目录下限深度搜索

### 项目真实路径的来源（按可信度）

1. 会话 jsonl 每行的 **`cwd` 字段**（最权威，实测每个文件都有）
2. `~/.claude.json` 的 `projects` 键
3. 解码目录名（`D--a-b` → `D:\a\b`）—— 仅兜底

### 标题的三源合并

```
<session>/custom-title.json  （用户手动命名，最准）
  > ai-title                 （Claude 自动生成）
  > sessions/*.json 的 name  （近期活跃会话的注册名）
  > 首条用户消息               （兜底）
```

> ⚠️ **会话 jsonl 里没有标题字段**。只翻 jsonl 会得出"标题没保存"的错误结论——这是本项目最大的一个弯路。

---

## 铁律（每条都是踩过的坑）

1. **解压/还原一律用短路径**（`C:\cm`）。超 260 字符会静默丢文件，`verify` 报缺失时先换短路径重解，别怀疑包坏了。
2. **必须建项目的原绝对路径空目录**，不是新位置。盘符不存在时用 `subst D: C:\_migrated\D`，或让用户选替代落点。
3. **技能里的大依赖必须剥离**：`.venv` / `venv` / `node_modules` / `__pycache__` / `*.egg-info`。保留 `pyproject.toml` / `uv.lock` / `requirements.txt` / `package.json` / `install.md` 供重建。
4. **每个 `<sid>.jsonl` 旁的 `<sid>\` 子目录要带上**（`subagents/` 子代理记录），漏了会话内容残缺。
5. **改 WorkBuddy 数据库前先备份**；识别导入会话的条件是 `cwd LIKE '%claude-code-imported%'`。
6. **凭证默认不还原**：`sessions/*.key`、`.claude.json`、桌面版 config 走 `SECRETS/`，需要时加 `--secrets`，之后立即轮换密钥。
7. **还原是合并复制，不是覆盖**：同名但内容不同的文件会另存 `.imported`，源数据全程只读。

---

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `verify` 报大量缺失 | 解压路径过长 | 换 `C:\cm` 重解 |
| 导入 `missing-folder` | 项目原路径没建 | 跑 `restore.py`（含建目录） |
| 导入 `write-failed` | 技能体积过大 | 手工复制技能目录，排除 `.venv`/`node_modules`，补 `_user_meta.json` |
| 标题是超长一段话 | WorkBuddy 用首条消息当标题 | 跑 `apply_titles.py` |
| 标题搜不到 | 同上 | 同上；`--only-real` 只应用人工/AI 标题 |
| 单个文件哈希不符但大小异常 | 源文件本身是 `%TSD` 等加密容器 | 对比本机副本 hash，一致就不是传输损坏 |
| jsonl 报 `UnicodeDecodeError`，头部 `%TSD-Header` | 企业加密/DLP 客户端包装了文件 | 换目录或用 Python 写入 |
| 会话数比源机少 | 该项目目录缺失导致会话被跳过 | 建目录后重新导入 |

导入失败时，失败原因在：

```
%USERPROFILE%\.workbuddy\data-import\<hash>\history\*.json
```

读里面 `state != committed` 的条目的 `reason` 字段。详见 `references/import-diagnose.md`。

---

## 目录结构

```
claude-data-migrate/
├── SKILL.md                     决策树 + 全流程 + 铁律 + 故障表
├── scripts/
│   ├── discover.py              自动发现根目录 / 项目路径 / 标题
│   ├── pack.py                  清洗打包 + MANIFEST + 自检
│   ├── restore.py               还原引擎（含假还原验证）
│   ├── apply_titles.py          标题回填（幂等）
│   └── fix-project-paths.*      项目路径修复（ps1 / sh）
└── references/
    ├── pitfalls.md              9 条踩坑记录，带实测数字
    ├── import-diagnose.md       WorkBuddy 导入失败诊断法
    └── bootstrap-prompt.md      目标机没有本 skill 时的引导提示词
```

---

## 适用范围

- ✅ Claude Code → 另一台电脑的 Claude Code
- ✅ Claude Code → WorkBuddy（数据导入）
- ✅ 只修标题、只修 `missing-folder`（单跑对应脚本）
- ❌ 不是备份工具（没有增量/定时）；不含云端会话（网页版 claude.ai 的对话）

---

## License

MIT
