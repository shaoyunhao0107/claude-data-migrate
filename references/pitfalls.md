# 踩坑记录（每条都有实测依据）

## 1. 路径超 260 字符 → 解压静默丢文件

**实测**：把 884MB 迁移包解压到
`C:\Users\<user>\WorkBuddy\<session-dir>\migrate-dl\extracted\...`
（根路径约 110 字符），105 个深层文件（`data/desktop-app/local-agent-mode-sessions/...`，
单条相对路径 157–201 字符）**没有报错但没被解出来**。7z 退出码仍是 0。

换到 `C:\cm` 重解，同样命令，4587/4588 通过。

**对策**：解压与还原目录一律用短路径（`C:\cm`、`/tmp/cm`）；脚本内部对读写加
`\\?\` 前缀；`pack.py` 在输出路径 >60 字符时给警告。

## 2. 项目 `missing-folder`

**实测**：首次导入 446 项，20 个 projects 全部失败，`reason: "missing-folder"`。
WorkBuddy 导入"项目"时会解析项目的真实路径并检查是否存在，目录不在就整项拒绝。
**注意**：这些项目里的**会话内容仍然成功导入**（chats 与 projects 是两类条目），
所以不是数据丢失，但项目本身没建起来。

**对策**：`restore.py` 在还原后按 `path-map.json` 建**原绝对路径**空目录
（`D:\001wendang\aiyunweigittlab` 这种，不是新位置）。盘符不存在时打印
`subst D: <目标盘>:\_migrated\d` 供用户选择。

## 3. 技能自带虚拟环境 → `write-failed`

**实测**：`video-use` 技能 374MB，其中 `.venv` 占 372MB，导入报 `write-failed`。
同批其他 71 个技能全部成功。

**对策**：打包时排除 `.venv`/`venv`/`node_modules`/`__pycache__`/`*.egg-info`/`.git`/
`cache`/`blobs`/`shell-snapshots`，保留 `pyproject.toml`/`uv.lock`/`requirements.txt`/
`package.json`/`install.md`，并生成 `rebuild-env` 脚本（只生成命令，不自动执行）。

## 4. 标题到底存在哪

**实测**：433 个会话 jsonl 里 `type=="summary"` 的行数为 **0**——jsonl 不存标题。
Claude Code 界面上显示的标题来自三处：

| 来源 | 位置 | 数量（实测） |
|---|---|---|
| 用户手动命名 | `projects/<proj>/<sid>/custom-title.json` 的 `customTitle` | 191 |
| AI 生成 | 同级 `ai-title` 类文件 | 194 |
| 近期活跃会话 | `sessions/<pid>.json` 的 `name` | 6（本机，仅运行时） |
| 兜底 | 首条用户消息截断 | 48 |

WorkBuddy 导入时取的是**原始首条用户消息**（含 `<pasted_content>` 包装、换行、超长），
354 个导入会话里 212 个被污染（217 个超 60 字、105 个带换行、17 个带粘贴包装）。

**对策**：`apply_titles.py` 用 `session-titles.json`（三源合并，带 `titleSource`）回填；
`--only-real` 可跳过"首条消息兜底"那部分。

## 5. 子代理记录在旁边的目录里

每个 `<session-id>.jsonl` 同级还有一个 `<session-id>\` 目录，里面是
`subagents/agent-*.jsonl`（子代理会话）与 `custom-title.json`。
只拷 `*.jsonl` 会丢子代理内容与标题。实测 73 个子代理文件。

## 6. WorkBuddy 数据库位置与识别条件

- 库：`~/.workbuddy/workbuddy.db`（sqlite，表 `sessions`，字段 `title` / `custom_title`）
- 导入会话识别：`cwd LIKE '%claude-code-imported%'`
- 导入历史：`~/.workbuddy/data-import/<hash>/history/<runId>.json`，
  条目里 `state` 为 `committed` / `review` / `failed`，失败带 `reason`
- 已导入键记录：同目录 `imports.json` 的 `records`（成功）与 `attempts`（尝试过）

## 7. 看起来"哈希不符"但不是损坏

`data/skills/lingxi-sdp/assets/sdp_user_template.xlsx`：MANIFEST 记 10086B，
实际 16384B、文件头 `%TSD-Header-###%`——这本身是加密容器格式，不是 xlsx。
本机已有副本与包内**完全一致**，说明是源文件打包时的记录差异，不影响使用。
判断方法：比较"包内副本"与"本机副本"的 hash，一致即非传输损坏。

## 8. 大项目与总量参考

实测一台机器的数据规模，用来预估传输与耗时：

- 总量 2.1GB：`projects` 1.7GB / `skills` 380MB / `file-history` 14MB
- 433 会话 + 73 子代理 + 72 技能 + 28 项目，230k 行 jsonl
- 单项目最大 489MB、405MB；65 个 jsonl 超 5MB
- 打包 1.7GB → zip 884MB；解压约 3 分钟；还原（全部一致时）约 40 秒

## 9. 文件头 `%TSD-Header-###%` → 被加密客户端包装了

**实测**：在装了企业加密/DLP 客户端的机器上，同一份内容写到不同目录，结果不一样——
有的目录写出来是明文，有的被包成 `%TSD-Header-###%` 开头的容器（体积会变成 16KB 的整数倍）。
本次遇到的三个样本：`skills/lingxi-sdp/assets/sdp_user_template.xlsx`、
robocopy 重定向出来的日志、以及用 shell 写入 `C:/` 下临时目录的 jsonl。

**判断**：`head -c 16 file | od -c`，开头是 `% T S D - H e a d e r` 就是被包装了。
被包装的 .jsonl 会直接解析失败（`UnicodeDecodeError`），被包装的 .xlsx 打不开。

**对策**：
1. 换目录（用户目录 / 桌面 / 项目工作区通常在白名单内），或让 IT 把目标目录加进加密客户端白名单；
2. 用 Python 写入而不是 shell 重定向（本次 Python 写的文件都是明文）；
3. 打包前先做一次"落地校验"：随机抽 5 个 jsonl，首行能否 `json.loads` 成功。
   `pack.py` 的 `--selftest` 会间接暴露这个问题（会话数对不上）。
