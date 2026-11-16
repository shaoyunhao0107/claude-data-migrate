# 引导提示词（目标机还没有这个 skill 时用）

skill 没法自己给自己打包。目标机如果只有 Claude Code / WorkBuddy、没有本 skill，
把下面这段连同 `claude-data-migrate-skill.zip` 一起给它——这段的作用只有一个：
把 skill 装到正确位置，然后让 agent 按 SKILL.md 自己跑。

（只需改两处：`<skill 包路径>` 和方向：`打包` / `还原`）

```text
我这台电脑上有 claude-data-migrate 这个 skill 的压缩包：<skill 包路径>。
请先把它解压到技能目录，再按里面的 SKILL.md 执行：

1) 解压（按当前系统选一个）：
   Windows: expand-archive "<skill 包路径>" "$env:USERPROFILE\.claude\skills" -Force
   或       robocopy 解压后的 claude-data-migrate 目录到  %USERPROFILE%\.claude\skills\claude-data-migrate
   mac/linux: mkdir -p ~/.claude/skills && unzip -o "<skill 包路径>" -d ~/.claude/skills
   （如果本机主要用 WorkBuddy，也同时复制一份到 ~/.workbuddy/skills/）

2) 确认目录结构是：claude-data-migrate/SKILL.md + scripts/ + references/
   然后完整读一遍 SKILL.md 和 references/pitfalls.md。

3) 执行【打包】方向（源机）：
   python scripts/discover.py --json discover.json
   → 把候选根目录清单打印给我，等我确认主根目录
   → python scripts/pack.py --out C:\cm --selftest
   → 报告包体积、文件数、会话/技能/项目/标题数量，以及自检是否通过

   执行【还原】方向（目标机）：
   → python restore.py verify（不通过就停下，先换短路径重解）
   → python restore.py --dry-run
   → python restore.py
   → python scripts/apply_titles.py --titles <包目录>\session-titles.json
   → 对账：会话数/技能数/项目数/标题数，与包内 README 逐项对比，不一致要明确说出来

4) 铁律（SKILL.md 里有详细版，先记住这几条）：
   - 解压和还原一律用短路径（如 C:\cm），路径超 260 字符会静默丢文件
   - 项目原绝对路径必须建空目录，否则导入报 missing-folder
   - 技能里的 .venv/node_modules/__pycache__ 必须剥离，否则 write-failed
   - 标题三源合并，jsonl 里没有标题字段
   每完成一步先汇报结果，不要一口气全做完再说话。
```

## 如果不方便传文件

退一步用纯提示词版本（质量会差一些，因为脚本要现场重写）：
把 `references/pitfalls.md` 全文贴给目标机的 agent，再补一句
"按这份踩坑记录自己写脚本执行，先跑发现、把清单给我确认"。
