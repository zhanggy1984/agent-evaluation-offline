# -*- coding: utf-8 -*-
"""一次性：给 gq suite 2161 补 no_hit/summarize/greeting 场景 case（扩充充分性）。

背景：场景覆盖 25%->100% 后，no_hit(1)/summarize(1)/greeting(1) 各仅 1 个 case，
充分性不足。本脚本基于库 3「技术文档库」4 份真实文档未覆盖的知识点补 6 个 case：
- no_hit 2：入职体检报销（库相关但未收录）、食堂停车位（完全无关）
- summarize 2：Docker 手册总结、发布流程归纳
- greeting 2：早上好、谢谢

断言沿用现有风格：field_nonempty + keyword_contains（命中真实输出）
+ 金丝雀 keyword_not_contains（防 DSML/tool_calls 泄漏）。no_hit/greeting 不强制 tool_called。
走平台 API POST /suites/2161/cases。幂等：按 name 查重跳过。

凭据：ADMIN_PASSWORD 环境变量优先，兜底 ../.env（2026-09-01 起平台账号密码统一 123456）。
本机运行亦可 `ADMIN_PASSWORD=123456 python scripts/_add_gq_scene_cases.py` 显式注入。
"""
import os

import httpx

PLATFORM = "http://localhost:8180/api"
SUITE_ID = 2161
INTERFACE_ID = 2188  # gq chat 接口


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

METRICS_4 = {
    "factuality": {"enabled": True},
    "tool_usage": {"enabled": True},
    "completeness": {"enabled": True},
    "reasoning_quality": {"enabled": True},
}


def mk(op: str, args: dict, dim: str) -> dict:
    return {"op": op, "args": args, "dimension": dim}


def canary() -> dict:
    return mk("keyword_not_contains", {"path": "answer", "keywords": ["DSML", "tool_calls"]}, "completeness")


# ---- ground truth：库 3 已灌文档（source of truth，用于 reference_docs）----
GQ_DOCKER = (
    "库 3 已灌文档（ground truth）：\n"
    "- Docker 核心概念：镜像（只读模板，定义运行环境与代码）、容器（镜像的运行实例，可启停删）、"
    "仓库（集中存放镜像，如 Docker Hub 或私有仓库）\n"
    "- 安装：Ubuntu（apt-get update→装依赖→加 GPG 密钥配源→装 docker-ce→systemctl enable --now docker→"
    "docker --version + run hello-world 验证）；CentOS 7+（yum remove 旧版→装依赖→配源→装 docker-ce→"
    "systemctl start+enable）；Windows（Docker Desktop，勾选 WSL2 后端，引擎 Running 后 docker --version 验证）\n"
    "- 常用命令：docker pull 拉镜像、docker images 查看镜像、docker run（-d 后台/-p 端口映射/--name 命名/-v 挂载卷）、"
    "docker ps(-a)、docker stop/start/rm/rmi、docker build -t 镜像:标签 .、docker push、docker logs\n"
    "- 加速与私有仓库：国内用阿里云镜像加速器；企业内部可搭 Harbor/自建 Registry，非 HTTPS 仓库在 "
    "daemon.json 配 insecure-registries 或登录认证\n"
    "- 故障排查：权限不足 permission denied 加 docker 组 usermod -aG docker $USER；容器无法访问外网查 DNS；"
    "端口占用 port is already allocated 换端口或停占用容器；拉取慢配镜像加速器"
)

