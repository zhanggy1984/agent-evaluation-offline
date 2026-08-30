"""4.2 seed 数据：四家自研 agent 的 v2 manifest 快照（单一真相源）+ 派生 adapter_config。

Q3 起真相源 = MANIFEST_SNAPSHOTS（与 agent 侧 /api/contracts 同构的 v2 manifest，含
contract 段）。adapter_config 由 build_adapter_config 派生（Q1 等价性：与迁移前手写值
逐字符相等），seed.py 写库时内嵌 _manifest_v2 快照，discover 时与 agent 运行时对比
contract 段抓漂移（scaffold._adapter_drift）。模板域：{case.input.*} / {auth.*} / {prepare.*}。

用途：
- seed.py::_seed_agents 按 SEED_AGENTS 幂等建 agent + interface + Fernet 凭证 + 示例 suite/case
- verify_cs/cc/gq_e2e.py 的 ADAPTER_CFG 从这里 import（消除重复，单一来源）

端口约定（seed 的 base_url 必须稳定可并发）：cs 8000 / cc 8001 / sp 8002 / gq 8080，
由各 agent 的 docker-compose 宿主映射决定（sp 由 ${SP_APP_PORT:-8002} 提供）。
"""

from datetime import datetime

from app.core.contracts_v2 import build_adapter_config

# 结构维度指标（judge 增强后语义维度开启：factuality/reasoning_quality 由 judge 判分；
# 键名必须用标准维度码 reasoning_quality，曾误写 "reasoning" 导致该维度被静默跳过）
METRICS_STRUCT = {
    "completeness": {"enabled": True},
    "tool_usage": {"enabled": True},
    "factuality": {"enabled": True},
    "reasoning_quality": {"enabled": True},
}
# sp chat 无 tool_call（评审对话不检索），工具维度不适用
METRICS_SP = {
    "completeness": {"enabled": True},
    "tool_usage": {"enabled": False},
    "factuality": {"enabled": True},
    "reasoning_quality": {"enabled": True},
}
ASSERTION_ANSWER = [
    {"dimension": "completeness", "op": "field_nonempty", "args": {"path": "answer"}},
]

# ── judge factuality 参考依据文档（阶段 7.3 增强）：case.expected.reference_docs ──
# 注入 ground truth 原文供 judge 区分「忠实引用」vs「凭空编造」，根治信息不对称误判。
# 与 golden_answer 同理属平台定标准；文档更新需同步（本次固化，不引入动态同步）。
# gq 文档库为空 → 显式说明（与 golden_answer「检索问答」口径一致）。

REF_GQ_EMPTY = "（本用例参考文档库为空，无文档内容）"

# cc 缺生效日合同原文（contract-check/data/test-contracts/b1_missing_date.pdf 提取，
# 平台 uploads/cc_b1_missing_date.pdf 同文件，sha a8747f51）。
# 注意：本常量仅 b1「缺生效日合同」专属；b2 负金额 / b4 缺乙方 的 reference_docs 在
# expand_cases_78.py 的 REF_CC_B2 / REF_CC_B4（各自 PDF 文本层提取），勿复用（T225 P0）。
REF_CC_CONTRACT = """购销合同
甲方：北京华云科技有限公司
统一社会信用代码：91110108MA01XXXXXX
乙方：上海中远贸易有限公司
统一社会信用代码：91310000MA1XXXXXXX
第一条 合同标的
乙方供应甲方服务器设备一批，总金额人民币 100000 元（含税）。
第二条 合同类型
本合同为采购合同，适用《中华人民共和国民法典》相关规定。
第四条 付款方式
货到验收合格后 30 日内，甲方向乙方支付全部合同价款。
第五条 交货期限
乙方应于本合同生效后 45 日内完成交货。
第六条 违约责任
任何一方违约应赔偿对方由此遭受的全部损失，违约金为合同总价的 10%。
第七条 争议解决
因本合同引起的争议，双方协商解决；协商不成的，提交甲方所在地人民法院诉讼解决。
第八条 保密条款
双方应对本合同内容及履行过程中知悉的对方商业秘密严格保密。
第九条 不可抗力
因不可抗力导致本合同无法履行的，受影响方应在不可抗力发生之日起 7 日内书面通知对方。
第十条 合同解除
经双方协商一致，可解除本合同；一方违约导致合同目的无法实现的，守约方有权解除合同。
第十一条 通知条款
双方往来通知应以书面形式送达对方注册地址。
甲方（盖章）：北京华云科技有限公司 乙方（盖章）：上海中远贸易有限公司
签订日期：2026 年 1 月 1 日"""

