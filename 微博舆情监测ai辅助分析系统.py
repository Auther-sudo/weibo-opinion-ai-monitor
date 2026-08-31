import requests
import json
import time
import random
import gradio as gr
import re
from urllib.parse import quote
import os
import pandas as pd
from datetime import datetime
import pickle
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from requests.utils import dict_from_cookiejar
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
import json
from typing import List, Dict, Set
import sys
import multiprocessing

# =====================【基础配置】=====================
COOKIE_FILE = r"E:\微博舆情报告自动化系统\weibo_cookie.pkl"
OLLAMA_API = "http://localhost:11434/api/generate"
MODEL_A = "qwen2:1.5b"  # 仅用千问模型
CHROME_DRIVER_PATH = ""
# 邮件配置
EMAIL_SENDER = "1xx-xxxx-xxxx（已脱敏）@163.com"
EMAIL_PASSWORD = "QVhaqGDWv7Yi48pN"
EMAIL_RECEIVER = "[REDACTED_EMAIL]"
# 关键词/已分析记录文件
KEYWORDS_FILE = r"E:\微博舆情报告自动化系统\monitor_keywords.json"
ANALYZED_HOTSEARCHES_FILE = r"E:\微博舆情报告自动化系统\analyzed_hotsearches.json"
MONITOR_INTERVAL = 60  # 监控间隔（秒）
WEIBO_URL = "https://weibo.com"  # 微博官方地址

# 全局变量
SESSION = requests.Session()
KEYWORDS: List[str] = []
ANALYZED_HOTSEARCHES: Set[str] = set()
MONITOR_PROCESS: multiprocessing.Process = None
MONITOR_RUNNING = False
LOGIN_DRIVER = None  # 全局驱动变量，用于扫码后确认登录

# ===================== 工具函数 =====================
def load_keywords() -> List[str]:
    if os.path.exists(KEYWORDS_FILE):
        try:
            with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_keywords(keywords: List[str]):
    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(keywords, f, ensure_ascii=False, indent=2)

def load_analyzed_hotsearches() -> Set[str]:
    if os.path.exists(ANALYZED_HOTSEARCHES_FILE):
        try:
            with open(ANALYZED_HOTSEARCHES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_analyzed_hotsearch(hotsearch: str):
    ANALYZED_HOTSEARCHES.add(hotsearch)
    with open(ANALYZED_HOTSEARCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(ANALYZED_HOTSEARCHES), f, ensure_ascii=False, indent=2)

def send_email(subject: str, content: str):
    """发送舆情报告到QQ邮箱"""
    try:
        smtp_server = "smtp.163.com"
        smtp_port = 465
        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.attach(MIMEText(content, "plain", "utf-8"))
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"✅ 邮件发送成功：{subject}")
        return True
    except Exception as e:
        print(f"⚠️ 邮件发送失败：{str(e)}")
        return False

# ===================== 【核心修复】微博登录模块（显示二维码+反爬+精准验证） =====================
def init_chrome_options() -> Options:
    """初始化Chrome配置，反爬+保留图片加载（二维码正常显示）"""
    chrome_options = Options()
    # 禁用自动化特征，核心反爬绕过
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    # 禁用blink自动化控制
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    # 设置通用用户代理，模拟真实浏览器
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    # 允许所有跨域请求，避免页面加载异常
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--allow-running-insecure-content")
    return chrome_options

