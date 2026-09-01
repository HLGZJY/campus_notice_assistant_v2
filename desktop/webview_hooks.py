"""webview 新窗口事件拦截（B10.T2，R14 兜底）。

桌面壳侧的外链「兜底」层，与前端 B10.T1 的主动拦截构成双保险：

1. **js_api 桥**：向 webview 注入 ``ExternalLinkBridge``，前端通过
   ``window.pywebview.api.open_external(url)`` 调用，走系统默认浏览器打开，
   绝不在 webview 内开新窗口/新标签。
2. **注入脚本**：页面加载后注入 ``EXTERNAL_LINK_INJECT_JS``，全局覆写
   ``window.open`` 并挂 document 级 click 监听，拦截任何漏网的
   ``target=_blank`` / 外链点击（即使前端主动拦截失效也兜得住）。

与 B10.T1 的职责边界：
   - T1（前端主动）：统一入口 helper + 全局点击拦截器；
   - T2（壳兜底）：js_api 桥 + window.open 覆写 + 兜底点击监听。
"""
from __future__ import annotations

import logging
import webbrowser
from typing import Any

logger = logging.getLogger(__name__)


class ExternalLinkBridge:
    """暴露给前端的 js_api 桥：外部链接交给系统默认浏览器打开。

    被 pywebview 挂到 ``window.pywebview.api`` 上，前端 helper 优先调用
    ``open_external``（B10.T1 的 openExternal 已接）。Python 方法名即 JS
    侧键名（pywebview 不做 camelCase 转换）。
    """

    def open_external(self, url: str) -> None:
        """用系统默认浏览器打开外部链接（webbrowser.open 是异步非阻塞的）。"""
        if not url:
            return
        try:
            # 新窗口=True、后台=False：前台打开，与用户点外链预期一致
            opened = webbrowser.open(url, new=2)
            logger.info("外链交由系统浏览器打开: %s (opened=%s)", url, opened)
        except Exception:  # noqa: BLE001 - 打开失败不阻断前端
            logger.exception("外链打开失败: %s", url)


# 注入脚本：覆写 window.open + 兜底拦截 target=_blank / 外链点击。
# 即使前端主动层失效，这里也能把所有新窗口请求转交 pywebview api 桥。
EXTERNAL_LINK_INJECT_JS = r"""
(function () {
  if (window.__cnaExternalInjected) return;
  window.__cnaExternalInjected = true;

  // 统一把 url 交给壳桥（走系统浏览器）；无桥时回退原 window.open。
  function routeExternal(url) {
    try {
      if (window.pywebview && window.pywebview.api &&
          typeof window.pywebview.api.open_external === 'function') {
        window.pywebview.api.open_external(String(url));
        return;
      }
    } catch (e) { /* 忽略，走回退 */ }
    if (window.__cnaOrigOpen) window.__cnaOrigOpen(url, '_blank', 'noopener');
  }

  // 1) 覆写 window.open：任何 _blank 新窗口请求都转系统浏览器
  var origOpen = window.open;
  window.__cnaOrigOpen = origOpen;
  window.open = function (url) {
    routeExternal(url);
    return null; // 不在 webview 内创建新窗口
  };

  // 2) 兜底：document 级点击拦截 target=_blank / 外链 <a>
  document.addEventListener('click', function (e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var el = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!el) return;
    var isBlank = el.getAttribute('target') === '_blank';
    var href = el.getAttribute('href') || '';
    var isExternal = /^https?:\/\//i.test(href) && (function () {
      try { return new URL(href, location.href).origin !== location.origin; }
      catch (err) { return false; }
    })();
    if (!isBlank && !isExternal) return;
    e.preventDefault();
    e.stopPropagation();
    routeExternal(el.href);
  }, true);
})();
"""


def install_js_api() -> ExternalLinkBridge:
    """创建并返回 js_api 桥实例（供 create_window 的 js_api 参数使用）。"""
    return ExternalLinkBridge()


def inject_new_window_guard(window: Any) -> None:
    """在页面加载完成后注入外链兜底脚本（幂等）。

    应在窗口 ``loaded`` 事件触发时调用。window 为 pywebview Window 实例。
    """
    if window is None:
        return
    try:
        window.evaluate_js(EXTERNAL_LINK_INJECT_JS)
        logger.debug("外链兜底脚本已注入 webview")
    except Exception:  # noqa: BLE001 - 注入失败不影响主流程（前端主动层仍在）
        logger.debug("外链兜底脚本注入失败（由前端主动层兜住）", exc_info=True)
