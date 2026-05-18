import os
import yaml
from itertools import groupby
from pathlib import Path

# Auto-discover all code-page qmds
code_pages = []
for root, dirs, files in os.walk("code-pages"):
    for f in sorted(files):
        if f.endswith(".qmd"):
            path = os.path.join(root, f).replace("\\", "/")
            code_pages.append(path)

# Group by workflow (subfolder name)
parts = []
key = lambda p: Path(p).parent.name
for folder, files in groupby(sorted(code_pages), key=key):
    parts.append({
        "part": folder,
        "chapters": list(files)
    })

new_chapters = ["index.qmd", "gcp-setup.qmd", "gcp-templates.qmd", "mh1-processor.qmd",
                "viirs-netpp.qmd", "mur-sst.qmd", "charm.qmd", "erddap-cloud-migration-presentations.qmd"] + parts

# Load current _quarto.yml
with open("_quarto.yml") as f:
    config = yaml.safe_load(f)

# Only write if chapters have changed
if config["book"]["chapters"] != new_chapters:
    config["book"]["chapters"] = new_chapters

    # Use ruamel.yaml to preserve key order and formatting
    try:
        from ruamel.yaml import YAML
        ryaml = YAML()
        ryaml.preserve_quotes = True
        with open("_quarto.yml") as f:
            doc = ryaml.load(f)
        doc["book"]["chapters"] = new_chapters
        with open("_quarto.yml", "w") as f:
            ryaml.dump(doc, f)
    except ImportError:
        # fallback to pyyaml if ruamel not available
        with open("_quarto.yml", "w") as f:
            yaml.dump(config, f, default_flow_style=False,
                     allow_unicode=True, sort_keys=False)

    print(f"Updated _quarto.yml with {len(code_pages)} code pages")
else:
    print("No changes to _quarto.yml")