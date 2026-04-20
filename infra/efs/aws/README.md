# AWS EFS: RWX storage (EFS CSI driver)

Adds **ReadWriteMany (RWX)** volumes on AWS using **EFS** and the **AWS EFS CSI Driver Operator** (Red Hat Operators catalog).

- **GitOps / automated:** from repo root run `uv run ./infra/efs/aws/deploy.py` (see main [README](../../../README.md)). That CLI creates an EFS file system in your VPC using **`kube-system/aws-creds`** (see Prerequisites below), then applies and syncs the Argo CD app **`efs-aws`** that installs the operator subscription, `ClusterCSIDriver`, and a `StorageClass`.
- **List existing `fs-…` IDs without the AWS console:** `./infra/efs/aws/list-efs.sh` (default: EFS with mount targets in the cluster VPC). Use `LIST_EFS_SCOPE=region` to print every EFS in the region.
- **This document:** manual steps if you prefer not to use the deploy CLI, or to troubleshoot.

## Prerequisites

- OpenShift on **AWS** (IPI or equivalent), with `StorageClass` for EBS (e.g. `gp3-csi`) already working.
- Cluster admin (`oc` authenticated).
- **OpenShift GitOps** installed (e.g. `./bootstrap.sh` from repo root).
- `argocd` CLI (for GitOps path).
- AWS API access from the cluster: the bootstrap flow uses the **`aws-creds`** secret in **`kube-system`** (the sandbox/installer `student` admin user), which has `elasticfilesystem:*` and `ec2:*` permissions. The `openshift-machine-api/aws-cloud-credentials` secret is intentionally scoped to EC2/machine operations only and **cannot** create EFS resources.

## Manual option 1: GitOps only (existing EFS)

If you already have an EFS **file system ID** (`fs-...`) with mount targets in the cluster VPC and security groups allowing **NFS TCP 2049** from worker nodes:

1. Ensure OpenShift GitOps is installed.
2. `argocd login ...` (see main README).
3. `oc apply -f gitops/infra/efs-aws.yaml`
4. Set parameters and sync (replace `fs-xxxxxxxx`):

   ```bash
   argocd app set efs-aws \
     -p fileSystemId=fs-xxxxxxxx \
     -p region=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}') \
     -p clusterName=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')
   argocd app sync efs-aws
   ```

5. Verify:

   ```bash
   oc get storageclass efs-csi
   oc get clusterscsidriver efs.csi.aws.com
   ```

## Manual option 2: Full walkthrough (operator + EFS + StorageClass)

Use this when you have **no** AWS console but **do** have cluster-admin and credentials in `openshift-machine-api/aws-cloud-credentials`.

### Step 1 — Install the EFS CSI operator (Subscription + ClusterCSIDriver)

Apply the same resources the Helm chart would render, or use Helm/Argo CD with a **temporary** `fileSystemId` only if you need to render; the chart **fails** Helm if `fileSystemId` is empty, so the practical order is: **create EFS first**, then apply the app (see `deploy.py`), OR install the operator manually:

```yaml
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: aws-efs-csi-driver-operator
  namespace: openshift-cluster-csi-drivers
spec:
  channel: stable
  installPlanApproval: Automatic
  name: aws-efs-csi-driver-operator
  source: redhat-operators
  sourceNamespace: openshift-marketplace
```

Wait until the operator CSV is `Succeeded`, then:

```yaml
apiVersion: operator.openshift.io/v1
kind: ClusterCSIDriver
metadata:
  name: efs.csi.aws.com
spec:
  managementState: Managed
```

```bash
oc get csv -n openshift-cluster-csi-drivers
oc apply -f clusterscsidriver.yaml
```

### Step 3 — Create EFS (AWS CLI inside the cluster)

Run a **short-lived pod** in **`openshift-machine-api`** so the pod can use the **`aws-cloud-credentials`** secret.

**Important:** `oc run` / Jobs that mount the secret often must run in **`openshift-machine-api`** (not `default`), because that is where the secret exists.

Example (interactive — adapt cluster name and region):

