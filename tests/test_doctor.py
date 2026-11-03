"""`showntell doctor` rows that need no models or network."""

import pytest

from showntell import __version__, browser, doctor


@pytest.fixture
def no_browser(monkeypatch):
    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: False)


def test_browser_row_says_the_daemon_fetches_it_and_what_is_off_until_then(no_browser):
    status, name, detail = doctor._browser_check()
    assert (status, name) == (doctor.WARN, "browser")
    assert "not downloaded yet" in detail and "in the background once the voice model is ready" in detail
    for part in ("not draw the slides", "layout findings", "pictures of alike slides", "poster", "MP4"):
        assert part in detail
    assert doctor.INSTALL_BROWSER in detail and "--only-shell" in doctor.INSTALL_BROWSER
    # the install commands are pinned to this release, like the plain one
    assert f"@v{__version__}" in doctor.INSTALL_BROWSER and f"@v{__version__}" in doctor.INSTALL


def test_browser_row_reports_the_daemons_download(no_browser):
    lock = {"health": {"browser": {"state": "downloading", "detail": "downloading (40% of 99 MiB)"}}}
    status, name, detail = doctor._browser_check(lock)
    assert status == doctor.WARN and "the daemon reports: downloading (40% of 99 MiB)" in detail


def test_browser_row_when_turned_off(no_browser, monkeypatch):
    monkeypatch.setenv("SHOWNTELL_BROWSER", "off")
    status, name, detail = doctor._browser_check()
    assert status == doctor.WARN and "SHOWNTELL_BROWSER=off" in detail and "not downloaded" not in detail


def test_browser_row_names_missing_libraries(monkeypatch):
    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: True)
    monkeypatch.setattr(browser, "verify", lambda: browser.explain_launch_error(RuntimeError("Host system is missing dependencies")))
    status, name, detail = doctor._browser_check()
    assert status == doctor.WARN and "install-deps chromium" in detail


def test_browser_row_is_ok_when_it_starts(monkeypatch):
    monkeypatch.delenv("SHOWNTELL_BROWSER", raising=False)
    monkeypatch.setattr(browser, "installed", lambda: True)
    monkeypatch.setattr(browser, "verify", lambda: "")
    assert doctor._browser_check()[:2] == (doctor.OK, "browser")


def test_mp4_row_names_the_missing_tool(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda x: None if x == "ffprobe" else "/usr/bin/" + x)
    status, name, detail = doctor._ffmpeg_check()
    assert (status, name) == (doctor.WARN, "mp4 export") and "ffprobe" in detail and "ffmpeg (" not in detail


def _hosted(tmp_path, **kw):
    from showntell.config import Config

    base = dict(
        host="0.0.0.0", port=4747, data_dir=tmp_path, public_url="https://s.example.com", mode="hosted", dev_users="", deliver="link"
    )
    base.update(oidc="google", oidc_client_id="id", oidc_client_secret="secret", allowed_domains="example.com")
    return Config(**{**base, **kw})


def test_hosted_rows_name_a_bad_setting(tmp_path):
    rows = doctor._hosted_checks(_hosted(tmp_path, allowed_domains=""))
    assert rows[0][:2] == (doctor.FAIL, "hosted settings") and "ALLOWED_DOMAINS" in rows[0][2]


def test_hosted_rows_check_the_provider_and_the_sandbox(tmp_path, monkeypatch):
    import httpx

    class Answer:
        def __init__(self, issuer):
            self.issuer = issuer

        def raise_for_status(self):
            return self

        def json(self):
            return {"issuer": self.issuer}

    monkeypatch.setattr(doctor, "_sandbox_check", lambda: (doctor.OK, "chromium sandbox", "on"))
    monkeypatch.setattr(httpx, "get", lambda url, timeout: Answer("https://accounts.google.com"))
    rows = doctor._hosted_checks(_hosted(tmp_path))
    assert [r[:2] for r in rows] == [(doctor.OK, "hosted settings"), (doctor.OK, "sign-in provider"), (doctor.OK, "chromium sandbox")]
    monkeypatch.setattr(httpx, "get", lambda url, timeout: Answer("https://evil.example"))
    assert doctor._hosted_checks(_hosted(tmp_path))[1][:2] == (doctor.FAIL, "sign-in provider")


def test_hosted_rows_warn_about_the_dev_login(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_sandbox_check", lambda: (doctor.OK, "chromium sandbox", "on"))
    cfg = _hosted(tmp_path, oidc="", host="127.0.0.1", public_url="http://localhost:4747", dev_users="a@example.com")
    assert doctor._hosted_checks(cfg)[0][:2] == (doctor.WARN, "hosted settings")
