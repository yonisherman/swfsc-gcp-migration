import os
from itertools import groupby
from pathlib import Path

import yaml


top_chapters = [
    {"text": "Overview", "href": "index.qmd"},
    {
        "part": "Cloud Setup",
        "chapters": [
            "gcp-setup.qmd",
            "gcp-infrastructure.qmd",
            "gcp-vm-access.qmd",
            "gcp-erddap-vm.qmd",
            "gcp-processing-vm.qmd",
            "gcp-migration-recovery.qmd",
            "gcp-templates.qmd",
        ],
    },
    {
        "part": "Workflows",
        "chapters": [
            "mh1-processor.qmd",
            "viirs-netpp.qmd",
            "mur-sst.qmd",
            "charm.qmd",
        ],
    },
    {
        "part": "Presentations",
        "chapters": [
            "erddap-cloud-migration-presentations.qmd",
        ],
    },
]


def discover_code_pages(root_dir: str = "code-pages") -> list[str]:
    """Return all generated code-page QMD files, sorted by path."""
    code_pages: list[str] = []

    for root, _dirs, files in os.walk(root_dir):
        for filename in sorted(files):
            if filename.endswith(".qmd"):
                path = os.path.join(root, filename).replace("\\", "/")
                code_pages.append(path)

    return sorted(code_pages)


def build_code_parts(code_pages: list[str]) -> list[dict]:
    """Group generated code pages into sidebar parts by workflow folder."""
    code_parts: list[dict] = []

    for folder, files in groupby(code_pages, key=lambda p: Path(p).parent.name):
        code_parts.append(
            {
                "part": f"Code: {folder}",
                "chapters": list(files),
            }
        )

    return code_parts


def main() -> None:
    """Update _quarto.yml with fixed chapters plus discovered code pages."""
    code_pages = discover_code_pages()
    new_chapters = top_chapters + build_code_parts(code_pages)

    with open("_quarto.yml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if config["book"].get("chapters") == new_chapters:
        print("No changes to _quarto.yml")
        return

    config["book"]["chapters"] = new_chapters

    # The chapter list is generated dynamically, so avoid keeping a stale
    # project.render list after pages are split, renamed, or regenerated.
    config["book"].pop("render", None)
    config.get("project", {}).pop("render", None)

    with open("_quarto.yml", "w", encoding="utf-8") as f:
        yaml.dump(
            config,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"Updated _quarto.yml: {len(code_pages)} code pages added to chapters")


if __name__ == "__main__":
    main()