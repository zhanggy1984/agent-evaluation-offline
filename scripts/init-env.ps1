# init-env.ps1：生成 .env（随机密钥 + 默认值），首次启动前执行一次。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/init-env.ps1
# 已存在 .env 时跳过（不覆盖手工改动）。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"

function Gen-Base64Url([int]$bytes) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] $bytes
    $rng.GetBytes($buf)
    return [System.Convert]::ToBase64String($buf).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (Test-Path $envPath) {
    Write-Host ".env 已存在，跳过生成。如需重新生成请先删除 .env。"
    exit 0
}

# Fernet key = urlsafe base64(32 字节)；JWT secret 用 64 字节 base64（远大于 256bit）
$fernet = Gen-Base64Url 32
$jwt    = Gen-Base64Url 48
$dbPwd  = Gen-Base64Url 18

@"
# ============ AI Agent 评测系统 .env（由 init-env.ps1 自动生成） ============
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000

# MySQL（docker-compose 内网地址）
DB_HOST=mysql
DB_PORT=3306
DB_USER=evaluation
DB_NAME=ai_evaluation
DB_PASSWORD=$dbPwd

# 密钥（启动强校验：jwt_secret ≥256bit；fernet_keys 非空）
FERNET_KEYS=$fernet
JWT_SECRET=$jwt

# LLM profile 密钥（judge 用，按实际填写）
JUDGE_API_KEY=
"@ | Out-File -FilePath $envPath -Encoding utf8

Write-Host "已生成 .env：$envPath"
Write-Host "DB_PASSWORD / FERNET_KEYS / JWT_SECRET 均为随机值。"
Write-Host "下一步：docker compose up -d"
