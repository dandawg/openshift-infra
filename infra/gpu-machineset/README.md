# GPU MachineSets

Kubernetes MachineSet definitions for deploying GPU-enabled nodes on OpenShift.

## Overview

GPU nodes are required for:
- AI model inference (serving)
- Model training
- GPU-accelerated workloads

This directory provides parameterized MachineSet templates for AWS with various GPU instance types, supporting multi-GPU deployments.

## Structure

```
gpu-machineset/
├── base/                  # Base MachineSet template
└── aws/                   # AWS configurations
    ├── deploy.sh         # Automated deployment script
    ├── helm/             # Helm chart for ArgoCD deployment
    │   ├── Chart.yaml
    │   ├── values.yaml
    │   ├── values-g4dn-xlarge.yaml
    │   ├── values-g6-2xlarge.yaml
    │   ├── values-g6-4xlarge.yaml
    │   └── templates/
    │       └── machineset.yaml
    └── overlays/         # Kustomize overlays (advanced)
        ├── g4dn-xlarge/
        ├── g6-2xlarge/
        └── g6-4xlarge/
```

## Quick Start

### 1. Choose Your Instance Type

See cloud-specific READMEs:
- [AWS GPU instances](aws/README.md) - Complete details on g4dn and g6 instances

### 2. Deploy CPU Workers (Optional)

For clusters starting with only master nodes, deploy CPU workers first:

```bash
# Deploy m6a.4xlarge worker (default)
./infra/gpu-machineset/aws/deploy-cpu-worker.sh

# Deploy with custom settings
INSTANCE_TYPE=m6a.4xlarge \
ROOT_VOLUME_SIZE=150 \
REPLICA_COUNT=2 \
./infra/gpu-machineset/aws/deploy-cpu-worker.sh
```

**CPU Worker Configuration Options:**
- `INSTANCE_TYPE` - CPU instance type (default: `m6a.4xlarge`)
- `ROOT_VOLUME_SIZE` - Root volume size in GB (default: `120`)
- `ROOT_VOLUME_TYPE` - Volume type: `gp3` or `gp2` (default: `gp3`)
- `ROOT_VOLUME_IOPS` - IOPS for gp3 volumes (default: `3000`)
- `REPLICA_COUNT` - Number of CPU worker nodes to create (default: `1`)

### 3. Deploy GPU Workers via Automated Script (Recommended)

Use the deployment script to automatically configure and deploy:

```bash
# Deploy with default settings (g6.2xlarge, 120GB gp3 volume)
./infra/gpu-machineset/aws/deploy.sh

# Deploy g4dn.xlarge (most cost-effective)
INSTANCE_TYPE=g4dn.xlarge \
ROOT_VOLUME_SIZE=100 \
./infra/gpu-machineset/aws/deploy.sh

# Deploy g6.2xlarge with custom storage
INSTANCE_TYPE=g6.2xlarge \
ROOT_VOLUME_SIZE=200 \
ROOT_VOLUME_TYPE=gp3 \
ROOT_VOLUME_IOPS=5000 \
./infra/gpu-machineset/aws/deploy.sh

# Deploy g6.4xlarge for high-performance workloads
INSTANCE_TYPE=g6.4xlarge ./infra/gpu-machineset/aws/deploy.sh

# Deploy with multiple replicas (default is 1)
INSTANCE_TYPE=g6.2xlarge \
REPLICA_COUNT=3 \
./infra/gpu-machineset/aws/deploy.sh
```

**GPU Worker Configuration Options:**
- `INSTANCE_TYPE` - GPU instance type (default: `g6.2xlarge`)
- `ROOT_VOLUME_SIZE` - Root volume size in GB (default: `120`)
- `ROOT_VOLUME_TYPE` - Volume type: `gp3` or `gp2` (default: `gp3`)
- `ROOT_VOLUME_IOPS` - IOPS for gp3 volumes (default: `3000`)
- `REPLICA_COUNT` - Number of GPU nodes to create (default: `1`)

**Available instance types:**
- `g4dn.xlarge` - 1x T4 GPU, most cost-effective (~$0.53/hr)
- `g6.2xlarge` - 1x L4 GPU, recommended for most workloads (~$1.10/hr)
- `g6.4xlarge` - 1x L4 GPU, high-performance (~$2.15/hr)

The script will:
1. Gather your cluster information (name, region, AZ, AMI) from master or worker nodes
2. Login to ArgoCD
3. Create the ArgoCD Application
4. Set cluster-specific Helm parameters
5. Sync the application to deploy the MachineSet

### 4. Deploy Multiple GPU Types

You can deploy multiple GPU instance types for different workloads:

```bash
# Deploy cost-effective g4dn for embedding models
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# Also deploy g6.2xlarge for production inference
INSTANCE_TYPE=g6.2xlarge ./infra/gpu-machineset/aws/deploy.sh

# And deploy g6.4xlarge for vision models
INSTANCE_TYPE=g6.4xlarge ./infra/gpu-machineset/aws/deploy.sh
```

