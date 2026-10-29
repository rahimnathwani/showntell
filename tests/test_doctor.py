"""`showntell doctor` rows that need no models or network."""

from showntell import __version__, doctor


def test_slide_checks_row_names_what_is_off_and_how_to_add_the_browser(monkeypatch):
    monkeypatch.setattr(doctor, "_chromium", lambda: "")
    status, name, detail = doctor._browser_check()
    assert (status, name) == (doctor.WARN, "slide checks")
    for part in ("not draw the slides", "layout findings", "pictures of alike slides", "poster", "MP4"):
        assert part in detail
    assert doctor.INSTALL_VIDEO in detail and doctor.INSTALL_BROWSER in detail
    # the install commands are pinned to this release, like the plain one
    assert f"@v{__version__}" in doctor.INSTALL_BROWSER and f"@v{__version__}" in doctor.INSTALL


def test_slide_checks_row_is_ok_with_the_browser(monkeypatch):
    monkeypatch.setattr(doctor, "_chromium", lambda: "/browsers/chrome")
    assert doctor._browser_check()[:2] == (doctor.OK, "slide checks")


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
