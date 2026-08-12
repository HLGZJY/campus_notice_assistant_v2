"""调查教务处通知页面的翻页结构。"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

url = "https://www.scuec.edu.cn/jwc/tzgg.htm"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
resp.encoding = resp.apparent_encoding
soup = BeautifulSoup(resp.text, "html.parser")

# 找翻页
print("[1] 翻页元素:")
for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)
    if text.isdigit() or any(kw in text for kw in ["下一页", "下页", "尾页", "首页", "上页"]):
        print(f"  {text}: {urljoin(url, a['href'])}")

# 找通知链接模式
print("\n[2] 通知链接分析:")
links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    text = a.get_text(strip=True)
    if text and "info" in href:
        links.append((text[:40], urljoin(url, href)))
print(f"  当前页通知数: {len(links)}")
for t, u in links[:5]:
    print(f"  {t}: {u}")

# 看 script 里的翻页
print("\n[3] script 中的翻页线索:")
for s in soup.find_all("script"):
    text = s.get_text()
    if any(kw in text for kw in ["page", "Page", "totalPage", "currentPage", "_jsq_"]):
        print(f"  {text[:300]}")