# Bug 修复方案：导出卷挂载 + 权重值域收紧

> 来源：第五批 E2E 验收（浏览器全场景）发现 2 个真实 bug + 1 项运维清理。
> 状态：**待用户确认后实施**（Bug #1/#2 为 A 级，plan-first）。
> 评审：独立 Plan subagent 只读评审。

---

## 背景

第五批 E2E 验收暴露：

| # | 问题 | 根因 | 现状 |
|---|---|---|---|
| Bug #1 | 权重 `factuality=99` → **500**（应 400） | `_validated_weight` 上限 100 与 DB 列 `Numeric(5,4)`（max 9.9999）冲突 | E2E 实测 |
| Bug #2 | 导出 → **500** `PermissionError: /app/exports` | compose 未单独挂载 exports，`/app/exports` 经父挂载（`./backend:/app`）以 root:root 755 呈现 → appuser 不可写 | E2E 实测 + 容器 touch 复现 |
| C1 | 5 个死配置 DB 残留 | 存量库未 DELETE | **已清理**（总数 43→38） |

---

## 1. Bug #1：权重值域收紧到 0-1

### 根因

`backend/app/api/agents.py:99` `_validated_weight` 校验 `0.0 <= val <= 100.0`，但写入列
`AgentDimensionWeight.weight` 是 `Numeric(5,4)`（models/dimension.py:33，max 9.9999）。
`factuality=99` 通过校验 → MySQL `DataError(1264)` → 未捕获 → **500**。

全仓库 weights 均为 0-1 量纲，无任何 0-100 调用方（评审已逐路径核实）：
- 写入方仅 4 处：`_init_default_weights`（agents.py:122）、`set_agent_weights`（:390/393）、
  `set_interface_weights`（:424/426）、`seed.py:145`——全经 `DEFAULT_WEIGHTS`（constants.py:87-92，0-1）
- scorer 消费 0-1（scorer.py:100/:123-124 归一化分母）
- 前端唯一权重 UI `Agents.vue:186-187` 输入框 `:min="0" :max="1"` 钳位；`:648` 提交 0-1
- `setInterfaceWeights`（agents.js:60）仅定义、全仓库无 .vue 调用
- 存量 DB：`agent_dimension_weight` 16 行，`MAX(weight)=0.35`，无 >1；备份同
- 既有 B4 测试（test_config_history.py:116-154）全用 ≤0.5 权重，无依赖 100 上限用例

### 改动（仅 agents.py）

```python
# :86-102
def _validated_weight(dim: str, w) -> float:
    """B4 权重维度/值域校验：非法维度、非数值、越界（0-1）→ 400。

    scorer 只消费 accuracy 维权重；权重为 0-1 量纲（对齐 DB Numeric(5,4) 与 DEFAULT_WEIGHTS），
    越界是配置错，拒绝写入（防止落到 DB DataError 变 500）。
    """
    if dim not in ACCURACY_DIMENSIONS:
        raise ApiError(E_VALIDATION, ...400)   # 不变
    val = float(w) ...                          # 不变
    if not 0.0 <= val <= 1.0:                   # ← 改动：100.0 → 1.0
        raise ApiError(E_VALIDATION,
                       f"权重需在 0-1 量纲内（{dim}={w}），合法维度: ...", 400)  # ← 消息同步
    return val
```

### 测试（归并进既有 `backend/tests/test_config_history.py::TestWeightsValidation`）

该文件已含 B4 权重校验用例（:116-154，≤0.5 权重），追加越界用例（直调 `_validated_weight`）：

| 用例 | 入参 | 断言 |
|---|---|---|
| 非法维度（回归） | `("no_such", 0.5)` | 400 |
| 非数字（回归） | `("factuality", "abc")` | 400 |
| 合法 0-1（回归） | `("factuality", 0.5)` / `1.0` / `0.0` | 返回原值 |
| **越界（修复点）** | `("factuality", 99)` / `1.5` / `-0.1` | 400（不再落 DB 500） |

### 验证

1. `cd backend && python -m pytest -p no:html tests/test_config_history.py`
2. 全量回归 `python -m pytest -p no:html tests/ -q`
3. 修复复验（API 直连，UI 钳位不可达）：`PUT /api/agents/{id}/weights {"weights":{"factuality":99}}` → 400；`{"factuality":0.9}` → 200
4. 权重页 UI 保存 0.9 → 成功（`:max=1` 钳位，>1 本就无法提交）