```bash
REGION=$(oc get infrastructure cluster -o jsonpath='{.status.platformStatus.aws.region}')
CLUSTER=$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}')

oc run aws-cli-efs -n openshift-machine-api -it --rm --restart=Never \
  --image=amazon/aws-cli:latest \
  --env="AWS_DEFAULT_REGION=${REGION}" \
  --env="CLUSTER_NAME=${CLUSTER}" \
  --overrides='{
    "spec": {
      "containers": [{
        "name": "aws-cli",
        "image": "amazon/aws-cli:latest",
        "stdin": true,
        "tty": true,
        "env": [
          {"name": "AWS_DEFAULT_REGION", "value": "'"${REGION}"'"},
          {"name": "CLUSTER_NAME", "value": "'"${CLUSTER}"'"},
          {"name": "AWS_SHARED_CREDENTIALS_FILE", "value": "/aws/credentials"}
        ],
        "volumeMounts": [{"name": "aws-creds", "mountPath": "/aws", "readOnly": true}]
      }],
      "volumes": [{
        "name": "aws-creds",
        "secret": {"secretName": "aws-cloud-credentials"}
      }]
    }
  }' -- /bin/bash
```

Inside the shell:

1. Discover VPC, subnets, and the **compute security group** (OpenShift IPI tags it `Name=<infrastructureName>-node`, i.e. the same name as `oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}'` plus the suffix `-node`).
2. `aws efs create-file-system` with tags, `aws efs wait file-system-available`.
3. Create a dedicated security group for EFS, allow **ingress TCP 2049** from the **worker** security group.
4. `aws efs create-mount-target` for **each** subnet used by workers (same AZs as the cluster).

Record the **File system ID** (`fs-...`).

**Note:** If `AWS_SHARED_CREDENTIALS_FILE=/aws/credentials` fails, inspect the secret keys: some clusters expose `aws_access_key_id` / `aws_secret_access_key` only. In that case export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from those files or use a helper script.

### Step 4 — StorageClass

Replace `fs-xxxxxxxx`:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: efs-csi
provisioner: efs.csi.aws.com
parameters:
  provisioningMode: efs-ap
  fileSystemId: fs-xxxxxxxx
  directoryPerms: "700"
reclaimPolicy: Delete
volumeBindingMode: Immediate
```

### Step 5 — Verify with a PVC

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: efs-rwx-test
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-csi
  resources:
    requests:
      storage: 1Gi
```

```bash
oc get pvc efs-rwx-test
```

## Troubleshooting

| Symptom | Things to check |
|--------|------------------|
| PVC stuck `Pending` | CSI controller logs; EFS mount targets in correct subnets; security group allows 2049 from worker nodes. |
| `AccessDenied` in Job logs | IAM rights for EFS/EC2 on the user behind `aws-cloud-credentials`. |
| Helm / Argo CD: `fileSystemId is required` | Set `-p fileSystemId=fs-...` before sync (or run `uv run ./infra/efs/aws/deploy.py`). |
| Operator not installing | `Subscription` status; `InstallPlan` in `openshift-cluster-csi-drivers`; catalog `redhat-operators` reachable. |

## Teardown

To remove everything (AWS EFS resources **and** the Argo CD app / StorageClass):

```bash
EFS_FILE_SYSTEM_ID=fs-xxxxxxxx ./infra/efs/aws/teardown.sh
```

What it removes:

| Resource | Where |
|---|---|
| EFS mount targets | AWS (waits for deletion) |
| EFS NFS security group | AWS |
| EFS filesystem | AWS |
| Argo CD `Application` `efs-aws` | OpenShift (cascade‑deletes StorageClass, ClusterCSIDriver, Subscription) |

To remove only the OpenShift side and leave the EFS filesystem intact (useful if you will redeploy):

```bash
SKIP_AWS_TEARDOWN=true ./infra/efs/aws/teardown.sh
```

---

## Cost / production notes

- EFS bills for storage and throughput; latency is higher than EBS — use for shared files, not database disks.
- `efs-ap` mode uses **access points** per PVC (good for multi-tenant isolation on one file system).
- For production, prefer **Regional** EFS and tighten security groups; for demos, **One Zone** may cost less.

## See also

- Main repo [README](../../../README.md) — EFS quick start via `uv run ./infra/efs/aws/deploy.py`
