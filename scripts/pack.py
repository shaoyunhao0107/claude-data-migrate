#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.py — 把 Claude 数据打包成"换机零失败"的迁移包

仅标准库，跨平台。产出布局与 restore.py 严格对齐。

用法：
  python pack.py --out D:\\pkg                     # 自动发现根目录后打包
  python pack.py --root ~/.claude --out D:\\pkg
  python pack.py --root ~/.claude --out D:\\pkg --secrets      # 同时导出凭证
  python pack.py --root ~/.claude --out D:\\pkg --selftest     # 打包后做假还原自检

铁律（踩过的坑，别改）：
  1. 输出目录必须短（<=40 字符）。否则嵌套路径超 260 字符，解压/还原会静默丢文件。
  2. 排除 .venv/node_modules/__pycache__ 等可重建依赖；技能超过 100MB 必查。
  3. 每个 <session>.jsonl 同名的 <session>\\ 子目录（子代理记录）必须一起带。
  4. 标题三源合并（custom-title > ai-title > 首条用户消息）。
"""
import os, sys, json, glob, time, shutil, hashlib, argparse, platform

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

IS_WIN = platform.system() == "Windows"
HERE = os.path.dirname(os.path.abspath(__file__))

EXCLUDE_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache",
                ".mypy_cache", "cache", "blobs", "shell-snapshots", ".cache"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".tmp", ".log", ".lock.tmp")
EXCLUDE_FILES = {".credentials.json", "statsig", "claude_desktop_config.json"}
SECRET_GLOBS = ["sessions/*.key", ".claude.json", "settings.local.json"]


def longpath(p):
    p = os.path.abspath(p)
    if IS_WIN and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def sha256_file(p):
    h = hashlib.sha256()
    with open(longpath(p), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_file(src, dst):
    os.makedirs(longpath(os.path.dirname(dst)) if os.path.dirname(dst) else ".", exist_ok=True)
    shutil.copy2(longpath(src), longpath(dst))


def walk_data(root):
    """产出 (绝对路径, 相对路径, 是否符号链接)"""
    for dirpath, dirnames, filenames in os.walk(longpath(root)):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_DIRS]
        for f in filenames:
            if f.lower().endswith(EXCLUDE_SUFFIX) or f in EXCLUDE_FILES:
                continue
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, longpath(root))
            yield full, rel.replace("\\", "/"), os.path.islink(full)


def main():
    ap = argparse.ArgumentParser(description="打包 Claude 数据迁移包")
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", required=True, help="输出目录（务必短路径）")
    ap.add_argument("--secrets", action="store_true", help="导出凭证到 SECRETS/")
    ap.add_argument("--selftest", action="store_true", help="打包后做假还原自检")
    ap.add_argument("--label", default=None, help="包名标签，默认主机名")
    args = ap.parse_args()

    # 1) 定位根目录
    if args.root:
        root = os.path.abspath(args.root)
    else:
        sys.path.insert(0, HERE)
        from discover import candidate_roots, root_stats
        cands = [s for s in (root_stats(r) for r in candidate_roots()) if s]
        if not cands:
            print("未发现 Claude 数据目录，请用 --root 指定")
            return 2
        cands.sort(key=lambda x: (-x["sessions"], -x["mtime"]))
        root = cands[0]["path"]
    if not os.path.isdir(root):
        print(f"根目录不存在: {root}")
        return 2

    # 2) 输出目录检查（长路径是头号杀手）
    out = os.path.abspath(args.out)
    if len(out) > 60:
        print(f"! 输出路径过长（{len(out)} 字符）: {out}")
        print("  嵌套后极易超过 260 字符上限，导致解压静默丢文件。建议改用 C:\\\\cm 这类短路径。")
    os.makedirs(longpath(out), exist_ok=True)

    label = args.label or platform.node()
    data_root = os.path.join(out, "data")
    manifest = []
    skipped_links = []

    print(f"== 打包 ==\n  源: {root}\n  目标: {out}")

    # 3) 复制数据
    total_bytes = 0
    for sub in ("projects", "skills", "sessions", "file-history", "scheduled-tasks", "plugins", "desktop-app"):
        src_dir = os.path.join(root, sub)
        if not os.path.isdir(src_dir):
            continue
        for full, rel, is_link in walk_data(src_dir):
            real = os.path.realpath(full) if is_link else full
            dst = os.path.join(data_root, sub, rel)
            try:
                copy_file(real, dst)
            except Exception as e:
                print(f"  ! 跳过 {rel}: {e}")
                continue
            size = os.path.getsize(longpath(dst))
            total_bytes += size
            entry = {"path": f"data/{sub}/{rel}", "bytes": size,
                     "sha256": sha256_file(dst), "type": "data", "category": sub}
            if is_link:
                entry["original_link"] = os.readlink(full) if hasattr(os, "readlink") else ""
                skipped_links.append(rel)
            manifest.append(entry)
        print(f"  {sub}: 完成")

    # 4) settings.json 只作参考，不直接覆盖目标
    s = os.path.join(root, "settings.json")
    if os.path.exists(s):
        dst = os.path.join(data_root, "settings.json.reference")
        copy_file(s, dst)
        manifest.append({"path": "data/settings.json.reference", "bytes": os.path.getsize(longpath(dst)),
                         "sha256": sha256_file(dst), "type": "data", "category": "config"})

    # 5) 凭证（可选）
    secrets_dir = os.path.join(out, "SECRETS")
    if args.secrets:
        os.makedirs(longpath(secrets_dir), exist_ok=True)
        for pat in SECRET_GLOBS:
            for p in glob.glob(os.path.join(root, pat)):
                if not os.path.isfile(p):
                    continue
                dst = os.path.join(secrets_dir, os.path.basename(p))
                copy_file(p, dst)
                manifest.append({"path": f"SECRETS/{os.path.basename(p)}",
                                 "bytes": os.path.getsize(longpath(dst)),
                                 "sha256": sha256_file(dst), "type": "secret", "category": "secret"})
        print("  SECRETS: 已导出（导入后请立刻轮换密钥）")

    # 6) 项目索引 / 标题 / 路径预案
    sys.path.insert(0, HERE)
    from discover import collect_titles, build_projects
    titles = collect_titles(root)
    projects = build_projects(root, titles)

    # 补 encoded / cwd：restore.py 需要 encoded 才能定位会话目录写回 custom-title.json
    sid2enc, enc2cwd = {}, {}
    for p in glob.glob(os.path.join(root, "projects", "*", "*.jsonl")):
        sid2enc[os.path.basename(p)[:-6]] = os.path.basename(os.path.dirname(p))
    for pr in projects:
        enc2cwd[pr["encoded"]] = pr["path"]
    for t in titles.values():
        enc = t.get("encoded") or sid2enc.get(t["sessionId"], "")
        t["encoded"] = enc
        if enc and enc in enc2cwd:
            t.setdefault("cwd", enc2cwd[enc])

    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source_root": root,
               "source_host": label, "source_platform": platform.system().lower(),
               "totals": {"projects": len(projects), "sessions": sum(p["sessions"] for p in projects),
                          "skills": len([d for d in glob.glob(os.path.join(root, "skills", "*")) if os.path.isdir(d)]),
                          "titles": len(titles)},
               "projects": projects},
              open(os.path.join(out, "projects-index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    from collections import Counter
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": len(titles),
               "coverage": dict(Counter(v["titleSource"] for v in titles.values())),
               "sessions": list(titles.values())},
              open(os.path.join(out, "session-titles.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    path_map = []
    for p in projects:
        status = "ready" if p["exists"] else ("drive-missing" if IS_WIN and not os.path.exists(p["drive"] + "\\") else "manual")
        path_map.append({"encoded": p["encoded"], "path": p["path"], "status": status,
                         "action": "mkdir -p" if status == "ready" else
                                   ("subst " + p["drive"] + " <目标盘>:\\_migrated\\" + p["drive"][0].lower())})
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "projects": path_map},
              open(os.path.join(out, "path-map.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 7) 还原脚本随包带走（apply_titles 也要带：目标机没装本 skill 也能回填标题）
    for f in ("restore.py", "apply_titles.py", "fix-project-paths.ps1", "fix-project-paths.sh"):
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            copy_file(src, os.path.join(out, f))
    for wrapper, body in (("restore.ps1", "python \"$PSScriptRoot\\restore.py\" @args\n"),
                          ("restore.sh", "#!/bin/sh\nexec python3 \"$(dirname \"$0\")/restore.py\" \"$@\"\n")):
        wp = os.path.join(out, wrapper)
        with open(wp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        if wrapper.endswith(".sh"):
            try:
                os.chmod(wp, 0o755)
            except OSError:
                pass

    # 8) MANIFEST / 校验和 / 说明
    totals = {"files": len(manifest), "bytes": sum(m["bytes"] for m in manifest)}
    json.dump({"package": f"claude-migrate-{label}-{time.strftime('%Y%m%d')}",
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "host": label, "source_platform": platform.system().lower(),
               "category_legend": {"projects": "会话与项目", "skills": "技能", "config": "配置参考",
                                   "secret": "凭证（默认不还原）"},
               "totals": totals, "files": manifest},
              open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    with open(os.path.join(out, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as fh:
        for m in manifest:
            fh.write(f"{m['sha256']}  {m['path']}\n")

    readme = f"""# Claude 迁移包 · {label}

