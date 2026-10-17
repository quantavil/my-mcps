import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inputs

ANDROID = 'http://schemas.android.com/apk/res/android'
CERT = 'a' * 64

def sdk_output(command):
    if 'manifest' in command:
        with zipfile.ZipFile(command[-1]) as z:
            xml = z.read('AndroidManifest.xml').decode()
        if xml == 'manifest':
            xml = f'<manifest xmlns:android="{ANDROID}" package="com.example.app" android:versionCode="1" android:versionName="1.0" />'
        return xml
    return f'Signer #1 certificate SHA-256 digest: {CERT}\n'

class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch('inputs._command', side_effect=sdk_output).start()
        patch('inputs.sdk_binary', side_effect=lambda name: name).start()

    def apk(self, name='base.apk', split='', package='com.example.app', code='1', entries=None, dependency=''):
        path = self.root / name
        attrs = f' split="{split}"' if split else ''
        dep = f'<uses-split android:name="{dependency}" />' if dependency else ''
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('AndroidManifest.xml', f'<manifest xmlns:android="{ANDROID}" package="{package}" android:versionCode="{code}" android:versionName="1.0"{attrs}>{dep}</manifest>')
            for key, value in (entries or {}).items():
                z.writestr(key, value)
        return path

    def test_split_identity_and_order(self):
        base = self.apk(entries={'classes.dex': b'dex'})
        arm = self.apk('arm.apk', 'config.arm64_v8a', entries={'lib/arm64-v8a/libapp.so': b'aot'})
        lang = self.apk('lang.apk', 'config.en')
        one = inputs.describe_input(base, [arm, lang])
        two = inputs.describe_input(base, [lang, arm])
        self.assertEqual(one['input_id'], two['input_id'])
        self.assertEqual(one['native_abis'], ['arm64-v8a'])
        self.assertEqual(len(one['artifacts']), 3)
        shutil.copy(base, self.root / 'copy.apk')
        self.assertEqual(inputs.describe_input(self.root / 'copy.apk')['input_id'], inputs.describe_input(base)['input_id'])

    def test_rejects_split_as_base_and_mixed_inputs(self):
        split = self.apk('arm.apk', 'config.arm64_v8a')
        with self.assertRaisesRegex(ValueError, 'base'):
            inputs.describe_input(split)
        base = self.apk()
        for kwargs in ({'package': 'other.app'}, {'code': '2'}, {'split': 'config.en', 'dependency': 'missing'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                inputs.describe_input(base, [self.apk('bad.apk', **kwargs)])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inputs.describe_input(base, [split, split])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inputs.describe_input(base, [split, self.apk('same.apk', 'config.arm64_v8a')])

    def test_changed_files_and_shared_code_mismatch(self):
        base = self.apk(entries={'classes.dex': b'one'})
        static = inputs.describe_input(base)
        other = inputs.describe_input(self.apk('other.apk', entries={'classes.dex': b'two'}))
        self.assertFalse(inputs.compare_inputs(static, other)['compatible'])
        base.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            inputs.verify_input_files(static)

    def test_invalid_signature_and_mixed_signers(self):
        base, split = self.apk(), self.apk('split.apk', 'config.en')
        with patch('inputs._command', side_effect=ValueError('signature invalid')):
            with self.assertRaisesRegex(ValueError, 'signature'):
                inputs.describe_input(base)
        def different(command):
            if 'manifest' not in command and command[-1].endswith('split.apk'):
                return 'Signer #1 certificate SHA-256 digest: ' + 'b' * 64
            return sdk_output(command)
        with patch('inputs._command', side_effect=different), self.assertRaisesRegex(ValueError, 'signer'):
            inputs.describe_input(base, [split])

    def test_resource_differences_are_gaps(self):
        a = inputs.describe_input(self.apk(entries={'resources.arsc': b'one'}))
        b = inputs.describe_input(self.apk('other.apk', entries={'resources.arsc': b'two'}))
        compared = inputs.compare_inputs(a, b)
        self.assertTrue(compared['compatible'])
        self.assertTrue(compared['gaps'])

    def test_descriptor_tamper_is_rejected(self):
        descriptor = inputs.describe_input(self.apk())
        descriptor['package'] = 'tampered.app'
        with self.assertRaisesRegex(ValueError, 'identity'):
            inputs.verify_input_files(descriptor)

    def test_config_split_inherits_omitted_version_name(self):
        base, split = self.apk(), self.apk('config.apk', 'config.en')
        def omitted(command):
            value = sdk_output(command)
            if 'manifest' in command and command[-1].endswith('config.apk'):
                value = value.replace(' android:versionName="1.0"', '')
            return value
        with patch('inputs._command', side_effect=omitted):
            descriptor = inputs.describe_input(base, [split])
        self.assertEqual(descriptor['version_name'], '1.0')

    def test_feature_config_cannot_satisfy_base_split_requirement(self):
        base = self.apk()
        feature = self.apk('feature.apk', 'feature')
        config = self.apk('feature_config.apk', 'feature.config.arm64')
        def scoped(command):
            value = sdk_output(command)
            if 'manifest' in command:
                if command[-1].endswith('/base.apk'):
                    value = value.replace('package="', 'android:requiredSplitTypes="base__abi" package="')
                elif command[-1].endswith('/feature.apk'):
                    value = value.replace('package="', 'android:isFeatureSplit="true" package="')
                else:
                    value = value.replace('package="', 'configForSplit="feature" android:splitTypes="base__abi" package="')
            return value
        with patch('inputs._command', side_effect=scoped), self.assertRaisesRegex(ValueError, 'required split'):
            inputs.describe_input(base, [feature, config])
