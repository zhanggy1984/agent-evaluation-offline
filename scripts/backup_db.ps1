# backup_db.ps1：备份 ai_evaluation 库（共享 infra MySQL，容器内 mysqldump）。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/backup_db.ps1
#
# P2-D14 备份策略：
# - 密码从 ../infra/.env 读 MYSQL_EVAL_PASSWORD（不硬编码，D18 同向）
# - docker exec shared-mysql mysqldump 全库导出（--single-transaction 一致性快照，不停库）
# - 滚动保留最近 14 份（删最旧），库很小（~3.6MB）14 份不到 60MB
# - 恢复：cmd 下 `docker exec -i shared-mysql mysql -h127.0.0.1 -uevaluation -p<PASS> ai_evaluation < backups/xxx.sql`

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backupDir = Join-Path $root "backups"
$infraEnv = Join-Path $root "../infra/.env"

if (-not (Test-Path $infraEnv)) { throw "未找到 $infraEnv（共享 infra 的 .env）" }
$line = (Select-String -Path $infraEnv -Pattern '^MYSQL_EVAL_PASSWORD=').Line
if (-not $line) { throw "infra/.env 缺少 MYSQL_EVAL_PASSWORD" }
$pass = $line -replace '^MYSQL_EVAL_PASSWORD=', ''

if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Force $backupDir | Out-Null }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$out = Join-Path $backupDir "ai_evaluation_$stamp.sql"

# cmd 重定向（字节级，避免 PowerShell 管道对 native 输出的 UTF-8 编码转换/BOM）；
# 密码为 base64url（-_. + 字母数字），无空格/引号，可安全嵌入 cmd 命令行。
Write-Host "备份到：$out"
$cmd = "docker exec shared-mysql mysqldump -h127.0.0.1 -uevaluation -p$pass --single-transaction --routines --no-tablespaces ai_evaluation > `"$out`""
cmd /c $cmd
if ($LASTEXITCODE -ne 0) { throw "mysqldump 失败（exit $LASTEXITCODE）" }

$file = Get-Item $out
if ($file.Length -eq 0) { throw "备份为空文件：$out" }

# 滚动保留最近 14 份
$keep = 14
Get-ChildItem $backupDir -Filter "ai_evaluation_*.sql" | Sort-Object Name -Descending | Select-Object -Skip $keep | ForEach-Object {
    Remove-Item $_.FullName
    Write-Host "清理旧备份：$($_.Name)"
}

Write-Host "完成：$($file.Name)（$([math]::Round($file.Length / 1KB)) KB）"