Each instance type creates a uniquely named MachineSet to avoid conflicts:
- g4dn.xlarge: `{clusterName}-gpu-g4dn-{az}` (e.g., `cluster-abc-gpu-g4dn-us-east-2a`)
- g6.2xlarge: `{clusterName}-gpu-g6-{az}` (e.g., `cluster-abc-gpu-g6-us-east-2a`)
- g6.4xlarge: `{clusterName}-gpu-g6-4x-{az}` (e.g., `cluster-abc-gpu-g6-4x-us-east-2a`)

### 5. Verify Deployment

```bash
# Watch MachineSet creation
oc get machineset -n openshift-machine-api -w

# View all GPU machines
oc get machine -n openshift-machine-api -l gpu-node=true

# View machines by instance type
oc get machine -n openshift-machine-api -l gpu-instance-type=g4dn.xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.2xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.4xlarge

# Or by suffix
oc get machine -n openshift-machine-api -l gpu-type-suffix=g4dn
oc get machine -n openshift-machine-api -l gpu-type-suffix=g6

# Wait for GPU node to be ready (5-10 minutes)
oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s

# Verify GPU nodes
oc get nodes -l nvidia.com/gpu.present=true

# View nodes by instance type
oc get nodes -l gpu-instance-type=g4dn.xlarge
oc get nodes -l gpu-instance-type=g6.2xlarge
oc get nodes -l gpu-instance-type=g6.4xlarge

# Check GPU detection on a specific node
oc describe node <gpu-node-name> | grep -i gpu
```

### 6. Delete GitOps-Deployed MachineSets

To remove GPU machinesets that were deployed via the scripts and ArgoCD:

```bash
# Delete specific GPU instance type deployment
# This removes the ArgoCD Application and the MachineSet

# Delete g4dn.xlarge deployment
oc delete application gpu-machineset-aws-g4dn-xlarge -n openshift-gitops

# Delete g6.2xlarge deployment
oc delete application gpu-machineset-aws-g6 -n openshift-gitops

# Delete g6.4xlarge deployment
oc delete application gpu-machineset-aws-g6-4xlarge -n openshift-gitops
```

**What happens when you delete the Application:**
- ArgoCD Application is removed from openshift-gitops namespace
- The MachineSet is automatically deleted (due to auto-prune)
- Machines (EC2 instances) are terminated
- Nodes are removed from the cluster

**Alternative: Delete MachineSet directly (keeps ArgoCD App)**

If you want to remove the MachineSet but keep the ArgoCD Application for later redeployment:

```bash
# Scale to zero first (optional, for graceful shutdown)
oc scale machineset <machineset-name> --replicas=0 -n openshift-machine-api

# Wait for machines to terminate, then delete the MachineSet
oc delete machineset <machineset-name> -n openshift-machine-api
```

**Note:** If the ArgoCD Application remains with auto-sync enabled, it will recreate the MachineSet. To prevent this, either:
1. Delete the Application (recommended)
2. Disable auto-sync first: `argocd app set <app-name> --sync-policy none`

**Verify deletion:**

```bash
# Check ArgoCD Applications
oc get applications -n openshift-gitops | grep gpu-machineset

# Check MachineSets
oc get machineset -n openshift-machine-api | grep gpu

# Check Machines (should show terminating/none)
oc get machine -n openshift-machine-api -l gpu-node=true

# Verify nodes are removed
oc get nodes -l nvidia.com/gpu.present=true
```

## GPU Node Configuration

All GPU nodes are configured with:

**Labels:**
- `node-role.kubernetes.io/gpu=""` - GPU node role
- `nvidia.com/gpu.present="true"` - GPU detection label
- `gpu-instance-type={instanceType}` - The EC2 instance type (e.g., `g4dn.xlarge`, `g6.2xlarge`)
- `gpu-type-suffix={suffix}` - The short suffix (e.g., `g4dn`, `g6`, `g6-4x`)

**Taints:**
- `nvidia.com/gpu=true:NoSchedule` - Ensures only GPU workloads schedule on GPU nodes

**AWS Instance Tags:**
- `gpu-node=true` - Marks this as a GPU node
- `gpu-instance-type={instanceType}` - The instance type
- `gpu-type={suffix}` - The short suffix

## Scheduling Workloads on GPU Nodes

To schedule a pod on GPU nodes:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gpu-pod
spec:
  nodeSelector:
    nvidia.com/gpu.present: "true"
    # Optional: target specific GPU type
    # gpu-instance-type: g4dn.xlarge
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
  containers:
    - name: app
      image: myapp:latest
      resources:
        limits:
          nvidia.com/gpu: 1  # Request 1 GPU
