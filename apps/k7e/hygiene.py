"""Garden hygiene auditor — checks structural integrity of knowledge nodes."""

import re
import time
from pathlib import Path

import engine as garden


def run_audit(fix=False):
    """Audit garden for structural issues. Returns list of issues found."""
    garden.init()
    nodes = list(garden.NODES_DIR.glob("KG-*.md"))
    mocs = list(garden.MOCS_DIR.glob("*.md"))
    assets = [f for f in garden.ASSETS_DIR.glob("*.*") if f.name != ".gitkeep"]

    node_ids = {n.stem for n in nodes}
    tag_to_nodes = {}
    referenced_assets = set()
    issues = []

    for node_path in nodes:
        text = node_path.read_text(encoding="utf-8")
        meta = garden._parse_frontmatter(text)
        node_id = node_path.stem

        required = ["id", "title", "status", "last_tended", "tags"]
        missing = [f for f in required if f not in meta]
        if missing:
            issues.append(f"[{node_id}] Missing fields: {', '.join(missing)}")

        tags = meta.get("tags", [])
        if not tags:
            issues.append(f"[{node_id}] No tags assigned")
        for tag in tags:
            tag_to_nodes.setdefault(tag, []).append(node_id)

        for link in re.findall(r"\[\[(KG-\d+)\]\]", text):
            if link not in node_ids:
                issues.append(f"[{node_id}] Dead link to [[{link}]]")

        for ref in re.findall(r"assets/([a-f0-9]+\.[a-z0-9]+)", text):
            referenced_assets.add(ref)

    moc_tags = {m.stem for m in mocs}
    for tag in tag_to_nodes:
        if tag not in moc_tags:
            issues.append(f"[Tag: {tag}] No MOC file exists")
            if fix:
                garden._update_mocs(
                    tag_to_nodes[tag][0],
                    garden._parse_frontmatter(
                        (garden.NODES_DIR / f"{tag_to_nodes[tag][0]}.md").read_text(encoding="utf-8")
                    ).get("title", ""),
                    [tag]
                )

    for asset in assets:
        if asset.name not in referenced_assets:
            issues.append(f"[Asset: {asset.name}] Unreferenced")
            if fix:
                asset.unlink()

    return issues
