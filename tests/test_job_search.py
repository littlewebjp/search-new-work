import unittest
from job_search import analyze, search_url


SOURCE = {'search_url': 'https://example.invalid/jobs', 'query_params': {'keyword': '{query}'}, 'listing_pattern': r'^/job/\d+$'}


class SearchTests(unittest.TestCase):
    def test_search_encodes_query_and_preserves_parameters(self):
        from urllib.parse import parse_qs, urlsplit
        url = search_url({**SOURCE, 'search_url': SOURCE['search_url']+'?remote=1'}, '画像編集 業務委託')
        self.assertEqual(parse_qs(urlsplit(url).query), {'remote': ['1'], 'keyword': ['画像編集 業務委託']})

    def test_only_public_matching_job_links_are_extracted(self):
        html = '<input value="画像"><a href="/job/1">画像の制作</a><a href="/job/1">重複</a><a href="https://evil.invalid/job/2">外部</a><a href="javascript:alert(1)">危険</a><script>fake</script>'
        result = analyze(SOURCE, '画像', SOURCE['search_url'], html)
        self.assertEqual(result['status'], 'listings_extracted')
        self.assertEqual(len(result['listings']), 1)
        self.assertEqual(result['listings'][0]['url'], 'https://example.invalid/job/1')
        self.assertNotIn('fake', result['page_excerpt'])

    def test_verified_contract_filter_is_applied_when_available(self):
        from urllib.parse import parse_qs, urlsplit
        source = {**SOURCE, 'mode_query_params': {'contract': {'ltoe[]': '6'}}}
        self.assertEqual(parse_qs(urlsplit(search_url(source, '画像', 'contract')).query)['ltoe[]'], ['6'])
        self.assertNotIn('ltoe[]', parse_qs(urlsplit(search_url(source, '画像', 'employment')).query))

    def test_ignored_keyword_is_not_reported_as_verified_search(self):
        result = analyze(SOURCE, 'レタッチ', SOURCE['search_url'], '<a href="/job/1">おすすめエンジニア</a>')
        self.assertEqual(result['status'], 'query_unconfirmed')

    def test_category_browsing_and_captcha_are_not_fake_results(self):
        browse = {**SOURCE, 'query_params': {}}
        self.assertEqual(analyze(browse, '画像', SOURCE['search_url'], '<a href="/job/1">仕事</a>')['status'], 'browsing_only')
        checked = analyze(SOURCE, '画像', SOURCE['search_url'], 'Please complete the following challenge')
        self.assertEqual(checked['status'], 'access_check')
        self.assertEqual(checked['listings'], [])

    def test_redirect_requires_review(self):
        self.assertEqual(analyze(SOURCE, '画像', 'https://other.invalid/', '')['status'], 'redirected')


if __name__ == '__main__':
    unittest.main()
