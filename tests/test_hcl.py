from heckle.hcl.parser import parse_body
from heckle.hcl.render import body_to_value, literal_string, render_value


def test_parse_nested_body_and_render_value():
    body = parse_body("""
name = "demo"
enabled = true
configuration {
  url = "https://example.invalid/hook"
}
""")
    assert literal_string(body.attributes["name"]) == "demo"
    value = body_to_value(body)
    rendered = "\n".join(render_value(value))
    assert '"configuration"' in rendered
    assert 'https://example.invalid/hook' in rendered
