# 문서 사이트 · GitHub Pages

게시 주소: [Kuavo Dataset Editor 가이드](https://robots-oh.github.io/kuavo-dataset-editor/).
소스는 [robots-oh/kuavo-dataset-editor](https://github.com/robots-oh/kuavo-dataset-editor) 비공개 저장소다.
**코드는 비공개, 이 문서 사이트는 공개**다. 2026-10-16에 첫 배포와 HTTP 200 응답을 확인했다.

README와 `docs/*.md`가 문서의 단일 원본이다. `tools/build_docs.py`가 공통 메뉴와 목차가 있는
정적 HTML로 변환한다. 앱 서버나 로봇, 로그인, analytics, 외부 font/CDN은 필요 없다.
사이트에는 문서만 포함되며 bag·workspace·인증정보는 포함되지 않는다.

## 로컬 미리보기

```bash
bash setup.sh --with-docs
.venv/bin/python tools/build_docs.py --output dist/site
.venv/bin/python -m http.server 8766 --bind 127.0.0.1 --directory dist/site
```

`http://127.0.0.1:8766`을 연다. editor 8765 포트와 별개다.
`dist/site/index.html`을 직접 열어도 된다. 상대 경로로 구성해 하위 URL에서도 동작한다.
문서 전용 환경이라면 `bash setup.sh --minimal --with-docs`로 desktop/export 의존성을 생략할 수 있다.

## GitHub 게시

1. 소스 repo를 생성하고 push한다. 이 문서는 특정 계정/조직/공개 여부를 가정하지 않는다.
2. **Settings → Pages → Build and deployment → Source → GitHub Actions**를 선택한다.
3. **Actions → Publish documentation to GitHub Pages → Run workflow**를 실행한다.
4. 완료된 `github-pages` deployment의 URL을 사용한다.

workflow는 **수동 실행만** 가능하다. push만으로 문서를 외부에 게시하지 않는다.
Pages 활성화에는 repo owner/plan/조직 정책의 제한이 있을 수 있다.
private source repo라고 문서 사이트도 자동 비공개인 것은 아니다. 공개 범위를 확인한 뒤 활성화한다.
배포 전 예상 URL을 게시 완료 URL로 안내하지 않는다.

Actions는 실제 `GITHUB_REPOSITORY`로 소스 링크를 생성한다.
기본 `GITHUB_TOKEN`의 contents:read, pages:write, id-token:write 권한만 사용한다.
개인 PAT나 Hugging Face token을 Pages secrets에 추가할 필요가 없다.

## 갱신

Markdown 수정 → 로컬 빌드 → 수동 workflow 재실행 순서다.
메뉴는 `tools/build_docs.py`의 `PAGES`, 디자인은 `docs/site/style.css`에서 관리한다.
`dist/site`는 생성 결과로 Git에서 제외한다. 앱 설치에는 문서 빌드가 필요 없다.

공식 참고: [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
