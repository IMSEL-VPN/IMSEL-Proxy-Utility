import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('history_guard', ROOT / 'scripts/history_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class HistoryGuardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git('init', '--initial-branch=main')
        (self.root / 'public-pages.json').write_text(json.dumps(['README.md']), encoding='utf-8')
        (self.root / 'README.md').write_text('# Welcome\nPublic guide.\n', encoding='utf-8')
        (self.root / 'mkdocs.yml').write_text('site_name: Test\nnav:\n  - Home: index.md\n', encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        environment = os.environ.copy()
        environment.update(GIT_AUTHOR_NAME='Guard Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                           GIT_COMMITTER_NAME='Guard Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        return subprocess.check_output(['git', *args], cwd=self.root, env=environment,
                                       stderr=subprocess.STDOUT).decode().strip()

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-m', 'test snapshot')
        return self.git('rev-parse', 'HEAD')

    def test_clean_history_passes(self):
        self.commit()
        self.assertEqual(guard.validate_history(root=self.root), (1, 1))

    def test_deleted_source_in_parent_is_rejected(self):
        (self.root / 'private.dart').write_text('private source', encoding='utf-8')
        self.commit()
        (self.root / 'private.dart').unlink()
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Unapproved historical file'):
            guard.validate_history(root=self.root)

    def test_removed_internal_markdown_is_rejected(self):
        (self.root / 'README.md').write_text('# Guide\nSubscriptionService lib/private.dart', encoding='utf-8')
        self.commit()
        (self.root / 'README.md').write_text('# Guide\nPublic content', encoding='utf-8')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Historical tree'):
            guard.validate_history(root=self.root)

    def test_foreign_ancestry_cannot_be_hidden_by_clean_tip(self):
        (self.root / 'public-pages.json').unlink()
        self.commit()
        (self.root / 'public-pages.json').write_text('["README.md"]', encoding='utf-8')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'Not a documentation tree'):
            guard.validate_history(root=self.root)

    def test_archive_and_unclassified_python_are_rejected(self):
        for name in ('application.zip', 'private.py', 'scripts/private.dart'):
            self.assertFalse(guard.allowed(name, ['README.md']))

    def test_non_main_push_is_rejected(self):
        oid = '1' * 40
        with patch.object(guard, 'check_destination'):
            with self.assertRaisesRegex(ValueError, 'Only the documentation main'):
                guard.check_push('origin', 'unused', f'refs/heads/topic {oid} refs/heads/topic {guard.ZERO}')

    def test_unapproved_deletion_is_rejected(self):
        oid = '1' * 40
        with patch.object(guard, 'check_destination'), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Unapproved branch deletion'):
                guard.check_push('origin', 'unused', f'(delete) {guard.ZERO} refs/heads/main {oid}')

    def test_reset_deletion_requires_exact_branch_and_sha(self):
        oid = '1' * 40
        with patch.object(guard, 'check_destination'), patch.dict(os.environ, {'DOCS_RESET_DELETE_SHA': oid}):
            guard.check_push('origin', 'unused',
                             f'(delete) {guard.ZERO} refs/heads/docs/ping-concurrency-settings {oid}')
            with self.assertRaises(ValueError):
                guard.check_push('origin', 'unused',
                                 f'(delete) {guard.ZERO} refs/heads/docs/ping-concurrency-settings {"2" * 40}')

    def test_application_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not a documentation'):
            guard.check_destination('origin', 'https://gitlab.com/samueladamson37/imsel-vpn.git')

    def test_all_outgoing_tips_are_checked(self):
        first, second = '1' * 40, '2' * 40
        with patch.object(guard, 'check_destination'), patch.object(guard, 'validate_history') as validate:
            guard.check_push('origin', 'unused',
                             f'HEAD {first} refs/heads/main {guard.ZERO}\n'
                             f'HEAD {second} refs/heads/main {guard.ZERO}')
            self.assertEqual({call.args[0] for call in validate.call_args_list}, {first, second})


if __name__ == '__main__':
    unittest.main()
