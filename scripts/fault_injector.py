"""
故障注入脚本 —— 通过 HTTP 调用微服务的 /fault/* 端点，模拟典型生产故障。

用法：
  python fault_injector.py --service order-service --fault cpu --duration 30
  python fault_injector.py --service user-service --fault npe
  python fault_injector.py --all                           # 查看所有可用故障
  python fault_injector.py --scenario slow-order           # 运行预设场景
"""

import argparse
import os
import sys
import time
import requests
from dataclasses import dataclass

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ==================== 服务地址 ====================
# 默认使用本地 demo 微服务地址，可通过环境变量覆盖
# 如：GATEWAY_URL=http://192.168.1.100:8080 ORDER_URL=...

SERVICES = {
    "gateway":  os.environ.get("GATEWAY_URL", "http://localhost:8080"),
    "order":    os.environ.get("ORDER_URL", "http://localhost:8081"),
    "user":     os.environ.get("USER_URL", "http://localhost:8082"),
}

# ==================== 故障定义 ====================

@dataclass
class Fault:
    method: str
    path: str
    params: dict
    description: str

FAULTS = {
    "cpu": Fault(
        method="POST",
        path="/fault/cpu",
        params={"seconds": "30"},
        description="CPU 飙升：启动死循环线程，吃满一个核心",
    ),
    "memory": Fault(
        method="POST",
        path="/fault/memory",
        params={"mbPerCall": "10", "calls": "5"},
        description="内存泄漏：持续分配 byte[] 不释放，模拟慢速内存泄漏",
    ),
    "npe": Fault(
        method="POST",
        path="/fault/npe",
        params={},
        description="空指针异常：故意 dereference null，触发 500",
    ),
    "slow": Fault(
        method="GET",
        path="/fault/slow",
        params={"seconds": "5"},
        description="慢接口：Thread.sleep 模拟慢 SQL 或外部调用超时",
    ),
    "timeout": Fault(
        method="GET",
        path="/fault/timeout",
        params={},
        description="服务超时：60 秒不返回，模拟下游服务 hang 住（仅 order-service）",
    ),
}

# ==================== 预设场景 ====================

SCENARIOS = {
    "slow-order": [
        ("order", "slow", {"seconds": "5"}),
    ],
    "cpu-order": [
        ("order", "cpu", {"seconds": "60"}),
    ],
    "memory-user": [
        ("user", "memory", {"mbPerCall": "20", "calls": "10"}),
    ],
    "cascade-npe": [
        ("user", "npe", {}),
    ],
    "full-chaos": [
        ("order", "cpu", {"seconds": "60"}),
        ("user", "memory", {"mbPerCall": "15", "calls": "5"}),
        ("order", "slow", {"seconds": "3"}),
    ],
}


def inject(service: str, fault_name: str, custom_params: dict | None = None) -> dict:
    """向指定服务注入指定故障，返回响应。"""
    if service not in SERVICES:
        return {"error": f"未知服务: {service}，可选: {list(SERVICES.keys())}"}
    if fault_name not in FAULTS:
        return {"error": f"未知故障: {fault_name}，可选: {list(FAULTS.keys())}"}

    fault = FAULTS[fault_name]
    url = f"{SERVICES[service]}{fault.path}"
    params = {**fault.params, **(custom_params or {})}

    print(f"🔥 注入故障: [{service}] {fault.description}")
    print(f"   {fault.method} {url} params={params}")

    try:
        if fault.method == "POST":
            resp = requests.post(url, params=params, timeout=10)
        else:
            resp = requests.get(url, params=params, timeout=10)

        print(f"   ✅ 响应: {resp.status_code} {resp.text[:200]}")
        return {"status": resp.status_code, "body": resp.text}
    except requests.exceptions.Timeout:
        print(f"   ⚠️  请求超时（服务可能 hang 住了——这其实是预期的故障效果）")
        return {"status": "timeout", "body": ""}
    except requests.exceptions.ConnectionError:
        print(f"   ❌ 连接失败——服务可能挂了（NPE 导致 500 或进程退出？）")
        return {"status": "connection_error", "body": ""}


def run_scenario(name: str):
    """运行预设故障场景。"""
    if name not in SCENARIOS:
        print(f"未知场景: {name}，可选: {list(SCENARIOS.keys())}")
        return

    steps = SCENARIOS[name]
    print(f"\n🎬 运行场景: {name} ({len(steps)} 步)")
    print("=" * 50)

    for i, (svc, fault, params) in enumerate(steps, 1):
        print(f"\n--- 步骤 {i}/{len(steps)} ---")
        inject(svc, fault, params)
        if i < len(steps):
            time.sleep(1)

    print(f"\n✅ 场景 {name} 执行完成")


def list_all():
    """列出所有服务和故障。"""
    print("📋 可用服务:")
    for name, url in SERVICES.items():
        print(f"   {name:10s} → {url}")

    print("\n📋 可用故障类型:")
    for name, fault in FAULTS.items():
        print(f"   {name:10s} → {fault.description}")

    print("\n📋 预设场景:")
    for name in SCENARIOS:
        steps = SCENARIOS[name]
        desc = " → ".join(f"{s} {f}" for s, f, _ in steps)
        print(f"   {name:15s} → {desc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIOps 故障注入工具")
    parser.add_argument("--service", "-s", choices=list(SERVICES.keys()), help="目标服务")
    parser.add_argument("--fault", "-f", choices=list(FAULTS.keys()), help="故障类型")
    parser.add_argument("--duration", "-d", type=int, default=None, help="持续时间（秒）")
    parser.add_argument("--mb", type=int, default=None, help="内存泄漏每次分配的 MB")
    parser.add_argument("--calls", type=int, default=None, help="内存泄漏的分配次数")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="预设场景名")
    parser.add_argument("--all", action="store_true", help="列出所有可用故障")

    args = parser.parse_args()

    if args.all or (not args.service and not args.scenario):
        list_all()
    elif args.scenario:
        run_scenario(args.scenario)
    elif args.service and args.fault:
        custom_params = {}
        if args.duration is not None:
            custom_params["seconds"] = str(args.duration)
        if args.mb is not None:
            custom_params["mbPerCall"] = str(args.mb)
        if args.calls is not None:
            custom_params["calls"] = str(args.calls)
        inject(args.service, args.fault, custom_params)
    else:
        parser.print_help()
