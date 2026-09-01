"""B13 开机自启与启动参数验收（离线，不触碰真实 HKCU Run 键）。

对应 docs/DESKTOP-ACCEPTANCE.md §4 B13 Gate：
  自动：切换开关后注册表值正确增删（脚本读取校验）

用例（依赖 desktop.autostart，B13 实现后自动激活；缺失则整脚本 SKIP）：
  1. set_enabled(True) 写入后 is_enabled() 为 True，且值为 build_command()
  2. set_enabled(False) 删除后 is_enabled() 为 False
  3. 未启用时 set_enabled(False) 幂等返回 ok
  4. build_command 尾部带 --autostart 标记（自启场景识别）
  5. 注入临时 HKCU 子键验证真实 Run 键不被污染

安全：通过 autostart.set_registry() 把注册表访问工厂指向 HKCU 下的一个临时
子键（如 Software\\CNA_Test_Autostart），用例结束后删除该临时键——**绝不触碰
真实 HKCU\\...\\CurrentVersion\\Run**。若 winreg 不可用（非 Windows）则 SKIP。

用法：python test_desktop_autostart.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tools._desktop_testkit import Suite  # noqa: E402

suite = Suite(batch="B13", module="desktop.autostart", title="开机自启开关与注册表增删")


def _tmp_registry_factory(subkey: str):
    """构造指向临时 HKCU 子键的打开工厂（读/写两个 mode）。"""
    import winreg

    def factory(mode: str):
        if mode == "read":
            return winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ
            )
        return winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            subkey,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        )

    return factory


@suite.case("1. set_enabled(True) 写入后 is_enabled() 为 True 且值为 build_command()")
def _(mod):
    import winreg

    set_enabled = suite.require(mod, "set_enabled")
    is_enabled = suite.require(mod, "is_enabled")
    build_command = suite.require(mod, "build_command")
    set_registry = suite.require(mod, "set_registry")

    subkey = r"Software\CNA_Test_Autostart"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass
    set_registry(_tmp_registry_factory(subkey))

    cmd = build_command("C:/apps/CampusNoticeAssistant.exe")
    ok, detail = set_enabled(True, exe_path="C:/apps/CampusNoticeAssistant.exe")
    assert ok, f"写入自启失败: {detail}"
    assert is_enabled(), "写入后 is_enabled() 应为 True"

    # 校验写入的值就是 build_command 的产物（含 --autostart 标记）
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as k:
        value, vtype = winreg.QueryValueEx(k, mod.VALUE_NAME)
    assert value == cmd, f"注册表值不匹配:\n 期望 {cmd}\n 实际 {value}"
    assert vtype == winreg.REG_SZ, f"值类型应为 REG_SZ，实际 {vtype}"
    assert "--autostart" in value, "注册表值应带 --autostart 自启标记"
    assert "C:/apps/CampusNoticeAssistant.exe" in value, "注册表值应含可执行路径"

    # 清理临时键
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)


@suite.case("2. set_enabled(False) 删除后 is_enabled() 为 False")
def _(mod):
    import winreg

    set_enabled = suite.require(mod, "set_enabled")
    is_enabled = suite.require(mod, "is_enabled")
    set_registry = suite.require(mod, "set_registry")

    subkey = r"Software\CNA_Test_Autostart"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass
    set_registry(_tmp_registry_factory(subkey))

    # 先写入再删除
    set_enabled(True, exe_path="C:/apps/X.exe")
    assert is_enabled(), "前置：应处于启用态"
    ok, _ = set_enabled(False)
    assert ok, "删除自启失败"
    assert not is_enabled(), "删除后 is_enabled() 应为 False"

    # 清理
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass


@suite.case("3. 未启用时 set_enabled(False) 幂等返回 ok")
def _(mod):
    import winreg

    set_enabled = suite.require(mod, "set_enabled")
    set_registry = suite.require(mod, "set_registry")

    subkey = r"Software\CNA_Test_Autostart"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass
    set_registry(_tmp_registry_factory(subkey))

    # 从未启用：删除应视为成功（幂等），不抛异常
    ok, detail = set_enabled(False)
    assert ok, f"未启用时删除应返回 ok，实际: {detail}"

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass


@suite.case("4. build_command 尾部带 --autostart 标记")
def _(mod):
    build_command = suite.require(mod, "build_command")
    cmd = build_command("D:/a/b/app.exe")
    assert cmd.endswith("--autostart"), f"命令应以 --autostart 结尾：{cmd}"
    assert cmd.startswith('"D:/a/b/app.exe"'), f"命令应引号包裹 exe 路径：{cmd}"


@suite.case("5. 临时子键注入隔离真实 HKCU Run 键")
def _(mod):
    import winreg

    set_registry = suite.require(mod, "set_registry")
    set_enabled = suite.require(mod, "set_enabled")
    is_enabled = suite.require(mod, "is_enabled")

    subkey = r"Software\CNA_Test_Autostart"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass
    set_registry(_tmp_registry_factory(subkey))

    set_enabled(True, exe_path="C:/apps/Isolated.exe")
    # 真实 Run 键不应因此出现该值（隔离性）
    try:
        real = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        with real:
            try:
                winreg.QueryValueEx(real, mod.VALUE_NAME)
                polluted = True
            except FileNotFoundError:
                polluted = False
        assert not polluted, "临时键注入不应污染真实 HKCU Run 键"
    except FileNotFoundError:
        pass  # 真实 Run 键不存在，天然隔离

    # 清理临时键
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    sys.exit(suite.run())
