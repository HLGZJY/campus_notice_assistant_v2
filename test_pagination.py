"""调查 scuec 列表页的翻页机制和 DOM 结构。"""
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

list_url = "https://www.scuec.edu.cn/cxcy/scss/jstz.htm"
resp = requests.get(list_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
resp.encoding = resp.apparent_encoding
soup = BeautifulSoup(resp.text, "html.parser")

# 1. 找翻页相关元素
print("=" * 60)
print("[1] 查找翻页元素")
print("=" * 60)

# 找分页 div/nav
for tag in soup.find_all(["div", "nav", "span", "a"], class_=True):
    cls = " ".join(tag.get("class", []))
    if any(kw in cls.lower() for kw in ["page", "pag", "next", "fy", "pb", "nav"]):
        print(f"  <{tag.name} class='{cls}'>: {tag.get_text(strip=True)[:80]}")
        # 找里面的链接
        for a in tag.find_all("a", href=True):
            print(f"    -> {a.get_text(strip=True)}: {a['href']}")

# 2. 找所有含数字的链接（可能是页码）
print("\n" + "=" * 60)
print("[2] 查找含数字的链接（可能是页码）")
print("=" * 60)
for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)
    href = a["href"]
    if text in ["下一页", "上一页", "尾页", "首页", "Next", "Last"] or text.isdigit():
        print(f"  {text}: {urljoin(list_url, href)}")

# 3. 分析通知链接的 DOM 结构和 xpath
print("\n" + "=" * 60)
print("[3] 分析通知链接的 DOM 结构")
print("=" * 60)

notice_links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "info/1009/" in href:
        notice_links.append(a)

print(f"  当前页通知链接数: {len(notice_links)}")

# 看第一个通知链接的 DOM 路径
if notice_links:
    a = notice_links[0]
    print(f"\n  第一个通知链接的 DOM 路径:")
    path = []
    node = a
    while node and node.name != "[document]":
        siblings = node.find_previous_siblings(node.name) if hasattr(node, 'find_previous_siblings') else []
        idx = len(list(node.find_previous_siblings(node.name))) + 1
        path.insert(0, f"{node.name}[{idx}]")
        node = node.parent
    print(f"    {' > '.join(path)}")
    
    # 看父元素结构
    parent = a.parent
    print(f"\n  父元素: <{parent.name} class='{parent.get('class', '')}'>")
    print(f"  父元素的父元素: <{parent.parent.name} class='{parent.parent.get('class', '')}'>")
    
    # 看通知链接的共同特征
    print(f"\n  所有通知链接的共同父元素:")
    parents = set()
    for a in notice_links:
        p = a.parent
        parents.add((p.name, tuple(p.get("class", []))))
    print(f"    {parents}")

# 4. 看是否有 JS 动态加载的迹象
print("\n" + "=" * 60)
print("[4] 检查是否有 JS 动态加载")
print("=" * 60)
scripts = soup.find_all("script")
print(f"  script 标签数: {len(scripts)}")
for s in scripts:
    text = s.get_text()
    if any(kw in text for kw in ["ajax", "fetch", "page", "list", "json"]):
        print(f"  可能相关的 script: {text[:200]}")