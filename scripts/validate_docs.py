#!/usr/bin/env python3
"""docs 体系校验闸门（validate_docs.py）

用法:
    python scripts/validate_docs.py [docs目录] [--quiet]

检查项:
    1. frontmatter 必填字段: title / type / status / date / updated
    2. 文件名前缀匹配类型: PRD-<语义> / DESIGN-<语义> / ADR-NNN-<语义> / PROGRESS-日期-<语义> / RETRO-<语义>
       （语义段必须存在且非纯数字/日期）
    3. ADR 编号: 递增且不重复
    4. 必填章节: 按 frontmatter type 对照模板固定章节
    5. 内部链接: 相对链接目标存在
    6. 文档地图登记: 每份正式文档必须在 docs/README.md 地图中有一行
    7. tech-index 登记: 每份 TECH 笔记必须在 docs/06-知识/README.md 台账中有一行

退出码: 0=全 PASS, 1=有 FAIL。可接入 pre-commit 或提交前手动跑。
"""
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK，无法编码 ✗ 等符号 → 统一按 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ---- 类型闭集（与 docs-guide.md 分类表保持一致）----
TYPES = {
    "prd":      {"prefix": "PRD-",      "sections": ["## 3. 验收标准", "## 4. 风险清单"]},
    "design":   {"prefix": "DESIGN-",   "sections": ["## 1. 目标与约束", "## 4. 接口定义", "## 5. 非功能需求"]},
    "adr":      {"prefix": "ADR-",      "sections": ["## 背景", "## 决策", "## 被否方案", "## 后果"]},
    "progress": {"prefix": "PROGRESS-", "sections": ["## 已完成"]},
    "retro":    {"prefix": "RETRO-",    "sections": ["## 3. 结果", "## 4. 可复现命令"]},
    "tech":     {"prefix": "TECH-",     "sections": ["## 2. 技术要点（MUST）", "## 3. 操作步骤（MUST）", "## 5. 亮点与代码锚点（MUST）"]},
    "spec":     {"prefix": "SPEC-",     "sections": ["## 1. 文档目的"]},
    "eval":     {"prefix": "EVAL-",     "sections": ["## 1. 文档目的"]},
}
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
FM_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
SKIP_DIRS = {"_generated", "_templates", "_inbox", "coverage_html", ".git", "node_modules"}
# 制度文件白名单：不按五类文档检查（docs-guide 宪法、文档地图）
SKIP_FILES = {"docs-guide.md", "README.md"}
LINK_RE = re.compile(r"\]\((\.{1,2}/[^)#\s]+)\)")


def is_docs_type_doc(path: Path) -> bool:
    """只检查已注册类型文档：文件名有类型前缀，或 frontmatter type 在 TYPES 内"""
    name = path.name
    if re.match(r"^(PRD|DESIGN|ADR-\d{3}|PROGRESS-\d{4}-\d{2}-\d{2}|RETRO|TECH)-", name):
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)
    return fm.get("type", "").lower() in TYPES


def parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    return {k: v.strip() for k, v in FM_FIELD_RE.findall(m.group(1))}


def _semantic_tail_ok(tail: str) -> bool:
    """语义段合法：非空、非纯数字、非纯日期（文件名必须能看出内容）"""
    t = tail.strip("-_ ")
    if t.lower().endswith(".md"):
        t = t[:-3].rstrip("-_ ")
    if not t:
        return False
    if t.isdigit():
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        return False
    return True


def check_file(path: Path, rel: Path) -> list[str]:
    """返回该文件的问题列表，空列表 = PASS"""
    issues = []
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)

    # 1. frontmatter 必填字段
    for field in ("title", "type", "status", "date", "updated"):
        if field not in fm:
            issues.append(f"缺 frontmatter 字段: {field}")

    doc_type = fm.get("type", "").lower()
    if doc_type not in TYPES:
        if fm:
            issues.append(f"type 不在类型闭集内: {doc_type!r}（允许: {', '.join(TYPES)}）")
        return issues

    spec = TYPES[doc_type]
    name = path.name

    # 2. 文件名前缀匹配（类型前缀 + 中文语义段，禁止纯数字/日期作语义）
    if doc_type == "progress":
        m = re.match(r"^PROGRESS-(\d{4}-\d{2}-\d{2})-(.+)$", name)
        ok_prefix = bool(m) and _semantic_tail_ok(m.group(2))
    elif doc_type == "adr":
        m = re.match(r"^ADR-(\d{3})-(.+)$", name)
        ok_prefix = bool(m) and _semantic_tail_ok(m.group(2))
    else:
        m = re.match(rf"^{spec['prefix']}(.+)$", name)
        ok_prefix = bool(m) and _semantic_tail_ok(m.group(1))
    if not ok_prefix:
        issues.append(
            f"文件名不符合命名规则: 应为 {spec['prefix']}<语义短语>.md"
            + "（ADR 用 ADR-NNN-，PROGRESS 用 PROGRESS-日期-；语义段用中文短语，禁止纯数字/日期）"
        )

    # 3. 必填章节
    for sec in spec["sections"]:
        if sec not in text:
            issues.append(f"缺必填章节: {sec}")

    # 4. 内部链接目标存在
    for m in LINK_RE.finditer(text):
        target = m.group(1)
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            issues.append(f"失效链接: {target}")

    return issues


