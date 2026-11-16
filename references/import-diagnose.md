# WorkBuddy 导入失败 · 定位方法

导入结束后如果显示"N 失败"，不要去猜——直接读导入日志。

## 一、日志在哪

```
~/.workbuddy/data-import/<64位hash>/history/<runId>.json
~/.workbuddy/data-import/<64位hash>/imports.json
```

- `history/<runId>.json`：一次导入的完整结果，`items[]` 每条有
  `state`（`committed` 成功 / `review` 待确认 / `failed` 失败）、`kind`
  （`chats`/`skills`/`projects`）、`target`（WorkBuddy 会话 id）、`warnings`
- `imports.json`：`records` 记已成功的键，`attempts` 记尝试过的键
  ——**这就是"之前几百条，现在 0 条可导入"的原因**：尝试过的都记下了，扫描时不再算新增

## 二、一条命令看全貌

```bash
python - <<'EOF'
import json, glob, os
from collections import Counter
for h in glob.glob(os.path.expanduser('~/.workbuddy/data-import/*/history/*.json')):
    d = json.load(open(h, encoding='utf-8'))
    items = d['items']
    print(os.path.basename(h), '| state:', d.get('state'), '| 总项:', len(items))
    print('  按类型/状态:', dict(Counter((i['kind'], i['state']) for i in items)))
    for i in items:
        if i['state'] != 'committed':
            print(f"  [{i['state']}] {i['kind']}: {i.get('name','')[:40]} → {i.get('reason','')}")
EOF
```

## 三、reason 对照表

| reason | 含义 | 处理 |
|---|---|---|
| `missing-folder` | 项目原路径不存在 | 建占位目录（`restore.py` 或 `fix-project-paths`）后重试该项 |
| `write-failed` | 写入目标失败，多见于技能体积过大 | 手工复制技能目录，排除 `.venv`/`node_modules`，补 `_user_meta.json` |
| 其它 | —— | 原样贴出给用户，不要猜测 |

## 四、判断"是不是真的丢数据"

用日志里的 `kind` 和 `state` 交叉统计：

- `chats` 全部 `committed` → **会话内容没丢**，即使用户觉得"导入失败"（失败的可能只是 projects）
- `projects` 全部 `review`/`failed` 且 `reason=missing-folder` → 只是项目载体没建，
  会话仍然在
- `attempts` 有 key 但 `records` 没有 → 该条从未成功过，值得重试

## 五、重试前必须先满足前置条件

WorkBuddy 不会因为"再点一次"就绕过检查：

1. 项目路径存在（同一台机器上 `Test-Path` 为 True）
2. 技能目录可写、体积合理
3. 目标会话/项目没有被同名占用

条件不满足时重试一定失败，且会再次写入 `attempts`，让界面继续显示 "0 项可导入"。

## 六、技能手工补装（`write-failed` 兜底）

```bash
# 从源目录复制，排除重型依赖
robocopy "源\skills\<名字>" "%USERPROFILE%\.workbuddy\skills\<名字>" /E \
  /XD .venv node_modules __pycache__ .git *.egg-info /XF *.pyc
# 补元数据（与自动导入一致）
echo {"name":"<名字>","installedAt":<毫秒时间戳>,"source":"userImport"} > "%USERPROFILE%\.workbuddy\skills\<名字>\_user_meta.json"
```