# cs 知识库 4 文件拼接（customer-service/backend/app/rag/knowledge/，按文件顺序）
REF_CS_KNOWLEDGE = """# 常见问题 FAQ

## 退款多久到账？

审核通过后 1-3 个工作日原路退回。部分银行可能延迟 1-2 天，请留意银行卡/支付宝账单。

## 如何查询物流？

在订单详情页点击"查看物流"，或告诉我订单号，我帮您查询最新物流轨迹。

## 退货需要自己出运费吗？

质量问题由商家承担；无理由退货运费按是否包邮判定（见退换货政策）。

## 可以申请电子发票吗？

可以。订单签收后可申请电子发票，在订单详情页选择"申请开票"，一般 1 个工作日内开具，发送至预留邮箱。

## 退货包装有什么要求？

使用原包装或同类安全包装寄回，避免运输损坏。配件、赠品请一并寄回。

## 商品签收后发现瑕疵怎么办？

签收 48 小时内联系客服并提供照片，属于质量问题的一律免费退换，运费商家承担。

## 如何联系人工客服？

您可以通过以下方式联系人工客服：
1. 拨打客服热线（工作时间 9:00-21:00）；
2. 在官网或 App 内在线客服入口回复"转人工"；
3. 通过公众号或小程序留言，我们会尽快跟进处理。

以上为官方联系渠道，不存在其他非官方客服号码或渠道。

# 售后服务总则

## 审核时效

- 退货/退款/投诉工单，商家在 2 小时内完成审核（工作时间 9:00-21:00）。
- 超时未审核的，系统自动通过并进入退款流程。

## 质保与维修

- 电子产品质保期 12 个月（自签收日起）。
- 质保期内非人为损坏，免费维修或换新，运费由商家承担。
- 人为损坏、私自拆机、进水等不在质保范围。

## 客服渠道

- 客服热线（工作时间 9:00-21:00）。
- 在线客服：工作时间内平均响应 < 1 分钟。
- 投诉升级：对处理结果不满意，可提交投诉工单，24 小时内专人跟进。

## 投诉分级

- HIGH：重大客诉（人身安全、批量质量问题、涉及金额 > 5000 元），1 小时内响应。
- MEDIUM：一般客诉（服务质量、体验问题），4 小时内响应。
- LOW：建议类反馈，24 小时内回复。

# 退款政策

## 仅退款适用条件

- 未发货（订单状态 PAID）：可申请仅退款，商家确认后原路退回，通常即时到账。
- 已发货未签收（订单状态 SHIPPED）：不支持直接仅退款，需先拒收，物流退回后自动退款。
- 已签收（订单状态 DELIVERED）：不支持仅退款，须走退货流程。

## 退款时效

- 审核通过后，退款 1-3 个工作日原路退回支付账户。
- 大促期间（如双 11）审核时效可能延长至 48 小时。

## 退款金额

- 部分退货按退货商品实际金额退款。
- 优惠券分摊部分按比例退还；已使用优惠券不退回。

## 退款状态说明

- APPROVED：已通过，等待退款打款。
- PENDING：审核中。
- REJECTED：申请被拒，可联系客服了解原因。

# 退换货政策

## 退货时限

- 自签收之日起 7 天内，商品支持无理由退货。
- 超 7 天（以快递签收时间为准）原则上不接受退货，质量问题除外（可联系售后鉴定）。

## 商品完好定义

- 商品外包装、配件、吊牌完整，未经使用、无污渍、无磨损。
- 影响二次销售的商品不支持退货。
- 定制类、个性定做类商品（如定制手机支架、刻字商品）一经生产不支持退货。

## 不适用无理由退货的品类

- 定制商品、生鲜食品、贴身衣物（拆封后）、已激活的电子设备。

## 运费规则

- 因商品质量问题退货：运费由商家承担。
- 7 天无理由退货：非包邮订单，运费由买家承担；包邮订单由商家承担。
- 拒收件产生的退回运费，按快递公司实际费用结算。

## 退货流程

1. 在订单中申请退货并选择退货原因。
2. 商家审核通过后，将商品寄回指定地址。
3. 仓库签收并验收后，进入退款环节，退款 1-3 个工作日原路退回。"""

