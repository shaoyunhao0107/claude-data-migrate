#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude 迁移包 · 还原引擎（Python 3.8+，仅标准库，跨平台）

用法：
  python restore.py                      # 还原到默认位置（CLAUDE_CONFIG_DIR 或 ~/.claude）
  python restore.py --target D:\\cc       # 还原到指定位置
  python restore.py --dry-run            # 只演练，不写盘
  python restore.py --secrets            # 额外还原 SECRETS（凭证，默认不还原）
  python restore.py --no-paths           # 不创建项目原绝对路径空目录
  python restore.py verify               # 只校验包完整性（sha256）
  python restore.py fix-paths --target D:\\cc --old D:\\ --new E:\\
  python restore.py list                 # 列出包内项目与目标路径映射
"""
import os, sys, json, hashlib, shutil, argparse, subprocess, platform, time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = platform.system() == "Windows"
MANIFEST = os.path.join(HERE, "MANIFEST.json")
PATHMAP = os.path.join(HERE, "path-map.json")
INDEX = os.path.join(HERE, "projects-index.json")

# 跨系统根替换表（默认 Windows→Windows 为恒等映射；其他平台按需改这里）
CROSS_ROOT = {
    ("windows", "darwin"): [(r"C:\Users", "/Users"), ("\\", "/"), (":\\", "/")],
    ("windows", "linux"): [(r"C:\Users", "/home"), ("\\", "/"), (":\\", "/")],
    ("windows", "windows"): [],
}


def log(msg):
    print(msg, flush=True)


def winpath(p):
    """超长路径加 \\\\?\\ 前缀（仅 Windows 绝对路径）"""
    if IS_WIN and len(p) > 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(p)
    return p


def sha256_file(p):
    h = hashlib.sha256()
    with open(winpath(p), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(p, default=None):
    if not os.path.isfile(p):
        return default
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def default_target():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude")


# ---------------------------------------------------------------- verify
def cmd_verify(args):
    man = load_json(MANIFEST)
    if not man:
        log("! MANIFEST.json 缺失，无法校验")
        return 2
    files = man["files"] if isinstance(man, dict) and "files" in man else man
    bad, missing, ok = [], [], 0
    for e in files:
        p = os.path.join(HERE, e["path"].replace("/", os.sep))
        if not os.path.isfile(p):
            missing.append(e["path"])
            continue
        if sha256_file(p) != e["sha256"]:
            bad.append(e["path"])
        else:
            ok += 1
    log("校验：通过 %d / %d，缺失 %d，哈希不符 %d" % (ok, len(files), len(missing), len(bad)))
    for x in missing[:10]:
        log("  缺失: " + x)
    for x in bad[:10]:
        log("  不符: " + x)
    if missing or bad:
        log("! 包不完整，请重新拷贝迁移包（切勿继续导入）")
        return 1
    log("OK 包完整性校验通过")
    return 0


# ---------------------------------------------------------------- list
def cmd_list(args):
    idx = load_json(INDEX, {})
    pm = load_json(PATHMAP, {})
    projs = idx.get("projects", []) if isinstance(idx, dict) else idx
    tj = load_json(os.path.join(HERE, "session-titles.json"), {})
    log("session-titles: %d 条" % len(tj.get("sessions", [])))
    log("%-52s %6s %6s %s" % ("项目目录", "会话", "MB", "还原目标"))
    for p in projs:
        tgt = (pm.get("map", {}) or {}).get(p["dir"], {}).get("target", p.get("path", ""))
        log("%-52s %6d %6.1f %s" % (p["dir"], p.get("sessions", 0), p.get("sizeMB", 0), tgt))
    return 0


# ---------------------------------------------------------------- restore
def map_rel(rel):
    """包内相对路径 -> 目标根下的相对路径"""
    r = rel.replace("\\", "/")
    if r.startswith("data/projects/"):
        return "projects/" + r[len("data/projects/"):]
    if r.startswith("data/skills/"):
        return "skills/" + r[len("data/skills/"):]
    if r == "data/settings.json.reference":
        return "settings.json"
    if r == "data/settings.json.pre-codex.bak.reference":
        return "settings.json.pre-codex.bak"
    if r == "data/CLAUDE.md":
        return "CLAUDE.md"
    if r.startswith("data/desktop-app/"):
        return None
    if r.startswith("data/"):
        return r[len("data/"):]
    if r == "session-titles.json":
        return "session-titles.json"
    if r.startswith("SECRETS/"):
        return None
    return None


def ensure_dir(p):
    p = winpath(p)
    if not os.path.isdir(p):
        os.makedirs(p, exist_ok=True)


def subst_hint(path):
    """盘符不存在时给出 subst 建议"""
    if not IS_WIN:
        return None
    drive = os.path.splitdrive(path)[0]
    if not drive:
        return None
    if os.path.exists(drive + "\\"):
        return None
    return ("目标机无 %s 盘。方案：先 `subst %s D:\\mnt-%s`（把任意本地目录挂成该盘符），"
            "再重跑本脚本；或直接 `python restore.py fix-paths --old %s\\ --new <新根>` 改写为实际盘符。"
            % (drive, drive, drive[0].lower(), drive))


def create_project_paths(target, dry):
    """阶段5：先建原绝对路径空目录，否则 Claude 打开项目会 missing-folder"""
    pm = load_json(PATHMAP, {})
    entries = (pm.get("map") or {})
    made, exist, manual, blocked = [], [], [], []
    for name, e in entries.items():
        p = e.get("target") or e.get("original")
        if not p:
            continue
        p = p.replace("/", os.sep) if IS_WIN else p
        if os.path.isdir(p):
            exist.append(p)
            continue
        h = subst_hint(p)
        if h:
            blocked.append((p, h))
            continue
        parent = os.path.dirname(p)
        try:
            if not dry:
                os.makedirs(p, exist_ok=True)
            made.append(p)
        except PermissionError:
            manual.append((p, "权限不足：建议改落到 %s" % os.path.join(os.path.expanduser("~"), "projects",
                                                                     os.path.basename(p))))
        except OSError as ex:
            manual.append((p, "创建失败(%s)：建议改落到用户目录" % ex))
        if parent and not os.path.isdir(parent):
            try:
                if not dry:
                    os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
    # project-files 目录用 junction / 软链
    links = []
    for name, e in entries.items():
        p = (e.get("target") or "").replace("/", os.sep)
        pf = os.path.join(p, "project-files") if p else ""
        if pf and os.path.isdir(pf) and e.get("project_files"):
            links.append(pf)
    return {"created": made, "existing": exist, "manual": manual,
            "blocked": blocked, "junctions": links}


def do_restore(args):
    man = load_json(MANIFEST)
    if not man:
        log("! 找不到 MANIFEST.json，请确认在迁移包根目录运行")
        return 2
    files = man["files"] if isinstance(man, dict) and "files" in man else man
    target = os.path.abspath(os.path.expanduser(args.target or default_target()))
    dry = args.dry_run
    log("== Claude 迁移包还原 ==")
    log("包路径 : %s" % HERE)
    log("目标   : %s%s" % (target, "  (dry-run)" if dry else ""))

    # 预检
    free = shutil.disk_usage(os.path.dirname(target) or ".").free
    need = sum(e["bytes"] for e in files) + 50 * 1024 * 1024
    log("需要空间: %.0f MB / 可用 %.0f MB" % (need / 1048576, free / 1048576))
    if free < need:
        log("! 磁盘空间不足，已中止（先清理目标盘）")
        return 3

    if not dry:
        ensure_dir(target)
    copied = skipped = failed = 0
    skipped_list = []
    for e in files:
        rel = e["path"]
        if rel.startswith("SECRETS/") and not args.secrets:
            continue
        r = map_rel(rel)
        if not r:
            continue
        dst = os.path.join(target, r.replace("/", os.sep))
        src = os.path.join(HERE, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            failed += 1
            log("  缺失: " + rel)
            continue
        if os.path.exists(dst):
            # 已存在则比对，相同跳过，不同则保留原文件并另存
            try:
                if os.path.getsize(dst) == e["bytes"] and sha256_file(dst) == e["sha256"]:
                    skipped += 1
                    continue
            except OSError:
                pass
            alt = dst + ".imported"
            if not dry:
                ensure_dir(os.path.dirname(dst))
                shutil.copy2(winpath(src), winpath(alt))
            skipped_list.append(os.path.relpath(alt, target))
            skipped += 1
            continue
        if not dry:
            ensure_dir(os.path.dirname(dst))
            shutil.copy2(winpath(src), winpath(dst))
            if sha256_file(dst) != e["sha256"]:
                failed += 1
                log("  哈希不符: " + rel)
                continue
        copied += 1

    # 桌面版会话回到 %APPDATA%
    if IS_WIN and not args.no_desktop:
        desk_src = os.path.join(HERE, "data", "desktop-app", "local-agent-mode-sessions")
        if os.path.isdir(desk_src):
            dst = os.path.join(os.environ.get("APPDATA", ""), "Claude", "local-agent-mode-sessions")
            if not dry:
                ensure_dir(os.path.dirname(dst))
                if not os.path.isdir(dst):
                    shutil.copytree(winpath(desk_src), winpath(dst))
            log("桌面版会话 -> %s" % dst)

    # 标题回写：custom-title 类按原名补回，避免导入后标题丢失
    title_fixed = 0
    tj = load_json(os.path.join(HERE, "session-titles.json"), {})
    if tj and not dry:
        for t in tj.get("sessions", []):
            if t.get("titleSource") != "custom-title":
                continue
            d = os.path.join(target, "projects", t["encoded"], t["sessionId"])
            ct = os.path.join(d, "custom-title.json")
            if os.path.isfile(ct):
                continue
            try:
                ensure_dir(d)
                with open(ct, "w", encoding="utf-8") as f:
                    json.dump({"customTitle": t["name"]}, f, ensure_ascii=False)
                title_fixed += 1
            except OSError:
                pass
    if title_fixed:
        log("已按原名回写 %d 个会话标题" % title_fixed)

    # 阶段5 路径落地
    pathres = {"created": [], "existing": [], "manual": [], "blocked": []}
    if not args.no_paths:
        pathres = create_project_paths(target, dry)

    # 导入报告
    rep = {"target": target, "dry_run": dry, "copied": copied, "skipped": skipped,
           "titles_fixed": title_fixed,
           "failed": failed, "conflicts_renamed": skipped_list[:50],
           "paths_created": pathres["created"], "paths_existing": pathres["existing"],
           "paths_manual": pathres["manual"], "paths_blocked": pathres["blocked"],
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not dry:
        with open(os.path.join(target, "migrate-import-log.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)

    log("---")
    log("已还原 %d 个文件，跳过 %d（已存在且一致/改名 .imported），失败 %d" % (copied, skipped, failed))
    log("项目路径：新建 %d，已存在 %d，需人工 %d，盘符缺失 %d"
        % (len(pathres["created"]), len(pathres["existing"]),
           len(pathres["manual"]), len(pathres["blocked"])))
    for p, h in pathres["blocked"][:5]:
        log("  [盘符] %s\n         %s" % (p, h))
    for p, h in pathres["manual"][:5]:
        log("  [人工] %s -> %s" % (p, h))
    if not args.secrets:
        log("SECRETS 未还原（如需：python restore.py --secrets，之后请立即轮换密钥）")
    log("下一步：运行 fix-project-paths 把会话里的 cwd 指向新路径；再运行 rebuild-env 重装依赖。")
    return 0 if failed == 0 else 4


# ---------------------------------------------------------------- fix paths
def cmd_fix_paths(args):
    if not args.old or not args.new:
        log("用法: restore.py fix-paths --old <原根> --new <新根> [--target <config>]")
        return 2
    target = os.path.abspath(os.path.expanduser(args.target or default_target()))
    old = args.old.rstrip("\\/")
    new = args.new.rstrip("\\/")
    n_files = n_lines = 0
    for root, _, fs in os.walk(os.path.join(target, "projects")):
        if os.path.basename(root) == "subagents":
            continue
        for fn in fs:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(root, fn)
            with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
                lines = f.readlines()
            changed = 0
            out = []
            for ln in lines:
                if old.lower() in ln.lower():
                    s = ln
                    for a, b in ((old, new), (old.replace("\\", "\\\\"), new.replace("\\", "\\\\")),
                                 (old.replace("\\", "/"), new.replace("\\", "/"))):
                        if a in s:
                            s = s.replace(a, b)
                    if s != ln:
                        changed += 1
                    out.append(s)
                else:
                    out.append(ln)
            if changed:
                if not args.dry_run:
                    shutil.copy2(p, p + ".pre-fix.bak")
                    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
                        f.writelines(out)
                n_files += 1
                n_lines += changed
    # 同步 SECRETS/claude.json 的 projects 键
    cfg = os.path.join(target, ".claude.json")
    if not os.path.isfile(cfg):
        cfg = os.path.join(HERE, "SECRETS", "claude.json")
    cnt = 0
    if os.path.isfile(cfg):
        try:
            d = json.load(open(cfg, encoding="utf-8"))
            pr = d.get("projects") or {}
            nd = {}
            for k, v in pr.items():
                nk = k
                if old.lower() in k.lower():
                    nk = k.replace(old, new)
                    cnt += 1
                nd[nk] = v
            d["projects"] = nd
            if not args.dry_run:
                json.dump(d, open(cfg + ".fixed", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                log("已写出 %s.fixed，确认无误后替换原文件" % os.path.basename(cfg))
        except Exception as ex:
            log("! .claude.json 改写失败: %s" % ex)
    log("fix-paths 完成：改写 %d 个 jsonl / %d 行，.claude.json 键 %d 个" % (n_files, n_lines, cnt))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Claude 迁移包还原工具")
    ap.add_argument("cmd", nargs="?", default="restore",
                    choices=["restore", "verify", "list", "fix-paths"])
    ap.add_argument("--target", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--secrets", action="store_true")
    ap.add_argument("--no-paths", action="store_true")
    ap.add_argument("--no-desktop", action="store_true", help="不还原桌面版会话到 %%APPDATA%%")
    ap.add_argument("--old", default=None)
    ap.add_argument("--new", default=None)
    a = ap.parse_args()
    return {"restore": do_restore, "verify": cmd_verify, "list": cmd_list,
            "fix-paths": cmd_fix_paths}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
