"""Regression tests for upstream release discovery without network access."""

from unittest import TestCase
from unittest.mock import patch

from scripts import update


def deb_download(architecture="x64", version="1.2.2"):
    file_arch = {"x64": "amd64", "arm64": "arm64"}[architecture]
    url = (
        f"https://gitcode.com/Pylogmon/Manggo/releases/download/{version}/"
        f"manggo_{version}_{file_arch}.deb"
    )
    return {
        "platform": "Linux",
        "architecture": architecture,
        "version": version,
        "systemRequirements": "Ubuntu/Debian",
        "target": {"kind": "externalUrl", "externalUrl": url, "href": url},
    }


class ManggoReleaseTests(TestCase):
    def discover(self, *items):
        data = {
            "data": {"sections": [{"type": "download", "download": {"items": items}}]}
        }
        with patch.object(update, "get_json", return_value=data):
            return update.manggo({"pkgver": "1.2.2"})

    def test_current_homepage_schema_discovers_debian_release(self):
        self.assertEqual(
            self.discover(deb_download(), deb_download("arm64")),
            {"pkgver": "1.2.2"},
        )

    def test_other_downloads_do_not_contribute_versions(self):
        rpm = deb_download(version="9.0.0")
        rpm["systemRequirements"] = "deb"
        rpm["target"] = {"href": "https://example.com/manggo.rpm?filename=manggo.deb"}
        windows = deb_download(version="9.1.0")
        windows["platform"] = "Windows"
        unsupported_arch = deb_download(version="9.2.0")
        unsupported_arch["architecture"] = "x86"
        self.assertEqual(
            self.discover(deb_download(), rpm, windows, unsupported_arch),
            {"pkgver": "1.2.2"},
        )

    def test_debian_url_query_and_fragment_do_not_hide_file_type(self):
        item = deb_download()
        item["target"]["href"] += "?download=1#binary"
        self.assertEqual(self.discover(item), {"pkgver": "1.2.2"})

    def test_external_url_is_used_when_href_is_absent_or_empty(self):
        for href in (None, ""):
            with self.subTest(href=href):
                item = deb_download()
                if href is None:
                    item["target"].pop("href")
                else:
                    item["target"]["href"] = href
                self.assertEqual(self.discover(item), {"pkgver": "1.2.2"})

    def test_href_takes_precedence_over_external_url(self):
        item = deb_download(version="9.0.0")
        item["target"]["href"] = "https://example.com/manggo.rpm"
        self.assertEqual(self.discover(deb_download(), item), {"pkgver": "1.2.2"})

    def test_missing_debian_download_fails(self):
        missing_target = deb_download()
        missing_target.pop("target")
        for items in ((), (missing_target,)):
            with self.subTest(items=items):
                with self.assertRaisesRegex(RuntimeError, r"found \[\]"):
                    self.discover(*items)

    def test_selected_download_without_version_fails(self):
        item = deb_download()
        item.pop("version")
        with self.assertRaises(KeyError):
            self.discover(item)

    def test_conflicting_debian_versions_fail(self):
        with self.assertRaisesRegex(RuntimeError, r"found \['1\.2\.2', '1\.2\.3'\]"):
            self.discover(deb_download(), deb_download("arm64", "1.2.3"))

    def test_version_prefix_is_normalized_before_comparison(self):
        item = deb_download("arm64")
        item["version"] = "v1.2.2"
        self.assertEqual(self.discover(deb_download(), item), {"pkgver": "1.2.2"})