# sp BID-024 标书全文（smart-procurement/data/synthetic/bid_content/BID-024.txt，8 章）
REF_SP_BID024 = """第一章  公司概况
公司重视技术创新，每年研发投入占营收比例超过10%，拥有多项软件著作权与专利。近年来公司营收稳步增长，市场信誉良好，连续多年获得行业年度优秀解决方案供应商称号。

近年来公司营收稳步增长，市场信誉良好，连续多年获得行业年度优秀解决方案供应商称号。公司构建了覆盖硬件、网络、云服务等环节的合作伙伴生态，保障方案交付资源充足。

公司设有客户成功团队，通过定期回访与价值共创，持续提升客户合作体验与粘性。公司在全国设有多个区域服务中心，能够提供本地化的快速响应与实施交付支持。

第二章  项目理解与需求分析
系统需提供完善的容灾与备份能力，我方将设计同城与异地备份方案，保障极端场景下的业务连续性。我方将建立与采购人常态化的沟通协作机制，需求变更与进度风险及时同步，保障各方信息对称。

通过对某省电子政务外网系统集成项目建设目标与业务现状的深入调研，我方认为本项目核心在于构建统一、开放、可扩展的信息化支撑平台。系统建成后需具备清晰的验收标准与可量化指标，我方将在实施初期即与采购人共同明确验收基线。

项目交付将配套完整操作培训与使用手册，面向不同角色分层开展培训，确保系统上线即用。我方将建立与采购人常态化的沟通协作机制，需求变更与进度风险及时同步，保障各方信息对称。

第三章  系统架构方案
系统支持容器化部署与标准化编排，配合CI/CD流水线实现自动化构建、测试与发布，缩短交付周期。前端采用前后端分离架构，Vue 技术栈组件化开发，适配 PC 端与移动端，支持主流浏览器。系统设计充分考虑安全架构，在网关、应用、数据各层设置纵深防御，防范外部攻击与越权访问。

系统技术选型兼顾成熟度与开放性，支持国产化数据库与中间件替换，降低技术绑定风险。架构设计预留容器化部署与弹性伸缩能力，支持基于 Kubernetes 的自动扩缩容。系统设计充分考虑安全架构，在网关、应用、数据各层设置纵深防御，防范外部攻击与越权访问。

服务通过注册中心实现自动注册与发现，配合配置中心集中管理环境配置，支持动态调整与热更新。前端采用前后端分离架构，Vue 技术栈组件化开发，适配 PC 端与移动端，支持主流浏览器。系统具备容量规划与弹性伸缩能力，可根据业务水位自动调整资源，兼顾成本与性能平衡。

第四章  安全方案
运维层面实行最小权限原则，账号权限按角色最小化分配，操作留痕可追溯。网络边界部署Web应用防火墙与入侵防御设备，实时拦截恶意流量与已知攻击特征。

系统严格落实等保三级控制项，从技术与管理双维度满足合规要求，保障测评顺利通过。敏感数据采用加密存储，关键业务数据传输启用 TLS 加密，防止数据泄露与篡改。系统采用密钥管理服务统一管理加密密钥，密钥轮换与销毁流程规范，保障加密体系安全可控。

数据备份异地保存，关键数据支持时间点恢复，最大数据丢失量不超过24小时。系统强化账号生命周期管理，离职与转岗账号及时回收，弱口令与默认口令全面治理。

第五章  实施计划
需求阶段结束后实行需求冻结管理，变更统一走评审流程，控制范围蔓延对进度的影响。项目启动即召开启动会，明确各方职责与沟通机制，确认项目目标、范围与验收标准。项目建立周报、例会与里程碑评审机制，进度偏差及时预警并采取纠偏措施。

针对关键里程碑设置双周滚动检查，出现偏差及时纠偏，确保按期交付。项目启动即成立联合实施团队，制定详细里程碑计划，每周输出进度周报并召开例会。项目实行变更管理制度，范围、计划、需求的变更统一评估影响并经审批后执行。

第六章  项目团队配置
项目统一文档规范与模板，交付文档标准一致，便于采购人查阅与验收。项目组实施人员具备丰富的现场实施经验，能够高效完成部署、培训与上线支持。

项目对关键岗位设置备份人员，重要节点AB角色互备，防范人员波动风险。针对关键岗位设置AB角机制，核心人员变动时能够快速补位，降低交付风险。项目对团队成员开展项目背景与行业知识培训，保障方案设计与实施贴合业务。

项目对关键岗位设置备份人员，重要节点AB角色互备，防范人员波动风险。项目建立人员绩效考核与激励制度，保障团队投入度与交付质量。

第七章  质量保障与售后服务
我司定期开展客户满意度调查，针对反馈制定改进措施并跟踪落实效果。我司承诺服务期内免费提供系统优化建议与安全加固补丁更新。

我司提供系统停运维护的窗口协调服务，重大维护提前通知并安排错峰实施。系统故障按严重程度分级响应，重大故障优先处理并事后出具分析报告。

我司为关键硬件与系统提供备件保障与备机方案，降低故障导致的业务中断时长。我司提供系统停运维护的窗口协调服务，重大维护提前通知并安排错峰实施。我司定期开展客户满意度调查，针对反馈制定改进措施并跟踪落实效果。

第八章  商务承诺
我方愿意接受采购人与监理单位对项目实施过程的全过程监督。我方同意按合同约定缴纳质保金，质保期满且无质量问题后申请无息退还。我方承诺不将合同主要义务转包或违规分包，保障项目实施的连续性。

合同争议解决方式、适用法律及管辖约定，我方同意按合同条款执行。我方将按合同约定的免责条款执行，因不可抗力等法定情形导致的延误按约定处理。"""

