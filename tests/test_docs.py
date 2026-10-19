import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest


def test_generated_site_links(tmp_path):
    pytest.importorskip('markdown_it')
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('build_docs', root / 'tools/build_docs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = module.build(root, tmp_path / 'site')
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links, self.ids, self.images = [], set(), []
        def handle_starttag(self, tag, attrs):
            assert tag != 'script'
            attrs = dict(attrs)
            if 'id' in attrs:
                self.ids.add(attrs['id'])
            if tag == 'a' and 'href' in attrs:
                self.links.append(attrs['href'])
            if tag == 'img':
                self.images.append(attrs['src'])
    parsed = {}
    for page in output.rglob('*.html'):
        parser = Links()
        parser.feed(page.read_text())
        parsed[page] = parser
    assert len(parsed) == len(module.PAGES)
    for page, parser in parsed.items():
        sidebar = page.read_text().split('</aside>', 1)[0]
        assert 'class="sidebar-contact"' in sidebar
        assert 'mailto:paragon7060@hanyang.ac.kr' in sidebar
        for href in parser.links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (page.parent / unquote(url.path)).resolve() if url.path else page
            assert target.is_file(), (page, href)
            if url.fragment:
                assert unquote(url.fragment) in parsed[target].ids, (page, href)
        for image in parser.images:
            assert (page.parent / image).resolve().is_file(), (page, image)
    guide = output / 'docs/VISUAL_GUIDE.html'
    assert len(parsed[guide].images) == 10
    guide_html = guide.read_text()
    assert 'shot-callout' in guide_html
    assert '다른 PC의 브라우저에서 사용하기: SSH 터널' in guide_html
    assert '127.0.0.1:8767:127.0.0.1:8765' in guide_html
    assert 'bash setup.sh --headless' in guide_html
    assert 'mailto:paragon7060@hanyang.ac.kr' in parsed[guide].links
