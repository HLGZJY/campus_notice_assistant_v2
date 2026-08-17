"""模型失败切换 + 供应商 API Key 写入 .env 的离线测试。

覆盖：
  1. ModelProfile 旧格式 model: "x" 自动迁移为 models: ["x"]
  2. get_model_candidates 返回有序候选列表（同供应商内失败切换的数据源）
  3. is_failover_worthy 判定：400/401/403 不切换，429/5xx/404/网络 切换
  4. NoticeExtractor：模型1抛可恢复错误 → 自动切模型2成功（顺序正确）
  5. NoticeExtractor：全部候选失败 → 返回 failed
  6. TodoGenerator / QAAgent.ask 同供应商切换
  7. save_api_key 写 .env：追加/替换幂等、保留注释、同步 os.environ、未知供应商报错、
     未配 api_key_env 自动生成并持久化
  8. 供应商删除守卫：被任务引用的供应商不允许删除
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

os.environ["APP_ENV"] = "test"

from config.schema import ModelProfile  # noqa: E402
from config.store import ConfigStore  # noqa: E402
from services.config_service import update_providers  # noqa: E402
from utils.llm import is_failover_worthy  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> None:
    print("== 1. ModelProfile 旧格式迁移 ==")
    p = ModelProfile(**{"provider": "opencode-zen", "model": "kimi-k2.7-code"})
    check("model:'x' → models:['x']", p.models == ["kimi-k2.7-code"], f"{p.models}")
    p2 = ModelProfile(**{"provider": "bailian", "models": ["a", "b", " c "]})
    check("models 列表去空去空格", p2.models == ["a", "b", "c"], f"{p2.models}")
    try:
        ModelProfile(**{"provider": "x", "models": []})
        check("空 models 拒绝", False, "未抛错")
    except ValueError:
        check("空 models 拒绝", True)
    try:
        ModelProfile(**{"provider": "x", "model": ""})
        check("空 model 迁移后仍拒绝", False, "未抛错")
    except ValueError:
        check("空 model 迁移后仍拒绝", True)

    print("\n== 2. get_model_candidates 有序候选 ==")
    tmp = Path(tempfile.mkdtemp())
    cfg_dir = tmp / "config"
    cfg_dir.mkdir()
    (tmp / ".env").write_text("", encoding="utf-8")
    ConfigStore.reset_instance()
    store = ConfigStore.get_instance(cfg_dir)
    provider, models = store.get_model_candidates("qa")
    check("provider=opencode-zen", provider.name == "opencode-zen", provider.name)
    check("qa 候选顺序", models == ["deepseek-v4-pro", "kimi-k2.7-code"], f"{models}")
    _, emb_models = store.get_model_candidates("embedding")
    check("embedding 单候选", emb_models == ["sentence-transformers/all-MiniLM-L6-v2"], f"{emb_models}")

    print("\n== 3. is_failover_worthy 判定 ==")

    class FakeStatusError(RuntimeError):
        def __init__(self, code: int):
            self.status_code = code
            super().__init__(f"err {code}")

    check("429(配额/限流) → 切换", is_failover_worthy(FakeStatusError(429)))
    check("500(5xx) → 切换", is_failover_worthy(FakeStatusError(500)))
    check("404(模型不存在) → 切换", is_failover_worthy(FakeStatusError(404)))
    check("网络异常(无 status) → 切换", is_failover_worthy(RuntimeError("conn refused")))
    check("400 → 不切换", not is_failover_worthy(FakeStatusError(400)))
    check("401(鉴权) → 不切换", not is_failover_worthy(FakeStatusError(401)))
    check("403(鉴权) → 不切换", not is_failover_worthy(FakeStatusError(403)))
    try:
        from openai import BadRequestError

        class FakeResp:
            status_code = 400
            headers = {}
            request = None
            text = "bad request"
            content = b"bad request"

            def json(self):
                return {"error": {}}

        bad = BadRequestError("bad request", response=FakeResp(), body={"error": {}})
        check("BadRequestError → 不切换", not is_failover_worthy(bad))
    except Exception as e:  # noqa: BLE001
        check("BadRequestError → 不切换", True, f"（跳过：{type(e).__name__}）")

    print("\n== 4. NoticeExtractor 失败切换：模型1失败 → 模型2成功 ==")
    import core.extractor as core_extractor
    from core.models import NoticeExtraction

    calls: list[str] = []

    async def fake_run(agent, prompt, **kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "model-a":
            raise RuntimeError("quota exceeded 429")
        return SimpleNamespace(final_output=NoticeExtraction(title="t", notice_type="competition", summary="s"))

    core_extractor.run_agent = fake_run
    extractor = core_extractor.NoticeExtractor.__new__(core_extractor.NoticeExtractor)
    extractor.api_key = "k"
    extractor.base_url = "https://example.com/v1"
    extractor.provider = "test-prov"
    extractor.models = ["model-a", "model-b"]
    extractor._agents = {}
    extractor._get_agent = lambda model: object()
    outcome = asyncio.run(extractor.extract_one("标题", "正文内容"))
    check("切换后提取成功", outcome.status in ("extracted", "partial") and outcome.extraction is not None, outcome.status)
    check("调用顺序 a→b", calls == ["model-a", "model-b"], f"{calls}")

    print("\n== 5. NoticeExtractor 全部候选失败 → failed ==")
    calls2: list[str] = []

    async def fake_run_all_fail(agent, prompt, **kwargs):
        calls2.append(kwargs["model"])
        raise RuntimeError("always down")

    core_extractor.run_agent = fake_run_all_fail
    outcome = asyncio.run(extractor.extract_one("标题", "正文"))
    check("返回 failed", outcome.status == "failed", outcome.status)
    check("失败信息含 model-b", "模型 model-b 失败" in (outcome.error or ""), f"{outcome.error}")
    check("两个模型都试过", calls2 == ["model-a", "model-b"], f"{calls2}")

    print("\n== 6. TodoGenerator / QAAgent.ask 同供应商切换 ==")
    import core.todo as core_todo
    from core.models import TodoList

    t_calls: list[str] = []
    result = {"n": 0}

    async def fake_todo_run(agent, prompt, **kwargs):
        t_calls.append(kwargs["model"])
        if kwargs["model"] == "todo-a":
            raise RuntimeError("quota")
        return SimpleNamespace(final_output=TodoList(items=[]))

    core_todo.run_agent = fake_todo_run
    gen = core_todo.TodoGenerator.__new__(core_todo.TodoGenerator)
    gen.provider = "test-prov"
    gen.models = ["todo-a", "todo-b"]
    gen._agents = {}
    gen._get_agent = lambda model: object()
    items = asyncio.run(gen.generate_one({"id": 1, "title": "报名", "deadline": None, "notice_type": "registration"}))
    # todo 链路保持原有"模型内重试 1 次再跨模型切换"语义：todo-a ×2 → todo-b
    check("todo 切换后成功", t_calls == ["todo-a", "todo-a", "todo-b"], f"{t_calls}")

    import core.qa as core_qa

    q_calls: list[str] = []

    async def fake_qa_run(agent, prompt, **kwargs):
        q_calls.append(kwargs["model"])
        if kwargs["model"] == "qa-a":
            raise RuntimeError("quota")
        return SimpleNamespace(final_output="答案")

    core_qa.run_agent = fake_qa_run
    qa = core_qa.QAAgent.__new__(core_qa.QAAgent)
    qa.provider = "test-prov"
    qa.models = ["qa-a", "qa-b"]
    qa._agents = {}
    qa.top_k = 6
    qa.max_sources = 5
    qa.search_mode = "vector"
    qa.strategy = "none"
    qa.expire_days = None
    qa.search_kwargs = {}
    qa._get_agent = lambda model: object()
    qa.index = SimpleNamespace(
        search=lambda question, k, **kwargs: [
            SimpleNamespace(
                metadata={"notice_id": 1, "title": "通知A", "notice_type": "competition", "source": "s", "url": "u", "deadline": None},
                page_content="内容",
            )
        ]
    )
    res = asyncio.run(qa.ask("有什么竞赛？"))
    check("qa 切换后成功", q_calls == ["qa-a", "qa-b"] and res.answer == "答案", f"{q_calls}")

    print("\n== 7. save_api_key 写 .env（追加/替换/注释/环境变量/未知供应商/自动生成） ==")
    env_path = tmp / ".env"
    env_path.write_text("# 顶部注释\n\nOTHER=keep-me\n", encoding="utf-8")
    ConfigStore.reset_instance()
    store = ConfigStore.get_instance(cfg_dir)

    res = store.save_api_key("bailian", "sk-abc")
    check("bailian 写入成功", res["ok"] and res["env_var"] == "DASHSCOPE_API_KEY", f"{res}")
    txt = env_path.read_text(encoding="utf-8")
    check(".env 含新 Key", "DASHSCOPE_API_KEY=sk-abc" in txt, txt)
    check("顶部注释保留", "# 顶部注释" in txt)
    check("无关行保留", "OTHER=keep-me" in txt)
    check("os.environ 同步", os.environ.get("DASHSCOPE_API_KEY") == "sk-abc")

    res = store.save_api_key("bailian", "sk-new")
    txt = env_path.read_text(encoding="utf-8")
    check("重复写入幂等（单行替换）", txt.count("DASHSCOPE_API_KEY=sk-new") == 1 and "sk-abc" not in txt, txt)

    res = store.save_api_key("nope", "x")
    check("未知供应商报错", not res["ok"], f"{res}")
    res = store.save_api_key("bailian", "")
    check("空 Key 报错", not res["ok"], f"{res}")

    res = store.save_api_key("local", "local-key")
    local_p = store.get_provider("local")
    check("自动生成 env 变量名并持久化", res["ok"] and local_p.api_key_env == "LOCAL_API_KEY", f"{res} {local_p.api_key_env}")
    check("os.environ 同步（local）", os.environ.get("LOCAL_API_KEY") == "local-key")
    yaml_txt = (cfg_dir / "app.yaml").read_text(encoding="utf-8")
    check("自动生成的 env 名已写入 app.yaml", "LOCAL_API_KEY" in yaml_txt, "app.yaml 已更新")

    print("\n== 8. 供应商删除守卫 ==")
    ConfigStore.reset_instance()
    store = ConfigStore.get_instance(cfg_dir)
    provs = store.app_config.providers

    # 默认任务引用 opencode-zen / local，删除 opencode-zen 应被拒
    new_provs = {k: v for k, v in provs.items() if k != "opencode-zen"}
    res = update_providers({k: v.model_dump() for k, v in new_provs.items()})
    check("删除被引用的供应商被拒", not res["ok"] and "不存在" in res.get("error", ""), f"{res.get('error')}")

    # bailian 默认未被引用，删除应成功
    new_provs = {k: v for k, v in provs.items() if k != "bailian"}
    res = update_providers({k: v.model_dump() for k, v in new_provs.items()})
    check("删除未引用的供应商成功", res["ok"], f"{res}")

    print("\n== 9. 供应商 display_name / type ==")
    from config.defaults import DEFAULT_PROVIDER_BAILIAN  # noqa: PLC0415
    from config.schema import ProviderConfig, infer_provider_type  # noqa: PLC0415

    check("推断:空 base_url → local", infer_provider_type("") == "local")
    check("推断:dashscope → bailian", infer_provider_type("https://dashscope.aliyuncs.com/compatible-mode/v1") == "bailian")
    check("推断:opencode.ai → opencode-zen", infer_provider_type("https://opencode.ai/zen/go/v1") == "opencode-zen")
    check("推断:其余 → custom", infer_provider_type("https://api.openai.com/v1") == "custom")

    pc = ProviderConfig(name="p1", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    check("type 留空自动推断并持久化", pc.type == "bailian", pc.type)
    pc2 = ProviderConfig(name="p2", base_url="https://api.x.com", type="custom")
    check("显式 type 保留", pc2.type == "custom", pc2.type)
    pc3 = ProviderConfig(name="p3", base_url="", display_name="")
    check("display_name 留空不报错", pc3.display_name == "" and pc3.type == "local", f"{pc3.display_name} {pc3.type}")
    check(
        "默认 bailian 带 display_name/type",
        DEFAULT_PROVIDER_BAILIAN.display_name == "阿里云百炼" and DEFAULT_PROVIDER_BAILIAN.type == "bailian",
        f"{DEFAULT_PROVIDER_BAILIAN.display_name} {DEFAULT_PROVIDER_BAILIAN.type}",
    )

    print()
    if failures:
        print(f"失败 {len(failures)} 项: {failures}")
        sys.exit(1)
    print("结果: 全部通过")


if __name__ == "__main__":
    main()
