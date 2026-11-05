import time
from qqbot import douyin
from qqbot.config import load_settings

settings = load_settings()
browser = douyin.Browser(settings)
page = browser.start(visible=False)
print("CDP 连上了")
page.goto(douyin.HOME, timeout=45)
time.sleep(6)
print("登录探测:", douyin.login_probe(page))
feed = douyin.read_feed(page, limit=5)
print("推荐条数:", len(feed))
for item in feed[:3]:
    print(" -", item["id"], item["text"][:50])