{time.strftime('%Y-%m-%d %H:%M')} 生成 ｜ 源根 `{root}`
**{totals['files']} 个文件 / {round(totals['bytes']/1048576,1)} MB ｜ {len(projects)} 个项目 · {sum(p['sessions'] for p in projects)} 个会话 · {len(titles)} 条标题**

## 四步导入（在目标机解压后执行）

```powershell
python restore.py verify      # ① 校验 sha256
python restore.py --dry-run   # ② 演练，不写盘
python restore.py            # ③ 正式还原（含建项目空目录）

# ④ 打开 WorkBuddy → 设置 → 数据导入 → Claude Code，勾选全部类别导入
#    导入完成后再回填原始标题（恢复"原来怎么搜就怎么搜"）：
python apply_titles.py --titles session-titles.json --dry-run
python apply_titles.py --titles session-titles.json
#    改的是 %USERPROFILE%\\.workbuddy\\workbuddy.db，脚本自动备份，重启 WorkBuddy 生效
```

## 注意

1. **解压到短路径**（如 `C:\\cm`）。解压到深层目录会因路径超 260 字符静默丢文件；
   `verify` 报缺失就先换短路径重解，不要继续还原。
2. 项目原路径不存在 → `restore.py` 会建空目录；盘符缺失会打印 subst 方案。
3. `SECRETS/` 默认不还原（`--secrets`），导入后立即轮换密钥。
4. 还原后进 WorkBuddy → 数据导入 → Claude Code；失败项看
   `%USERPROFILE%\\.workbuddy\\data-import\\<hash>\\history\\*.json` 的 `reason`。
