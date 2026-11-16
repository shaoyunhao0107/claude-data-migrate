#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discover.py — 自动发现 Claude 数据根目录、项目真实路径、会话标题

仅标准库，跨平台。输出供 pack.py 消费。

用法：
  python discover.py                     # 自动发现并打印摘要
  python discover.py --json out.json     # 同时写出结构化结果
  python discover.py --root D:\\bak\\.claude [--merge]   # 指定根目录（--merge 合并多个根）
"""
import os, sys, json, glob, argparse, platform, hashlib

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

IS_WIN = platform.system() == "Windows"
HOME = os.path.expanduser("~")
SYSTEM = platform.system().lower()


def longpath(p):
    p = os.path.abspath(p)
    if IS_WIN and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


# ---------- 根目录发现 ----------

def candidate_roots():
    """按优先级返回候选根目录（去重，保留顺序）"""
    cands = []
    for env in ("CLAUDE_CONFIG_DIR", "CLAUDE_HOME", "ANTHROPIC_CONFIG_DIR"):
        v = os.environ.get(env)
        if v:
            cands.append(v)
    cands.append(os.path.join(HOME, ".claude"))
    if SYSTEM == "windows":
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [os.path.join(base, "Claude"), os.path.join(base, "AnthropicClaude")]
        # 其他用户 / 其他盘
        for drive in "CDEFGH":
            for up in glob.glob(f"{drive}:\\Users\\*"):
                cands.append(os.path.join(up, ".claude"))
    elif SYSTEM == "darwin":
        cands += [os.path.join(HOME, "Library", "Application Support", "Claude")]
        for up in glob.glob("/Users/*"):
            cands.append(os.path.join(up, ".claude"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
        cands += [os.path.join(xdg, "claude"),
                  os.path.join(HOME, ".local", "share", "claude")]
        for up in glob.glob("/home/*"):
            cands.append(os.path.join(up, ".claude"))
    # 去重保持顺序
    seen, out = set(), []
    for c in cands:
        if not c:
            continue
        k = os.path.normcase(os.path.abspath(c))
        if k in seen:
            continue
        seen.add(k)
        out.append(os.path.abspath(c))
    return out


def root_stats(root):
    """判断是否为有效根目录并返回统计"""
    if not os.path.isdir(root):
        return None
    projects = os.path.join(root, "projects")
    skills = os.path.join(root, "skills")
    n_sess = len(glob.glob(os.path.join(projects, "*", "*.jsonl")))
    n_skill = len([d for d in glob.glob(os.path.join(skills, "*")) if os.path.isdir(d)])
    score = n_sess * 10 + n_skill
    has_marker = any(os.path.exists(os.path.join(root, m))
                     for m in ("projects", "sessions", "settings.json", "skills"))
    if not has_marker:
        return None
    size = 0
    for dirpath, dirnames, filenames in os.walk(longpath(root)):
        for f in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    mtime = 0
    for dirpath, dirnames, filenames in os.walk(longpath(root)):
        for f in filenames:
            try:
                mtime = max(mtime, os.path.getmtime(os.path.join(dirpath, f)))
            except OSError:
                pass
    return {"path": root, "sessions": n_sess, "skills": n_skill, "sizeMB": round(size / 1048576, 1),
            "mtime": mtime, "score": score}


# ---------- 会话读取 ----------

def iter_lines(path, limit=None):
    try:
        with open(longpath(path), "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    return
                yield line
    except OSError:
        return


def session_meta(path):
    """返回 (首条用户消息, cwd, 用户消息数)"""
    first, cwd, n_user = None, None, 0
    for line in iter_lines(path, limit=400):
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not isinstance(o, dict):
            continue
        if cwd is None and o.get("cwd"):
            cwd = o["cwd"]
        if o.get("type") == "user" and not o.get("isSidechain") and not o.get("isMeta"):
            c = (o.get("message") or {}).get("content")
            texts = []
            if isinstance(c, str):
                texts = [c]
            elif isinstance(c, list):
                texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
            t = "\n".join(x for x in texts if x).strip()
            if t:
                n_user += 1
                if first is None:
                    first = " ".join(t.split())[:80]
    return first, cwd, n_user


# ---------- 标题三源 ----------

def collect_titles(root):
    """合并三处标题：custom-title.json > sessions/*.json name > ai-title* 兜底首条消息"""
    titles = {}

    def put(sid, name, src):
        if not sid or not name:
            return
        rank = {"custom-title": 3, "ai-title": 2, "session-json": 1, "first-user-message": 0}
        old = titles.get(sid)
        if old and rank.get(old["titleSource"], 0) >= rank.get(src, 0):
            return
        titles[sid] = {"sessionId": sid, "name": name.strip(), "titleSource": src}

    # 1) 会话子目录里的 custom-title.json / ai-title.json
    for pat, src in ((os.path.join(root, "projects", "*", "*", "custom-title.json"), "custom-title"),
                     (os.path.join(root, "projects", "*", "*", "ai-title.json"), "ai-title"),
                     (os.path.join(root, "projects", "*", "*", "title.json"), "ai-title")):
        for p in glob.glob(pat):
            try:
                d = json.load(open(longpath(p), encoding="utf-8"))
            except Exception:
                continue
            sid = os.path.basename(os.path.dirname(p))
            name = d.get("customTitle") or d.get("title") or d.get("name") if isinstance(d, dict) else None
            put(sid, name, src)

    # 2) sessions/*.json 的 name（仅近期活跃会话）
    for p in glob.glob(os.path.join(root, "sessions", "*.json")):
        try:
            d = json.load(open(longpath(p), encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict):
            put(d.get("sessionId"), d.get("name"), "session-json")

    # 3) 兜底：首条用户消息
    for p in glob.glob(os.path.join(root, "projects", "*", "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        if sid in titles:
            continue
        first, cwd, _ = session_meta(p)
        if first:
            titles[sid] = {"sessionId": sid, "name": first, "titleSource": "first-user-message",
                           "cwd": cwd}
    return titles


# ---------- 项目路径解析 ----------

def decode_project_dir(name):
    """目录名解码兜底：D--a-b → D:\\a\\b；-home-u-x → /home/u/x"""
    if len(name) > 2 and name[1] == "-" and name[0].isalpha():
        drive = name[0] + ":"
        rest = name[3:] if len(name) > 2 else ""
        return drive + "\\" + rest.replace("-", "\\")
    if name.startswith("-"):
        return "/" + name[1:].replace("-", "/")
    return name


def build_projects(root, titles):
    out = []
    for d in sorted(glob.glob(os.path.join(root, "projects", "*"))):
        if not os.path.isdir(d):
            continue
        enc = os.path.basename(d)
        files = glob.glob(os.path.join(d, "*.jsonl"))
        cwd, src = None, "decoded"
        for f in files:
            first, c, _ = session_meta(f)
            if c:
                cwd, src = c, "cwd"
                break
        if not cwd:
            cwd = decode_project_dir(enc)
        size = 0
        for dirpath, dirnames, filenames in os.walk(longpath(d)):
            for f in filenames:
                try:
                    size += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        out.append({
            "encoded": enc, "path": cwd, "source": src,
            "exists": os.path.isdir(cwd),
            "sessions": len(files),
            "sizeMB": round(size / 1048576, 2),
            "platform": "win" if IS_WIN else SYSTEM,
            "drive": os.path.splitdrive(cwd)[0] if IS_WIN else "/",
            "titlesRecovered": sum(1 for f in files if os.path.basename(f)[:-6] in titles),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="发现 Claude 数据根目录 / 项目路径 / 标题")
    ap.add_argument("--root", default=None, help="指定根目录（默认自动发现）")
    ap.add_argument("--json", default=None, help="把结构化结果写入该文件")
    args = ap.parse_args()

    if args.root:
        roots = [os.path.abspath(args.root)]
    else:
        roots = candidate_roots()

    found = []
    for r in roots:
        st = root_stats(r)
        if st:
            found.append(st)
    if not found:
        print("未发现任何 Claude 数据目录。请用 --root 指定。")
        return 2

    found.sort(key=lambda x: (-x["sessions"], -x["mtime"]))
    print("== 候选根目录 ==")
    for i, st in enumerate(found):
        tag = "主" if i == 0 else ("备" if st["sessions"] else "忽略")
        print(f"  [{tag}] {st['path']} | 会话 {st['sessions']} | 技能 {st['skills']} | "
              f"{st['sizeMB']}MB | {tag and ''}")

    root = found[0]["path"]
    title_map = collect_titles(root)
    projects = build_projects(root, title_map)

    print(f"\n== 选定根目录 ==\n  {root}")
    print(f"  会话 {len(glob.glob(os.path.join(root, 'projects', '*', '*.jsonl')))} | "
          f"项目 {len(projects)} | 技能 {found[0]['skills']} | 标题 {len(title_map)}")
    miss = [p for p in projects if not p["exists"]]
    print(f"  项目路径不存在（需建占位目录）: {len(miss)}")
    for p in miss[:5]:
        print(f"    - {p['path']}")

    data = {"root": root, "candidates": found, "projects": projects,
            "titles": list(title_map.values()), "platform": SYSTEM}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"\n结果已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
