#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apply_titles.py — 把 Claude 原始标题回填到 WorkBuddy 会话

WorkBuddy 导入 Claude Code 会话时，标题 = 原始首条用户消息（含 <pasted_content> 包装、
换行、超长），与 Claude Code 里看到的不一致。本脚本用 session-titles.json 的真标题覆盖。

匹配方式：WorkBuddy 会话的 title 与某条 jsonl 的首条用户消息前缀一致 → 认定为同一会话。

用法：
  python apply_titles.py --titles session-titles.json --root ~/.claude --dry-run
  python apply_titles.py --titles session-titles.json --root ~/.claude
  python apply_titles.py --root ~/.claude --only-real   # 不用 titles 文件，只取本地 custom-title/ai-title
"""
import os, sys, json, glob, sqlite3, argparse, shutil, time, platform, re

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

IS_WIN = platform.system() == "Windows"
HERE = os.path.dirname(os.path.abspath(__file__))


def longpath(p):
    p = os.path.abspath(p)
    if IS_WIN and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def default_db():
    base = os.environ.get("WORKBUDDY_HOME") or os.path.join(os.path.expanduser("~"), ".workbuddy")
    return os.path.join(base, "workbuddy.db")


def default_root():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def first_user(path):
    try:
        with open(longpath(path), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not isinstance(o, dict) or o.get("type") != "user":
                    continue
                if o.get("isSidechain") or o.get("isMeta"):
                    continue
                c = (o.get("message") or {}).get("content")
                texts = []
                if isinstance(c, str):
                    texts = [c]
                elif isinstance(c, list):
                    texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                t = "\n".join(x for x in texts if x).strip()
                if t:
                    return " ".join(t.split())[:80]
    except OSError:
        return None
    return None


def load_titles(args):
    """返回 {sessionId: {name, titleSource}}"""
    if args.titles and os.path.exists(args.titles):
        d = json.load(open(longpath(args.titles), encoding="utf-8"))
        items = d.get("sessions") if isinstance(d, dict) else d
        return {i["sessionId"]: i for i in items if i.get("sessionId") and i.get("name")}
    # 从本地数据目录直接读
    sys.path.insert(0, HERE)
    from discover import collect_titles
    return collect_titles(args.root)


def main():
    ap = argparse.ArgumentParser(description="回填 Claude 原始标题到 WorkBuddy")
    ap.add_argument("--titles", default=None, help="session-titles.json（缺省则从 --root 现读）")
    ap.add_argument("--root", default=None, help="Claude 数据根目录")
    ap.add_argument("--db", default=None, help="WorkBuddy 数据库（默认 ~/.workbuddy/workbuddy.db）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-real", action="store_true", help="只应用 custom-title / ai-title，忽略首条消息")
    args = ap.parse_args()
    args.root = args.root or default_root()
    db_path = args.db or default_db()

    if not os.path.exists(db_path):
        print(f"找不到 WorkBuddy 数据库: {db_path}")
        return 2

    titles = load_titles(args)
    if not titles:
        print("没有可用标题")
        return 2

    # 建首条消息 → sessionId 索引
    by_first = {}
    for p in glob.glob(os.path.join(args.root, "projects", "*", "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        if sid not in titles:
            continue
        f = first_user(p)
        if f:
            by_first.setdefault(f, []).append(sid)

    # 复制数据库后再改（WorkBuddy 运行中也能安全写，重启生效）
    bak = f"{db_path}.bak-titles-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(longpath(db_path), longpath(bak))
    print(f"数据库备份: {bak}")

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    rows = cur.execute("SELECT id, title FROM sessions WHERE cwd LIKE '%claude-code-imported%'").fetchall()

    # 幂等：标题已经是真名的，直接算一致
    real_names = {" ".join(v["name"].split()) for v in titles.values()}

    plan, miss, same = [], 0, 0
    for sid_db, title in rows:
        tn = " ".join((title or "").split())[:80]
        if tn in real_names:
            same += 1
            continue
        cands = by_first.get(tn) or [v[0] for k, v in by_first.items() if k.startswith(tn[:40]) and len(by_first[k]) == 1]
        if not cands:
            # 已是真名但格式略有差异（截断/省略号），按前缀再试一次
            cands = [k for n in real_names if n.startswith(tn[:20]) for k in titles if titles[k]["name"] == n][:1]
        if not cands:
            miss += 1
            continue
        src = titles.get(cands[0])
        if not src:
            miss += 1
            continue
        if args.only_real and src.get("titleSource") == "first-user-message":
            continue
        name = src["name"].strip()
        if name == tn.strip():
            same += 1
            continue
        plan.append((sid_db, title, name, src.get("titleSource")))

    print(f"导入会话 {len(rows)} | 匹配 {len(rows)-miss} | 未匹配 {miss} | 已一致 {same} | 待更新 {len(plan)}")
    if args.dry_run:
        for _, old, new, src in plan[:15]:
            print(f"  [{src}] {new}   (旧: {(old or '')[:40]})")
        print("\n[dry-run] 未写入")
        con.close()
        return 0

    n = 0
    for sid_db, old, new, src in plan:
        cur.execute("UPDATE sessions SET title=?, custom_title=? WHERE id=?", (new, new, sid_db))
        n += cur.rowcount
    con.commit()
    print(f"已更新 {n} 条。重启 WorkBuddy 生效。")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
