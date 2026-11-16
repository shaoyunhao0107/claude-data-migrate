# fix-project-paths.ps1 — 改写会话里的 cwd，让老会话指向新路径
# 场景：新机盘符/用户目录与源机不同（如 D:\ -> E:\，或 C:\Users\a -> D:\Users\a）
# 用法：
#   .\fix-project-paths.ps1 -Old "D:\" -New "E:\"
#   .\fix-project-paths.ps1 -Old "C:\Users\260482" -New "D:\Users\260482" -Target "$env:USERPROFILE\.claude"
# 说明：改写前自动为每个 jsonl 留 .pre-fix.bak；.claude.json 只写 .fixed 副本，需你确认后替换。
param(
    [Parameter(Mandatory = $true)][string]$Old,
    [Parameter(Mandatory = $true)][string]$New,
    [string]$Target = "",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$py = $null
foreach ($c in @("python", "python3", "py")) {
    $p = Get-Command $c -ErrorAction SilentlyContinue
    if ($p) { $py = $p.Source; break }
}
if (-not $py) { Write-Host "未找到 Python 3，请先安装" -ForegroundColor Red; exit 1 }

if (-not $Target) {
    $Target = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".claude" }
}

$args2 = @((Join-Path $here "restore.py"), "fix-paths", "--old", $Old, "--new", $New, "--target", $Target)
if ($DryRun) { $args2 += "--dry-run" }
Write-Host ("改写范围: " + $Target) -ForegroundColor DarkGray
& $py @args2
exit $LASTEXITCODE
