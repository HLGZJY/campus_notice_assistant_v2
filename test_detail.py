"""测试 newspaper4k 对 scuec 通知详情页的提取效果。"""
import newspaper

# 用手动发现的真实通知 URL 测试
test_urls = [
    "https://www.scuec.edu.cn/cxcy/info/1009/2953.htm",  # 数学建模
    "https://www.scuec.edu.cn/cxcy/info/1009/2946.htm",  # 工程实践
    "https://www.scuec.edu.cn/cxcy/info/1009/2919.htm",  # iCAN
]

for url in test_urls:
    print("=" * 60)
    print(f"测试详情页: {url}")
    print("=" * 60)
    try:
        article = newspaper.article(url, language="zh")
        print(f"  标题: {article.title}")
        print(f"  发布日期: {article.publish_date}")
        print(f"  作者: {article.authors}")
        print(f"  正文长度: {len(article.text)} 字")
        print(f"  正文前300字:\n  {article.text[:300]}")
        print(f"  ---")
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")
    print()