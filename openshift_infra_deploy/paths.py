from pathlib import Path


def openshift_infra_root() -> Path:
    """Directory that contains pyproject.toml and gitops/."""
    return Path(__file__).resolve().parents[1]
