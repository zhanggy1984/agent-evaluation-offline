<template>
  <div class="dashboard">
    <!-- 顶部操作区 -->
    <el-card shadow="never" class="block">
      <div class="toolbar">
        <el-select
          v-model="activeAgentId"
          placeholder="选择 Agent 下钻"
          clearable
          filterable
          style="width: 240px"
          @change="onAgentChange"
        >
          <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-button v-if="isStaff" type="primary" @click="openTrigger">触发评测</el-button>
        <el-button :loading="loading" @click="refreshAll">刷新</el-button>
      </div>
    </el-card>

    <!-- L0 门禁墙 -->
    <el-card shadow="never" class="block">
      <template #header>
        <TermTip term="gate" />
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L0" /></el-tag>
      </template>
      <el-row :gutter="12">
        <el-col v-for="g in gateList" :key="g.agent_id" :xs="24" :sm="12" :md="8" :lg="6">
          <div
            class="gate-card"
            :class="{ active: g.agent_id === activeAgentId }"
            @click="selectAgent(g)"
          >
            <div class="gate-head">
              <span class="gate-name">{{ g.agent_name }}</span>
              <el-tag v-if="g.version" size="small">{{ g.version }}</el-tag>
              <el-tag v-else size="small" type="info">未评测</el-tag>
            </div>
            <div class="gate-score">
              <template v-if="g.agent_score != null">
                <!-- agent_score 为 0-100 百分制（scorer score_case 合成），进度条直接取分值 -->
                <div class="score-num">{{ fmtScore(g.agent_score) }}<span class="score-unit"> / 100</span></div>
                <el-progress :percentage="Math.min(100, Math.round(g.agent_score || 0))" :stroke-width="10" />
              </template>
              <el-empty v-else description="暂无评分" :image-size="42" />
            </div>
            <div class="gate-meta">
              <span>通过率 {{ fmtRate(g.pass_rate) }}</span>
              <span>{{ g.pass_case }}/{{ g.total_case }} 用例</span>
            </div>
            <div v-if="g.stale_suites && g.stale_suites.length" class="gate-stale">
              <el-tag v-for="s in g.stale_suites" :key="s.id" size="small" type="warning">
                陈旧:{{ s.name }}
              </el-tag>
            </div>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <!-- run 列表 -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="run-toolbar">
          <span>评测记录</span>
          <el-input
            v-model="runSearch"
            placeholder="搜索 ID / Agent / 状态"
            clearable
            size="small"
            style="width: 220px; margin-left: 12px"
          />
          <el-select v-model="runStatusFilter" placeholder="状态筛选" clearable size="small" style="width: 130px; margin-left: 8px">
            <el-option v-for="(v, k) in RUN_STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
          <span class="dim-note" style="margin-left: 8px">共 {{ filteredRuns.length }} 条</span>
          <el-text v-if="hasMoreRuns" type="warning" size="small" style="margin-left: 8px">
            run 记录超 200 条，仅展示最新 200 条，更早记录未覆盖
          </el-text>
        </div>
      </template>
      <el-table :data="pagedRuns" v-loading="runsLoading" size="small" stripe>
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column label="Agent" width="160">
          <template #default="{ row }">{{ agentName(row.agent_id) }}</template>
        </el-table-column>
        <el-table-column label="版本" width="150">
          <template #default="{ row }">
            {{ row.version }}
            <el-tag v-if="row.case_ids?.length" size="small" type="info" style="margin-left: 4px">
              子集 {{ row.case_ids.length }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag :type="runStatus(row.status).type" size="small">{{ runStatus(row.status).label }}</el-tag>
            <el-tag v-if="row.fail_case > 0" size="small" type="warning" style="margin-left: 4px">
              {{ row.fail_case }} 未达标
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="80">
          <template #default="{ row }">{{ fmtScore(row.agent_score) }}</template>
        </el-table-column>
        <el-table-column label="通过率" width="90">
          <template #default="{ row }">
            {{ fmtRate(row.total_case ? row.pass_case / row.total_case : null) }}
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="200">
          <template #default="{ row }">
            <template v-if="row.done_case != null">
              <el-progress
                :percentage="Math.min(100, Math.round(row.total_case ? (row.done_case / row.total_case) * 100 : 0))"
                :stroke-width="10"
                style="width: 90px; display: inline-block; vertical-align: middle"
              />
              <span class="dim-note" style="margin-left: 6px">{{ row.done_case }}/{{ row.total_case }}</span>
              <span v-if="row.estimate_remaining_sec != null" class="dim-note">· 剩 {{ fmtDuration(row.estimate_remaining_sec) }}</span>
              <el-tag v-if="row.judge_queue" size="small" type="info" style="margin-left: 4px">判分 {{ row.judge_queue }}</el-tag>
            </template>
            <span v-else class="dim-note">—</span>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" min-width="160">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRunDetail(row)">明细</el-button>
            <el-button v-if="isStaff && isActive(row.status)" link type="warning" size="small" @click="doCancel(row)">
              取消
            </el-button>
            <el-button v-if="isStaff && !isActive(row.status)" link type="primary" size="small" @click="doRerun(row)">
              重跑
            </el-button>
            <el-button
              v-if="isStaff && !isActive(row.status) && row.fail_case > 0"
              link
              type="warning"
              size="small"
              @click="doRerunFailed(row)"
            >
              重跑未达标
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="filteredRuns.length > PAGE_SIZE"
        layout="prev, pager, next, total"
        :total="filteredRuns.length"
        :page-size="PAGE_SIZE"
        :current-page="runPage"
        @current-change="(p) => (runPage = p)"
        style="margin-top: 8px; justify-content: flex-end"
      />
    </el-card>

    <!-- 三面板：性能 / 成本 / 覆盖（轻量化合并，跟随顶部 agent 下钻） -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <el-tabs v-model="activePanel">
        <el-tab-pane label="性能" name="perf">
          <el-alert
            v-if="perfRows.length && !perfHasTTFT"
            type="info" :closable="false" show-icon
            title="该 agent 为同步接口（无流式首字），无 TTFT 数据，仅展示 E2E"
            style="margin-bottom: 12px"
          />
          <EChart v-if="perfRows.length" :option="perfOption" height="300px" />
          <el-empty v-else description="该 agent 暂无终态评测记录" />
          <el-table v-if="perfRows.length" :data="perfRows" size="small" stripe style="margin-top: 12px">
            <el-table-column prop="run_id" label="Run" width="70" />
            <el-table-column prop="version" label="版本" width="100" />
            <el-table-column label="TTFT p50" width="120">
              <template #default="{ row }">{{ fmtMs(row.ttft_p50) }}</template>
            </el-table-column>
            <el-table-column label="TTFT p95" width="120">
              <template #default="{ row }">{{ fmtMs(row.ttft_p95) }}</template>
            </el-table-column>
            <el-table-column label="E2E p50" width="120">
              <template #default="{ row }">{{ fmtMs(row.e2e_p50) }}</template>
            </el-table-column>
            <el-table-column label="E2E p95" width="120">
              <template #default="{ row }">{{ fmtMs(row.e2e_p95) }}</template>
            </el-table-column>
            <el-table-column label="开始时间" min-width="160">
              <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="成本" name="cost" lazy>
          <el-alert
            v-if="costNoPrice"
            type="warning"
            :closable="false"
            show-icon
            title="该模型未配置单价，成本为 N/A（见下方模型单价表）"
            style="margin-bottom: 12px"
          />
          <EChart v-if="costRows.length" :option="costTokensOption" height="200px" />
          <div v-if="costRows.length && costHasMoney" style="margin-top: 12px">
            <EChart :option="costMoneyOption" height="200px" />
          </div>
          <div
            v-if="costRows.length"
            style="margin-top: 4px; display: flex; gap: 16px; flex-wrap: wrap; align-items: center; font-size: 12px; color: #909399"
          >
            <span>图例：</span>
            <span style="display: inline-flex; align-items: center">
              <span :style="{ width: '16px', height: '2px', background: COLOR_TOKEN, display: 'inline-block', marginRight: '4px' }"></span>Token 总量
            </span>
            <span v-if="costHasMoney" style="display: inline-flex; align-items: center">
              <span :style="{ width: '16px', height: '2px', background: COLOR_COST, display: 'inline-block', marginRight: '4px' }"></span>成本(元)
            </span>
            <span style="display: inline-flex; align-items: center">
              <span :style="{ width: '14px', height: '12px', background: COLOR_NA_AREA, border: '1px solid #d0d0d0', display: 'inline-block', marginRight: '4px' }"></span>灰色竖条 = 该 run 无该维度数据（N/A，非 0）
            </span>
          </div>
          <el-empty v-else description="该 agent 暂无终态评测记录" />
          <el-table v-if="costRows.length" :data="costRows" size="small" stripe style="margin-top: 12px">
            <el-table-column prop="run_id" label="Run" width="70" />
            <el-table-column prop="version" label="版本" width="100" />
            <el-table-column prop="model" label="模型" min-width="140" />
            <el-table-column label="Token 总量" width="120">
              <template #default="{ row }">{{ row.total_tokens ?? 'N/A' }}</template>
            </el-table-column>
            <el-table-column label="成本(元)" width="110">
              <template #default="{ row }">{{ fmtCost(row.total_cost) }}</template>
            </el-table-column>
            <el-table-column label="开始时间" min-width="160">
              <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
            </el-table-column>
          </el-table>
          <div class="prices-head">
            <span>模型单价</span>
            <el-tag size="small" type="info" style="margin-left: 8px">成本(元) = (输入token×输入价 + 输出token×输出价) ÷ 1e6，单价单位：元/百万 token</el-tag>
          </div>
          <el-table :data="modelPrices" size="small" stripe style="margin-top: 8px">
            <el-table-column prop="model" label="模型" min-width="180" />
            <el-table-column label="输入单价(元/百万)" width="150">
              <template #default="{ row }">{{ row.input_price ?? 'N/A' }}</template>
            </el-table-column>
            <el-table-column label="输出单价(元/百万)" width="150">
              <template #default="{ row }">{{ row.output_price ?? 'N/A' }}</template>
            </el-table-column>
            <el-table-column label="生效时间" min-width="160">
              <template #default="{ row }">{{ fmtTime(row.effective_from) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!modelPrices.length" description="暂无单价配置" />
        </el-tab-pane>

        <el-tab-pane label="覆盖" name="coverage" lazy>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12">
              <div class="cov-title">接口覆盖</div>
              <div v-if="coverage" class="coverage-body">
                <div class="coverage-rate">
                  <el-progress type="dashboard" :percentage="pct(coverage.interface_rate)" :width="150">
                    <template #default>
                      <div class="pct-num">{{ pct(coverage.interface_rate) }}%</div>
                      <div class="pct-sub">{{ coverage.interface_covered }}/{{ coverage.interface_total }}</div>
                    </template>
                  </el-progress>
                </div>
                <el-alert
                  v-if="coverage.interface_blank.length"
                  type="warning" :closable="false" show-icon title="以下接口未被任何用例覆盖（盲区）"
                />
                <el-alert v-else type="success" :closable="false" show-icon title="全部启用接口均已覆盖" />
                <el-table v-if="coverage.interface_blank.length" :data="coverage.interface_blank" size="small" stripe style="margin-top: 12px">
                  <el-table-column prop="id" label="ID" width="60" />
                  <el-table-column prop="name" label="接口名" min-width="140" />
                  <el-table-column prop="method" label="方法" width="80" />
                  <el-table-column prop="path" label="路径" min-width="160" show-overflow-tooltip />
                </el-table>
              </div>
              <el-empty v-else description="暂无数据" />
            </el-col>
            <el-col :xs="24" :md="12">
              <div class="cov-title">场景覆盖</div>
              <div v-if="coverage" class="coverage-body">
                <div class="coverage-rate">
                  <el-progress type="dashboard" :percentage="pct(coverage.scene_rate)" :width="150">
                    <template #default>
                      <div class="pct-num">{{ pct(coverage.scene_rate) }}%</div>
                      <div class="pct-sub">{{ coverage.scene_covered }}/{{ coverage.scene_total }}</div>
                    </template>
                  </el-progress>
                </div>
                <el-alert
                  v-if="coverage.scene_blank.length"
                  type="warning" :closable="false" show-icon title="以下场景未被任何用例覆盖（盲区）"
                />
                <el-alert v-else type="success" :closable="false" show-icon title="全部场景均已覆盖" />
                <el-table v-if="coverage.scene_blank.length" :data="coverage.scene_blank" size="small" stripe style="margin-top: 12px">
                  <el-table-column prop="tag" label="场景" min-width="160" />
                  <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
                </el-table>
              </div>
              <el-empty v-else description="暂无数据" />
            </el-col>
          </el-row>

          <div class="baseline-head">
            <TermTip term="baseline" />
            <el-tag v-if="baseline.run" size="small" type="info" style="margin-left: 8px">
              run #{{ baseline.run.run_id }} · v{{ baseline.run.version }}
            </el-tag>
            <el-tag v-if="baseline.is_gold_count" size="small" type="warning" style="margin-left: 8px">
              <TermTip term="gold" /> {{ baseline.is_gold_count }} 例
            </el-tag>
          </div>
          <template v-if="baseline.run">
            <div class="baseline-summary">
              总分 <b>{{ baseline.run.agent_score ?? '—' }}</b>
              · 通过 {{ baseline.run.pass_case }}/{{ baseline.run.total_case }}
              <span v-if="baseline.run.finished_at">· 完成 {{ fmtTime(baseline.run.finished_at) }}</span>
              <el-tag size="small" type="info" style="margin-left: 8px">
                {{ (RUN_STATUS[baseline.run.status] || {}).label || baseline.run.status }}
              </el-tag>
            </div>
            <el-table :data="baselineRows" size="small" stripe style="margin-top: 12px">
              <el-table-column prop="iface" label="接口" min-width="170" show-overflow-tooltip />
              <el-table-column label="维度" width="100">
                <template #default="{ row }">{{ DIM_LABEL[row.code] || row.code }}</template>
              </el-table-column>
              <el-table-column label="run 得分" width="200">
                <template #default="{ row }">
                  <el-progress
                    v-if="row.score != null"
                    :percentage="row.score"
                    :stroke-width="12"
                    :color="row.met ? '#67c23a' : '#f56c6c'"
                  />
                  <span v-else class="dim-none">—</span>
                </template>
              </el-table-column>
              <el-table-column width="90">
                <template #header><TermTip term="target" /></template>
                <template #default="{ row }">{{ row.target ?? '未配置' }}</template>
              </el-table-column>
              <el-table-column label="差距" width="100">
                <template #default="{ row }">
                  <span v-if="row.gap != null" :class="row.gap >= 0 ? 'gap-plus' : 'gap-minus'">
                    {{ row.gap >= 0 ? '+' : '' }}{{ row.gap }}
                  </span>
                  <span v-else class="dim-none">—</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.met != null" :type="row.met ? 'success' : 'danger'" size="small">
                    {{ row.met ? '达标' : '未达标' }}
                  </el-tag>
                  <el-tag v-else type="info" size="small">未配置</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty v-else description="最近无已评分 run，暂无比对" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- L1 趋势 -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>评测趋势</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L1" /></el-tag>
      </template>
      <EChart v-if="trendData.length" :option="trendOption" height="280px" />
      <el-empty v-else description="该 agent 暂无终态评测记录" />
    </el-card>

    <!-- L2 版本对比 -->
    <el-card v-if="activeAgentId" shadow="never" class="block">
      <template #header>
        <span>版本对比</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L2" /></el-tag>
      </template>
      <div class="compare-bar">
        <el-select v-model="compareA" placeholder="Run A" filterable style="width: 240px">
          <el-option
            v-for="r in trendData"
            :key="r.run_id"
            :value="r.run_id"
            :label="`#${r.run_id} ${r.version} ${fmtTime(r.started_at)}`"
          />
        </el-select>
        <span class="vs">vs</span>
        <el-select v-model="compareB" placeholder="Run B" filterable style="width: 240px">
          <el-option
            v-for="r in trendData"
            :key="r.run_id"
            :value="r.run_id"
            :label="`#${r.run_id} ${r.version} ${fmtTime(r.started_at)}`"
          />
        </el-select>
        <el-button type="primary" :disabled="!compareA || !compareB || compareA === compareB" @click="doCompare">
          对比
        </el-button>
      </div>
      <template v-if="compareData">
        <EChart :option="compareOption" height="300px" />
        <el-table :data="compareData.dims" size="small" stripe style="margin-top: 8px">
          <el-table-column label="维度">
            <template #default="{ row }">{{ DIM_LABEL[row.code] || row.code }}</template>
          </el-table-column>
          <el-table-column label="版本 A 均值">
            <template #default="{ row }">{{ fmtScore(row.mean_a) }}</template>
          </el-table-column>
          <el-table-column label="版本 B 均值">
            <template #default="{ row }">{{ fmtScore(row.mean_b) }}</template>
          </el-table-column>
          <el-table-column label="Δ">
            <template #default="{ row }">{{ fmtScore(row.delta) }}</template>
          </el-table-column>
          <el-table-column label="显著性（2σ）" width="190">
            <template #header>
              <el-tooltip
                placement="top"
                content="A、B 两次跑分差多少，要和这个 agent 平时分数的波动幅度比：差距明显大于平时波动，才算显著（提升/下降）；差距没超过平时波动，就是「差异不显著」——变化太小，分不清是版本真有差别，还是跑分本身忽高忽低。"
              >
                <span>显著性（2σ）<span class="sig-hint">ⓘ</span></span>
              </el-tooltip>
            </template>
            <template #default="{ row }">
              <span v-if="row.significant === 'up'" class="sig up">↑ 显著提升</span>
              <span v-else-if="row.significant === 'down'" class="sig down">↓ 显著下降</span>
              <span v-else-if="row.significant === 'flat'" class="sig flat">差异不显著</span>
              <span v-else class="sig na">数据不足</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <!-- L3 用例明细 + L3.5 门禁失败摘要（drawer 弹出） -->
    <el-drawer v-model="detailVisible" size="720px" direction="rtl">
      <template #header>
        <span>用例明细</span>
        <el-tag size="small" type="info" style="margin-left: 8px"><TermTip term="L3" /></el-tag>
        <el-tag v-if="curRunId" size="small" style="margin-left: 8px">run #{{ curRunId }}</el-tag>
        <!-- P2-9 评测态追溯：评测时 gq 知识版本 + 应用缓存命中 case 数（区别于 DeepSeek 上下文缓存） -->
        <el-tag v-if="curRunKnowledge" size="small" type="primary" style="margin-left: 8px">
          <TermTip term="knowledge" /> {{ curRunKnowledge }}
        </el-tag>
        <el-tag v-if="cacheHitCount" size="small" type="success" style="margin-left: 8px">
          <TermTip term="app_cache" /> {{ cacheHitCount }}/{{ runResults.length }}
        </el-tag>
        <span v-if="isStaff" class="export-bar">
          <el-button size="small" type="primary" :loading="exporting" @click="doExport('pdf')">导出 PDF</el-button>
        </span>
      </template>
      <el-table :data="runResults" v-loading="resultsLoading" size="small" stripe>
        <!-- 行内明细「维度判定」：四维统一展示。语义维度（事实性/思考链）judge 判分+理由（staff）；
             准确率维度（完成度/工具使用）规则断言判，展示通过数/总数；失败断言明细挂末尾 -->
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="row-detail">
              <div v-if="(row.score_per_dimension || []).length" class="detail-sec">
                <div class="detail-title">维度判定</div>
                <div v-for="d in row.score_per_dimension" :key="d.code" class="fail-dim">
                  <span class="dim-label">{{ DIM_LABEL[d.code] || d.code }}</span>
                  <span v-if="d.na" class="dim-na">N/A{{ d.na_reason ? '（' + d.na_reason + '）' : '' }}</span>
                  <template v-else>
                    <span class="dim-score">{{ fmtScore(d.value) }}</span>
                    <span v-if="judgeReason(row, d.code)" class="dim-reason">— {{ judgeReason(row, d.code) }}</span>
                    <span v-else-if="d.detail && typeof d.detail.pass === 'number'" class="dim-reason">
                      — 断言 {{ d.detail.pass }}/{{ d.detail.total }} 通过
                    </span>
                  </template>
                </div>
                <div v-for="(a, i) in row.assertion_results" :key="'a' + i" class="fail-dim assert-fail">
                  <el-tag size="small" type="danger">{{ DIM_LABEL[a.dimension] || a.dimension }} 断言失败</el-tag>
                  <span class="assert-op">[{{ a.op }}] args={{ JSON.stringify(a.args) }} → actual={{ a.actual }}</span>
                </div>
              </div>
              <div v-if="!(row.score_per_dimension || []).length" class="detail-sec detail-empty">
                无维度判定
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="case_name" label="用例" min-width="160" show-overflow-tooltip />
        <el-table-column prop="interface_name" label="接口" width="120" />
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag :type="pfTag(row.pass_fail)" size="small">{{ pfLabel(row.pass_fail) }}</el-tag>
          </template>
        </el-table-column>
        <!-- #12 单 case 状态流：判分中/判分失败显式标注，终态留空避免噪音 -->
        <el-table-column label="阶段" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.stage === 'judging'" size="small" type="warning">判分中</el-tag>
            <el-tag v-else-if="row.stage === 'judge_failed'" size="small" type="danger">判分失败</el-tag>
            <span v-else class="dim-note">{{ stageLabel(row.stage) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="总分" width="80">
          <template #default="{ row }">{{ fmtScore(row.score_total) }}</template>
        </el-table-column>
        <el-table-column label="维度分" min-width="220">
          <template #default="{ row }">
            <div v-for="d in row.score_per_dimension" :key="d.code" class="dim-line">
              <span class="dim-label">{{ DIM_LABEL[d.code] || d.code }}</span>
              <span v-if="d.na" class="dim-na">N/A{{ d.na_reason ? '（' + d.na_reason + '）' : '' }}</span>
              <span v-else>{{ fmtScore(d.value) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="错误" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error_type" class="err">{{ row.error_type }}: {{ row.error_detail }}</span>
          </template>
        </el-table-column>
        <!-- P2-6 Answer 摘要：后端已按 D3 截断 500（basic，含 viewer） -->
        <el-table-column label="Answer 摘要" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.answer">{{ row.answer }}</span>
            <span v-else class="dim-na">(空)</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button v-if="isStaff" link type="primary" size="small" @click="openEvidence(row)">证据</el-button>
          </template>
        </el-table-column>
      </el-table>
      <!-- L3.5 门禁失败摘要 -->
      <div v-if="runFailures.items && runFailures.items.length" class="drawer-failures">
        <div class="drawer-failures-title">
          <span>门禁失败摘要</span>
          <el-tag size="small" type="warning" style="margin-left: 8px"><TermTip term="L3.5" /></el-tag>
        </div>
        <!-- P2-6 top 扣分原因：按维度聚合，staff 才有数据（judge reason 证据级） -->
        <div v-if="runFailures.top_reasons && runFailures.top_reasons.length" class="top-reasons">
          <div class="top-reasons-title">Top 扣分原因</div>
          <div v-for="t in runFailures.top_reasons" :key="t.dimension" class="top-reason-item">
            <el-tag size="small" type="danger">{{ DIM_LABEL[t.dimension] || t.dimension }}</el-tag>
            <span class="top-reason-count">× {{ t.count }} 例 · avg {{ fmtScore(t.avg_score) }}</span>
            <div v-for="(r, i) in t.sample_reasons" :key="i" class="top-reason-sample">· {{ r }}</div>
          </div>
        </div>
        <el-collapse>
          <el-collapse-item v-for="f in runFailures.items" :key="f.case_id" :name="f.case_id">
            <template #title>
              <span class="fail-title">{{ f.case_name }}</span>
              <el-tag size="small" type="danger" style="margin-left: 8px">fail</el-tag>
              <el-tag v-if="f.interface_name" size="small" type="info" style="margin-left: 8px">
                {{ f.interface_name }}
              </el-tag>
              <span class="fail-score">score: {{ fmtScore(f.score_total) }}</span>
            </template>
            <div v-if="f.fail_dims.length" class="fail-sec">
              <div class="fail-sec-title">不达标维度</div>
              <div v-for="d in f.fail_dims" :key="d.code" class="fail-dim">
                {{ DIM_LABEL[d.code] || d.code }}: {{ fmtScore(d.value) }} &lt; <TermTip term="target" label="target" /> {{ fmtScore(d.target) }}
              </div>
            </div>
            <div v-if="f.assertion_failures.length" class="fail-sec">
              <div class="fail-sec-title">断言失败</div>
              <div v-for="(a, i) in f.assertion_failures" :key="i" class="fail-dim">
                [{{ a.dimension || '-' }}] {{ a.op }} args={{ JSON.stringify(a.args) }} → actual={{ a.actual }}
              </div>
            </div>
            <div v-if="f.judge_failures.length" class="fail-sec">
              <div class="fail-sec-title">judge 判定不达标</div>
              <div v-for="(j, i) in f.judge_failures" :key="i" class="fail-dim">
                {{ DIM_LABEL[j.dimension] || j.dimension }}: {{ fmtScore(j.score) }} — {{ j.reason }}
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>

    <!-- 触发评测 dialog -->
    <el-dialog v-model="triggerVisible" title="触发评测" width="480px">
      <el-form label-width="90px">
        <el-form-item label="Agent">
          <el-select v-model="triggerForm.agent_id" style="width: 100%" @change="onTriggerAgentChange">
            <el-option v-for="a in agentOptions" :key="a.id" :label="a.name" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="测试套件">
          <el-select
            v-model="triggerForm.suite_id"
            style="width: 100%"
            placeholder="选择套件"
            :disabled="!triggerForm.agent_id"
          >
            <el-option
              v-for="s in suiteOptions"
              :key="s.id"
              :label="`${s.name}（${s.case_count ?? '-'} 用例）`"
              :value="s.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="版本">
          <el-input v-model="triggerForm.version" placeholder="semver，如 1.2.3" />
        </el-form-item>
        <el-form-item label="触发类型">
          <el-radio-group v-model="triggerForm.trigger_type">
            <el-radio value="manual">常规评测</el-radio>
            <el-radio value="held_out">留出集复测</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="将真实调用 agent（LLM 耗时/费用），请确认版本无误"
        />
      </el-form>
      <template #footer>
        <el-button @click="triggerVisible = false">取消</el-button>
        <el-button type="primary" :loading="triggering" @click="doTrigger">确认触发</el-button>
      </template>
    </el-dialog>

    <!-- L4 证据 dialog -->
    <el-dialog v-model="evidenceVisible" width="860px" top="4vh">
      <template #header>评测证据（<TermTip term="L4" />）</template>
      <div v-if="evidence" v-loading="evidenceLoading" class="evidence">
        <el-collapse>
          <el-collapse-item title="Answer（agent 最终回答）" name="answer">
            <pre class="pre">{{ evidence.answer || '(空)' }}</pre>
          </el-collapse-item>
          <el-collapse-item title="Reasoning（思考链全文）" name="reasoning">
            <pre class="pre">{{ evidence.reasoning || '(空)' }}</pre>
          </el-collapse-item>
          <el-collapse-item v-if="evidenceDims(evidence).length" title="维度判定" name="judge">
            <!-- 四维判定：语义维度 judge 判（score+reason）；准确率维度规则断言判（通过数/总数） -->
            <div v-for="(d, i) in evidenceDims(evidence)" :key="i" class="judge-row">
              <el-tag size="small">{{ DIM_LABEL[d.code] || d.code }}</el-tag>
              <template v-if="d.kind === 'judge'">
                <span class="judge-score">score={{ fmtScore(d.score) }}</span>
                <div class="judge-reason">{{ d.reason }}</div>
              </template>
              <span v-else class="judge-assert">{{ d.reason }}</span>
            </div>
          </el-collapse-item>
          <el-collapse-item
            v-if="evidence.assertion_results && evidence.assertion_results.length"
            title="断言详情"
            name="assert"
          >
            <div v-for="(a, i) in evidence.assertion_results" :key="i" class="assert-row">
              <div>
                <span class="assert-op">[{{ a.dimension || '-' }}] {{ a.op }}</span>
                <span v-if="a.pass" class="sig up">PASS</span>
                <span v-else class="sig down">FAIL</span>
              </div>
              <pre class="pre">{{ JSON.stringify(a, null, 2) }}</pre>
            </div>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.tool_calls && evidence.tool_calls.length" title="工具调用" name="tools">
            <pre class="pre">{{ JSON.stringify(evidence.tool_calls, null, 2) }}</pre>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.usage || evidence.timing" title="Usage / Timing" name="meta">
            <!-- P2-D16：原始 JSON 改可读说明（usage/timing 各 attempt 逐条展示；ts 为毫秒时间戳→可读时间） -->
            <div v-for="(u, i) in (evidence.usage || [])" :key="'u' + i" class="meta-block">
              <div class="meta-title">调用 #{{ i + 1 }} · token 用量</div>
              <div class="meta-row">输入 token：{{ u?.prompt_tokens ?? 'N/A' }}</div>
              <div class="meta-row">输出 token：{{ u?.completion_tokens ?? 'N/A' }}</div>
              <div class="meta-row">总 token：{{ u?.total_tokens ?? 'N/A' }}</div>
              <div class="meta-row">应用缓存命中：{{ u?.cached ? '是' : '否' }}</div>
              <div class="meta-row">时间：{{ u?.ts ? fmtTime(u.ts) : '—' }}</div>
            </div>
            <div v-for="(t, i) in (evidence.timing || [])" :key="'t' + i" class="meta-block">
              <div class="meta-title">调用 #{{ i + 1 }} · 耗时</div>
              <div class="meta-row">首字延迟（TTFT）：{{ fmtMs(t?.first_token_ts) }}</div>
              <div class="meta-row">端到端（E2E）：{{ fmtMs(t?.end_ts) }}</div>
            </div>
            <div v-if="!(evidence.usage?.length) && !(evidence.timing?.length)" class="dim-note">
              无 Usage / Timing 数据
            </div>
          </el-collapse-item>
          <el-collapse-item v-if="evidence.error_type" title="错误信息" name="err">
            <pre class="pre">{{ evidence.error_type }}: {{ evidence.error_detail }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import TermTip from '../components/TermTip.vue'
import { listAgents } from '../api/agents'
import { listSuites } from '../api/suites'
import {
  listRuns, createRun, cancelRun, rerunRun,
  listRunResults, listRunFailures, getResultEvidence,
} from '../api/runs'
import {
  getGate, getTrend, getCompare,
  getPerf, getCost, getCoverage, getBaseline,
} from '../api/dashboard'
import { listModelPrices } from '../api/config'
import { createExport, downloadExport } from '../api/exports'
import EChart from '../components/EChart.vue'
import { filterRuns, paginate } from '../utils/runFilter'

// ---- 常量 ----
const DIM_LABEL = {
  completeness: '完成度',
  factuality: '事实性',
  reasoning_quality: '思考链',
  tool_usage: '工具使用',
  ttft: '首字延迟',
  e2e: '端到端延迟',
  token_cost: 'Token 成本',
}
// 展开行「维度判定」：语义维度 judge 理由。读 row.judge_results（staff 才有，viewer 为 null → 权限天然正确）。
// 不能读 score_per_dimension.detail.reason——该字段未做 staff 过滤，直接展示会向 viewer 泄露 judge 证据（D3）
const judgeReason = (row, code) => {
  const j = (row.judge_results || []).find((x) => x.dimension === code)
  return j ? j.reason : null
}
// 证据面板四维判定组装：语义维度取 judge_results（score+reason）；准确率维度从全量断言反推统计
// （证据接口不返回 score_per_dimension，断言明细 basic 级 viewer 可见，统计同级别）
const evidenceDims = (ev) => {
  if (!ev) return []
  const dims = []
  const seen = new Set()
  for (const j of ev.judge_results || []) {
    dims.push({ code: j.dimension, score: j.score, reason: j.reason, kind: 'judge' })
    seen.add(j.dimension)
  }
  const byDim = {}
  for (const a of ev.assertion_results || []) {
    (byDim[a.dimension] = byDim[a.dimension] || []).push(a)
  }
  for (const [dim, rows] of Object.entries(byDim)) {
    if (seen.has(dim)) continue
    const pass = rows.filter((r) => r.pass).length
    dims.push({ code: dim, score: null, reason: `断言 ${pass}/${rows.length} 通过`, kind: 'assert' })
  }
  return dims
}
const RUN_STATUS = {
  pending: { label: '等待中', type: 'info' },
  running: { label: '执行中', type: 'primary' },
  scoring: { label: '评分中', type: 'warning' },
  scoring_failed: { label: '评分失败', type: 'danger' },
  completed: { label: '完成', type: 'success' },
  partial_failed: { label: '部分失败', type: 'danger' },
  timeout: { label: '超时', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
}
const PF = {
  pass: { label: '通过', type: 'success' },
  fail: { label: '失败', type: 'danger' },
  error: { label: '错误', type: 'warning' },
  na: { label: 'N/A', type: 'info' },
}
const ACTIVE = ['pending', 'running', 'scoring']
// P2-C4：锚定结尾（原只验前缀，`1.2.3<script>` 能通过 → 图表 tooltip XSS）
const SEMVER = /^\d+\.\d+\.\d+$/

const auth = useAuthStore()
const { isAdmin } = storeToRefs(auth)
const isStaff = computed(() => ['admin', 'evaluator'].includes(auth.role))

// ---- 数据 ----
const loading = ref(false)
const gateList = ref([])
const agentOptions = ref([])
const activeAgentId = ref(null)
const runsLoading = ref(false)
const runList = ref([])
const hasMoreRuns = ref(false) // 后端 limit 上限 200，run 量超出时提示仅展示最新
const trendData = ref([])
const compareA = ref(null)
const compareB = ref(null)
const compareData = ref(null)
const curRunId = ref(null)
// P2-9 评测态追溯：当前 run 评测时的 gq 知识版本（list_runs env_snapshot 回填透出）
const curRunKnowledge = ref(null)
const resultsLoading = ref(false)
const runResults = ref([])
// P2-9 应用缓存命中聚合：run_results 每行 cache_hit（usage.cached=True）为真即计入
const cacheHitCount = computed(() => runResults.value.filter((r) => r.cache_hit).length)
const exporting = ref(false)
const runFailures = ref({ items: [], top_reasons: [] })
const triggerVisible = ref(false)
const triggering = ref(false)
const triggerForm = reactive({ agent_id: null, suite_id: null, version: '', trigger_type: 'manual' })
const suiteOptions = ref([])
const evidenceVisible = ref(false)
const evidenceLoading = ref(false)
const evidence = ref(null)
const detailVisible = ref(false) // L3 用例明细抽屉

// ---- 三面板（性能/成本/覆盖，轻量化合并，跟随顶部 agent 下钻）----
const activePanel = ref('perf')
const perfRows = ref([])
const costRows = ref([])
const modelPrices = ref([])
const coverage = ref(null)
const baseline = ref({ run: null, interfaces: [], is_gold_count: 0 })
// P2-D16 展示态：同步接口（contract-check）无 TTFT 全 null，需隐藏系列+说明；成本拆图需判空
const perfHasTTFT = computed(() => perfRows.value.some((r) => r.ttft_p50 != null))
const costNoPrice = computed(() => costRows.value.length && costRows.value.every((r) => r.total_cost == null))
const costHasMoney = computed(() => costRows.value.some((r) => r.total_cost != null))

let pollTimer = null
let pollCount = 0

// ---- 格式化 ----
const agentName = (id) => (agentOptions.value.find((a) => a.id === id) || {}).name || `#${id}`
const runStatus = (s) => RUN_STATUS[s] || { label: s, type: 'info' }
const pfTag = (v) => (PF[v] || {}).type || 'info'
const pfLabel = (v) => (PF[v] || {}).label || v
const isActive = (s) => ACTIVE.includes(s)
const fmtTime = (t) => (t ? new Date(t).toLocaleString('zh-CN') : '-')
const fmtRate = (r) => (r == null ? 'N/A' : `${(r * 100).toFixed(1)}%`)
// #12 预计剩余时长：s / m+s / h+m
const fmtDuration = (sec) => {
  if (sec == null) return '—'
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m${sec % 60 ? ` ${sec % 60}s` : ''}`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`
}
// #12 单 case 状态流（stage 推断）
const STAGE_LABEL = { error: '失败', judging: '判分中', judge_failed: '判分失败', completed: '完成' }
const stageLabel = (s) => STAGE_LABEL[s] || s
// 分数统一兜底：保留 2 位小数并去尾零（避免 100.0 / 2.092 这类长尾展示，走查 #3）
const fmtScore = (v) => (v == null ? 'N/A' : Number(Number(v).toFixed(2)))
// P2-D16 性能时间：后端存秒，前端统一转毫秒显示（TTFT/E2E 行业惯例 ms）
const fmtMs = (v) => (v == null ? 'N/A' : `${Math.round(v * 1000)} ms`)
// P2-C4：tooltip 走 ECharts HTML 渲染，任何动态串进 HTML 前必须转义（存量恶意 version 兜底）
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')

// ---- 图表 option ----
const trendOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const r = trendData.value[params[0].dataIndex]
      return [
        `#${r.run_id} ${esc(r.version)}（${esc(runStatus(r.status).label)}）`,
        `总分：${fmtScore(r.agent_score)}`,
        `通过率：${fmtRate(r.pass_rate)}（${r.pass_case}/${r.total_case}）`,
        `TTFT p50：${fmtMs(r.ttft_p50)}`,
        `E2E p50：${fmtMs(r.e2e_p50)}`,
        `${fmtTime(r.started_at)}`,
      ].join('<br/>')
    },
  },
  grid: { left: 48, right: 24, top: 24, bottom: 32 },
  xAxis: {
    type: 'category',
    // 横轴带版本（如 #3027 / 0.2.0）展示版本演进，与成本图的纯 run_id 不同是有意的：
    // 趋势图无 markArea 定位需求，标签带版本更有信息量；成本图 markArea 需精确 category 匹配才用纯 run_id
    data: trendData.value.map((r) => `#${r.run_id}\n${r.version}`),
    // 标签放不下时自动隐藏中间部分（ECharts hideOverlap），run 多不互相遮挡
    axisLabel: { interval: 'auto', hideOverlap: true },
  },
  yAxis: { type: 'value', min: 0, max: 100, name: '总分' },
  series: [
    {
      type: 'line',
      smooth: true,
      symbolSize: 8,
      data: trendData.value.map((r) => r.agent_score),
      lineStyle: { width: 2 },
    },
  ],
}))

const headLabel = (h) => `#${h.run_id} ${h.version}`

// P2-D16：版本对比改雷达图（双版本多边形叠加），下方表格保留精确 Δ
const compareOption = computed(() => {
  if (!compareData.value) return {}
  const dims = compareData.value.dims
  const la = headLabel(compareData.value.a)
  const lb = headLabel(compareData.value.b)
  // 雷达 data 需数值：mean 为 null（数据不足）以 0 占位，tooltip 回显真实值避免误导
  const num = (v) => (v == null ? 0 : Number(v))
  const dimName = (i) => DIM_LABEL[dims[i].code] || dims[i].code
  const fmtVal = (i, key) => (dims[i][key] == null ? 'N/A' : fmtScore(dims[i][key]))
  return {
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const lines = [`<b>${esc(params.name)}</b>`]
        dims.forEach((d, i) => {
          lines.push(`${dimName(i)}：A=${fmtVal(i, 'mean_a')}　B=${fmtVal(i, 'mean_b')}`)
        })
        return lines.join('<br/>')
      },
    },
    legend: { data: [la, lb], bottom: 0 },
    radar: {
      indicator: dims.map((d) => ({ name: DIM_LABEL[d.code] || d.code, max: 100 })),
      radius: '65%',
      splitArea: { areaStyle: { color: ['rgba(84,112,198,0.03)', 'rgba(84,112,198,0.06)'] } },
      axisName: { color: '#606266' },
    },
    series: [
      {
        type: 'radar',
        symbolSize: 5,
        data: [
          {
            name: la,
            value: dims.map((d) => num(d.mean_a)),
            lineStyle: { color: '#5470c6', width: 2 },
            itemStyle: { color: '#5470c6' },
            areaStyle: { color: 'rgba(84,112,198,0.15)' },
          },
          {
            name: lb,
            value: dims.map((d) => num(d.mean_b)),
            lineStyle: { color: '#91cc75', width: 2 },
            itemStyle: { color: '#91cc75' },
            areaStyle: { color: 'rgba(145,204,117,0.15)' },
          },
        ],
      },
    ],
  }
})

