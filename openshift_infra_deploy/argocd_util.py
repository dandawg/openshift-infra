import base64
import subprocess
import time
from pathlib import Path

from openshift_infra_deploy.subprocess_util import run_cmd

_ARGOCD_OP_IN_PROGRESS = 20


def argocd_admin_login() -> str:
    password_raw = run_cmd(
        [
            "oc",
            "get",
            "secret/openshift-gitops-cluster",
            "-n",
            "openshift-gitops",
            "-o",
            "jsonpath={.data.admin\\.password}",
        ],
        capture_output=True,
    ).stdout.strip()
    password = base64.b64decode(password_raw).decode()
    server = run_cmd(
        [
            "oc",
            "get",
            "route",
            "openshift-gitops-server",
            "-n",
            "openshift-gitops",
            "-o",
            "jsonpath={.spec.host}",
        ],
        capture_output=True,
    ).stdout.strip()
    if not server:
        raise RuntimeError(
            "OpenShift GitOps not found. Please install OpenShift GitOps first."
        )
    run_cmd(
        [
            "argocd",
            "login",
            server,
            "--username",
            "admin",
            "--password",
            password,
            "--insecure",
            "--grpc-web",
            "--skip-test-tls",
        ],
        capture_output=True,
    )
    return server


def argocd_admin_login_verbose() -> str:
    """Login suitable for EFS; surfaces argocd stderr on failure."""
    password_raw = run_cmd(
        [
            "oc",
            "get",
            "secret/openshift-gitops-cluster",
            "-n",
            "openshift-gitops",
            "-o",
            "jsonpath={.data.admin\\.password}",
        ],
        capture_output=True,
    ).stdout.strip()
    password = base64.b64decode(password_raw).decode()
    server = run_cmd(
        [
            "oc",
            "get",
            "route",
            "openshift-gitops-server",
            "-n",
            "openshift-gitops",
            "-o",
            "jsonpath={.spec.host}",
        ],
        capture_output=True,
    ).stdout.strip()
    if not server:
        raise RuntimeError("OpenShift GitOps not found. Run ./bootstrap.sh (repo root) first.")
    try:
        run_cmd(
            [
                "argocd",
                "login",
                server,
                "--username",
                "admin",
                "--password",
                password,
                "--insecure",
                "--grpc-web",
                "--skip-test-tls",
            ],
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        hints = (
            f"Error: argocd login failed for {server}\n{err}\n\n"
            "Hints: oc get pods -n openshift-gitops; "
            f'reachability: curl -skI "https://{server}/"\n'
            "  Admin password: oc get secret openshift-gitops-cluster -n openshift-gitops "
            "-o jsonpath='{.data.admin\\\\.password}' | base64 -d"
        )
        raise RuntimeError(hints) from e
    return server


def ensure_argocd_cli() -> None:
    from shutil import which

    if which("argocd") is None:
        raise RuntimeError(
            "argocd CLI not found in PATH.\n"
            "  Install: https://argo-cd.readthedocs.io/en/stable/cli_installation/"
        )


def application_exists(app_name: str) -> bool:
    try:
        run_cmd(
            ["oc", "get", "application", app_name, "-n", "openshift-gitops"],
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def oc_apply_gitops_manifest(path: Path) -> None:
    run_cmd(["oc", "apply", "-f", str(path)])


def argocd_app_wait_operation(app_name: str, *, timeout: int = 120) -> None:
    """Block until any running operation on the app completes (or timeout expires)."""
    run_cmd(
        ["argocd", "app", "wait", app_name, "--operation", f"--timeout={timeout}"],
        capture_output=True,
    )


def argocd_app_set_params(
    app_name: str,
    params: list[tuple[str, str]],
    *,
    retries: int = 5,
    retry_delay: float = 5.0,
) -> None:
    """Set Helm parameters on an Argo CD app, retrying on exit 20 (operation in progress)."""
    args: list[str] = ["argocd", "app", "set", app_name]
    for k, v in params:
        args.extend(["-p", f"{k}={v}"])
    for attempt in range(retries):
        try:
            run_cmd(args, capture_output=True)
            return
        except subprocess.CalledProcessError as e:
            if e.returncode == _ARGOCD_OP_IN_PROGRESS and attempt < retries - 1:
                try:
                    argocd_app_wait_operation(app_name)
                except subprocess.CalledProcessError:
                    time.sleep(retry_delay)
                continue
            raise


def argocd_app_sync(app_name: str, *, silent: bool = True) -> None:
    args = ["argocd", "app", "sync", app_name]
    run_cmd(args, capture_output=silent)


def argocd_app_enable_autosync(app_name: str) -> None:
    run_cmd(
        [
            "argocd",
            "app",
            "set",
            app_name,
            "--sync-policy",
            "automated",
            "--auto-prune",
            "--self-heal",
        ],
        capture_output=True,
    )
