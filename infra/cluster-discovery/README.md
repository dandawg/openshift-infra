# Cluster Discovery Job

Automated Kubernetes Job that discovers AWS-specific cluster information and creates a ConfigMap for use by GitOps applications.

## Purpose

When deploying infrastructure via GitOps (ArgoCD), many resources need cluster-specific information like:
- Cluster name and infrastructure ID
- AWS region and availability zone
- AMI ID for worker nodes

This job automatically discovers these values and stores them in a ConfigMap, eliminating manual parameter configuration.

## What It Discovers

The cluster-discovery job queries the OpenShift cluster and extracts:

- **Cluster Name**: Infrastructure name from `infrastructure.config.openshift.io/cluster`
- **Region**: AWS region from cluster status
- **Availability Zone**: AZ from existing worker or master machines
- **Infrastructure ID**: Cluster infrastructure identifier
- **AMI ID**: Amazon Machine Image ID from existing worker nodes

## Usage

### Standalone Deployment

Deploy the cluster-discovery job independently:

```bash
# Apply the GitOps manifest
oc apply -f gitops/infra/cluster-discovery.yaml

# Monitor the job
oc get jobs -n openshift-machine-api -w

# View the discovered cluster info
oc get configmap cluster-info -n openshift-machine-api -o yaml
```

### As Part of Multi-Source ArgoCD Application

Reference the cluster-discovery path in your ArgoCD Application's sources:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
spec:
  sources:
    # Cluster discovery runs first (Wave 0)
    - repoURL: https://github.com/dandawg/openshift-infra.git
      targetRevision: main
      path: infra/cluster-discovery
    
    # Other sources that need cluster info...
    - repoURL: https://github.com/my-org/my-repo.git
      # ...
```

## Output ConfigMap

The job creates a ConfigMap in the `openshift-machine-api` namespace:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-info
  namespace: openshift-machine-api
data:
  clusterName: "mycluster-abc123"
  region: "us-east-1"
  availabilityZone: "us-east-1a"
  infraID: "mycluster-abc123-12345"
  amiId: "ami-0bc8dda494f111572"
```

## ArgoCD Integration

The job includes ArgoCD annotations for proper sync behavior:

- **Sync Wave**: `0` - Runs first, before other infrastructure
- **Hook**: `PreSync` - Executes before the main sync operation

This ensures cluster information is available before deploying machinesets or other infrastructure.

## RBAC Permissions

The job requires specific permissions to discover cluster information:

**ClusterRole** (`cluster-info-reader`):
- Read `infrastructures` in `config.openshift.io`
- Read `machines` and `machinesets` in `machine.openshift.io`

**Role** (`cluster-info-writer`):
- Create/update ConfigMaps in `openshift-machine-api` namespace

## Troubleshooting

**Job fails to complete:**
```bash
# Check job status
oc get jobs -n openshift-machine-api

# View job logs
oc logs -n openshift-machine-api job/cluster-info-discovery

# Check pod events
oc get events -n openshift-machine-api --sort-by='.lastTimestamp'
```

**ConfigMap not created:**
- Verify the ServiceAccount has proper RBAC permissions
- Check if the cluster is on AWS (job is AWS-specific)
- Ensure at least one worker or master machine exists

**Job runs but values are empty:**
- Check the cluster is properly configured with AWS platform status
- Verify machines/machinesets exist in `openshift-machine-api` namespace

## Cleanup

The job automatically cleans up after 1 hour (3600 seconds) via `ttlSecondsAfterFinished`:

```bash
# Manual cleanup if needed
oc delete job cluster-info-discovery -n openshift-machine-api

# ConfigMap persists for use by other resources
oc delete configmap cluster-info -n openshift-machine-api  # if needed
```

## Related Components

This cluster discovery job is used by:
- GPU MachineSets (`infra/gpu-machineset/aws/helm`)
- CPU MachineSets (`infra/cpu-machineset/aws/helm`)
- Other infrastructure components requiring cluster-specific AWS information

## Future Enhancements

Potential improvements:
- Support for other cloud providers (Azure, GCP)
- Additional cluster metadata (VPC ID, subnet IDs, security groups)
- Automatic parameter injection into ArgoCD Applications using ArgoCD 2.6+ variable substitution