// P2-D16：性能系列动态生成——全 null（同步接口无 TTFT）的系列不渲染也不进 legend；
// 后端存秒 ×1000 转 ms 显示；TTFT 用虚线、E2E 用实线区分（首字≈终答时两线易重叠）
const perfSeries = computed(() => {
  const defs = [
    { name: 'TTFT p50', key: 'ttft_p50', dashed: true },
    { name: 'TTFT p95', key: 'ttft_p95', dashed: true },
    { name: 'E2E p50', key: 'e2e_p50', dashed: false },
    { name: 'E2E p95', key: 'e2e_p95', dashed: false },
  ]
  const has = (key) => perfRows.value.some((r) => r[key] != null)
  return defs
    .filter((d) => has(d.key))
    .map((d) => ({
      name: d.name,
      type: 'line',
      smooth: true,
      symbolSize: 8,
      data: perfRows.value.map((r) => (r[d.key] == null ? null : Math.round(r[d.key] * 1000))),
      lineStyle: { width: 2, type: d.dashed ? 'dashed' : 'solid' },
    }))
})

const perfOption = computed(() => {
  const series = perfSeries.value
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const r = perfRows.value[params[0].dataIndex]
        const lines = [`<b>#${r.run_id} ${esc(r.version)}</b>`]
        for (const p of params) {
          lines.push(`${p.marker}${p.seriesName}：${p.value == null ? 'N/A' : `${p.value} ms`}`)
        }
        lines.push(fmtTime(r.started_at))
        return lines.join('<br/>')
      },
    },
    legend: { data: series.map((s) => s.name) },
    grid: { left: 56, right: 24, top: 36, bottom: 32 },
    xAxis: {
      type: 'category',
      data: perfRows.value.map((r) => `#${r.run_id}\n${r.version}`),
    },
    yAxis: { type: 'value', name: 'ms' },
    series,
  }
})

