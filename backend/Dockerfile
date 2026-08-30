FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# P2-D13：安装用 requirements.lock（pip-compile 钉死版本，可复现构建）；requirements.txt 为顶层声明供参考
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# P2-E8：非 root 降权运行（收敛「容器 RCE 即 root」风险面）。注意 compose 用
# ./backend:/app 热挂载覆盖整个 /app——镜像内目录属主/chown 对运行时无效，uploads/exports
# 落盘权限取决于宿主挂载目录：Docker Desktop（Windows）挂载权限宽松可直接写；
# Linux 部署需宿主 `chown -R 10001:10001 uploads backend/exports`（见 README「P2-E8 上线检查」）。
RUN useradd --no-create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 健康检查：python:3.11-slim 无 curl/wget，用标准库 urllib 探活 /healthz（app/main.py，无鉴权）。
# compose 未定义 healthcheck 时此配置生效；探活失败由 restart: unless-stopped 拉起。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

# 单进程部署约束（workers=1）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