def check_adr_numbering(docs_root: Path) -> list[str]:
    """ADR 编号递增且不重复"""
    issues = []
    adrs = sorted(docs_root.glob("**/ADR-*.md"))
    numbers = []
    for p in adrs:
        m = re.match(r"ADR-(\d+)", p.name)
        if m:
            numbers.append(int(m.group(1)))
    if numbers != sorted(set(numbers)):
        issues.append(f"ADR 编号重复: {numbers}")
    if numbers != sorted(numbers):
        issues.append(f"ADR 编号未递增: {numbers}")
    # 编号应连续从 1 开始（容忍缺失，仅提示）
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        issues.append(f"ADR 编号不连续（缺失编号）: {sorted(numbers)}")
    return issues


def _table_first_cells(table_path: Path, header_names: tuple[str, ...]) -> list[str] | None:
    """解析 markdown 表格第一列；文件不存在返回 None"""
    if not table_path.exists():
        return None
    cells = []
    for line in table_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if not cols:
            continue
        # 跳过表头行（第一列恰为列名）与分隔行（---）；
        # 用精确匹配而非子串包含，避免 PRD-文档体系.md 这类文档名被误判为表头
        if cols[0] in header_names or set(cols[0]) <= set("-: "):
            continue
        cells.append(cols[0])
    return cells


def check_map_registration(docs_root: Path) -> list[str]:
    """每份正式文档（五类 + tech）必须在 docs/README.md 文档地图中登记一行"""
    cells = _table_first_cells(docs_root / "README.md", ("文档", "文件", "名称", "文档名"))
    if cells is None:
        return ["docs/README.md 文档地图缺失：每份正式文档必须在此登记一行"]

    unregistered = []
    for path in sorted(docs_root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if not is_docs_type_doc(path):
            continue
        # tech 笔记登记到 tech-index 台账（06-知识/README.md），不查 docs 地图
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type", "").lower() == "tech":
            continue
        rel = path.relative_to(docs_root).as_posix()
        if not any(path.name in cell or rel in cell for cell in cells):
            unregistered.append(rel)
    if unregistered:
        return [f"未在 docs/README.md 文档地图登记: {', '.join(unregistered)}（每份正式文档一行：文档名/类型/状态/定位）"]
    return []


def check_tech_index_registration(docs_root: Path) -> list[str]:
    """每份 TECH 笔记必须在 docs/06-知识/README.md tech-index 台账登记一行"""
    cells = _table_first_cells(docs_root / "06-知识" / "README.md", ("笔记", "技术", "文档"))
    if cells is None:
        return ["docs/06-知识/README.md tech-index 台账缺失：每份 TECH 笔记必须在此登记一行"]

    unregistered = []
    for path in sorted(docs_root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if not is_docs_type_doc(path):
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type", "").lower() != "tech":
            continue
        rel = path.relative_to(docs_root).as_posix()
        if not any(path.name in cell or rel in cell for cell in cells):
            unregistered.append(rel)
    if unregistered:
        return [f"未在 docs/06-知识/README.md tech-index 台账登记: {', '.join(unregistered)}（每份 TECH 笔记一行：笔记/技术/日期/解决什么问题/亮点/难点）"]
    return []


def main() -> int:
    docs_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs")
    quiet = "--quiet" in sys.argv
    if not docs_root.exists():
        print(f"FAIL: docs 目录不存在: {docs_root}")
        return 1

    all_issues: list[tuple[str, list[str]]] = []
    for path in sorted(docs_root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if not is_docs_type_doc(path):
            continue  # 扩展类/非五类文档不检查
        rel = path.relative_to(docs_root)
        issues = check_file(path, rel)
        if issues:
            all_issues.append((str(rel), issues))

    adr_issues = check_adr_numbering(docs_root)
    if adr_issues:
        all_issues.append(("(ADR 编号)", adr_issues))

    map_issues = check_map_registration(docs_root)
    if map_issues:
        all_issues.append(("(文档地图)", map_issues))

    tech_issues = check_tech_index_registration(docs_root)
    if tech_issues:
        all_issues.append(("(tech-index)", tech_issues))

    if not all_issues:
        print("PASS: 全部文档合规")
        return 0
    print(f"FAIL: {len(all_issues)} 个文件/检查点有问题")
    for name, issues in all_issues:
        print(f"  ✗ {name}")
        for i in issues:
            print(f"      - {i}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
