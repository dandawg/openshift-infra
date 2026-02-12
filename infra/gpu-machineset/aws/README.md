# AWS GPU MachineSets

GPU-enabled MachineSets for AWS EC2 instances with NVIDIA GPUs.

## Available Instance Types

| Instance Type | GPUs | GPU Memory | vCPUs | RAM | Cost/hr* | Use Case |
|---------------|------|------------|-------|-----|----------|----------|
| g4dn.xlarge | 1x T4 | 16GB | 4 | 16GB | ~$0.53 | Cost-effective, embedding models, small models |
| g6.2xlarge | 1x L4 | 24GB | 8 | 32GB | ~$1.10 | Production, single model inference |
| g6.4xlarge | 1x L4 | 24GB | 16 | 64GB | ~$2.15 | Large models, vision models, high throughput |
| g6e.2xlarge | 1x L40S | 48GB | 8 | 64GB | ~$2.24 | AI inference, 3D graphics, high-memory GPU workloads |

*Approximate on-demand pricing (us-east-1, subject to change)

## Prerequisites

- OpenShift cluster on AWS
- AWS quota for GPU instances (g4dn, g6, g6e)
- Cluster in region that supports GPU instances
  - g4dn: Available in most regions
  - g6: us-east-1, us-west-2, eu-west-1, and others
  - g6e: us-east-1, us-west-2, eu-west-1, and others
- OpenShift GitOps (ArgoCD) installed

## Usage

### Via Deployment Script (Recommended)

Use the automated deployment script that handles all cluster configuration:

```bash
# Deploy with default settings (g6.2xlarge, 120GB gp3 volume)
./infra/gpu-machineset/aws/deploy.sh

# Deploy g4dn.xlarge (most cost-effective)
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# Deploy g6.4xlarge with custom storage
INSTANCE_TYPE=g6.4xlarge \
ROOT_VOLUME_SIZE=200 \
ROOT_VOLUME_TYPE=gp3 \
ROOT_VOLUME_IOPS=5000 \
./infra/gpu-machineset/aws/deploy.sh

# Deploy g6e.2xlarge (NVIDIA L40S)
INSTANCE_TYPE=g6e.2xlarge ./infra/gpu-machineset/aws/deploy.sh

# Deploy with multiple replicas (default is 1)
INSTANCE_TYPE=g6.2xlarge \
REPLICA_COUNT=3 \
./infra/gpu-machineset/aws/deploy.sh
```

**Configuration Options:**
- `INSTANCE_TYPE` - Instance type (default: `g6.2xlarge`)
  - `g4dn.xlarge` - Most cost-effective (~$0.53/hr)
  - `g6.2xlarge` - Recommended for most workloads (~$1.10/hr)
  - `g6.4xlarge` - High-performance (~$2.15/hr)
  - `g6e.2xlarge` - High GPU memory (~$2.24/hr, 48GB L40S)
- `ROOT_VOLUME_SIZE` - Root volume size in GB (default: `120`)
- `ROOT_VOLUME_TYPE` - Volume type: `gp3` (recommended) or `gp2` (default: `gp3`)
- `ROOT_VOLUME_IOPS` - IOPS for gp3 volumes (default: `3000`)
- `REPLICA_COUNT` - Number of GPU nodes to create (default: `1`)

The script automatically:
1. Gathers your cluster information (name, region, AZ, AMI)
2. Logs in to ArgoCD
3. Creates the ArgoCD Application
4. Sets cluster-specific Helm parameters
5. Syncs the application to deploy the MachineSet

**Scaling and Volume Adjustments:**

ArgoCD is configured to ignore changes to replica count and volume size, allowing you to scale or adjust storage after deployment without ArgoCD reverting your changes:

```bash
# Scale GPU nodes up or down
oc scale machineset <machineset-name> --replicas=3 -n openshift-machine-api

# Changes to replicas will persist even after ArgoCD sync
```

