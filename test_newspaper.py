"""测试 newspaper4k 对 scuec 创新创业学院通知列表页的提取效果。"""
import newspaper
from newspaper import Config

# 配置中文
config = Config()
config.language = "zh"
config.memoize_articles = False  # 测试时关闭去重，看全部
config.fetch_images = False  # 不抓图片，加快速度

list_url = "https://www.scuec.edu.cn/cxcy/scss/jstz.htm"

print("=" * 60)
print(f"测试列表页: {list_url}")
print("=" * 60)

# 方法1: 用 Source.build() 发现文章链接
print("\n[1] 用 Source.build() 发现文章链接...")
source = newspaper.build(list_url, config=config)
print(f"   发现文章数: {len(source.articles)}")
print(f"   article_urls() 示例（前5个）:")
for i, url in enumerate(source.article_urls()[:5]):
    print(f"     {i+1}. {url}")

# 方法2: 直接抓列表页，看能否解析出链接
print("\n[2] 直接下载列表页 HTML，检查内容...")
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

resp = requests.get(list_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
resp.encoding = resp.apparent_encoding  # 处理中文编码
print(f"   状态码: {resp.status_code}")
print(f"   编码: {resp.encoding}")
print(f"   HTML 长度: {len(resp.text)}")

soup = BeautifulSoup(resp.text, "html.parser")
# 找所有链接
all_links = soup.find_all("a", href=True)
print(f"   总链接数: {len(all_links)}")

# 看看有哪些看起来像通知的链接
notice_links = []
for a in all_links:
    href = a["href"]
    text = a.get_text(strip=True)
    if text and ("info" in href or "tzgg" in href or "jstz" in href):
        full_url = urljoin(list_url, href)
        notice_links.append((text[:40], full_url))

print(f"   疑似通知链接数: {len(notice_links)}")
print(f"   示例（前10个）:")
for i, (text, url) in enumerate(notice_links[:10]):
    print(f"     {i+1}. {text}")
    print(f"        {url}")

# 方法3: 如果 Source 发现了文章，试抓一篇详情页
if source.articles:
    print("\n[3] 试抓取第一篇详情页...")
    article = source.articles[0]
    try:
        article.download()
        article.parse()
        print(f"   URL: {article.url}")
        print(f"   标题: {article.title}")
        print(f"   发布日期: {article.publish_date}")
        print(f"   正文长度: {len(article.text)}")
        print(f"   正文前200字: {article.text[:200]}")
    except Exception as e:
        print(f"   ❌ 抓取失败: {type(e).__name__}: {e}")
elif notice_links:
    print("\n[3] Source 未发现文章，用 notice_links 手动抓一篇...")
    text, url = notice_links[0]
    try:
        article = newspaper.article(url, language="zh")
        print(f"   URL: {article.url}")
        print(f"   标题: {article.title}")
        print(f"   发布日期: {article.publish_date}")
        print(f"   正文长度: {len(article.text)}")
        print(f"   正文前200字: {article.text[:200]}")
    except Exception as e:
        print(f"   ❌ 抓取失败: {type(e).__name__}: {e}")
else:
    print("\n[3] 没有可测试的详情页链接")