# -*- coding: utf-8 -*-
"""一次性工具：为 4 家 agent 落「真实业务评测」suite + 补足激活 21 条骨架。

仅调用平台 API（POST /suites、POST /suites/{id}/cases、PUT /cases/{id}），
平台代码零改动。逐家执行（--agent 参数），一次只起一个 agent 容器评测。

用法：
  python scripts/seed_real_cases.py --agent cs      # 落 cs 新 suite + 补足 4 骨架
  python scripts/seed_real_cases.py --agent cc      # 落 cc 新 suite + 补足 5 骨架
  python scripts/seed_real_cases.py --agent sp      # 落 sp 新 suite + 补足 8 骨架
  python scripts/seed_real_cases.py --agent gq      # 落 gq 新 suite + 补足 4 骨架

平台 API 根默认为 http://localhost:8180/api，admin 口令读 ADMIN_PASSWORD（env 或 ../.env）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

PLATFORM = "http://localhost:8180/api"


def _env_secret(key: str, hint: str = "") -> str:
    """敏感配置：优先环境变量，兜底读项目根 ../.env（不硬编码凭据）。"""
    val = os.environ.get(key)
    if val:
        return val
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    raise SystemExit(f"缺少 {key}（环境变量或 ../.env 中配置）{hint}")


ADMIN_PWD = _env_secret("ADMIN_PASSWORD", "（平台 admin 登录口令）")

AGENT_IDS = {"sp": 2297, "cs": 2298, "cc": 2299, "gq": 2300}
INTERFACE_IDS = {
    "cs": {"chat": 2186},
    "cc": {"result": 2187},
    "sp": {"chat": 2184, "score": 2185},
    "gq": {"chat": 2188},
}
# 骨架用例 id（21 条）
SKELETON_IDS = {
    "sp": [3104, 3105, 3106, 3107, 3108, 3109, 3110, 3111],
    "cs": [3112, 3113, 3114, 3115],
    "cc": [3116, 3117, 3118, 3119, 3120],
    "gq": [3121, 3122, 3123, 3124],
}

# 断言/维度辅助
ACCURACY_4 = {
    "completeness": {"enabled": True},
    "factuality": {"enabled": True},
    "tool_usage": {"enabled": True},
    "reasoning_quality": {"enabled": True},
}


def _auth(c: httpx.Client) -> dict:
    r = c.post(f"{PLATFORM}/auth/login", json={"username": "admin", "password": ADMIN_PWD})
    if r.status_code >= 400:
        raise SystemExit(f"登录失败 HTTP {r.status_code}: {r.text[:300]}")
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _mk_assertion(op: str, args: dict, dim: str) -> dict:
    return {"op": op, "args": args, "dimension": dim}


def _create_suite(c, h, agent_id: int, name: str, desc: str) -> int:
    r = c.post(f"{PLATFORM}/suites", headers=h,
               json={"agent_id": agent_id, "name": name, "description": desc})
    if r.status_code >= 400:
        raise SystemExit(f"suite 落库失败 HTTP {r.status_code}: {r.text[:400]}")
    return r.json()["data"]["id"]


def _create_case(c, h, suite_id: int, body: dict) -> int:
    r = c.post(f"{PLATFORM}/suites/{suite_id}/cases", headers=h, json=body)
    if r.status_code >= 400:
        print(f"✗ case 落库失败 HTTP {r.status_code}: {r.text[:400]}")
        return 0
    return r.json()["data"]["id"]


def _update_case(c, h, case_id: int, body: dict) -> None:
    r = c.put(f"{PLATFORM}/cases/{case_id}", headers=h, json=body)
    if r.status_code >= 400:
        print(f"✗ 骨架补足失败 case={case_id} HTTP {r.status_code}: {r.text[:400]}")


# ==================== cs ====================
def cs_real_cases() -> list[dict]:
    """cs 真实业务 suite：订单查询 + 售后政策，user_1 凭证（DB id=2）。

    reference_docs = 订单真实事实（订单号/状态/商品）+ 知识库原文要点。
    golden_answer = 标准回答要点。
    """
    order_facts = (
        "订单事实（真实数据库，T10 补齐金额/数量防 judge 误判）：\n"
        "ORD-20240801-001 DELIVERED 已签收，总金额¥69.70，含 SKU-001 手机壳×1（¥29.90）、SKU-002 钢化膜×2（¥19.90/件，均已退货）\n"
        "ORD-20240805-002 SHIPPED 已发货，总金额¥228.90，含 SKU-003 蓝牙耳机×1（¥199.00）、SKU-004 耳机收纳盒×1（¥29.90）\n"
        "ORD-20240806-003 PAID 已付款，总金额¥89.85，含 SKU-005 数据线×2（¥29.95/件）、SKU-006 定制手机支架×1（¥29.95，returnable=0 不可退）\n"
        "ORD-20240725-005 DELIVERED 已签收，含 SKU-008 台灯×1（¥88.00）"
    )
    policy_facts = (
        "售后政策要点（知识库原文，T10 补齐大促条款）：\n"
        "- 退款审核通过后 1-3 个工作日原路退回；大促期间（如双11）审核时效可能延长至 48 小时\n"
        "- 未发货(PAID)可仅退款；已发货未签收(SHIPPED)不支持直接仅退款，需先拒收，物流退回后自动退款；已签收(DELIVERED)不支持仅退款，须走退货\n"
        "- 自签收之日起 7 天内无理由退货；定制类商品（如定制手机支架）一经生产不支持退货\n"
        "- 运费规则：质量问题退货运费由商家承担；7 天无理由退货非包邮订单由买家承担、包邮订单由商家承担；拒收件退回运费按快递实际费用结算\n"
        "- 签收后 48 小时内联系客服并提供照片，属于质量问题一律免费退换，运费商家承担\n"
        "- 退货/退款/投诉工单，商家 2 小时内审核（工作时间 9:00-21:00）"
    )
    return [
        {
            "name": "order_query-已签收",
            "interface_name": "chat",
            "input": {"content": "查询订单 ORD-20240801-001 的状态"},
            "expected": {
                "reference_docs": order_facts,
                "golden_answer": "订单 ORD-20240801-001 已签收（DELIVERED），商品为手机壳、钢化膜（均已退货）。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["已签收"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["order_query"],
            "status": "active",
        },
        {
            "name": "order_query-已发货",
            "interface_name": "chat",
            "input": {"content": "ORD-20240805-002 现在什么状态"},
            "expected": {
                "reference_docs": order_facts,
                "golden_answer": "订单 ORD-20240805-002 已发货（SHIPPED），商品为蓝牙耳机、耳机收纳盒。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["已发货"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["order_query"],
            "status": "active",
        },
        {
            "name": "order_query-已付款",
            "interface_name": "chat",
            "input": {"content": "查一下 ORD-20240806-003 的状态"},
            "expected": {
                "reference_docs": order_facts,
                "golden_answer": "订单 ORD-20240806-003 已付款（PAID），商品为数据线、定制手机支架。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["已付款"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["order_query"],
            "status": "active",
        },
        {
            "name": "order_query-已签收2",
            "interface_name": "chat",
            "input": {"content": "ORD-20240725-005 状态如何"},
            "expected": {
                "reference_docs": order_facts,
                "golden_answer": "订单 ORD-20240725-005 已签收（DELIVERED），商品为台灯。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["已签收"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["order_query"],
            "status": "active",
        },
        {
            "name": "after_sales-退款时效",
            "interface_name": "chat",
            "input": {"content": "退款多久到账"},
            "expected": {
                "reference_docs": policy_facts,
                "golden_answer": "审核通过后 1-3 个工作日原路退回，部分银行可能延迟 1-2 天。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "search_policy"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["1-3", "工作日"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["after_sales"],
            "status": "active",
        },
        {
            "name": "after_sales-退货资格-已发货需拒收",
            "interface_name": "chat",
            "input": {"content": "ORD-20240805-002 能退货吗"},
            "expected": {
                "reference_docs": policy_facts,
                "golden_answer": "订单已发货未签收（SHIPPED），不支持直接仅退款，需先拒收，物流退回后自动退款。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["拒收"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["after_sales"],
            "status": "active",
        },
        {
            "name": "after_sales-不可退商品",
            "interface_name": "chat",
            "input": {"content": "ORD-20240806-003 的定制手机支架能退吗"},
            "expected": {
                "reference_docs": order_facts + "\n" + policy_facts,
                "golden_answer": "定制手机支架属于定制类商品，一经生产不支持退货。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["定制", "不支持退货"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["after_sales"],
            "status": "active",
        },
        {
            "name": "after_sales-无理由退货时限",
            "interface_name": "chat",
            "input": {"content": "7 天内无理由退货的要求是什么"},
            "expected": {
                "reference_docs": policy_facts,
                "golden_answer": "自签收之日起 7 天内商品支持无理由退货，需商品完好、配件齐全；定制类等商品除外。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "search_policy"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["7", "无理由"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["after_sales"],
            "status": "active",
        },
    ]


# 骨架补足定义：case_id → 更新体
CS_SKELETON_UPDATES = {
    3112: {  # greeting-chat
        "expected": {
            "reference_docs": "智能客服场景：支持订单查询、售后政策、投诉、转人工等服务。",
            "golden_answer": "智能客服应友好回应问候，说明可提供的服务范围（商品/订单咨询、售后服务、转人工等），并邀请用户提出具体问题；不得编造服务功能。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["您好", "你好"], "match": "any"}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3113: {  # order_query-chat（原 input=你好 无意义）
        "input": {"content": "查询订单 ORD-20240801-001 的状态"},
        "expected": {
            # 与真业务 suite 3133（同订单 001）reference 同口径：补全数量/金额，
            # 否则 judge 把 agent 从真实订单查到的正确信息（钢化膜×2、¥69.7）误判为编造（3113 实测 factuality=40）。
            "reference_docs": "订单事实：ORD-20240801-001 DELIVERED 已签收，总金额¥69.70，含 SKU-001 手机壳×1（¥29.90）、SKU-002 钢化膜×2（¥19.90/件，均已退货）。",
            "golden_answer": "订单 ORD-20240801-001 已签收（DELIVERED）。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "query_order"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["已签收"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3114: {  # after_sales-chat（原 input=你好）
        "input": {"content": "退货政策是什么"},
        "expected": {
            "reference_docs": "退换货政策：自签收之日起 7 天内无理由退货；定制类商品不支持退货。",
            "golden_answer": "自签收之日起 7 天内支持无理由退货，商品需完好；质量问题由商家承担运费。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "search_policy"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["退货", "7"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3115: {  # human_handoff-chat（原 input=你好）
        "input": {"content": "转人工"},
        "expected": {
            "reference_docs": "客服渠道：拨打客服热线（工作时间 9:00-21:00）、在线客服入口回复转人工、公众号或小程序留言。",
            "golden_answer": "告知用户联系人工客服的渠道（热线/在线客服转人工/公众号留言），并说明会由人工客服跟进；不得编造不存在的渠道。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["人工"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
}


# ==================== cc ====================
# 平台 uploads 内已放好 cc 场景 PDF（b1-b8/good/scanned，与 cc 容器校验文件 md5 一致）。
# 断言 keyword 均命中 cc result.answer 的真实规则 message（verify_b_scenarios 校准）：
#   b1=生效日期必填 / b2=金额不得小于0 / b4=乙方未签署 / b5=终止早于生效 /
#   b6=违约金未具体化 / b7=义务失衡 / b8=违约责任条款缺失 / b3=未检出违规（合规正例）。
def cc_real_cases() -> list[dict]:
    """cc 真实业务 suite：file 型（input.file_path 指向平台 uploads 内 PDF）。"""
    return [
        {
            "name": "missing_date-生效日期缺失",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b1_missing_date.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：合同缺少必填信息-生效日期，属必填项缺失违规（HIGH）。",
                "golden_answer": "检出违规：合同缺少必填信息：生效日期。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["生效日期"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["missing_date"],
            "status": "active",
        },
        {
            "name": "negative_amount-金额为负",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b2_negative_amount.pdf"},
            "expected": {
                "reference_docs": "b2 合同事实：合同总金额 totalAmount=-50000（负值，触发 MEDIUM min 规则）；标的金额 itemAmount 未提取为 null（不触发）。"
                "合同审查规则：合同总金额不得小于 0，金额为负属数值越界违规（MEDIUM）。",
                "golden_answer": "检出违规：合同总金额不得小于 0（b2 合同总金额为负）。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["总金额"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["conflict"],
            "status": "active",
        },
        {
            "name": "single_party-乙方未签署",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b4_missing_party_b.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：乙方仅有空白盖章占位行未实际签署，属单方签署违规（HIGH）。",
                "golden_answer": "检出违规：甲方有盖章，但乙方仅有空白盖章占位行，未实际签署。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["乙方"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["single_party"],
            "status": "active",
        },
        {
            "name": "termination_before_effective-终止早于生效",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b5_termination_before_effective.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：合同终止日期必须晚于或等于生效日期，终止早于生效属日期逻辑错误（HIGH）。",
                "golden_answer": "检出违规：合同终止日期必须晚于或等于生效日期。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["终止"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["conflict"],
            "status": "active",
        },
        {
            "name": "breach_clause_undetailed-违约金未具体化",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b6_missing_breach_clause.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：违约责任条款应约定违约金数额、比例、赔偿范围或责任承担方式，泛泛表述属违约条款不完整（HIGH）。",
                "golden_answer": "检出违规：合同全文未约定违约金数额、比例、赔偿范围或责任承担方式等具体违约责任内容。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["违约金"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["conflict"],
            "status": "active",
        },
        {
            "name": "unbalanced_obligations-义务失衡",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b7_unbalanced_obligations.pdf"},
            "expected": {
                "reference_docs": "b7 合同事实：合同义务条款明显失衡（甲方承担全部义务、乙方仅有权利），触发 MEDIUM 义务失衡规则；"
                "且合同违约责任条款仅为泛泛表述，未约定违约金数额/比例/赔偿范围，同时触发 HIGH 违约条款未具体化规则。",
                "golden_answer": "检出违规：① 义务条款明显失衡，甲方承担全部义务、乙方仅有权利；② 合同未约定具体违约责任条款（违约金数额/比例/赔偿范围均缺失）。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["失衡"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["conflict"],
            "status": "active",
        },
        {
            "name": "breach_clause_missing-违约条款缺失",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_b8_service_contract.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：合同应包含违约责任条款；仅含争议解决条款而无违约约定，属违约条款缺失（HIGH）。",
                "golden_answer": "检出违规：合同全文未出现任何关于违约责任、违约金、赔偿范围或责任承担方式的具体约定。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["违约"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["conflict"],
            "status": "active",
        },
        {
            "name": "genuine-clean-无违规",
            "interface_name": "result",
            "input_type": "file",
            "input": {"file_path": "/app/uploads/cc_good.pdf"},
            "expected": {
                "reference_docs": "合同审查规则：合规合同应校验完成并明示未检出违规项。",
                "golden_answer": "合同校验完成，未检出违规项。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["未检出违规"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "scenes": ["genuine"],
            "status": "active",
        },
    ]


# 骨架补足定义：case_id → 更新体（原 3116-3120 input 全指向 b1，按场景语义改指真实 PDF）
CC_SKELETON_UPDATES = {
    3116: {  # missing_date-result
        "input": {"file_path": "/app/uploads/cc_b1_missing_date.pdf"},
        "expected": {
            "reference_docs": "合同审查规则：合同缺少必填信息-生效日期，属必填项缺失违规（HIGH）。",
            "golden_answer": "检出违规：合同缺少必填信息：生效日期。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["生效日期"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3117: {  # single_party-result
        "input": {"file_path": "/app/uploads/cc_b4_missing_party_b.pdf"},
        "expected": {
            "reference_docs": "合同审查规则：乙方仅有空白盖章占位行未实际签署，属单方签署违规（HIGH）。",
            "golden_answer": "检出违规：甲方有盖章，但乙方仅有空白盖章占位行，未实际签署。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["乙方"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3118: {  # scanned_pdf-result（扫描件：OCR 抽取，命中缺违约条款）
        "input": {"file_path": "/app/uploads/cc_scanned.pdf"},
        "expected": {
            "reference_docs": "合同审查规则：扫描件应 OCR 抽取文本后校验；扫描合同若缺违约条款应检出（HIGH）。",
            "golden_answer": "检出违规：合同全文未包含任何违约责任、赔偿或责任承担相关约定。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["违约"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3119: {  # conflict-result（日期逻辑冲突：终止早于生效）
        "input": {"file_path": "/app/uploads/cc_b5_termination_before_effective.pdf"},
        "expected": {
            "reference_docs": "合同审查规则：合同终止日期必须晚于或等于生效日期，终止早于生效属日期逻辑错误（HIGH）。",
            "golden_answer": "检出违规：合同终止日期必须晚于或等于生效日期。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["终止"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3120: {  # genuine-result（合规正例）
        "input": {"file_path": "/app/uploads/cc_b3_bad_type.pdf"},
        "expected": {
            "reference_docs": "合同审查规则：合规合同应校验完成并明示未检出违规项。",
            "golden_answer": "合同校验完成，未检出违规项。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["未检出违规"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
}


# ==================== sp ====================
# 契约实测（T0c + T4 probe，容器 sp-app:18002，expert_02/123456 登录）：
#   chat 接口：POST /reviews/{review_id}/chat，SSE 含 tool_call(retrieve_knowledge)→answer→usage→done
#   score 接口：POST /reviews/{review_id}/score，SSE 含 tool_call(knowledge_retrieval)→answer→score_result→usage→done
#   ★平台评测路由（T8 方案 A，用户确认）：agent_interface chat/score 的 path 改为完整模板
#     {prepare.review.review_id} + 移除 adapter_config.request.path → build_request 按 interface.path
#     路由：chat 用例走 /chat（retrieve_knowledge）、score 用例走 /score（knowledge_retrieval），
#     断言与所测接口一一对应（不迎合返回值）。
#   报价维度（DIM-LOT-008-2）走 price_calc 无 usage → 契约 error no_usage → 彻底排除报价用例
#   LOT-008 非报价维度：DIM-LOT-008-1 售后服务 / -3 企业实力 / -4 项目团队（无"技术方案"维度，
#   骨架 3104-3111 原 question「技术方案完整性可行性」与 dimension 不匹配 → 补足时改指真实维度）
#   BID-024 标书正文（bid_content/BID-024.txt）：第七章质量保障与售后服务、第一章公司概况（研发投入>10%）
def _sp_input(bid_id: str, dim: str, question: str) -> dict:
    return {"bid_id": bid_id, "dimension_id": dim, "question": question}


def sp_real_cases() -> list[dict]:
    """sp 真实业务 suite：chat 问答 + score 评分，全非报价维度，断言命中真实契约输出。"""
    bid_ref = (
        "标书事实（BID-024，T10 补齐 structured_data 防 judge 误判）：\n"
        "- 第一章公司概况：研发投入占营收>10%，拥有软件著作权与专利，多区域服务中心，质量认证 quality_cert=CMMI3\n"
        "- 第七章质量保障与售后服务：定期满意度调查、服务期免费优化建议与安全加固、停运维护窗口协调\n"
        "- 第八章商务承诺：同意缴纳质保金、质保期 24 个月（warranty_months=24）、不转包违规分包\n"
        "评分维度 DIM-LOT-008-1 售后服务（服务承诺 rubric 8分 7×24响应 / 5-7 工作日 / 0-4 不明确；"
        "培训与支持 7分 全面培训 / 4-6 基础 / 0-3 无）。"
    )
    return [
        {
            "name": "chat-售后服务承诺",
            "interface_name": "chat",
            "input": _sp_input("BID-024", "DIM-LOT-008-1",
                               "从售后服务角度评价 BID-024 投标方案中关于售后服务的承诺是否满足招标要求。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "从售后服务角度评价：BID-024 已作出质保金、满意度调查、免费优化建议与安全加固等售后服务承诺，整体方向符合需求，但部分关键指标未量化（如响应时效），需对照招标文件具体条款核验。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["售后"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "chat-企业实力",
            "interface_name": "chat",
            "input": _sp_input("BID-024", "DIM-LOT-008-3",
                               "评估 BID-024 投标人的企业实力。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "企业实力评估：BID-024 公司研发投入占营收比例超过10%，拥有多项软件著作权与专利，营收稳步增长，具备多区域本地化交付能力。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["研发"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "chat-项目团队",
            "interface_name": "chat",
            "input": _sp_input("BID-024", "DIM-LOT-008-4",
                               "评估 BID-024 投标方案的项目团队配置。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "项目团队评估：BID-024 项目组实施人员具备丰富现场经验，关键岗位设置 AB 角互备、备份人员，建立绩效考核与激励制度。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["团队"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "chat-质保承诺",
            "interface_name": "chat",
            "input": _sp_input("BID-024", "DIM-LOT-008-1",
                               "BID-024 投标方案中的质保承诺是什么？"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "质保承诺：同意按合同约定缴纳质保金，质保期满无质量问题后无息退还；服务期内免费提供系统优化建议与安全加固补丁更新。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["质保"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "score-售后服务",
            "interface_name": "score",
            "input": _sp_input("BID-024", "DIM-LOT-008-1",
                               "对 BID-024 售后服务维度进行评分。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "售后服务维度评分：依据评分标准给出各子项得分（服务承诺/培训与支持）及总分，并说明评分理由。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "score-企业实力",
            "interface_name": "score",
            "input": _sp_input("BID-024", "DIM-LOT-008-3",
                               "对 BID-024 企业实力维度进行评分。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "企业实力维度评分：依据评分标准评估投标人资质/业绩/研发投入等并给出得分与理由。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "score-项目团队",
            "interface_name": "score",
            "input": _sp_input("BID-024", "DIM-LOT-008-4",
                               "对 BID-024 项目团队维度进行评分。"),
            "expected": {
                "reference_docs": bid_ref,
                "golden_answer": "项目团队维度评分：依据评分标准评估团队配置/经验/稳定性并给出得分与理由。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
    ]


# 骨架补足定义：case_id → 更新体（原 8 条 input 全 BID-024/DIM-LOT-008-1/技术方案 question，
# 与 LOT-008 维度体系不匹配；补足时按真实维度 + 匹配 question 修正，报价维度排除）
SP_SKELETON_UPDATES = {
    3104: {  # tech_scheme-chat
        "input": _sp_input("BID-024", "DIM-LOT-008-1", "评价 BID-024 售后服务承诺的完整性与可行性。"),
        "expected": {
            "reference_docs": "BID-024 标书第七章质量保障与售后服务 + 第八章商务承诺（质保金/不转包）。",
            "golden_answer": "BID-024 售后服务承诺较完整：质保金、满意度调查、免费优化与安全加固、停运维护窗口协调；建议量化响应时效。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["售后"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3105: {  # tech_scheme-score
        "input": _sp_input("BID-024", "DIM-LOT-008-1", "请对 BID-024 售后服务维度打分。"),
        "expected": {
            "reference_docs": "DIM-LOT-008-1 售后服务 rubric：服务承诺（8分 7×24/工作日/不明确）、培训与支持（7分 全面/基础/无）。",
            "golden_answer": "按 rubric 对服务承诺、培训与支持两子项打分并汇总总分。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3106: {  # price-chat（报价维度 no_usage 排除 → 改企业实力）
        "input": _sp_input("BID-024", "DIM-LOT-008-3", "评价 BID-024 投标人的企业实力与资质。"),
        "expected": {
            "reference_docs": "BID-024 标书第一章公司概况：研发投入>10%、软件著作权与专利、多区域服务中心。",
            "golden_answer": "BID-024 企业实力：研发投入超营收10%、多项软著与专利、营收稳步增长、区域服务中心覆盖广。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["研发"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3107: {  # price-score（改企业实力）
        "input": _sp_input("BID-024", "DIM-LOT-008-3", "请对 BID-024 企业实力维度打分。"),
        "expected": {
            "reference_docs": "DIM-LOT-008-3 企业实力 rubric（资质/业绩/研发投入等）。",
            "golden_answer": "按 rubric 评估企业实力各子项并给出得分与理由。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3108: {  # conflict_interest-chat（改项目团队）
        "input": _sp_input("BID-024", "DIM-LOT-008-4", "评价 BID-024 项目团队的配置与稳定性。"),
        "expected": {
            "reference_docs": "BID-024 标书第六章项目团队配置：实施人员经验丰富、关键岗位 AB 角互备、备份人员、绩效考核激励。",
            "golden_answer": "BID-024 团队配置合理：实施经验丰富、关键岗位 AB 角互备、备份人员与绩效激励齐备。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["团队"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3109: {  # conflict_interest-score（改项目团队）
        "input": _sp_input("BID-024", "DIM-LOT-008-4", "请对 BID-024 项目团队维度打分。"),
        "expected": {
            "reference_docs": "DIM-LOT-008-4 项目团队 rubric（配置/经验/稳定性等）。",
            "golden_answer": "按 rubric 评估项目团队各子项并给出得分与理由。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3110: {  # collusion-chat（改质保承诺）
        "input": _sp_input("BID-024", "DIM-LOT-008-1", "BID-024 的质保承诺具体内容是什么？"),
        "expected": {
            "reference_docs": "BID-024 标书第八章商务承诺：质保金、质保期满无息退还、不转包。",
            "golden_answer": "质保承诺：缴纳质保金，质保期满无质量问题无息退还；服务期内免费优化建议与安全加固。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "retrieve_knowledge"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["质保"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3111: {  # collusion-score（售后评分，question 与 3105 错开）
        "input": _sp_input("BID-024", "DIM-LOT-008-1", "依据评分标准评估 BID-024 售后服务的培训与支持子项得分。"),
        "expected": {
            "reference_docs": "DIM-LOT-008-1 培训与支持 rubric：7分 全面培训 / 4-6 基础 / 0-3 无。",
            "golden_answer": "评估培训与支持子项得分：BID-024 承诺分层培训与使用手册，依据 rubric 给出子项得分。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "knowledge_retrieval"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["评分"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
}


# ==================== gq ====================
# 契约确认（T0b + 代码）：request body {"stream":true,"content":"{case.input.content}"}（input 用 content 字段），
# SSE field_map token→answer；检索工具名 = hybrid_retrieve（LLM 自主决定是否检索，chat_service.py:75 等）；
# no_hit 兜底话术 = "根据当前文档库的内容，未找到与您问题直接相关的信息"（chat_service.py:107）。
# 库 3 已灌 4 篇文档：员工考勤管理制度/产品发布上线流程规范/Docker 环境安装部署手册/客户数据保密协议。
# 断言策略：doc_qa 用 keyword（文档特有词，question 避免回显）+ tool_called(hybrid_retrieve)（可触发才加）；
# no_hit 用 keyword(未找到)；greeting/summarize 不强制 tool。
GQ_DOCS = (
    "库 3 已灌文档（ground truth）：\n"
    "- 员工考勤管理制度：标准工时制，周一至周五 9:00-12:00、13:00-18:00，午休 12:00-13:00，每天 8 小时每周 40 小时，适用于全体正式员工由人力资源部解释监督执行；事假须提前 3 个工作日申请（不计发工资）；病假需诊断证明按最低工资 80% 发放；"
    "年假满 1 年 5 天/满 10 年 10 天/满 20 年 15 天、当年度休完；迟到（超过上班时间 5-30 分钟）或早退"
    "（下班前 30 分钟内擅自离岗）每次扣 50 元、当月累计 3 次视缺勤半天、不可抗力迟到当日说明可免扣；"
    "加班工作日 1.5 倍/休息日 2 倍、可 1:1 调休 3 个月内使用完；旷工半天以内按 2 倍日工资、1 天按 3 倍日工资扣款、"
    "连续旷工 3 天以上或年度累计 5 天以上可解除劳动合同；缺勤期间不享受每月 300 元全勤奖；婚假/产假/丧假按国家规定\n"
    "- 产品发布上线流程规范：需求评审→开发→测试→预发布验证→灰度发布（5% 流量按用户 ID 尾号）→正式发布→线上监控；"
    "上线需测试全过+预发布冒烟（核心链路：登录、主流程、支付/下单 无阻塞缺陷）+压测 P99≤500ms/可用性≥99.9%；"
    "回滚方案：发布前须准备上一版本镜像或制品并验证回滚可行，触发回滚条件为发布后 15 分钟内核心指标恶化/P0 事故/灰度异常，"
    "回滚由发布负责人执行且回滚后 30 分钟内复盘；常规发布窗口工作日 20:00-22:00、周五 17 点后不安排常规发布、"
    "紧急修复不受窗口限制但须值班负责人审批\n"
    "- Docker 环境安装部署手册：镜像/容器/仓库三概念；Ubuntu/CentOS/Windows 安装步骤；docker pull/run/ps 等常用命令\n"
    "- 客户数据保密协议：保密范围（身份信息/业务数据）；未经书面同意不得向第三方披露；保密期限 3 年；"
    "违约金 50 万元；终止后 30 日内归还或销毁数据"
)
GQ_EMPTY_NOTE = "no_hit 用例口径：该问题相关但文档库 3 未收录（如工资发放日/生育津贴），应如实回复未找到，不得编造。"

# gq 全 case 金丝雀断言（评测侧防御，3161 根因）：answer 不得残留工具调用声明。
# DeepSeek V4 偶发把二次检索意图渲染成 DSML/XML 声明泄漏进 answer（改动 1 拦截失效的哨兵），
# 任一泄漏标记（"DSML" 覆盖四种竖线变体、"tool_calls" 覆盖标准/DSML 开闭标签）出现即 fail。
_GQ_CANARY = _mk_assertion(
    "keyword_not_contains",
    {"path": "answer", "keywords": ["DSML", "tool_calls"]},
    "completeness",
)


def _gq_content(text: str) -> dict:
    return {"content": text}


def gq_real_cases() -> list[dict]:
    return [
        {
            "name": "doc_qa-考勤事假",
            "interface_name": "chat",
            "input": _gq_content("请事假需要提前几天申请？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "事假须提前 3 个工作日提出申请，经审批通过后方可休假，事假期间不计发工资。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["提前"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "doc_qa-年假天数",
            "interface_name": "chat",
            "input": _gq_content("员工的年休假天数是怎么规定的？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "连续工作满 1 年不满 10 年的年假 5 天；满 10 年不满 20 年的 10 天；满 20 年以上的 15 天，原则上当年度休完。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["年假"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "doc_qa-Docker概念",
            "interface_name": "chat",
            "input": _gq_content("Docker 的核心概念和安装步骤是什么？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "Docker 核心概念为镜像（只读模板）、容器（运行实例）、仓库（存放镜像）；Ubuntu 安装：apt-get update→装依赖→配源→装 docker-ce→启动并验证。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["镜像"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "doc_qa-发布流程",
            "interface_name": "chat",
            "input": _gq_content("产品版本正式上线需要满足什么条件？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "上线条件：测试环境用例全过且缺陷清单清零/书面确认；预发布冒烟通过；性能压测 P99≤500ms、可用性≥99.9%；关键变更经架构评审。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
                # 断言与 golden 对齐：golden 列的上线条件含"预发布冒烟通过"，改查"冒烟"
                # （原查"灰度"是发布流程环节、非上线条件，与 golden 自相矛盾，LLM 聚焦条件时省略灰度被误判缺陷）
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["冒烟"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "doc_qa-保密违约金",
            "interface_name": "chat",
            "input": _gq_content("客户数据泄露的赔偿或违约金是怎么约定的？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "因泄密导致客户数据泄露的，接收方应向提供方支付违约金人民币 50 万元；实际损失超过违约金的按实际损失赔偿。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
                # golden 原文即"违约金人民币 50 万元"（数字与单位间有空格），"50万"连写为过度苛刻的简写断言
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["50", "万元"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "no_hit-工资发放日",
            "interface_name": "chat",
            "input": _gq_content("工资发放日是哪天？"),
            "expected": {
                "reference_docs": GQ_DOCS + "\n" + GQ_EMPTY_NOTE,
                "golden_answer": "文档库未收录工资发放相关条款，应如实回复未找到，不编造具体日期。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["未找到"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "summarize-总结考勤",
            "interface_name": "chat",
            "input": _gq_content("总结一下考勤管理制度的主要内容"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "总结考勤制度：工作时间与工时、请假管理（事假/病假/年假等）、迟到早退处理、加班与调休、缺勤处理及全勤奖。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["加班"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
        {
            "name": "greeting-打招呼",
            "interface_name": "chat",
            "input": _gq_content("你好，请问在吗？"),
            "expected": {
                "reference_docs": GQ_DOCS,
                "golden_answer": "问候场景：正常友好应答并引导提问，不强制检索、不说未找到。",
            },
            "assertions": [
                _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
                _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["你好"]}, "completeness"),
            ],
            "metrics": ACCURACY_4,
            "status": "active",
        },
    ]


# 骨架补足：3121-3124 原 input 全 {"content":"你好"}、expected 空 → 按 scene 语义补足并填 expected
GQ_SKELETON_UPDATES = {
    3121: {  # greeting（保持你好）
        "input": _gq_content("你好"),
        "expected": {
            "reference_docs": GQ_DOCS,
            "golden_answer": "问候场景：友好应答并引导提问。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["你好"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3122: {  # doc_qa：考勤事假
        "input": _gq_content("请事假有什么规定？"),
        "expected": {
            "reference_docs": GQ_DOCS,
            "golden_answer": "事假须提前 3 个工作日提出申请，经审批通过后方可休假，事假期间不计发工资。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("tool_called", {"tool": "hybrid_retrieve"}, "tool_usage"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["工作日"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3123: {  # no_hit：文档相关但库 3 未收录
        "input": _gq_content("生育津贴怎么申请？"),
        "expected": {
            "reference_docs": GQ_DOCS + "\n" + GQ_EMPTY_NOTE,
            "golden_answer": "文档库未收录生育津贴相关内容，应如实回复未找到，不编造。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["未找到"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
    3124: {  # summarize：总结考勤制度
        "input": _gq_content("总结员工考勤管理制度的主要内容"),
        "expected": {
            "reference_docs": GQ_DOCS,
            "golden_answer": "总结考勤制度：工作时间、请假管理、迟到早退、加班调休、缺勤处理与全勤奖。",
        },
        "assertions": [
            _mk_assertion("field_nonempty", {"path": "answer"}, "completeness"),
            _mk_assertion("keyword_contains", {"path": "answer", "keywords": ["考勤"]}, "completeness"),
        ],
        "metrics": ACCURACY_4,
        "status": "active",
    },
}


# ==================== 执行 ====================
def seed(agent: str) -> None:
    aid = AGENT_IDS[agent]
    with httpx.Client(timeout=30, trust_env=False) as c:
        h = _auth(c)
        iface_map = {}
        r = c.get(f"{PLATFORM}/agents/{aid}/interfaces", headers=h)
        if r.status_code < 400:
            iface_map = {itf["name"]: itf["id"] for itf in r.json()["data"]}
        print(f"== {agent} agent={aid} interfaces={iface_map} ==")

        # 1) 骨架补足
        skel_updates = globals().get(f"{agent.upper()}_SKELETON_UPDATES", {})
        # gq 全 case 金丝雀（改动评测侧防御）：骨架补足断言也带，防既有 case 漏网
        if agent == "gq":
            for body in skel_updates.values():
                body.setdefault("assertions", []).append(_GQ_CANARY)
        for cid, body in skel_updates.items():
            iface = body.pop("interface_id", None)
            if iface:
                body["interface_id"] = iface
            _update_case(c, h, cid, body)
        print(f"  ✓ 骨架补足 {len(skel_updates)} 条")

        # 2) 新 suite
        cases_builder = globals().get(f"{agent}_real_cases", lambda: [])
        cases = cases_builder()
        # gq 全 case 金丝雀：真实 case 统一追加（DRY，新增 case 不手写；算子为通用负向断言，
        # 任何 agent 的 answer 残留工具调用声明都会被判 fail，平台侧客观公平）
        if agent == "gq":
            for body in cases:
                body.setdefault("assertions", []).append(_GQ_CANARY)
        if not cases:
            print(f"  - 无新用例（{agent} 未实现或不需要）")
            return
        suite_id = _create_suite(c, h, aid, f"{agent} 真实业务评测", "真实业务场景测试集（完整评测）")
        print(f"  ✓ suite_id={suite_id}")
        ok_n = 0
        new_ids = []
        for body in cases:
            iname = body.pop("interface_name")
            body["interface_id"] = iface_map[iname]
            body.setdefault("input_type", "text")  # file 型由 builder 显式指定
            if body.get("scenes"):
                body["scenes"] = body["scenes"]  # 已 list
            cid = _create_case(c, h, suite_id, body)
            if cid:
                ok_n += 1
                new_ids.append(cid)
        # create_case 不落 status（默认 draft）；新 suite 用例置 active 才能被 run 执行
        for cid in new_ids:
            _update_case(c, h, cid, {"status": "active"})
        print(f"  ✓ 新 suite 落库 {ok_n}/{len(cases)} 条（已置 active）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, choices=["cs", "cc", "sp", "gq"])
    args = ap.parse_args()
    seed(args.agent)


if __name__ == "__main__":
    main()