**MachineSet Naming:**
Each instance type creates a uniquely named MachineSet to avoid conflicts:
- g4dn.xlarge: `{clusterName}-gpu-g4dn-{az}` (e.g., `cluster-abc-gpu-g4dn-us-east-2a`)
- g6.2xlarge: `{clusterName}-gpu-g6-{az}` (e.g., `cluster-abc-gpu-g6-us-east-2a`)
- g6.4xlarge: `{clusterName}-gpu-g6-4x-{az}` (e.g., `cluster-abc-gpu-g6-4x-us-east-2a`)
- g6e.2xlarge: `{clusterName}-gpu-g6e-{az}` (e.g., `cluster-abc-gpu-g6e-us-east-2a`)

This allows you to deploy multiple GPU instance types in the same cluster simultaneously.

**Machine and Node Labels:**
Each machine and node gets labeled for easy filtering:
- `gpu-instance-type={instanceType}` - The EC2 instance type (e.g., `g4dn.xlarge`, `g6.2xlarge`, `g6e.2xlarge`)
- `gpu-type-suffix={suffix}` - The short suffix (e.g., `g4dn`, `g6`, `g6-4x`, `g6e`)
- `nvidia.com/gpu.present=true` - Standard GPU node label (added by GPU Operator)

**AWS Instance Tags:**
EC2 instances are tagged for visibility in the AWS console:
- `gpu-node=true` - Marks this as a GPU node
- `gpu-instance-type={instanceType}` - The instance type
- `gpu-type={suffix}` - The short suffix

### Deploy Multiple GPU Types

You can deploy multiple GPU instance types simultaneously for different workloads:

```bash
# Deploy cost-effective g4dn for embedding models
INSTANCE_TYPE=g4dn.xlarge ./infra/gpu-machineset/aws/deploy.sh

# Also deploy g6.2xlarge for production inference
INSTANCE_TYPE=g6.2xlarge ./infra/gpu-machineset/aws/deploy.sh

# And deploy g6.4xlarge for vision models
INSTANCE_TYPE=g6.4xlarge ./infra/gpu-machineset/aws/deploy.sh

# And deploy g6e.2xlarge for high GPU memory workloads
INSTANCE_TYPE=g6e.2xlarge ./infra/gpu-machineset/aws/deploy.sh
```

Then monitor with:
```bash
# Watch MachineSet creation
oc get machineset -n openshift-machine-api -w

# View all GPU machines
oc get machine -n openshift-machine-api -l gpu-node=true

# View machines by instance type
oc get machine -n openshift-machine-api -l gpu-instance-type=g4dn.xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.2xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6.4xlarge
oc get machine -n openshift-machine-api -l gpu-instance-type=g6e.2xlarge

# Or by suffix
oc get machine -n openshift-machine-api -l gpu-type-suffix=g4dn
oc get machine -n openshift-machine-api -l gpu-type-suffix=g6

# Wait for GPU node to be ready (5-10 minutes)
oc wait --for=condition=Ready nodes -l nvidia.com/gpu.present=true --timeout=600s

# View all GPU nodes
oc get nodes -l nvidia.com/gpu.present=true

# View nodes by instance type
oc get nodes -l gpu-instance-type=g4dn.xlarge
oc get nodes -l gpu-instance-type=g6.2xlarge
oc get nodes -l gpu-instance-type=g6.4xlarge
oc get nodes -l gpu-instance-type=g6e.2xlarge
```

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