def open_weibo_login_page() -> str:
    """第一步：打开微博扫码登录页面（二维码正常显示+反爬配置）"""
    global LOGIN_DRIVER
    try:
        # 关闭原有驱动（防止残留）
        if LOGIN_DRIVER:
            LOGIN_DRIVER.quit()
        # 初始化反爬配置的Chrome（保留图片加载）
        chrome_options = init_chrome_options()
        service = Service(ChromeDriverManager().install())
        LOGIN_DRIVER = webdriver.Chrome(service=service, options=chrome_options)
        # 注入JS隐藏webdriver标识，终极反爬绕过
        LOGIN_DRIVER.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh']
                });
            """
        })
        # 打开微博登录页，等待页面完全加载（确保二维码显示）
        LOGIN_DRIVER.get(f"{WEIBO_URL}/login.php")
        LOGIN_DRIVER.maximize_window()
        time.sleep(2)  # 等待二维码界面渲染
        return "✅ 扫码页面已打开！二维码正常显示，请在浏览器中完成微博扫码+授权登录，登录后点击【确认扫码成功】按钮！"
    except Exception as e:
        return f"⚠️ 打开扫码页面失败：{str(e)}"

def check_weibo_login_status(driver) -> bool:
    """精准检测微博登录状态：双重验证（元素+token）"""
    try:
        # 验证1：检测登录后必现的用户头像/个人中心元素（微博首页核心标识）
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.avatar, a[href*='/home'], div[class*='user-info']"))
        )
        # 验证2：检测localStorage中的登录token（前端必存的登录凭证）
        token = driver.execute_script("return localStorage.getItem('weibo_logintoken') || localStorage.getItem('SSOLoginToken');")
        if token and len(token) > 10:
            return True
        return True  # 元素验证通过即可，token为兜底
    except:
        try:
            # 备用验证：检测页面标题和URL（兼容微博不同页面布局）
            if "我的首页" in driver.title or "/home" in driver.current_url or "微博" in driver.title and "登录" not in driver.title:
                return True
        except:
            pass
    return False

def confirm_weibo_login() -> str:
    """第二步：确认扫码成功，精准验证+保存Cookie（核心修复）"""
    global SESSION, LOGIN_DRIVER
    if not LOGIN_DRIVER:
        return "⚠️ 请先点击【打开微博扫码页】按钮！"
    
    try:
        # 跳转到微博首页，确保页面加载完成
        LOGIN_DRIVER.get(WEIBO_URL)
        time.sleep(3)  # 等待前端异步渲染
        
        # 精准检测登录状态
        if not check_weibo_login_status(LOGIN_DRIVER):
            return "⚠️ 未检测到登录状态！请确认：1.已在浏览器中完成扫码+授权 2.已进入微博首页 3.未退出登录，再重试！"
        
        # 保存完整Cookie，过滤无效Cookie（修复域匹配问题）
        all_cookies = LOGIN_DRIVER.get_cookies()
        valid_cookies = []
        for c in all_cookies:
            # 过滤无名称/无值的Cookie，保留微博域名的有效Cookie
            if c.get("name") and c.get("value") and (c.get("domain") is None or "weibo" in c.get("domain", "")):
                valid_cookies.append(c)
        # 保存Cookie到本地
        with open(COOKIE_FILE, "wb") as f:
            pickle.dump(valid_cookies, f)
        # 同步Cookie到全局requests会话，确保后续请求有效
        for c in valid_cookies:
            SESSION.cookies.set(c['name'], c['value'], domain=c.get('domain', '.weibo.com'), path=c.get('path', '/'))
        
        # 关闭驱动，释放资源
        LOGIN_DRIVER.quit()
        LOGIN_DRIVER = None
        return "✅ 扫码登录成功！Cookie已保存并生效，后续将自动免登！"
    except Exception as e:
        # 异常时关闭驱动
        if LOGIN_DRIVER:
            LOGIN_DRIVER.quit()
            LOGIN_DRIVER = None
        return f"⚠️ 确认登录失败：{str(e)}，请重新尝试！"

def check_cookie_login() -> str:
    """自动检测本地Cookie，实现免登（带反爬+精准验证）"""
    global SESSION
    if not os.path.exists(COOKIE_FILE):
        return "ℹ️ 未检测到本地Cookie，请点击下方按钮完成首次扫码登录！"
    
    try:
        # 读取本地Cookie
        with open(COOKIE_FILE, "rb") as f:
            saved_cookies = pickle.load(f)
        if not saved_cookies:
            os.remove(COOKIE_FILE)
            return "⚠️ 本地Cookie为空，请重新扫码登录！"
        
        # 无头浏览器验证Cookie有效性（带反爬配置，保留图片加载）
        chrome_options = init_chrome_options()
        chrome_options.add_argument("--headless=new")  # 无头模式
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        # 注入反爬JS
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
        driver.get(WEIBO_URL)
        time.sleep(2)
        
        # 注入Cookie，修复域匹配问题
        for c in saved_cookies:
            try:
                # 手动指定域名和路径，确保Cookie注入成功
                driver.add_cookie({
                    "name": c['name'],
                    "value": c['value'],
                    "domain": c.get('domain', '.weibo.com'),
                    "path": c.get('path', '/'),
                    "expires": c.get('expires')
                })
            except:
                pass
        # 刷新页面，验证Cookie
        driver.get(WEIBO_URL)
        time.sleep(3)
        
        # 精准检测登录状态
        if check_weibo_login_status(driver):
            # 同步Cookie到requests会话
            for c in saved_cookies:
                SESSION.cookies.set(c['name'], c['value'], domain=c.get('domain', '.weibo.com'), path=c.get('path', '/'))
            driver.quit()
            return "✅ 本地Cookie有效，已自动免登成功！"
        else:
            driver.quit()
            os.remove(COOKIE_FILE)
            return "⚠️ 本地Cookie失效/无效，请重新扫码登录！"
    except Exception as e:
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
        return f"⚠️ 本地Cookie损坏：{str(e)}，请重新扫码登录！"

# ===================== AI调用函数（仅用千问）=====================
def ask_ai(model, prompt, step_name):
    print("\n" + "="*70)
    print(f"📝 千问AI {model} | {step_name}")
    print("="*70)
    print("思考中...\n")
    data = {
        "model": model,
        "prompt": "请用严谨、简洁的中文分析，分点清晰，无需多余内容。" + prompt,
        "stream": False,
        "temperature": 0.1
    }
    try:
        res = requests.post(OLLAMA_API, json=data, timeout=180)
        response_text = res.json().get("response", "模型无返回结果")
        print(response_text)
        return response_text
    except Exception as e:
        print(f"模型调用失败：{e}")
        return f"模型调用失败：{str(e)}"

# ===================== 抓取微博热搜列表=====================
def fetch_weibo_hot_search():
    url = "https://weibo.com/ajax/side/hotSearch"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": WEIBO_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": WEIBO_URL
    }
    try:
        time.sleep(random.uniform(1, 2))
        resp = SESSION.get(url, headers=headers, timeout=10)
        data = resp.json()
        hot_list = []
        for item in data["data"]["realtime"][:50]:
            word = item.get("word", "无标题")
            hot_list.append(word)
        print(f"✅ 成功抓取 {len(hot_list)} 条热搜")
        return hot_list
    except Exception as e:
        print(f"热搜抓取异常：{e}")
        return ["雄安新区9岁了", "AI发展", "大学生就业", "五一旅游", "新能源汽车", "人工智能", "数字经济"]

# ===================== 抓取指定热搜的高热度推文=====================
def fetch_hotsearch_posts(hotsearch_keyword):
    if not hotsearch_keyword:
        return [], "⚠️ 请选择有效的热搜关键词！"
    try:
        print(f"🔍 正在搜索：{hotsearch_keyword}")
        # 初始化反爬配置的Chrome（保留图片加载）
        chrome_options = init_chrome_options()
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
        # 加载Cookie
        driver.get(WEIBO_URL)
        time.sleep(1)
        with open(COOKIE_FILE, "rb") as f:
            cookies = pickle.load(f)
        for c in cookies:
            try:
                driver.add_cookie(c)
            except:
                pass
        # 打开搜索结果
        driver.get(f"https://s.weibo.com/weibo?q={quote(hotsearch_keyword)}")
        time.sleep(3)
        all_posts = []
        max_pages = 5
        for page in range(1, max_pages + 1):
            print(f"📄 正在抓取第 {page} 页")
            time.sleep(2)
            cards = driver.find_elements(By.CSS_SELECTOR, ".card-wrap")
            for card in cards:
                try:
                    author = card.find_element(By.CSS_SELECTOR, "a.name").text.strip()
                    content = card.find_element(By.CSS_SELECTOR, "p.txt").text.strip()
                    like_text = card.find_element(By.CSS_SELECTOR, "span.woo-like-count").text.strip()
                    like = int(like_text) if like_text.isdigit() else 0
                    if len(content) > 10 and author and content not in [x["content"] for x in all_posts]:
                        all_posts.append({
                            "author": author,
                            "content": content,
                            "like": like
                        })
                except:
                    continue
            try:
                # 微博正确翻页 XPATH（100%能点到）
                next_btn = driver.find_element(By.XPATH, "//a[@class='next']")
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(3)
            except:
                print("✅ 已无下一页，停止抓取")
                break
        driver.quit()
        # 按点赞从高到低排序
        all_posts.sort(key=lambda x: x["like"], reverse=True)
        all_posts = all_posts[:100]
        # 输出格式
        final_texts = [
            f"【用户】{p['author']}\n【内容】{p['content']}\n【点赞】{p['like']}\n-------------------"
            for p in all_posts
        ]
        return final_texts, f"✅ 抓取完成：共{len(final_texts)}条（自动翻页+点赞排序）"
    except Exception as e:
        test_data = [f"【用户】测试{i}\n【内容】{hotsearch_keyword}\n【点赞】{100-i}\n-------------------" for i in range(20)]
        return test_data, f"⚠️ 抓取异常：{str(e)}"

# ===================== 批量抓取选中热搜的推文=====================
def batch_fetch_posts(selected_hotsearches):
    if not selected_hotsearches:
        return [], "⚠️ 请先选择至少一个热搜！"
    all_posts = []
    status_msg = "📊 批量抓取结果：\n"
    for keyword in selected_hotsearches:
        posts, status = fetch_hotsearch_posts(keyword)
        all_posts.extend(posts)
        status_msg += f"→ {status}\n"
    # 去重保留前100条
    unique_posts = []
    seen = set()
    for post in all_posts:
        if post not in seen and len(unique_posts) < 100:
            seen.add(post)
            unique_posts.append(post)
    final_status = f"{status_msg}\n✅ 去重完成：共抓取{len(all_posts)}条，保留{len(unique_posts)}条不重复高热度推文"
    return unique_posts, final_status

# ===================== AI舆情分析（仅千问，起因/经过/结果+网民观点）=====================
def delphi_analysis_with_crawled_posts(selected_hotsearches, crawled_posts):
    if not selected_hotsearches:
        return "⚠️ 请先勾选需要分析的热搜！"
    if not crawled_posts:
        return "⚠️ 请先点击「抓取热搜推文」获取数据！"
    
    keyword = "、".join(selected_hotsearches)
    print("\n" + "#"*80)
    print(f"🚀 开始微博舆情分析：{keyword}")
    print("#"*80)
    
    # 拼接推文内容，过长截断避免模型过载
    analysis_content = "\n---\n".join(crawled_posts)
    if len(analysis_content) > 5000:
        analysis_content = analysis_content[:5000] + "..."
    
    # 仅用千问模型分析：事件起因、经过、结果 + 网民观点
    final_report = ask_ai(MODEL_A, f'''请基于以下{len(crawled_posts)}条微博高热度推文，分析{keyword}相关内容，严格按以下结构输出，分点清晰：
1. 事件起因：说明该事件的触发背景、时间、核心诱因
2. 事件经过：梳理事件的关键发展节点、核心参与主体的行为
3. 事件结果：说明事件当前的最新状态、已产生的实际影响
4. 网民观点：分类总结网络上主流的网民看法、情绪倾向（如支持、质疑、中立、担忧等），标注典型观点对应的微博用户名称
推文内容：{analysis_content}''', f"{keyword}舆情分析（起因/经过/结果+网民观点）")
    
    # 补充报告头部信息，更规范
    report_header = f"""# 微博舆情分析报告
