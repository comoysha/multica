import datetime
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ios_signing as signing


class SigningTest(unittest.TestCase):
    def test_sync_cannot_create_assets_or_change_team(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".config/dev-platform/signing/fastlane/Matchfile"
            config.parent.mkdir(parents=True)
            config.write_text('readonly(true)\nteam_id("CYVBAW2826")\n')
            binary = home / ".local/share/dev-platform-tools/fastlane/fixture/gems/ruby/4/bin/fastlane"
            binary.parent.mkdir(parents=True)
            binary.touch()
            (home / ".remote-build").mkdir()
            with mock.patch.object(signing.Path, "home", return_value=home), \
                    mock.patch.object(signing.ios_remote_env, "ROOT", home), \
                    mock.patch.object(signing.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                signing.sync_existing()
                argv = run.call_args.args[0]
                self.assertIn("--readonly", argv)
                self.assertEqual(argv[-1], signing.TEAM)
                self.assertEqual(run.call_args.kwargs["env"]["MATCH_READONLY"], "true")
                run.reset_mock()
                config.write_text('readonly(false)\nteam_id("CYVBAW2826")\n')
                with self.assertRaisesRegex(ValueError, "readonly"):
                    signing.sync_existing()
                run.assert_not_called()

    def test_requires_current_team_exact_bundle_phone_and_existing_private_key(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = b"fixture public certificate"
        identities = {hashlib.sha1(certificate).hexdigest().upper()}
        profile = {"TeamIdentifier": [signing.TEAM], "Entitlements": {
            "application-identifier": signing.TEAM + "." + signing.BUNDLE, "get-task-allow": True},
            "ProvisionedDevices": [signing.DEVICE], "ExpirationDate": now + datetime.timedelta(days=1),
            "DeveloperCertificates": [certificate]}
        self.assertTrue(signing.usable_profile(profile, identities, now))
        for changes in ({"TeamIdentifier": ["OLDTEAM"]}, {"ProvisionedDevices": []},
                        {"ExpirationDate": now - datetime.timedelta(days=1)}, {"DeveloperCertificates": [b"foreign"]},
                        {"Entitlements": {"application-identifier": signing.TEAM + ".*", "get-task-allow": True}},
                        {"Entitlements": {**profile["Entitlements"], "get-task-allow": False}}):
            with self.subTest(changes=changes):
                self.assertFalse(signing.usable_profile({**profile, **changes}, identities, now))
        self.assertFalse(signing.usable_profile(profile, set(), now))


if __name__ == "__main__":
    unittest.main()