// P2-D16：成本拆两图——Token 总量与成本量级差 5 个数量级，双轴混排把成本压成贴底线；
// 成本 tab 系列色：series itemStyle 与图例块共用，避免多处硬编码色值（换主题只改这里）
const COLOR_TOKEN = '#5470c6'
const COLOR_COST = '#67c23a'
const COLOR_NA_AREA = 'rgba(150,150,150,0.12)'

// 成本图横轴 category 值：仅 run_id（自增主键，纯数字，无拼接歧义，排序变化不标错列）。
// 版本只在 tooltip / 表格展示，不拼进横轴——标签更短，run 多时也能放下。
// xAxis.data 与 markArea 定位同源共用此函数，避免两处表达式漂移
const costCat = (r) => `#${r.run_id}`

// 各自独立单轴，成本波动才可读
const costTokensOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const r = costRows.value[params[0].dataIndex]
      return [
        `<b>#${r.run_id} ${esc(r.version)}</b>`,
        `Token 总量：${r.total_tokens ?? 'N/A'}`,
        fmtTime(r.started_at),
      ].join('<br/>')
    },
  },
  grid: { left: 64, right: 24, top: 28, bottom: 24 },
  xAxis: {
    type: 'category',
    data: costRows.value.map(costCat),
    // 标签放不下时自动隐藏中间部分（ECharts hideOverlap），run 多不互相遮挡；完整 run_id 靠 tooltip 悬浮读取
    axisLabel: { interval: 'auto', hideOverlap: true },
  },
  yAxis: { type: 'value', name: 'tokens' },
  series: [
    {
      name: 'Token 总量',
      type: 'line',
      data: costRows.value.map((r) => r.total_tokens),
      smooth: true,
      // 无成本/无 token 的 run（usage 缺失或模型未配单价）断开折线而非跨 null 直连，
      // 避免误读成「那段时间数据连续」（tooltip 已显示 N/A）
      connectNulls: false,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color: COLOR_TOKEN },
      // 无 token 的 run 处标灰 + 标 #run_id N/A，与断开的折线呼应，避免误读成连续。
      // 定位用 xAxis 的 category 值（与 xAxis.data 同源共 costCat），排序变化也不标错列。
      // label 直接带 run_id：即便横轴标签因多 run 被 hideOverlap 隐藏，灰条处也能直读是哪个 run
      markArea: {
        silent: true,
        itemStyle: { color: COLOR_NA_AREA },
        label: {
          show: true,
          position: 'insideTop',
          color: '#999',
          fontSize: 10,
        },
        // 灰条 label 用静态字符串（数据项带 run_id）：纯 region 的 markArea（只指定 xAxis）
        // label formatter 参数没有 value 属性，读 p.value[0] 会抛 TypeError，故改为 data item 自带 label
        data: costRows.value.flatMap((r) =>
          r.total_tokens == null
            ? [[
                {
                  xAxis: costCat(r),
                  label: {
                    show: true,
                    position: 'insideTop',
                    color: '#999',
                    fontSize: 10,
                    formatter: `${costCat(r)} N/A`,
                  },
                },
                { xAxis: costCat(r) },
              ]]
            : []),
      },
    },
  ],
}))

const costMoneyOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const r = costRows.value[params[0].dataIndex]
      return [
        `<b>#${r.run_id} ${esc(r.version)}</b>`,
        `成本：${r.total_cost == null ? 'N/A' : `${fmtCost(r.total_cost)} 元`}`,
        fmtTime(r.started_at),
      ].join('<br/>')
    },
  },
  grid: { left: 64, right: 24, top: 28, bottom: 24 },
  xAxis: {
    type: 'category',
    data: costRows.value.map(costCat),
    // 标签放不下时自动隐藏中间部分（ECharts hideOverlap），run 多不互相遮挡；完整 run_id 靠 tooltip 悬浮读取
    axisLabel: { interval: 'auto', hideOverlap: true },
  },
  yAxis: { type: 'value', name: '元' },
  series: [
    {
      name: '成本(元)',
      type: 'line',
      data: costRows.value.map((r) => r.total_cost),
      smooth: true,
      // 无成本/无 token 的 run（usage 缺失或模型未配单价）断开折线而非跨 null 直连，
      // 避免误读成「那段时间数据连续」（tooltip 已显示 N/A）
      connectNulls: false,
      symbol: 'circle',
      symbolSize: 6,
      itemStyle: { color: COLOR_COST },
      // 无成本（模型未配单价或 usage 缺失）的 run 处标灰 + 标 #run_id N/A，与断开的折线呼应。
      // 定位用 xAxis 的 category 值（与 xAxis.data 同源共 costCat），排序变化也不标错列。
      // label 直接带 run_id：即便横轴标签因多 run 被 hideOverlap 隐藏，灰条处也能直读是哪个 run
      markArea: {
        silent: true,
        itemStyle: { color: COLOR_NA_AREA },
        label: {
          show: true,
          position: 'insideTop',
          color: '#999',
          fontSize: 10,
        },
        // 灰条 label 用静态字符串（数据项带 run_id）：纯 region 的 markArea（只指定 xAxis）
        // label formatter 参数没有 value 属性，读 p.value[0] 会抛 TypeError，故改为 data item 自带 label
        data: costRows.value.flatMap((r) =>
          r.total_cost == null
            ? [[
                {
                  xAxis: costCat(r),
                  label: {
                    show: true,
                    position: 'insideTop',
                    color: '#999',
                    fontSize: 10,
                    formatter: `${costCat(r)} N/A`,
                  },
                },
                { xAxis: costCat(r) },
              ]]
            : []),
      },
    },
  ],
}))