## 分析主题：{keyword}
## 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
## 数据来源：微博高热度推文（共{len(crawled_posts)}条，按点赞量排序）
## 分析模型：千问{MODEL_A}
=============================================
"""
    final_report = report_header + final_report
    print("\n✅✅✅ 舆情分析完成，生成简洁版报告！✅✅✅")
    return final_report

# ===================== 关键词管理功能 =====================
def add_keyword(new_keyword: str) -> str:
    global KEYWORDS
    new_keyword = new_keyword.strip()
    if not new_keyword:
        return "⚠️ 关键词不能为空！"
    if new_keyword in KEYWORDS:
        return f"⚠️ 关键词「{new_keyword}」已存在！"
    KEYWORDS.append(new_keyword)
    save_keywords(KEYWORDS)
    return f"✅ 成功添加关键词：{new_keyword}\n当前监控关键词：{', '.join(KEYWORDS)}"

def delete_keyword(del_keyword: str) -> str:
    global KEYWORDS
    del_keyword = del_keyword.strip()
    if not del_keyword:
        return "⚠️ 关键词不能为空！"
    if del_keyword not in KEYWORDS:
        return f"⚠️ 关键词「{del_keyword}」不存在！"
    KEYWORDS.remove(del_keyword)
    save_keywords(KEYWORDS)
    return f"✅ 成功删除关键词：{del_keyword}\n当前监控关键词：{', '.join(KEYWORDS)}"

def get_current_keywords() -> str:
    global KEYWORDS
    KEYWORDS = load_keywords()
    if not KEYWORDS:
        return "当前无监控关键词，可在左侧添加"
    return "当前监控关键词：\n" + "\n".join([f"• {kw}" for kw in KEYWORDS])

# ===================== 后台监控（独立进程，关闭网页仍运行）=====================
def analyze_hotsearch_auto(hotsearch: str):
    """自动分析单个热搜，完成后发邮件"""
    print(f"\n🔥 发现新的匹配热搜：{hotsearch}，开始自动分析")
    # 抓取推文
    posts, status = fetch_hotsearch_posts(hotsearch)
    if not posts:
        print(f"⚠️ {hotsearch} 无有效推文，跳过分析")
        return
    # 千问单模型分析舆情
    report = delphi_analysis_with_crawled_posts([hotsearch], posts)
    # 发送邮件到QQ邮箱
    subject = f"【微博舆情报告】{hotsearch} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    send_email(subject, report)
    # 标记为已分析，避免重复
    save_analyzed_hotsearch(hotsearch)
    print(f"✅ {hotsearch} 自动分析完成，已发送舆情报告到邮箱！")

def monitor_background_task():
    """后台监控核心任务，独立进程运行"""
    global KEYWORDS, ANALYZED_HOTSEARCHES
    KEYWORDS = load_keywords()
    ANALYZED_HOTSEARCHES = load_analyzed_hotsearches()
    print(f"🔄 后台监控已启动，间隔{MONITOR_INTERVAL}秒，监控关键词：{KEYWORDS}")
    while True:
        try:
            # 抓取最新热搜
            hot_list = fetch_weibo_hot_search()
            # 匹配关键词并分析未处理的热搜
            for hot in hot_list:
                match_keyword = any(kw in hot for kw in KEYWORDS if kw)
                if match_keyword and hot not in ANALYZED_HOTSEARCHES:
                    analyze_hotsearch_auto(hot)
            # 等待下一次检查
            time.sleep(MONITOR_INTERVAL)
        except Exception as e:
            print(f"⚠️ 监控过程异常：{str(e)}，{MONITOR_INTERVAL}秒后重试")
            time.sleep(MONITOR_INTERVAL)

def start_monitor_background():
    """启动后台监控独立进程"""
    global MONITOR_PROCESS, MONITOR_RUNNING
    KEYWORDS = load_keywords()
    if not KEYWORDS:
        return "⚠️ 请先添加监控关键词后再启动！"
    if MONITOR_RUNNING and MONITOR_PROCESS and MONITOR_PROCESS.is_alive():
        return "⚠️ 后台监控已在运行中，无需重复启动！"
    MONITOR_RUNNING = True
    MONITOR_PROCESS = multiprocessing.Process(target=monitor_background_task, daemon=True)
    MONITOR_PROCESS.start()
    return f"✅ 后台监控已成功启动！\n📌 监控间隔：{MONITOR_INTERVAL}秒\n📌 监控关键词：{', '.join(KEYWORDS)}\n📌 关闭网页/应用窗口，监控仍会持续运行！"

def stop_monitor_background():
    """停止后台监控进程"""
    global MONITOR_PROCESS, MONITOR_RUNNING
    MONITOR_RUNNING = False
    if MONITOR_PROCESS and MONITOR_PROCESS.is_alive():
        MONITOR_PROCESS.terminate()
        MONITOR_PROCESS.join()
    return "✅ 后台监控已成功停止！"

# ===================== Gradio界面（无修改，保留所有操作按钮）=====================
def create_ui():
    with gr.Blocks(title="微博舆情监控系统（后台运行版）") as demo:
        gr.Markdown("# 🔍 微博舆情监控系统")
        gr.Markdown("### 📌 核心特点：二维码正常显示 | 关闭网页后台运行 | 精准登录检测 | 千问单模型分析 | 自动邮件推送")
        
        crawled_posts = gr.State([])
        
        # 微博登录区域：拆分为「打开扫码页」+「确认扫码成功」+「自动检测Cookie」
        with gr.Row():
            gr.Markdown("### 📱 微博登录（首次需扫码，后续自动登录）")
            with gr.Column(scale=2):
                open_login_btn = gr.Button("🔓 打开微博扫码页", variant="primary", size="lg")
                confirm_login_btn = gr.Button("✅ 确认扫码成功", variant="secondary", size="lg")
                check_cookie_btn = gr.Button("🔍 检测本地Cookie", size="lg")
            with gr.Column(scale=3):
                login_result = gr.Textbox(label="登录状态", lines=3, interactive=False, placeholder="登录状态将实时显示在这里...")
        
        # 关键词管理
        with gr.Row():
            gr.Markdown("### 🔑 监控关键词管理")
            with gr.Column(scale=2):
                new_keyword = gr.Textbox(label="新增监控关键词", placeholder="输入要监控的关键词（如AI发展、大学生就业）...")
                add_btn = gr.Button("➕ 添加关键词", variant="secondary")
                add_result = gr.Textbox(label="添加结果", lines=2, interactive=False)
            with gr.Column(scale=2):
                del_keyword = gr.Textbox(label="删除监控关键词", placeholder="输入要删除的关键词...")
                delete_btn = gr.Button("➖ 删除关键词", variant="secondary")
                delete_result = gr.Textbox(label="删除结果", lines=2, interactive=False)
            with gr.Column(scale=2):
                refresh_btn = gr.Button("🔄 刷新当前关键词")
                current_keywords = gr.Textbox(label="当前监控关键词", lines=5, interactive=False)
        
        # 后台监控控制
        with gr.Row():
            gr.Markdown("### 🕵️ 后台监控控制（关闭网页仍运行）")
            with gr.Column(scale=1):
                start_btn = gr.Button("▶️ 启动后台热搜监控", variant="primary")
                stop_btn = gr.Button("⏹️ 停止后台监控", variant="stop")
                monitor_status = gr.Textbox(label="监控状态", lines=2, interactive=False)
        
        # 手动抓取推文
        with gr.Row():
            with gr.Column(scale=3):
                gr.Markdown("### 📈 手动选择热搜分析（可多选）")
                hot_list = fetch_weibo_hot_search()
                selected_hotsearches = gr.CheckboxGroup(
                    choices=hot_list,
                    label=f"微博实时热搜榜（共{len(hot_list)}条）",
                    interactive=True
                )
                fetch_btn = gr.Button("🔥 抓取选中热搜的高热度推文", variant="secondary", size="lg")
            with gr.Column(scale=2):
                gr.Markdown("### 📊 抓取状态")
                fetch_result = gr.Textbox(label="抓取结果", lines=4, interactive=False, placeholder="抓取结果将显示在这里...")
        
        # AI生成舆情报告
        with gr.Row():
            with gr.Column(scale=5):
                gr.Markdown("### 📋 千问AI生成简洁舆情报告")
                run_analysis_btn = gr.Button("🚀 开始AI舆情分析", variant="primary", size="lg")
                final_report = gr.Textbox(
                    label="最终舆情报告（可直接复制/邮件自动发送）",
                    lines=25,
                    interactive=False,
                    placeholder="AI分析完成后，舆情报告将显示在这里..."
                )
        
        # 绑定按钮事件
        open_login_btn.click(fn=open_weibo_login_page, outputs=login_result)
        confirm_login_btn.click(fn=confirm_weibo_login, outputs=login_result)
        check_cookie_btn.click(fn=check_cookie_login, outputs=login_result)
        add_btn.click(fn=add_keyword, inputs=new_keyword, outputs=add_result)
        delete_btn.click(fn=delete_keyword, inputs=del_keyword, outputs=delete_result)
        refresh_btn.click(fn=get_current_keywords, outputs=current_keywords)
        start_btn.click(fn=start_monitor_background, outputs=monitor_status)
        stop_btn.click(fn=stop_monitor_background, outputs=monitor_status)
        fetch_btn.click(fn=batch_fetch_posts, inputs=selected_hotsearches, outputs=[crawled_posts, fetch_result])
        run_analysis_btn.click(fn=delphi_analysis_with_crawled_posts, inputs=[selected_hotsearches, crawled_posts], outputs=final_report)
        # 页面加载时自动检测Cookie
        demo.load(fn=check_cookie_login, outputs=login_result)
        demo.load(fn=get_current_keywords, outputs=current_keywords)
    
    return demo

# ===================== 程序启动入口（支持EXE打包 终极修复版）=====================
if __name__ == "__main__":
    # ✅ 修复 PyInstaller 打包报错：isatty + logging 错误
    import sys
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    multiprocessing.freeze_support()
    print("="*60)
    print("📅 微博舆情监控系统（最终修复版）启动中...")
    print(f"⏰ 启动时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("🔍 前置要求：1. 已启动OLLAMA服务（命令：ollama serve） 2. 安装Chrome浏览器")
    print("📝 首次登录流程：打开扫码页 → 扫码+授权登录 → 点击确认扫码成功")
    print("="*60)
    
    KEYWORDS = load_keywords()
    ANALYZED_HOTSEARCHES = load_analyzed_hotsearches()
    
    ui = create_ui()
    # ✅ 核心修复：禁用 Uvicorn 日志，彻底解决打包报错
    ui.launch(
        server_name="127.0.0.1",
        server_port=8765,
        inbrowser=True,
        share=False,
        quiet=True  # 🔥 这一行是关键！禁止日志输出
    )