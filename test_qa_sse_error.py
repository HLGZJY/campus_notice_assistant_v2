"""批次 D3 验收：流式 SSE 错误兜底（离线，TestClient 临时库隔离）。

覆盖：
  1. 中途断流（已产出 delta 后抛异常）→ 末事件 error、message=友好文案
     「推理中断，请稍后重试」、HTTP 200 流正常关闭（不抛 500）
  2. 首 token 前失败（全部模型失败路径）→ 同样出 error 事件而非 500
  3. error 事件后流正常结束（iter_lines 完整收尾，无挂起）

用法：python test_qa_sse_error.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["APP_ENV"] = "test"

logging.basicConfig(level=logging.CRITICAL, format="%(levelname)s %(message)s")

failures: list[str] = []

FRIENDLY_ERROR = "推理中断，请稍后重试"


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def _seed_config(config_dir: Path) -> None:
    schools = config_dir / "schools"
    schools.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        """active_school: scuec
models:
  extraction:
    provider: opencode-zen
    model: model-a
  qa:
    provider: opencode-zen
    model: model-a
  todo:
    provider: opencode-zen
    model: model-a
  embedding:
    provider: local
    model: bge
providers:
  opencode-zen:
    name: opencode-zen
    base_url: http://localhost:1
    api_key_env: OPENCODE_API_KEY
  local:
    name: local
    base_url: ""
    api_key_env: ""
    models: ["bge"]
""",
        encoding="utf-8",
    )
    (schools / "scuec.yaml").write_text(
        """name: 测试大学
code: scuec
sources:
- name: 教务处
  type: web
  list_url: http://example.com/tzgg.htm
  max_pages: 3
""",
        encoding="utf-8",
    )


def main() -> None:
    from fastapi.testclient import TestClient
    from unittest.mock import patch

    from api.main import create_app

    tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmpdir.name) / "test_qa_sse.db"

    from storage import db as db_mod

    db_mod.DB_PATH = db_path
    db_mod.get_connection().close()

    from config.store import ConfigStore

    config_dir = Path(tmpdir.name) / "config"
    _seed_config(config_dir)
    ConfigStore.reset_instance()
    ConfigStore.get_instance(config_dir)

    with TestClient(create_app()) as client:
        print("== 1. 中途断流：delta 后抛异常 → error 事件 + 200 正常关流 ==")
        async def _fail_stream(question):
            yield ("delta", "部分输出已到达")
            raise RuntimeError("模拟 LLM 中途故障")

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = _fail_stream
            with client.stream(
                "GET", "/api/v1/qa/ask/stream", params={"question": "会中途失败的问题"}
            ) as r:
                check(
                    "HTTP 200 + text/event-stream（不抛 500）",
                    r.status_code == 200
                    and r.headers["content-type"].startswith("text/event-stream"),
                    f"status={r.status_code} ctype={r.headers.get('content-type')}",
                )
                events = [
                    json.loads(line[6:])
                    for line in r.iter_lines()
                    if line.startswith("data: ")
                ]
        check(
            "事件序列 [delta, error]",
            [e["type"] for e in events] == ["delta", "error"],
            f"{[e['type'] for e in events]}",
        )
        check(
            "error message 为友好文案（不泄露异常内部细节）",
            events[-1]["message"] == FRIENDLY_ERROR,
            f"message={events[-1].get('message')!r}",
        )
        check("delta 内容保留", events[0]["content"] == "部分输出已到达", f"{events[0]}")

        print("\n== 2. 首 token 前失败（无任何 delta）→ error 事件 + 200 ==")
        async def _early_fail(question):
            # async generator：首次迭代即抛异常（贴合真实 ask_stream 路径，
            # 而非把普通协程交给 async for 迭代导致 TypeError）
            if False:
                yield None
            raise RuntimeError("模型全失败")

        with patch("core.qa.QAAgent") as FakeAgent:
            FakeAgent.return_value.ask_stream = _early_fail
            with client.stream(
                "GET", "/api/v1/qa/ask/stream", params={"question": "一进来就失败"}
            ) as r:
                check(
                    "HTTP 200（错误事件化，不升级为 500）",
                    r.status_code == 200,
                    f"status={r.status_code}",
                )
                events = [
                    json.loads(line[6:])
                    for line in r.iter_lines()
                    if line.startswith("data: ")
                ]
        check(
            "仅一个 error 事件且为友好文案",
            len(events) == 1
            and events[0]["type"] == "error"
            and events[0]["message"] == FRIENDLY_ERROR,
            f"{events}",
        )

    tmpdir.cleanup()
    print("=" * 60)
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    main()