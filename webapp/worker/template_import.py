"""Run the deterministic PPTX-to-template import inside the Worker image."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from pathlib import Path

WORKSPACE = Path("/workspace/template")
SKILL_ROOT = Path("/app/skills/ppt-master")


def _emit_progress(stage: str, message: str) -> None:
    print(json.dumps({"type": "progress", "stage": stage, "message": message}, ensure_ascii=False), flush=True)


def _run_step(stage: str, message: str, command: list[str]) -> None:
    """Run one import step and expose its lifecycle to the trusted runner."""

    _emit_progress(stage, message)
    result = subprocess.run(command, cwd=SKILL_ROOT, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr[-3000:] or result.stdout[-3000:] or "步骤执行失败"
        raise RuntimeError(detail)
    _emit_progress(stage, f"{message}完成")


def _first_color(theme: dict) -> str:
    values = theme.get("colors", theme.get("themeColors", []))
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, (list, tuple)):
        return "#0F766E"
    for value in values:
        candidate = str(value).lstrip("#")
        if len(candidate) == 6 and all(char in "0123456789abcdefABCDEF" for char in candidate):
            return f"#{candidate.upper()}"
    return "#0F766E"


def _write_template_spec(template_root: Path, manifest: dict, page_count: int) -> None:
    """Create the minimum portable Deck contract without semantic AI inference."""

    theme = manifest.get("theme") if isinstance(manifest.get("theme"), dict) else {}
    primary_color = _first_color(theme)
    roster = "\n".join(
        f"- {index:03d}: imported source slide {index}; preserve its visual structure as a reusable template."
        for index in range(1, page_count + 1)
    ) or "- No slides were recovered from the source PPTX."
    content = f'''---
deck_id: imported-template
kind: deck
category: general
summary: Imported presentation template for personal use
keywords: [imported, presentation, template]
primary_color: "{primary_color}"
canvas_format: ppt169
canvas_width: 1280
canvas_height: 720
canvas_viewbox: "0 0 1280 720"
replication_mode: mirror
native_structure_mode: structured
page_count: {page_count}
---

# Imported Presentation Template - Design Specification

## I. Template Overview

This is a structure-preserving import of the uploaded presentation. The source identity,
page grammar, and native Master/Layout relationships are retained for reuse.

## II. Color Scheme

Primary imported color: `{primary_color}`.

## III. Typography

Typography is retained from the source package for consistent downstream generation.

## IV. Signature Design Elements

Source decorations, imagery, and layout families are preserved in the imported SVG roster.

## V. Page Roster

{roster}
'''
    (template_root / "templates" / "design_spec.md").write_text(content, encoding="utf-8")


def main() -> int:
    source = WORKSPACE / "source.pptx"
    import_root = WORKSPACE / "import"
    template_root = WORKSPACE / "deck"
    if not source.is_file():
        raise RuntimeError("上传的 PPTX 文件不存在")
    if import_root.exists():
        shutil.rmtree(import_root)
    if template_root.exists():
        shutil.rmtree(template_root)
    command = [
        sys.executable,
        str(SKILL_ROOT / "scripts/pptx_template_import.py"),
        str(source),
        "--output",
        str(import_root),
        "--inheritance-mode",
        "both",
    ]
    _run_step("extracting", "正在读取页面、母版和主题信息", command)
    _run_step(
        "authoring_view",
        "正在准备可编辑的模板结构",
        [
            sys.executable,
            str(SKILL_ROOT / "scripts/svg_authoring_view.py"),
            str(import_root / "svg"),
            "-o",
            str(import_root / "authoring-svg"),
            "--projection-kind",
            "layered",
        ],
    )
    # Materialization touches each imported SVG and native payload repeatedly.
    # Work on the container-local tmpfs to avoid Windows bind-mount metadata latency.
    with tempfile.TemporaryDirectory(prefix="pptmaster-template-") as temporary:
        local_root = Path(temporary)
        local_import = local_root / "import"
        local_template = local_root / "deck"
        _emit_progress("materializing", "正在生成可复用模板")
        shutil.copytree(import_root, local_import)
        result = subprocess.run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts/mirror_template_materialize.py"),
                str(local_import),
                str(local_template),
            ],
            cwd=SKILL_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            detail = result.stderr[-3000:] or result.stdout[-3000:] or "模板物化失败"
            raise RuntimeError(detail)
        shutil.copytree(local_template, template_root)
        _emit_progress("materializing", "正在生成可复用模板完成")
    _emit_progress("summarizing", "正在整理模板页数、配色和字体摘要")
    manifest = json.loads((import_root / "manifest.json").read_text(encoding="utf-8"))
    pages = manifest.get("slides") or manifest.get("pages") or []
    theme = manifest.get("theme") or {}
    fonts = manifest.get("fonts") or []
    _write_template_spec(template_root, manifest, len(pages))
    return_data = {
        "page_count": len(pages),
        "preview_files": sorted(str(path.relative_to(WORKSPACE)) for path in template_root.glob("templates/*.svg")),
        "template_root": "deck",
        "colors": theme.get("colors", theme.get("themeColors", [])) if isinstance(theme, dict) else [],
        "fonts": fonts,
        "source_manifest": "import/manifest.json",
    }
    print(json.dumps({"type": "result", "summary": return_data}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