GQ_PUBLISH = (
    "库 3 已灌文档（ground truth）：\n"
    "- 流程总览：需求评审→开发→测试→预发布验证→灰度发布→正式发布→线上监控，绕过流程'直发'视为违规\n"
    "- 各环境：开发（日常联调、提交即构建、不做发布约束）/测试（验证功能、回归缺陷）/预发布（与生产完全一致的仿真环境，"
    "发布前最后一轮冒烟）/灰度（先让 5% 流量按用户 ID 尾号进新版本，验证无异常再全量）/生产（灰度通过后放量）\n"
    "- 上线条件：测试环境用例全通过且缺陷清单清零或书面确认暂缓、预发布冒烟通过（登录/主流程/支付下单无阻塞）、"
    "性能压测 P99 响应 ≤500ms 且可用性 ≥99.9%、关键变更（库表结构/核心接口/安全权限）架构评审通过并记录\n"
    "- 发布窗口：常规为工作日 20:00-22:00；紧急修复（高危线上缺陷）不受窗口限制但须值班负责人审批全程跟进；"
    "周五 17:00 后与法定节假日前一天原则上不安排常规发布\n"
    "- 回滚：发布前必须准备上一版本镜像/制品并验证回滚步骤；触发条件=发布后 15 分钟内核心指标明显恶化/P0 事故/灰度数据异常；"
    "回滚由发布负责人执行，回滚后 30 分钟内复盘留记录\n"
    "- 审批与违规：研发负责人提交，测试/运维/产品三方审批通过后执行；全程在发布平台留痕供审计；擅自发布通报批评或绩效处罚"
)

GQ_ATTENDANCE = (
    "库 3 已灌文档（ground truth）：\n"
    "- 标准工时制：周一至周五 9:00-12:00、13:00-18:00，午休 12:00-13:00，每天 8 小时每周 40 小时，适用于全体正式员工\n"
    "- 请假：事假提前 3 个工作日申请不计发工资；病假须医院诊断证明/病假条，按当地最低工资 80% 发放；"
    "年假满 1 年 5 天/满 10 年 10 天/满 20 年 15 天，当年度休完不跨年；婚产丧假按国家规定凭证明\n"
    "- 迟到/早退：超过上班时间 5-30 分钟记迟到一次、下班前 30 分钟内擅自离岗记早退一次，每次扣 50 元；"
    "当月累计 3 次视缺勤半天；交通等不可抗力当日说明经核实可免扣\n"
    "- 加班：须提前报部门负责人审批；工作日 1.5 倍、休息日 2 倍；可 1:1 折算调休，加班起 3 个月内使用完逾期作废；"
    "未经审批不视为加班\n"
    "- 旷工：未请假未到岗视为旷工，半天以内按 2 倍日工资扣款、1 天按 3 倍；连续旷工 3 天以上或年度累计 5 天以上可解除劳动合同；"
    "缺勤不享受当月全勤奖（标准 300 元/月）"
)

