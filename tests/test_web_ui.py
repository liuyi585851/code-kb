import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.web_ui import render_app_shell, render_metric_cards


class WebUiTests(unittest.TestCase):
    def test_app_shell_provides_reusable_dashboard_chrome(self):
        html = render_app_shell(
            title="测试页面",
            subtitle="研发知识运营",
            active="hub",
            body='<section class="panel">Body</section>',
            actions=(("/healthz", "健康检查"),),
        )

        self.assertIn('data-ui-version="3"', html)
        self.assertIn('class="app"', html)
        self.assertIn('class="sidebar"', html)
        self.assertIn('class="nav"', html)
        self.assertIn('class="nav-item"', html)
        self.assertNotIn('class="side-rail"', html)
        self.assertIn("Code-KB", html)
        self.assertIn("工作台", html)
        self.assertIn('href="/hub"', html)
        self.assertIn('href="/audit/page"', html)
        self.assertIn('href="/demo/current-user"', html)
        self.assertIn('href="/demo/webhook"', html)
        self.assertIn('href="/auth/im/confirmations/page"', html)
        self.assertIn('href="/storage/qdrant/page"', html)
        self.assertIn('href="/diagnose/final-verification/page"', html)
        self.assertIn('aria-current="page"', html)
        self.assertIn('<section class="panel">Body</section>', html)

    def test_app_shell_escapes_titles_and_action_labels(self):
        html = render_app_shell(
            title='<script>alert("x")</script>',
            subtitle="<b>subtitle</b>",
            active="unknown",
            body="",
            actions=(('/unsafe?x="<tag>"', '<open>'),),
        )

        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;subtitle&lt;/b&gt;", html)
        self.assertIn("&lt;open&gt;", html)
        self.assertIn("/unsafe?x=&quot;&lt;tag&gt;&quot;", html)
        self.assertNotIn("<script>alert", html)

    def test_metric_cards_render_stable_dashboard_contract(self):
        html = render_metric_cards(
            (
                ("服务", "检查中", "healthz"),
                ("索引", "检查中", "index"),
            )
        )

        self.assertIn('class="metric-grid"', html)
        self.assertIn('data-metric="healthz"', html)
        self.assertIn("服务", html)
        self.assertIn("索引", html)


if __name__ == "__main__":
    unittest.main()
