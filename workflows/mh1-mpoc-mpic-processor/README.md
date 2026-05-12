## Template for data processing workflow
 
This repository is a **template for data processing workflows**. 

<img width="804" height="268" alt="image" src="https://github.com/user-attachments/assets/502b6c77-2264-4ee3-87ec-4d91090f49a1" />

### Directory Organization

To make the best use of cloud resources and to ensure that data processing workflows are transparent, reproducible, and transferable,  
the following workflow and directory structures are recommended.

Within each subgroup’s project directory, the recommended organization is:

```plaintext
/home/polarwatch/seaice123/
├── README.md                 # Project overview and setup instructions
├── environment.yml           # Conda environment file (or requirements.txt)
├── config.yml                # Configuration file (or config.ini)
├── LICENSE                   # data usage license (ex: CC-1.0)
│
├── notebooks/                # Jupyter notebooks for exploration / analysis
│   ├── 01_exploration.ipynb
│   └── 02_analysis.ipynb
│
├── scripts/                  # Scripts for running workflows
│   ├── example.sh
│   └── example.py
│
├── src/                      # Reusable Python modules and helpers
│   ├── __init__.py
│   ├── helper.py
│   └── utils.py
│
├── data/                     # In-process or intermediate data
│   └── in-process.nc
│
├── docs/                     # Documentation
│   ├── quarto-docs/
│   └── sphinx/
│
├── tests/                      # Tests
│   ├── test1.py
│   └── test2.py
│
├── templates/                      # Template files
│   ├── example.cdl
│   └── example.csv
│
├── resources/                    # Misc text files viewd during processing
│   └── fileNames.txt  # (e.g. roylib.py url_lines function)
│
├── logs/                     # Runtime logs
│   └── logs.txt
│
└── ...                       # Other project-specific directories or files

/mnt/gcs/data/seaice123/       # Mounted bucket for large files
/mnt/gcs/erddap_data_pw/seaice123/ # Mounted bucket for ERDDAP data

```

###  Helplful Notes
- Use **`environment.yml`** (or `requirements.txt`) to capture dependencies for reproducibility.  
- Keep **large raw and archived datasets** in shared storage (e.g., mounted buckets), not inside each project directory.  
- Organize **code (`src/`) vs scripts (`scripts/`) vs notebooks (`notebooks/`) vs temporary data (`data/`)** clearly to keep the project clean and maintainable.
- Version-control each project directory 