```

## NVIDIA GPU Operator

For GPU support on OpenShift, you need the NVIDIA GPU Operator. This is typically installed as part of RHOAI deployment.

The operator provides:
- GPU drivers
- CUDA runtime
- GPU device plugin
- GPU monitoring (DCGM)

## Cost Management

### Best Practices

1. **Scale Down When Not in Use**
   ```bash
   # Scale down specific GPU type
   oc scale machineset <machineset-name> --replicas=0 -n openshift-machine-api
   
   # Or use labels to target specific types
   oc scale machineset $(oc get machineset -n openshift-machine-api -l gpu-instance-type=g6.2xlarge -o name) --replicas=0 -n openshift-machine-api
   ```
   
   **Note:** ArgoCD ignores replica count changes, so your scaling adjustments will persist even after ArgoCD syncs.

2. **Choose Right Instance Size**
   - g4dn.xlarge ($0.53/hr) - cost-effective for small models and embeddings
   - g6.2xlarge ($1.10/hr) - recommended for most workloads
   - g6.4xlarge ($2.15/hr) - for large models and high throughput

3. **Consider Spot Instances**
   - 60-70% cost savings
   - Good for non-critical workloads
   - Can be configured in the Helm values

4. **Monitor Usage**
   - Set up billing alerts in AWS
   - Use cluster autoscaler for dynamic scaling
   - Monitor GPU utilization with DCGM metrics

### Cost Comparison

| Cloud | Instance | GPUs | $/hour (approx) | Best For |
|-------|----------|------|-----------------|----------|
| AWS | g4dn.xlarge | 1x T4 | $0.53 | Cost-effective, embeddings, small models |
| AWS | g6.2xlarge | 1x L4 | $1.10 | Production, single model inference |
| AWS | g6.4xlarge | 1x L4 | $2.15 | Large models, vision, high throughput |

*Prices are approximate On-Demand rates and vary by region*

### Model Recommendations by Instance

- **g4dn.xlarge**: Qwen3-VL-Embedding-2B, sentence-transformers, small 7B models
- **g6.2xlarge**: Granite 7B, Qwen3-VL-4B, general inference workloads
- **g6.4xlarge**: Qwen3-VL-8B, Llama 3 8B, vision models, high-throughput scenarios

## Troubleshooting

### MachineSet Created but No Machines

```bash
oc describe machineset <name> -n openshift-machine-api
```

Check for:
- Invalid instance type
- AZ/region mismatch
- Missing IAM permissions
- AWS quota limits

### Machine Provisioning Failed

```bash
oc describe machine <name> -n openshift-machine-api
```

Common causes:
- **AWS:** Instance quota exceeded, spot unavailable
- Instance type not available in the selected availability zone
- Network/subnet issues
- IAM role/permissions issues

### Node Ready but GPU Not Detected

```bash
# Check if NVIDIA GPU Operator is installed
oc get pods -n nvidia-gpu-operator

# Check operator logs
oc logs -n nvidia-gpu-operator -l app=nvidia-driver-daemonset

# If not installed, install it as part of RHOAI deployment
```

### Pod Pending - "No nodes available"

Check:
1. GPU node is Ready: `oc get nodes -l nvidia.com/gpu.present=true`
2. Node has GPU label: `oc get nodes -L nvidia.com/gpu.present`
3. Pod has correct toleration for taint
4. Pod requested GPU resource: `nvidia.com/gpu: 1`

### Error: "Resource not found: REPLACE_ME-gpu-REPLACE_ME"

This error occurs when the GitOps Application is applied without setting the Helm parameters first.

**Solution:** Use the deployment script which handles this automatically:
```bash
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh
```

If you already applied the application manually:
```bash
# Delete the failed application
oc delete application gpu-machineset-aws-g4dn-xlarge -n openshift-gitops

# Then use the deployment script
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh
```

## Advanced Usage

### Via Kustomize (Advanced)

For direct kustomization without ArgoCD, edit `params.yaml` in the overlay:

```yaml
instanceType: g4dn.xlarge  # or g6.2xlarge, g6.4xlarge
replicas: 2                # Add more GPU nodes
```

Then apply:

```bash
# For g4dn.xlarge
kustomize build infra/gpu-machineset/aws/overlays/g4dn-xlarge | oc apply -f -

# For g6.2xlarge
kustomize build infra/gpu-machineset/aws/overlays/g6-2xlarge | oc apply -f -

# For g6.4xlarge
kustomize build infra/gpu-machineset/aws/overlays/g6-4xlarge | oc apply -f -
```

**Note:** The Kustomize approach may require manual cluster configuration. The deployment script (recommended) handles this automatically.

## Adding New Instance Types

### To add a new instance type:

1. Create values file:
   ```bash
   cp infra/gpu-machineset/aws/helm/values-g6-2xlarge.yaml \
      infra/gpu-machineset/aws/helm/values-NEW-TYPE.yaml
   ```

2. Edit the values file with new instance details

3. Create ArgoCD Application:
   ```bash
   cp gitops/infra/gpu-machineset-aws-g6.yaml \
      gitops/infra/gpu-machineset-aws-NEW-TYPE.yaml
   ```

4. Update the Application to reference new values file

5. Update `deploy.sh` script to support new instance type

## References

- [OpenShift Machine Management](https://docs.openshift.com/container-platform/latest/machine_management/index.html)
- [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/overview.html)
- [AWS EC2 GPU Instances](https://aws.amazon.com/ec2/instance-types/)
