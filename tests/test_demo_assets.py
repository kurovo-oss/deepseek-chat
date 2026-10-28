"""演示页静态校验：确保资源引用有效、页面自包含。

没有后端逻辑，所以这里只做"能不能正常打开"层面的检查：
1. index.html 存在且是完整 HTML
2. 引用的图片资源全部存在（防止改路径后白屏）
3. 不依赖任何外部 CDN（离线也能看）
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "index.html"


class DemoSelfContainedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assert_exists()
        cls.html = DEMO.read_text(encoding="utf-8")

    @classmethod
    def assert_exists(cls):
        if not DEMO.is_file():
            raise AssertionError(f"缺少演示页: {DEMO}")

    def test_html_skeleton(self):
        self.assertTrue(self.html.lstrip().lower().startswith("<!doctype html>"))
        self.assertIn('id="chat"', self.html)
        self.assertIn('id="inp"', self.html)

    def test_referenced_images_exist(self):
        refs = set(re.findall(r"\.\./assets/seed-images/[A-Za-z0-9_.-]+", self.html))
        self.assertTrue(refs, "演示页未引用任何图库资源")
        for ref in refs:
            path = (DEMO.parent / ref).resolve()
            self.assertTrue(path.is_file(), f"引用的图片不存在: {ref}")

    def test_no_external_cdn(self):
        self.assertNotIn("cdn.", self.html.lower())
        self.assertNotIn("https://", self.html.lower(), "演示页不应依赖外网资源")

    def test_key_flows_present(self):
        # 四个核心交互必须在场：输入、主动消息、指令、分段延迟
        self.assertIn("/memory", self.html)
        self.assertIn("/status", self.html)
        self.assertIn("proactiveDemo", self.html)
        self.assertIn("1500", self.html)  # 分段间隔


if __name__ == "__main__":
    unittest.main()
