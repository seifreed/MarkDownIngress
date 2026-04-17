from markdown_ingress.config_models import DomainPolicy
from markdown_ingress.core.domain_rules import apply_domain_html_rules

HTML = """
<html>
  <body>
    <article>
      <h1>Title</h1>
      <div class="promo">Buy now</div>
      <form><input type="text" /></form>
      <section class="content"><p>Keep me</p></section>
      <aside><p>Sidebar</p></aside>
    </article>
  </body>
</html>
"""


def test_apply_domain_html_rules_removes_selectors_and_tags():
    policy = DomainPolicy(
        domain="example.com",
        blocked_selectors=[".promo"],
        blocked_tags=["form"],
    )

    filtered, stats = apply_domain_html_rules(HTML, policy)

    assert "Buy now" not in filtered
    assert "<form" not in filtered
    assert stats["blocked_selectors_removed"] == 1
    assert stats["blocked_tags_removed"] == 1


def test_apply_domain_html_rules_unwraps_and_allows_only_specific_tags():
    policy = DomainPolicy(
        domain="example.com",
        unwrap_selectors=["section.content"],
        allowed_tags=["article", "h1", "p"],
    )

    filtered, stats = apply_domain_html_rules(HTML, policy)

    assert "<section" not in filtered
    assert "<aside" not in filtered
    assert "<article" in filtered
    assert "<p>Keep me</p>" in filtered
    assert stats["unwrapped_nodes"] >= 2


def test_apply_domain_html_rules_removes_active_tags_when_whitelisting():
    policy = DomainPolicy(domain="example.com", allowed_tags=["div"])

    filtered, stats = apply_domain_html_rules(HTML, policy)

    assert "Buy now" in filtered
    assert "Sidebar" in filtered
    assert "alert(1)" not in filtered
    assert "body{color:red}" not in filtered
    assert "Meta Title" not in filtered
    assert stats["unwrapped_nodes"] >= 2
