from safeplate import api_server


def test_app_html_takes_no_theme_and_has_no_toggle():
    html = api_server.app_html()  # must accept zero args now
    assert "@sort-core:start" in html
    assert "theme-switch" not in html
    assert "?theme=" not in html


def test_no_theme_cookie_or_param_machinery():
    import inspect
    src = inspect.getsource(api_server)
    assert "sp_theme" not in src
    assert "_APP_TEMPLATE_PATHS" not in src
