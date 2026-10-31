import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_offline import build
from latte import Store


class PublicBuildTests(unittest.TestCase):
    def test_public_build_never_reads_personal_store(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('build_offline.Store', side_effect=AssertionError('Personal store must not be read')):
                build('/not-a-personal-store', folder, public_site=True)
            html = Path(folder, 'index.html').read_text()
            seed = json.loads(re.search(r'id="latte-seed">(.*?)</script>', html, re.S).group(1))
            self.assertEqual(seed['profile']['facts'], [])
            self.assertEqual(seed['jobs'], [])
            self.assertEqual(seed['messages'], [])
            self.assertIn("connect-src 'none'", html)
            self.assertNotIn('src="/app.js"', html)
            self.assertNotIn('href="/styles.css"', html)
            self.assertTrue(Path(folder, '.nojekyll').exists())

    def test_private_export_escapes_script_closing_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder, 'data')
            store = Store(data)
            dangerous = '</script><script>window.BAD=true</script>'
            store.add_memory({'label': 'Synthetic test', 'value': dangerous})
            output = Path(folder, 'output')
            build(data, output)
            html = (output/'LATTE.html').read_text()
            raw = re.search(r'id="latte-seed">(.*?)</script>', html, re.S).group(1)
            self.assertNotIn('<script>', raw)
            self.assertEqual(json.loads(raw)['profile']['facts'][0]['value'], dangerous)


if __name__ == '__main__':
    unittest.main()
