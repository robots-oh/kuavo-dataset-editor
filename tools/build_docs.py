"""Generate portable documentation from README/docs (one source of truth)."""
import argparse
import html
import posixpath
import re
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

PAGES = [
    ("README.md", "개요 · Quick start"),
    ("docs/GETTING_STARTED.md", "새 PC 설치·실행"),
    ("docs/DATASET_REPOSITORIES.md", "Dataset 생성·추가·수정"),
    ("docs/HUGGING_FACE.md", "Hugging Face 업로드"),
    ("docs/LEROBOT_COMPATIBILITY.md", "LeRobot 버전·업데이트"),
    ("docs/ARCHITECTURE.md", "구조"),
    ("docs/DEVELOPMENT.md", "개발·테스트·패키징"),
    ("docs/VALIDATION.md", "검증 범위"),
    ("docs/GITHUB_PAGES.md", "문서 사이트 배포"),
]


def build(root, output, repo_url=""):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root / "docs") or any(output.glob("*.md")) or (output / "meta/info.json").exists():
        raise ValueError("Use a generated-output directory, e.g. dist/site")
    destinations = {name: "index.html" if name == "README.md" else name[:-3] + ".html" for name, _ in PAGES}
    output.mkdir(parents=True, exist_ok=True)
    (output / "style.css").write_text((root / "docs/site/style.css").read_text())
    (output / ".nojekyll").write_text("")
    for source, label in PAGES:
        destination = Path(destinations[source])
        def relative(target):
            return posixpath.relpath(str(target), str(destination.parent))
        renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
        headings, used = [], {}
        def heading_open(tokens, idx, options, env):
            title = tokens[idx + 1].content
            slug = re.sub(r"[^\w\s-]", "", title.lower())
            slug = re.sub(r"[\s_]+", "-", slug).strip("-") or "section"
            number = used.get(slug, 0)
            used[slug] = number + 1
            slug += f"-{number}" if number else ""
            tokens[idx].attrSet("id", slug)
            if tokens[idx].tag == "h2":
                headings.append((slug, title))
            return renderer.renderer.renderToken(tokens, idx, options, env)
        def link_open(tokens, idx, options, env):
            href = tokens[idx].attrGet("href")
            url = urlsplit(href)
            if not url.scheme and not url.netloc and url.path:
                resolved = posixpath.normpath(str(Path(source).parent / url.path))
                if resolved in destinations:
                    href = relative(destinations[resolved]) + ("#" + url.fragment if url.fragment else "")
                elif repo_url:
                    href = repo_url.rstrip("/") + "/blob/main/" + resolved
                tokens[idx].attrSet("href", href)
            return renderer.renderer.renderToken(tokens, idx, options, env)
        renderer.renderer.rules["heading_open"] = heading_open
        renderer.renderer.rules["link_open"] = link_open
        content = renderer.render((root / source).read_text())
        nav = "".join(f'<a href="{html.escape(relative(destinations[name]))}"'
                      + (' aria-current="page"' if name == source else '')
                      + f'>{html.escape(title)}</a>' for name, title in PAGES)
        toc = "".join(f'<a href="#{html.escape(slug)}">{html.escape(title)}</a>' for slug, title in headings)
        repo_link = f'<a href="{html.escape(repo_url)}">GitHub source ↗</a>' if repo_url else ''
        document = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="ROS bag · LeRobot 데이터셋 영상 편집기 설치와 사용 가이드">
<title>{html.escape(label)} — Kuavo Dataset Editor</title>
<link rel="stylesheet" href="{relative('style.css')}"></head>
<body><a class="skip" href="#content">본문으로 건너뛰기</a>
<aside class="sidebar"><a class="brand" href="{relative('index.html')}"><span class="mark">K</span><span>KUAVO<small>DATASET EDITOR</small></span></a>
<p class="eyebrow">USER &amp; DEVELOPER GUIDE</p><nav aria-label="가이드">{nav}</nav>
<div class="sidebar-note">LOCAL FIRST<br>ROS 설치 · 로봇 연결 불필요<br>Linux x86_64 · Python 3.12</div>{repo_link}</aside>
<div class="page"><header><span>RECORDINGS → LABELED DATASETS</span><span class="badge">LeRobot + ROS1</span></header>
<div class="reading"><main id="content"><div class="page-label">GUIDE / {html.escape(label)}</div>{content}
<footer>편집·변환은 로컬에서. Hub 공유는 확인 후에만.<br>문서 원본: {html.escape(source)}</footer></main>
<aside class="toc"><strong>이 페이지</strong>{toc}</aside></div></div></body></html>'''
        path = output / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/site")
    parser.add_argument("--repo-url", default="")
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output, args.repo_url))