# ====================================================================
# 单一真相源：4 家 v2 manifest 快照 + 派生 adapter_config（Q3）
# 快照与 agent 侧 GET /api/contracts 同构（agent + contract_version + interfaces +
# scenes + contract 段）。adapter_config 由 build_adapter_config 派生（Q1 等价性回归
# 锁住与迁移前手写值逐字符相等），seed.py 写库时内嵌 _manifest_v2，discover 时与 agent
# 运行时 contract 段对比抓漂移（scaffold._adapter_drift）。
# ====================================================================

GQ_LIBRARY_ID = 3  # 现存空文档库（无文档，检索空亦合法）；原 6 迁移后 id 漂移不存在，2026-08-25 修正

MANIFEST_SNAPSHOTS = {
    # ── customer-service：SSE，login→建 session→messages，事件名已统一（无需 field_map）──
    "customer-service": {
        "agent": "customer-service", "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/sessions/{sid}/messages", "method": "POST",
             "contract_type": "sse", "llm": True,
             "description": "客服会话对话（SSE 流式，透出 token/usage/done；token 事件含 content+delta 双字段，平台 field_map 可映射 answer）"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权（辅助接口）"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "order_query", "description": "订单查询"},
            {"tag": "after_sales", "description": "售后服务（退换/退款等）"},
            {"tag": "human_handoff", "description": "转人工客服"},
        ],
        "contract": {
            "type": "sse", "timeout": 120,
            "prepare": [
                {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
                 "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
                 "extract": {"token": "access_token"}},
                # cs 建 session 返回 {session_id}（非 id）：extract 指定映射
                {"name": "session", "method": "POST", "path": "/api/v1/sessions",
                 "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
                 "extract": {"id": "session_id"}},
            ],
            "request": {
                "path": "/api/v1/sessions/{{prepare.session.id}}/messages", "method": "POST",
                "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                            "Content-Type": "application/json"},
                "body": {"content": "{{input.content}}"},
            },
        },
    },
    # ── contract-check：同步 JSON，multipart 上传→轮询 WAITING_REVIEW→取 result，无鉴权 ──
    "contract-check": {
        "agent": "contract-check", "contract_version": "2.0",
        "interfaces": [
            {"name": "result", "path": "/api/tasks/{task_id}/result", "method": "GET",
             "contract_type": "sync", "llm": True,
             "description": "合同校验结果（同步 JSON，透出 answer/usage/timing/tool_calls）"},
            {"name": "upload", "path": "/api/files/upload", "method": "POST",
             "llm": False, "description": "上传合同文件（辅助接口）"},
        ],
        "scenes": [
            {"tag": "missing_date", "description": "缺失生效日期"},
            {"tag": "single_party", "description": "单方签署"},
            {"tag": "scanned_pdf", "description": "扫描件识别"},
            {"tag": "conflict", "description": "条款冲突"},
            {"tag": "genuine", "description": "合规合同"},
        ],
        "contract": {
            "type": "sync", "timeout": 300,
            "prepare": [
                {"name": "upload", "method": "POST", "path": "/api/files/upload",
                 "files": {"file": "{{input.file_path}}"},
                 "extract": {"task_id": "task_id"}},
                # 决策 #41：不 resume，取 WAITING_REVIEW 时的 result 打分（不产生假 review 记录）
                {"name": "wait_done", "poll": {
                    "path": "/api/tasks/{{prepare.upload.task_id}}",
                    "until": {"status": ["WAITING_REVIEW", "SUCCESS", "FAILED", "CANCELLED"]},
                    "interval": 2, "timeout": 300}},
            ],
            "request": {"path": "/api/tasks/{{prepare.upload.task_id}}/result", "method": "GET"},
        },
    },
    # ── smart-procurement：SSE，login→建评审→chat；answer{delta}/usage(全名)/done 口径一致 ──
    "smart-procurement": {
        "agent": "smart-procurement", "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/reviews/{review_id}/chat", "method": "POST",
             "contract_type": "sse", "llm": True,
             "description": "评审对话（SSE 流式，透出 answer/usage/done）"},
            {"name": "score", "path": "/api/v1/reviews/{review_id}/score", "method": "POST",
             "contract_type": "sse", "llm": True,
             "description": "AI 评分（SSE，含 tool_call knowledge_retrieval；报价维度走 price_calc，run 前需用例规避）"},
            {"name": "login", "path": "/api/v1/auth/login", "method": "POST",
             "llm": False, "description": "专家/管理员鉴权（辅助接口）"},
        ],
        "scenes": [
            {"tag": "tech_scheme", "description": "技术方案评审"},
            {"tag": "price", "description": "报价评审"},
            {"tag": "conflict_interest", "description": "利益冲突检测"},
            {"tag": "collusion", "description": "围串标检测"},
        ],
        "contract": {
            "type": "sse", "timeout": 120,
            "prepare": [
                {"name": "login", "method": "POST", "path": "/api/v1/auth/login",
                 "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
                 "extract": {"token": "access_token"}},
                # 评审需 FROZEN 标书 + 专家账号（display_name==expert.name 反查 expert_id）
                # review 每次新建会堆积（prepare 无幂等），测试环境定期清库（决策 58）
                {"name": "review", "method": "POST", "path": "/api/v1/reviews",
                 "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                             "Content-Type": "application/json"},
                 "body": {"bid_id": "{{input.bid_id}}", "dimension_id": "{{input.dimension_id}}"},
                 "extract": {"review_id": "review_id"}},
            ],
            "request": {
                "path": "/api/v1/reviews/{{prepare.review.review_id}}/chat", "method": "POST",
                "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                            "Content-Type": "application/json"},
                "body": {"question": "{{input.question}}"},
            },
            # sse 无需 field_map：sp 的 answer{delta} / usage{prompt,completion,total_tokens} /
            # done{content} 与统一口径一致
        },
    },
    # ── good-question：SSE，login→建 session→chat，token 事件需 field_map 映射为 answer ──
    "good-question": {
        "agent": "good-question", "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/chat/{session_id}", "method": "POST",
             "contract_type": "sse", "llm": True,
             "description": "知识问答（SSE 流式，LLM 自主决定是否检索，检索经 tool_call/sources 事件回传；token 事件经 field_map 映射 answer）"},
            {"name": "login", "path": "/api/auth/login", "method": "POST",
             "llm": False, "description": "会话鉴权（辅助接口）"},
        ],
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "doc_qa", "description": "文档检索问答"},
            {"tag": "no_hit", "description": "无命中/意图不明兜底（检索空时如实回复或引导澄清）"},
            {"tag": "summarize", "description": "文档内容总结"},
        ],
        "contract": {
            "type": "sse", "timeout": 180,
            "prepare": [
                {"name": "login", "method": "POST", "path": "/api/auth/login",
                 "body": {"username": "{{auth.username}}", "password": "{{auth.password}}"},
                 "extract": {"token": "access_token"}},
                {"name": "session", "method": "POST", "path": "/api/sessions",
                 "headers": {"Authorization": "Bearer {{prepare.login.token}}"},
                 "body": {"library_id": GQ_LIBRARY_ID},
                 "extract": {"id": "id"}},
            ],
            "request": {
                "path": "/api/chat/{{prepare.session.id}}", "method": "POST",
                "headers": {"Authorization": "Bearer {{prepare.login.token}}",
                            "Content-Type": "application/json"},
                "body": {"content": "{{input.content}}", "stream": True},
            },
            "sse": {"field_map": {"token": "answer"}},  # gq 终答事件名是 token 非 answer
        },
    },
}