# For g6e.2xlarge
kustomize build infra/gpu-machineset/aws/overlays/g6e-2xlarge | oc apply -f -
```

**Note:** The Kustomize approach requires manual cluster configuration. The deployment script (recommended) handles this automatically.

## Cost Considerations

Approximate On-Demand pricing (subject to change):
- **g4dn.xlarge**: ~$0.53/hour (best value for cost-conscious workloads)
- **g6.2xlarge**: ~$1.10/hour (good balance of performance and cost)
- **g6.4xlarge**: ~$2.15/hour (high performance for demanding workloads)
- **g6e.2xlarge**: ~$2.24/hour (high GPU memory for large models)

**Cost Savings Tips:**
- Use Spot instances for 60-70% discount
- Scale down when not in use
- Start with g4dn.xlarge for development and testing
- Use g6.2xlarge for production single-model workloads
- Reserve g6.4xlarge for high-performance scenarios

**Model Recommendations by Instance:**
- **g4dn.xlarge**: Qwen3-VL-Embedding-2B, small 7B models, embedding workloads
- **g6.2xlarge**: Granite 7B, Qwen3-VL-4B, general inference
- **g6.4xlarge**: Qwen3-VL-8B, Llama 3 8B, vision models, high-throughput scenarios
- **g6e.2xlarge**: Large language models (13B+), multi-modal models, models requiring 48GB GPU memory

## Helm Chart Configuration

The Helm chart supports the following parameters:

### Cluster Configuration (Auto-detected by script)
- `clusterName` - OpenShift cluster infrastructure name
- `region` - AWS region (e.g., us-east-1)
- `availabilityZone` - AWS availability zone (e.g., us-east-1a)
- `infraID` - OpenShift infrastructure ID
- `amiId` - RHCOS AMI ID (auto-detected from existing workers)

### GPU Configuration
- `instanceType` - AWS EC2 instance type
- `machineNameSuffix` - Unique suffix for the MachineSet name
- `replicas` - Number of GPU nodes (default: 1)

### Storage Configuration
- `rootVolume.size` - Root volume size in GB (default: 120)
- `rootVolume.type` - EBS volume type: gp3 or gp2 (default: gp3)
- `rootVolume.iops` - IOPS for gp3 volumes (default: 3000)

### GPU Metadata
- `gpu.count` - Number of GPUs per node
- `gpu.memoryMb` - GPU memory in MB
- `gpu.vCPU` - Number of vCPUs

## Troubleshooting

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

### Machine Stays in "Provisioning"

```bash
oc describe machine <machine-name> -n openshift-machine-api
```

Common causes:
- AWS quota limit reached
- Instance type not available in AZ
- IAM permissions issue
- Insufficient AWS capacity

### Node Doesn't Show GPU

```bash
# Check node labels
oc get nodes -L nvidia.com/gpu.present

# Check if NVIDIA GPU Operator is installed
oc get pods -n nvidia-gpu-operator

# Check operator logs
oc logs -n nvidia-gpu-operator -l app=nvidia-driver-daemonset
```

### Check AWS Quota

```bash
# List current quota for GPU instances in your region
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA  # Running On-Demand G instances
```

Request quota increase if needed through AWS console.

## Regional Availability

### g4dn instances
Available in most AWS regions including:
- us-east-1, us-east-2, us-west-1, us-west-2
- eu-west-1, eu-central-1
- ap-southeast-1, ap-northeast-1

### g6 instances
Available in select regions including:
- us-east-1, us-west-2
- eu-west-1, eu-central-1
- ap-southeast-1, ap-northeast-1

### g6e instances
Available in select regions including:
- us-east-1, us-west-2
- eu-west-1, eu-central-1
- ap-southeast-1, ap-northeast-1

Check [AWS documentation](https://aws.amazon.com/ec2/instance-types/) for the latest regional availability.

## Performance Tips

### Storage Configuration
- Use **gp3** volumes (default) for better price/performance
- Increase IOPS (up to 16,000) for I/O intensive workloads
- Consider larger volumes (200GB+) for model caching

### Network Performance
- g6 instances have better network performance than g4dn
- Use enhanced networking (enabled by default)

### GPU Utilization
- Monitor with DCGM metrics via NVIDIA GPU Operator
- Use node affinity to co-locate workloads with GPU nodes
- Consider batch inference for better GPU utilization

## References

- [AWS EC2 G4dn Instances](https://aws.amazon.com/ec2/instance-types/g4/)
- [AWS EC2 G6 Instances](https://aws.amazon.com/ec2/instance-types/g6/)
- [AWS EC2 G6e Instances](https://aws.amazon.com/ec2/instance-types/g6e/)
- [OpenShift Machine Management on AWS](https://docs.openshift.com/container-platform/latest/machine_management/creating_machinesets/creating-machineset-aws.html)
- [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/overview.html)
