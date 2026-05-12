# Cloud Run Container Template

This repository is a reusable template for organizing, building, and deploying Docker containers on **Google Cloud Run**.

## Directory Organization

To make effective use of cloud resources and to ensure that data-processing workflows are transparent, reproducible, and transferable, the following directory structure is recommended:

```text
.
├── config/        # Configuration files (YAML, requirements, etc.)
│   └── config.yml
├── scripts/       # Data processing / execution scripts
│   └── example.py
│
├── src/           # Shared Python library code
│   └── roylib.py
│
├── templates/     # NetCDF / CDL / config templates
│   └── example.cdl
│
├── Dockerfile     # Container definition
│
├── entrypoint.sh  # Container entrypoint
│
├── README.md
│
├── CHANGELOG.md
│
└── LICENSE