def _derive_adapter(name: str) -> dict:
    """manifest 快照 → adapter_config（原始，不含 _manifest_v2；快照由 seed.py 写库时内嵌）。

    派生失败（快照与 build_adapter_config 契约不符）属于代码 bug，import 即炸而非运行时静默。
    """
    draft, errs = build_adapter_config(MANIFEST_SNAPSHOTS[name])
    if draft is None:
        raise RuntimeError(f"seed manifest 快照生成 adapter 失败: {name} -> {errs}")
    return draft.adapter_config


CS_ADAPTER_CFG = _derive_adapter("customer-service")
CC_ADAPTER_CFG = _derive_adapter("contract-check")
SP_ADAPTER_CFG = _derive_adapter("smart-procurement")
GQ_ADAPTER_CFG = _derive_adapter("good-question")

# ── 四家完整 seed 定义（seed.py::_seed_agents 消费）──
SEED_AGENTS = [
    {
        "name": "customer-service",
        "base_url": "http://host.docker.internal:8000",
        "adapter_type": "config",
        "adapter_config": CS_ADAPTER_CFG,
        "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/sessions/{sid}/messages", "method": "POST",
             "contract_type": "sse", "contract_version": "2.0"},
        ],
        "auth_secrets": None,  # P2-D17：凭证不入库明文，seed 时从 AGENT_AUTH_SECRETS env 注入
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "order_query", "description": "订单查询"},
            {"tag": "after_sales", "description": "售后服务（退换/退款等）"},
            {"tag": "human_handoff", "description": "转人工客服"},
        ],
        "sample_suite": {
            "name": "示例-基础问答", "description": "4.2 固化示例：普通问答（不触发 confirm 阻塞，决策 57）",
            "cases": [
                {"name": "打招呼", "description": "普通欢迎语", "interface": "chat",
                 "input_type": "text", "input": {"content": "你好，请问有什么可以帮助你的？"},
                 # 6.4 金标准：judge_gold_scores 是人工标注的期望 judge 判分，
                 # 漂移检测重判后与其比对一致率（score 步进 20，容差 10 = 要求等级一致）。
                 # 打招呼为纯问候无推理内容（judge 实判 reasoning_quality=0），该维度不参与
                 # 漂移检测（阶段 4 走查 #8：误标 80 致空库首测即告警，已移除）
                 "is_gold": True,
                 "expected": {"judge_gold_scores": {
                     "factuality": {"score": 100, "level": 5},
                 }, "reference_docs": REF_CS_KNOWLEDGE, "golden_answer": "智能客服应友好回应问候，说明可提供的服务范围（商品/订单咨询、售后服务、转人工等），并邀请用户提出具体问题；不得编造服务功能或作出虚假承诺。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_STRUCT,
                 "scenes": ["greeting"]},
                {"name": "咨询人工客服", "description": "咨询联系人工客服的路径", "interface": "chat",
                 "input_type": "text", "input": {"content": "我想了解一下怎么联系你们的人工客服。"},
                 "expected": {"reference_docs": REF_CS_KNOWLEDGE, "golden_answer": "客服应告知用户联系人工客服的渠道与方式（如拨打客服热线、官网/应用内转人工入口、公众号或小程序留言等），并说明会由人工客服跟进处理；不得编造不存在的渠道或号码。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_STRUCT,
                 "scenes": ["human_handoff"]},
            ],
        },
    },
    {
        "name": "contract-check",
        "base_url": "http://host.docker.internal:8001",
        "adapter_type": "config",
        "adapter_config": CC_ADAPTER_CFG,
        "contract_version": "2.0",
        "interfaces": [
            {"name": "result", "path": "/api/tasks/{task_id}/result", "method": "GET",
             "contract_type": "sync", "contract_version": "2.0"},
        ],
        "auth_secrets": None,
        "scenes": [
            {"tag": "missing_date", "description": "缺失生效日期"},
            {"tag": "single_party", "description": "单方签署"},
            {"tag": "scanned_pdf", "description": "扫描件识别"},
            {"tag": "conflict", "description": "条款冲突"},
            {"tag": "genuine", "description": "合规合同"},
        ],
        "sample_suite": {
            "name": "示例-合同校验", "description": "4.2 固化示例：file 型合同必填校验（同步变体）",
            "cases": [
                {"name": "缺生效日合同", "description": "b1_missing_date：必填校验 FAIL → WAITING_REVIEW 取数",
                 "interface": "result", "input_type": "file",
                 "input": {"file_path": "/app/uploads/cc_b1_missing_date.pdf"},
                 "file_ref": "/app/uploads/cc_b1_missing_date.pdf",
                 "expected": {"reference_docs": REF_CC_CONTRACT, "golden_answer": "该合同缺少生效日期（生效日条款）这一必填信息，合同校验报告应检出并明确指出该缺失项；不得虚构合同中不存在的违规内容。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_STRUCT,
                 "scenes": ["missing_date"]},
            ],
        },
    },
    {
        "name": "smart-procurement",
        "base_url": "http://host.docker.internal:8002",
        "adapter_type": "config",
        "adapter_config": SP_ADAPTER_CFG,
        "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/v1/reviews/{review_id}/chat", "method": "POST",
             "contract_type": "sse", "contract_version": "2.0"},
        ],
        "auth_secrets": None,  # P2-D17：凭证不入库明文，seed 时从 AGENT_AUTH_SECRETS env 注入
        "scenes": [
            {"tag": "tech_scheme", "description": "技术方案评审"},
            {"tag": "price", "description": "报价评审"},
            {"tag": "conflict_interest", "description": "利益冲突检测"},
            {"tag": "collusion", "description": "围串标检测"},
        ],
        "sample_suite": {
            "name": "示例-评审对话", "description": "4.2 固化示例：评审对话（sp chat，依赖 FROZEN 标书+专家账号预置）",
            # bid_id/dimension_id 已实测确认（2026-08）：BID-024（LOT-008 FROZEN）
            # + DIM-LOT-008-1（LOT-008 非报价维度）建评审 201 + chat SSE 全链路通过
            "cases": [
                {"name": "技术方案概要评估", "description": "评审对话：请专家概述标书技术方案要点", "interface": "chat",
                 "input_type": "text",
                 "input": {"question": "请概述这份标书的技术方案核心要点，并指出需要重点关注的风险。",
                           "bid_id": "BID-024", "dimension_id": "DIM-LOT-008-1"},
                 "expected": {"reference_docs": REF_SP_BID024, "golden_answer": "标书为某省电子政务外网系统集成项目，技术方案核心包括：容器化部署与标准化编排（支持基于Kubernetes的弹性伸缩）、CI/CD自动化流水线、前后端分离架构（Vue技术栈，适配PC端与移动端）、国产化数据库与中间件替换、纵深防御安全架构（网络边界WAF与入侵防御设备、等保三级、敏感数据加密存储与TLS传输、密钥管理服务、最小权限原则）、同城与异地容灾备份（关键数据时间点恢复、最大数据丢失量不超过24小时）、需求冻结与变更管理、联合实施团队与里程碑计划、关键岗位AB角备份、分级故障响应与备件保障；评审应概述上述要点，并提示风险：RPO≤24小时对政务系统偏松、Kubernetes弹性伸缩与国产化替换缺乏实际案例与性能验证、等保三级无测评案例佐证、需求理解与方案设计针对性不足；不得编造标书中不存在的技术方案内容。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_SP,
                 "scenes": ["tech_scheme"]},
                {"name": "技术方案亮点与不足", "description": "评审对话：请专家评价标书技术方案亮点与不足", "interface": "chat",
                 "input_type": "text",
                 "input": {"question": "这份标书的技术方案有哪些亮点和不足？请给出专业评价。",
                           "bid_id": "BID-024", "dimension_id": "DIM-LOT-008-1"},
                 "expected": {"reference_docs": REF_SP_BID024, "golden_answer": "评审应基于标书实际内容（电子政务外网系统集成项目：容器化部署与K8s弹性伸缩、CI/CD流水线、Vue前后端分离、国产化数据库与中间件替换、纵深防御+等保三级+TLS+密钥管理、同城异地容灾RPO≤24h、需求冻结+变更管理、联合团队+AB角、分级故障响应+备件保障）给出亮点与不足：亮点包括架构开放且支持容器化弹性伸缩、安全体系较全面（等保三级、纵深防御）、容灾有量化指标（RPO≤24h）、项目管理机制规范（周报/例会/里程碑评审/变更管理）、团队与售后承诺完善；不足包括项目需求针对性不足（通用化表述多）、量化指标缺失（无并发/SLA/RTO承诺）、安全管理体系描述不充分、系统集成类核心能力（接口对接/数据迁移/新旧切换）未展开、实施计划颗粒度偏粗；不得编造标书中不存在的评价依据。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_SP,
                 "scenes": ["tech_scheme"]},
            ],
        },
    },
    {
        "name": "good-question",
        "base_url": "http://host.docker.internal:8080",
        "adapter_type": "config",
        "adapter_config": GQ_ADAPTER_CFG,
        "contract_version": "2.0",
        "interfaces": [
            {"name": "chat", "path": "/api/chat/{session_id}", "method": "POST",
             "contract_type": "sse", "contract_version": "2.0"},
        ],
        "auth_secrets": None,  # P2-D17：凭证不入库明文，seed 时从 AGENT_AUTH_SECRETS env 注入
        "scenes": [
            {"tag": "greeting", "description": "问候与闲聊"},
            {"tag": "doc_qa", "description": "文档检索问答"},
            {"tag": "no_hit", "description": "无命中兜底"},
            {"tag": "summarize", "description": "文档内容总结"},
        ],
        "sample_suite": {
            "name": "示例-知识问答", "description": "4.2 固化示例：RAG 问答（token 事件 field_map 映射 answer）",
            "cases": [
                {"name": "打招呼", "description": "普通欢迎语", "interface": "chat",
                 "input_type": "text", "input": {"content": "你好，请自我介绍一下。"},
                 "expected": {"reference_docs": REF_GQ_EMPTY, "golden_answer": "回答应说明自己是文档问答助手、可基于文档库内容查找/总结/理解文档信息并解答问题，并邀请用户提问；不得编造检索到文档或虚构能力。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_STRUCT,
                 "scenes": ["greeting"]},
                {"name": "检索问答", "description": "查询文档库违约责任条款", "interface": "chat",
                 "input_type": "text", "input": {"content": "文档库中是否有关于违约责任的规定？"},
                 "expected": {"reference_docs": REF_GQ_EMPTY, "golden_answer": "该文档库当前未收录任何文档内容，不存在违约责任相关规定；回答应如实说明未找到相关文档或信息，不得编造条款内容或假装已检索到文档。"},
                 "assertions": ASSERTION_ANSWER, "metrics": METRICS_STRUCT,
                 "scenes": ["doc_qa"]},
            ],
        },
    },
]

# ============ 空库重建 seed 补充（阶段 7 前置，seed.py::_seed_model_prices/_seed_users 消费） ============
# 模型单价（元/百万 token；成本=tokens×单价/1e6，见 memory cost-unit-per-million）。
# 版本化 2 档展示价格演进（主键 model+effective_from），成本计算按 run 时间取最新生效档。
SEED_MODEL_PRICES = [
    {"model": "deepseek-chat", "input_price": 2.0, "output_price": 8.0,
     "effective_from": datetime(2026, 1, 1)},
    {"model": "deepseek-chat", "input_price": 1.5, "output_price": 4.5,
     "effective_from": datetime(2026, 7, 1)},
]

# 走查/演示用户（role ∈ ROLE 枚举）。P0 安全收敛：密码禁止硬编码在源码，
# 改由 env DEMO_EVALUATOR_PASSWORD / DEMO_VIEWER_PASSWORD 注入（seed.py::_seed_users），
# 缺 env 不建——生产不产生已知口令账号；password_changed_at=None → 首登强制改密。
# admin 不走这里（seed.py::_seed_admin 独立：密码走 env ADMIN_PASSWORD，首登强制改密）。
SEED_USERS = [
    {"username": "evaluator", "role": "evaluator"},
    {"username": "viewer", "role": "viewer"},
]
