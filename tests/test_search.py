"""查一下：只读标题和摘要，查不到就说不知道。"""

import time

from qqbot import search

SO_PAGE = """<ul>
<li class="res-list"><h3><a href="https://www.so.com/link?m=1">月亮距离地球有多远_百度知道</a></h3>
<p class="res-desc">月球离地球平均有 384401 公里。</p></li>
<li class="res-list"><h3><a href="https://www.so.com/link?m=2">地球到月亮有多远?</a></h3></li>
</ul>"""

RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title>地球到月球有多远 - 百度百科</title>
<link>https://baike.baidu.com/item/x</link>
<description>地球与月球的平均距离约 38.4 万公里。</description></item>
<item><title>月球 - 维基百科</title>
<link>https://zh.wikipedia.org/wiki/y</link>
<description>月球是地球唯一的天然卫星。</description></item>
</channel></rss>""".encode("utf-8")


def test_strip_tag_removes_marker():
    text, query = search.strip_tag("等我一下，我查查～\n{{查:地球到月球多远}}")

    assert query == "地球到月球多远"
    assert "{{查" not in text
    assert "我查查" in text


def test_strip_tag_without_tag():
    text, query = search.strip_tag("在呀，怎么啦")
    assert query == ""
    assert text == "在呀，怎么啦"


def test_strip_tag_handles_fullwidth_colon():
    _, query = search.strip_tag("{{查：今天几号}}")
    assert query == "今天几号"


def test_parse_reads_items():
    rows = search._parse(RSS)

    assert [row["title"] for row in rows] == ["地球到月球有多远 - 百度百科", "月球 - 维基百科"]
    assert "38.4" in rows[0]["snippet"]


def test_parse_broken_xml_is_empty():
    assert search._parse(b"not xml") == []


def test_parse_360_reads_title_and_snippet():
    rows = search._parse_360(SO_PAGE)

    assert [row["title"] for row in rows] == ["月亮距离地球有多远_百度知道", "地球到月亮有多远?"]
    assert "384401" in rows[0]["snippet"]
    assert rows[1]["snippet"] == ""


def test_prefers_360_and_skips_bing(monkeypatch):
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        return SO_PAGE if "so.com" in url else RSS.decode("utf-8")

    monkeypatch.setattr(search, "_fetch", fake_fetch)
    search._cache.clear()

    rows = search.search("地球到月球有多远")

    assert rows[0]["title"].startswith("月亮距离地球")
    assert len(calls) == 1


def test_falls_back_to_bing(monkeypatch):
    calls = []

    def fake_fetch(url, timeout):
        calls.append(url)
        if "so.com" in url:
            raise OSError("blocked")
        return RSS.decode("utf-8")

    monkeypatch.setattr(search, "_fetch", fake_fetch)
    search._cache.clear()

    rows = search.search("地球到月球多远")

    assert rows[0]["title"].startswith("地球到月球")
    assert len(calls) == 2


def test_block_lists_results_without_links(monkeypatch):
    rows = search._parse(RSS)

    text = search.block(rows)

    assert "你刚查到的" in text
    assert "38.4" in text
    assert "baike.baidu.com" in text
    assert "http" not in text.split("）")[0]


def test_search_uses_cache(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            calls.append(1)
            return RSS

    monkeypatch.setattr(search.urllib.request, "urlopen", lambda *a, **k: Response())
    search._cache.clear()

    first = search.search("地球到月球多远")
    second = search.search("地球到月球多远")

    assert first and second
    assert len(calls) == 2


def test_search_failure_is_quiet(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no net")

    monkeypatch.setattr(search.urllib.request, "urlopen", boom)
    search._cache.clear()

    assert search.search("随便") == []


def test_command_without_query():
    assert "/查" in search.command("")


def test_command_lists_results(monkeypatch):
    monkeypatch.setattr(
        search, "search", lambda query, limit=3: [{"title": "标题", "url": "", "snippet": "摘要"}]
    )

    text = search.command("地球到月球多远")

    assert "标题" in text
    assert "摘要" in text


class FakeResult:
    model = "fake"
    tokens_in = 10
    tokens_out = 5

    def __init__(self, text):
        self.text = text


def test_follow_up_without_results_replaces_the_promise(monkeypatch):
    monkeypatch.setattr(search, "search", lambda query, limit=4: [])
    called = []

    text = search.follow_up(
        type("S", (), {"search_enabled": True})(),
        lambda messages: called.append(1) or FakeResult("多余"),
        [{"role": "user", "content": "珠峰多高"}],
        "我查一下哦\n{{查:珠峰多高}}",
    )

    assert "真不知道" in text
    assert "{{查" not in text
    assert called == []


def test_follow_up_strips_a_repeated_tag(monkeypatch):
    rows = search._parse(RSS)
    monkeypatch.setattr(search, "search", lambda query, limit=4: rows)
    settings = type("S", (), {"search_enabled": True})()

    text = search.follow_up(
        settings,
        lambda messages: FakeResult("查到了，三十八万公里左右\n{{查:地球到月球多远}}"),
        [{"role": "user", "content": "多远"}],
        "等我查查\n{{查:地球到月球多远}}",
    )

    assert "三十八万" in text
    assert "{{查" not in text


def test_no_result_keeps_a_real_answer():
    assert search._no_result("大概三十八万公里吧") == "大概三十八万公里吧"
