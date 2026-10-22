"""Read-only browsing of explicitly allowed local data folders."""
from pathlib import Path

from .core import EditError


def browse_local(roots, path=None, query="", offset=0, limit=200, *, directories_only=False):
    roots = list(dict.fromkeys(Path(p).expanduser().resolve() for p in roots))
    def permitted(p):
        return any(p.is_relative_to(root) for root in roots)
    if path is None:
        return {"roots": [str(p) for p in roots], "path": None, "entries": [], "parent": None}
    folder = Path(path).expanduser().resolve()
    if not permitted(folder):
        raise EditError("접근 허용 폴더 밖입니다. --allow-root 또는 데스크톱 파일 선택으로 폴더를 허용하세요.")
    if not folder.is_dir():
        raise EditError("탐색할 폴더가 없습니다.")
    if offset < 0 or not 1 <= limit <= 500:
        raise EditError("잘못된 목록 범위입니다.")
    entries = []
    try:
        for child in folder.iterdir():
            if child.name.startswith(".") or query.casefold() not in child.name.casefold():
                continue
            try:
                resolved = child.resolve()
                if not permitted(resolved):
                    continue
                if resolved.is_dir():
                    info = (resolved / "meta/info.json").resolve()
                    kind = "dataset" if permitted(info) and info.is_file() else "directory"
                    size = None
                elif not directories_only and resolved.is_file() and child.suffix.lower() == ".bag":
                    kind, size = "bag", resolved.stat().st_size
                else:
                    continue
                entries.append({"name": child.name, "path": str(resolved), "kind": kind, "size": size})
            except (OSError, RuntimeError):
                continue
    except OSError as exc:
        raise EditError(f"폴더를 읽을 수 없습니다: {exc.strerror}") from exc
    entries.sort(key=lambda e: (e["kind"] == "bag", e["name"].casefold()))
    return {"roots": [str(p) for p in roots], "path": str(folder),
            "parent": str(folder.parent) if permitted(folder.parent) and folder != folder.parent else None,
            "dataset": permitted((folder / "meta/info.json").resolve()) and (folder / "meta/info.json").is_file(),
            "entries": entries[offset:offset+limit], "total": len(entries), "offset": offset,
            "next_offset": offset+limit if offset+limit < len(entries) else None}