// 接口 × 维度扁平化（覆盖 tab 基线对比表数据）
const baselineRows = computed(() => {
  const rows = []
  for (const it of baseline.value.interfaces || []) {
    for (const d of it.dims || []) {
      rows.push({
        iface: `${it.name}（${it.case_count} 用例）`,
        code: d.code, score: d.score, target: d.target, gap: d.gap, met: d.met,
      })
    }
  }
  return rows
})
const fmtCost = (v) => (v == null ? 'N/A' : Number(v).toFixed(6).replace(/\.?0+$/, ''))
const pct = (r) => (r == null ? 0 : Math.round(r * 100))

// ---- 加载 ----
async function refreshAll() {
  loading.value = true
  try {
    // 评测记录跟随当前选中 agent（联动一致）；未选则全量
    await Promise.all([loadAgents(), loadGate(), loadRuns(activeAgentId.value || undefined)])
    if (activeAgentId.value) {
      await Promise.all([loadTrend(activeAgentId.value), loadPanels(activeAgentId.value)])
    }
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  const data = await listAgents()
  agentOptions.value = data
}

async function loadGate() {
  gateList.value = await getGate()
}

async function loadRuns(agentId) {
  runsLoading.value = true
  try {
    // 全量拉取（后端 limit 上限 200）→ 前端本地过滤 + 本地分页，避免 offset 分页与本地过滤冲突。
    // agentId 传参：门禁墙/下拉选 agent 后评测记录联动只显示该 agent 的 run（后端已支持 agent_id）
    const params = agentId ? { limit: 200, agent_id: agentId } : { limit: 200 }
    runList.value = await listRuns(params)
    runPage.value = 1
    // 截断探测：取第 201 条判断是否还有更早记录（复用现有 offset 参数，零后端改动）；失败静默不阻断
    try {
      const extra = await listRuns({ limit: 1, offset: 200, ...(agentId ? { agent_id: agentId } : {}) })
      hasMoreRuns.value = extra.length > 0
    } catch (e) {
      hasMoreRuns.value = false
    }
  } finally {
    runsLoading.value = false
  }
}

// ---- 评测记录分页/搜索（run 量 <200，本地过滤足够；超限再评估后端加 search 参数） ----
const runSearch = ref('')
const runStatusFilter = ref('')
const runPage = ref(1)
const PAGE_SIZE = 5
const filteredRuns = computed(() =>
  filterRuns(runList.value, {
    statusFilter: runStatusFilter.value,
    search: runSearch.value,
    agentName,
    runStatus,
  })
)
const pagedRuns = computed(() => paginate(filteredRuns.value, runPage.value, PAGE_SIZE))
watch([runSearch, runStatusFilter], () => { runPage.value = 1 })

async function loadTrend(agentId) {
  trendData.value = await getTrend(agentId)
  // 有 ≥2 次终态 run 时自动预选最近两次对比。
  // 语义约定：A=历史（基准）、B=最新（被测）——后端 Δ = B−A，B 为最新时 Δ 即"新版本较历史的变化"。
  const len = trendData.value.length
  if (len >= 2) {
    compareA.value = trendData.value[len - 2].run_id
    compareB.value = trendData.value[len - 1].run_id
    await doCompare()
  } else {
    compareA.value = len ? trendData.value[0].run_id : null
    compareB.value = null
    compareData.value = null
  }
}

// 三面板数据：性能/成本/覆盖（含模型单价）一次并行拉取
async function loadPanels(agentId) {
  if (!agentId) {
    perfRows.value = []
    costRows.value = []
    modelPrices.value = []
    coverage.value = null
    baseline.value = { run: null, interfaces: [], is_gold_count: 0 }
    return
  }
  const [perf, cost, prices, cov, base] = await Promise.all([
    getPerf(agentId),
    getCost(agentId),
    listModelPrices(),
    getCoverage(agentId),
    getBaseline(agentId),
  ])
  // 展示最近 10 条终态 run（perf/cost 端点返回全量，低频页面量级小，前端 slice 隔离显示层）
  perfRows.value = perf.slice(-10)
  costRows.value = cost.slice(-10)
  modelPrices.value = prices
  coverage.value = cov
  baseline.value = base
}

function selectAgent(g) {
  activeAgentId.value = g.agent_id
  resetDrill()
  loadTrend(g.agent_id)
  loadPanels(g.agent_id)
  loadRuns(g.agent_id)
}

function onAgentChange() {
  resetDrill()
  if (activeAgentId.value) {
    loadTrend(activeAgentId.value)
    loadPanels(activeAgentId.value)
    loadRuns(activeAgentId.value)
  } else {
    loadPanels(null)
    loadRuns()
  }
}

// 切换 agent 时清空 L2/L3 下钻
function resetDrill() {
  compareA.value = null
  compareB.value = null
  compareData.value = null
  curRunId.value = null
  curRunKnowledge.value = null
  detailVisible.value = false
  runResults.value = []
  runFailures.value = { items: [], top_reasons: [] }
  evidence.value = null
}

async function doCompare() {
  if (!compareA.value || !compareB.value || compareA.value === compareB.value) return
  compareData.value = await getCompare(compareA.value, compareB.value)
}

async function openRunDetail(row) {
  curRunId.value = row.id
  curRunKnowledge.value = row.knowledge_version || null
  resultsLoading.value = true
  detailVisible.value = true
  try {
    const [results, failures] = await Promise.all([
      listRunResults(row.id),
      listRunFailures(row.id),
    ])
    runResults.value = results
    runFailures.value = failures
  } finally {
    resultsLoading.value = false
  }
}

// ---- 触发评测 ----
function openTrigger() {
  triggerForm.agent_id = activeAgentId.value
  triggerForm.suite_id = null
  triggerForm.version = ''
  triggerForm.trigger_type = 'manual'
  suiteOptions.value = []
  if (triggerForm.agent_id) onTriggerAgentChange()
  triggerVisible.value = true
}

async function onTriggerAgentChange() {
  triggerForm.suite_id = null
  if (!triggerForm.agent_id) {
    suiteOptions.value = []
    return
  }
  suiteOptions.value = await listSuites(triggerForm.agent_id)
}

async function doTrigger() {
  if (!triggerForm.agent_id || !triggerForm.suite_id) {
    ElMessage.warning('请选择 Agent 和测试套件')
    return
  }
  if (!SEMVER.test(triggerForm.version)) {
    ElMessage.warning('版本需符合 semver（如 1.2.3）')
    return
  }
  try {
    await ElMessageBox.confirm(
      '将真实调用 agent 执行评测（LLM 耗时/费用）。确认继续？',
      '触发评测',
      { type: 'warning', confirmButtonText: '确认触发', cancelButtonText: '再想想' }
    )
  } catch {
    return
  }
  triggering.value = true
  try {
    const res = await createRun({
      agent_id: triggerForm.agent_id,
      suite_id: triggerForm.suite_id,
      version: triggerForm.version,
      trigger_type: triggerForm.trigger_type,
    })
    ElMessage.success(`run #${res.id} 已创建，正在执行`)
    triggerVisible.value = false
    await refreshAll()
    startPoll()
  } finally {
    triggering.value = false
  }
}

// 触发后轮询刷新（run 全终态或超时自动停）
function startPoll() {
  stopPoll()
  pollCount = 0
  pollTimer = setInterval(async () => {
    pollCount += 1
    await refreshAll()
    if (pollCount >= 45) stopPoll() // 最多约 3 分钟
    else if (!runList.value.some((r) => isActive(r.status))) stopPoll()
  }, 4000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ---- run 操作 ----
async function doCancel(row) {
  try {
    await ElMessageBox.confirm(`确认取消 run #${row.id}？`, '取消评测', { type: 'warning' })
  } catch {
    return
  }
  await cancelRun(row.id)
  ElMessage.success('已取消')
  refreshAll()
}

async function doRerun(row) {
  // 子集 run 重跑同一批：显式传 case_ids（=row.case_ids），全量 run 传 null → 后端继承原 run 子集（null=全量）
  const scope = row.case_ids?.length ? `（子集 ${row.case_ids.length} 例）` : ''
  try {
    await ElMessageBox.confirm(
      `确认重跑 run #${row.id}（${row.version}）${scope}？将真实调用 agent，耗时/费用。`,
      '重跑评测',
      { type: 'warning' }
    )
  } catch {
    return
  }
  const res = await rerunRun(row.id, { case_ids: row.case_ids })
  ElMessage.success(`已创建 run #${res.id}`)
  refreshAll()
  startPoll()
}

// 定向重跑未达标（#3）：拉 run_failures 失败 case_ids，仅重跑这批（P2-6 起返回 {items, top_reasons}）
async function doRerunFailed(row) {
  const failures = await listRunFailures(row.id)
  const ids = (failures.items || []).map((f) => f.case_id)
  if (!ids.length) {
    ElMessage.info('该 run 无未达标用例，无需定向重跑')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认仅重跑 run #${row.id} 的 ${ids.length} 个未达标用例？将真实调用 agent，耗时/费用。`,
      '定向重跑未达标',
      { type: 'warning' }
    )
  } catch {
    return
  }
  const res = await rerunRun(row.id, { case_ids: ids })
  ElMessage.success(`已创建定向 run #${res.id}（${ids.length} 例）`)
  refreshAll()
  startPoll()
}

// ---- 报告导出（6.1）----
async function doExport(format) {
  if (!curRunId.value) return
  exporting.value = true
  try {
    const data = await createExport(curRunId.value, format)
    await downloadExport(data.token, data.filename)
    ElMessage.success(`${format === 'pdf' ? 'PDF' : 'Excel'} 报告已下载`)
  } catch (e) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

// ---- L4 证据 ----
async function openEvidence(row) {
  evidenceVisible.value = true
  evidenceLoading.value = true
  evidence.value = null
  try {
    evidence.value = await getResultEvidence(curRunId.value, row.id)
  } finally {
    evidenceLoading.value = false
  }
}

onMounted(() => {
  refreshAll()
})

onBeforeUnmount(() => {
  stopPoll()
})
</script>

<style scoped>
.block {
  margin-bottom: 16px;
}
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
}
.run-toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.gate-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.gate-card:hover {
  border-color: var(--el-color-primary);
  box-shadow: var(--el-box-shadow-light);
}
.gate-card.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.gate-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.gate-name {
  font-weight: 600;
  font-size: 15px;
}
.score-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--el-color-primary);
}
.score-unit {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.gate-score {
  margin-bottom: 8px;
}
.gate-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.gate-stale {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.compare-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.vs {
  color: var(--el-text-color-secondary);
}
.export-bar {
  float: right;
  margin-left: auto; /* drawer header 为 flex 容器时靠右 */
}
.export-bar .el-button + .el-button {
  margin-left: 8px;
}
.drawer-failures {
  margin-top: 16px;
}
.drawer-failures-title {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
  font-weight: 600;
}
.sig.up {
  color: #67c23a;
  font-weight: 600;
}
.sig.down {
  color: #f56c6c;
  font-weight: 600;
}
.sig.flat {
  color: var(--el-text-color-secondary);
}
.sig.na {
  color: var(--el-text-color-placeholder);
}
.sig-hint {
  margin-left: 2px;
  color: var(--el-text-color-placeholder);
  cursor: help;
  font-size: 12px;
}
.dim-line {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 20px;
}
.dim-label {
  color: var(--el-text-color-secondary);
  min-width: 52px;
}
.dim-na {
  color: var(--el-text-color-placeholder);
}
.err {
  color: #f56c6c;
  font-size: 12px;
}
.fail-title {
  font-weight: 600;
}
.fail-score {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.fail-sec {
  margin-top: 8px;
}
.fail-sec-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.fail-dim {
  font-size: 13px;
  line-height: 22px;
  padding-left: 8px;
}
/* 维度判定：分数高亮 + 判定说明 + 失败断言缩进 */
.dim-score {
  color: var(--el-color-primary);
  font-weight: 600;
}
.dim-reason {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.assert-fail {
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* P2-6 行内明细（expand）+ top 扣分原因 */
.row-detail {
  padding: 8px 12px;
}
.detail-sec {
  margin-bottom: 8px;
}
.detail-sec:last-child {
  margin-bottom: 0;
}
.detail-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.detail-empty {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}
.top-reasons {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.top-reasons-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.top-reason-item {
  margin-bottom: 6px;
}
.top-reason-item:last-child {
  margin-bottom: 0;
}
.top-reason-count {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.top-reason-sample {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding-left: 8px;
  line-height: 20px;
}
.pre {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 360px;
  overflow: auto;
}
.judge-row {
  margin-bottom: 10px;
}
.judge-score {
  margin-left: 8px;
  color: var(--el-color-primary);
  font-weight: 600;
}
.judge-assert {
  margin-left: 8px;
  color: var(--el-color-success);
  font-weight: 600;
}
.judge-reason {
  font-size: 13px;
  margin-top: 4px;
  padding-left: 8px;
}
.assert-row {
  margin-bottom: 10px;
}
.assert-op {
  font-weight: 600;
  margin-right: 8px;
}
/* P2-D16：证据 Usage/Timing 可读展示 */
.meta-block {
  margin-bottom: 10px;
}
.meta-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.meta-row {
  font-size: 13px;
  line-height: 22px;
}
/* 三面板（性能/成本/覆盖合并） */
.prices-head {
  display: flex;
  align-items: center;
  margin-top: 16px;
  font-weight: 600;
}
.cov-title {
  font-weight: 600;
  margin-bottom: 12px;
}
.coverage-body {
  display: flex;
  flex-direction: column;
}
.coverage-rate {
  display: flex;
  justify-content: center;
  margin-bottom: 8px;
}
.pct-num {
  font-size: 22px;
  font-weight: 700;
  color: var(--el-color-primary);
}
.pct-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.baseline-head {
  display: flex;
  align-items: center;
  margin-top: 16px;
  font-weight: 600;
}
.baseline-summary {
  font-size: 13px;
  color: var(--el-text-color-regular);
  margin-top: 8px;
}
.dim-none {
  color: var(--el-text-color-placeholder);
}
.gap-plus {
  color: var(--el-color-success);
  font-weight: 600;
}
.gap-minus {
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>
