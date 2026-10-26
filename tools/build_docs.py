"""Generate portable documentation from README/docs (one source of truth)."""
import argparse
import html
import json
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

PAGES = [
    ("README.md", "개요 · Quick start"),
    ("docs/GETTING_STARTED.md", "새 PC 설치·실행"),
    ("docs/DATA_IMPORT.md", "파일 선택 · SSH 가져오기"),
    ("docs/VISUAL_GUIDE.md", "화면으로 따라 하기"),
    ("docs/DATASET_REPOSITORIES.md", "Dataset 생성·추가·수정"),
    ("docs/WHOLEBODY_SCHEMA.md", "Torso·랙 state / action·mask"),
    ("docs/DATA_INSPECTION.md", "Raw bag · Export 미리보기"),
    ("docs/GOAL_ANNOTATIONS.md", "Goal / Step 두 계층 라벨"),
    ("docs/SOURCE_STATUS.md", "원본별 라벨링 상태"),
    ("docs/ACTIVITY_VIEW.md", "영상 동작 요약"),
    ("docs/STORAGE.md", "저장 공간 · 무손실 정리"),
    ("docs/HUGGING_FACE.md", "Hugging Face 업로드"),
    ("docs/LEROBOT_COMPATIBILITY.md", "LeRobot 버전·업데이트"),
    ("docs/ARCHITECTURE.md", "구조"),
    ("docs/DEVELOPMENT.md", "개발·테스트·패키징"),
    ("docs/VALIDATION.md", "검증 범위"),
    ("docs/INTEGRATION_VER2.md", "RWH + ver2 통합 보고서"),
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
    assets = root / "docs/assets"
    if assets.exists():
        shutil.copytree(assets, output / "docs/assets", dirs_exist_ok=True)
    callout_path = assets / "screenshots/callouts.json"
    callouts = json.loads(callout_path.read_text()) if callout_path.exists() else {}
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
        original_image = renderer.renderer.rules["image"]
        def guide_image(tokens, idx, options, env):
            token = tokens[idx]
            src = token.attrGet("src")
            shot = callouts.get(Path(src).stem) if "assets/screenshots/" in src else None
            token.attrSet("loading", "lazy")
            token.attrSet("decoding", "async")
            if shot:
                # Reserve space before lazy images load, so heading links do not jump.
                token.attrSet("width", str(shot["width"]))
                token.attrSet("height", str(shot["height"]))
            image = original_image(tokens, idx, options, env)
            if not shot:
                return image
            markers = "".join(
                f'<span class="shot-callout" aria-hidden="true" title="{html.escape(p["label"], quote=True)}" '
                f'style="left:{100*p["x"]/shot["width"]:.3f}%;top:{100*p["y"]/shot["height"]:.3f}%">{p["number"]}</span>'
                for p in shot["points"])
            return ('<span class="guide-shot"><a class="screenshot-link" target="_blank" rel="noopener" '
                    f'href="{html.escape(src, quote=True)}" aria-label="화면 원본 크기로 보기">{image}{markers}</a>'
                    '<span class="shot-caption">번호는 사용 안내 표시입니다. 이미지를 누르면 실제 화면을 원본 크기로 볼 수 있습니다.</span></span>')
        renderer.renderer.rules["image"] = guide_image
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
<div class="sidebar-note">LOCAL FIRST<br>ROS 설치 · 로봇 연결 불필요<br>Linux x86_64 / ARM64 · Python 3.12</div>{repo_link}
<div class="sidebar-contact" aria-label="문의 이메일"><span>CONTACT</span>
<a href="mailto:paragon7060@hanyang.ac.kr">paragon7060@hanyang.ac.kr</a></div></aside>
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