"""
    with open(os.path.join(out, "README-导入说明.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(readme)

    print(f"\n== 完成 ==\n  文件 {totals['files']} | {round(totals['bytes']/1048576,1)} MB | "
          f"项目 {len(projects)} | 会话 {sum(p['sessions'] for p in projects)} | 标题 {len(titles)}")
    if skipped_links:
        print(f"  符号链接已解引用: {len(skipped_links)} 个")

    # 9) 假还原自检
    if args.selftest:
        import subprocess, tempfile
        tmp = tempfile.mkdtemp(prefix="cm-")
        target = os.path.join(tmp, "claude")
        print(f"\n== 假还原自检 ==\n  临时目标: {target}")
        rc = subprocess.call([sys.executable, os.path.join(out, "restore.py"), "--target", target])
        if rc == 0:
            n = len(glob.glob(os.path.join(target, "projects", "*", "*.jsonl")))
            nk = len([d for d in glob.glob(os.path.join(target, "skills", "*")) if os.path.isdir(d)])
            print(f"  自检: 会话 {n} / 技能 {nk}（包内 {sum(p['sessions'] for p in projects)} / "
                  f"{len([d for d in glob.glob(os.path.join(root,'skills','*')) if os.path.isdir(d)])}）")
        else:
            print(f"  ! 自检失败，退出码 {rc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