---

## 2. Bug #2：compose 补挂载 exports 卷

### 根因

容器以 `appuser`（uid 10001，P2-E8 降权）运行。compose volumes（docker-compose.yml:53-57）
只挂 `./backend:/app`（父挂载）+ `./uploads:/app/uploads`，**未单独挂 exports**。`/app/exports`
是父挂载内宿主 `backend/exports` 目录经 Docker Desktop（9p/drvfs）以 **root:root 755** 呈现
（评审实测，非镜像层——`.dockerignore:18` 已排除 `exports/`，镜像本无此目录内容）→ appuser
不可写 → `PermissionError` → 500。Docker Desktop 对每个 **bind mount 根**统一呈现 777，
`/app`（宿主 755）与 `/app/uploads`（宿主 755）两例皆证 → 补子挂载即可获得 777。

已实测：
- `docker exec -u appuser touch /app/exports/.wtest` → **Permission denied**（父挂载 755）
- `docker exec -u appuser touch /app/uploads/.wtest` → **成功**（独立挂载根 777）

### 改动（仅 docker-compose.yml）

```yaml
    volumes:
      # 开发期热挂载：改代码后 restart 即生效（无需 rebuild）
      - ./backend:/app
      # 文件型用例自存文件（决策 #12）：宿主 ./uploads ↔ 容器 /app/uploads
      - ./uploads:/app/uploads
      # B5 导出文件目录：宿主 ./backend/exports ↔ 容器 /app/exports（独立挂载根 → 777，appuser 可写）
      - ./backend/exports:/app/exports        # ← 新增
```

宿主 `backend/exports/` 现有 **12 个**旧导出文件（2026-08-18~21，4 xlsx + 8 pdf），
子挂载同目录二次挂载，**不隐藏不丢失**。

### 验证

1. `docker compose up -d`（卷变更需 recreate backend 容器）
2. `docker exec -u appuser touch /app/exports/.wtest` → 成功（777 权限模拟），清理测试文件
3. 浏览器复验导出链路：`POST /runs/{id}/export` → 文件生成 → `GET /exports/{token}` 下载 200
4. **旧文件语义**：12 个旧文件是孤儿（`export_token` 表当前 0 行，无对应 token 可下载），
   且 `_cleanup_stale_exports`（exports.py:54-67，create/download 触发）会惰性清掉
   mtime 超 24h 的文件 → 首次导出后旧文件被清空，**符合 B5 设计**，不属回归。

### 备选（主路径验证失败才用）

Dockerfile `USER appuser` 前追加 `RUN mkdir -p /app/exports && chown appuser:appuser /app/exports`
+ 重新 build。缺点：无卷则容器重建丢文件，持久性差，不推荐。

---

## 3. C1 运维清理（已完成）

`system_config` 5 条死配置已 DELETE（2026-08-31）：
`contract_check_timeout` / `sse_idle_timeout` / `error_rate_block` / `breaker_half_open_probe` /
`retry_backoff_max`。删除前 5 条残留 → 删除后 0，总数 43→38。

上线待办.md:298 回填：「存量库 DELETE 待执行」→「已执行（2026-08-31）」。

---

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `backend/app/api/agents.py` | :99 值域 100.0→1.0；docstring + 错误消息同步 |
| `backend/tests/test_config_history.py` | `TestWeightsValidation` 追加越界用例（99/1.5/-0.1 → 400） |
| `docker-compose.yml` | :57 后新增 exports 挂载 |
| `上线待办.md` | C1 回填已执行 |

## 风险

- **Bug #1**：值域收紧同时作用于 `set_agent_weights`（agents.py:383）与 `set_interface_weights`
  （:417）两端点——后者当前无前端调用方（agents.js:60 仅定义），属 API 契约同步收紧。
  若存在隐藏 0-100 调用方会立即 400——已全仓库核实无。接口行为从「写入 500」变「400 拒收」，
  属修复目标。前端 `:max=1` 钳位不受影响。
- **Bug #2**：bind mount 权限依赖 Docker Desktop 权限模拟（uploads 已验证 777）；Linux 部署
  环境需宿主目录 `chown 10001:10001`（P2-E8 注释已要求同款处理 uploads）。首次导出会惰性
  清理 12 个超龄孤儿文件（B5 既有设计）。