CASES = [
    {
        "name": "no_hit-入职体检报销",
        "input": {"content": "员工入职体检的费用可以报销吗？"},
        "expected": {
            "golden_answer": "文档库未收录入职体检报销相关条款，应如实回复未找到，不编造报销政策。",
            "reference_docs": GQ_ATTENDANCE + "\n（考勤制度仅覆盖请假/考勤/加班/旷工，无入职体检报销条款）",
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "match": "any",
                                     "keywords": ["未找到", "没有找到", "未规定", "未提及", "未明确"]},
               "completeness"),
            canary(),
        ],
        "scenes": ["no_hit"],
    },
    {
        "name": "no_hit-食堂停车位",
        "input": {"content": "公司有员工食堂或者停车位吗？"},
        "expected": {
            "golden_answer": "文档库未收录员工食堂或停车位相关信息，应如实回复未找到，不编造。",
            "reference_docs": GQ_ATTENDANCE + "\n（考勤制度与食堂/停车位无关，属完全未收录话题）",
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "match": "any",
                                     "keywords": ["未找到", "没有找到", "未规定", "未提及", "未明确"]},
               "completeness"),
            canary(),
        ],
        "scenes": ["no_hit"],
    },
    {
        "name": "summarize-Docker手册总结",
        "input": {"content": "总结一下 Docker 环境安装部署手册的主要内容"},
        "expected": {
            "golden_answer": "总结 Docker 手册：核心概念（镜像/容器/仓库）、各平台安装（Ubuntu/CentOS/Windows）、"
                             "常用命令、镜像加速与私有仓库、常见问题排查。",
            "reference_docs": GQ_DOCKER,
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "keywords": ["Docker"]}, "completeness"),
            mk("keyword_contains", {"path": "answer", "keywords": ["安装"]}, "completeness"),
            canary(),
        ],
        "scenes": ["summarize"],
    },
    {
        "name": "summarize-发布流程归纳",
        "input": {"content": "把产品发布上线流程规范的核心要点归纳一下"},
        "expected": {
            "golden_answer": "归纳发布规范：流程总览（需求评审→开发→测试→预发布→灰度→正式→监控）、各环境职责、"
                             "上线条件、发布窗口、回滚机制、三方审批与违规责任。",
            "reference_docs": GQ_PUBLISH,
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "keywords": ["发布"]}, "completeness"),
            mk("keyword_contains", {"path": "answer", "keywords": ["回滚"]}, "completeness"),
            canary(),
        ],
        "scenes": ["summarize"],
    },
    {
        "name": "greeting-早上好",
        "input": {"content": "早上好！"},
        "expected": {
            "golden_answer": "问候场景：正常友好应答并引导提问，不强制检索、不说未找到。",
            "reference_docs": GQ_ATTENDANCE + "\n（问候场景，无需检索文档）",
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "match": "any", "keywords": ["早上好", "你好"]},
               "completeness"),
            canary(),
        ],
        "scenes": ["greeting"],
    },
    {
        "name": "greeting-谢谢",
        "input": {"content": "好的，谢谢你的解答！"},
        "expected": {
            "golden_answer": "礼貌收尾场景：友好回应，不强制检索、不编造文档内容。",
            "reference_docs": GQ_ATTENDANCE + "\n（礼貌回应场景，无需检索文档）",
        },
        "assertions": [
            mk("field_nonempty", {"path": "answer"}, "completeness"),
            mk("keyword_contains", {"path": "answer", "match": "any", "keywords": ["不客气", "谢谢", "有需要"]},
               "completeness"),
            canary(),
        ],
        "scenes": ["greeting"],
    },
]


def main() -> None:
    with httpx.Client(timeout=30, trust_env=False) as c:
        r = c.post(f"{PLATFORM}/auth/login", json={"username": "admin", "password": ADMIN_PWD})
        if r.status_code >= 400:
            raise SystemExit(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
        h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

        # 查重：已存在的 name 跳过（幂等）
        r = c.get(f"{PLATFORM}/suites/{SUITE_ID}/cases", headers=h)
        if r.status_code >= 400:
            raise SystemExit(f"suite cases GET 失败 HTTP {r.status_code}: {r.text[:200]}")
        cases_data = r.json()["data"]
        exists = {c_["name"] for c_ in (cases_data if isinstance(cases_data, list) else cases_data.get("cases", []))}

        created = 0
        for case in CASES:
            if case["name"] in exists:
                print(f"已存在，跳过: {case['name']}")
                continue
            body = {
                "name": case["name"],
                "interface_id": INTERFACE_ID,
                "input_type": "text",
                "input": case["input"],
                "expected": case["expected"],
                "assertions": case["assertions"],
                "metrics": METRICS_4,
                "scenes": case["scenes"],
            }
            r = c.post(f"{PLATFORM}/suites/{SUITE_ID}/cases", headers=h, json=body)
            if r.status_code >= 400:
                print(f"创建失败 {case['name']} HTTP {r.status_code}: {r.text[:300]}")
                continue
            cid = r.json()["data"]["id"]
            # CaseCreate 无 status 字段，落库默认 draft；需 PUT 激活
            r = c.put(f"{PLATFORM}/cases/{cid}", headers=h, json={"status": "active"})
            if r.status_code >= 400:
                print(f"激活失败 {case['name']} HTTP {r.status_code}: {r.text[:200]}")
            created += 1
            print(f"OK {cid} {case['name']} scenes={case['scenes']} (已激活)")
        print(f"DONE: 新创建 {created} 个 case")


if __name__ == "__main__":
    main()
